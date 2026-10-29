import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import database
from app import migrations
from app.data.crud import platform_setting as platform_setting_crud
from app.logging_config import setup_logging
from app.services import detector, storage
from app.services.auto_worker import start_worker as start_auto_worker
from app.services.auto_worker import stop_worker as stop_auto_worker
from app.services.reminders import start_worker as start_reminder_worker
from app.services.reminders import stop_worker as stop_reminder_worker

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI):
    '''启动时迁移建表、应用平台存储策略与模型配置、拉起自动标注/逾期提醒 worker。

    - 建表走 alembic（app/migrations.py）：老库自动 stamp，`IMAGELABEL_AUTO_MIGRATE=0`
      退回 create_all()；
    - `storage_uri` 由管理员在 /admin/storage-policy 设置，覆盖环境变量；
    - 模型路径/精度/设备由管理员在 /admin/model-config 设置，覆盖环境变量；
    - 自动标注 worker 是进程内 daemon 线程，`IMAGELABEL_AUTO_WORKER=0` 可关闭；
    - 逾期提醒 worker 每 `IMAGELABEL_REMINDER_INTERVAL`（默认 300s）扫一次，
      `IMAGELABEL_REMINDER_WORKER=0` 可关闭。
    '''
    setup_logging()
    migrations.upgrade_to_head()

    with database.SessionLocal() as db:
        setting = platform_setting_crud.read_setting(db)
    storage.set_override_uri(setting.storage_uri if setting is not None else None)
    detector.set_override_config(platform_setting_crud.model_overrides(setting))

    logger.info(
        '启动完成：db=%s storage=%s revision=%s',
        database.engine.url,
        storage.effective_uri(),
        migrations.current_revision(),
    )

    start_auto_worker()
    start_reminder_worker()
    try:
        yield
    finally:
        stop_reminder_worker()
        stop_auto_worker()
        logger.info('已停止后台 worker')
