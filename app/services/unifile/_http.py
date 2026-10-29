"""HTTP 传输层。

提供两个可互换的实现，接口一致：

- :class:`StdlibTransport` —— 基于 ``http.client``，**零依赖**，默认实现。
- :class:`RequestsTransport` —— 基于 ``requests``（可选依赖）。

两者都返回统一的 :class:`HttpResponse`，上层（OSS / WebDAV）无需关心
底层用的是哪一个。

设计要点：

1. **连接复用**。``urllib.request.urlopen`` 每次调用都新建 TCP 与 TLS
   握手，列举上千个对象时开销显著。这里按 (scheme, host, port) 复用。
2. **重试**。连接级错误与可重试状态码（408/425/429/5xx）指数退避重试；
   带文件体的请求会在重试前回卷文件指针。
3. **压缩**。按需协商 ``Accept-Encoding`` 并透明解压。默认关闭 —— 对象
   存储里可能存着本身就带 ``Content-Encoding: gzip`` 的对象，解压会破坏
   原始字节。
4. **代理**。支持 HTTP 代理，HTTPS 走 CONNECT 隧道；代理地址可显式传入
   或从环境变量读取，支持 ``NO_PROXY``。

签名（SigV4 / OSS V1）通过 ``signer`` 回调注入，传输层不碰签名细节。
"""

from __future__ import annotations

import contextlib
import gzip
import http.client
import os
import random
import socket
import ssl
import threading
import time
import zlib
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

#: 需要重试的 HTTP 状态码（408 超时、425 过载、429 限流、5xx 服务端错误）
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

#: 签名回调：接收 (method, url, headers, payload_hash)，返回带签名的 headers
Signer = Callable[[str, str, dict[str, str], str], dict[str, str]]

_DEFAULT_CHUNK = 256 * 1024


def _backoff_delay(backoff: float, attempt: int) -> float:
    """计算第 ``attempt`` 次重试前的等待时长（指数退避 + 抖动）。

    纯指数退避有个隐蔽问题：一批客户端同时被限流后，会算出完全相同的
    等待时间、在同一毫秒一起重试，服务端刚缓过来又被同样的并发压垮
    （thundering herd）。乘一个 0.5~1.5 的随机因子把波峰摊平。

    Args:
        backoff: 基础等待秒数。
        attempt: 第几次尝试（从 1 开始）。

    Returns:
        实际等待秒数。
    """
    return backoff * (2 ** (attempt - 1)) * random.uniform(0.5, 1.5)


class HttpError(Exception):
    """HTTP 请求失败（服务端返回 >= 400）。"""

    def __init__(self, status: int, reason: str, body: bytes = b"") -> None:
        self.status = status
        self.reason = reason
        self.body = body
        detail = body.decode("utf-8", "replace")[:500] if body else ""
        super().__init__(f"HTTP {status} {reason}" + (f"：{detail}" if detail else ""))


class ConnectionError_(Exception):
    """连接层错误（超时、断开等），重试耗尽后抛出。"""


