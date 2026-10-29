'''项目-图片关联。

图片本身挂在组（空间）下（``Image.group_id``），这里只记「这张图被哪个项目用了」。
任务、标注都从项目角度用图，所以项目里可以挑一批图上传；同一张图可以被多个项目引用。
'''

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BasicModel
from app.utils.time import utcnow


class ProjectImage(BasicModel):
    __tablename__ = 'project_image'

    project_id: Mapped[int] = mapped_column(
        ForeignKey('project.id', ondelete='CASCADE'), primary_key=True
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey('image.id', ondelete='CASCADE'), primary_key=True, index=True
    )
    added_by_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL')
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f'<ProjectImage project={self.project_id} image={self.image_id}>'
