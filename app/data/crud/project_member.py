from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.data import Project, ProjectMember
from app.errors import (
    InvalidProjectRoleError,
    ProjectMemberNotFoundError,
    ProjectNotFoundError,
)
from app.utils.time import utcnow


VALID_PROJECT_ROLES = ('manager', 'annotator', 'reviewer', 'viewer')


def _get_project_or_raise(db: Session, project_id: int) -> Project:
    project = db.scalar(
        select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
    )
    if not project:
        raise ProjectNotFoundError(project_id)
    return project


def _normalize_roles(roles: list[str]) -> list[str]:
    unknown = [r for r in roles if r not in VALID_PROJECT_ROLES]
    if unknown:
        raise InvalidProjectRoleError(
            f'invalid project role(s): {unknown}; '
            f'must be one of {VALID_PROJECT_ROLES}'
        )
    wanted = set(roles)
    return [r for r in VALID_PROJECT_ROLES if r in wanted]


def list_members(db: Session, project_id: int) -> list[ProjectMember]:
    _get_project_or_raise(db, project_id)
    now = utcnow()
    stmt = (
        select(ProjectMember)
        .options(joinedload(ProjectMember.account))
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.status == 'active',
            (ProjectMember.expires_at.is_(None))
            | (ProjectMember.expires_at > now),
        )
        .order_by(ProjectMember.joined_at, ProjectMember.id)
    )
    return list(db.scalars(stmt).all())


def get_membership(
    db: Session, project_id: int, account_id: int
) -> ProjectMember | None:
    return db.scalar(
        select(ProjectMember).where(
            ProjectMember.project_id == project_id,
            ProjectMember.account_id == account_id,
        )
    )


def upsert_member(
    db: Session,
    project_id: int,
    account_id: int,
    *,
    role: str,
    source_group_id: int | None = None,
    expires_at: datetime | None = None,
) -> ProjectMember:
    if role not in VALID_PROJECT_ROLES:
        raise InvalidProjectRoleError(
            f'invalid project role: {role!r}; must be one of {VALID_PROJECT_ROLES}'
        )

    membership = get_membership(db, project_id, account_id)
    if membership is None:
        membership = ProjectMember(
            project_id=project_id,
            account_id=account_id,
            roles=[role],
            status='active',
            source_group_id=source_group_id,
            expires_at=expires_at,
            joined_at=utcnow(),
        )
        db.add(membership)
        db.flush()
        return membership

    membership.status = 'active'
    membership.expires_at = expires_at
    if source_group_id is not None and membership.source_group_id is None:
        membership.source_group_id = source_group_id
    if membership.joined_at is None:
        membership.joined_at = utcnow()

    merged = _normalize_roles(list(membership.roles or []) + [role])
    if merged != list(membership.roles or []):
        membership.roles = merged

    db.flush()
    return membership


def remove_member(db: Session, project_id: int, account_id: int) -> None:
    membership = get_membership(db, project_id, account_id)
    if membership is None:
        raise ProjectMemberNotFoundError(project_id, account_id)
    membership.status = 'removed'
    db.commit()


def change_roles(
    db: Session, project_id: int, account_id: int, *, roles: list[str]
) -> ProjectMember:
    if not roles:
        raise InvalidProjectRoleError(
            'roles 不能为空；移除成员请使用 remove_member'
        )

    membership = get_membership(db, project_id, account_id)
    if membership is None:
        raise ProjectMemberNotFoundError(project_id, account_id)
    if membership.status != 'active':
        raise ProjectMemberNotFoundError(project_id, account_id)

    membership.roles = _normalize_roles(list(roles))
    db.commit()
    db.refresh(membership)
    return membership
