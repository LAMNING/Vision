from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.project import Project


TRANSLATE_STYLES = ('phrase', 'raw')

DEFAULT_AUTO_ANNOTATE_ENABLED = True
# 0.30 起：0.25 时实测会冒出「沙发 0.27 框在电视上」这类噪声框。改这个常量只影响
# 新建的配置行，老项目库里已存的值不动（见 crud/get_or_create_config）。
DEFAULT_BOX_THRESHOLD = 0.30
DEFAULT_TEXT_THRESHOLD = 0.25
DEFAULT_TOPK: int | None = None
DEFAULT_TRANSLATE_ENABLED = True
DEFAULT_TRANSLATE_STYLE = 'phrase'


class ProjectAutoConfig(BasicModel):

    __tablename__ = 'project_auto_config'

    project_id: Mapped[int] = mapped_column(
        ForeignKey('project.id', ondelete='CASCADE'), primary_key=True
    )

    auto_annotate_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=DEFAULT_AUTO_ANNOTATE_ENABLED
    )
    box_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_BOX_THRESHOLD
    )
    text_threshold: Mapped[float] = mapped_column(
        Float, nullable=False, default=DEFAULT_TEXT_THRESHOLD
    )
    top_k: Mapped[int | None] = mapped_column(Integer)
    translate_enabled: Mapped[bool] = mapped_column(
        nullable=False, default=DEFAULT_TRANSLATE_ENABLED
    )
    translate_style: Mapped[str] = mapped_column(
        String(10), nullable=False, default=DEFAULT_TRANSLATE_STYLE
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, onupdate=utcnow
    )

    project: Mapped['Project'] = relationship(back_populates='auto_config')

    def __repr__(self) -> str:
        return (
            f'<ProjectAutoConfig project={self.project_id} '
            f'box={self.box_threshold} text={self.text_threshold}>'
        )
