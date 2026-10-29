from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.data.account import Account
from app.data.annotation import Annotation
from app.data.audit_log import AuditLog
from app.data.crud import audit as audit_crud
from app.data.group import Group, GroupMember
from app.data.image import Image
from app.data.invitation import Invitation
from app.data.project import Project
from app.data.project_member import ProjectMember
from app.data.task import Task
from app.errors import AccountNotFoundError, ConflictError, ValidationError
from app.utils.security import hash_password

PLATFORM_ROLES = ('user', 'admin')


@dataclass(frozen=True)
class AdminGroupRow:
    group: Group
    owner_username: str | None
    member_count: int


@dataclass(frozen=True)
class AdminProjectRow:
    project: Project
    group_name: str | None
    member_count: int
    task_count: int



def get_account(db: Session, account_id: int) -> Account | None:
    return db.get(Account, account_id)


def get_account_or_raise(db: Session, account_id: int) -> Account:
    account = get_account(db, account_id)
    if account is None:
        raise AccountNotFoundError(account_id)
    return account


def list_accounts(
    db: Session,
    *,
    search: str | None = None,
    platform_role: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Account], int]:
    conditions = []
    if search:
        like = f'%{search}%'
        conditions.append(
            or_(
                Account.username.like(like),
                Account.email.like(like),
                Account.phone.like(like),
            )
        )
    if platform_role:
        if platform_role not in PLATFORM_ROLES:
            raise ValidationError(f'unknown platform_role: {platform_role}')
        conditions.append(Account.platform_role == platform_role)

    total = db.scalar(select(func.count(Account.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(Account)
            .where(*conditions)
            .order_by(Account.id)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, int(total)


def set_platform_role(
    db: Session,
    account: Account,
    *,
    role: str,
    actor_account_id: int,
) -> Account:
    if role not in PLATFORM_ROLES:
        raise ValidationError(f'unknown platform_role: {role}')
    if account.id == actor_account_id:
        raise ValidationError('不能修改自己的平台角色')
    if role == account.platform_role:
        return account
    if (
        account.platform_role == 'admin'
        and role != 'admin'
        and (db.scalar(select(func.count(Account.id)).where(Account.platform_role == 'admin')) or 0) <= 1
    ):
        raise ConflictError('至少保留一个平台管理员')

    before = {'platform_role': account.platform_role}
    account.platform_role = role
    audit_crud.record(
        db,
        action='account.platform_role_changed',
        resource_type='account',
        resource_id=account.id,
        actor_account_id=actor_account_id,
        before=before,
        after={'platform_role': role},
        commit=False,
    )
    db.commit()
    db.refresh(account)
    return account


def reset_password(
    db: Session,
    account: Account,
    *,
    new_password: str,
    actor_account_id: int,
) -> None:
    account.password = hash_password(new_password)
    account.token_version += 1
    audit_crud.record(
        db,
        action='account.password_reset',
        resource_type='account',
        resource_id=account.id,
        actor_account_id=actor_account_id,
        after={'token_version': account.token_version},
        commit=False,
    )
    db.commit()
    db.refresh(account)


def _count(db: Session, model, *conditions) -> int:
    return int(db.scalar(select(func.count(model.id)).where(*conditions)) or 0)


def platform_stats(db: Session) -> dict[str, int]:
    active = lambda model: model.deleted_at.is_(None)  # noqa: E731
    return {
        'accounts': _count(db, Account),
        'admins': _count(db, Account, Account.platform_role == 'admin'),
        'groups': _count(db, Group, active(Group)),
        'projects': _count(db, Project, active(Project)),
        'images': _count(db, Image, active(Image)),
        'tasks': _count(db, Task, active(Task)),
        'annotations': _count(db, Annotation),
        'pending_invitations': _count(db, Invitation, Invitation.status == 'pending'),
    }


def list_all_groups(
    db: Session,
    *,
    search: str | None = None,
    group_type: str | None = None,
    include_deleted: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[AdminGroupRow], int]:
    conditions = []
    if not include_deleted:
        conditions.append(Group.deleted_at.is_(None))
    if search:
        conditions.append(Group.name.like(f'%{search}%'))
    if group_type:
        conditions.append(Group.type == group_type)

    total = db.scalar(select(func.count(Group.id)).where(*conditions)) or 0
    groups = list(
        db.scalars(
            select(Group)
            .where(*conditions)
            .order_by(Group.id)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    if not groups:
        return [], int(total)

    ids = [g.id for g in groups]
    owner_ids = [g.owner_account_id for g in groups]
    owners = dict(
        db.execute(
            select(Account.id, Account.username).where(Account.id.in_(owner_ids))
        ).all()
    )
    member_counts = dict(
        db.execute(
            select(GroupMember.group_id, func.count(GroupMember.id))
            .where(GroupMember.group_id.in_(ids))
            .group_by(GroupMember.group_id)
        ).all()
    )
    rows = [
        AdminGroupRow(
            group=g,
            owner_username=owners.get(g.owner_account_id),
            member_count=int(member_counts.get(g.id, 0)),
        )
        for g in groups
    ]
    return rows, int(total)


def list_all_projects(
    db: Session,
    *,
    search: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[AdminProjectRow], int]:
    conditions = []
    if not include_deleted:
        conditions.append(Project.deleted_at.is_(None))
    if search:
        conditions.append(Project.name.like(f'%{search}%'))
    if status:
        conditions.append(Project.status == status)

    total = db.scalar(select(func.count(Project.id)).where(*conditions)) or 0
    projects = list(
        db.scalars(
            select(Project)
            .where(*conditions)
            .order_by(Project.id)
            .offset(offset)
            .limit(limit)
        ).all()
    )
    if not projects:
        return [], int(total)

    ids = [p.id for p in projects]
    group_ids = [p.group_id for p in projects]
    group_names = dict(
        db.execute(select(Group.id, Group.name).where(Group.id.in_(group_ids))).all()
    )
    member_counts = dict(
        db.execute(
            select(ProjectMember.project_id, func.count(ProjectMember.id))
            .where(ProjectMember.project_id.in_(ids))
            .group_by(ProjectMember.project_id)
        ).all()
    )
    task_counts = dict(
        db.execute(
            select(Task.project_id, func.count(Task.id))
            .where(Task.project_id.in_(ids), Task.deleted_at.is_(None))
            .group_by(Task.project_id)
        ).all()
    )
    rows = [
        AdminProjectRow(
            project=p,
            group_name=group_names.get(p.group_id),
            member_count=int(member_counts.get(p.id, 0)),
            task_count=int(task_counts.get(p.id, 0)),
        )
        for p in projects
    ]
    return rows, int(total)


def list_audit_logs(
    db: Session,
    *,
    action_prefix: str | None = None,
    actor_account_id: int | None = None,
    group_id: int | None = None,
    resource_type: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[AuditLog], int]:
    conditions = []
    if action_prefix:
        conditions.append(AuditLog.action.like(f'{action_prefix}%'))
    if actor_account_id is not None:
        conditions.append(AuditLog.actor_account_id == actor_account_id)
    if group_id is not None:
        conditions.append(AuditLog.group_id == group_id)
    if resource_type:
        conditions.append(AuditLog.resource_type == resource_type)

    total = db.scalar(select(func.count(AuditLog.id)).where(*conditions)) or 0
    rows = list(
        db.scalars(
            select(AuditLog)
            .where(*conditions)
            .order_by(AuditLog.id.desc())
            .offset(offset)
            .limit(limit)
        ).all()
    )
    return rows, int(total)
