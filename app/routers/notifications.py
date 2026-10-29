'''站内通知：列表、未读数、标记已读。'''

from typing import Annotated

from fastapi import APIRouter, Query

from app.data.crud import notification as notif_crud
from app.deps import CurrentAccount, DBSession
from app.errors import NotFoundError
from app.schemas.base import Page
from app.schemas.notification import NotificationRead, NotificationUnreadCount

router = APIRouter(prefix='/notifications', tags=['notifications'])


@router.get('', response_model=Page[NotificationRead])
def list_notifications(
    account: CurrentAccount,
    db: DBSession,
    unread_only: bool = False,
    offset: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> Page[NotificationRead]:
    '''当前账号的站内通知，按时间倒序。'''
    rows, total = notif_crud.list_for_account(
        db, account.id, unread_only=unread_only, offset=offset, limit=limit
    )
    return Page[NotificationRead](
        items=[NotificationRead.model_validate(row) for row in rows],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.get('/unread-count', response_model=NotificationUnreadCount)
def unread_count(account: CurrentAccount, db: DBSession) -> NotificationUnreadCount:
    return NotificationUnreadCount(count=notif_crud.unread_count(db, account.id))


@router.post('/read-all')
def mark_all_read(account: CurrentAccount, db: DBSession) -> dict:
    '''把当前账号所有未读通知标记为已读，返回受影响条数。'''
    return {'updated': notif_crud.mark_all_read(db, account.id)}


@router.post('/{notification_id}/read', response_model=NotificationRead)
def mark_read(
    notification_id: int, account: CurrentAccount, db: DBSession
) -> NotificationRead:
    row = notif_crud.mark_read(db, notification_id, account.id)
    if row is None:
        raise NotFoundError(f'notification {notification_id} not found')
    return NotificationRead.model_validate(row)
