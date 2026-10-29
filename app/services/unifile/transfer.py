"""跨后端传输与增量同步。

``transfer`` 把一个后端的内容搬到另一个后端，``sync`` 在其上加增量比对。
两者都走"下载到本地临时文件 → 再上传"的路径，因此内存占用与文件大小无关，
大文件不会因为跨后端而 OOM。

Example:
    >>> from unifile import LocalStorage, OssStorage, transfer, sync
    >>> local = LocalStorage("./backup")
    >>> oss = OssStorage(bucket="b", endpoint="oss-cn-hangzhou.aliyuncs.com")
    >>> sync(oss, local, "photos/")          # OSS → 本地
    >>> transfer(local, oss, "photos/")      # 本地 → OSS
"""

from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass, field
from typing import Sequence

from ._util import join_path, norm_path
from .base import (
    DEFAULT_CONCURRENCY,
    FileInfo,
    FileNotFound,
    NotADirectory,
    NotAFile,
    ProgressCallback,
    Storage,
    _guard_progress,
    _ignored,
    _run_jobs,
)


@dataclass
class SyncResult:
    """同步结果统计。

    Attributes:
        uploaded: 目标不存在、本次新建的文件路径。
        updated: 目标已存在但内容不同、本次覆盖的文件路径。
        deleted: 因源端已无对应文件而删除的目标路径（仅 ``delete=True``）。
        skipped: 两端一致、未传输的文件路径。
        failed: ``(路径, 错误信息)`` 列表。
    """

    uploaded: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    failed: list[tuple[str, str]] = field(default_factory=list)

    @property
    def transferred(self) -> int:
        """实际传输的文件数（新建 + 覆盖）。"""
        return len(self.uploaded) + len(self.updated)

    def __str__(self) -> str:  # pragma: no cover - 展示用
        return (
            f"新建 {len(self.uploaded)}、覆盖 {len(self.updated)}、"
            f"删除 {len(self.deleted)}、跳过 {len(self.skipped)}、"
            f"失败 {len(self.failed)}"
        )


def _is_directory(store: Storage, path: str) -> bool:
    """判断是否为目录，兼容对象存储的「虚拟目录」。

    对象存储里一个"目录"可能只是若干对象的公共前缀，本身没有对应实体，
    所以 ``stat`` 会返回 404。这时改为尝试列举，能列出内容就算目录。

    Args:
        store: 目标后端。
        path: 路径。

    Returns:
        是否应视为目录。
    """
    if store.is_dir(path):
        return True
    try:
        next(store.iter_list(path))
        return True
    except (NotADirectory, StopIteration):
        return False
    except Exception:  # noqa: BLE001 - 不存在或不可读都按非目录处理
        return False


def _collect_files(
    store: Storage,
    path: str,
    *,
    ignore: Sequence[str] | None,
) -> list[tuple[str, str]]:
    """收集待传输文件，返回 ``(源路径, 相对路径)`` 列表。

    Args:
        store: 源后端。
        path: 源目录（空串为根）。
        ignore: 忽略规则。

    Returns:
        ``(源路径, 相对于 path 的路径)`` 列表。
    """
    base = norm_path(path)
    files: list[tuple[str, str]] = []
    for item in store.iter_list(path, recursive=True):
        if item.is_dir:
            continue
        rel = item.path[len(base) + 1 :] if base else item.path
        if _ignored(rel, ignore):
            continue
        files.append((item.path, rel))
    return files


def transfer(
    src: Storage,
    dst: Storage,
    path: str = "",
    dst_path: str | None = None,
    *,
    recursive: bool = True,
    progress: ProgressCallback | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    ignore: Sequence[str] | None = None,
    overwrite: bool = True,
) -> list[FileInfo]:
    """把 ``src`` 上的文件或目录复制到 ``dst``。

    数据先落到本地临时文件再上传，所以内存占用恒定，2GB 文件也能安全传输。

    Args:
        src: 源后端。
        dst: 目标后端。
        path: 源路径（文件或目录）。
        dst_path: 目标路径。目录传输时作为目标根目录；留空表示与源同构。
        recursive: ``path`` 是目录时是否递归（``True`` 始终递归，参数保留给
            未来支持单层复制）。
        progress: 进度回调 ``(已传字节, 总字节)``，按文件触发。
        concurrency: 并发传输的文件数。
        ignore: 忽略规则（fnmatch 通配符）。
        overwrite: 目标已存在时是否覆盖；``False`` 时跳过已存在项。

    Returns:
        传输完成的 :class:`~unifile.base.FileInfo` 列表。

    Example:
        >>> transfer(oss, local, "logs/2026/", "./archive")
    """
    del recursive  # 目录一律递归；参数保留以保持签名稳定
    # dst_path 为 None 时保持源布局（docs/ → docs/）；
    # 显式给 "" 则把内容铺到目标根（docs/ → /）
    target_root = norm_path(path) if dst_path is None else norm_path(dst_path)
    cb = _guard_progress(progress) if concurrency > 1 else progress

    # --- 单个文件 ---
    if src.is_file(path):
        target = dst_path or path
        if not overwrite and dst.exists(target):
            return []
        return [_copy_one(src, dst, path, target, cb)]

    if not _is_directory(src, path):
        raise NotAFile(f"源路径既不是文件也不是目录：{path}")

    # --- 目录 ---
    files = _collect_files(src, path, ignore=ignore)
    jobs = [
        (src_path, join_path(target_root, rel))
        for src_path, rel in files
    ]
    if not overwrite:
        jobs = [(s, t) for s, t in jobs if not dst.exists(t)]
    if not jobs:
        return []

    return _run_jobs(
        jobs, lambda job: _copy_one(src, dst, job[0], job[1], cb), concurrency
    )


