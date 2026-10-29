from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.database import BasicModel
from app.utils.time import utcnow


class RevokedToken(BasicModel):
    '''已作废的 token（按 jti 记录）。

    token 自身带 exp，过期后自动被拒；所以这张表只存到过期为止，
    ``crud.revoke`` 会顺手清掉过期行，避免表无限增长。
    '''

    __tablename__ = 'revoked_token'

    jti: Mapped[str] = mapped_column(String(64), primary_key=True)
    account_id: Mapped[int] = mapped_column(
        ForeignKey('account.id', ondelete='CASCADE'), index=True
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, index=True
    )
    revoked_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, default=utcnow
    )

    def __repr__(self) -> str:
        return f'<RevokedToken {self.jti}>'
