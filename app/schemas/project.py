from datetime import datetime
from typing import Literal

from pydantic import Field

from app.schemas.base import InputModel, ORMModel, ProjectName

__all__ = [
    'DefaultReviewer',
    'ProjectStatus',
    'ProjectCreate',
    'ProjectCreateInGroup',
    'ProjectUpdate',
    'ProjectRead',
    'ProjectListItem',
]

DefaultReviewer = Literal['submitter', 'project_manager']
ProjectStatus = Literal['active', 'archived']


class ProjectCreate(InputModel):
    name: ProjectName
    description: str | None = Field(default=None, max_length=2000)
    default_reviewer: DefaultReviewer = 'submitter'
    # 指定 manager 时 crud 会同时写入 ProjectMember（upsert_member）
    manager_account_id: int | None = Field(default=None, gt=0)


class ProjectCreateInGroup(ProjectCreate):
    '''在指定空间里建项目：个人空间、小组、企业（背后的组）都走这里。'''

    group_id: int = Field(gt=0)


class ProjectUpdate(InputModel):
    '''PATCH 语义：只传要改的字段；description=null 表示清空。

    路由层用 ``payload.model_dump(exclude_unset=True)`` 丢给 crud，
    可区分"没传"和"显式传 null"。
    '''

    name: ProjectName | None = None
    description: str | None = Field(default=None, max_length=2000)
    default_reviewer: DefaultReviewer | None = None


class ProjectRead(ORMModel):
    id: int
    group_id: int
    name: str
    description: str | None
    default_reviewer: DefaultReviewer
    status: ProjectStatus
    created_at: datetime


class ProjectListItem(ProjectRead):
    '''项目列表项：附上所属组与「我在这个项目里的角色」。'''

    group_name: str
    group_type: str
    role: str
    can_review: bool = False
    # 能不能在项目里上传、能不能改设置，前端据此决定入口显隐
    can_manage: bool = False
    can_upload: bool = False
