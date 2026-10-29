from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.task import Task

REVIEW_DECISIONS = ('approve', 'reject_fix', 'reject_reannotate')


class Review(BasicModel):
    __tablename__ = 'review'
    __table_args__ = (
        UniqueConstraint('task_id', 'round', name='uq_review_task_round'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    task_id: Mapped[int] = mapped_column(
        ForeignKey('task.id', ondelete='CASCADE'), nullable=False, index=True
    )
    reviewer_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False
    )
    round: Mapped[int] = mapped_column(Integer, nullable=False)

    decision: Mapped[str] = mapped_column(String(20), nullable=False)
    comment: Mapped[str | None] = mapped_column(Text)

    approved_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    task: Mapped['Task'] = relationship(back_populates='reviews')

    def __repr__(self) -> str:
        return (
            f'<Review task={self.task_id} round={self.round} '
            f'decision={self.decision!r}>'
        )
