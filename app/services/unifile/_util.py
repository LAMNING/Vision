"""路径处理、编码等通用小工具。

本模块只依赖 Python 标准库。
"""

from __future__ import annotations

import hashlib
import re
from typing import Iterable

# RFC 3986 unreserved characters
_UNRESERVED = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
)


def uri_encode(value: str, safe: str = "") -> str:
    """RFC 3986 百分号编码（UTF-8 字节级）。

    与 ``urllib.parse.quote`` 的区别：不会把 ``~`` 当作可再编码字符，
    也不会产生 ``+`` 号（``quote`` 默认的 safe 是 ``/``，且 ``~`` 在
    Python 3.7 之后已不转义，这里显式声明以保证跨版本一致）。
    """
    extra = set(safe)
    out = []
    for byte in value.encode("utf-8"):
        char = chr(byte)
        if char in _UNRESERVED or char in extra:
            out.append(char)
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def uri_decode(value: str) -> str:
    """百分号解码（不处理 ``+``）。"""
    from urllib.parse import unquote_to_bytes

    return unquote_to_bytes(value).decode("utf-8", "replace")


def norm_path(path: str | None) -> str:
    """规范化 POSIX 风格相对路径。

    - 统一 ``\\`` 为 ``/``
    - 去掉首尾斜杠
    - 折叠重复斜杠
    - 解析 ``.`` 与 ``..``（``..`` 不会越过根）
    """
    if path is None:
        return ""
    text = str(path).replace("\\", "/")
    parts: list[str] = []
    for segment in text.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            if parts:
                parts.pop()
            continue
        parts.append(segment)
    return "/".join(parts)


def join_path(*parts: str | None) -> str:
    """拼接多个路径片段并规范化。"""
    joined = "/".join(p for p in parts if p)
    return norm_path(joined)


def parent_path(path: str) -> str:
    """返回父目录路径（根返回空串）。"""
    normalized = norm_path(path)
    head, _, _ = normalized.rpartition("/")
    return head


def base_name(path: str) -> str:
    """返回路径的最后一段。"""
    normalized = norm_path(path)
    return normalized.rpartition("/")[2]


def split_prefix(path: str, prefix: str) -> str:
    """从 ``path`` 中剥掉 ``prefix`` 前缀。"""
    if not prefix:
        return norm_path(path)
    normalized = norm_path(path)
    prefix = norm_path(prefix)
    if normalized == prefix:
        return ""
    if normalized.startswith(prefix + "/"):
        return normalized[len(prefix) + 1 :]
    return normalized


def ensure_trailing_slash(prefix: str) -> str:
    """给对象存储前缀补上结尾斜杠，空串保持空串。"""
    if not prefix:
        return ""
    return prefix if prefix.endswith("/") else prefix + "/"


def is_sub_path(path: str, prefix: str) -> bool:
    """判断 ``path`` 是否位于 ``prefix`` 之下。"""
    if not prefix:
        return True
    normalized = norm_path(path)
    prefix = norm_path(prefix)
    return normalized == prefix or normalized.startswith(prefix + "/")


def guess_region(endpoint: str, default: str = "us-east-1") -> str:
    """从 endpoint 域名猜测 region。

    覆盖常见形态：

    - 阿里云 OSS：``oss-cn-hangzhou.aliyuncs.com``
    - AWS S3：``s3.us-west-2.amazonaws.com``、``s3-us-west-2.amazonaws.com``
    - 腾讯云 COS：``cos.ap-guangzhou.myqcloud.com``、``bucket.cos.ap-shanghai.myqcloud.com``
    - 其他（MinIO 等）：``minio.local:9000`` → 返回 ``default``

    Note:
        腾讯云的 region 是 ``ap-guangzhou`` 这种**无数字后缀**形式，AWS 是
        ``us-west-2``。两者都得认，否则 COS 会被误判成 ``us-east-1``，
        SigV4 的 credential scope 一错，请求必定 403。
    """
    host = re.sub(r"^https?://", "", endpoint or "").split("/")[0].split(":")[0]
    patterns = (
        r"^oss-([a-z0-9-]+)\.",                    # 阿里云 OSS
        r"^s3[.-]([a-z0-9-]+)\.",                  # AWS s3.us-west-2 / s3-us-west-2
        r"^cos\.([a-z0-9-]+)\.",                   # 腾讯云 COS（region 紧跟 cos.）
        r"\.([a-z]{2,3}-[a-z]+(?:-\d+)?)\.",       # bucket.<endpoint>.<region>.<tld>
        r"^([a-z]{2,3}-[a-z]+(?:-\d+)?)\.",        # <region>.<tld>
    )
    for pattern in patterns:
        match = re.search(pattern, host)
        if match:
            return match.group(1)
    return default


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data, usedforsecurity=False).hexdigest()


def iter_file_chunks(fileobj, size: int = 1024 * 1024):
    """按固定大小切块读取文件对象。"""
    while True:
        chunk = fileobj.read(size)
        if not chunk:
            break
        yield chunk


def chunks_to_file(chunks: Iterable[bytes], fileobj) -> int:
    """把字节块写入文件，返回写入总字节数。"""
    total = 0
    for chunk in chunks:
        fileobj.write(chunk)
        total += len(chunk)
    return total


def human_size(num: float) -> str:
    """人类可读的字节数。"""
    for unit in ("B", "KB", "MB", "GB", "TB", "PB"):
        if abs(num) < 1024.0 or unit == "PB":
            return f"{num:.0f} {unit}" if unit == "B" else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} PB"
