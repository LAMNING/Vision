from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    DateTime,
    Float,
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
    from app.data.project import Project


class ProjectCategory(BasicModel):
    '''项目类目：中文展示名 + 英文检测提示词。

    ``en_name`` 直接作为 LLMDet 的文本提示词（如 ``a remote control``），
    与中文名分开存是为了中文名可以随时改展示，英文名只改检测口径；
    两者都可编辑，分别有 ``(project_id, zh_name)`` / ``(project_id, en_name)``
    唯一约束。

    ``threshold_override`` 是推理之后的过滤阈值：LLMDet 一次推理只有一个
    ``threshold``，按类目的覆盖值在结果上过滤，不需要为每个类目重跑。
    '''

    __tablename__ = 'project_category'
    __table_args__ = (
        UniqueConstraint('project_id', 'zh_name', name='uq_project_category_zh'),
        UniqueConstraint('project_id', 'en_name', name='uq_project_category_en'),
        Index('ix_project_category_project_sort', 'project_id', 'sort_order'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey('project.id', ondelete='CASCADE'), nullable=False
    )

    zh_name: Mapped[str] = mapped_column(String(50), nullable=False)
    en_name: Mapped[str] = mapped_column(String(100), nullable=False)

    threshold_override: Mapped[float | None] = mapped_column(Float)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    enabled: Mapped[bool] = mapped_column(nullable=False, default=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    project: Mapped['Project'] = relationship(back_populates='categories')

    def __repr__(self) -> str:
        return (
            f'<ProjectCategory id={self.id} zh={self.zh_name!r} '
            f'en={self.en_name!r}>'
        )
