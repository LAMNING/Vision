"""命令行工具：``python -m unifile <命令> <URI>``。

    python -m unifile ls file://./data
    python -m unifile ls oss://my-bucket/docs --recursive
    python -m unifile cp local.txt oss://my-bucket/docs/local.txt
    python -m unifile get oss://my-bucket/a.pdf ./
    python -m unifile put ./a.pdf oss://my-bucket/
    python -m unifile rm oss://my-bucket/old --recursive
    python -m unifile info oss://my-bucket/a.pdf
"""

from __future__ import annotations

import argparse
import sys
from . import __version__, open_storage
from ._util import human_size
from .base import StorageError


def _progress(label: str):
    def _cb(done: int, total: int) -> None:
        if total > 0:
            pct = done * 100 // total
            sys.stderr.write(f"\r{label}: {human_size(done)}/{human_size(total)} ({pct}%)")
        else:
            sys.stderr.write(f"\r{label}: {human_size(done)}")
        if total > 0 and done >= total:
            sys.stderr.write("\n")
            sys.stderr.flush()

    return _cb


def _cmd_ls(args: argparse.Namespace) -> int:
    store = open_storage(args.uri)
    items = store.list(args.path, recursive=args.recursive)
    if not items:
        print("(空目录)")
        return 0
    total = 0
    for item in items:
        prefix = "d" if item.is_dir else "-"
        print(f"{prefix} {item.human_size:>10}  {item.path}")
        total += item.size
    if args.recursive:
        print(f"\n共 {len(items)} 项，合计 {human_size(total)}")
    return 0


def _cmd_info(args: argparse.Namespace) -> int:
    store = open_storage(args.uri)
    item = store.stat(args.path)
    print(f"路径    : {item.path}")
    print(f"类型    : {'目录' if item.is_dir else '文件'}")
    print(f"大小    : {item.human_size} ({item.size} 字节)")
    print(f"修改时间: {item.mtime}")
    if item.etag:
        print(f"ETag    : {item.etag}")
    if item.storage:
        print(f"后端    : {item.storage}")
    return 0


def _cmd_get(args: argparse.Namespace) -> int:
    """下载：unifile get <uri> <本地目标> [--path 远端路径]"""
    store = open_storage(args.uri)
    if args.recursive:
        target = store.download_dir(args.path, args.dest, progress=_progress("下载"))
    else:
        target = store.download(args.path, args.dest, progress=_progress("下载"))
    print(f"已保存到 {target}")
    return 0


def _cmd_put(args: argparse.Namespace) -> int:
    """上传：unifile put <uri> <本地源> [--path 远端路径]"""
    store = open_storage(args.uri)
    if args.recursive:
        results = store.upload_dir(args.src, args.path, progress=_progress("上传"))
        print(f"已上传 {len(results)} 个文件")
    else:
        info = store.upload(args.src, args.path, progress=_progress("上传"))
        print(f"已上传 {info.path}（{info.human_size}）")
    return 0


def _cmd_cp(args: argparse.Namespace) -> int:
    src = open_storage(args.src)
    dst = open_storage(args.dst)
    info = src.copy(args.src_path, args.dst_path)
    print(f"已复制到 {info.path}")
    return 0


def _cmd_mv(args: argparse.Namespace) -> int:
    src = open_storage(args.src)
    dst = open_storage(args.dst)
    info = src.move(args.src_path, args.dst_path)
    print(f"已移动到 {info.path}")
    return 0


def _cmd_rm(args: argparse.Namespace) -> int:
    store = open_storage(args.uri)
    store.delete(args.path, recursive=args.recursive)
    print(f"已删除 {args.path}")
    return 0


def _cmd_checksum(args: argparse.Namespace) -> int:
    """计算校验和。"""
    if args.local:
        # 本地模式不需要连接后端，直接算文件摘要
        import hashlib

        digest = hashlib.new(args.algorithm)
        with open(args.path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 1024), b""):
                digest.update(chunk)
        print(digest.hexdigest())
        return 0

    store = open_storage(args.uri)
    print(store.checksum(args.path, args.algorithm))


def _cmd_sync(args: argparse.Namespace) -> int:
    """跨后端增量同步。"""
    from .transfer import sync

    src = open_storage(args.src)
    dst = open_storage(args.dst)
    try:
        result = sync(
            src,
            dst,
            args.src_path,
            args.dst_path,
            delete=args.delete,
            dry_run=args.dry_run,
            concurrency=args.concurrency,
        )
    finally:
        for store in (src, dst):
            close = getattr(store, "close", None)
            if callable(close):
                close()

    prefix = "（试运行，未改动任何文件）" if args.dry_run else ""
    print(f"{result}{prefix}")
    for rel in result.deleted:
        print(f"  - {rel}")
    for rel, err in result.failed:
        print(f"  ! {rel}: {err}", file=sys.stderr)
    return 1 if result.failed else 0


