import logging
import mimetypes
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.exc import OperationalError

from app import database
from app.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    TooManyRequestsError,
)
from app.errors import ValidationError as AppValidationError
from app.lifespan import lifespan
from app.logging_config import setup_logging
from app.middleware import REQUEST_ID_HEADER, RequestContextMiddleware, new_request_id
from app.routers import (
    accounts,
    admin,
    auth,
    enterprises,
    groups,
    health,
    images,
    invitations,
    me,
    notifications,
    projects,
    space,
    tasks,
    unifile,
)

setup_logging()
logger = logging.getLogger('app')

app = FastAPI(
    title='ImageLabel API',
    description='FastAPI app',
    version='0.1.0',
    lifespan=lifespan,
)

# 正常部署时前端（web/）由本进程托管，同源，CORS 根本用不上；
# 这一段是给「单独起静态服务调前端」（如 python -m http.server 8125）留的，
# 那种场景要显式配 IMAGELABEL_CORS_ORIGINS=http://127.0.0.1:8125，不要用默认的 *。
# Bearer token 走 Authorization 头，不需要 cookie，所以 allow_credentials=False。
_cors_origins = [
    origin.strip()
    for origin in os.environ.get('IMAGELABEL_CORS_ORIGINS', '*').split(',')
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
    expose_headers=['Content-Disposition', 'ETag'],
)

# JSON 响应压缩（图片/已压缩格式会跳过；threshold 以上才压）。
app.add_middleware(GZipMiddleware, minimum_size=1024)

# 放最后添加 = 最外层：所有请求（包括 CORS 预检）都有 request-id 和访问日志。
app.add_middleware(RequestContextMiddleware)


def _status_for(exc: AppError) -> int:
    '''app/errors.py 末尾的 HTTP 映射表；按 isinstance 顺序不能乱。'''
    if isinstance(exc, NotFoundError):
        return 404
    if isinstance(exc, ConflictError):
        return 409
    if isinstance(exc, PermissionDeniedError):
        return 403
    if isinstance(exc, AppValidationError):
        return 400
    if isinstance(exc, TooManyRequestsError):
        return 429
    return 500


def _rid(request: Request) -> str:
    return getattr(request.state, 'request_id', None) or new_request_id()


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    # crud 只抛 AppError 子类、不认识 HTTP；到这里统一翻译成状态码。
    # 注册在父类上即可，Starlette 会沿 MRO 找到匹配的处理器。
    headers = {REQUEST_ID_HEADER: _rid(request)}
    if isinstance(exc, TooManyRequestsError):
        headers['Retry-After'] = str(exc.retry_after)
    return JSONResponse(
        status_code=_status_for(exc),
        content={'detail': str(exc), 'request_id': _rid(request)},
        headers=headers,
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    rid = _rid(request)
    # request_id 已由 RequestContextMiddleware 写进日志 LogRecord（contextvar），
    # 这里不能再传 extra={'request_id': ...}，否则 logging 会抛 KeyError。
    logger.error(
        '未处理异常 %s %s rid=%s', request.method, request.url.path, rid,
        exc_info=exc,
    )
    return JSONResponse(
        status_code=500,
        content={'detail': '服务器内部错误，请把请求编号发给管理员', 'request_id': rid},
        headers={REQUEST_ID_HEADER: rid},
    )


@app.exception_handler(OperationalError)
async def db_locked_handler(request: Request, exc: OperationalError) -> JSONResponse:
    '''SQLite 写锁冲突 -> 503（可重试），而不是当成 500 内部错误。

    正常配置下写事务都是毫秒级（见 app/database.py 的 busy_timeout 说明），
    出现这个响应说明确实有事务持锁过久，日志里带 request-id 便于排查。
    '''
    if not database.is_locked_error(exc):
        return await unhandled_exception_handler(request, exc)
    rid = _rid(request)
    logger.warning(
        '数据库写锁冲突 %s %s rid=%s: %s', request.method, request.url.path, rid,
        getattr(exc, 'orig', exc),
    )
    return JSONResponse(
        status_code=503,
        content={'detail': '数据库正忙，请稍后重试', 'request_id': rid},
        headers={REQUEST_ID_HEADER: rid, 'Retry-After': '2'},
    )


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(space.router)
app.include_router(groups.router)
app.include_router(projects.router)
app.include_router(invitations.router)
app.include_router(images.router)
app.include_router(admin.router)
app.include_router(enterprises.router)
app.include_router(unifile.router)
app.include_router(tasks.router)
app.include_router(notifications.router)
app.include_router(accounts.router)
app.include_router(me.router)


@app.get('/_alive', tags=['root'])
def root():
    return {'message': 'FastAPI app is running'}


# 前端（web/）由同一个进程托管：和 API 同源，不用 CORS，也没有 Flask 那一层。
# 必须挂在所有路由之后——Starlette 按注册顺序匹配，'/' 挂载会吃掉后面注册的路径，
# 把 /_alive、/docs、/auth/* 全挡成静态文件的 404。
_WEB_DIR = Path(__file__).resolve().parent.parent / 'web'
if _WEB_DIR.is_dir():
    # 字体 MIME：Windows 的注册表里 .ttf 常被登记成 application/octet-stream，
    # @font-face 虽然大多能加载，但控制台会警告，这里显式纠正。
    mimetypes.add_type('font/ttf', '.ttf')
    mimetypes.add_type('font/woff2', '.woff2')
    # html=True：'/' 直接给 index.html（未登录时 core.js 会自己跳 login.html）；
    # 静态文件带 ETag/Last-Modified，浏览器二次访问走 304。
    app.mount('/', StaticFiles(directory=_WEB_DIR, html=True), name='web')
else:
    logger.warning('找不到前端目录 %s，本次只提供 API', _WEB_DIR)
