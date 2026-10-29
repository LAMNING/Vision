'''站内通知 CRUD：创建、列表、未读数、标记已读。'''

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.data.notification import NOTIFICATION_KINDS, Notification
from app.errors import ValidationError
from app.utils.time import utcnow


def create(
    db: Session,
    account_id: int,
    *,
    kind: str,
    title: str,
    body: str | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    commit: bool = True,
) -> Notification:
    if kind not in NOTIFICATION_KINDS:
        raise ValidationError(f'未知的通知类型：{kind!r}')
    if not title.strip():
        raise ValidationError('通知标题不能为空')
    row = Notification(
        account_id=account_id,
        kind=kind,
        title=title.strip()[:120],
        body=body[:500] if body else None,
        resource_type=resource_type,
        resource_id=resource_id,
    )
    db.add(row)
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def list_for_account(
    db: Session,
    account_id: int,
    *,
    unread_only: bool = False,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[Notification], int]:
    '''按时间倒序返回 (rows, total)。'''
    conds = [Notification.account_id == account_id]
    if unread_only:
        conds.append(Notification.read_at.is_(None))
    count_stmt = select(func.count(Notification.id)).where(*conds)
    total = int(db.scalar(count_stmt) or 0)

    stmt = (
        select(Notification)
        .where(*conds)
        .order_by(Notification.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt).all()), total


def unread_count(db: Session, account_id: int) -> int:
    stmt = select(func.count(Notification.id)).where(
        Notification.account_id == account_id,
        Notification.read_at.is_(None),
    )
    return int(db.scalar(stmt) or 0)


def get_for_account(
    db: Session, notification_id: int, account_id: int
) -> Notification | None:
    return db.scalar(
        select(Notification).where(
            Notification.id == notification_id,
            Notification.account_id == account_id,
        )
    )


def mark_read(
    db: Session, notification_id: int, account_id: int, *, commit: bool = True
) -> Notification | None:
    '''只允许本人标记自己的通知；不存在/不属于本人返回 None。'''
    row = get_for_account(db, notification_id, account_id)
    if row is None:
        return None
    if row.read_at is None:
        row.read_at = utcnow()
        if commit:
            db.commit()
            db.refresh(row)
        else:
            db.flush()
    return row


def mark_all_read(db: Session, account_id: int, *, commit: bool = True) -> int:
    result = db.execute(
        update(Notification)
        .where(
            Notification.account_id == account_id,
            Notification.read_at.is_(None),
        )
        .values(read_at=utcnow())
        .execution_options(synchronize_session=False)
    )
    if commit:
        db.commit()
    return int(result.rowcount or 0)
