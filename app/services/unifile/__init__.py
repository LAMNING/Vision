"""unifile —— 五种存储后端，同一套 API。

本地文件系统、对象存储（OSS / S3）、FTP、SFTP、WebDAV。默认零第三方依赖，
只用 Python 标准库。

    from unifile import LocalStorage, OssStorage

    # 换后端只需换这一行，其余代码不动
    store = LocalStorage("./data")        # 或 OssStorage(bucket="b", endpoint=...)
    store.upload("a.txt", "docs/a.txt")
    print(store.list("docs"))

跨后端搬数据用 transfer / sync：

    from unifile import sync

    sync(oss, local, "photos/", "./photos", delete=True)
"""

from __future__ import annotations

from ._http import (
    HttpResponse,
    RequestsTransport,
    StdlibTransport,
    has_requests,
    make_transport,
)
from .base import (
    FileExists,
    FileInfo,
    FileNotFound,
    NotADirectory,
    NotAFile,
    PermissionDenied,
    ProgressCallback,
    Storage,
    StorageError,
    UnsupportedOperation,
)
from .ftp import FtpStorage
from .local import LocalStorage
from .oss import OssError, OssStorage
from .sftp import SftpError, SftpStorage
from .transfer import SyncResult, sync, transfer
from .webdav import WebDavError, WebDavStorage

__version__ = "0.6.1"

__all__ = [
    "Storage",
    "FileInfo",
    "ProgressCallback",
    "LocalStorage",
    "OssStorage",
    "OssError",
    "FtpStorage",
    "SftpStorage",
    "SftpError",
    "WebDavStorage",
    "WebDavError",
    "StorageError",
    "FileNotFound",
    "FileExists",
    "NotADirectory",
    "NotAFile",
    "PermissionDenied",
    "UnsupportedOperation",
    "open_storage",
    # 跨后端传输
    "transfer",
    "sync",
    "SyncResult",
    # 传输层（进阶用法）
    "StdlibTransport",
    "RequestsTransport",
    "HttpResponse",
    "make_transport",
    "has_requests",
    "__version__",
]


def open_storage(uri: str, **kwargs):
    """根据 URI 打开对应的后端。

    支持的写法：

    - ``file://./data`` 或 ``./data`` 或 ``C:/data`` → 本地
    - ``oss://bucket/prefix`` → 阿里云 OSS（原生签名）
    - ``s3://bucket/prefix`` → AWS S3 / MinIO 等 S3 兼容服务
    - ``cos://bucket/prefix?region=ap-guangzhou`` → 腾讯云 COS
    - ``ftp://user:pass@host/path`` → FTP
    - ``sftp://user@host/path`` → SFTP
    - ``dav://user:pass@host/path`` → WebDAV

    Args:
        uri: 后端地址。
        **kwargs: 传给具体后端构造函数的额外参数。

    Returns:
        :class:`Storage` 实例。

    Raises:
        ValueError: ``cos://`` 既没给 ``region`` 也没给 ``endpoint``。
    """
    from urllib.parse import parse_qs, unquote, urlparse

    if "://" not in uri:
        return LocalStorage(uri, **kwargs)

    parsed = urlparse(uri)
    scheme = parsed.scheme.lower()

    if scheme in ("file", "local"):
        # file:///tmp/x  ->  /tmp/x     （三斜杠：host 为空，path 是绝对路径）
        # file://./data  ->  ./data     （双斜杠 + 相对 host）
        # file://C:/data ->  C:/data    （Windows 盘符写在 host 位）
        path = unquote(parsed.path or "")
        host = parsed.netloc
        if host and host not in ("", "localhost"):
            if len(host) == 2 and host[1] == ":":
                # Windows 盘符：file://C:/data
                path = host + path
            else:
                # UNC 或相对目录：file://./data
                path = host + path
        if len(path) > 2 and path[0] == "/" and path[2] == ":":
            path = path[1:]  # Windows: /C:/data → C:/data
        return LocalStorage(path or ".", **kwargs)

    if scheme in ("oss", "s3"):
        bucket = parsed.netloc
        prefix = (parsed.path or "").lstrip("/")
        params = dict(kwargs)
        params.setdefault("prefix", prefix)
        if scheme == "oss":
            params.setdefault("signature_version", "oss-hmac-sha1")
        return OssStorage(bucket, **params)

    if scheme == "cos":
        # 腾讯云 COS 走的是官方 S3 兼容接口，用 SigV4 + virtual 寻址
        bucket = parsed.netloc
        prefix = (parsed.path or "").lstrip("/")
        params = dict(kwargs)
        params.setdefault("prefix", prefix)
        # region 可以写在 query 里：cos://bucket/docs?region=ap-guangzhou
        query_region = parse_qs(parsed.query).get("region", [None])[0]
        region = params.get("region") or query_region
        if not params.get("endpoint"):
            if not region:
                raise ValueError(
                    "cos:// 需要 region 或 endpoint，例如 "
                    "open_storage('cos://bucket/docs', region='ap-guangzhou')"
                )
            params["endpoint"] = f"cos.{region}.myqcloud.com"
        if region:
            params["region"] = region
        # COS 的 S3 兼容接口在待签字符串里不放时间戳，与 AWS 规范不同，
        # 所以默认用 cosv4。若将来 COS 修正了行为，传 signature_version="s3v4"。
        params.setdefault("signature_version", "cosv4")
        return OssStorage(bucket, **params)

    if scheme == "ftp":
        return FtpStorage(
            parsed.hostname or "",
            port=parsed.port or 21,
            user=unquote(parsed.username or "anonymous"),
            password=unquote(parsed.password or ""),
            root=(parsed.path or "").lstrip("/"),
            **kwargs,
        )

    if scheme == "sftp":
        return SftpStorage(
            parsed.hostname or "",
            port=parsed.port or 22,
            user=unquote(parsed.username or ""),
            password=unquote(parsed.password or ""),
            root=(parsed.path or "").lstrip("/"),
            **kwargs,
        )

    if scheme in ("dav", "davs", "webdav", "webdavs"):
        params = dict(kwargs)
        if parsed.username:
            params.setdefault("user", unquote(parsed.username))
        if parsed.password:
            params.setdefault("password", unquote(parsed.password))
        if scheme in ("davs", "webdavs"):
            params.setdefault("verify_ssl", True)
        return WebDavStorage(
            f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}",
            **params,
        )

    raise ValueError(f"不支持的 URI scheme：{scheme}")
