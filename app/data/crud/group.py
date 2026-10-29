from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import Account, Group, GroupMember, Project
from app.errors import (
    GroupHasChildrenError,
    GroupNotEmptyError,
    GroupNotFoundError,
    InvalidGroupTypeError,
    MemberNotFoundError,
    PersonalGroupImmutableError,
    ValidationError,
)
from app.utils.time import utcnow


PERSONAL_QUOTA = {'storage_mb': 1024, 'images': 500, 'members': 1}
TEAM_DEFAULT_QUOTA = {'storage_mb': 10_240, 'images': 10_000, 'members': 10}
ORG_DEFAULT_QUOTA = {'storage_mb': 102_400, 'images': 100_000, 'members': 100}


def _get_or_raise(db: Session, group_id: int) -> Group:
    group = db.scalar(
        select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
    )
    if not group:
        raise GroupNotFoundError(group_id)
    return group


def create_personal_group(
    db: Session, *, account: Account, commit: bool = True
) -> Group:
    existing = db.scalar(
        select(Group).where(
            Group.owner_account_id == account.id,
            Group.type == 'personal',
            Group.deleted_at.is_(None),
        )
    )
    if existing:
        return existing

    group = Group(
        name=account.username,
        type='personal',
        parent_id=None,
        owner_account_id=account.id,
        quota_storage_mb=PERSONAL_QUOTA['storage_mb'],
        quota_images=PERSONAL_QUOTA['images'],
        quota_members=PERSONAL_QUOTA['members'],
        quota_source='own',
    )
    db.add(group)
    db.flush()

    db.add(
        GroupMember(
            group_id=group.id,
            account_id=account.id,
            role='owner',
            joined_at=utcnow(),
        )
    )

    if not commit:
        # 由调用方（如 register_account）统一提交，保证账号与空间同生共死
        db.flush()
        return group

    db.commit()
    db.refresh(group)
    return group


def get_personal_group(db: Session, account_id: int) -> Group | None:
    '''账号的个人空间；一个账号最多一个（type='personal' 且 owner 是本人）。'''
    return db.scalar(
        select(Group).where(
            Group.owner_account_id == account_id,
            Group.type == 'personal',
            Group.deleted_at.is_(None),
        )
    )


def create_group(
    db: Session,
    *,
    owner_account_id: int,
    name: str,
    type: str = 'team',
    parent_id: int | None = None,
    quota_storage_mb: int | None = None,
    quota_images: int | None = None,
    quota_members: int | None = None,
) -> Group:
    if type == 'personal':
        raise InvalidGroupTypeError('请使用 create_personal_group 创建个人空间')
    if type not in ('team', 'org'):
        raise InvalidGroupTypeError(f'unknown group type: {type}')

    owner = db.get(Account, owner_account_id)
    if not owner:
        raise ValidationError(f'owner account {owner_account_id} not found')

    if parent_id is not None:
        parent = _get_or_raise(db, parent_id)
        if parent.type == 'personal':
            raise ValidationError('personal 组不能作为父组')
        if parent.type == 'team':
            raise ValidationError('team 组不能包含子组，只有 org 可以')

    defaults = dict(ORG_DEFAULT_QUOTA if type == 'org' else TEAM_DEFAULT_QUOTA)
    if type == 'team':
        # 平台管理员在 /admin/storage-policy 里配置了 team 默认额度就优先用它
        from app.data.crud.platform_setting import read_setting

        setting = read_setting(db)
        if setting is not None:
            defaults['images'] = setting.team_quota_images
            defaults['storage_mb'] = setting.team_quota_storage_mb
    group = Group(
        name=name,
        type=type,
        parent_id=parent_id,
        owner_account_id=owner_account_id,
        quota_storage_mb=quota_storage_mb or defaults['storage_mb'],
        quota_images=quota_images or defaults['images'],
        quota_members=quota_members or defaults['members'],
        quota_source='own',
    )
    db.add(group)
    db.flush()
    db.add(
        GroupMember(
            group_id=group.id,
            account_id=owner_account_id,
            role='owner',
            joined_at=utcnow(),
        )
    )
    db.commit()
    db.refresh(group)
    return group


def get_group(db: Session, group_id: int) -> Group | None:
    return db.scalar(
        select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
    )


def list_account_groups(db: Session, account_id: int) -> list[Group]:
    stmt = (
        select(Group)
        .join(GroupMember, GroupMember.group_id == Group.id)
        .where(
            GroupMember.account_id == account_id,
            Group.deleted_at.is_(None),
        )
        .order_by(Group.type.desc(), Group.id)  #（'p' > 'o' > 't'）
    )
    return list(db.scalars(stmt).all())

def update_group(
    db: Session,
    group_id: int,
    *,
    name: str | None = None,
    quota_storage_mb: int | None = None,
    quota_images: int | None = None,
    quota_members: int | None = None,
) -> Group:
    group = _get_or_raise(db, group_id)
    if group.type == 'personal' and name is not None:
        raise PersonalGroupImmutableError('个人空间不允许改组名')

    if name is not None:
        group.name = name
    if quota_storage_mb is not None:
        group.quota_storage_mb = quota_storage_mb
    if quota_images is not None:
        group.quota_images = quota_images
    if quota_members is not None:
        group.quota_members = quota_members
    db.commit()
    db.refresh(group)
    return group


def delete_group(db: Session, group_id: int) -> None:
    '''软删。personal 不可删。'''
    group = _get_or_raise(db, group_id)
    if group.type == 'personal':
        raise PersonalGroupImmutableError('个人空间不可删除')

    child_count = db.scalar(
        select(func.count())
        .select_from(Group)
        .where(Group.parent_id == group_id, Group.deleted_at.is_(None))
    )
    if child_count:
        raise GroupHasChildrenError(group_id)

    # 活跃项目 = 未软删且未归档
    active_projects = db.scalar(
        select(func.count())
        .select_from(Project)
        .where(
            Project.group_id == group_id,
            Project.deleted_at.is_(None),
            Project.status != 'archived',
        )
    )
    if active_projects:
        raise GroupNotEmptyError(group_id)

    group.deleted_at = utcnow()
    db.commit()


def transfer_ownership(
    db: Session,
    group_id: int,
    *,
    new_owner_account_id: int,
) -> GroupMember:
    group = _get_or_raise(db, group_id)
    if group.type == 'personal':
        raise PersonalGroupImmutableError('个人空间不可转让')

    new_member = db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.account_id == new_owner_account_id,
        )
    )
    if not new_member:
        raise MemberNotFoundError(new_owner_account_id)
    if new_member.role == 'owner':
        return new_member  # 幂等

    old_owner = db.scalar(
        select(GroupMember).where(
            GroupMember.group_id == group_id,
            GroupMember.role == 'owner',
        )
    )
    if not old_owner:
        raise ValidationError(f'group {group_id} has no owner')

    old_owner.role = 'admin'
    new_member.role = 'owner'
    group.owner_account_id = new_owner_account_id
    db.commit()
    db.refresh(new_member)
    return new_member