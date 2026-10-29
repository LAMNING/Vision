from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.account import AccountRead
from app.schemas.base import InputModel

__all__ = ['LoginRequest', 'TokenResponse']


class LoginRequest(InputModel):
    '''JSON 登录体。

    长度上限与 ``Account.username`` / ``Account.password`` 列宽保持一致；
    这里不接受 email 登录（email 可改、可清空，登录标识只用 username）。
    '''

    username: str = Field(min_length=1, max_length=30)
    password: str = Field(min_length=1, max_length=128)


class TokenResponse(BaseModel):
    '''签发结果。

    顺手把 ``account`` 带上，前端登录后不必再打一次 /auth/me；
    ``token_type`` 固定 bearer，方便客户端直接拼 ``Authorization`` 头。
    '''

    access_token: str
    token_type: Literal['bearer'] = 'bearer'
    expires_in: int
    account: AccountRead
