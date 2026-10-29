from sqlalchemy import func, select
from sqlalchemy.orm import Session

from fastapi import APIRouter, status

from app.data import Account, Group, GroupMember
from app.data.crud.group import (
    create_group,
    delete_group,
    get_group,
    list_account_groups,
    transfer_ownership,
    update_group,
)
from app.data.crud.group_member import (
    add_member,
    change_member_role,
    get_membership,
    leave_group,
    list_members,
    remove_member,
)
from app.deps import CurrentAccount, DBSession
from app.errors import (
    CannotRemoveOwnerError,
    GroupNotFoundError,
    MemberNotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from app.schemas.group import (
    GroupCreate,
    GroupMemberCreate,
    GroupMemberRead,
    GroupMemberRoleUpdate,
    GroupRead,
    GroupTransferRequest,
    GroupUpdate,
)

router = APIRouter(prefix='/groups', tags=['groups'])

MANAGE_ROLES = ('owner', 'admin')


# ---- 权限辅助 ----

def _load_team_group(db: Session, group_id: int) -> Group:
    group = get_group(db, group_id)
    if group is None or group.type == 'personal':
        raise GroupNotFoundError(group_id)
    return group


def _require_view(db: Session, account: Account, group_id: int) -> Group:
    '''组员才能看；不在组里 = 不存在。'''
    group = _load_team_group(db, group_id)
    if get_membership(db, group_id, account.id) is None:
        raise GroupNotFoundError(group_id)
    return group


def _require_manage(db: Session, account: Account, group_id: int) -> GroupMember:
    group = _load_team_group(db, group_id)
    membership = get_membership(db, group_id, account.id)
    if membership is None:
        raise GroupNotFoundError(group_id)
    if membership.role not in MANAGE_ROLES:
        raise PermissionDeniedError('需要 owner 或 admin 权限')
    return membership


def _require_owner(db: Session, account: Account, group_id: int) -> Group:
    group = _load_team_group(db, group_id)
    membership = get_membership(db, group_id, account.id)
    if membership is None:
        raise GroupNotFoundError(group_id)
    if membership.role != 'owner':
        raise PermissionDeniedError('需要 owner 权限')
    return group


def _target_member(db: Session, group_id: int, account_id: int) -> GroupMember:
    membership = get_membership(db, group_id, account_id)
    if membership is None:
        raise MemberNotFoundError(account_id)
    return membership


def _ensure_member_quota(db: Session, group: Group) -> None:
    '''quota_members 为 0 表示不限量。'''
    if not group.quota_members:
        return
    count = db.scalar(
        select(func.count())
        .select_from(GroupMember)
        .where(GroupMember.group_id == group.id)
    )
    if count >= group.quota_members:
        raise ValidationError(f'成员数已达上限（{group.quota_members}）')


# ---- 组本身 ----

@router.get('', response_model=list[GroupRead])
def list_my_groups(account: CurrentAccount, db: DBSession) -> list[Group]:
    '''我所在的 team / org 组；个人空间不在这里（去 /space）。'''
    return [
        group
        for group in list_account_groups(db, account.id)
        if group.type != 'personal'
    ]


@router.post('', response_model=GroupRead, status_code=status.HTTP_201_CREATED)
def create_team_group(
    payload: GroupCreate, account: CurrentAccount, db: DBSession
) -> Group:
    return create_group(
        db,
        owner_account_id=account.id,
        name=payload.name,
        type=payload.type,
        parent_id=payload.parent_id,
        quota_storage_mb=payload.quota_storage_mb,
        quota_images=payload.quota_images,
        quota_members=payload.quota_members,
    )


@router.get('/{group_id}', response_model=GroupRead)
def get_team_group(
    group_id: int, account: CurrentAccount, db: DBSession
) -> Group:
    return _require_view(db, account, group_id)


@router.patch('/{group_id}', response_model=GroupRead)
def update_team_group(
    payload: GroupUpdate, group_id: int, account: CurrentAccount, db: DBSession
) -> Group:
    _require_manage(db, account, group_id)
    return update_group(db, group_id, **payload.model_dump(exclude_unset=True))


@router.delete('/{group_id}', status_code=status.HTTP_204_NO_CONTENT)
def delete_team_group(
    group_id: int, account: CurrentAccount, db: DBSession
) -> None:
    _require_owner(db, account, group_id)
    delete_group(db, group_id)


@router.post('/{group_id}/transfer', response_model=GroupRead)
def transfer_team_group(
    payload: GroupTransferRequest,
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> Group:
    group = _require_owner(db, account, group_id)
    transfer_ownership(
        db, group_id, new_owner_account_id=payload.new_owner_account_id
    )
    db.refresh(group)
    return group


# ---- 成员 ----

@router.get('/{group_id}/members', response_model=list[GroupMemberRead])
def list_team_members(
    group_id: int, account: CurrentAccount, db: DBSession
) -> list[GroupMember]:
    _require_view(db, account, group_id)
    return list_members(db, group_id)


@router.post(
    '/{group_id}/members',
    response_model=GroupMemberRead,
    status_code=status.HTTP_201_CREATED,
)
def add_team_member(
    payload: GroupMemberCreate,
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> GroupMember:
    _require_manage(db, account, group_id)
    group = _load_team_group(db, group_id)

    if db.get(Account, payload.account_id) is None:
        raise ValidationError(f'account {payload.account_id} not found')

    existing = get_membership(db, group_id, payload.account_id)
    if existing is None:
        _ensure_member_quota(db, group)

    # add_member 是幂等的：已存在时返回原记录（member→admin 会顺手升级），
    # 并发重复提交撞唯一约束时也会回退并返回已有记录。
    membership = add_member(db, group_id, payload.account_id, role=payload.role)
    db.commit()
    db.refresh(membership)
    return membership


@router.patch(
    '/{group_id}/members/{account_id}', response_model=GroupMemberRead
)
def change_team_member_role(
    payload: GroupMemberRoleUpdate,
    group_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> GroupMember:
    me = _require_manage(db, account, group_id)
    target = _target_member(db, group_id, account_id)

    if target.role == 'owner':
        raise CannotRemoveOwnerError('不能直接改 owner 的角色，请先转让所有权')
    if me.role == 'admin' and target.role == 'admin':
        raise PermissionDeniedError('只有 owner 能调整其他管理员的角色')

    return change_member_role(db, group_id, account_id, role=payload.role)


@router.delete(
    '/{group_id}/members/{account_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_team_member(
    group_id: int,
    account_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> None:
    me = _require_manage(db, account, group_id)
    target = _target_member(db, group_id, account_id)

    if target.role == 'owner':
        raise CannotRemoveOwnerError('不能移除 owner，请先转让')
    if me.role == 'admin' and target.role == 'admin':
        raise PermissionDeniedError('只有 owner 能移除其他管理员')

    remove_member(db, group_id, account_id)


@router.post('/{group_id}/leave', status_code=status.HTTP_204_NO_CONTENT)
def leave_team_group(
    group_id: int, account: CurrentAccount, db: DBSession
) -> None:
    _require_view(db, account, group_id)
    leave_group(db, group_id, account.id)
