'''任务逾期提醒：定时扫描 due_at 已过、仍没结束的任务，给指派人/提交者发站内通知。

与自动标注 worker 同样的单进程 daemon 线程模型；每个任务只提醒一次
（靠 notification 表里 kind='task_overdue' + resource_id=task.id 去重）。
'''

from __future__ import annotations

import logging
import os
import threading

from app import database
from app.data.crud import notification as notif_crud
from app.data.crud import task as task_crud

logger = logging.getLogger(__name__)

WORKER_ENV = 'IMAGELABEL_REMINDER_WORKER'
INTERVAL_ENV = 'IMAGELABEL_REMINDER_INTERVAL'
DEFAULT_INTERVAL = 300.0


def worker_enabled() -> bool:
    '''``IMAGELABEL_REMINDER_WORKER=0``（或 false/no/off）可关掉提醒线程。'''
    raw = os.environ.get(WORKER_ENV, '1').strip().lower()
    return raw not in ('0', 'false', 'no', 'off')


def worker_interval() -> float:
    try:
        value = float(os.environ.get(INTERVAL_ENV, DEFAULT_INTERVAL))
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL
    return max(1.0, value)


def scan_once(db) -> int:
    '''扫描一轮逾期任务并创建提醒通知，返回本轮新建的提醒数。'''
    tasks = task_crud.list_overdue_tasks(db)
    created = 0
    for task in tasks:
        account_id = task.assignee_account_id or task.submitter_account_id
        if account_id is None:
            continue
        due = task.due_at.strftime('%Y-%m-%d %H:%M') if task.due_at else ''
        notif_crud.create(
            db,
            account_id,
            kind='task_overdue',
            title=f'任务 #{task.id} 已逾期',
            body=f'{(task.name or "任务")} 截止时间 {due}',
            resource_type='task',
            resource_id=task.id,
            commit=False,
        )
        created += 1
    if created:
        db.commit()
    return created


class ReminderWorker:
    def __init__(self, *, interval: float | None = None) -> None:
        self.interval = worker_interval() if interval is None else max(0.5, interval)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> 'ReminderWorker':
        if self.running:
            return self
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._loop, name='task-reminder', daemon=True
        )
        self._thread.start()
        logger.info('task reminder worker started (interval=%.0fs)', self.interval)
        return self

    def stop(self, timeout: float = 10.0) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout)
        self._thread = None

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                with database.SessionLocal() as db:
                    created = scan_once(db)
                    if created:
                        logger.info('task reminder: %d overdue notification(s)', created)
            except Exception:  # 单轮异常不能让线程退出
                logger.exception('task reminder worker loop error')
            self._stop.wait(self.interval)


_worker: ReminderWorker | None = None


def start_worker() -> ReminderWorker | None:
    global _worker
    if not worker_enabled():
        logger.info('task reminder worker disabled by %s', WORKER_ENV)
        return None
    if _worker is None or not _worker.running:
        _worker = ReminderWorker().start()
    return _worker


def stop_worker() -> None:
    global _worker
    if _worker is not None:
        _worker.stop()
        _worker = None
