from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import String, Integer, DateTime, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.account import Account
    from app.data.image import Image
    from app.data.invitation import Invitation
    from app.data.project import Project


class Group(BasicModel):
    __tablename__ = 'groups'

    id: Mapped[int] = mapped_column(primary_key=True)

    name: Mapped[str] = mapped_column(String(50), nullable=False)
    type: Mapped[str] = mapped_column(String(10), nullable=False, index=True) # personal / team / org

    parent_id: Mapped[int | None] = mapped_column(
        ForeignKey('groups.id', ondelete='SET NULL'), nullable=True, index=True
    )
    owner_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False, index=True
    )

    quota_storage_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_images: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    quota_members: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    quota_source: Mapped[str] = mapped_column(String(10), nullable=False, default='own')

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    owner: Mapped['Account'] = relationship(
        'Account',
        back_populates='owned_groups',
        foreign_keys=[owner_account_id],
    )
    projects: Mapped[list['Project']] = relationship(back_populates='group')
    invitations: Mapped[list['Invitation']] = relationship(back_populates='group')
    images: Mapped[list['Image']] = relationship('Image', back_populates='group')
    members: Mapped[list['GroupMember']] = relationship(
        'GroupMember',
        back_populates='group',
        cascade='all, delete-orphan',
    )
    parent: Mapped['Group | None'] = relationship(
        'Group',
        remote_side='Group.id',
        back_populates='children',
    )
    children: Mapped[list['Group']] = relationship(
        'Group',
        back_populates='parent',
    )

    def __repr__(self) -> str:
        return f'<Group id={self.id} name={self.name!r} type={self.type!r}>'


class GroupMember(BasicModel):
    __tablename__ = 'group_member'
    __table_args__ = (
        UniqueConstraint('group_id', 'account_id', name='uq_group_member_group_account'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int] = mapped_column(
        ForeignKey('groups.id', ondelete='CASCADE'), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )

    role: Mapped[str] = mapped_column(String(10), nullable=False) # owner / admin / member

    joined_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    group: Mapped['Group'] = relationship('Group', back_populates='members')
    account: Mapped['Account'] = relationship('Account', back_populates='group_memberships')

    def __repr__(self) -> str:
        return f'<GroupMember group={self.group_id} account={self.account_id} role={self.role!r}>'