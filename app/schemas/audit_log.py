from datetime import datetime
from typing import Any

from app.schemas.base import ORMModel

__all__ = ['AuditLogRead']


class AuditLogRead(ORMModel):
    id: int
    group_id: int | None
    actor_account_id: int | None
    # {resource}.{verb_past}，如 group.role_changed / task.status_changed
    action: str
    resource_type: str
    resource_id: int
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime
