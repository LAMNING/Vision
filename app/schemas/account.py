from datetime import datetime

from pydantic import Field

from app.schemas.base import (
    Email,
    InputModel,
    ORMModel,
    Password,
    Phone,
    Username,
)

__all__ = [
    'AccountCreate',
    'AccountUpdate',
    'PasswordChange',
    'AccountRead',
    'AccountSummary',
]


class AccountCreate(InputModel):
    '''注册。

    ``platform_role`` / ``*_verified`` 不给客户端：它们由服务端授予，
    放进来就是提权入口（crud.create_account 接受 **kw，所以路由层要显式收窄）。
    '''

    username: Username
    password: Password
    email: Email | None = None
    phone: Phone | None = None


class AccountUpdate(InputModel):
    '''PATCH 语义：只传要改的字段；显式传 null 表示清空 email / phone。'''

    email: Email | None = None
    phone: Phone | None = None


class PasswordChange(InputModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: Password


class AccountRead(ORMModel):
    '''对外的账号视图。

    这里没有 ``password`` 字段（是 argon2 哈希），从结构上杜绝泄露；
    不要改成 ``exclude={'password'}`` 之类的运行时补救。
    '''

    id: int
    username: str
    email: str | None
    phone: str | None
    platform_role: str
    email_verified: bool
    phone_verified: bool
    created_at: datetime


class AccountSummary(ORMModel):
    '''嵌在成员列表 / 审计里的精简账号信息。'''

    id: int
    username: str