def _cmd_transfer(args: argparse.Namespace) -> int:
    """跨后端复制（不比对，直接传）。"""
    from .transfer import transfer

    src = open_storage(args.src)
    dst = open_storage(args.dst)
    try:
        infos = transfer(
            src,
            dst,
            args.src_path,
            args.dst_path,
            concurrency=args.concurrency,
            overwrite=not args.no_overwrite,
        )
    finally:
        for store in (src, dst):
            close = getattr(store, "close", None)
            if callable(close):
                close()
    print(f"已传输 {len(infos)} 个文件")
    return 0


def _cmd_mkdir(args: argparse.Namespace) -> int:
    store = open_storage(args.uri)
    store.mkdir(args.path)
    print(f"已创建 {args.path}")
    return 0


def _cmd_tree(args: argparse.Namespace) -> int:
    store = open_storage(args.uri)
    for item in store.walk(args.path):
        depth = item.path.count("/") - norm_depth(args.path)
        indent = "  " * max(0, depth)
        mark = "/" if item.is_dir else ""
        print(f"{indent}{item.name}{mark}  {item.human_size if not item.is_dir else ''}")
    return 0


def norm_depth(path: str) -> int:
    return len([p for p in path.split("/") if p])


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="unifile", description="统一管理本地文件、OSS/S3 与 FTP"
    )
    parser.add_argument("--version", action="version", version=f"unifile {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("ls", help="列出目录内容")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", default="", help="目录路径，留空为根目录")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=_cmd_ls)

    p = sub.add_parser("tree", help="树状展示目录")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", default="", help="目录路径，留空为根目录")
    p.set_defaults(func=_cmd_tree)

    p = sub.add_parser("info", help="查看文件元信息")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", default="", help="文件路径")
    p.set_defaults(func=_cmd_info)

    p = sub.add_parser("get", help="下载（<uri> <本地目标>，远端路径用 --path）")
    p.add_argument("uri", help="后端地址，如 oss://bucket")
    p.add_argument("dest", nargs="?", default=".", help="本地目标路径")
    p.add_argument("--path", default="", help="远端文件或目录路径")
    p.add_argument("-r", "--recursive", action="store_true", help="下载整个目录")
    p.set_defaults(func=_cmd_get)

    p = sub.add_parser("put", help="上传（<uri> <本地源>，远端路径用 --path）")
    p.add_argument("uri", help="后端地址，如 oss://bucket")
    p.add_argument("src", help="本地文件或目录")
    p.add_argument("--path", default="", help="远端目标路径")
    p.add_argument("-r", "--recursive", action="store_true", help="上传整个目录")
    p.set_defaults(func=_cmd_put)

    p = sub.add_parser("cp", help="同后端内复制")
    p.add_argument("src")
    p.add_argument("src_path")
    p.add_argument("dst")
    p.add_argument("dst_path")
    p.set_defaults(func=_cmd_cp)

    p = sub.add_parser("mv", help="同后端内移动")
    p.add_argument("src")
    p.add_argument("src_path")
    p.add_argument("dst")
    p.add_argument("dst_path")
    p.set_defaults(func=_cmd_mv)

    p = sub.add_parser("transfer", help="跨后端复制（<src> <src路径> <dst> <dst路径>）")
    p.add_argument("src", help="源后端地址")
    p.add_argument("src_path", help="源路径")
    p.add_argument("dst", help="目标后端地址")
    p.add_argument("dst_path", nargs="?", default=None, help="目标路径，留空保持源布局")
    p.add_argument("-j", "--concurrency", type=int, default=4, help="并发数")
    p.add_argument("--no-overwrite", action="store_true", help="跳过目标已存在的文件")
    p.set_defaults(func=_cmd_transfer)

    p = sub.add_parser("sync", help="跨后端增量同步（<src> <src路径> <dst> <dst路径>）")
    p.add_argument("src", help="源后端地址")
    p.add_argument("src_path", help="源路径")
    p.add_argument("dst", help="目标后端地址")
    p.add_argument("dst_path", nargs="?", default=None, help="目标路径，留空保持源布局")
    p.add_argument("-j", "--concurrency", type=int, default=4, help="并发数")
    p.add_argument("--delete", action="store_true", help="删除目标端多余的文件（镜像）")
    p.add_argument("--dry-run", action="store_true", help="只看差异，不改动")
    p.set_defaults(func=_cmd_sync)

    p = sub.add_parser("rm", help="删除")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", default="", help="要删除的路径")
    p.add_argument("-r", "--recursive", action="store_true")
    p.set_defaults(func=_cmd_rm)

    p = sub.add_parser("checksum", help="计算校验和")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", default="", help="文件路径")
    p.add_argument("--algorithm", default="md5", choices=["md5", "sha256"])
    p.add_argument("--local", action="store_true", help="计算本地文件而非远端")
    p.set_defaults(func=_cmd_checksum)

    p = sub.add_parser("mkdir", help="创建目录")
    p.add_argument("uri", help="后端地址")
    p.add_argument("--path", required=True, help="要创建的目录路径")
    p.set_defaults(func=_cmd_mkdir)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except StorageError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except FileNotFoundError as exc:
        print(f"错误：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
