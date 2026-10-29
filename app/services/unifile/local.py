"""本地文件系统后端。"""

from __future__ import annotations

import contextlib
import os
import shutil
import tempfile
from typing import Any, Iterator

from ._util import base_name, norm_path
from .base import (
    FileInfo,
    FileNotFound,
    NotADirectory,
    NotAFile,
    Storage,
    StorageError,
)


class LocalStorage(Storage):
    """本地文件系统后端。

    Example:
        >>> store = LocalStorage("/tmp/data")
        >>> store.write_text("notes/a.txt", "hello")
        >>> store.read_text("notes/a.txt")
        'hello'
    """

    kind = "local"
    preserves_mtime = True

    def __init__(
        self,
        root: str = ".",
        *,
        create: bool = True,
        atomic: bool = True,
        fsync: bool = True,
    ) -> None:
        """
        Args:
            root: 本地根目录。
            create: 是否在根目录不存在时自动创建。
            atomic: 写入是否走「临时文件 + 原子替换」。
                开启时写一半崩溃不会污染目标文件（目标保持旧内容或不存在）。
            fsync: 替换前是否 ``fsync`` 落盘。防断电丢数据，大文件上会有
                明显额外耗时（需要把页缓存刷到磁盘），可关闭换取速度。
        """
        super().__init__(root)
        self._root = os.path.abspath(os.path.expanduser(root))
        self.atomic = atomic
        self.fsync = fsync
        if create:
            os.makedirs(self._root, exist_ok=True)
        elif not os.path.isdir(self._root):
            raise NotADirectory(f"根目录不存在：{self._root}")

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _abs(self, path: str) -> str:
        """把相对路径映射为绝对路径。

        根目录是一个安全边界：
        - 相对路径中不允许出现 ``..`` 段（否则 ``../x`` 会被静默改写成 ``x``，
          调用方以为写到了根外，实际写到了根内，容易误判）；
        - 允许传入绝对路径，但必须落在根目录内。
        """
        raw = str(path or "").replace("\\", "/")
        segments = [s for s in raw.split("/") if s]
        if ".." in segments:
            raise StorageError(f"路径越界（不允许 ..）：{path}")
        if raw.startswith("/") or (len(raw) > 1 and raw[1] == ":"):
            candidate = os.path.abspath(os.path.expanduser(raw))
        else:
            rel = norm_path(raw)
            candidate = os.path.abspath(os.path.join(self._root, rel.replace("/", os.sep)))
        root_prefix = self._root + os.sep
        if candidate != self._root and not candidate.startswith(root_prefix):
            raise StorageError(f"路径越界：{path}")
        return candidate

    def _rel(self, abs_path: str) -> str:
        """把绝对路径映射回相对路径。"""
        rel = os.path.relpath(abs_path, self._root)
        return "" if rel == "." else norm_path(rel)

    def _info(self, abs_path: str, name: str | None = None) -> FileInfo:
        stat = os.stat(abs_path)
        is_dir = os.path.isdir(abs_path)
        return FileInfo(
            path=self._rel(abs_path),
            size=0 if is_dir else stat.st_size,
            is_dir=is_dir,
            mtime=stat.st_mtime,
            storage=self.kind,
            extra={"absolute_path": abs_path},
        )

    # ------------------------------------------------------------------
    # 核心原语
    # ------------------------------------------------------------------
    def _list(self, path: str) -> list[FileInfo]:
        target = self._abs(path)
        if not os.path.exists(target):
            raise FileNotFound(f"路径不存在：{path}")
        if not os.path.isdir(target):
            raise NotADirectory(f"不是目录：{path}")
        items: list[FileInfo] = []
        with os.scandir(target) as it:
            for entry in it:
                items.append(self._info(entry.path, entry.name))
        return items

    def _stat(self, path: str) -> FileInfo:
        target = self._abs(path)
        if not os.path.exists(target):
            raise FileNotFound(f"路径不存在：{path}")
        return self._info(target)

    def _read_bytes(self, path: str) -> bytes:
        target = self._abs(path)
        if not os.path.exists(target):
            raise FileNotFound(f"文件不存在：{path}")
        if os.path.isdir(target):
            raise NotAFile(f"目标是目录：{path}")
        with open(target, "rb") as fh:
            return fh.read()

    @contextlib.contextmanager
    def _open_write(self, target: str) -> Iterator[Any]:
        """以「临时文件 + 原子替换」方式打开目标文件写入。

        临时文件必须与目标同目录，否则 ``os.replace`` 可能跨文件系统
        而失败（``EXDEV``）。退出时若写入过程中抛异常，临时文件会被清理，
        目标文件保持原状——这是「不写出半截文件」的关键。

        Args:
            target: 目标绝对路径。

        Yields:
            可写的二进制文件对象。
        """
        if not self.atomic:
            with open(target, "wb") as fh:
                yield fh
            return

        parent = os.path.dirname(target) or "."
        os.makedirs(parent, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=parent, prefix=".unifile-", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as fh:
                yield fh
                fh.flush()
                if self.fsync:
                    os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            # 尽力清理临时文件；清理失败不能盖掉原始异常
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        target = self._abs(path)
        if os.path.isdir(target):
            raise NotAFile(f"目标是目录：{path}")
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with self._open_write(target) as fh:
            fh.write(data)
        return self._info(target)

    def _delete(self, path: str, recursive: bool = False) -> None:
        target = self._abs(path)
        if not os.path.exists(target):
            raise FileNotFound(f"路径不存在：{path}")
        if os.path.isdir(target):
            if recursive:
                shutil.rmtree(target)
            else:
                os.rmdir(target)
        else:
            os.remove(target)

    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        target = self._abs(path)
        if os.path.exists(target) and not os.path.isdir(target):
            raise NotADirectory(f"已存在且不是目录：{path}")
        try:
            os.makedirs(target, exist_ok=exist_ok)
        except FileExistsError as exc:
            raise StorageError(f"目录已存在：{path}") from exc

    # ------------------------------------------------------------------
    # 本地后端特有的高效实现
    # ------------------------------------------------------------------
    def copy(self, src: str, dst: str) -> FileInfo:
        """使用 ``shutil.copy2`` 复制，保留元数据。"""
        source = self._abs(src)
        if not os.path.exists(source):
            raise FileNotFound(f"路径不存在：{src}")
        if os.path.isdir(source):
            raise NotAFile(f"暂不支持目录复制：{src}")
        target = self._abs(dst)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.copy2(source, target)
        return self._info(target)

    def move(self, src: str, dst: str) -> FileInfo:
        """使用 ``shutil.move`` 移动。"""
        source = self._abs(src)
        if not os.path.exists(source):
            raise FileNotFound(f"路径不存在：{src}")
        target = self._abs(dst)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        shutil.move(source, target)
        return self._info(target)

    def _download_to(self, path: str, target: str, *, progress: Any = None) -> str:
        """流式复制到指定本地路径（原子替换，大文件无需整体读入内存）。"""
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录：{path}")
        source = self._abs(path)
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        total = info.size
        copied = 0
        with open(source, "rb") as src, self._open_write(target) as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if progress:
                    progress(copied, total)
        # 替换完成后再复制元数据，否则时间戳会加在临时文件上
        shutil.copystat(source, target)
        return target

    def upload(self, local_path: str, path: str = "", *, progress: Any = None) -> FileInfo:
        """使用 ``shutil.copy2`` 上传。"""
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        remote = norm_path(path) or base_name(local_path)
        target = self._abs(remote)
        parent = os.path.dirname(target)
        if parent:
            os.makedirs(parent, exist_ok=True)
        total = os.path.getsize(local_path)
        copied = 0
        with open(local_path, "rb") as src, self._open_write(target) as dst:
            while True:
                chunk = src.read(1024 * 1024)
                if not chunk:
                    break
                dst.write(chunk)
                copied += len(chunk)
                if progress:
                    progress(copied, total)
        # 保留源文件的修改时间：否则「本地 → 本地」同步每次都会判成已变更
        shutil.copystat(local_path, target)
        return self._info(target)
