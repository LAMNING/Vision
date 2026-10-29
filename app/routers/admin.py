import os

from fastapi import APIRouter, Query, Response

from app.data.crud import admin as admin_crud
from app.data.crud import platform_setting as platform_setting_crud
from app.deps import DBSession, PlatformAdmin
from app.schemas.account import AccountRead
from app.schemas.admin import (
    AdminGroupRead,
    AdminProjectRead,
    AdminStatsRead,
    ModelConfigRead,
    ModelConfigUpdate,
    PasswordResetRequest,
    PlatformRoleUpdate,
    StoragePolicyRead,
    StoragePolicyUpdate,
)
from app.schemas.audit_log import AuditLogRead
from app.schemas.base import Page
from app.services import detector as detector_service
from app.services import storage as storage_service

router = APIRouter(prefix='/admin', tags=['admin'])


def _policy_read(setting) -> StoragePolicyRead:
    effective = storage_service.effective_uri()
    return StoragePolicyRead(
        storage_uri=setting.storage_uri,
        team_quota_images=setting.team_quota_images,
        team_quota_storage_mb=setting.team_quota_storage_mb,
        effective_uri=effective,
        backend=storage_service.backend_label(effective),
        updated_at=setting.updated_at,
    )


def _model_read(setting) -> ModelConfigRead:
    try:
        status = detector_service.detector_status()
    except detector_service.DetectorNotConfiguredError:
        status = {}
    path = status.get('path')
    translator_dir = status.get('translator_dir')
    return ModelConfigRead(
        detector_onnx=setting.detector_onnx,
        detector_dir=setting.detector_dir,
        detector_precision=setting.detector_precision,
        detector_device=setting.detector_device,
        detector_size=setting.detector_size,
        detector_max_size=setting.detector_max_size,
        translator_dir=setting.translator_dir,
        translator_precision=setting.translator_precision,
        effective_detector_path=status.get('path'),
        effective_detector_precision=status.get('precision'),
        effective_detector_device=status.get('device'),
        effective_detector_size=status.get('size') or detector_service.DEFAULT_SIZE,
        effective_detector_max_size=(
            status.get('max_size') or detector_service.DEFAULT_MAX_SIZE
        ),
        effective_translator_dir=status.get('translator_dir'),
        effective_translator_precision=(
            status.get('translator_precision')
            or detector_service.DEFAULT_TRANSLATOR_PRECISION
        ),
        detector_path_exists=bool(path and os.path.isfile(path)),
        translator_dir_exists=bool(
            translator_dir
            and os.path.isdir(os.path.join(translator_dir, 'onnx'))
        ),
        detector_configured=bool(status.get('configured')),
        translator_configured=bool(status.get('translator_configured')),
        detector_loaded=bool(status.get('loaded')),
        translator_loaded=bool(status.get('translator_loaded')),
        updated_at=setting.updated_at,
    )


