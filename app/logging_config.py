"""进程内日志配置。

环境变量：
- ``IMAGELABEL_LOG_LEVEL``：默认 INFO（DEBUG/INFO/WARNING/ERROR）
- ``IMAGELABEL_LOG_JSON=1``：输出单行 JSON（便于 Loki/ELK 采集）
- ``IMAGELABEL_LOG_REQUEST_BODY=1``：保留位（暂未实现，避免误记录敏感信息）
"""

import json
import logging
import os
import sys
from contextvars import ContextVar
from datetime import datetime, timezone

_LEVELS = {'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'}
_configured = False

# 当前请求编号：Middleware 里 set，LogRecordFactory 读出来注入 record.request_id。
# 不要用 logger.xxx(..., extra={'request_id': ...})——LogRecord 已有该属性会抛
# KeyError: "Attempt to overwrite 'request_id' in LogRecord"。
request_id_var: ContextVar[str] = ContextVar('request_id', default='-')


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            'ts': datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            'level': record.levelname,
            'logger': record.name,
            'msg': record.getMessage(),
        }
        request_id = getattr(record, 'request_id', None)
        if request_id:
            payload['request_id'] = request_id
        if record.exc_info:
            payload['exc'] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def log_level() -> int:
    raw = os.environ.get('IMAGELABEL_LOG_LEVEL', 'INFO').strip().upper()
    return getattr(logging, raw, logging.INFO) if raw in _LEVELS else logging.INFO


def setup_logging() -> None:
    '''幂等：重复调用不会叠加 handler。'''
    global _configured
    if _configured:
        return

    handler = logging.StreamHandler(sys.stdout)
    if os.environ.get('IMAGELABEL_LOG_JSON', '').strip() in {'1', 'true', 'yes', 'on'}:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                '%(asctime)s %(levelname)-7s %(name)s [%(request_id)s] %(message)s',
                datefmt='%H:%M:%S',
            )
        )

    # 每条日志都带上当前请求编号（没有请求时用 '-' 占位），两种 formatter 都依赖它。
    old_factory = logging.getLogRecordFactory()

    def factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        if not hasattr(record, 'request_id'):
            record.request_id = request_id_var.get()
        return record

    logging.setLogRecordFactory(factory)

    root = logging.getLogger()
    root.setLevel(log_level())
    # 避免 uvicorn/reloader 已经装过 handler 时重复输出。
    if not any(getattr(h, '_imagelabel', False) for h in root.handlers):
        handler._imagelabel = True  # type: ignore[attr-defined]
        root.addHandler(handler)

    # 访问日志交给 RequestContextMiddleware，避免 uvicorn.access 重复打印。
    logging.getLogger('uvicorn.access').handlers = []
    logging.getLogger('uvicorn.access').propagate = False

    _configured = True
