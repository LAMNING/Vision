from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.project_category import ProjectCategory
    from app.data.task_image import TaskImage

ANNOTATION_TYPES = ('bbox', 'polygon', 'keypoint', 'classification')
ANNOTATION_SOURCES = ('auto', 'manual')


class Annotation(BasicModel):
    __tablename__ = 'annotation'
    __table_args__ = (
        Index(
            'ix_annotation_task_image_current',
            'task_image_id',
            'is_current',
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    task_image_id: Mapped[int] = mapped_column(
        ForeignKey('task_image.id', ondelete='CASCADE'),
        nullable=False,
        index=True,
    )

    label: Mapped[str] = mapped_column(String(50), nullable=False)
    annotation_type: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict] = mapped_column(JSON, nullable=False)

    confidence: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(10), nullable=False)

    category_id: Mapped[int | None] = mapped_column(
        ForeignKey('project_category.id', ondelete='SET NULL'), index=True
    )

    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_current: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    task_image: Mapped['TaskImage'] = relationship(back_populates='annotations')
    category: Mapped['ProjectCategory'] = relationship('ProjectCategory')

    def __repr__(self) -> str:
        return f'<Annotation id={self.id} label={self.label!r} rev={self.revision}>'