def _copy_one(
    src: Storage,
    dst: Storage,
    src_path: str,
    dst_path: str,
    progress: ProgressCallback | None,
) -> FileInfo:
    """复制单个文件：下载到临时文件再上传。

    用临时文件而不是内存缓冲，是为了让跨后端传输同样具备流式特性。
    临时文件放在系统临时目录，用完即删。
    """
    fd, tmp = tempfile.mkstemp(prefix="unifile-xfer-")
    os.close(fd)
    try:
        src._download_to(src_path, tmp, progress=progress)
        return dst.upload(tmp, dst_path, progress=progress)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _same_file(
    src_info: FileInfo,
    dst_info: FileInfo,
    tolerance: float,
    *,
    mtime_reliable: bool,
) -> bool:
    """判断两端是否为同一份内容。

    以大小为第一判据（各家后端都有且准确）。mtime 只在两端都可信时才参与
    判断——涉及对象存储时它的 ``LastModified`` 是上传时刻，与源文件无关，
    拿它比较会导致每次同步都全量重传。

    Args:
        src_info: 源端元信息。
        dst_info: 目标端元信息。
        tolerance: mtime 允许误差（秒）。
        mtime_reliable: 两端的时间戳是否都反映内容修改时间。

    Returns:
        是否视为相同。
    """
    if src_info.size != dst_info.size:
        return False
    if not mtime_reliable:
        return True
    if not src_info.mtime or not dst_info.mtime:
        return True
    return abs(src_info.mtime - dst_info.mtime) <= tolerance


def sync(
    src: Storage,
    dst: Storage,
    path: str = "",
    dst_path: str | None = None,
    *,
    delete: bool = False,
    dry_run: bool = False,
    progress: ProgressCallback | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
    ignore: Sequence[str] | None = None,
    mtime_tolerance: float = 1.0,
) -> SyncResult:
    """把 ``src`` 同步到 ``dst``（增量）。

    只传输"目标没有"或"两端不一致"的文件，比全量 ``transfer`` 省下绝大部分
    流量。判断依据是大小 + 修改时间。

    Args:
        src: 源后端。
        dst: 目标后端。
        path: 源目录。
        dst_path: 目标根目录；留空表示与源同构。
        delete: 是否删除"目标有、源已无"的文件（镜像语义）。
            ``False`` 时只增不减，是更安全的选择。
        dry_run: 只计算差异不实际传输，用于预览。
        progress: 进度回调。
        concurrency: 并发传输数。
        ignore: 忽略规则。
        mtime_tolerance: 判断"是否同一版本"时允许的时间误差（秒）。
            跨时区或精度不同的后端可适当调大。

    Returns:
        :class:`SyncResult`。

    Example:
        >>> result = sync(oss, local, "photos/", "./photos", delete=True)
        >>> print(result)
        新建 12、覆盖 3、删除 1、跳过 480、失败 0
    """
    target_root = norm_path(path) if dst_path is None else norm_path(dst_path)
    base = norm_path(path)
    result = SyncResult()
    # 只有两端的时间戳都反映内容修改时间，才能拿 mtime 做判据
    mtime_reliable = src.preserves_mtime and dst.preserves_mtime

    # 一次遍历收集源端清单
    source_files: dict[str, FileInfo] = {}
    for item in src.iter_list(path, recursive=True):
        if item.is_dir:
            continue
        rel = item.path[len(base) + 1 :] if base else item.path
        if _ignored(rel, ignore):
            continue
        source_files[rel] = item

    # 目标端清单：只有需要 delete 时才全量列举
    target_map: dict[str, FileInfo] = {}
    if delete or not dry_run:
        try:
            for item in dst.iter_list(target_root, recursive=True):
                if item.is_dir:
                    continue
                rel = item.path[len(target_root) + 1 :] if target_root else item.path
                target_map[rel] = item
        except FileNotFound:
            # 目标端还没有这个目录，等价于"全都要传"
            target_map = {}

    to_copy: list[tuple[FileInfo, str, str]] = []
    for rel, src_info in source_files.items():
        dst_info = target_map.get(rel)
        if dst_info is not None and _same_file(
            src_info, dst_info, mtime_tolerance, mtime_reliable=mtime_reliable
        ):
            result.skipped.append(rel)
            continue
        target = join_path(target_root, rel)
        if dst_info is None:
            result.uploaded.append(rel)
        else:
            result.updated.append(rel)
        to_copy.append((src_info, src_info.path, target))

    if delete:
        for rel in target_map:
            if rel not in source_files:
                result.deleted.append(rel)
                if not dry_run:
                    try:
                        dst.delete(join_path(target_root, rel))
                    except Exception as exc:  # noqa: BLE001
                        result.failed.append((rel, str(exc)))

    if dry_run or not to_copy:
        return result

    cb = _guard_progress(progress) if concurrency > 1 else progress
    jobs = [(s_path, t_path) for _info, s_path, t_path in to_copy]

    def work(job: tuple[str, str]) -> None:
        src_path, dst_path_ = job
        try:
            _copy_one(src, dst, src_path, dst_path_, cb)
        except Exception as exc:  # noqa: BLE001 - 单个失败不中断整体同步
            result.failed.append((dst_path_, str(exc)))

    _run_jobs(jobs, work, concurrency)
    return result
