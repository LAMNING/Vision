from datetime import datetime
from typing import Literal

from pydantic import Field

from app.data.crud.project_member import VALID_PROJECT_ROLES
from app.schemas.account import AccountSummary
from app.schemas.base import InputModel, ORMModel

__all__ = [
    'ProjectRole',
    'ProjectMemberCreate',
    'ProjectMemberRolesUpdate',
    'ProjectMemberRead',
]

ProjectRole = Literal[*VALID_PROJECT_ROLES]


class ProjectMemberCreate(InputModel):
    '''对应 crud.upsert_member(role=...)。

    重复加成员不会报错，roles 由 crud 合并去重（_normalize_roles）。
    '''

    account_id: int = Field(gt=0)
    role: ProjectRole
    # None 表示长期有效；crud 会把 expires_at 原样写回
    expires_at: datetime | None = None
    # 通过组邀请带进来的成员会记录来源组
    source_group_id: int | None = Field(default=None, gt=0)


class ProjectMemberRolesUpdate(InputModel):
    '''对应 crud.change_roles(roles=[...])：整组覆盖，不许为空。

    移除成员用 DELETE 端点（remove_member），不要传空列表。
    '''

    roles: list[ProjectRole] = Field(min_length=1)


class ProjectMemberRead(ORMModel):
    id: int
    project_id: int
    account_id: int
    roles: list[ProjectRole]
    status: str
    source_group_id: int | None
    expires_at: datetime | None
    joined_at: datetime | None
    created_at: datetime
    account: AccountSummary | None = None
