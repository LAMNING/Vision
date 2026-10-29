"""WebDAV 后端。

纯 HTTP + XML，只依赖标准库。兼容 Nextcloud、ownCloud、坚果云、
Apache mod_dav 等常见 WebDAV 服务。

用到的 HTTP 方法：
- ``PROPFIND`` 列举目录（带 ``Depth`` 头）
- ``GET`` / ``PUT`` 读写文件
- ``DELETE`` 删除
- ``MKCOL`` 创建目录
- ``MOVE`` 移动/重命名
- ``HEAD`` 探测元信息
"""

from __future__ import annotations

import base64
import os
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import quote, urlsplit

from ._http import ConnectionError_, HttpError, make_transport
from ._util import base_name, norm_path
from .base import (
    FileInfo,
    FileNotFound,
    NotAFile,
    Storage,
    StorageError,
)

_DAV_NS = "{DAV:}"


class WebDavError(StorageError):
    """WebDAV 操作失败。"""


class WebDavStorage(Storage):
    """WebDAV 后端。

    Example:
        >>> store = WebDavStorage(
        ...     "https://dav.example.com/remote.php/dav/files/user",
        ...     user="user",
        ...     password="pass",
        ... )
        >>> store.upload("a.txt", "docs/a.txt")
    """

    kind = "webdav"
    preserves_mtime = True

    def __init__(
        self,
        url: str,
        *,
        user: str = "",
        password: str = "",
        token: str = "",
        root: str = "",
        timeout: float = 30.0,
        verify_ssl: bool = True,
        max_retries: int = 3,
        transport: str = "auto",
        proxy: str | None = None,
        no_proxy: str | None = None,
    ) -> None:
        """
        Args:
            url: WebDAV 服务的基础 URL。
            user: 用户名（Basic 认证）。
            password: 密码（Basic 认证）。
            token: Bearer Token（与 user/password 二选一）。
            root: 相对于 base URL 的子路径。
            timeout: 单次请求超时（秒）。
            verify_ssl: 是否校验 TLS 证书。
            max_retries: 重试次数。
            transport: HTTP 传输层，``auto``（默认，有 requests 就用）
                / ``stdlib`` / ``requests``。
            proxy: 代理地址。
            no_proxy: 不走代理的主机列表。
        """
        super().__init__(root)
        if not url:
            raise ValueError("url 不能为空")
        self.base_url = url.rstrip("/")
        self.user = user
        self.password = password
        self.token = token
        self.timeout = timeout
        self.verify_ssl = verify_ssl
        self._pool = make_transport(
            transport,
            timeout=timeout,
            verify_ssl=verify_ssl,
            max_retries=max_retries,
            proxy=proxy,
            no_proxy=no_proxy,
        )

    # ------------------------------------------------------------------
    # URL 与认证
    # ------------------------------------------------------------------
    def _url(self, path: str) -> str:
        """构造资源的完整 URL。"""
        rel = norm_path(path)
        root = norm_path(self.root)
        if root:
            rel = f"{root}/{rel}" if rel else root
        if not rel:
            return self.base_url + "/"
        return self.base_url + "/" + "/".join(quote(p, safe="") for p in rel.split("/"))

    def _auth_headers(self) -> dict[str, str]:
        if self.token:
            return {"Authorization": f"Bearer {self.token}"}
        if self.user:
            raw = f"{self.user}:{self.password}".encode("utf-8")
            return {"Authorization": "Basic " + base64.b64encode(raw).decode("ascii")}
        return {}

    def _request(
        self,
        method: str,
        url: str,
        *,
        body: bytes | None = None,
        headers: dict[str, str] | None = None,
        raise_for_status: bool = True,
        compress: bool = False,
    ) -> tuple[int, dict[str, str], bytes]:
        """发请求，返回 (状态码, 响应头, 响应体)。

        Args:
            raise_for_status: 状态码 >= 400 时是否抛异常。
                MKCOL 对已存在目录返回 405 属于正常情况，需要关闭。
        """
        request_headers = dict(self._auth_headers())
        request_headers.update(headers or {})
        try:
            resp = self._pool.request(
                method,
                url,
                body=body,
                headers=request_headers,
                raise_for_status=raise_for_status,
                compress=compress,
            )
        except HttpError as exc:
            if exc.status == 404:
                raise FileNotFound(f"路径不存在（HTTP 404）：{url}") from exc
            raise WebDavError(str(exc)) from exc
        except ConnectionError_ as exc:
            raise StorageError(f"请求失败：{exc}") from exc
        try:
            data = resp.read()
            head = resp.headers
            return resp.status, head, data
        finally:
            resp.close()

    def _request_stream(self, method: str, url: str) -> Any:
        """发起请求并返回未读取的响应流（用于大文件下载）。

        与 :meth:`_request` 的区别是不把响应体读进内存。调用方负责
        ``read()`` 到 EOF 并 ``close()``。

        Args:
            method: HTTP 方法。
            url: 目标 URL。

        Returns:
            :class:`~unifile._http.HttpResponse`。
        """
        headers = dict(self._auth_headers())
        try:
            return self._pool.request(
                method, url, headers=headers, compress=False
            )
        except HttpError as exc:
            if exc.status == 404:
                raise FileNotFound(f"路径不存在（HTTP 404）：{url}") from exc
            raise WebDavError(str(exc)) from exc
        except ConnectionError_ as exc:
            raise StorageError(f"请求失败：{exc}") from exc

    # ------------------------------------------------------------------
    # 核心原语
    # ------------------------------------------------------------------
    def _list(self, path: str) -> list[FileInfo]:
        """用 PROPFIND 列举目录。"""
        url = self._url(path)
        body = (
            '<?xml version="1.0" encoding="utf-8" ?>'
            '<D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'
        ).encode("utf-8")
        status, _headers, data = self._request(
            "PROPFIND",
            url,
            body=body,
            headers={
                "Depth": "1",
                "Content-Type": "application/xml; charset=utf-8",
            },
            compress=True,
        )
        if status == 404:
            raise FileNotFound(f"路径不存在：{path}")
        if status not in (207, 200):
            raise WebDavError(f"PROPFIND 失败（HTTP {status}）：{url}")

        return self._parse_multistatus(data, path)

    def _parse_multistatus(self, data: bytes, base_path: str) -> list[FileInfo]:
        """解析 207 Multi-Status 响应。"""
        try:
            root = ET.fromstring(data)
        except ET.ParseError as exc:
            raise WebDavError(f"无法解析 PROPFIND 响应：{exc}") from exc

        base_url = self._url(base_path).rstrip("/")
        # href 是路径形式（可能是绝对 URL），统一取路径部分来比较
        base_path_only = urlsplit(base_url).path.rstrip("/")
        items: list[FileInfo] = []
        for response in root.findall(f"{_DAV_NS}response"):
            href = response.findtext(f"{_DAV_NS}href") or ""
            if not href:
                continue
            # 跳过目录自身
            href_path = urlsplit(href).path.rstrip("/")
            if href_path == base_path_only:
                continue

            propstat = response.find(f"{_DAV_NS}propstat")
            props = propstat.find(f"{_DAV_NS}prop") if propstat is not None else None
            if props is None:
                continue

            resourcetype = props.find(f"{_DAV_NS}resourcetype")
            is_dir = resourcetype is not None and resourcetype.find(
                f"{_DAV_NS}collection"
            ) is not None

            size = 0
            size_el = props.find(f"{_DAV_NS}getcontentlength")
            if size_el is not None and size_el.text:
                try:
                    size = int(size_el.text)
                except ValueError:
                    size = 0

            mtime = 0.0
            mtime_el = props.find(f"{_DAV_NS}getlastmodified")
            if mtime_el is not None and mtime_el.text:
                mtime = self._parse_http_date(mtime_el.text)

            name = self._name_from_href(href)
            if not name:
                continue
            child = f"{norm_path(base_path)}/{name}" if norm_path(base_path) else name

            items.append(
                FileInfo(
                    path=norm_path(child),
                    size=0 if is_dir else size,
                    is_dir=is_dir,
                    mtime=mtime,
                    storage=self.kind,
                    extra={"href": href},
                )
            )
        return items

    def _name_from_href(self, href: str) -> str:
        """从 href 里取最后一段文件名。"""
        path = urlsplit(href).path.rstrip("/")
        return path.rsplit("/", 1)[-1] if "/" in path else path

    @staticmethod
    def _parse_http_date(value: str) -> float:
        """解析 RFC 1123 日期。"""
        from email.utils import parsedate_to_datetime

        try:
            return parsedate_to_datetime(value).timestamp()
        except (TypeError, ValueError):
            return 0.0

    def _stat(self, path: str) -> FileInfo:
        url = self._url(path)
        try:
            status, headers, _data = self._request("HEAD", url)
        except (FileNotFound, WebDavError):
            # 404：路径不存在；405：HEAD 不允许（多半是目录）
            # 两种情况都用 PROPFIND Depth:0 确认
            body = (
                '<?xml version="1.0" encoding="utf-8" ?>'
                '<D:propfind xmlns:D="DAV:"><D:allprop/></D:propfind>'
            ).encode("utf-8")
            status, _h, data = self._request(
                "PROPFIND",
                url,
                body=body,
                headers={"Depth": "0", "Content-Type": "application/xml; charset=utf-8"},
                compress=True,
            )
            if status == 404:
                raise FileNotFound(f"路径不存在：{path}")
            items = self._parse_multistatus(data, path)
            if items:
                return items[0]
            return FileInfo(
                path=norm_path(path), is_dir=True, storage=self.kind
            )
        size = 0
        length = headers.get("content-length")
        if length:
            try:
                size = int(length)
            except ValueError:
                size = 0
        mtime = 0.0
        last = headers.get("last-modified")
        if last:
            mtime = self._parse_http_date(last)
        return FileInfo(
            path=norm_path(path),
            size=size,
            is_dir=False,
            mtime=mtime,
            storage=self.kind,
            extra={"etag": headers.get("etag")},
        )

    def _read_bytes(self, path: str) -> bytes:
        status, _headers, data = self._request("GET", self._url(path))
        if status == 404:
            raise FileNotFound(f"文件不存在：{path}")
        return data

    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        url = self._url(path)
        parent = self._relative_parent(path)
        if parent:
            self._makedirs(parent, exist_ok=True)
        status, _headers, _data = self._request(
            "PUT", url, body=data, headers={"Content-Type": "application/octet-stream"}
        )
        if status not in (200, 201, 204):
            raise WebDavError(f"写入失败（HTTP {status}）：{path}")
        return FileInfo(path=norm_path(path), size=len(data), storage=self.kind)

    def _delete(self, path: str, recursive: bool = False) -> None:
        url = self._url(path)
        if recursive:
            for item in self.walk(path):
                if item.is_dir:
                    continue
                self._request("DELETE", self._url(item.path))
            dirs = [i for i in self.walk(path) if i.is_dir]
            dirs.sort(key=lambda i: len(i.path), reverse=True)
            for item in dirs:
                self._request("DELETE", self._url(item.path))
            self._request("DELETE", url)
            return
        status, _headers, _data = self._request("DELETE", url)
        if status == 404:
            raise FileNotFound(f"路径不存在：{path}")
        if status not in (200, 202, 204):
            raise WebDavError(f"删除失败（HTTP {status}）：{path}")

    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        """创建目录（逐级创建，WebDAV 的 MKCOL 不支持递归）。"""
        rel = norm_path(path)
        if not rel:
            return
        current = ""
        for part in rel.split("/"):
            current += "/" + part
            url = self._url(current)
            # 405 表示目录已存在，属于正常情况，所以不抛异常
            status, _headers, _data = self._request(
                "MKCOL", url, raise_for_status=False
            )
            if status == 405 and exist_ok:
                continue
            if status not in (201, 405):
                raise WebDavError(f"创建目录失败（HTTP {status}）：{current}")

    # ------------------------------------------------------------------
    # WebDAV 特有能力
    # ------------------------------------------------------------------
    def _download_to(self, path: str, target: str, *, progress: Any = None) -> str:
        """流式下载到指定路径（大文件不进内存）。"""
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录：{path}")
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        resp = self._request_stream("GET", self._url(path))
        done = 0
        try:
            with open(target, "wb") as fh:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    fh.write(chunk)
                    done += len(chunk)
                    if progress:
                        progress(done, info.size)
        finally:
            resp.close()
        if progress:
            progress(done, done)
        return target

    def upload(self, local_path: str, path: str = "", *, progress: Any = None) -> FileInfo:
        """上传文件。"""
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        remote = norm_path(path) or base_name(local_path)
        parent = self._relative_parent(remote)
        if parent:
            self._makedirs(parent, exist_ok=True)
        with open(local_path, "rb") as fh:
            data = fh.read()
        info = self._write_bytes(remote, data)
        if progress:
            progress(len(data), len(data))
        return info

    @staticmethod
    def _relative_parent(path: str) -> str:
        rel = norm_path(path)
        head, _, _ = rel.rpartition("/")
        return head

    def move(self, src: str, dst: str) -> FileInfo:
        """用 MOVE 移动/重命名（服务端操作）。"""
        status, _headers, _data = self._request(
            "MOVE",
            self._url(src),
            headers={"Destination": self._url(dst), "Overwrite": "T"},
        )
        if status not in (200, 201, 204):
            raise WebDavError(f"移动失败（HTTP {status}）：{src} → {dst}")
        return FileInfo(path=norm_path(dst), storage=self.kind)

    def copy(self, src: str, dst: str) -> FileInfo:
        """用 COPY 复制（服务端操作）。"""
        status, _headers, _data = self._request(
            "COPY",
            self._url(src),
            headers={"Destination": self._url(dst), "Overwrite": "T"},
        )
        if status not in (200, 201, 204):
            raise WebDavError(f"复制失败（HTTP {status}）：{src} → {dst}")
        return FileInfo(path=norm_path(dst), storage=self.kind)

    def close(self) -> None:
        """关闭 HTTP 连接池。"""
        self._pool.close()

    def __enter__(self) -> "WebDavStorage":
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()
