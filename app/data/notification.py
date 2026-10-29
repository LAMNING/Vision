'''站内通知：邀请、任务指派、任务进度、复核结果。'''

from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import BasicModel
from app.utils.time import utcnow

if TYPE_CHECKING:
    from app.data.account import Account


NOTIFICATION_KINDS = (
    'invitation',
    'task_assigned',
    'task_ready',
    'task_review',
    'task_overdue',
    'system',
)


class Notification(BasicModel):
    '''一条站内通知，属于某一个账号；read_at 为空表示未读。'''

    __tablename__ = 'notification'
    __table_args__ = (
        Index('ix_notification_account_unread', 'account_id', 'read_at'),
    )

    id: Mapped[int] = mapped_column(primary_key=True)

    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(30), nullable=False, default='system')
    title: Mapped[str] = mapped_column(String(120), nullable=False)
    body: Mapped[str | None] = mapped_column(String(500))

    # 指向触发通知的资源，方便前端跳转
    resource_type: Mapped[str | None] = mapped_column(String(30))
    resource_id: Mapped[int | None] = mapped_column(Integer)

    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    account: Mapped['Account'] = relationship()

    @property
    def is_read(self) -> bool:
        return self.read_at is not None

    def __repr__(self) -> str:
        return f'<Notification id={self.id} kind={self.kind!r} read={self.is_read}>'
