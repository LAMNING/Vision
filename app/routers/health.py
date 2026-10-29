"""健康检查：给负载均衡/K8s 探针用，不需要鉴权。

- ``GET /health/live``：进程活着就 200（不做任何 IO，避免探针拖慢）；
- ``GET /health/ready``：检查数据库、存储、迁移版本、后台 worker 状态，
  任一关键项失败返回 503，body 里逐项给出 ok/detail。
"""

import logging
import time

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app import database
from app import migrations
from app.services import detector, storage

router = APIRouter(prefix='/health', tags=['health'])
logger = logging.getLogger(__name__)

DEFAULT_STORAGE_URI = 'file://./state/storage'


def _check_database() -> dict:
    started = time.perf_counter()
    try:
        with database.engine.connect() as conn:
            conn.execute(text('SELECT 1'))
        return {
            'ok': True,
            'backend': database.engine.dialect.name,
            'latency_ms': round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001 —— 探针需要把任何失败都变成 503
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


def _check_storage() -> dict:
    started = time.perf_counter()
    try:
        backend = storage.get_storage()
        uri = storage.effective_uri()
        probe = 'health-probe.txt'
        backend.write_text(probe, 'ok')
        try:
            backend.delete(probe)
        except Exception:  # noqa: BLE001 —— 探针文件删不掉不该让 readiness 失败
            logger.warning('健康检查探针文件删除失败：%s', probe)
        return {
            'ok': True,
            'backend': storage.backend_label(uri),
            'uri': uri,
            'latency_ms': round((time.perf_counter() - started) * 1000, 1),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            'ok': False,
            'backend': storage.backend_label(storage.effective_uri())
            if storage.effective_uri()
            else None,
            'error': f'{type(exc).__name__}: {exc}',
        }


def _check_models() -> dict:
    # 只报「有没有配置」，不加载模型（加载一次要几秒，探针不该这么贵）。
    return {
        'ok': True,
        'detector_configured': detector.detector_configured(),
        'translator_configured': detector.translator_configured(),
    }


def _check_migrations() -> dict:
    try:
        revision = migrations.current_revision()
        return {'ok': revision is not None, 'revision': revision}
    except Exception as exc:  # noqa: BLE001
        return {'ok': False, 'error': f'{type(exc).__name__}: {exc}'}


@router.get('/live')
def health_live() -> dict:
    return {'status': 'ok'}


@router.get('/ready')
def health_ready() -> JSONResponse:
    from app.services.auto_worker import worker_enabled as auto_worker_enabled
    from app.services.reminders import worker_enabled as reminder_worker_enabled

    checks = {
        'database': _check_database(),
        'storage': _check_storage(),
        'models': _check_models(),
        'migrations': _check_migrations(),
        'workers': {
            'ok': True,
            'auto_worker_enabled': auto_worker_enabled(),
            'reminder_worker_enabled': reminder_worker_enabled(),
        },
    }
    # 模型未配置不算「不健康」：上传/人工标注等主流程仍可用，
    # 只是自动标注任务会失败（任务状态会给出明确错误）。
    critical_ok = checks['database']['ok'] and checks['storage']['ok']
    status_code = 200 if critical_ok else 503
    return JSONResponse(
        status_code=status_code,
        content={'status': 'ok' if critical_ok else 'degraded', 'checks': checks},
    )
