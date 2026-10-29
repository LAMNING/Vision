from datetime import datetime
from typing import Literal

from pydantic import Field

from app.data.crud.group_member import ASSIGNABLE_ROLES
from app.schemas.account import AccountSummary
from app.schemas.base import GroupName, InputModel, ORMModel

__all__ = [
    'GroupTypeRead',
    'GroupTypeCreate',
    'GroupMemberRole',
    'AssignableGroupRole',
    'GroupCreate',
    'GroupUpdate',
    'GroupRead',
    'GroupTreeNode',
    'GroupMemberCreate',
    'GroupMemberRoleUpdate',
    'GroupTransferRequest',
    'GroupMemberRead',
]

# 读：库里可能存在 personal
GroupTypeRead = Literal['personal', 'team', 'org']
# 写：personal 只能由 create_personal_group 生成（crud 会拒绝 type='personal'）
GroupTypeCreate = Literal['team', 'org']

# 读：成员行里 owner 也是一条记录
GroupMemberRole = Literal['owner', *ASSIGNABLE_ROLES]
# 写：owner 只能通过 transfer_ownership 转让
AssignableGroupRole = Literal[*ASSIGNABLE_ROLES]


class GroupCreate(InputModel):
    name: GroupName
    type: GroupTypeCreate = 'team'
    parent_id: int | None = Field(default=None, gt=0)
    # None 表示用 crud 里按 type 取的默认额度；0 表示不限量
    quota_storage_mb: int | None = Field(default=None, ge=0)
    quota_images: int | None = Field(default=None, ge=0)
    quota_members: int | None = Field(default=None, ge=0)


class GroupUpdate(InputModel):
    '''PATCH 语义：只传要改的字段。

    personal 组改组名会被 crud 拒绝（PersonalGroupImmutableError）；
    type / parent_id / owner 不在这里出现：改类型和移动层级没有对应 crud。
    '''

    name: GroupName | None = None
    quota_storage_mb: int | None = Field(default=None, ge=0)
    quota_images: int | None = Field(default=None, ge=0)
    quota_members: int | None = Field(default=None, ge=0)


class GroupRead(ORMModel):
    id: int
    name: str
    type: GroupTypeRead
    parent_id: int | None
    owner_account_id: int
    quota_storage_mb: int
    quota_images: int
    quota_members: int
    quota_source: str
    created_at: datetime


class GroupTreeNode(GroupRead):
    '''组织架构树：children 来自 Group.children 关系。'''

    children: list['GroupTreeNode'] = Field(default_factory=list)


class GroupMemberCreate(InputModel):
    account_id: int = Field(gt=0)
    role: AssignableGroupRole = 'member'


class GroupMemberRoleUpdate(InputModel):
    role: AssignableGroupRole


class GroupTransferRequest(InputModel):
    new_owner_account_id: int = Field(gt=0)


class GroupMemberRead(ORMModel):
    id: int
    group_id: int
    account_id: int
    role: GroupMemberRole
    joined_at: datetime
    account: AccountSummary | None = None
