"""SFTP 后端。

通过调用系统自带的 ``sftp`` 命令实现，不引入 paramiko 等第三方依赖。
``sftp`` 的批处理模式（``-b -``）从 stdin 读命令、stdout 返回结果，
非常适合脚本化调用。

流程：
- 上传/下载：``get`` / ``put`` 命令直接由 sftp 处理，支持断点续传参数
- 列举：``ls -l`` 输出格式固定，解析成 :class:`FileInfo`
- 其余操作：``mkdir`` / ``rm`` / ``rmdir`` / ``rename`` / ``chmod`` 等

注意：每次操作都会起一个 sftp 子进程（保持连接会显著拖慢脚本化调用）。
若需要高频交互，建议改用 paramiko。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from typing import Any, Sequence

from ._util import base_name, norm_path
from .base import (
    FileInfo,
    FileNotFound,
    NotAFile,
    Storage,
    StorageError,
)

# ls -l 的一行，形如：
# -rw-r--r--    1 user     group         1234 Jan  2 15:04 name.txt
# drwxr-xr-x    2 user     group         4096 Jan  2 15:04 dirname
# lrwxrwxrwx    1 user     group            9 Jan  2 15:04 link -> target
_LS_LINE = re.compile(
    r"^(?P<mode>[-dlbcps][-rwxSsTt]{9})\s+"
    r"(?P<nlink>\d+)\s+"
    r"(?P<owner>\S+)\s+"
    r"(?P<group>\S+)\s+"
    r"(?P<size>\d+)\s+"
    r"(?P<date>\S+\s+\S+\s+\S+)\s+"
    r"(?P<name>.+)$"
)


class SftpError(StorageError):
    """SFTP 操作失败。"""


class SftpStorage(Storage):
    """SFTP 后端（基于系统 sftp 命令）。

    Example:
        >>> store = SftpStorage(
        ...     host="example.com",
        ...     user="deploy",
        ...     key_filename="~/.ssh/id_ed25519",
        ...     root="/var/www",
        ... )
        >>> store.upload("index.html", "public/index.html")
    """

    kind = "sftp"
    preserves_mtime = True

    def __init__(
        self,
        host: str,
        *,
        port: int = 22,
        user: str = "",
        password: str = "",
        key_filename: str | None = None,
        root: str = "",
        timeout: float = 30.0,
        ssh_options: Sequence[str] = (),
        sftp_path: str | None = None,
        batch_mode: bool = True,
    ) -> None:
        """
        Args:
            host: 服务器地址。
            port: SSH 端口。
            user: 登录用户名。
            password: 密码（与 key_filename 二选一）。
            key_filename: 私钥路径。
            root: 远端根目录（相对登录后的起始目录）。
            timeout: 单次操作超时（秒）。
            ssh_options: 额外的 ``-o`` 选项，如
                ``("StrictHostKeyChecking=no",)``。
            sftp_path: sftp 可执行文件路径，默认从 PATH 查找。
            batch_mode: 是否用 ``BatchMode=yes``（禁止交互式密码输入）。
        """
        super().__init__(root)
        self.host = host
        self.port = port
        self.user = user
        self.password = password
        self.key_filename = os.path.expanduser(key_filename) if key_filename else None
        self.timeout = timeout
        self.ssh_options = list(ssh_options)
        self.sftp_path = sftp_path or shutil.which("sftp") or "sftp"
        self.batch_mode = batch_mode

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------
    def _remote(self, path: str) -> str:
        """把相对路径映射为远端绝对路径。"""
        rel = norm_path(path)
        root = norm_path(self.root)
        if root and rel:
            return "/" + root + "/" + rel
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

    def _argv(self) -> list[str]:
        """构造 sftp 命令行参数。"""
        argv = [self.sftp_path, "-b", "-", "-P", str(self.port)]
        if self.key_filename:
            argv += ["-i", self.key_filename]
        for opt in self.ssh_options:
            argv += ["-o", opt]
        if self.batch_mode:
            argv += ["-o", "BatchMode=yes"]
        if self.password:
            # sftp 没有直接的密码参数，靠 sshpass 或交互；此处提示用 key
            argv += ["-o", "PreferredAuthentications=publickey"]
        target = f"{self.user}@{self.host}" if self.user else self.host
        argv.append(target)
        return argv

    def _run(self, commands: Sequence[str], *, check: bool = True) -> tuple[int, str, str]:
        """执行一批 sftp 命令，返回 (退出码, stdout, stderr)。"""
        script = "\n".join(commands) + "\nbye\n"
        try:
            proc = subprocess.run(
                self._argv(),
                input=script.encode("utf-8"),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=self.timeout,
            )
        except FileNotFoundError as exc:
            raise SftpError(
                f"找不到 sftp 命令（{self.sftp_path}）。请安装 OpenSSH 客户端，"
                f"或改用 paramiko 方案。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise SftpError(f"sftp 操作超时（{self.timeout}s）") from exc

        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        if check and proc.returncode != 0:
            raise SftpError(f"sftp 命令失败（退出码 {proc.returncode}）：{err.strip() or out.strip()}")
        return proc.returncode, out, err

    @staticmethod
    def _quote(path: str) -> str:
        """给 sftp 命令里的路径加引号。

        路径含空格时必须加，否则 sftp 会把它拆成多个参数
        （Windows 上临时目录常在 ``C:\\Users\\<用户名>\\AppData\\...`` 下，
        用户名含空格就会出问题）。
        """
        if not path:
            return '""'
        if path.startswith('"') and path.endswith('"'):
            return path
        return '"' + path.replace('"', '\\"') + '"'

    # ------------------------------------------------------------------
    # 核心原语
    # ------------------------------------------------------------------
    def _list(self, path: str) -> list[FileInfo]:
        remote = self._remote(path)
        code, out, err = self._run([f"ls -l {self._quote(remote)}"], check=False)
        if code != 0:
            raise FileNotFound(f"路径不存在或无法列举：{path}（{err.strip()}）")

        items: list[FileInfo] = []
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("total "):
                continue
            match = _LS_LINE.match(line)
            if not match:
                continue
            mode = match.group("mode")
            name = match.group("name")
            # 符号链接显示为 "name -> target"，只取前半
            if " -> " in name:
                name = name.split(" -> ", 1)[0]
            is_dir = mode[0] == "d"
            is_link = mode[0] == "l"
            try:
                size = int(match.group("size"))
            except ValueError:
                size = 0
            child = self._relative(f"{remote.rstrip('/')}/{name}")
            items.append(
                FileInfo(
                    path=child,
                    size=0 if is_dir else size,
                    is_dir=is_dir,
                    mtime=0.0,
                    storage=self.kind,
                    extra={"mode": mode, "is_link": is_link},
                )
            )
        return items

    def _stat(self, path: str) -> FileInfo:
        remote = self._remote(path)
        # 用 ls -ld 查看目标本身（不带 -d 时 ls 列出的是目录内容而非目录自身）
        code, out, err = self._run([f"ls -ld {self._quote(remote)}"], check=False)
        if code != 0:
            raise FileNotFound(f"路径不存在：{path}")
        for line in out.splitlines():
            line = line.strip()
            if not line or line.startswith("total "):
                continue
            match = _LS_LINE.match(line)
            if not match:
                continue
            mode = match.group("mode")
            name = match.group("name")
            if " -> " in name:
                name = name.split(" -> ", 1)[0]
            try:
                size = int(match.group("size"))
            except ValueError:
                size = 0
            is_dir = mode[0] == "d"
            return FileInfo(
                path=norm_path(path),
                size=0 if is_dir else size,
                is_dir=is_dir,
                storage=self.kind,
                extra={"mode": mode},
            )
        raise FileNotFound(f"路径不存在：{path}")

    def _read_bytes(self, path: str) -> bytes:
        remote = self._remote(path)
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp.close()
        try:
            code, _out, err = self._run([f"get {self._quote(remote)} {self._quote(tmp.name)}"], check=False)
            if code != 0:
                raise FileNotFound(f"读取失败：{path}（{err.strip()}）")
            with open(tmp.name, "rb") as fh:
                return fh.read()
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)

    def _write_bytes(self, path: str, data: bytes) -> FileInfo:
        remote = self._remote(path)
        parent = remote.rsplit("/", 1)[0]
        if parent and parent != "/" and parent:
            self._run([f"mkdir {self._quote(parent)}"], check=False)
        tmp = tempfile.NamedTemporaryFile(delete=False)
        try:
            tmp.write(data)
            tmp.close()
            code, _out, err = self._run([f"put {self._quote(tmp.name)} {self._quote(remote)}"], check=False)
            if code != 0:
                raise SftpError(f"写入失败：{path}（{err.strip()}）")
        finally:
            if os.path.exists(tmp.name):
                os.unlink(tmp.name)
        return FileInfo(
            path=norm_path(path),
            size=len(data),
            storage=self.kind,
        )

    def _delete(self, path: str, recursive: bool = False) -> None:
        remote = self._remote(path)
        if recursive:
            for item in self.walk(path):
                if item.is_dir:
                    continue
                self._run([f"rm {self._quote(self._remote(item.path))}"], check=False)
            dirs = [i for i in self.walk(path) if i.is_dir]
            dirs.sort(key=lambda i: len(i.path), reverse=True)
            for item in dirs:
                self._run([f"rmdir {self._quote(self._remote(item.path))}"], check=False)
            self._run([f"rmdir {self._quote(remote)}"], check=False)
            return
        self._run([f"rm {self._quote(remote)}"], check=False)

    def _makedirs(self, path: str, exist_ok: bool = True) -> None:
        remote = self._remote(path)
        if not remote or remote == "/":
            return
        # sftp 的 mkdir 不递归，逐级创建
        current = ""
        for part in [p for p in remote.split("/") if p]:
            current += "/" + part
            self._run([f"mkdir {self._quote(current)}"], check=False)

    # ------------------------------------------------------------------
    # SFTP 特有能力
    # ------------------------------------------------------------------
    def _download_to(self, path: str, target: str, *, progress: Any = None) -> str:
        """下载到指定路径（由 sftp 自己处理传输，支持续传）。"""
        info = self._stat(path)
        if info.is_dir:
            raise NotAFile(f"目标是目录：{path}")
        remote = self._remote(path)
        parent = os.path.dirname(os.path.abspath(target))
        if parent:
            os.makedirs(parent, exist_ok=True)
        # sftp 的 get 命令自带续传（-a）
        code, _out, err = self._run([f"get -a {self._quote(remote)} {self._quote(target)}"], check=False)
        if code != 0:
            raise SftpError(f"下载失败：{path}（{err.strip()}）")
        if progress:
            size = os.path.getsize(target) if os.path.exists(target) else 0
            progress(size, size)
        return target

    def upload(self, local_path: str, path: str = "", *, progress: Any = None) -> FileInfo:
        """上传文件（由 sftp 自己处理传输，支持续传）。"""
        if not os.path.isfile(local_path):
            raise NotAFile(f"本地文件不存在：{local_path}")
        remote = self._remote(norm_path(path) or base_name(local_path))
        parent = remote.rsplit("/", 1)[0]
        if parent and parent != "/":
            self._makedirs(self._relative(parent), exist_ok=True)
        # put 的 -a 参数是续传标志
        code, _out, err = self._run([f"put -a {self._quote(local_path)} {self._quote(remote)}"], check=False)
        if code != 0:
            raise SftpError(f"上传失败：{path}（{err.strip()}）")
        total = os.path.getsize(local_path)
        if progress:
            progress(total, total)
        return FileInfo(
            path=norm_path(path) or base_name(local_path),
            size=total,
            storage=self.kind,
        )

    def rename(self, src: str, dst: str) -> FileInfo:
        """远端重命名。"""
        code, _out, err = self._run(
            [f"rename {self._quote(self._remote(src))} {self._quote(self._remote(dst))}"], check=False
        )
        if code != 0:
            raise SftpError(f"重命名失败：{src} → {dst}（{err.strip()}）")
        return FileInfo(path=norm_path(dst), storage=self.kind)

    def move(self, src: str, dst: str) -> FileInfo:
        """远端移动（优先用 rename，失败则退化为复制+删除）。"""
        try:
            return self.rename(src, dst)
        except SftpError:
            data = self._read_bytes(src)
            info = self._write_bytes(dst, data)
            self._delete(src, recursive=False)
            return info

    def copy(self, src: str, dst: str) -> FileInfo:
        """远端复制（sftp 无原生 copy，走下载+上传）。"""
        data = self._read_bytes(src)
        return self._write_bytes(dst, data)

    def chmod(self, path: str, mode: int) -> None:
        """修改远端文件权限。"""
        self._run([f"chmod {oct(mode)[2:]} {self._quote(self._remote(path))}"], check=False)
