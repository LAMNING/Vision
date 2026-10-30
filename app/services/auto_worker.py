'''自动标注后台 worker：轮询 queued 任务，跑 LLMDet 写标注。

单进程单线程（daemon 线程），多进程部署时每个进程都会抢任务，claim 靠
``transition_task`` 的乐观锁（status + version）保证只有一个能赢。

流程：queued -> auto_annotating -> 逐图 detect+写 annotations -> auto_annotated；
任一步在任务级别失败则 -> failed（error_message 里给出原因），可走 /retry 重投。
'''

from __future__ import annotations

import logging
import os
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from io import BytesIO
from uuid import uuid4

from PIL import Image as PILImage
from sqlalchemy import select

from app import database
from app.data.crud import annotation as annotation_crud
from app.data.crud import project_category as category_crud
from app.data.crud import task as task_crud
from app.data.crud import task_image as task_image_crud
from app.data.crud.project_auto_config import get_or_create_config
from app.data.project import Project
from app.data.task import Task
from app.data.task_image import TaskImage
from app.services import storage
from app.services.auto_annotation import (
    annotate_image,
    auto_config,
    category_specs,
)
from app.services.detector import (
    DetectorNotConfiguredError,
    TranslatorNotConfiguredError,
    get_detector,
    get_translator,
)

logger = logging.getLogger(__name__)

WORKER_ENV = 'IMAGELABEL_AUTO_WORKER'
INTERVAL_ENV = 'IMAGELABEL_AUTO_WORKER_INTERVAL'
DEFAULT_INTERVAL = 2.0

# 单图推理超时：防止一张畸形图把 worker 永久挂住
INFERENCE_TIMEOUT_ENV = 'IMAGELABEL_AUTO_TIMEOUT_SECONDS'
DEFAULT_INFERENCE_TIMEOUT = 180.0

# 单图像素上限：解压炸弹直接拒绝（40MP ≈ 120MB RGB）
MAX_PIXELS_ENV = 'IMAGELABEL_MAX_IMAGE_PIXELS'
DEFAULT_MAX_PIXELS = 40_000_000

# 回收租约过期任务的扫描间隔（秒）
RECOVERY_INTERVAL = 60.0


class LeaseLostError(RuntimeError):
    '''租约已过期/被回收：立刻停止处理，不再写库。'''


class InferenceTimeoutError(RuntimeError):
    '''单图推理超时。'''


