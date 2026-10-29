import os
from collections.abc import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

DEFAULT_DATABASE_URL = 'sqlite:///./test.db'

# SQLite 写锁忙等超时（毫秒）：另一个事务正在写时，本事务最多等这么久再报
# "database is locked"。注意它只对「排队等锁」有效——真正持锁太久的一方必须
# 自己把长事务拆短（见 app/services/auto_worker.py、app/routers/projects.py）。
DEFAULT_BUSY_TIMEOUT_MS = 30_000
BUSY_TIMEOUT_ENV = 'IMAGELABEL_SQLITE_BUSY_TIMEOUT_MS'

# SQLite 报写锁冲突时的典型文案（"database is locked" / "database table is locked"）
_LOCK_ERROR_MARKERS = ('database is locked', 'database table is locked')


def sqlite_busy_timeout_ms() -> int:
    raw = os.environ.get(BUSY_TIMEOUT_ENV, '').strip()
    if not raw:
        return DEFAULT_BUSY_TIMEOUT_MS
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_BUSY_TIMEOUT_MS
    return max(1_000, value)


def is_locked_error(exc: BaseException) -> bool:
    '''判断异常是不是 SQLite 写锁冲突（好翻译成 503 而不是 500）。'''
    detail = str(getattr(exc, 'orig', exc)).lower()
    return any(marker in detail for marker in _LOCK_ERROR_MARKERS)


def database_url() -> str:
    '''优先级：环境变量 IMAGELABEL_DATABASE_URL > 默认 sqlite 文件。

    生产建议直接给 PostgreSQL URL（``postgresql+psycopg://user:pw@host/db``），
    SQLite 仅用于本地开发/单机演示。
    '''
    value = os.environ.get('IMAGELABEL_DATABASE_URL', '').strip()
    return value or DEFAULT_DATABASE_URL


SQLALCHEMY_DATABASE_URL = database_url()


def _engine_kwargs(url: str) -> dict:
    kwargs: dict = {'future': True, 'pool_pre_ping': True}
    if url.startswith('sqlite'):
        # 单文件 SQLite：允许多线程共享连接，并设置忙等超时减少 "database is locked"。
        # timeout（秒）和 PRAGMA busy_timeout（毫秒）要保持一致。
        kwargs['connect_args'] = {
            'check_same_thread': False,
            'timeout': sqlite_busy_timeout_ms() / 1000,
        }
    else:
        kwargs['pool_size'] = int(os.environ.get('IMAGELABEL_DB_POOL_SIZE', '10'))
        kwargs['max_overflow'] = int(os.environ.get('IMAGELABEL_DB_MAX_OVERFLOW', '20'))
        kwargs['pool_recycle'] = 1800
    return kwargs


engine = create_engine(SQLALCHEMY_DATABASE_URL, **_engine_kwargs(SQLALCHEMY_DATABASE_URL))

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class BasicModel(DeclarativeBase):
    pass


@event.listens_for(engine, 'connect')
def _enable_sqlite_pragmas(dbapi_connection, _connection_record):
    '''SQLite 调优：外键校验 + WAL（读写并发）+ 忙等超时。

    注意这里只做「连接级」设置：pysqlite 在 legacy 事务模式下，SELECT 走驱动
    autocommit、第一条 INSERT/UPDATE/DELETE 才隐式 BEGIN，所以读不会互相阻塞；
    写锁冲突只能靠 busy_timeout 等待，持锁方必须尽快提交（见 app/services/auto_worker.py）。
    '''
    if not SQLALCHEMY_DATABASE_URL.startswith('sqlite'):
        return
    busy_timeout = sqlite_busy_timeout_ms()
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute('PRAGMA foreign_keys=ON')
        # 已经是 WAL 时这条 PRAGMA 是空操作（不会去抢排他锁）
        cursor.execute('PRAGMA journal_mode=WAL')
        cursor.execute('PRAGMA synchronous=NORMAL')
        cursor.execute(f'PRAGMA busy_timeout={busy_timeout}')
    finally:
        cursor.close()


def req_db() -> Iterator[Session]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def create_all() -> None:
    '''建缺失的表；正常启动由 app/migrations.py 决定走 alembic 还是这里。

    模型模块必须先被 import 才会注册到 ``BasicModel.metadata``，否则
    ``create_all()`` 什么都不会建（曾经只在 lifespan 恰好先 import 了
    ``app.data`` 时才碰巧可用）。这里显式兜底，让 CLI 等入口也能用。
    '''
    import app.data  # noqa: F401  # 聚合导出全部模型，只为副作用

    BasicModel.metadata.create_all(bind=engine)
