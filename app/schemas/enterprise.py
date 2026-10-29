from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints

from app.data.enterprise import (
    ASSIGNABLE_ROLES,
    ENTERPRISE_ROLES,
    JOIN_MODES,
    REVIEW_STATUSES,
)
from app.schemas.account import AccountSummary
from app.schemas.base import Email, InputModel, ORMModel, Username

__all__ = [
    'EnterpriseName',
    'EnterpriseRole',
    'AssignableEnterpriseRole',
    'JoinMode',
    'ReviewStatus',
    'ReviewDecision',
    'EnterpriseCreate',
    'EnterpriseUpdate',
    'EnterpriseRead',
    'EnterpriseListItem',
    'EnterpriseMemberCreate',
    'EnterpriseMemberUpdate',
    'EnterpriseMemberRead',
    'EnterpriseImageRead',
    'EnterpriseReviewRequest',
    'EnterpriseUsageRead',
    'EnterpriseRankRead',
    'EnterpriseJoinRequestCreate',
    'EnterpriseJoinRequestRead',
    'EnterpriseJoinResult',
    'EnterpriseLogRead',
    'EnterpriseReportMemberRead',
    'EnterpriseReportRead',
    'EnterpriseHeartbeat',
    'EnterpriseHeartbeatRead',
]

EnterpriseName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)
]
NoticeText = Annotated[str, StringConstraints(strip_whitespace=True, max_length=300)]
JoinMessage = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
EnterpriseCode = Annotated[
    str, StringConstraints(strip_whitespace=True, to_upper=True, min_length=4, max_length=16)
]
ReviewNote = Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]

EnterpriseRole = Literal[*ENTERPRISE_ROLES]
AssignableEnterpriseRole = Literal[*ASSIGNABLE_ROLES]
JoinMode = Literal[*JOIN_MODES]
ReviewStatus = Literal[*REVIEW_STATUSES]
ReviewDecision = Literal['approve', 'reject']


class EnterpriseCreate(InputModel):
    '''创建企业：创建者直接成为 owner，企业码由服务端生成。'''

    name: EnterpriseName
    email: Email
    quota_images: int = Field(default=2000, ge=1, le=100_000)
    quota_members: int = Field(default=50, ge=1, le=10_000)


class EnterpriseUpdate(InputModel):
    '''PATCH 语义：只传要改的字段；notice_text 传 null 表示撤下公告。

    只有 owner 能改（配额与审核规则都属于企业级设置）。
    '''

    name: EnterpriseName | None = None
    quota_images: int | None = Field(default=None, ge=1, le=100_000)
    quota_members: int | None = Field(default=None, ge=1, le=10_000)
    auto_approve_staff: bool | None = None
    join_mode: JoinMode | None = None
    notice_text: NoticeText | None = None


class EnterpriseRead(ORMModel):
    '''企业基础信息（不含统计与「我的角色」）。'''

    id: int
    name: str
    code: str
    email: str
    owner_account_id: int
    quota_images: int
    quota_members: int
    auto_approve_staff: bool
    join_mode: JoinMode
    notice_text: str | None
    created_at: datetime


class EnterpriseListItem(EnterpriseRead):
    '''列表 / 详情统一用这个：附统计与当前账号的角色，前端一次拿全。

    ``my_role`` 为 null 表示还没加入（例如申请待审批时返回的企业信息）。
    '''

    my_role: EnterpriseRole | None = None
    # 企业背后的组 id：企业项目挂在这个组上，前端建项目/筛项目要用
    group_id: int
    member_count: int
    image_count: int
    used_bytes: int
    review_pending: int
    pending_count: int


class EnterpriseMemberCreate(InputModel):
    '''直接按用户名邀请（无需对方审批）。'''

    username: Username
    role: AssignableEnterpriseRole = 'member'


class EnterpriseMemberUpdate(InputModel):
    '''本人只能改 invisible；管理员及以上可以改 role。

    两种情况都走 PATCH，crud 里按「改的是不是自己」分流。
    '''

    role: AssignableEnterpriseRole | None = None
    invisible: bool | None = None


class EnterpriseMemberRead(ORMModel):
    id: int
    account_id: int
    role: EnterpriseRole
    invisible: bool
    online: bool
    upload_count: int
    joined_at: datetime
    account: AccountSummary | None = None


class EnterpriseImageRead(ORMModel):
    '''企业图库条目：``id`` 是关联行 id，取图/看详情用 ``image_id``。'''

    id: int
    enterprise_id: int
    image_id: int
    filename: str
    width: int | None
    height: int | None
    size_bytes: int
    uploader_account_id: int
    review_status: ReviewStatus
    review_note: str | None
    reviewed_by_account_id: int | None
    reviewed_at: datetime | None
    created_at: datetime


class EnterpriseReviewRequest(InputModel):
    decision: ReviewDecision
    note: ReviewNote | None = None


class EnterpriseUsageRead(ORMModel):
    image_count: int
    used_bytes: int
    quota_images: int
    member_count: int
    quota_members: int


class EnterpriseRankRead(ORMModel):
    account_id: int
    username: str
    role: EnterpriseRole
    count: int
    rank: int


class EnterpriseJoinRequestCreate(InputModel):
    code: EnterpriseCode
    message: JoinMessage | None = None


class EnterpriseJoinRequestRead(ORMModel):
    account_id: int
    message: str | None
    applied_at: datetime
    account: AccountSummary | None = None


class EnterpriseJoinResult(ORMModel):
    '''open 模式直接是 active；approval 模式是 pending，等管理员审批。'''

    status: Literal['pending', 'active']
    enterprise: EnterpriseListItem


class EnterpriseLogRead(ORMModel):
    id: int
    actor_account_id: int | None
    actor_name: str | None
    action: str
    detail: str | None
    created_at: datetime


class EnterpriseReportMemberRead(ORMModel):
    username: str
    uploads: int
    approved: int
    rejected: int
    pending: int


class EnterpriseReportRead(ORMModel):
    '''按自然日统计（本地时区），「通过率」只算已出结论的图。'''

    date: date
    uploads: int
    approved: int
    rejected: int
    pending: int
    pass_rate: float | None
    active_members: int
    by_member: list[EnterpriseReportMemberRead]


class EnterpriseHeartbeat(InputModel):
    invisible: bool = False


class EnterpriseHeartbeatRead(ORMModel):
    online: bool
    invisible: bool
    server_time: datetime
