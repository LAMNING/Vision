"""FTP 后端。

基于标准库 ``ftplib``，支持主动/被动模式、目录列举（含 MLSD）、
流式上传下载。
"""

from __future__ import annotations

import ftplib
import io
import os
import posixpath
import re
import threading
from typing import Any

from ._util import base_name, join_path, norm_path
from .base import (
    FileInfo,
    FileNotFound,
    NotAFile,
    Storage,
    StorageError,
)

_MLSD_FACT_RE = re.compile(r"(\w+)=([^;]+)")


class FtpStorage(Storage):
    """FTP 后端。

    Example:
        >>> store = FtpStorage("ftp.example.com", user="anonymous", password="x")
        >>> store.upload("data.csv", "pub/data.csv")
    """

    kind = "ftp"
    preserves_mtime = True

    def __init__(
        self,
        host: str,
        *,
        port: int = 21,
        user: str = "anonymous",
        password: str = "",
        root: str = "",
        passive: bool = True,
        timeout: float = 30.0,
        encoding: str = "utf-8",
        use_tls: bool = False,
        keep_alive: bool = False,
    ) -> None:
        """
        Args:
            host: FTP 服务器地址。
            port: 端口，默认 21。
            user: 用户名。
            password: 密码。
            root: 远端根目录（相对登录后的起始目录）。
            passive: 是否使用被动模式；部分网络环境下需要改为主动模式。
            timeout: socket 超时（秒）。
            encoding: 远端路径编码，默认 utf-8。
            use_tls: 是否使用 FTPS（显式 TLS）。
            keep_alive: 是否启用 FTP 主动保活（``ftplib`` 的 ``FTP_TLS`` 不支持）。
        """
        super().__init__(root)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.passive = passive
        self.timeout = timeout
        self.encoding = encoding
        self.use_tls = use_tls
        self.keep_alive = keep_alive
        # FTP 只有一条控制连接，并发使用会串命令、数据连接错乱。
        # 每个线程各自持有一条连接，close() 时统一回收。
        self._local = threading.local()
        self._all_conns: list[ftplib.FTP] = []
        self._conns_lock = threading.Lock()

    # ------------------------------------------------------------------
    # 连接管理
    # ------------------------------------------------------------------
    def connect(self) -> ftplib.FTP:
        """取当前线程的连接（幂等；每个线程一条独立连接）。

        必须按线程隔离：``ftplib.FTP`` 的控制连接是有状态的，
        两个线程同时 ``storbinary`` 会把命令和数据通道搅在一起。
        """
        ftp: ftplib.FTP | None = getattr(self._local, "conn", None)
        if ftp is not None:
            try:
                ftp.voidcmd("NOOP")
                return ftp
            except Exception:
                self._local.conn = None
        if self.use_tls:
            ftp = ftplib.FTP_TLS(timeout=self.timeout)
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
            ftp.prot_p()
        else:
            ftp = ftplib.FTP(timeout=self.timeout)
            ftp.connect(self.host, self.port)
            ftp.login(self.user, self.password)
        ftp.set_pasv(self.passive)
        ftp.encoding = self.encoding
        self._local.conn = ftp
        with self._conns_lock:
            self._all_conns.append(ftp)
        if self.root:
            # _remote 已经把 root 拼进去了，这里不能再传 self.root（会拼两次）
            ftp.cwd(self._remote(""))
        return ftp

    def close(self) -> None:
        """关闭所有线程持有的连接。"""
        with self._conns_lock:
            conns, self._all_conns = self._all_conns, []
        for ftp in conns:
            try:
                ftp.quit()
            except Exception:
                try:
                    ftp.close()
                except Exception:
                    pass
        self._local = threading.local()

    def __enter__(self) -> "FtpStorage":
        self.connect()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()

    def _remote(self, path: str) -> str:
        """把相对路径映射为远端绝对路径。"""
        rel = norm_path(path)
        root = norm_path(self.root)
        if root and rel:
            return "/" + posixpath.join(root, rel)
        if root:
            return "/" + root
        return "/" + rel if rel else "/"

    def _relative(self, remote: str) -> str:
        """把远端绝对路径映射回相对路径。"""
        rel = norm_path(remote)
        root = norm_path(self.root)
        if root:
            if rel == root:
                return ""
            if rel.startswith(root + "/"):
                rel = rel[len(root) + 1 :]
        return norm_path(rel)

    # ------------------------------------------------------------------
    # 核心原语
    # ------------------------------------------------------------------
    def _list(self, path: str) -> list[FileInfo]:
        ftp = self.connect()
        remote = self._remote(path)
        items: list[FileInfo] = []
        try:
            entries = list(ftp.mlsd(remote, facts=["type", "size", "modify"]))
        except (ftplib.error_perm, AttributeError):
            entries = self._list_fallback(ftp, remote)
        for name, facts in entries:
            if name in (".", ".."):
                continue
            kind = facts.get("type", "file")
            is_dir = kind in ("dir", "cdir", "pdir")
            size = int(facts.get("size", 0) or 0)
            mtime = 0.0
            modify = facts.get("modify")
            if modify:
                try:
                    import datetime as _dt

                    mtime = _dt.datetime.strptime(modify, "%Y%m%d%H%M%S").timestamp()
                except ValueError:
                    mtime = 0.0
            child = join_path(norm_path(path), name)
            items.append(
                FileInfo(
                    path=child,
                    size=0 if is_dir else size,
                    is_dir=is_dir,
                    mtime=mtime,
                    storage=self.kind,
                    extra={"remote": self._remote(child)},
                )
            )
        return items

    def _list_fallback(self, ftp: ftplib.FTP, remote: str) -> list[tuple[str, dict[str, str]]]:
        """服务器不支持 MLSD 时的降级方案：NLST + SIZE。"""
        entries: list[tuple[str, dict[str, str]]] = []
        for name in ftp.nlst(remote):
            base = posixpath.basename(name.rstrip("/"))
            if base in (".", ".."):
                continue
            facts: dict[str, str] = {"type": "file", "size": "0"}
            try:
                facts["size"] = str(ftp.size(name))
            except Exception:
                try:
                    ftp.cwd(name)
                    facts["type"] = "dir"
                    ftp.cwd(remote)
                except Exception:
                    pass
            entries.append((base, facts))
        return entries

    def _stat(self, path: str) -> FileInfo:
        ftp = self.connect()
        remote = self._remote(path)
        if not remote or remote == "/":
            return FileInfo(path="", is_dir=True, storage=self.kind)
        try:
            size = ftp.size(remote)
            mtime = 0.0
            try:
                resp = ftp.sendcmd(f"MDTM {remote}")
                mtime = _parse_mdtm(resp)
            except Exception:
                pass
            return FileInfo(
                path=norm_path(path),
                size=int(size),
                is_dir=False,
                mtime=mtime,
                storage=self.kind,
                extra={"remote": remote},
            )
        except ftplib.error_perm:
            pass
        # 可能是目录
        parent = posixpath.dirname(remote.rstrip("/"))
        name = posixpath.basename(remote.rstrip("/"))
        try:
            for entry_name, facts in ftp.mlsd(parent or "/", facts=["type", "size"]):
                if entry_name == name:
                    is_dir = facts.get("type") in ("dir", "cdir", "pdir")
                    return FileInfo(
                        path=norm_path(path),
                        size=0 if is_dir else int(facts.get("size", 0) or 0),
                        is_dir=is_dir,
                        storage=self.kind,
                        extra={"remote": remote},
                    )
        except Exception as exc:
            raise FileNotFound(f"路径不存在：{path}") from exc
        raise FileNotFound(f"路径不存在：{path}")

    def _read_bytes(self, path: str) -> bytes:
        ftp = self.connect()
        remote = self._remote(path)
        buffer = io.BytesIO()
        try:
            ftp.retrbinary(f"RETR {remote}", buffer.write)
        except ftplib.error_perm as exc:
            raise FileNotFound(f"文件不存在：{path}") from exc
        except (ConnectionResetError, BrokenPipeError, ConnectionAbortedError) as exc:
            # 服务端在 RETR 失败时可能直接断开连接（如文件不存在时回 550 再关）
            raise FileNotFound(f"文件不存在或连接被中断：{path}") from exc
        except OSError as exc:
            raise StorageError(f"读取失败：{path}（{exc}）") from exc
        return buffer.getvalue()

    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        ftp = self.connect()
        remote = self._remote(path)
        parent = posixpath.dirname(remote)
        if parent and parent != "/":
            self._ensure_dir(ftp, parent)
        try:
            ftp.storbinary(f"STOR {remote}", io.BytesIO(data))
        except ftplib.error_perm as exc:
            raise StorageError(f"上传失败：{path}（{exc}）") from exc
        return FileInfo(path=norm_path(path), size=len(data), storage=self.kind)

    def _delete(self, path: str, recursive: bool = False) -> None:
        ftp = self.connect()
        remote = self._remote(path)
        if recursive:
            for item in self.walk(path):
                if item.is_dir:
                    continue
                try:
                    ftp.delete(item.extra.get("remote", self._remote(item.path)))
                except ftplib.error_perm:
                    pass
            # 从深到浅删目录
            dirs = [i for i in self.walk(path) if i.is_dir]
            dirs.sort(key=lambda i: len(i.path), reverse=True)
            for item in dirs:
                try:
                    ftp.rmd(item.extra.get("remote", self._remote(item.path)))
                except ftplib.error_perm:
                    pass
            try:
                ftp.rmd(remote)
            except ftplib.error_perm as exc:
                if not self.passive:
                    pass
                else:
                    raise StorageError(f"删除目录失败：{path}（{exc}）") from exc
            return
        try:
            if self._is_dir(ftp, remote):
                ftp.rmd(remote)
            else:
                ftp.delete(remote)
        except ftplib.error_perm as exc:
            raise StorageError(f"删除失败：{path}（{exc}）") from exc

    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        ftp = self.connect()
        remote = self._remote(path)
        self._ensure_dir(ftp, remote)

    def _ensure_dir(self, ftp: ftplib.FTP, remote: str) -> None:
        """递归创建远端目录（逐级尝试，已存在则忽略）。"""
        current = ""
        for part in [p for p in remote.split("/") if p]:
            current += "/" + part
            try:
                ftp.mkd(current)
            except ftplib.error_perm:
                if not exist_ok:
                    raise StorageError(f"目录已存在：{current}") from None

    @staticmethod
    def _is_dir(ftp: ftplib.FTP, remote: str) -> bool:
        try:
            ftp.size(remote)
            return False
        except ftplib.error_perm:
            return True

    # ------------------------------------------------------------------
    # FTP 特有能力
    # ------------------------------------------------------------------
    def _download_to(self, path: str, target: str, *, progress: Any = None) -> str:
        """流式下载到指定路径，内存占用与文件大小无关。"""
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录：{path}")
        ftp = self.connect()
        remote = self._remote(path)
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        total = info.size
        state = {"done": 0}

        def _on_chunk(chunk: bytes) -> None:
            """retrbinary 的回调只给数据块：写盘 + 累加进度。"""
            fh.write(chunk)
            state["done"] += len(chunk)
            if progress:
                progress(state["done"], total)

        with open(target, "wb") as fh:
            try:
                ftp.retrbinary(f"RETR {remote}", _on_chunk)
            except ftplib.error_perm as exc:
                raise FileNotFound(f"下载失败：{path}（{exc}）") from exc
        if progress:
            progress(state["done"], total)
        return target

    def upload(self, local_path: str, path: str = "", *, progress: Any = None) -> FileInfo:
        """流式上传。"""
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        ftp = self.connect()
        remote = self._remote(norm_path(path) or base_name(local_path))
        parent = posixpath.dirname(remote)
        if parent and parent != "/":
            self._ensure_dir(ftp, parent)
        total = os.path.getsize(local_path)
        state = {"done": 0}

        def _cb(chunk: bytes) -> None:
            state["done"] += len(chunk)
            if progress:
                progress(state["done"], total)

        with open(local_path, "rb") as fh:
            try:
                ftp.storbinary(f"STOR {remote}", fh, callback=_cb)
            except ftplib.error_perm as exc:
                raise StorageError(f"上传失败：{path}（{exc}）") from exc
        return FileInfo(
            path=norm_path(path) or base_name(local_path),
            size=total,
            storage=self.kind,
        )

    def rename(self, src: str, dst: str) -> FileInfo:
        """FTP 重命名（服务端操作）。"""
        ftp = self.connect()
        ftp.rename(self._remote(src), self._remote(dst))
        return FileInfo(path=norm_path(dst), storage=self.kind)


def _parse_mdtm(response: str) -> float:
    """解析 ``MDTM`` 响应，如 ``213 20240102150400``。"""
    parts = response.split()
    if len(parts) < 2:
        return 0.0
    try:
        import datetime as _dt

        return _dt.datetime.strptime(parts[1], "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return 0.0
