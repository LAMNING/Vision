import argparse
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

APP_IMPORT = 'app.main:app'
LOG_LEVELS = ('critical', 'error', 'warning', 'info', 'debug', 'trace')


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {'1', 'true', 'yes', 'on', 'y'}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        sys.exit(f'环境变量 {name} 必须是整数，当前值为 {raw!r}')


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='启动 ImageLabel FastAPI 服务',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('-H', '--host', default=os.getenv('HOST', '127.0.0.1'), help='监听地址')
    parser.add_argument('-p', '--port', type=int, default=_env_int('PORT', 8000), help='监听端口')
    parser.add_argument(
        '-r',
        '--reload',
        dest='reload',
        action='store_true',
        default=_env_bool('RELOAD', True),
        help='开启热重载（仅开发环境）',
    )
    parser.add_argument(
        '--no-reload',
        dest='reload',
        action='store_false',
        help='关闭热重载',
    )
    parser.add_argument(
        '-w', '--workers', type=int, default=_env_int('WORKERS', 1), help='worker 进程数'
    )
    parser.add_argument(
        '--log-level',
        default=os.getenv('LOG_LEVEL', 'info').lower(),
        choices=LOG_LEVELS,
        help='日志级别',
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    try:
        import uvicorn
    except ModuleNotFoundError:
        sys.exit('缺少依赖 uvicorn，请先执行: pip install -r requirements.txt')

    if not (BASE_DIR / 'app' / 'main.py').is_file():
        sys.exit(f'找不到 {BASE_DIR / 'app' / 'main.py'}，请确认项目结构完整')

    if args.reload and args.workers > 1:
        print('[run] --reload 与多 worker 互斥，已自动将 workers 调整为 1', flush=True)
        args.workers = 1

    host = '127.0.0.1' if args.host.lower() == 'localhost' else args.host
    display_host = 'localhost' if host in {'0.0.0.0', '127.0.0.1'} else host
    print(
        f'[run] {APP_IMPORT} -> http://{display_host}:{args.port} '
        f'(host={host}, reload={args.reload}, workers={args.workers}, log_level={args.log_level})',
        flush=True,
    )

    uvicorn.run(
        APP_IMPORT,
        host=host,
        port=args.port,
        reload=args.reload,
        reload_dirs=[str(BASE_DIR / 'app')] if args.reload else None,
        reload_includes=['*.py'] if args.reload else None,
        workers=args.workers,
        log_level=args.log_level,
    )


if __name__ == '__main__':
    main()