def worker_enabled() -> bool:
    '''``IMAGELABEL_AUTO_WORKER=0``（或 false/no/off）可关掉后台线程。'''
    raw = os.environ.get(WORKER_ENV, '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def worker_interval() -> float:
    try:
        value = float(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(0.2, value)


def _positive_float(name: str, default: float, minimum: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    return max(minimum, value)


def inference_timeout() -> float:
    return _positive_float(INFERENCE_TIMEOUT_ENV, DEFAULT_INFERENCE_TIMEOUT, 10.0)


def max_image_pixels() -> int:
    return int(
        _positive_float(MAX_PIXELS_ENV, float(DEFAULT_MAX_PIXELS), 1_000_000)
    )


def make_worker_id() -> str:
    return f'{socket.gethostname()[:16]}-{os.getpid()}-{uuid4().hex[:8]}'


def _run_inference(fn, *args, timeout: float):
    '''在独立线程里跑推理，超时抛 InferenceTimeoutError。

    超时的线程没法强杀（onnxruntime 不响应取消），这里 ``shutdown(wait=False)``
    放弃等待，让 worker 继续处理其它任务；卡住的线程在任务失败后随进程回收。
    '''
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix='infer')
    future = executor.submit(fn, *args)
    try:
        return future.result(timeout=timeout)
    except FuturesTimeout as exc:
        logger.warning('推理超过 %.0fs 未返回，放弃等待', timeout)
        raise InferenceTimeoutError(f'单图推理超过 {timeout:.0f}s 未返回') from exc
    finally:
        executor.shutdown(wait=False)


def _load_image(image) -> PILImage.Image:
    data = storage.read_bytes(image.storage_key)
    pil = PILImage.open(BytesIO(data))
    width, height = pil.size
    limit = max_image_pixels()
    if width * height > limit:
        raise RuntimeError(
            f'图片过大：{width}x{height} = {width * height} 像素（上限 {limit}）'
        )
    pil.load()
    return pil.convert('RGB')


def annotate_task(db, task, *, worker_id: str | None = None) -> int:
    '''把任务里的所有图片跑一遍自动标注，返回成功张数。

    任务级前置条件（项目/类目/配置/模型/图片）不满足时直接抛异常，由调用方
    把任务标成 failed；单张图片失败只标记该图，不影响其它图。
    传了 worker_id 时每标完一张续一次租约，租约丢了立刻中止（LeaseLostError）。
    '''
    project = db.get(Project, task.project_id)
    if project is None:
        raise RuntimeError(f'project {task.project_id} not found')

    config_row = get_or_create_config(db, project.id)
    if not config_row.auto_annotate_enabled:
        raise RuntimeError('项目未开启自动标注')
    config = auto_config(config_row)

    categories = category_crud.list_categories(db, project.id)
    if not categories:
        raise RuntimeError('项目还没有启用的类目')

    # 模型加载 + 推理是重活（首次加载可能几分钟）：进来之前必须保证没有未提交的
    # 写事务，否则 worker 会一直占着 SQLite 写锁，其它请求写库直接 "database is locked"。
    db.commit()

    detector = get_detector()
    specs = category_specs(categories)
    # 有英文提示词的类目直接用，只有空着的那几个才需要翻译（见 build_prompts）。
    need_translation = any(not spec.en_name.strip() for spec in specs)
    translator = get_translator() if need_translation else None

    rows = task_image_crud.list_task_images(db, task.id)
    if not rows:
        raise RuntimeError('任务没有图片')

    failures: list[str] = []
    timeout = inference_timeout()
    for row in rows:
        try:
            image = _load_image(row.image)
            items = _run_inference(
                annotate_image,
                detector,
                image,
                specs,
                config,
                translator,
                timeout=timeout,
            )
            annotation_crud.replace_current(
                db, row, items, source='auto', commit=False
            )
            task_image_crud.mark_annotated(db, row, commit=False)
            db.commit()
        except LeaseLostError:
            raise
        except Exception as exc:  # 单张失败不影响其它图
            db.rollback()
            logger.warning(
                'task %s image %s failed: %s', task.id, row.image_id, exc
            )
            failures.append(f'image {row.image_id}: {exc}')
            fresh = db.get(TaskImage, row.id)
            if fresh is not None:
                task_image_crud.mark_annotation_failed(
                    db, fresh, str(exc)[:255]
                )
                db.commit()

        if worker_id is not None and not task_crud.renew_lease(
            db, task, worker_id=worker_id
        ):
            raise LeaseLostError(
                f'task {task.id} lease lost (worker {worker_id})'
            )

    if failures and len(failures) == len(rows):
        raise RuntimeError('; '.join(failures)[:2000])
    return len(rows) - len(failures)


def process_one(db, *, worker_id: str | None = None) -> bool:
    '''挑一个 queued 的 auto 任务处理；返回是否处理了任务（没活时 False）。

    manual 任务由标注员在工作台里画框，worker 必须跳过。
    抢占用条件 UPDATE（status='queued'）写入租约，抢不到就返回 False。
    '''
    worker_id = worker_id or make_worker_id()
    task = task_crud.claim_queued_task(db, worker_id=worker_id)
    if task is None:
        return False

    try:
        annotate_task(db, task, worker_id=worker_id)
    except LeaseLostError:
        logger.warning('task %s lease lost, stop processing', task.id)
        return True
    except (DetectorNotConfiguredError, TranslatorNotConfiguredError) as exc:
        db.rollback()
        logger.warning('task %s failed: %s', task.id, exc)
        _fail(db, task, str(exc))
        return True
    except Exception as exc:
        db.rollback()
        logger.warning('task %s failed: %s', task.id, exc)
        message = str(exc)[:2000] or '自动标注失败'
        _fail(db, task, message)
        return True

    task_crud.mark_auto_annotated(db, task)
    task_crud.clear_lease(db, task)
    logger.info('task %s auto annotated', task.id)
    return True


def _fail(db, task, message: str) -> None:
    '''标记失败并清掉租约；任务已被别处推进时忽略。'''
    try:
        task_crud.mark_failed(db, task, message)
    except Exception:
        db.rollback()
        logger.info('task %s already moved on, skip marking failed', task.id)
    try:
        task_crud.clear_lease(db, task)
    except Exception:
        db.rollback()


class AutoAnnotateWorker:
    def __init__(self, *, interval: float | None = None) -> None:
        self.interval = worker_interval() if interval is None else max(0.05, interval)
        self.worker_id = make_worker_id()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> 'AutoAnnotateWorker':
        if self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name='auto-annotate', daemon=True
        )
        self._thread.start()
        logger.info(
            'auto annotate worker started (id=%s interval=%.2fs)',
            self.worker_id,
            self.interval,
        )
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None

    def _recover(self) -> None:
        '''回收租约过期的 auto_annotating 任务（上一个 worker 崩溃留下的）。'''
        with database.SessionLocal() as db:
            recovered = task_crud.recover_stale_tasks(db)
        for task in recovered:
            logger.warning('recovered stale task %s -> failed', task.id)

    def _loop(self) -> None:
        last_recovery = 0.0
        while not self._stop.is_set():
            try:
                now = time.monotonic()
                if now - last_recovery >= RECOVERY_INTERVAL:
                    self._recover()
                    last_recovery = now
                with database.SessionLocal() as db:
                    if process_one(db, worker_id=self.worker_id):
                        continue  # 还有活就立刻接着干，不等轮询
            except Exception:  # 单轮异常不能让线程退出
                logger.exception('auto annotate worker loop error')
            self._stop.wait(self.interval)


_worker: AutoAnnotateWorker | None = None


def start_worker() -> AutoAnnotateWorker | None:
    global _worker
    if not worker_enabled():
        logger.info('auto annotate worker disabled by %s', WORKER_ENV)
        return None
    if _worker is None or not _worker.running:
        _worker = AutoAnnotateWorker().start()
    return _worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
