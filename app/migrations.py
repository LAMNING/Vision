"""建表/迁移的运行时入口。

启动流程（app/lifespan.py）调用 :func:`upgrade_to_head`：

1. 仓库里**没有** alembic 脚本时（本仓库默认不带 ``alembic/`` 目录和
   ``alembic.ini``，见提交 "Remove deployment and migration tooling"），退回
   :func:`database.create_all`：只建*缺失*的表，**不会给已有表补列/补索引**，
   并打一条 WARNING 说明退化了；
2. 有脚本时，老库（有业务表但没有 ``alembic_version``）先 ``stamp head``
   —— 只写版本号，不改任何表；这样历史库能平滑接入迁移体系；
3. 然后 ``upgrade head`` 补齐缺的表/列。

``IMAGELABEL_AUTO_MIGRATE=0`` 可以强制走 ``create_all()``、跳过 alembic。
"""

import logging
import os
import pathlib

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from app import database

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
CONFIG_FILE = ROOT / 'alembic.ini'
SCRIPT_DIR = ROOT / 'alembic'

AUTO_MIGRATE_ENV = 'IMAGELABEL_AUTO_MIGRATE'
_FALSE_VALUES = {'0', 'false', 'no', 'off'}


def auto_migrate_enabled() -> bool:
    value = os.environ.get(AUTO_MIGRATE_ENV, '1').strip().lower()
    return value not in _FALSE_VALUES


def alembic_available() -> bool:
    '''迁移脚本是否就位：ini 和脚本目录缺一不可。'''
    return CONFIG_FILE.is_file() and SCRIPT_DIR.is_dir()


def _absolute_url(url: str) -> str:
    '''SQLite 相对路径转绝对路径：保证「运行时连的库」和「迁移连的库」是同一个文件。'''
    prefix = 'sqlite:///'
    if url.startswith(prefix) and not url.startswith('sqlite:////'):
        raw = url[len(prefix):]
        path = pathlib.Path(raw)
        if not path.is_absolute():
            path = (pathlib.Path.cwd() / path).resolve()
            return f'{prefix}{path.as_posix()}'
    return url


def alembic_config() -> Config:
    ini = CONFIG_FILE
    cfg = Config(str(ini))
    cfg.set_main_option('script_location', str(SCRIPT_DIR))
    # 显式把当前 engine 的 URL 交给 alembic/env.py，避免"运行时连 A、迁移连 B"。
    url = database.engine.url.render_as_string(hide_password=False)
    cfg.set_main_option('sqlalchemy.url', _absolute_url(url))
    return cfg


def _existing_tables() -> set[str]:
    return set(inspect(database.engine).get_table_names())


def needs_stamp() -> bool:
    '''老库特征：有业务表、但没有 alembic_version。'''
    tables = _existing_tables()
    return 'alembic_version' not in tables and 'account' in tables


# create_all() 只会建*缺失的表*，不会给已有表补列。这里维护一份「后来新加的列」
# 清单：(表名, 列名, 列定义)。启动时按当前库结构检查，缺了才 ALTER TABLE ADD COLUMN。
# 只加列、不改类型、不加索引——够用且在各方言上都安全（默认值必须是常量）。
_ADDED_COLUMNS: tuple[tuple[str, str, str], ...] = (
    ('task', 'review_required', 'BOOLEAN NOT NULL DEFAULT 1'),
)


def ensure_columns() -> None:
    '''给老库补齐 _ADDED_COLUMNS 里的列（幂等，全新库上是空操作）。'''
    inspector = inspect(database.engine)
    tables = set(inspector.get_table_names())
    for table, column, ddl in _ADDED_COLUMNS:
        if table not in tables:
            continue
        existing = {item['name'] for item in inspector.get_columns(table)}
        if column in existing:
            continue
        with database.engine.begin() as conn:
            conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {ddl}'))
        logger.warning('已补齐缺失的列：%s.%s（%s）', table, column, ddl)


def upgrade_to_head() -> None:
    if not auto_migrate_enabled():
        logger.info('%s 已关闭，退回 create_all() 模式', AUTO_MIGRATE_ENV)
        database.create_all()
        ensure_columns()
        return

    if not alembic_available():
        # 不是错误：这个仓库不带迁移脚本，create_all() 足以支撑全新库与老库启动。
        logger.warning(
            '未找到 %s / %s（迁移脚本未随仓库分发），退回 create_all()：'
            '只建缺失的表，不会给已有表补列或补索引',
            CONFIG_FILE.name,
            SCRIPT_DIR.name,
        )
        database.create_all()
        ensure_columns()
        return

    cfg = alembic_config()
    if needs_stamp():
        logger.warning('检测到老库（无 alembic_version），先 stamp head：%s', database.engine.url)
        command.stamp(cfg, 'head')
    command.upgrade(cfg, 'head')
    ensure_columns()
    logger.info('数据库迁移完成：%s', database.engine.url)


def current_revision() -> str | None:
    '''返回当前库的 alembic 版本号；没有版本表（create_all 建的库）时返回 None。

    先看表在不在，避免 ``MigrationContext`` 顺手把 ``alembic_version`` 建出来
    —— 启动日志不该往库里写东西。
    '''
    from alembic.runtime.migration import MigrationContext

    if 'alembic_version' not in _existing_tables():
        return None
    with database.engine.connect() as conn:
        return MigrationContext.configure(conn).get_current_revision()
