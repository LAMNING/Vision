"""统一存储抽象层。

所有后端（本地、OSS/S3、FTP）实现同一套接口，因此上层代码可以用
统一的语法操作不同存储介质：

    from unifile import LocalStorage, OssStorage, FtpStorage

    store = OssStorage(bucket="my-bucket", endpoint="oss-cn-hangzhou.aliyuncs.com")
    store.upload("report.pdf", "docs/2026/report.pdf")
"""

from __future__ import annotations

import abc
import fnmatch
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, Sequence

from ._util import base_name, human_size, join_path, norm_path, parent_path

ProgressCallback = Callable[[int, int], None]
"""进度回调，签名为 ``(已传输字节, 总字节)``。总字节未知时传 -1。"""


def _ignored(rel_path: str, patterns: Sequence[str] | None) -> bool:
    """判断相对路径是否命中忽略规则。

    支持两种写法：

    - 匹配路径中的**任一目录名**：``.git``、``node_modules``
      → 整个子树被跳过；
    - 匹配**完整相对路径**：``*.log``、``build/*.tmp``。

    Args:
        rel_path: 相对路径（POSIX 风格）。
        patterns: fnmatch 通配符列表；``None`` 或空表示不过滤。

    Returns:
        是否应跳过。
    """
    if not patterns:
        return False
    for pattern in patterns:
        if fnmatch.fnmatch(rel_path, pattern):
            return True
        if any(fnmatch.fnmatch(part, pattern) for part in rel_path.split("/")):
            return True
    return False


def _run_jobs(jobs: list[Any], worker: Callable[[Any], Any], concurrency: int) -> list[Any]:
    """按并发度执行一批任务，返回结果并保持输入顺序。

    并发度为 1 时退化为普通循环（不引入线程开销）。
    任一任务抛异常会立即向上传播，其余任务被取消。

    Args:
        jobs: 任务参数列表，逐项传给 ``worker``。
        worker: 单个任务的处理函数。
        concurrency: 并发线程数。

    Returns:
        与 ``jobs`` 等长、同顺序的结果列表。
    """
    if concurrency <= 1 or len(jobs) <= 1:
        return [worker(job) for job in jobs]

    results: list[Any] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=min(concurrency, len(jobs))) as pool:
        futures = {pool.submit(worker, job): index for index, job in enumerate(jobs)}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _guard_progress(progress: ProgressCallback | None) -> ProgressCallback | None:
    """给进度回调加锁，避免并发传输时回调被多线程同时进入。"""
    if progress is None:
        return None
    lock = threading.Lock()

    def safe(done: int, total: int) -> None:
        with lock:
            progress(done, total)

    return safe


DEFAULT_CONCURRENCY = 4
"""批量传输的默认并发数。"""


class StorageError(Exception):
    """存储操作通用异常基类。"""


class FileNotFound(StorageError):
    """目标路径不存在。"""


class PermissionDenied(StorageError):
    """权限不足。"""


class FileExists(StorageError):
    """目标已存在且不允许覆盖。"""


class NotADirectory(StorageError):
    """期望目录但实际是文件。"""


class NotAFile(StorageError):
    """期望文件但实际是目录。"""


class UnsupportedOperation(StorageError):
    """该后端不支持此操作。"""


@dataclass
class FileInfo:
    """统一的文件元信息。

    Attributes:
        path: 相对于后端根目录的规范化路径（POSIX 风格，无前导斜杠）。
        size: 文件大小（字节）；目录为 0。
        is_dir: 是否为目录。
        mtime: 最后修改时间（POSIX 时间戳，秒）。
        etag: 对象存储的 ETag（若有）。
        storage: 所属后端类型，如 ``local`` / ``oss`` / ``ftp``。
        extra: 后端特有的附加信息。
    """

    path: str
    size: int = 0
    is_dir: bool = False
    mtime: float = 0.0
    etag: str | None = None
    storage: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        """文件名（不含目录）。"""
        return base_name(self.path)

    @property
    def parent(self) -> str:
        """父目录路径。"""
        return parent_path(self.path)

    @property
    def human_size(self) -> str:
        """人类可读的大小。"""
        return human_size(self.size)

    def __str__(self) -> str:  # pragma: no cover - 展示用
        kind = "d" if self.is_dir else "-"
        return f"{kind} {self.human_size:>9}  {self.path}"