# ----------------------------------------------------------------------
# 统一响应
# ----------------------------------------------------------------------
class HttpResponse:
    """统一的 HTTP 响应接口。

    屏蔽 ``http.client.HTTPResponse`` 与 ``requests.Response`` 的差异。
    ``read()`` 返回的已是解压后的数据（若启用了 ``compress``）。

    响应体必须读完（或显式 ``close()``）才会把连接归还给池。没读完就
    关闭的连接会被丢弃——HTTP/1.1 连接上还有残留字节时复用会读到上一个
    响应的尾巴，这是最难排查的一类 bug。
    """

    __slots__ = ("status", "headers", "_stream", "_closer", "_body", "_closed", "_finish")

    def __init__(
        self,
        status: int,
        headers: dict[str, str],
        stream: Any = None,
        closer: Callable[[], None] | None = None,
        body: bytes | None = None,
        finish: Callable[[bool], None] | None = None,
    ) -> None:
        self.status = status
        #: 小写键的响应头字典
        self.headers = headers
        self._stream = stream
        self._closer = closer
        self._body = body
        self._closed = False
        #: 连接结算回调，参数为「连接是否可复用」
        self._finish = finish

    def _settle(self, reusable: bool) -> None:
        """结算连接归属，只生效一次。"""
        callback, self._finish = self._finish, None
        if callback is not None:
            try:
                callback(reusable)
            except Exception:
                pass

    def getheader(self, name: str, default: str | None = None) -> str | None:
        """取响应头（大小写不敏感）。"""
        return self.headers.get(name.lower(), default)

    def getheaders(self) -> list[tuple[str, str]]:
        """返回响应头列表。"""
        return list(self.headers.items())

    def read(self, size: int = -1) -> bytes:
        """读取响应体；``size`` 为负时读到 EOF。"""
        if self._body is not None:
            if size is None or size < 0:
                data, self._body = self._body, b""
                return data
            data, self._body = self._body[:size], self._body[size:]
            return data
        if self._stream is None:
            self._settle(True)
            return b""
        data = self._stream.read(size)
        data = data if data is not None else b""
        # 三种情况都说明已到 EOF，连接上不再有残留字节，可以安全复用：
        #   1) read(-1) 一次读到底
        #   2) 返回空
        #   3) 短读（请求 n 字节却不足 n）
        if not data or size is None or size < 0 or len(data) < size:
            self._settle(True)
        return data

    def iter_chunks(self, size: int = _DEFAULT_CHUNK) -> Iterator[bytes]:
        """按块迭代响应体。"""
        while True:
            chunk = self.read(size)
            if not chunk:
                break
            yield chunk

    def close(self) -> None:
        """关闭响应、释放连接。"""
        if self._closed:
            return
        self._closed = True
        if self._stream is not None:
            try:
                self._stream.close()
            except Exception:
                pass
        # 若 _finish 仍存活，说明从未读到 EOF：连接上有残留字节，只能丢弃。
        # 读到过 EOF 时 _settle 已在 read() 里触发，这里是空操作。
        self._settle(False)
        if self._closer is not None:
            try:
                self._closer()
            except Exception:
                pass

    def __del__(self) -> None:
        # 调用方忘了 close 时兜底，防止连接永久泄漏
        if not getattr(self, "_closed", True):
            try:
                self.close()
            except Exception:
                pass

    def __enter__(self) -> "HttpResponse":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# ----------------------------------------------------------------------
# 内部工具
# ----------------------------------------------------------------------
class _DeflateReader:
    """``deflate`` 解压读取器（兼容带/不带 zlib 头两种实现）。"""

    def __init__(self, raw: Any) -> None:
        self._raw = raw
        self._obj = zlib.decompressobj(zlib.MAX_WBITS)
        self._buf = bytearray()
        self._eof = False

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            while not self._eof:
                chunk = self._raw.read(65536)
                if not chunk:
                    self._eof = True
                    self._buf.extend(self._obj.flush())
                    break
                self._buf.extend(self._obj.decompress(chunk))
            data = bytes(self._buf)
            self._buf.clear()
            return data

        while len(self._buf) < size and not self._eof:
            chunk = self._raw.read(65536)
            if not chunk:
                self._eof = True
                self._buf.extend(self._obj.flush())
                break
            self._buf.extend(self._obj.decompress(chunk))
        data = bytes(self._buf[:size])
        del self._buf[:size]
        return data

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception:
            pass


def _decode_stream(raw: Any, encoding: str) -> Any:
    """按 ``Content-Encoding`` 包装解压流。"""
    encoding = (encoding or "").lower().strip()
    if encoding in ("gzip", "x-gzip"):
        return gzip.GzipFile(fileobj=raw, mode="rb")
    if encoding == "deflate":
        return _DeflateReader(raw)
    return raw


def _collect_headers(items) -> dict[str, str]:
    """把响应头规整成小写键字典（重复键以逗号合并）。"""
    out: dict[str, str] = {}
    for key, value in items:
        key = key.lower()
        if key in out:
            out[key] = f"{out[key]}, {value}"
        else:
            out[key] = value
    return out


