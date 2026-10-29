from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.account import Account
    from app.data.group import Group
    from app.data.project import Project


class Invitation(BasicModel):
    __tablename__ = 'invitation'

    id: Mapped[int] = mapped_column(primary_key=True)

    scope: Mapped[str] = mapped_column(String(10), nullable=False)

    group_id: Mapped[int | None] = mapped_column(
        ForeignKey('groups.id', ondelete='CASCADE'), index=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey('project.id', ondelete='CASCADE'), index=True
    )

    inviter_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )
    invitee_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), index=True
    )
    invitee_email: Mapped[str | None] = mapped_column(String(50), index=True)

    role: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(String(10), nullable=False, default='pending')

    expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    responded_at: Mapped[datetime | None] = mapped_column(DateTime)

    group: Mapped['Group | None'] = relationship(back_populates='invitations')
    project: Mapped['Project | None'] = relationship(back_populates='invitations')
    inviter: Mapped['Account'] = relationship(foreign_keys=[inviter_account_id])
    invitee: Mapped['Account | None'] = relationship(
        foreign_keys=[invitee_account_id]
    )

    @property
    def group_name(self) -> str | None:
        return self.group.name if self.group is not None else None

    @property
    def project_name(self) -> str | None:
        return self.project.name if self.project is not None else None

    @property
    def inviter_username(self) -> str | None:
        return self.inviter.username if self.inviter is not None else None

    def __repr__(self) -> str:
        return f'<Invitation id={self.id} scope={self.scope} status={self.status}>'
