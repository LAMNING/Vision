'''图片存储服务：业务代码只认这里的几个函数，底层交给 unifile。

`Image.storage_key` 就是 unifile 里的对象路径（如 `g3/9f2c...e1.png`），
换后端只改 `IMAGELABEL_STORAGE_URI`，数据库和路由都不用动：

    file://./state/storage      本地目录（默认，开发用）
    oss://bucket/prefix         OSS / S3
    ftp://user:pw@host/path     FTP
    sftp://user:pw@host/path    SFTP
    dav://host/path             WebDAV

unifile 是零第三方依赖的库，已放到 `app/services/unifile/`。
'''

from __future__ import annotations

import io
import os
from collections.abc import Iterator
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from app.services.unifile import FileInfo, FileNotFound, Storage, open_storage

STORAGE_URI_ENV = 'IMAGELABEL_STORAGE_URI'
DEFAULT_STORAGE_URI = 'file://./state/storage'
SUPPORTED_SCHEMES = frozenset(
    {'file', 'local', 'oss', 's3', 'cos', 'ftp', 'sftp', 'dav', 'davs', 'webdav', 'webdavs'}
)

# 平台管理员在 /admin/storage-policy 里设置的 URI；为空表示跟随环境变量。
_override_uri: str | None = None


def env_uri() -> str:
    '''环境变量里的后端 URI，没配就用默认本地目录。'''
    return os.environ.get(STORAGE_URI_ENV) or DEFAULT_STORAGE_URI


def set_override_uri(uri: str | None) -> None:
    '''设置平台级存储后端；传 None 表示恢复成环境变量/默认值。'''
    global _override_uri
    _override_uri = (uri or '').strip() or None
    reset_storage_cache()


def override_uri() -> str | None:
    return _override_uri


def effective_uri() -> str:
    '''按优先级取当前生效的 URI：平台设置 > 环境变量 > 默认。'''
    return _override_uri or env_uri()


def storage_uri() -> str:
    '''当前存储后端 URI。'''
    return effective_uri()


def validate_storage_uri(uri: str) -> str:
    '''校验管理员填的 URI；不带 scheme 的当作本地路径（如 ./state/storage）。'''
    value = (uri or '').strip()
    if not value:
        raise ValueError('storage_uri 不能为空')
    if '://' not in value:
        return value
    scheme = value.split('://', 1)[0].lower()
    if scheme not in SUPPORTED_SCHEMES:
        allowed = ', '.join(sorted(SUPPORTED_SCHEMES))
        raise ValueError(f'不支持的存储类型：{scheme}（可选：{allowed}）')
    return value


def backend_label(uri: str) -> str:
    '''给前端显示的后端名称：local / oss / s3 / cos / ftp / sftp / webdav。'''
    if '://' not in uri:
        return 'local'
    scheme = uri.split('://', 1)[0].lower()
    return {
        'file': 'local',
        'local': 'local',
        'dav': 'webdav',
        'davs': 'webdav',
        'webdav': 'webdav',
        'webdavs': 'webdav',
    }.get(scheme, scheme)


@lru_cache(maxsize=1)
def get_storage() -> Storage:
    '''按 URI 创建并缓存存储实例；测试里换 URI 后调 reset_storage_cache()。'''
    return open_storage(storage_uri())


def reset_storage_cache() -> None:
    get_storage.cache_clear()


def build_key(group_id: int, filename: str | None) -> str:
    '''为一张图生成对象路径：组隔离 + uuid，避免重名与路径穿越。'''
    ext = Path(filename or '').suffix.lower()
    if len(ext) <= 9 and ext[1:].isalnum():
        suffix = ext
    else:
        suffix = '.bin'
    return f'g{int(group_id)}/{uuid4().hex}{suffix}'


def save_bytes(key: str, data: bytes) -> None:
    get_storage().write_bytes(key, data)


def read_bytes(key: str) -> bytes:
    return get_storage().read_bytes(key)


def delete(key: str) -> None:
    '''删除对象；已经不存在视为成功（幂等）。'''
    try:
        get_storage().delete(key)
    except FileNotFound:
        pass


def exists(key: str) -> bool:
    return get_storage().exists(key)


def stat_size(key: str) -> int | None:
    try:
        return get_storage().stat(key).size
    except FileNotFound:
        return None


def stat_info(key: str) -> FileInfo | None:
    try:
        return get_storage().stat(key)
    except FileNotFound:
        return None


STREAM_CHUNK = 64 * 1024


def open_binary(key: str) -> BinaryIO:
    '''打开对象用于流式读取。

    本地后端直接 ``open(path, 'rb')``，不会把整张图读进内存；其他后端
    （OSS/FTP/SFTP/WebDAV）unifile 没有暴露文件句柄，退化为 BytesIO 包装
    ``read_bytes``。返回的对象由调用方负责关闭。
    '''
    backend = get_storage()
    resolver = getattr(backend, '_abs', None)
    if backend.__class__.__name__ == 'LocalStorage' and callable(resolver):
        return open(resolver(key), 'rb')
    return io.BytesIO(read_bytes(key))


def iter_chunks(stream: BinaryIO, chunk_size: int = STREAM_CHUNK) -> Iterator[bytes]:
    '''按块读取并保证流最终关闭，供 StreamingResponse 使用。'''
    try:
        while True:
            chunk = stream.read(chunk_size)
            if not chunk:
                break
            yield chunk
    finally:
        stream.close()


__all__ = [
    'STORAGE_URI_ENV',
    'DEFAULT_STORAGE_URI',
    'SUPPORTED_SCHEMES',
    'STREAM_CHUNK',
    'env_uri',
    'set_override_uri',
    'override_uri',
    'effective_uri',
    'storage_uri',
    'validate_storage_uri',
    'backend_label',
    'get_storage',
    'reset_storage_cache',
    'build_key',
    'save_bytes',
    'read_bytes',
    'delete',
    'exists',
    'stat_size',
    'stat_info',
    'open_binary',
    'iter_chunks',
]
