from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.revoked_token import RevokedToken
from app.utils.time import utcnow


def is_revoked(db: Session, jti: str) -> bool:
    return (
        db.scalar(select(RevokedToken.jti).where(RevokedToken.jti == jti))
        is not None
    )


def revoke(
    db: Session, *, jti: str, account_id: int, expires_at: datetime
) -> None:
    db.add(RevokedToken(jti=jti, account_id=account_id, expires_at=expires_at))
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < utcnow()))
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
