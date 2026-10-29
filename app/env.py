"""项目根目录 ``.env`` 的最小加载器（零依赖，不引入 python-dotenv）。

为什么需要：本仓库把 ``IMAGELABEL_SECRET_KEY`` 放在仓库根的 ``.env`` 里，
但进程启动时**没有任何一处**读它，于是：
- ``app/utils/security.py`` 每次启动都走"临时密钥"分支 → 重启后所有 token 失效；
- CLI（``python -m app.cli.make_admin``）和服务的 ``IMAGELABEL_DATABASE_URL``
  可能指向不同的库，管理员会建到"另一个数据库"里。

优先级：**真实环境变量 > .env**（``os.environ.setdefault`` 语义），
所以 ``$env:IMAGELABEL_DATABASE_URL=...`` 之类临时覆盖照旧生效。

加载点：``app/__init__.py`` 在 import 时调用一次，因此任何入口
（``run.py``、``python -m app.cli.*``、pytest）拿到的环境都一样。
"""

from __future__ import annotations

import logging
import os
import pathlib

logger = logging.getLogger(__name__)

ROOT = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT / '.env'
ENV_FILE_VAR = 'IMAGELABEL_ENV_FILE'

_loaded_for: set[str] = set()


def _parse(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export '):
            line = line[len('export '):].lstrip()
        key, sep, value = line.partition('=')
        if not sep:
            continue
        key = key.strip()
        if not key or not key.replace('_', '').isalnum():
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
            value = value[1:-1]
        # 行内注释只对未加引号的值生效：KEY=abc  # 说明
        elif ' #' in value:
            value = value.split(' #', 1)[0].rstrip()
        values[key] = value
    return values


def env_file_path() -> pathlib.Path | None:
    '''要加载的文件：``IMAGELABEL_ENV_FILE`` 显式指定 > 仓库根 ``.env``。'''
    explicit = os.environ.get(ENV_FILE_VAR, '').strip()
    if explicit:
        # 显式指定但不存在时说一声，避免"我明明写了为什么没生效"。
        path = pathlib.Path(explicit).expanduser()
        if not path.is_file():
            logger.warning('%s 指向的文件不存在：%s', ENV_FILE_VAR, path)
            return None
        return path
    return DEFAULT_ENV_FILE if DEFAULT_ENV_FILE.is_file() else None


def load_env_file(path: str | os.PathLike[str] | None = None) -> dict[str, str]:
    '''读取并注入环境变量；返回真正被写入的键值（已存在的变量不覆盖）。'''
    target = pathlib.Path(path).expanduser() if path is not None else env_file_path()
    if target is None or not target.is_file():
        return {}

    key = str(target.resolve())
    if key in _loaded_for:
        return {}
    _loaded_for.add(key)

    try:
        text = target.read_text(encoding='utf-8-sig')
    except OSError as exc:  # 权限/损坏都只告警，不影响启动
        logger.warning('读取 %s 失败，忽略：%s', target, exc)
        return {}

    applied: dict[str, str] = {}
    for name, value in _parse(text).items():
        if name in os.environ:
            continue
        os.environ[name] = value
        applied[name] = value
    if applied:
        logger.debug('已从 %s 载入 %d 个环境变量', target, len(applied))
    return applied
