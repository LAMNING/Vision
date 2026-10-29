"""请求上下文中间件：request-id + 访问日志 + 耗时统计。

- 入站带 ``X-Request-ID`` 就透传，否则生成 16 位 hex；
- 响应头回写 ``X-Request-ID``，方便前端/网关把错误对到日志；
- 访问日志格式：``method path -> status 12.3ms rid=...``；
- 未处理异常先记 traceback 再向上抛（由 app.main 的 Exception handler 返回 500）。

注意：request_id 通过 ``app.logging_config.request_id_var``（ContextVar）注入
LogRecord，**不要**再传 ``extra={'request_id': ...}``——LogRecord 已有该属性，
logging 会直接抛 ``KeyError: "Attempt to overwrite 'request_id' in LogRecord"``。
"""

import logging
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.logging_config import request_id_var

REQUEST_ID_HEADER = 'X-Request-ID'
logger = logging.getLogger('app.access')


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get(REQUEST_ID_HEADER) or new_request_id()
        request.state.request_id = request_id
        token = request_id_var.set(request_id)
        started = time.perf_counter()

        try:
            try:
                response = await call_next(request)
            except Exception:
                elapsed_ms = (time.perf_counter() - started) * 1000
                logger.error(
                    '%s %s -> 500 %.1fms rid=%s（未处理异常）',
                    request.method,
                    request.url.path,
                    elapsed_ms,
                    request_id,
                    exc_info=True,
                )
                raise

            elapsed_ms = (time.perf_counter() - started) * 1000
            status = response.status_code
            logger.log(
                logging.WARNING if status >= 500 else logging.INFO,
                '%s %s -> %d %.1fms rid=%s',
                request.method,
                request.url.path,
                status,
                elapsed_ms,
                request_id,
            )
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            request_id_var.reset(token)
