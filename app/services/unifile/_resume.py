"""断点续传的状态存储。

设计要点：**权威来源是服务端，不是本文件**。

S3 的 ``ListParts`` 能查出一个上传任务已经收到哪些分片，所以这里只需要
记住 ``uploadId``——分片清单交给服务端。状态文件坏了、丢了，最坏情况是
重传一次，不会产生错误数据。

状态失效的判据见 :meth:`ResumeState.matches`。任意一条不满足就静默重来：
续传是优化，不该让调用方感知它的失败。
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterator

#: 状态文件的默认存活时间（秒）。S3 的 multipart upload 默认 7 天后过期，
#: 这里留一天余量，避免在边界上和服务端赛跑。
DEFAULT_MAX_AGE = 6 * 24 * 3600

#: 环境变量名，用于覆盖默认的状态目录
RESUME_DIR_ENV = "UNIFILE_RESUME_DIR"


def default_resume_dir() -> str:
    """返回当前平台的默认状态目录。

    Returns:
        - Windows：``%LOCALAPPDATA%\\unifile\\resume``
        - macOS：``~/Library/Caches/unifile/resume``
        - 其他：``$XDG_CACHE_HOME/unifile/resume``（回退 ``~/.cache``）
    """
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser(
            os.path.join("~", "AppData", "Local")
        )
        return os.path.join(base, "unifile", "resume")
    if sys.platform == "darwin":
        return os.path.expanduser(os.path.join("~", "Library", "Caches", "unifile", "resume"))
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser(os.path.join("~", ".cache"))
    return os.path.join(base, "unifile", "resume")


@dataclass
class ResumeState:
    """一次未完成分片上传的存档。

    Attributes:
        upload_id: 服务端返回的 UploadId。
        key: 目标对象 key。
        total: 源文件当时的字节数。
        part_size: 当时用的分片大小。**必须校验**——分片边界一变，
            续传拼出来的文件就是错的，而且不会报错。
        mtime: 源文件当时的修改时间。
        created_at: 存档创建时间（POSIX 秒），用于判断过期。
    """

    upload_id: str
    key: str
    total: int
    part_size: int
    mtime: float
    created_at: float

    def matches(
        self,
        *,
        total: int,
        part_size: int,
        mtime: float,
        mtime_tolerance: float = 1.0,
        max_age: float = DEFAULT_MAX_AGE,
    ) -> bool:
        """判断这份存档是否还能用来续传。

        Args:
            total: 当前源文件大小。
            part_size: 当前配置的分片大小。
            mtime: 当前源文件修改时间。
            mtime_tolerance: mtime 允许误差（秒）。文件系统精度不同，
                不必要求完全相等。
            max_age: 存档最大存活秒数。

        Returns:
            是否可续传。
        """
        if self.total != total:
            return False
        if self.part_size != part_size:
            # 分片边界变了，已传的片和新的片无法拼在一起
            return False
        if self.mtime and mtime and abs(self.mtime - mtime) > mtime_tolerance:
            # 源文件改过了
            return False
        if max_age > 0 and (time.time() - self.created_at) > max_age:
            return False
        return True


def _lock_file(fd: int) -> bool:
    """尝试对已打开的文件加非阻塞独占锁。

    Args:
        fd: 文件描述符。

    Returns:
        是否成功拿到锁。拿不到说明别的进程正在用这份存档。
    """
    try:
        if os.name == "nt":
            import msvcrt

            # Windows 的锁定要求文件至少 1 字节
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"\0")
                os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def _unlock_file(fd: int) -> None:
    """释放文件锁（失败无妨，关闭描述符时系统也会释放）。"""
    with contextlib.suppress(OSError):
        if os.name == "nt":
            import msvcrt

            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(fd, fcntl.LOCK_UN)


class ResumeStore:
    """续传状态的文件存储，带跨进程锁。

    目录结构::

        <resume_dir>/
        ├── <fingerprint>.json   # ResumeState
        └── <fingerprint>.lock   # 空文件，仅用于加锁
    """

    def __init__(self, directory: str | None = None) -> None:
        """
        Args:
            directory: 状态目录。留空依次读 ``UNIFILE_RESUME_DIR``
                环境变量和平台默认位置。
        """
        self.directory = os.path.expanduser(
            directory or os.environ.get(RESUME_DIR_ENV) or default_resume_dir()
        )

    # -- 路径 --------------------------------------------------------
    @staticmethod
    def fingerprint(local_path: str, remote_key: str) -> str:
        """计算「源文件 + 目标 key」的稳定指纹。

        只用绝对路径和目标 key，**不含大小和时间**——那两项是
        :meth:`ResumeState.matches` 的校验内容。指纹本身必须稳定，
        否则源文件一变就找不到存档，连"该丢弃"这个判断都做不了。

        Args:
            local_path: 源文件的绝对路径。
            remote_key: 目标对象 key。

        Returns:
            32 位十六进制字符串。
        """
        raw = f"{os.path.abspath(local_path)}\x00{remote_key}".encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:32]

    def _state_path(self, fingerprint: str) -> str:
        return os.path.join(self.directory, f"{fingerprint}.json")

    def _lock_path(self, fingerprint: str) -> str:
        return os.path.join(self.directory, f"{fingerprint}.lock")

    # -- 锁 ----------------------------------------------------------
    @contextlib.contextmanager
    def lock(self, fingerprint: str) -> Iterator[bool]:
        """尝试独占某个存档。

        拿不到锁说明另一个进程/线程正在续传同一个文件，此时插一脚会让
        两边都出错。调用方应据此退化为「全量重传」而不是等待。

        Yields:
            是否拿到锁。
        """
        os.makedirs(self.directory, exist_ok=True)
        fd = os.open(self._lock_path(fingerprint), os.O_RDWR | os.O_CREAT, 0o600)
        acquired = _lock_file(fd)
        try:
            yield acquired
        finally:
            if acquired:
                _unlock_file(fd)
            os.close(fd)
            # 上传完成后存档已删，锁文件就没用了，顺手清掉。
            # 必须等 close(fd) 之后才能删——Windows 不允许删除打开中的文件。
            # 这里不会有等待者：锁是非阻塞的，抢不到的一方早就退化为全量了。
            if acquired and not os.path.exists(self._state_path(fingerprint)):
                with contextlib.suppress(OSError):
                    os.unlink(self._lock_path(fingerprint))

    # -- 读写 --------------------------------------------------------
    def load(self, fingerprint: str) -> ResumeState | None:
        """读取存档；文件不存在或格式损坏时返回 ``None``。"""
        path = self._state_path(fingerprint)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            return ResumeState(
                upload_id=str(data["upload_id"]),
                key=str(data.get("key", "")),
                total=int(data.get("total", 0)),
                part_size=int(data.get("part_size", 0)),
                mtime=float(data.get("mtime", 0.0)),
                created_at=float(data.get("created_at", 0.0)),
            )
        except (OSError, ValueError, KeyError):
            # 存档坏了就当没有——最坏情况是多传一次
            return None

    def save(self, fingerprint: str, state: ResumeState) -> None:
        """写入存档（原子替换，避免读到写了一半的 JSON）。"""
        os.makedirs(self.directory, exist_ok=True)
        path = self._state_path(fingerprint)
        tmp = f"{path}.tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(asdict(state), fh)
            os.replace(tmp, path)
        except OSError:
            # 存档写不进去不影响上传本身，静默忽略
            with contextlib.suppress(OSError):
                os.unlink(tmp)

    def drop(self, fingerprint: str) -> None:
        """删除存档（续传完成或判定失效后调用）。"""
        with contextlib.suppress(OSError):
            os.unlink(self._state_path(fingerprint))

    # -- 维护 --------------------------------------------------------
    def purge_expired(self, max_age: float = DEFAULT_MAX_AGE) -> int:
        """清理过期存档。

        Args:
            max_age: 超过该秒数的存档会被删除。

        Returns:
            删除的存档数量。
        """
        if not os.path.isdir(self.directory):
            return 0
        removed = 0
        now = time.time()
        for name in os.listdir(self.directory):
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.directory, name)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    created = float(json.load(fh).get("created_at", 0.0))
            except (OSError, ValueError):
                created = 0.0
            if now - created > max_age:
                with contextlib.suppress(OSError):
                    os.unlink(path)
                    removed += 1
                # 顺手清掉配套的锁文件
                with contextlib.suppress(OSError):
                    os.unlink(path[: -len(".json")] + ".lock")
        return removed