class Storage(abc.ABC):
    """存储后端抽象基类。

    子类需要实现核心的 :meth:`_list`、:meth:`_stat`、:meth:`_read_bytes`、
    :meth:`_write_bytes`、:meth:`_delete`、:meth:`_makedirs`，其余方法
    由基类基于这些原语组合出更丰富的语义。
    """

    #: 后端类型标识
    kind: str = "storage"

    #: 该后端的时间戳是否等于内容本身的修改时间。
    #: 对象存储的 ``LastModified`` 是「对象创建时刻」（服务端写入的时间），
    #: 与上传前本地文件的 mtime 毫无关系，因此跨到对象存储做增量同步时
    #: 不能拿它当判据——否则每次都会判成"已变更"，退化为全量传输。
    preserves_mtime: bool = False

    def __init__(self, root: str = "") -> None:
        """
        Args:
            root: 后端根路径。本地后端是本地目录，OSS 是 key 前缀，
                FTP 是远端目录。所有操作都相对于该根。
        """
        self.root = norm_path(root)

    # ------------------------------------------------------------------
    # 需要子类实现的核心原语
    # ------------------------------------------------------------------
    @abc.abstractmethod
    def _list(self, path: str) -> list[FileInfo]:
        """列出目录下的直接子项。"""

    @abc.abstractmethod
    def _stat(self, path: str) -> FileInfo:
        """获取单个路径的元信息，不存在时抛 :class:`FileNotFound`。"""

    @abc.abstractmethod
    def _read_bytes(self, path: str) -> bytes:
        """读取整个文件内容。"""

    @abc.abstractmethod
    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        """整体写入文件（覆盖）。"""

    @abc.abstractmethod
    def _delete(self, path: str, recursive: bool = False) -> None:
        """删除文件或目录。"""

    @abc.abstractmethod
    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        """创建目录。"""

    # ------------------------------------------------------------------
    # 公共 API
    # ------------------------------------------------------------------
    def exists(self, path: str) -> bool:
        """路径是否存在。"""
        try:
            self._stat(path)
            return True
        except FileNotFound:
            return False

    def is_dir(self, path: str) -> bool:
        """是否为目录。"""
        try:
            return self._stat(path).is_dir
        except FileNotFound:
            return False

    def is_file(self, path: str) -> bool:
        """是否为文件。"""
        try:
            return not self._stat(path).is_dir
        except FileNotFound:
            return False

    def list(self, path: str = "", *, recursive: bool = False) -> list[FileInfo]:
        """列出目录内容。

        Args:
            path: 相对目录路径，空串表示根目录。
            recursive: 是否递归列出所有层级。

        Returns:
            排序后的 :class:`FileInfo` 列表，目录在前，同类按名称排序。

        Note:
            需要排序就必须把结果全部装进内存。目录很大时（十万级以上）
            改用 :meth:`iter_list`，它不排序但内存占用是常量。
        """
        if recursive:
            return list(self.walk(path))
        items = self._list(path)
        items.sort(key=lambda i: (not i.is_dir, i.name))
        return items

    def iter_list(self, path: str = "", *, recursive: bool = False) -> Iterator[FileInfo]:
        """流式列举目录内容（生成器）。

        与 :meth:`list` 的区别：

        - 不把结果攒成列表，逐项产出，内存占用与条目数无关；
        - **不排序**，顺序由后端决定（OSS 按 key 字典序，本地按文件系统顺序）。

        这是「列举百万级对象」的正确方式：

        >>> for item in store.iter_list("backup", recursive=True):
        ...     if item.size > 1024 ** 3:
        ...         print(item.path)

        Args:
            path: 相对目录路径，空串表示根目录。
            recursive: 是否递归遍历子目录。

        Yields:
            :class:`FileInfo`。
        """
        if recursive:
            yield from self._iter_deep(path)
        else:
            yield from self._iter_list(path)

    def _iter_list(self, path: str) -> Iterator[FileInfo]:
        """列举一层子项（可覆写为真正的流式实现）。

        默认实现基于 :meth:`_list`，因此一次性读入一层。对象存储这类
        本身分页列举的后端应覆写此方法，直接产出分页结果。
        """
        yield from self._list(path)

    def _iter_deep(self, path: str) -> Iterator[FileInfo]:
        """递归流式遍历。"""
        for item in self._iter_list(path):
            yield item
            if item.is_dir:
                yield from self._iter_deep(item.path)

    def walk(self, path: str = "") -> Iterator[FileInfo]:
        """递归遍历目录，产出所有后代项。

        基于 :meth:`iter_list`，逐目录展开，不会一次读完整棵子树。
        """
        yield from self._iter_deep(path)

    def stat(self, path: str) -> FileInfo:
        """获取元信息。"""
        return self._stat(path)

    def mkdir(self, path: str, *, exist_ok: bool = True) -> FileInfo:
        """创建目录。"""
        self._makedirs(path, exist_ok=exist_ok)
        return self._stat(path)

    def read_bytes(self, path: str) -> bytes:
        """读取文件全部内容。"""
        return self._read_bytes(path)

    def read_text(self, path: str, encoding: str = "utf-8") -> str:
        """读取文件并按文本解码。"""
        return self._read_bytes(path).decode(encoding)

    def write_bytes(self, path: str, data: bytes) -> FileInfo:
        """整体写入文件。"""
        return self._write_bytes(path, data)

    def write_text(self, path: str, text: str, encoding: str = "utf-8") -> FileInfo:
        """写入文本文件。"""
        return self._write_bytes(path, text.encode(encoding))

    def delete(self, path: str, *, recursive: bool = False) -> None:
        """删除文件或目录。

        Args:
            path: 目标路径。
            recursive: 目录非空时是否连同内容一起删除。
        """
        info = self._stat(path)
        if info.is_dir and not recursive:
            children = self._list(path)
            if children:
                raise StorageError(
                    f"目录非空，删除被拒绝：{path}（使用 recursive=True 强制删除）"
                )
        self._delete(path, recursive=recursive)

    def rmtree(self, path: str = "") -> None:
        """递归删除目录及其全部内容。"""
        self._delete(path, recursive=True)

    def download(self, path: str, local_path: str, *, progress: ProgressCallback | None = None) -> str:
        """下载文件到本地。

        Args:
            path: 远端文件路径。
            local_path: 本地目标路径。已有目录或以路径分隔符结尾时，
                视为目录，文件会写进该目录并沿用原文件名。
            progress: 进度回调 ``(done, total)``。

        Returns:
            实际写入的本地路径。
        """
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录，请使用 download_dir：{path}")
        target = self._resolve_local_target(local_path, info.name)
        return self._download_to(path, target, progress=progress)

    def _download_to(
        self, path: str, target: str, *, progress: ProgressCallback | None = None
    ) -> str:
        """把远端文件写到指定本地文件路径。

        与 :meth:`download` 的区别是不做「目标是目录还是文件」的推断，
        因此可以直接用于批量下载。子类应覆写为流式实现（避免大文件整读）。

        Args:
            path: 远端文件路径。
            target: 本地目标文件路径（精确路径，不会被当作目录）。
            progress: 进度回调。

        Returns:
            写入的本地路径。
        """
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        data = self._read_bytes(path)
        with open(target, "wb") as fh:
            fh.write(data)
        if progress:
            progress(len(data), len(data))
        return target

    @staticmethod
    def _resolve_local_target(local_path: str, default_name: str) -> str:
        """判断本地目标是文件还是目录。

        规则：已存在的目录、以路径分隔符结尾、或没有扩展名的路径 → 视为目录。
        """
        if not local_path:
            return default_name
        if os.path.isdir(local_path):
            return os.path.join(local_path, default_name)
        if local_path.endswith(("/", "\\")):
            return os.path.join(local_path, default_name)
        if not os.path.splitext(local_path)[1]:
            return os.path.join(local_path, default_name)
        return local_path

    def download_dir(
        self,
        path: str,
        local_dir: str,
        *,
        progress: ProgressCallback | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        ignore: Sequence[str] | None = None,
    ) -> str:
        """递归下载目录到本地。

        Args:
            path: 远端目录。
            local_dir: 本地父目录；实际内容会写进 ``local_dir/<目录名>``。
            progress: 进度回调（按文件触发，并发时顺序不保证）。
            concurrency: 并发下载的文件数。``1`` 表示串行。
            ignore: 忽略规则（fnmatch 通配符），见 :func:`_ignored`。

        Returns:
            实际写入的本地目录路径。
        """
        target = os.path.join(local_dir, base_name(path) or "root")
        os.makedirs(target, exist_ok=True)
        root = norm_path(path)
        jobs: list[tuple[str, str]] = []
        for item in self.walk(path):
            if item.is_dir:
                os.makedirs(os.path.join(target, item.path), exist_ok=True)
                continue
            rel = item.path[len(root) + 1 :] if root else item.path
            if _ignored(rel, ignore):
                continue
            dest = os.path.join(target, rel.replace("/", os.sep))
            jobs.append((item.path, dest))

        cb = _guard_progress(progress) if concurrency > 1 else progress
        _run_jobs(jobs, lambda job: self._download_to(job[0], job[1], progress=cb), concurrency)
        return target

    def upload(
        self, local_path: str, path: str = "", *, progress: ProgressCallback | None = None
    ) -> FileInfo:
        """上传本地文件到后端。

        Args:
            local_path: 本地文件路径。
            path: 远端目标路径；留空则使用本地文件名。
            progress: 进度回调。
        """
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        remote = norm_path(path) or base_name(local_path)
        parent = parent_path(remote)
        if parent:
            self._makedirs(parent, exist_ok=True)
        with open(local_path, "rb") as fh:
            data = fh.read()
        info = self._write_bytes(remote, data)
        if progress:
            progress(len(data), len(data))
        return info

    def upload_dir(
        self,
        local_dir: str,
        path: str = "",
        *,
        progress: ProgressCallback | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        ignore: Sequence[str] | None = None,
    ) -> list[FileInfo]:
        """递归上传本地目录。

        默认 4 路并发——逐个串行上传时，单个小文件的耗时几乎全是往返延迟，
        并发能把这条路占满。

        Args:
            local_dir: 本地目录。
            path: 远端目标目录前缀。
            progress: 进度回调（按文件触发，并发时顺序不保证）。
            concurrency: 并发上传的文件数。``1`` 表示串行。
            ignore: 忽略规则（fnmatch 通配符）。例如
                ``ignore=(".git", "__pycache__", "*.log")``。
                被忽略的目录整棵子树都不会被遍历。

        Returns:
            上传成功的 :class:`FileInfo` 列表（与遍历顺序一致）。

        Example:
            >>> store.upload_dir("./site", "public", ignore=(".git", "node_modules"))
        """
        base = os.path.abspath(local_dir)
        if not os.path.isdir(base):
            raise NotADirectory(f"本地目录不存在：{local_dir}")

        jobs: list[tuple[str, str]] = []
        for dirpath, dirnames, filenames in os.walk(base):
            rel_dir = os.path.relpath(dirpath, base).replace(os.sep, "/")
            rel_dir = "" if rel_dir == "." else rel_dir
            # 就地裁剪 dirnames：被忽略的目录整棵子树不进入遍历
            dirnames[:] = [
                name
                for name in dirnames
                if not _ignored(f"{rel_dir}/{name}" if rel_dir else name, ignore)
            ]
            for filename in filenames:
                rel = f"{rel_dir}/{filename}" if rel_dir else filename
                if _ignored(rel, ignore):
                    continue
                jobs.append((os.path.join(dirpath, filename), join_path(path, rel)))

        cb = _guard_progress(progress) if concurrency > 1 else progress
        return _run_jobs(
            jobs, lambda job: self.upload(job[0], job[1], progress=cb), concurrency
        )

    def copy(self, src: str, dst: str) -> FileInfo:
        """在同一个后端内复制文件。默认走读—写实现，子类可覆盖为服务端复制。"""
        info = self._stat(src)
        if info.is_dir:
            raise NotAFile(f"暂不支持目录复制：{src}")
        return self._write_bytes(dst, self._read_bytes(src))

    def move(self, src: str, dst: str) -> FileInfo:
        """在同一个后端内移动文件。"""
        info = self.copy(src, dst)
        self._delete(src, recursive=False)
        return info

    def exists_many(self, paths: list[str]) -> dict[str, bool]:
        """批量检查路径是否存在。"""
        return {p: self.exists(p) for p in paths}

    def remove_many(self, paths: list[str], *, recursive: bool = False) -> list[str]:
        """批量删除，返回成功删除的路径列表。"""
        removed: list[str] = []
        for path in paths:
            try:
                self.delete(path, recursive=recursive)
                removed.append(path)
            except FileNotFound:
                continue
        return removed

    def size_of(self, path: str = "") -> int:
        """递归计算目录总大小（字节）。"""
        return sum(item.size for item in self.walk(path) if not item.is_dir)

    def find(
        self, path: str = "", *, pattern: str = "*.txt", recursive: bool = True
    ) -> list[FileInfo]:
        """按通配符查找文件。

        Args:
            path: 起始目录。
            pattern: 通配符模式，如 ``*.pdf``、``report_*``。
            recursive: 是否递归。
        """
        import fnmatch

        return [
            item
            for item in self.list(path, recursive=recursive)
            if not item.is_dir and fnmatch.fnmatch(item.name, pattern)
        ]

    def __contains__(self, path: str) -> bool:
        return self.exists(path)

    def __repr__(self) -> str:  # pragma: no cover - 展示用
        return f"<{type(self).__name__} kind={self.kind!r} root={self.root!r}>"