@router.get('/accounts', response_model=Page[AccountRead])
def list_accounts(
    admin: PlatformAdmin,
    db: DBSession,
    search: str | None = None,
    platform_role: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    '''账号列表：search 模糊匹配 username/email/phone。'''
    rows, total = admin_crud.list_accounts(
        db, search=search, platform_role=platform_role, offset=offset, limit=limit
    )
    return Page(items=rows, total=total, offset=offset, limit=limit)


@router.get('/accounts/{account_id}', response_model=AccountRead)
def get_account(account_id: int, admin: PlatformAdmin, db: DBSession):
    return admin_crud.get_account_or_raise(db, account_id)


@router.patch('/accounts/{account_id}/role', response_model=AccountRead)
def update_platform_role(
    account_id: int,
    payload: PlatformRoleUpdate,
    admin: PlatformAdmin,
    db: DBSession,
):
    '''提升/取消平台管理员；不能改自己，也不能把最后一个 admin 降级。'''
    target = admin_crud.get_account_or_raise(db, account_id)
    return admin_crud.set_platform_role(
        db,
        target,
        role=payload.platform_role,
        actor_account_id=admin.id,
    )


@router.post('/accounts/{account_id}/reset-password', status_code=204)
def reset_account_password(
    account_id: int,
    payload: PasswordResetRequest,
    admin: PlatformAdmin,
    db: DBSession,
):
    target = admin_crud.get_account_or_raise(db, account_id)
    admin_crud.reset_password(
        db,
        target,
        new_password=payload.new_password,
        actor_account_id=admin.id,
    )
    return Response(status_code=204)


@router.get('/stats', response_model=AdminStatsRead)
def platform_stats(admin: PlatformAdmin, db: DBSession):
    return AdminStatsRead(**admin_crud.platform_stats(db))


@router.get('/groups', response_model=Page[AdminGroupRead])
def list_all_groups(
    admin: PlatformAdmin,
    db: DBSession,
    search: str | None = None,
    group_type: str | None = None,
    include_deleted: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows, total = admin_crud.list_all_groups(
        db,
        search=search,
        group_type=group_type,
        include_deleted=include_deleted,
        offset=offset,
        limit=limit,
    )
    items = [
        AdminGroupRead(
            id=row.group.id,
            name=row.group.name,
            type=row.group.type,
            owner_account_id=row.group.owner_account_id,
            owner_username=row.owner_username,
            member_count=row.member_count,
            created_at=row.group.created_at,
            deleted_at=row.group.deleted_at,
        )
        for row in rows
    ]
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get('/projects', response_model=Page[AdminProjectRead])
def list_all_projects(
    admin: PlatformAdmin,
    db: DBSession,
    search: str | None = None,
    status: str | None = None,
    include_deleted: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows, total = admin_crud.list_all_projects(
        db,
        search=search,
        status=status,
        include_deleted=include_deleted,
        offset=offset,
        limit=limit,
    )
    items = [
        AdminProjectRead(
            id=row.project.id,
            group_id=row.project.group_id,
            group_name=row.group_name,
            name=row.project.name,
            status=row.project.status,
            member_count=row.member_count,
            task_count=row.task_count,
            created_at=row.project.created_at,
            deleted_at=row.project.deleted_at,
        )
        for row in rows
    ]
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.get('/audit-logs', response_model=Page[AuditLogRead])
def list_audit_logs(
    admin: PlatformAdmin,
    db: DBSession,
    action_prefix: str | None = None,
    actor_account_id: int | None = None,
    group_id: int | None = None,
    resource_type: str | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    rows, total = admin_crud.list_audit_logs(
        db,
        action_prefix=action_prefix,
        actor_account_id=actor_account_id,
        group_id=group_id,
        resource_type=resource_type,
        offset=offset,
        limit=limit,
    )
    return Page(items=rows, total=total, offset=offset, limit=limit)


@router.get('/storage-policy', response_model=StoragePolicyRead)
def get_storage_policy(admin: PlatformAdmin, db: DBSession):
    '''当前存储策略：后台实际生效的 URI（平台设置 > 环境变量 > 默认）。'''
    return _policy_read(platform_setting_crud.get_setting(db))


@router.put('/storage-policy', response_model=StoragePolicyRead)
def update_storage_policy(
    payload: StoragePolicyUpdate,
    admin: PlatformAdmin,
    db: DBSession,
):
    '''更新存储后端和 team 默认额度；只改传了的字段，`storage_uri: null` 表示清空。'''
    fields = payload.model_dump(exclude_unset=True)
    setting = platform_setting_crud.update_setting(
        db, **fields, actor_account_id=admin.id
    )
    storage_service.set_override_uri(setting.storage_uri)
    return _policy_read(setting)


@router.get('/model-config', response_model=ModelConfigRead)
def get_model_config(admin: PlatformAdmin, db: DBSession):
    '''当前模型配置：DB 保存值 + 实际生效值（DB > 环境变量 > 默认）。'''
    return _model_read(platform_setting_crud.get_setting(db))


@router.put('/model-config', response_model=ModelConfigRead)
def update_model_config(
    payload: ModelConfigUpdate,
    admin: PlatformAdmin,
    db: DBSession,
):
    '''更新检测/翻译模型配置；空串或 null 表示回到环境变量。改完立即生效。'''
    fields = payload.model_dump(exclude_unset=True)
    setting = platform_setting_crud.update_setting(
        db, **fields, actor_account_id=admin.id
    )
    detector_service.set_override_config(
        platform_setting_crud.model_overrides(setting)
    )
    return _model_read(setting)
