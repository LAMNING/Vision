from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.data import Account, Group, Invitation
from app.data.crud import group_member as gm
from app.data.crud import notification as notif_crud
from app.data.crud import project as pj
from app.data.crud import project_member as pm
from app.errors import (
    DuplicateInvitationError,
    GroupNotFoundError,
    InvalidProjectRoleError,
    InvalidRoleError,
    InvitationExpiredError,
    InvitationNotFoundError,
    InvitationNotPendingError,
    InvitationScopeError,
    PermissionDeniedError,
    ProjectArchivedError,
    ProjectNotFoundError,
    ValidationError,
)
from app.utils.time import utcnow


def _validate_role(scope: str, role: str) -> None:
    if scope == 'group':
        if role not in gm.ASSIGNABLE_ROLES:
            raise InvalidRoleError(
                f'group invitation role must be one of {gm.ASSIGNABLE_ROLES}, '
                f'got {role!r}（转让所有权请用 transfer_ownership）'
            )
    else:
        if role not in pm.VALID_PROJECT_ROLES:
            raise InvalidProjectRoleError(
                f'project invitation role must be one of '
                f'{pm.VALID_PROJECT_ROLES}, got {role!r}'
            )


def create_invitation(
    db: Session,
    *,
    scope: str,
    inviter_account_id: int,
    role: str,
    group_id: int | None = None,
    project_id: int | None = None,
    invitee_account_id: int | None = None,
    invitee_email: str | None = None,
    expires_at: datetime | None = None,
) -> Invitation:
    if scope not in ('group', 'project'):
        raise InvitationScopeError(scope)
    if (invitee_account_id is None) == (invitee_email is None):
        raise ValidationError('必须且仅提供 invitee_account_id 或 invitee_email 之一')

    _validate_role(scope, role)

    if inviter_account_id is None:
        raise ValidationError('缺少 inviter_account_id')
    if db.get(Account, inviter_account_id) is None:
        raise ValidationError(f'inviter account {inviter_account_id} not found')

    if invitee_account_id is not None and db.get(Account, invitee_account_id) is None:
        raise ValidationError(f'invitee account {invitee_account_id} not found')

    if scope == 'group':
        if group_id is None or project_id is not None:
            raise ValidationError('scope=group 时必须且仅提供 group_id')
        group = db.scalar(
            select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
        )
        if not group:
            raise GroupNotFoundError(group_id)
        if group.type == 'personal':
            raise ValidationError('个人空间不允许邀请成员')
    else:  # project
        if project_id is None or group_id is not None:
            raise ValidationError('scope=project 时必须且仅提供 project_id')
        project = pj.get_project(db, project_id)
        if not project:
            raise ProjectNotFoundError(project_id)
        if project.status == 'archived':
            raise ProjectArchivedError(project_id)

    dup_stmt = select(Invitation).where(
        Invitation.scope == scope,
        Invitation.status == 'pending',
    )
    if group_id is not None:
        dup_stmt = dup_stmt.where(Invitation.group_id == group_id)
    if project_id is not None:
        dup_stmt = dup_stmt.where(Invitation.project_id == project_id)
    if invitee_account_id is not None:
        dup_stmt = dup_stmt.where(
            Invitation.invitee_account_id == invitee_account_id
        )
    else:
        dup_stmt = dup_stmt.where(Invitation.invitee_email == invitee_email)
    if db.scalar(dup_stmt):
        raise DuplicateInvitationError()

    invitation = Invitation(
        scope=scope,
        group_id=group_id,
        project_id=project_id,
        inviter_account_id=inviter_account_id,
        invitee_account_id=invitee_account_id,
        invitee_email=invitee_email,
        role=role,
        status='pending',
        expires_at=expires_at,
    )
    db.add(invitation)
    db.commit()
    db.refresh(invitation)
    if invitee_account_id is not None:
        scope_label = '组' if scope == 'group' else '项目'
        notif_crud.create(
            db,
            invitee_account_id,
            kind='invitation',
            title=f'你收到一个{scope_label}邀请',
            body=f'角色：{role}',
            resource_type='invitation',
            resource_id=invitation.id,
        )
    return invitation


def list_account_invitations(
    db: Session, account: Account, *, status: str | None = 'pending'
) -> list[Invitation]:
    conds = [Invitation.invitee_account_id == account.id]
    if account.email:
        conds.append(Invitation.invitee_email == account.email)
    stmt = select(Invitation).where(or_(*conds))
    if status is not None:
        stmt = stmt.where(Invitation.status == status)
    stmt = stmt.order_by(Invitation.created_at.desc())
    return list(db.scalars(stmt).all())


