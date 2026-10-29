from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.ext.mutable import MutableList
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.account import Account
    from app.data.project import Project


class ProjectMember(BasicModel):
    __tablename__ = 'project_member'
    __table_args__ = (
        UniqueConstraint('project_id', 'account_id', name='uq_project_member'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey('project.id', ondelete='CASCADE'), nullable=False, index=True
    )
    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )

    source_group_id: Mapped[int | None] = mapped_column(
        ForeignKey('groups.id', ondelete='SET NULL'), index=True
    )

    roles: Mapped[list[str]] = mapped_column(
        MutableList.as_mutable(JSON), nullable=False, default=list
    )

    status: Mapped[str] = mapped_column(String(10), nullable=False, default='active')

    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    joined_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    project: Mapped['Project'] = relationship(back_populates='members')
    account: Mapped['Account'] = relationship(back_populates='project_memberships')

    def __repr__(self) -> str:
        return (
            f'<ProjectMember project={self.project_id} '
            f'account={self.account_id} roles={self.roles} status={self.status}>'
        )
