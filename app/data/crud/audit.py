from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.data.audit_log import AuditLog


AUDIT_FIELD_WHITELIST: dict[str, tuple[str, ...]] = {
    'account': ('username', 'email', 'phone', 'platform_role', 'token_version'),
    'groups': ('name', 'type', 'owner_account_id', 'deleted_at'),
    'group_member': ('group_id', 'account_id', 'role'),
    'project': ('name', 'description', 'default_reviewer', 'status', 'deleted_at'),
    'project_member': ('project_id', 'account_id', 'roles', 'status', 'expires_at'),
    'invitation': ('scope', 'group_id', 'project_id', 'role', 'status'),
    'image': ('group_id', 'filename', 'content_hash', 'size_bytes', 'deleted_at'),
    'task': (
        'project_id',
        'name',
        'status',
        'mode',
        'assignee_account_id',
        'priority',
        'due_at',
        'note',
        'submitter_account_id',
        'reviewer_account_id',
        'retry_count',
        'version',
        'completed_at',
    ),
    'task_image': (
        'task_id',
        'image_id',
        'annotation_status',
        'review_status',
        'revision',
        'reject_reason',
    ),
    'annotation': ('task_image_id', 'label', 'annotation_type', 'source', 'revision', 'is_current'),
    'review': ('task_id', 'round', 'decision', 'approved_count', 'rejected_count'),
    'platform_setting': (
        'storage_uri',
        'team_quota_images',
        'team_quota_storage_mb',
        'detector_onnx',
        'detector_dir',
        'detector_precision',
        'detector_device',
        'detector_size',
        'detector_max_size',
        'translator_dir',
        'translator_precision',
    ),
}


def _jsonable(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def snapshot(obj: Any, fields: tuple[str, ...] | None = None) -> dict[str, Any]:
    table = getattr(obj, '__tablename__', None)
    if fields is None:
        fields = AUDIT_FIELD_WHITELIST.get(table, ())
    return {
        name: _jsonable(getattr(obj, name, None))
        for name in fields
        if hasattr(obj, name)
    }


def record(
    db: Session,
    *,
    action: str,
    resource_type: str,
    resource_id: int,
    actor_account_id: int | None = None,
    group_id: int | None = None,
    before: dict | None = None,
    after: dict | None = None,
    commit: bool = False,
) -> AuditLog:
    entry = AuditLog(
        group_id=group_id,
        actor_account_id=actor_account_id,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        before=_jsonable(before) if before is not None else None,
        after=_jsonable(after) if after is not None else None,
    )
    db.add(entry)
    if commit:
        db.commit()
        db.refresh(entry)
    else:
        db.flush()
    return entry


def list_logs(
    db: Session,
    *,
    group_id: int | None = None,
    action: str | None = None,
    action_prefix: str | None = None,
    resource_type: str | None = None,
    resource_id: int | None = None,
    limit: int = 100,
) -> list[AuditLog]:
    stmt = select(AuditLog)
    if group_id is not None:
        stmt = stmt.where(AuditLog.group_id == group_id)
    if action is not None:
        stmt = stmt.where(AuditLog.action == action)
    if action_prefix is not None:
        stmt = stmt.where(AuditLog.action.like(f'{action_prefix}%'))
    if resource_type is not None:
        stmt = stmt.where(AuditLog.resource_type == resource_type)
    if resource_id is not None:
        stmt = stmt.where(AuditLog.resource_id == resource_id)
    stmt = stmt.order_by(AuditLog.id.desc()).limit(limit)
    return list(db.scalars(stmt).all())
