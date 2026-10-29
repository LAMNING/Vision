from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.annotation import Annotation
    from app.data.image import Image
    from app.data.task import Task

ANNOTATION_STATUSES = ('pending', 'annotated', 'failed')
REVIEW_STATUSES = ('pending', 'approved', 'rejected')


class TaskImage(BasicModel):
    __tablename__ = 'task_image'
    __table_args__ = (
        UniqueConstraint('task_id', 'image_id', name='uq_task_image'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    task_id: Mapped[int] = mapped_column(
        ForeignKey('task.id', ondelete='CASCADE'), nullable=False, index=True
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey('image.id', ondelete='RESTRICT'), nullable=False, index=True
    )

    annotation_status: Mapped[str] = mapped_column(
        String(15), nullable=False, default='pending'
    )
    review_status: Mapped[str] = mapped_column(
        String(15), nullable=False, default='pending'
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    reject_reason: Mapped[str | None] = mapped_column(String(255))

    reviewed_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL'), index=True
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    annotated_at: Mapped[datetime | None] = mapped_column(DateTime)

    task: Mapped['Task'] = relationship(back_populates='task_images')
    image: Mapped['Image'] = relationship(back_populates='task_images')
    annotations: Mapped[list['Annotation']] = relationship(
        back_populates='task_image', cascade='all, delete-orphan'
    )

    def __repr__(self) -> str:
        return (
            f'<TaskImage task={self.task_id} image={self.image_id} '
            f'ann={self.annotation_status} rev={self.review_status}>'
        )