def _proxy_auth_header(user: str | None, password: str | None) -> dict[str, str]:
    """生成代理认证头。"""
    if not user:
        return {}
    import base64

    raw = f"{user}:{password or ''}".encode("utf-8")
    return {"Proxy-Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}


def _is_loopback(host: str) -> bool:
    """是否为回环地址（这类地址永远不该走代理）。"""
    if not host:
        return False
    lowered = host.lower().strip("[]")
    if lowered in ("localhost", "::1", "0.0.0.0"):
        return True
    return lowered.startswith("127.")


def _resolve_proxy(
    scheme: str, explicit: str | None, no_proxy: str | None, host: str
) -> str | None:
    """决定该请求走哪个代理。

    Args:
        scheme: ``http`` 或 ``https``。
        explicit: 显式指定的代理。三种取值：
            ``None``（默认）读环境变量、``""``（空串）强制直连、
            其他字符串作为代理地址。
        no_proxy: 不走代理的主机列表（逗号分隔）。
        host: 目标主机。

    Returns:
        代理地址，或 ``None`` 表示直连。

    Note:
        回环地址（``localhost`` / ``127.*`` / ``::1``）**始终直连**。
        否则在设置了 ``HTTP_PROXY`` 的环境里，访问本地服务会被错误地
        转发给代理。
    """
    if explicit == "":
        return None
    if _is_loopback(host):
        return None

    if no_proxy is None:
        no_proxy = os.environ.get("NO_PROXY") or os.environ.get("no_proxy")
    if no_proxy:
        for target in (t.strip().lower() for t in no_proxy.split(",") if t.strip()):
            if target == "*":
                return None
            bare = target.lstrip(".").strip("[]")
            if host == bare or host.endswith("." + bare):
                return None

    proxy = explicit
    if proxy is None:
        names = ("HTTPS_PROXY", "https_proxy") if scheme == "https" else ("HTTP_PROXY", "http_proxy")
        for name in names + ("ALL_PROXY", "all_proxy"):
            value = os.environ.get(name)
            if value:
                proxy = value
                break
    return proxy or None


def _parse_proxy(proxy: str) -> tuple[str, int, str | None, str | None]:
    """解析代理地址，返回 (host, port, user, password)。"""
    if "://" not in proxy:
        proxy = "http://" + proxy
    parts = urlsplit(proxy)
    host = parts.hostname or ""
    port = parts.port or (443 if parts.scheme == "https" else 8080)
    return host, port, parts.username, parts.password


# ----------------------------------------------------------------------
# 标准库传输层
# ----------------------------------------------------------------------
class StdlibTransport:
    """基于 ``http.client`` 的传输层，零第三方依赖。

    Example:
        >>> t = StdlibTransport()
        >>> resp = t.request("GET", "https://example.com/x")
        >>> resp.read()
        >>> resp.close()
        >>> t.close()
    """

    name = "stdlib"
    supports_compression = True
    supports_proxy = True

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 3,
        backoff: float = 0.4,
        proxy: str | None = None,
        no_proxy: str | None = None,
        max_idle_per_host: int = 8,
    ) -> None:
        """
        Args:
            timeout: 单次请求超时（秒）。
            verify_ssl: 是否校验 TLS 证书。
            max_retries: 最大尝试次数（含首次）。
            backoff: 首次重试前等待秒数，之后按 2 倍递增并叠加抖动。
            proxy: 代理地址；``None`` 时读 ``HTTPS_PROXY`` / ``HTTP_PROXY``。
            no_proxy: 不走代理的主机列表。
            max_idle_per_host: 每个 (协议, 主机, 端口, 代理) 组合保留的
                空闲连接上限。并发数超过此值时多出的连接用完即关。
        """
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.backoff = backoff
        self.proxy = proxy
        self.no_proxy = no_proxy
        self.max_idle_per_host = max_idle_per_host
        #: key -> 空闲连接列表。使用中的连接不在此结构中，
        #: 因此不同线程绝不会拿到同一个连接。
        self._idle: dict[tuple, list[http.client.HTTPConnection]] = {}
        self._lock = threading.Lock()
        self._closed = False
        self._ssl_context: ssl.SSLContext | None = None
        if not verify_ssl:
            self._ssl_context = ssl._create_unverified_context()

    # -- 连接管理 ----------------------------------------------------
    @staticmethod
    def _key(scheme: str, host: str, port: int, proxy: str | None) -> tuple:
        return (scheme, host, port, proxy or "")

    def _new_conn(
        self, scheme: str, host: str, port: int, proxy: str | None
    ) -> http.client.HTTPConnection:
        """新建一个连接（不注册到池，调用方负责归还或关闭）。"""
        if proxy:
            p_host, p_port, p_user, p_pass = _parse_proxy(proxy)
            if scheme == "https":
                # HTTPS 走 CONNECT 隧道：连代理 → 建隧道 → 对目标做 TLS 握手
                conn = http.client.HTTPSConnection(
                    p_host, p_port, timeout=self.timeout, context=self._ssl_context
                )
                conn.set_tunnel(host, port, headers=_proxy_auth_header(p_user, p_pass))
            else:
                conn = http.client.HTTPConnection(p_host, p_port, timeout=self.timeout)
        elif scheme == "https":
            conn = http.client.HTTPSConnection(
                host, port, timeout=self.timeout, context=self._ssl_context
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=self.timeout)
        # 默认 8KB 太小，发送文件对象时按 256KB 分块
        conn.blocksize = _DEFAULT_CHUNK
        return conn

    def _acquire(
        self, scheme: str, host: str, port: int, proxy: str | None
    ) -> tuple[tuple, http.client.HTTPConnection]:
        """取一个可用连接：优先复用空闲的，否则新建。"""
        key = self._key(scheme, host, port, proxy)
        while True:
            with self._lock:
                bucket = self._idle.get(key)
                conn = bucket.pop() if bucket else None
            if conn is None:
                break
            # 池里的连接 socket 已断（服务端 keep-alive 超时）就丢掉重取
            if conn.sock is not None:
                return key, conn
            with contextlib.suppress(Exception):
                conn.close()
        return key, self._new_conn(scheme, host, port, proxy)

    def _release(self, key: tuple, conn: http.client.HTTPConnection) -> None:
        """把读完响应的连接放回池。"""
        # socket 已断（服务端发了 Connection: close，或 keep-alive 超时）
        # 说明连接不可复用，直接丢弃
        if conn.sock is None:
            return
        with self._lock:
            if self._closed:
                reusable = False
            else:
                bucket = self._idle.setdefault(key, [])
                reusable = len(bucket) < self.max_idle_per_host
                if reusable:
                    bucket.append(conn)
        if not reusable:
            with contextlib.suppress(Exception):
                conn.close()

    @staticmethod
    def _discard(conn: http.client.HTTPConnection | None) -> None:
        """关闭连接，不再复用。"""
        if conn is not None:
            with contextlib.suppress(Exception):
                conn.close()

    def idle_count(self) -> int:
        """当前空闲连接数（诊断用）。"""
        with self._lock:
            return sum(len(v) for v in self._idle.values())

    def close(self) -> None:
        """关闭所有空闲连接。"""
        with self._lock:
            self._closed = True
            buckets = list(self._idle.values())
            self._idle.clear()
        for bucket in buckets:
            for conn in bucket:
                with contextlib.suppress(Exception):
                    conn.close()

    def __enter__(self) -> "StdlibTransport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    # -- 请求 --------------------------------------------------------
    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        body_file: Any = None,
        body_length: int | None = None,
        headers: dict[str, str] | None = None,
        signer: Signer | None = None,
        payload_hash: str | None = None,
        raise_for_status: bool = True,
        compress: bool = False,
    ) -> HttpResponse:
        """发送请求。

        Args:
            body: 字节请求体。
            body_file: 文件对象请求体（流式发送）。
            body_length: ``body_file`` 的字节数。
            headers: 额外请求头。
            signer: 签名回调。
            payload_hash: 签名用的 payload 哈希。
            raise_for_status: 状态码 >= 400 时是否抛异常。
            compress: 是否协商 gzip/deflate 并自动解压。
                取对象原始字节时必须保持 ``False``。

        Returns:
            :class:`HttpResponse`。
        """
        parts = urlsplit(url)
        scheme = (parts.scheme or "https").lower()
        host = parts.hostname or ""
        port = parts.port or (443 if scheme == "https" else 80)
        path = parts.path or "/"
        if parts.query:
            path = f"{path}?{parts.query}"
        proxy = _resolve_proxy(scheme, self.proxy, self.no_proxy, host)

        base_headers = dict(headers or {})
        if body is not None and body_length is None:
            body_length = len(body)
        base_headers.setdefault(
            "Accept-Encoding", "gzip, deflate" if compress else "identity"
        )

        # 走 HTTP 代理时请求行要用绝对 URI；HTTPS 隧道仍用路径
        target = url if (proxy and scheme == "http") else path
        if proxy and scheme == "http":
            _, _, p_user, p_pass = _parse_proxy(proxy)
            base_headers.update(_proxy_auth_header(p_user, p_pass))

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            conn: http.client.HTTPConnection | None = None
            key: tuple = ()
            try:
                request_headers = dict(base_headers)
                if signer is not None:
                    request_headers = signer(
                        method, url, request_headers,
                        payload_hash if payload_hash is not None else "",
                    )
                if body_length is not None:
                    request_headers.setdefault("Content-Length", str(body_length))

                key, conn = self._acquire(scheme, host, port, proxy)
                send_body = body if body is not None else body_file
                if body_file is not None and hasattr(body_file, "seek"):
                    body_file.seek(0)

                conn.request(method, target, body=send_body, headers=request_headers)
                resp = conn.getresponse()

                if resp.status in RETRY_STATUSES and attempt < self.max_retries:
                    with contextlib.suppress(Exception):
                        resp.read()
                    self._discard(conn)
                    conn = None
                    last_exc = HttpError(resp.status, resp.reason)
                    self._sleep_backoff(attempt)
                    continue

                head = _collect_headers(resp.getheaders())
                if resp.status >= 400:
                    data = resp.read()
                    # 错误响应体已读完，连接可以复用（除非服务端要求关闭）
                    self._release(key, conn)
                    conn = None
                    if raise_for_status:
                        raise HttpError(resp.status, resp.reason, data)
                    return HttpResponse(resp.status, head, body=data)

                # 无响应体的方法/状态码：立即归还，不必等调用方 read()
                if method == "HEAD" or resp.status in (204, 304) or resp.length == 0:
                    # 必须 read() 一次让 http.client 复位连接状态并置
                    # isclosed()，否则下一次 getresponse 会抛 ResponseNotReady。
                    # 注意不能用 close()——那会连 socket 一起关掉，连接就废了。
                    with contextlib.suppress(Exception):
                        resp.read()
                    self._release(key, conn)
                    conn = None
                    return HttpResponse(resp.status, head, body=b"")

                stream: Any = resp
                if compress and head.get("content-encoding"):
                    stream = _decode_stream(resp, head["content-encoding"])

                # 用默认参数固化 conn/key，避免闭包捕获到下一轮循环的变量
                def _finish(reusable: bool, _k: tuple = key, _c: Any = conn) -> None:
                    if reusable:
                        self._release(_k, _c)
                    else:
                        self._discard(_c)

                return HttpResponse(resp.status, head, stream=stream, finish=_finish)

            except HttpError:
                raise
            except socket.gaierror as exc:
                # 域名解析失败重试没有意义（拼错的域名不会自己变好），
                # 直接失败并给出可操作的提示。这是配错 region 的典型症状：
                # cos.us-east-1.myqcloud.com 这种 host 根本不存在。
                self._discard(conn)
                raise ConnectionError_(
                    f"域名解析失败：{host}。"
                    f"请检查 endpoint / region 是否拼写正确"
                ) from exc
            except (socket.timeout, socket.error, OSError) as exc:
                self._discard(conn)
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                self._sleep_backoff(attempt)

        assert last_exc is not None
        if isinstance(last_exc, HttpError):
            raise last_exc
        raise ConnectionError_(str(last_exc)) from last_exc

    def _sleep_backoff(self, attempt: int) -> None:
        """按指数退避 + 抖动等待。

        Args:
            attempt: 当前是第几次尝试（从 1 开始）。
        """
        delay = _backoff_delay(self.backoff, attempt)
        if delay > 0:
            time.sleep(delay)


