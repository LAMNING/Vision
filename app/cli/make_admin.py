"""引导平台管理员（全新部署唯一的提权入口）。

为什么需要它：``/admin/*`` 全部挂在 :data:`app.deps.PlatformAdmin` 后面
（``app/routers/admin.py``），而注册接口只会给 ``platform_role='user'``
（``app/data/account.py:29``）。也就是说，一个没有任何管理员的库里**不存在**
任何能把自己变成管理员的 HTTP 路径 —— 首个管理员只能从命令行创建。

用法::

    # 全新库：建账号 + 设为管理员（密码不给则交互输入，不回显）
    python -m app.cli.make_admin alice --create

    # 脚本里一次到位（注意：密码会进 shell history）
    python -m app.cli.make_admin alice --create --password 12345678

    # 已有账号：直接提权（幂等，已经是管理员就只打印状态）
    python -m app.cli.make_admin alice

    # 提权 + 重置密码（顺带 +1 token_version，让该账号已签发的 token 全失效）
    python -m app.cli.make_admin alice --reset-password

命令会先跑一遍 ``upgrade_to_head()``，所以空库也能直接执行，不必先起服务。
"""

from __future__ import annotations

import argparse
import getpass
import sys
from collections.abc import Sequence
from typing import NoReturn

from pydantic import TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select

from app import database, migrations
from app.data import Account
from app.data.crud import audit as audit_crud
from app.data.crud.account import register_account
from app.errors import AppError
from app.schemas.base import Email, Password, Phone, Username
from app.utils.security import hash_password

_USERNAME = TypeAdapter(Username)
_PASSWORD = TypeAdapter(Password)
_EMAIL = TypeAdapter(Email)
_PHONE = TypeAdapter(Phone)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='python -m app.cli.make_admin',
        description='创建账号并把它提升为平台管理员（幂等）。',
    )
    parser.add_argument('username', help='账号名（3-30 位字母/数字/下划线/点/横线）')
    parser.add_argument(
        '--create',
        action='store_true',
        help='账号不存在时新建；不加则只在账号已存在时提权',
    )
    parser.add_argument(
        '--password',
        default=None,
        help='创建或重置时使用的密码（至少 8 位）；不给则交互输入',
    )
    parser.add_argument(
        '--reset-password',
        action='store_true',
        help='顺带重置密码，并作废该账号已签发的 token',
    )
    parser.add_argument('--email', default=None, help='只在 --create 时写入')
    parser.add_argument('--phone', default=None, help='只在 --create 时写入')
    return parser


def _die(message: str, code: int = 2) -> NoReturn:
    '''统一失败出口：提示进 stderr，退出码 2（用法/输入错误）区别于 1（冲突）。'''
    print(message, file=sys.stderr)
    raise SystemExit(code)


def _validated(adapter, value: str, label: str, hint: str) -> str:
    try:
        return adapter.validate_python(value)
    except PydanticValidationError:
        _die(f'{label}不合法：{value!r}（{hint}）')


def _prompt_password() -> str:
    '''交互输入两次；不回显、不写进 shell history。'''
    first = getpass.getpass('密码（至少 8 位，不回显）: ')
    password = _validated(_PASSWORD, first, '密码', '至少 8 位，最多 128 位')
    if getpass.getpass('再输一次: ') != password:
        _die('两次输入的密码不一致')
    return password


def _promote(db, account: Account) -> bool:
    '''把账号设为平台管理员；返回是否真的改了。'''
    if account.platform_role == 'admin':
        return False
    before = {'platform_role': account.platform_role}
    account.platform_role = 'admin'
    audit_crud.record(
        db,
        action='account.platform_role_changed',
        resource_type='account',
        resource_id=account.id,
        # CLI 没有登录态，操作者就是被操作账号；审计里用 after.via 标明来源。
        actor_account_id=account.id,
        before=before,
        after={'platform_role': 'admin', 'via': 'cli.make_admin'},
    )
    db.commit()
    return True


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    username = _validated(_USERNAME, args.username, '用户名', '3-30 位字母/数字/下划线/点/横线')
    email = _validated(_EMAIL, args.email, '邮箱', '形如 a@b.com') if args.email else None
    phone = _validated(_PHONE, args.phone, '手机号', '只允许数字/空格/横线/括号，可带 +') if args.phone else None

    # 空库也能直接跑：建表规则和启动流程完全一致。
    migrations.upgrade_to_head()

    try:
        with database.SessionLocal() as db:
            account = db.scalar(select(Account).where(Account.username == username))

            created = False
            if account is None:
                if not args.create:
                    print(
                        f'账号 {username!r} 不存在。要新建并提权，请加 --create。',
                        file=sys.stderr,
                    )
                    return 2
                password = args.password or _prompt_password()
                password = _validated(_PASSWORD, password, '密码', '至少 8 位，最多 128 位')
                account = register_account(
                    db,
                    username=username,
                    password=password,
                    email=email,
                    phone=phone,
                )
                created = True
                print(f'已创建账号 {username!r}（id={account.id}）')
            elif args.password and not args.reset_password:
                print('提示：账号已存在，--password 只在配合 --reset-password 时生效。')

            if args.reset_password and not created:
                password = args.password or _prompt_password()
                password = _validated(_PASSWORD, password, '密码', '至少 8 位，最多 128 位')
                account.password = hash_password(password)
                # 改密即失效旧 token，和接口层 change_password 的语义保持一致。
                account.token_version += 1
                db.commit()
                print(f'已重置 {username!r} 的密码，该账号此前签发的 token 全部失效')

            if _promote(db, account):
                print(f'已把 {username!r} 提升为平台管理员（platform_role=admin）')
            else:
                print(f'{username!r} 已经是平台管理员，无需变更')
    except AppError as exc:
        print(f'失败：{exc}', file=sys.stderr)
        return 1

    print(f'完成。现在可以用 {username!r} 登录，并访问 /admin/* 接口。')
    return 0


if __name__ == '__main__':
    sys.exit(main())