def list_scope_invitations(
    db: Session,
    *,
    group_id: int | None = None,
    project_id: int | None = None,
    status: str | None = 'pending',
) -> list[Invitation]:
    '''管理员查看某个组 / 项目下的邀请。'''
    stmt = select(Invitation)
    if group_id is not None:
        stmt = stmt.where(Invitation.group_id == group_id)
    if project_id is not None:
        stmt = stmt.where(Invitation.project_id == project_id)
    if status is not None:
        stmt = stmt.where(Invitation.status == status)
    stmt = stmt.order_by(Invitation.created_at.desc(), Invitation.id.desc())
    return list(db.scalars(stmt).all())


def _get_pending_or_raise(db: Session, invitation_id: int) -> Invitation:
    invitation = db.get(Invitation, invitation_id)
    if not invitation:
        raise InvitationNotFoundError(f'invitation {invitation_id} not found')
    if invitation.status != 'pending':
        raise InvitationNotPendingError(invitation.status)
    return invitation


def _ensure_invitee(invitation: Invitation, account: Account) -> None:
    matched = invitation.invitee_account_id == account.id or (
        invitation.invitee_email is not None
        and invitation.invitee_email == account.email
    )
    if not matched:
        raise PermissionDeniedError('invitation does not belong to this account')


def accept_invitation(
    db: Session, invitation_id: int, *, account: Account
) -> Invitation:
    invitation = _get_pending_or_raise(db, invitation_id)
    _ensure_invitee(invitation, account)

    now = utcnow()

    # 先判过期，再写成员关系 —— 否则过期邀请也会把人加进项目/组
    if invitation.expires_at and now > invitation.expires_at:
        invitation.status = 'expired'
        invitation.responded_at = now
        db.commit()
        raise InvitationExpiredError()

    if invitation.scope == 'group':
        gm.add_member(db, invitation.group_id, account.id, role=invitation.role)
    else:
        project = pj.get_project(db, invitation.project_id)
        if not project:
            invitation.status = 'revoked'
            invitation.responded_at = now
            db.commit()
            raise ProjectNotFoundError(invitation.project_id)
        pm.upsert_member(
            db,
            invitation.project_id,
            account.id,
            role=invitation.role,
            source_group_id=None,
            expires_at=invitation.expires_at,
        )

    invitation.status = 'accepted'
    invitation.responded_at = now
    # email 邀请接受后绑定到账号，便于后续追溯
    if invitation.invitee_account_id is None:
        invitation.invitee_account_id = account.id
    db.commit()
    db.refresh(invitation)
    notif_crud.create(
        db,
        invitation.inviter_account_id,
        kind='invitation',
        title=f'邀请已被接受：{invitation.scope} 邀请 #{invitation.id}',
        body=f'角色：{invitation.role}',
        resource_type='invitation',
        resource_id=invitation.id,
    )
    return invitation


def reject_invitation(
    db: Session, invitation_id: int, *, account: Account
) -> Invitation:
    invitation = _get_pending_or_raise(db, invitation_id)
    _ensure_invitee(invitation, account)
    invitation.status = 'rejected'
    invitation.responded_at = utcnow()
    db.commit()
    db.refresh(invitation)
    notif_crud.create(
        db,
        invitation.inviter_account_id,
        kind='invitation',
        title=f'邀请已被拒绝：{invitation.scope} 邀请 #{invitation.id}',
        body=f'角色：{invitation.role}',
        resource_type='invitation',
        resource_id=invitation.id,
    )
    return invitation


def revoke_invitation(
    db: Session, invitation_id: int, *, actor: Account
) -> Invitation:
    invitation = _get_pending_or_raise(db, invitation_id)
    if invitation.inviter_account_id != actor.id:
        allowed = False
        # 组邀请：组 owner / admin 可撤回
        if invitation.group_id is not None:
            m = gm.get_membership(db, invitation.group_id, actor.id)
            allowed = bool(m and m.role in ('owner', 'admin'))
        # 项目邀请：项目的 admin 级角色（manager）可撤回
        if not allowed and invitation.project_id is not None:
            m = pm.get_membership(db, invitation.project_id, actor.id)
            allowed = bool(
                m
                and m.status == 'active'
                and 'manager' in (m.roles or [])
            )
        if not allowed:
            raise PermissionDeniedError('无权撤回该邀请')
    invitation.status = 'revoked'
    invitation.responded_at = utcnow()
    db.commit()
    db.refresh(invitation)
    return invitation