#: 兼容旧名字（v0.2 之前的调用方）
HttpPool = StdlibTransport


# ----------------------------------------------------------------------
# requests 传输层（可选依赖）
# ----------------------------------------------------------------------
class _RequestsStream:
    """把 ``requests.Response`` 包装成 ``read(size)`` 接口。"""

    __slots__ = ("_resp", "_iter")

    def __init__(self, resp: Any) -> None:
        self._resp = resp
        self._iter = resp.iter_content(chunk_size=_DEFAULT_CHUNK)

    def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            buf = bytearray()
            for chunk in self._iter:
                buf.extend(chunk)
            return bytes(buf)
        if size != _DEFAULT_CHUNK:
            self._iter = self._resp.iter_content(chunk_size=size)
        try:
            return next(self._iter)
        except StopIteration:
            return b""

    def close(self) -> None:
        try:
            self._resp.close()
        except Exception:
            pass


class RequestsTransport:
    """基于 ``requests`` 的传输层（可选依赖）。

    ``requests`` 不是必需依赖。装上后可获得更成熟的连接池、代理
    （含 SOCKS）与 TLS 配置。

    **OSS 签名场景的坑**：``requests`` 的 ``prepare_url`` 会对 URL 做
    ``requote_uri``，其 safe 字符集包含 ``+`` ``&`` ``=`` ``?``，于是
    ``%2B`` 会被还原成 ``+``。这会让 SigV4 的 canonical URI 与实际请求
    路径不一致，服务端返回 403 ``SignatureDoesNotMatch``。

    本实现通过**手工构造 ``PreparedRequest`` 并直接赋值 ``url``** 绕过
    ``prepare_url``，从而保留调用方已编码好的路径。

    Example:
        >>> from unifile import OssStorage
        >>> store = OssStorage(bucket="b", transport="requests")
    """

    name = "requests"
    supports_compression = True
    supports_proxy = True

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 3,
        backoff: float = 0.4,
        proxy: str | None = None,
        no_proxy: str | None = None,
    ) -> None:
        """
        Args:
            timeout: 单次请求超时（秒）。
            verify_ssl: 是否校验 TLS 证书。
            max_retries: 最大尝试次数（含首次）。
            backoff: 首次重试前等待秒数。
            proxy: 代理地址；``None`` 时读环境变量。
            no_proxy: 不走代理的主机列表。
        """
        try:
            import requests
            from requests.adapters import HTTPAdapter
        except ImportError as exc:  # pragma: no cover - 取决于环境
            raise ImportError(
                "使用 requests 传输层需要先安装：\n"
                "    pip install 'unifile[requests]'\n"
                "或去掉 transport='requests'，改用默认的标准库实现。"
            ) from exc

        self._requests = requests
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self.max_retries = max_retries
        self.backoff = backoff

        session = requests.Session()
        adapter = HTTPAdapter(
            pool_connections=4,
            pool_maxsize=8,
            max_retries=0,  # 重试由本层统一控制，避免双重退避
        )
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        if proxy == "":
            # 空串 = 强制直连：连环境变量里的代理也不认
            session.trust_env = False
            session.proxies = {}
        elif proxy:
            # 显式代理优先，同时关掉环境变量以免相互干扰
            session.trust_env = False
            session.proxies = {"http": proxy, "https": proxy}
        if no_proxy:
            os.environ.setdefault("NO_PROXY", no_proxy)
        session.verify = verify_ssl
        self._session = session

    def close(self) -> None:
        """关闭会话与连接池。"""
        try:
            self._session.close()
        except Exception:
            pass

    def __enter__(self) -> "RequestsTransport":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        body_file: Any = None,
        body_length: int | None = None,
        headers: dict[str, str] | None = None,
        signer: Signer | None = None,
        payload_hash: str | None = None,
        raise_for_status: bool = True,
        compress: bool = False,
    ) -> HttpResponse:
        """发送请求（接口与 :meth:`StdlibTransport.request` 一致）。"""
        req_mod = self._requests
        base_headers = dict(headers or {})
        if body is not None and body_length is None:
            body_length = len(body)
        base_headers.setdefault(
            "Accept-Encoding", "gzip, deflate" if compress else "identity"
        )

        last_exc: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                request_headers = dict(base_headers)
                if signer is not None:
                    request_headers = signer(
                        method, url, request_headers,
                        payload_hash if payload_hash is not None else "",
                    )
                if body_length is not None:
                    request_headers.setdefault("Content-Length", str(body_length))

                streaming = body_file is not None
                data: Any = body
                if streaming:
                    if hasattr(body_file, "seek"):
                        body_file.seek(0)
                    data = body_file

                # 关键：手工构造 PreparedRequest 并赋值 url，
                # 跳过 prepare_url 的 requote，保住 SigV4 的 canonical URI
                prepared = req_mod.PreparedRequest()
                prepared.method = method
                prepared.url = url
                prepared.headers = request_headers
                prepared.body = data
                prepared.hooks = {"response": []}

                resp = self._session.send(
                    prepared,
                    stream=streaming,
                    verify=self.verify_ssl,
                    timeout=self.timeout,
                    allow_redirects=False,
                )
                head = _collect_headers(resp.headers.items())

                if resp.status_code >= 400:
                    err_body = resp.content
                    resp.close()
                    if (
                        resp.status_code in RETRY_STATUSES
                        and attempt < self.max_retries
                    ):
                        last_exc = HttpError(resp.status_code, resp.reason or "", err_body)
                        time.sleep(_backoff_delay(self.backoff, attempt))
                        continue
                    if raise_for_status:
                        raise HttpError(resp.status_code, resp.reason or "", err_body)
                    return HttpResponse(resp.status_code, head, body=err_body)

                if streaming:
                    # stream=True 时 requests 不解压，交给 iter_content 处理
                    resp.raw.decode_content = True
                    return HttpResponse(
                        resp.status_code, head, stream=_RequestsStream(resp)
                    )
                return HttpResponse(resp.status_code, head, body=resp.content)

            except HttpError:
                raise
            except Exception as exc:
                if not isinstance(exc, (req_mod.exceptions.RequestException, OSError)):
                    raise
                # requests 把 DNS 失败包成 ConnectionError，消息里带 NameResolutionError
                if "NameResolutionError" in str(exc) or "Name or service not known" in str(exc):
                    raise ConnectionError_(
                        f"域名解析失败：{host}。请检查 endpoint / region 是否拼写正确"
                    ) from exc
                last_exc = exc
                if attempt >= self.max_retries:
                    break
                time.sleep(_backoff_delay(self.backoff, attempt))

        assert last_exc is not None
        raise ConnectionError_(str(last_exc)) from last_exc


# ----------------------------------------------------------------------
# 工厂
# ----------------------------------------------------------------------
def has_requests() -> bool:
    """是否安装了 ``requests``。"""
    try:
        import requests  # noqa: F401

        return True
    except ImportError:
        return False


def make_transport(kind: str = "auto", **kwargs: Any):
    """创建传输层实例。

    Args:
        kind: ``stdlib``、``requests`` 或 ``auto``。
            ``auto`` 优先用 requests（若已安装），否则回退标准库。
        **kwargs: 透传给具体实现。

    Returns:
        :class:`StdlibTransport` 或 :class:`RequestsTransport`。

    Raises:
        ValueError: ``kind`` 取值非法。
        ImportError: 指定 ``requests`` 但未安装。
    """
    kind = (kind or "auto").lower()
    if kind == "stdlib":
        return StdlibTransport(**kwargs)
    if kind == "requests":
        return RequestsTransport(**kwargs)
    if kind == "auto":
        if has_requests():
            try:
                return RequestsTransport(**kwargs)
            except ImportError:  # pragma: no cover - 竞态兜底
                pass
        return StdlibTransport(**kwargs)
    raise ValueError(
        f"未知的传输层类型：{kind}（可选 stdlib / requests / auto）"
    )
