'''企业空间：成员分级、上传审核、配额、公告与操作留痕。

图片不另存一套实体：企业图仍然是 ``Image``（存在企业背后的隐藏 ``Group``
里，复用去重、缩略图与 ``/images/{id}/content``），``EnterpriseImage`` 只做
「企业 + 审核状态」这层关联，和 ``ProjectImage`` 的定位一致。
'''

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.account import Account
    from app.data.group import Group
    from app.data.image import Image

# 角色从高到低；guest 只读
ENTERPRISE_ROLES = ('owner', 'admin', 'reviewer', 'member', 'guest')
ROLE_RANK = {role: len(ENTERPRISE_ROLES) - index for index, role in enumerate(ENTERPRISE_ROLES)}

# 可被邀请/调整的角色（owner 只能靠转让，当前不提供转让接口）
ASSIGNABLE_ROLES = ('admin', 'reviewer', 'member', 'guest')

REVIEW_STATUSES = ('pending', 'approved', 'rejected')
JOIN_MODES = ('approval', 'open')
JOIN_REQUEST_STATUSES = ('pending', 'approved', 'rejected')


class Enterprise(BasicModel):
    __tablename__ = 'enterprise'

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(40), nullable=False)
    # 企业码：VS-XXXXXXXX，加入时用；生成后不改
    code: Mapped[str] = mapped_column(String(16), nullable=False, unique=True, index=True)
    email: Mapped[str] = mapped_column(String(120), nullable=False)

    owner_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    # 企业图落在这个隐藏组里（不写 GroupMember，组列表里看不到）
    group_id: Mapped[int] = mapped_column(
        ForeignKey('groups.id', ondelete='RESTRICT'), nullable=False, unique=True
    )

    quota_images: Mapped[int] = mapped_column(Integer, nullable=False, default=2000)
    quota_members: Mapped[int] = mapped_column(Integer, nullable=False, default=50)
    auto_approve_staff: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    join_mode: Mapped[str] = mapped_column(String(10), nullable=False, default='approval')
    notice_text: Mapped[str | None] = mapped_column(String(300))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    members: Mapped[list['EnterpriseMember']] = relationship(
        'EnterpriseMember', back_populates='enterprise', cascade='all, delete-orphan'
    )
    images: Mapped[list['EnterpriseImage']] = relationship(
        'EnterpriseImage', back_populates='enterprise', cascade='all, delete-orphan'
    )
    requests: Mapped[list['EnterpriseJoinRequest']] = relationship(
        'EnterpriseJoinRequest',
        back_populates='enterprise',
        cascade='all, delete-orphan',
    )
    logs: Mapped[list['EnterpriseLog']] = relationship(
        'EnterpriseLog', back_populates='enterprise', cascade='all, delete-orphan'
    )

    def __repr__(self) -> str:
        return f'<Enterprise id={self.id} name={self.name!r} code={self.code!r}>'


class EnterpriseMember(BasicModel):
    __tablename__ = 'enterprise_member'
    __table_args__ = (
        UniqueConstraint(
            'enterprise_id', 'account_id', name='uq_enterprise_member_account'
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey('enterprise.id', ondelete='CASCADE'), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(10), nullable=False, default='member')
    # 在线状态不落库，按 last_seen_at + invisible 现算（见 crud.online_of）
    invisible: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)

    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    enterprise: Mapped['Enterprise'] = relationship('Enterprise', back_populates='members')
    account: Mapped['Account'] = relationship('Account')

    def __repr__(self) -> str:
        return (
            f'<EnterpriseMember enterprise={self.enterprise_id} '
            f'account={self.account_id} role={self.role!r}>'
        )


class EnterpriseImage(BasicModel):
    __tablename__ = 'enterprise_image'
    __table_args__ = (
        UniqueConstraint('enterprise_id', 'image_id', name='uq_enterprise_image'),
        Index('ix_enterprise_image_review', 'enterprise_id', 'review_status'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey('enterprise.id', ondelete='CASCADE'), nullable=False, index=True
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey('image.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    uploader_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False, index=True
    )

    review_status: Mapped[str] = mapped_column(
        String(10), nullable=False, default='pending'
    )
    review_note: Mapped[str | None] = mapped_column(String(200))
    reviewed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL')
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    enterprise: Mapped['Enterprise'] = relationship('Enterprise', back_populates='images')
    image: Mapped['Image'] = relationship('Image')

    def __repr__(self) -> str:
        return (
            f'<EnterpriseImage enterprise={self.enterprise_id} '
            f'image={self.image_id} status={self.review_status!r}>'
        )


class EnterpriseJoinRequest(BasicModel):
    __tablename__ = 'enterprise_join_request'
    __table_args__ = (
        UniqueConstraint(
            'enterprise_id', 'account_id', name='uq_enterprise_request_account'
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey('enterprise.id', ondelete='CASCADE'), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )

    message: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), nullable=False, default='pending')

    applied_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decided_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL')
    )

    enterprise: Mapped['Enterprise'] = relationship(
        'Enterprise', back_populates='requests'
    )
    account: Mapped['Account'] = relationship('Account', foreign_keys=[account_id])

    def __repr__(self) -> str:
        return (
            f'<EnterpriseJoinRequest enterprise={self.enterprise_id} '
            f'account={self.account_id} status={self.status!r}>'
        )


class EnterpriseLog(BasicModel):
    __tablename__ = 'enterprise_log'
    __table_args__ = (Index('ix_enterprise_log_recent', 'enterprise_id', 'id'),)

    id: Mapped[int] = mapped_column(primary_key=True)

    enterprise_id: Mapped[int] = mapped_column(
        ForeignKey('enterprise.id', ondelete='CASCADE'), nullable=False, index=True
    )
    actor_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL')
    )

    action: Mapped[str] = mapped_column(String(40), nullable=False)
    # 直接给用户看的一句话（渲染时优先于 action）
    detail: Mapped[str | None] = mapped_column(String(255))

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    enterprise: Mapped['Enterprise'] = relationship('Enterprise', back_populates='logs')
    actor: Mapped['Account | None'] = relationship('Account')

    def __repr__(self) -> str:
        return f'<EnterpriseLog id={self.id} action={self.action!r}>'


__all__ = [
    'ENTERPRISE_ROLES',
    'ROLE_RANK',
    'ASSIGNABLE_ROLES',
    'REVIEW_STATUSES',
    'JOIN_MODES',
    'JOIN_REQUEST_STATUSES',
    'Enterprise',
    'EnterpriseMember',
    'EnterpriseImage',
    'EnterpriseJoinRequest',
    'EnterpriseLog',
]
