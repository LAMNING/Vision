from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.data.crud.group_member import ASSIGNABLE_ROLES as _GROUP_ROLES
from app.data.crud.project_member import VALID_PROJECT_ROLES as _PROJECT_ROLES
from app.schemas.base import Email, InputModel, ORMModel

__all__ = [
    'InvitationScope',
    'InvitationCreate',
    'InvitationCreateBody',
    'InvitationRead',
]

InvitationScope = Literal['group', 'project']


class InvitationCreate(InputModel):
    '''创建邀请。

    这里的校验与 crud.create_invitation 的前置检查重复，是有意为之：
    边界处直接 422 给出字段级错误，业务不变量（成员是否存在、组是否 personal、
    是否重复邀请……）仍由 crud 兜底。
    '''

    scope: InvitationScope
    role: str = Field(min_length=1, max_length=20)
    group_id: int | None = Field(default=None, gt=0)
    project_id: int | None = Field(default=None, gt=0)
    invitee_account_id: int | None = Field(default=None, gt=0)
    invitee_email: Email | None = None
    expires_at: datetime | None = None

    @model_validator(mode='after')
    def _check_target(self) -> 'InvitationCreate':
        if (self.invitee_account_id is None) == (self.invitee_email is None):
            raise ValueError('必须且仅提供 invitee_account_id 或 invitee_email 之一')

        if self.scope == 'group':
            if self.group_id is None or self.project_id is not None:
                raise ValueError('scope=group 时必须且仅提供 group_id')
            if self.role not in _GROUP_ROLES:
                raise ValueError(
                    f'group invitation role must be one of {_GROUP_ROLES}, '
                    f'got {self.role!r}（转让所有权请用 transfer_ownership）'
                )
        else:
            if self.project_id is None or self.group_id is not None:
                raise ValueError('scope=project 时必须且仅提供 project_id')
            if self.role not in _PROJECT_ROLES:
                raise ValueError(
                    f'project invitation role must be one of {_PROJECT_ROLES}, '
                    f'got {self.role!r}'
                )
        return self


class InvitationCreateBody(InputModel):
    '''按路由创建邀请时的请求体。

    目标（组/项目）与 scope 来自 URL 路径，所以这里只管「邀请谁、给什么角色」。
    '''

    invitee_account_id: int | None = Field(default=None, gt=0)
    invitee_email: Email | None = None
    role: str = Field(min_length=1, max_length=20)
    expires_at: datetime | None = None

    @model_validator(mode='after')
    def _check_invitee(self) -> 'InvitationCreateBody':
        if (self.invitee_account_id is None) == (self.invitee_email is None):
            raise ValueError('必须且仅提供 invitee_account_id 或 invitee_email 之一')
        return self


class InvitationRead(ORMModel):
    id: int
    scope: InvitationScope
    group_id: int | None
    project_id: int | None
    inviter_account_id: int
    invitee_account_id: int | None
    invitee_email: str | None
    role: str
    # pending / accepted / rejected / revoked / expired 由 crud 写入，用 str 放宽
    status: str
    expires_at: datetime | None
    created_at: datetime
    responded_at: datetime | None
    # 展示用（模型上的 property）
    group_name: str | None = None
    project_name: str | None = None
    inviter_username: str | None = None
