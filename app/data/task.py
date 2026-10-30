from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.project import Project
    from app.data.review import Review
    from app.data.task_image import TaskImage


TASK_STATUSES = (
    'draft',
    'queued',
    'auto_annotating',
    'auto_annotated',
    'approved',
    'rejected_fix',
    'rejected_reannotate',
    'failed',
    'cancelled',
)

TERMINAL_TASK_STATUSES = ('approved', 'cancelled')

# auto = 提交后交给 worker 自动标注；manual = 人工在标注工作台里画框
TASK_MODES = ('auto', 'manual')

# 优先级：0 低 / 1 普通 / 2 高（排序用整数，展示文案由前端决定）
TASK_PRIORITY_MIN = 0
TASK_PRIORITY_MAX = 2


ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    'draft': ('queued', 'cancelled'),
    # queued -> auto_annotated 只给 manual 任务用（人工标完直接进待复核）
    'queued': ('auto_annotating', 'auto_annotated', 'failed', 'cancelled'),
    'auto_annotating': ('auto_annotated', 'failed', 'cancelled'),
    'auto_annotated': (
        'approved',
        'rejected_fix',
        'rejected_reannotate',
        'cancelled',
    ),
    'rejected_fix': ('auto_annotated', 'queued', 'cancelled'),
    'rejected_reannotate': ('queued', 'cancelled'),
    'failed': ('queued', 'cancelled'),
    'approved': (),
    'cancelled': (),
}


class Task(BasicModel):
    __tablename__ = 'task'

    id: Mapped[int] = mapped_column(primary_key=True)

    project_id: Mapped[int] = mapped_column(
        ForeignKey('project.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    name: Mapped[str | None] = mapped_column(String(100))

    status: Mapped[str] = mapped_column(String(20), nullable=False, default='draft')

    submitter_account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='RESTRICT'), nullable=False, index=True
    )
    reviewer_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL'), index=True
    )

    # 任务类型与协作字段
    mode: Mapped[str] = mapped_column(String(10), nullable=False, default='auto')
    assignee_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL'), index=True
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    note: Mapped[str | None] = mapped_column(String(500))

    # 标注完成后是否还要人工复核：关掉时标注一结束就自动通过（见 crud.review.auto_approve）
    review_required: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text('1')
    )

    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    # worker 租约：auto_annotating 的任务由某个 worker 独占，租约过期可被回收
    worker_id: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)

    version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime)

    project: Mapped['Project'] = relationship(back_populates='tasks')
    task_images: Mapped[list['TaskImage']] = relationship(
        back_populates='task', cascade='all, delete-orphan'
    )
    reviews: Mapped[list['Review']] = relationship(
        back_populates='task', cascade='all, delete-orphan'
    )

    def __repr__(self) -> str:
        return f'<Task id={self.id} status={self.status!r}>'
