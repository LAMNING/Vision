'''站内通知的响应模型。'''

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.data.notification import NOTIFICATION_KINDS
from app.schemas.base import ORMModel

__all__ = [
    'NotificationKind',
    'NotificationRead',
    'NotificationUnreadCount',
]

NotificationKind = Literal[*NOTIFICATION_KINDS]


class NotificationRead(ORMModel):
    id: int
    account_id: int
    kind: NotificationKind
    title: str
    body: str | None
    resource_type: str | None
    resource_id: int | None
    read_at: datetime | None
    created_at: datetime
    is_read: bool = False


class NotificationUnreadCount(BaseModel):
    count: int
