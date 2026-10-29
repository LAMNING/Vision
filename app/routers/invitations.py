from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from app.data import Account
from app.data.crud.invitation import (
    accept_invitation,
    create_invitation,
    list_account_invitations,
    list_scope_invitations,
    reject_invitation,
    revoke_invitation,
)
from app.data.crud.project_access import require_manage as require_project_manage
from app.deps import CurrentAccount, DBSession
from app.routers.groups import _require_manage as require_group_manage
from app.schemas.invitation import InvitationCreateBody, InvitationRead

router = APIRouter(tags=['invitations'])


def _status_param(value: str | None) -> str | None:
    '''``all`` / 省略 → 不过滤状态。'''
    if value in (None, '', 'all'):
        return None
    return value


@router.get('/invitations', response_model=list[InvitationRead])
def list_my_invitations(
    account: CurrentAccount,
    db: DBSession,
    status: Annotated[str | None, Query()] = 'pending',
) -> list:
    return list_account_invitations(db, account, status=_status_param(status))


@router.post(
    '/groups/{group_id}/invitations',
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
)
def invite_to_group(
    payload: InvitationCreateBody,
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> object:
    require_group_manage(db, account, group_id)
    return create_invitation(
        db,
        scope='group',
        group_id=group_id,
        inviter_account_id=account.id,
        role=payload.role,
        invitee_account_id=payload.invitee_account_id,
        invitee_email=payload.invitee_email,
        expires_at=payload.expires_at,
    )


@router.get('/groups/{group_id}/invitations', response_model=list[InvitationRead])
def list_group_invitations(
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
    status: Annotated[str | None, Query()] = 'pending',
) -> list:
    require_group_manage(db, account, group_id)
    return list_scope_invitations(
        db, group_id=group_id, status=_status_param(status)
    )


@router.post(
    '/projects/{project_id}/invitations',
    response_model=InvitationRead,
    status_code=status.HTTP_201_CREATED,
)
def invite_to_project(
    payload: InvitationCreateBody,
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> object:
    require_project_manage(db, account, project_id)
    return create_invitation(
        db,
        scope='project',
        project_id=project_id,
        inviter_account_id=account.id,
        role=payload.role,
        invitee_account_id=payload.invitee_account_id,
        invitee_email=payload.invitee_email,
        expires_at=payload.expires_at,
    )


@router.get(
    '/projects/{project_id}/invitations', response_model=list[InvitationRead]
)
def list_project_invitations(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    status: Annotated[str | None, Query()] = 'pending',
) -> list:
    require_project_manage(db, account, project_id)
    return list_scope_invitations(
        db, project_id=project_id, status=_status_param(status)
    )


@router.post('/invitations/{invitation_id}/accept', response_model=InvitationRead)
def accept_my_invitation(
    invitation_id: int, account: CurrentAccount, db: DBSession
) -> object:
    return accept_invitation(db, invitation_id, account=account)


@router.post('/invitations/{invitation_id}/reject', response_model=InvitationRead)
def reject_my_invitation(
    invitation_id: int, account: CurrentAccount, db: DBSession
) -> object:
    return reject_invitation(db, invitation_id, account=account)


@router.delete(
    '/invitations/{invitation_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_pending_invitation(
    invitation_id: int, account: CurrentAccount, db: DBSession
) -> None:
    revoke_invitation(db, invitation_id, actor=account)
