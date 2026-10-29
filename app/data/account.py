from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, DateTime
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.group import Group, GroupMember
    from app.data.project_member import ProjectMember


class Account(BasicModel):
    __tablename__ = 'account'

    id: Mapped[int] = mapped_column(primary_key=True)

    username: Mapped[str] = mapped_column(String(30), nullable=False, unique=True, index=True)
    # 展示名：只影响界面称呼，登录始终用 username
    display_name: Mapped[str | None] = mapped_column(String(50))
    password: Mapped[str] = mapped_column(String(128), nullable=False)

    email: Mapped[str | None] = mapped_column(String(50), unique=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(30), unique=True, index=True)

    # 头像对象在 storage 里的路径；None 表示没上传（对外只暴露 has_avatar）
    avatar_key: Mapped[str | None] = mapped_column(String(255))

    email_verified: Mapped[bool] = mapped_column(default=False)
    phone_verified: Mapped[bool] = mapped_column(default=False)

    platform_role: Mapped[str] = mapped_column(String(20), nullable=False, default='user')

    token_version: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    owned_groups: Mapped[list['Group']] = relationship(
        'Group',
        back_populates='owner',
        foreign_keys='Group.owner_account_id',
    )
    group_memberships: Mapped[list['GroupMember']] = relationship(
        'GroupMember',
        back_populates='account',
        cascade='all, delete-orphan',
    )
    project_memberships: Mapped[list['ProjectMember']] = relationship(
        'ProjectMember',
        back_populates='account',
    )

    def __repr__(self) -> str:
        return f'<Account id={self.id} username={self.username!r}>'

    @property
    def has_avatar(self) -> bool:
        '''给响应模型用：外部不需要知道对象路径，只关心有没有。'''
        return bool(self.avatar_key)