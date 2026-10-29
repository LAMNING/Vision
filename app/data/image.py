from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
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
    from app.data.group import Group
    from app.data.task_image import TaskImage


class Image(BasicModel):
    __tablename__ = 'image'
    __table_args__ = (
        UniqueConstraint(
            'group_id', 'storage_key', name='uq_image_group_storage_key'
        ),
        Index('ix_image_group_content_hash', 'group_id', 'content_hash'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int] = mapped_column(
        ForeignKey('groups.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    uploader_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False
    )

    storage_key: Mapped[str] = mapped_column(String(255), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64))  # sha256

    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    group: Mapped['Group'] = relationship('Group', back_populates='images')
    task_images: Mapped[list['TaskImage']] = relationship(
        'TaskImage', back_populates='image', passive_deletes=True
    )

    def __repr__(self) -> str:
        return f'<Image id={self.id} filename={self.filename!r}>'
