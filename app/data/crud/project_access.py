from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.data import Account, Group, Project
from app.data.crud import enterprise as enterprise_crud
from app.data.crud import group_member as gm
from app.data.crud import project_member as pm
from app.data.crud.group import get_group
from app.data.crud.project import get_project_or_raise
from app.errors import (
    GroupNotFoundError,
    PermissionDeniedError,
    ProjectNotFoundError,
)
from app.utils.time import utcnow

MANAGE_GROUP_ROLES = ('owner', 'admin')
UPLOAD_ROLES = ('manager', 'annotator')
ENTERPRISE_MANAGE_ROLES = ('owner', 'admin')
ENTERPRISE_UPLOAD_ROLES = ('owner', 'admin', 'member')


@dataclass(frozen=True)
class ProjectAccess:
    project: Project
    roles: tuple[str, ...]
    group_role: str | None
    is_personal_owner: bool
    # 项目挂在企业背后的隐藏组上时，这里是该账号的企业角色
    enterprise_role: str | None = None

    @property
    def can_view(self) -> bool:
        return (
            bool(self.roles)
            or self.group_role is not None
            or self.is_personal_owner
            or self.enterprise_role is not None
        )

    @property
    def can_manage(self) -> bool:
        if self.is_personal_owner:
            return True
        if self.group_role in MANAGE_GROUP_ROLES:
            return True
        if self.enterprise_role in ENTERPRISE_MANAGE_ROLES:
            return True
        return 'manager' in self.roles

    @property
    def can_upload(self) -> bool:
        '''往项目里传图：能管项目的都行，另外 manager/annotator 也行。'''
        if self.can_manage:
            return True
        if self.enterprise_role in ENTERPRISE_UPLOAD_ROLES:
            return True
        return any(role in UPLOAD_ROLES for role in self.roles)

    @property
    def can_review(self) -> bool:
        '''审核任务：项目里带 reviewer/manager，或企业里审核员及以上。'''
        if self.can_manage:
            return True
        if self.enterprise_role in ('owner', 'admin', 'reviewer'):
            return True
        return any(role in ('manager', 'reviewer') for role in self.roles)

    @property
    def label(self) -> str:
        '''给前端展示的「我在这个项目里是谁」。'''
        if self.is_personal_owner:
            return 'owner'
        if self.roles:
            return ','.join(self.roles)
        if self.group_role:
            return self.group_role
        return self.enterprise_role or ''


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

    # 企业项目挂在企业背后的隐藏组上；企业成员不走 GroupMember，单独查一次
    enterprise_role = None
    if group.type == 'org':
        enterprise_role = enterprise_crud.enterprise_role_for_group(
            db, group.id, account.id
        )

    return ProjectAccess(
        project=project,
        roles=roles,
        group_role=group_role,
        is_personal_owner=is_personal_owner,
        enterprise_role=enterprise_role,
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


def require_group_manage(db: Session, account: Account, group_id: int) -> Group:
    '''在某个空间里建项目：个人空间主人、组的 owner/admin、企业的 owner/admin。

    企业背后的隐藏组没有 GroupMember，只能靠 EnterpriseMember 判断。
    '''
    group = get_group(db, group_id)
    if group is None or group.deleted_at is not None:
        raise GroupNotFoundError(group_id)
    if group.owner_account_id == account.id:
        return group

    membership = gm.get_membership(db, group_id, account.id)
    if membership is not None and membership.role in MANAGE_GROUP_ROLES:
        return group

    if group.type == 'org':
        role = enterprise_crud.enterprise_role_for_group(db, group_id, account.id)
        if role in ENTERPRISE_MANAGE_ROLES:
            return group

    raise PermissionDeniedError('需要空间 owner/admin 权限才能新建项目')
