from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.data import Account, Project
from app.data.crud import group_member as gm
from app.data.crud import project_member as pm
from app.data.crud.group import get_group
from app.data.crud.project import get_project_or_raise
from app.errors import PermissionDeniedError, ProjectNotFoundError
from app.utils.time import utcnow

MANAGE_GROUP_ROLES = ('owner', 'admin')
UPLOAD_ROLES = ('manager', 'annotator')


@dataclass(frozen=True)
class ProjectAccess:
    project: Project
    roles: tuple[str, ...]
    group_role: str | None
    is_personal_owner: bool

    @property
    def can_view(self) -> bool:
        return bool(self.roles) or self.group_role is not None or self.is_personal_owner

    @property
    def can_manage(self) -> bool:
        if self.is_personal_owner:
            return True
        if self.group_role in MANAGE_GROUP_ROLES:
            return True
        return 'manager' in self.roles

    @property
    def can_upload(self) -> bool:
        '''往项目里传图：能管项目的都行，另外 manager/annotator 也行。'''
        if self.can_manage:
            return True
        return any(role in UPLOAD_ROLES for role in self.roles)

    @property
    def label(self) -> str:
        '''给前端展示的「我在这个项目里是谁」。'''
        if self.is_personal_owner:
            return 'owner'
        if self.roles:
            return ','.join(self.roles)
        return self.group_role or ''


def resolve_project_access(
    db: Session, account: Account, project_id: int
) -> ProjectAccess:
    project = get_project_or_raise(db, project_id)
    group = get_group(db, project.group_id)
    if group is None or group.deleted_at is not None:
        raise ProjectNotFoundError(project_id)

    membership = gm.get_membership(db, project.group_id, account.id)
    group_role = membership.role if membership is not None else None
    is_personal_owner = (
        group.type == 'personal' and group.owner_account_id == account.id
    )

    roles: tuple[str, ...] = ()
    pm_membership = pm.get_membership(db, project_id, account.id)
    if pm_membership is not None and pm_membership.status == 'active':
        now = utcnow()
        if (
            pm_membership.expires_at is None
            or pm_membership.expires_at > now
        ):
            roles = tuple(pm_membership.roles or ())

    return ProjectAccess(
        project=project,
        roles=roles,
        group_role=group_role,
        is_personal_owner=is_personal_owner,
    )


def require_view(db: Session, account: Account, project_id: int) -> ProjectAccess:
    access = resolve_project_access(db, account, project_id)
    if not access.can_view:
        raise ProjectNotFoundError(project_id)
    return access


def require_manage(db: Session, account: Account, project_id: int) -> ProjectAccess:
    access = resolve_project_access(db, account, project_id)
    if not access.can_view:
        raise ProjectNotFoundError(project_id)
    if not access.can_manage:
        raise PermissionDeniedError('需要项目 manager 或空间 owner/admin 权限')
    return access


def require_upload(db: Session, account: Account, project_id: int) -> ProjectAccess:
    '''往项目里传图的权限：can_upload（manager/annotator/空间管理员）。'''
    access = resolve_project_access(db, account, project_id)
    if not access.can_view:
        raise ProjectNotFoundError(project_id)
    if not access.can_upload:
        raise PermissionDeniedError(
            '需要项目 manager/annotator 或空间 owner/admin 权限'
        )
    return access
