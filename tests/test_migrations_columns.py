"""老库补列：`create_all()` 不会给已有表加列，`ensure_columns()` 必须补上且幂等。

`Task.review_required` 是后加的列。仓库不带 alembic 脚本，这条路径就是老库
升级的唯一保障，所以单独覆盖：

- 缺列时 `ALTER TABLE task ADD COLUMN review_required BOOLEAN NOT NULL DEFAULT 1`；
- 已经有列时不动（第二次调用是空操作）；
- 表不存在时跳过（全新空库上不报错）。
"""

import pytest
from sqlalchemy import create_engine, inspect, text

from app import database, migrations


@pytest.fixture()
def isolated_engine(monkeypatch, tmp_path):
    engine = create_engine(f'sqlite:///{(tmp_path / "old.db").as_posix()}')
    monkeypatch.setattr(database, 'engine', engine)
    yield engine
    engine.dispose()


def columns_of(engine, table):
    return {item['name'] for item in inspect(engine).get_columns(table)}


def test_review_required_is_in_the_added_columns_list():
    tables = {table for table, _column, _ddl in migrations._ADDED_COLUMNS}
    assert 'task' in tables
    entry = next(item for item in migrations._ADDED_COLUMNS if item[1] == 'review_required')
    assert entry[0] == 'task'
    assert 'NOT NULL' in entry[2]
    assert 'DEFAULT' in entry[2]


def test_ensure_columns_adds_missing_column(isolated_engine):
    with isolated_engine.begin() as conn:
        conn.execute(text('CREATE TABLE task (id INTEGER PRIMARY KEY, name TEXT)'))
    assert 'review_required' not in columns_of(isolated_engine, 'task')

    migrations.ensure_columns()
    assert 'review_required' in columns_of(isolated_engine, 'task')

    with isolated_engine.begin() as conn:
        conn.execute(text("INSERT INTO task (name) VALUES ('old row')"))
    with isolated_engine.connect() as conn:
        value = conn.execute(text('SELECT review_required FROM task')).scalar_one()
    assert value == 1


def test_ensure_columns_is_idempotent(isolated_engine):
    with isolated_engine.begin() as conn:
        conn.execute(text('CREATE TABLE task (id INTEGER PRIMARY KEY, name TEXT)'))
    migrations.ensure_columns()
    migrations.ensure_columns()
    assert 'review_required' in columns_of(isolated_engine, 'task')


def test_ensure_columns_skips_missing_tables(isolated_engine):
    migrations.ensure_columns()
    assert 'task' not in set(inspect(isolated_engine).get_table_names())


def test_live_database_has_the_column():
    """启动流程跑过一次之后，真实测试库上必须已经有这一列。"""
    assert 'review_required' in columns_of(database.engine, 'task')
