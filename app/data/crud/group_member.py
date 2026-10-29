from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.data import Group, GroupMember
from app.errors import (
    CannotRemoveOwnerError,
    GroupNotFoundError,
    InvalidRoleError,
    MemberNotFoundError,
    PersonalGroupImmutableError,
)
from app.utils.time import utcnow

ASSIGNABLE_ROLES = ('admin', 'member')


def _get_group_or_raise(db: Session, group_id: int) -> Group:
    group = db.scalar(
        select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
    )
    if not group:
        raise GroupNotFoundError(group_id)
    return group


def list_members(db: Session, group_id: int) -> list[GroupMember]:
    _get_group_or_raise(db, group_id)
    stmt = (
        select(GroupMember)
        .options(joinedload(GroupMember.account))
        .where(GroupMember.group_id == group_id)
        .order_by(GroupMember.joined_at)
    )
    return list(db.scalars(stmt).all())


def get_membership(
    db: Session, group_id: int, account_id: int
) -> GroupMember | None:
    return db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.account_id == account_id,
        )
    )


def _insert_member_ignore_duplicate(
    db: Session, group_id: int, account_id: int, role: str
) -> None:
    '''插入成员；若 (group_id, account_id) 已存在（并发/重复提交）则静默跳过。

    用数据库的 ``INSERT ... ON CONFLICT DO NOTHING``（SQLite/PostgreSQL），
    避免先查后插的竞态；其它方言退化为普通插入（调用方已有预检查）。
    '''

    values = {
        'group_id': group_id,
        'account_id': account_id,
        'role': role,
        'joined_at': utcnow(),
    }
    dialect = db.get_bind().dialect.name
    if dialect == 'sqlite':
        from sqlalchemy.dialects.sqlite import insert as dialect_insert
    elif dialect == 'postgresql':
        from sqlalchemy.dialects.postgresql import insert as dialect_insert
    else:
        db.add(GroupMember(**values))
        db.flush()
        return

    stmt = (
        dialect_insert(GroupMember)
        .values(**values)
        .on_conflict_do_nothing(index_elements=['group_id', 'account_id'])
    )
    db.execute(stmt)


def add_member(
    db: Session,
    group_id: int,
    account_id: int,
    *,
    role: str = 'member',
) -> GroupMember:
    if role not in ASSIGNABLE_ROLES:
        raise InvalidRoleError(f'role must be one of {ASSIGNABLE_ROLES}')

    existing = get_membership(db, group_id, account_id)
    if existing is not None:
        # 已经是成员：允许把 member 提升为 admin（owner 不动），保持幂等。
        if existing.role != 'owner' and existing.role != role:
            existing.role = role
        return existing

    _insert_member_ignore_duplicate(db, group_id, account_id, role)
    membership = get_membership(db, group_id, account_id)
    if membership is None:  # pragma: no cover - 方言不支持 ON CONFLICT 且并发时
        raise RuntimeError(f'failed to add member {account_id} to group {group_id}')
    return membership


def change_member_role(
    db: Session,
    group_id: int,
    account_id: int,
    *,
    role: str,
) -> GroupMember:
    if role == 'owner':
        raise InvalidRoleError('请使用 transfer_ownership 转让所有者')
    if role not in ASSIGNABLE_ROLES:
        raise InvalidRoleError(f'role must be one of {ASSIGNABLE_ROLES}')

    membership = get_membership(db, group_id, account_id)
    if not membership:
        raise MemberNotFoundError(account_id)
    if membership.role == 'owner':
        raise CannotRemoveOwnerError('不能直接改 owner 的角色，请先转让所有权')

    membership.role = role
    db.commit()
    db.refresh(membership)
    return membership


def remove_member(db: Session, group_id: int, account_id: int) -> None:
    group = _get_group_or_raise(db, group_id)
    if group.type == 'personal':
        raise PersonalGroupImmutableError('个人空间不允许移除成员')

    membership = get_membership(db, group_id, account_id)
    if not membership:
        raise MemberNotFoundError(account_id)
    if membership.role == 'owner':
        raise CannotRemoveOwnerError('不能移除 owner，请先转让')

    db.delete(membership)
    db.commit()


def leave_group(db: Session, group_id: int, account_id: int) -> None:
    membership = get_membership(db, group_id, account_id)
    if not membership:
        raise MemberNotFoundError(account_id)
    if membership.role == 'owner':
        raise CannotRemoveOwnerError('owner 不能退出，请先转让或解散组')
    db.delete(membership)
    db.commit()