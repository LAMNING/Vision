"""独立自动标注 worker 进程：``python -m app.worker``。

把任务队列消费者从 API 进程里拆出来，好处：

- API 进程重启/滚动发布不会打断正在跑的推理；
- worker 崩了 API 还活着，任务留在 ``auto_annotating`` 等下次租约过期后被回收；
- 可以单独调 worker 的数量/机器（多进程靠 ``claim_queued_task`` 的条件 UPDATE 抢占）。

用法：
    python -m app.worker                 # 常驻
    python -m app.worker --once          # 只处理一轮（排查/定时任务）
    python -m app.worker --interval 1.5  # 空闲轮询间隔

环境变量与 API 进程共用（数据库/存储/模型都在 platform_setting 或环境里）。
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time

from app import database
from app import migrations
from app.data.crud import platform_setting as platform_setting_crud
from app.data.crud import task as task_crud
from app.logging_config import setup_logging
from app.services import detector, storage
from app.services.auto_worker import (
    RECOVERY_INTERVAL,
    make_worker_id,
    process_one,
    worker_interval,
)

logger = logging.getLogger(__name__)


def _apply_overrides() -> None:
    '''和 lifespan 一样：管理员配置的存储/模型设置优先于环境变量。'''
    with database.SessionLocal() as db:
        setting = platform_setting_crud.read_setting(db)
    storage.set_override_uri(setting.storage_uri if setting is not None else None)
    detector.set_override_config(platform_setting_crud.model_overrides(setting))


def _recover(worker_id: str) -> None:
    with database.SessionLocal() as db:
        recovered = task_crud.recover_stale_tasks(db)
    for task in recovered:
        logger.warning('worker %s recovered stale task %s -> failed', worker_id, task.id)


def run_forever(*, interval: float | None = None, once: bool = False) -> int:
    interval = worker_interval() if interval is None else max(0.05, interval)
    worker_id = make_worker_id()

    stop = threading.Event()

    def _handle(_signum, _frame):
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle)
        except (ValueError, OSError):  # 非主线程/不支持的平台
            pass

    setup_logging()
    migrations.upgrade_to_head()
    _apply_overrides()

    logger.info(
        'worker %s started (db=%s storage=%s revision=%s interval=%.2fs)',
        worker_id,
        database.engine.url,
        storage.effective_uri(),
        migrations.current_revision(),
        interval,
    )

    last_recovery = 0.0
    while not stop.is_set():
        try:
            now = time.monotonic()
            if now - last_recovery >= RECOVERY_INTERVAL:
                _recover(worker_id)
                last_recovery = now
            with database.SessionLocal() as db:
                worked = process_one(db, worker_id=worker_id)
        except Exception:
            logger.exception('worker loop error')
            worked = False
        if once:
            break
        if worked:
            continue
        stop.wait(interval)

    logger.info('worker %s stopped', worker_id)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='ImageLabel 自动标注 worker')
    parser.add_argument('--once', action='store_true', help='只处理一轮队列后退出')
    parser.add_argument(
        '--interval', type=float, default=None, help='空闲轮询间隔秒数（默认取 env）'
    )
    args = parser.parse_args(argv)
    return run_forever(interval=args.interval, once=args.once)


if __name__ == '__main__':
    sys.exit(main())
