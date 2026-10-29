from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BasicModel
from app.utils.time import utcnow


class AuditLog(BasicModel):
    __tablename__ = 'audit_log'

    id: Mapped[int] = mapped_column(primary_key=True)

    group_id: Mapped[int | None] = mapped_column(
        ForeignKey('groups.id', ondelete='SET NULL'), index=True
    )
    actor_account_id: Mapped[int | None] = mapped_column(
        ForeignKey('account.id', ondelete='SET NULL'), index=True
    )

    # {resource}.{verb_past}：group.role_changed / task.status_changed
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    resource_type: Mapped[str] = mapped_column(String(30), nullable=False)
    resource_id: Mapped[int] = mapped_column(Integer, nullable=False)

    before: Mapped[dict | None] = mapped_column(JSON)
    after: Mapped[dict | None] = mapped_column(JSON)

    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow, index=True
    )

    def __repr__(self) -> str:
        return f'<AuditLog {self.action} {self.resource_type}:{self.resource_id}>'
