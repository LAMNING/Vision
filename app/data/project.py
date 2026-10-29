from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.group import Group
    from app.data.invitation import Invitation
    from app.data.project_auto_config import ProjectAutoConfig
    from app.data.project_category import ProjectCategory
    from app.data.project_member import ProjectMember
    from app.data.task import Task


class Project(BasicModel):
    __tablename__ = 'project'

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int] = mapped_column(
        ForeignKey('groups.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    default_reviewer: Mapped[str] = mapped_column(
        String(20), nullable=False, default='submitter'
    )

    # 'active' | 'archived'
    status: Mapped[str] = mapped_column(String(10), nullable=False, default='active')

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    group: Mapped['Group'] = relationship(back_populates='projects')
    members: Mapped[list['ProjectMember']] = relationship(
        back_populates='project', cascade='all, delete-orphan'
    )
    invitations: Mapped[list['Invitation']] = relationship(
        back_populates='project', cascade='all, delete-orphan'
    )
    tasks: Mapped[list['Task']] = relationship('Task', back_populates='project')
    categories: Mapped[list['ProjectCategory']] = relationship(
        back_populates='project', cascade='all, delete-orphan'
    )
    auto_config: Mapped['ProjectAutoConfig'] = relationship(
        back_populates='project', uselist=False, cascade='all, delete-orphan'
    )

    def __repr__(self) -> str:
        return f'<Project id={self.id} name={self.name!r}>'
