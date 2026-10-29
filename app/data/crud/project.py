from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.data import (
    Account,
    Group,
    GroupMember,
    Invitation,
    Project,
    ProjectMember,
)
from app.data.crud import project_member as pm
from app.errors import (
    GroupNotFoundError,
    ProjectArchivedError,
    ProjectNameConflictError,
    ProjectNotFoundError,
    ValidationError,
)
from app.utils.time import utcnow

VALID_DEFAULT_REVIEWERS = ('submitter', 'project_manager')

UNSET = object()


def get_project(db: Session, project_id: int) -> Project | None:
    return db.scalar(
        select(Project).where(
            Project.id == project_id, Project.deleted_at.is_(None)
        )
    )


def get_project_or_raise(db: Session, project_id: int) -> Project:
    project = get_project(db, project_id)
    if not project:
        raise ProjectNotFoundError(project_id)
    return project


def get_archivable_project(db: Session, project_id: int) -> Project:
    project = get_project_or_raise(db, project_id)
    if project.status == 'archived':
        raise ProjectArchivedError(project_id)
    return project


def list_group_projects(
    db: Session, group_id: int, *, include_archived: bool = False
) -> list[Project]:
    stmt = select(Project).where(
        Project.group_id == group_id, Project.deleted_at.is_(None)
    )
    if not include_archived:
        stmt = stmt.where(Project.status == 'active')
    return list(db.scalars(stmt.order_by(Project.id)).all())


def list_account_projects(db: Session, account_id: int) -> list[Project]:
    now = utcnow()
    stmt = (
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.account_id == account_id,
            ProjectMember.status == 'active',
            (ProjectMember.expires_at.is_(None))
            | (ProjectMember.expires_at > now),
            Project.deleted_at.is_(None),
        )
        .order_by(Project.id)
    )
    return list(db.scalars(stmt).all())


def list_viewable_projects(
    db: Session, account_id: int, *, include_archived: bool = False
) -> list[Project]:
    '''我能看到的项目：自己管的组/个人空间里的、所在组里的、被显式加成员的、所在企业里的。'''
    from app.data.enterprise import Enterprise, EnterpriseMember

    owned_group_ids = select(Group.id).where(Group.owner_account_id == account_id)
    member_group_ids = select(GroupMember.group_id).where(
        GroupMember.account_id == account_id
    )
    project_ids = select(ProjectMember.project_id).where(
        ProjectMember.account_id == account_id,
        ProjectMember.status == 'active',
    )
    # 企业项目挂企业背后的隐藏组，成员没有 GroupMember 行，单独查一遍
    enterprise_group_ids = (
        select(Enterprise.group_id)
        .join(
            EnterpriseMember,
            EnterpriseMember.enterprise_id == Enterprise.id,
        )
        .where(EnterpriseMember.account_id == account_id)
    )

    stmt = select(Project).where(
        Project.deleted_at.is_(None),
        or_(
            Project.group_id.in_(owned_group_ids),
            Project.group_id.in_(member_group_ids),
            Project.group_id.in_(enterprise_group_ids),
            Project.id.in_(project_ids),
        ),
    )
    if not include_archived:
        stmt = stmt.where(Project.status == 'active')
    return list(db.scalars(stmt.order_by(Project.id)).all())


def _ensure_group(db: Session, group_id: int) -> Group:
    group = db.scalar(
        select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
    )
    if not group:
        raise GroupNotFoundError(group_id)
    return group


def _ensure_name_free(
    db: Session, group_id: int, name: str, *, exclude_id: int | None = None
) -> None:
    stmt = select(Project.id).where(
        Project.group_id == group_id,
        Project.name == name,
        Project.deleted_at.is_(None),
    )
    if exclude_id is not None:
        stmt = stmt.where(Project.id != exclude_id)
    if db.scalar(stmt):
        raise ProjectNameConflictError(name)


def _validate_default_reviewer(value: str) -> str:
    if value not in VALID_DEFAULT_REVIEWERS:
        raise ValidationError(
            f'default_reviewer must be one of {VALID_DEFAULT_REVIEWERS}, got {value!r}'
        )
    return value


def create_project(
    db: Session,
    *,
    group_id: int,
    name: str,
    description: str | None = None,
    default_reviewer: str = 'submitter',
    manager_account_id: int | None = None,
) -> Project:
    name = (name or '').strip()
    if not name:
        raise ValidationError('项目名不能为空')
    if len(name) > 100:
        raise ValidationError('项目名过长（上限 100 字符）')

    _ensure_group(db, group_id)
    _validate_default_reviewer(default_reviewer)
    _ensure_name_free(db, group_id, name)

    if manager_account_id is not None:
        if db.get(Account, manager_account_id) is None:
            raise ValidationError(f'account {manager_account_id} not found')

    project = Project(
        group_id=group_id,
        name=name,
        description=description,
        default_reviewer=default_reviewer,
        status='active',
    )
    db.add(project)
    db.flush()

    if manager_account_id is not None:
        pm.upsert_member(db, project.id, manager_account_id, role='manager')

    db.commit()
    db.refresh(project)
    return project


def update_project(
    db: Session,
    project_id: int,
    *,
    name: str | None = None,
    description: str | None | object = UNSET,
    default_reviewer: str | None = None,
) -> Project:
    project = get_project_or_raise(db, project_id)

    if name is not None:
        name = name.strip()
        if not name:
            raise ValidationError('项目名不能为空')
        if len(name) > 100:
            raise ValidationError('项目名过长（上限 100 字符）')
        if name != project.name:
            _ensure_name_free(db, project.group_id, name, exclude_id=project.id)
            project.name = name

    if description is not UNSET:
        project.description = description  # type: ignore[assignment]

    if default_reviewer is not None:
        project.default_reviewer = _validate_default_reviewer(default_reviewer)

    db.commit()
    db.refresh(project)
    return project


def archive_project(db: Session, project_id: int) -> Project:
    project = get_project_or_raise(db, project_id)
    if project.status != 'archived':
        project.status = 'archived'
        db.commit()
        db.refresh(project)
    return project


def restore_project(db: Session, project_id: int) -> Project:
    project = get_project_or_raise(db, project_id)
    if project.status != 'active':
        project.status = 'active'
        db.commit()
        db.refresh(project)
    return project


def delete_project(db: Session, project_id: int) -> None:
    project = get_project_or_raise(db, project_id)
    now = utcnow()

    db.execute(
        update(Invitation)
        .where(
            Invitation.project_id == project.id,
            Invitation.status == 'pending',
        )
        .values(status='revoked', responded_at=now)
    )

    project.deleted_at = now
    db.commit()
