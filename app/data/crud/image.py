from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.group import Group
from app.data.image import Image
from app.data.task import TERMINAL_TASK_STATUSES, Task
from app.data.task_image import TaskImage
from app.data.crud import group_member as gm
from app.errors import (
    ConflictError,
    GroupNotFoundError,
    ImageDeletedError,
    ImageNotFoundError,
    PermissionDeniedError,
    QuotaExceededError,
    ValidationError,
)
from app.utils.time import utcnow

BYTES_PER_MB = 1024 * 1024


def _get_group_or_raise(db: Session, group_id: int) -> Group:
    group = db.scalar(
        select(Group).where(Group.id == group_id, Group.deleted_at.is_(None))
    )
    if not group:
        raise GroupNotFoundError(group_id)
    return group


def get_image(
    db: Session, image_id: int, *, include_deleted: bool = False
) -> Image | None:
    stmt = select(Image).where(Image.id == image_id)
    if not include_deleted:
        stmt = stmt.where(Image.deleted_at.is_(None))
    return db.scalar(stmt)


def get_image_or_raise(
    db: Session, image_id: int, *, include_deleted: bool = False
) -> Image:
    image = get_image(db, image_id, include_deleted=include_deleted)
    if image is None:
        raise ImageNotFoundError(image_id)
    return image


def list_group_images(
    db: Session, group_id: int, *, include_deleted: bool = False
) -> list[Image]:
    stmt = select(Image).where(Image.group_id == group_id).order_by(Image.id)
    if not include_deleted:
        stmt = stmt.where(Image.deleted_at.is_(None))
    return list(db.scalars(stmt).all())


def find_by_hash(
    db: Session, group_id: int, content_hash: str
) -> list[Image]:
    stmt = (
        select(Image)
        .where(
            Image.group_id == group_id,
            Image.content_hash == content_hash,
            Image.deleted_at.is_(None),
        )
        .order_by(Image.id)
    )
    return list(db.scalars(stmt).all())


def get_usage(db: Session, group_id: int) -> tuple[int, int]:
    stmt = select(
        func.count(Image.id),
        func.coalesce(func.sum(Image.size_bytes), 0),
    ).where(Image.group_id == group_id, Image.deleted_at.is_(None))
    count, total = db.execute(stmt).one()
    return int(count), int(total)


def _assert_image_quota(group: Group, used: int, cost: int) -> None:
    # quota <= 0 视为不限量（平台/内部组）
    if group.quota_images and group.quota_images > 0:
        if used + cost > group.quota_images:
            raise QuotaExceededError('images', group.quota_images, used + cost)


def _assert_storage_quota(group: Group, used_bytes: int, cost_bytes: int) -> None:
    if group.quota_storage_mb and group.quota_storage_mb > 0:
        limit = group.quota_storage_mb * BYTES_PER_MB
        if used_bytes + cost_bytes > limit:
            raise QuotaExceededError('storage_bytes', limit, used_bytes + cost_bytes)


def create_image(
    db: Session,
    *,
    group_id: int,
    uploader_account_id: int,
    storage_key: str,
    filename: str,
    size_bytes: int,
    width: int | None = None,
    height: int | None = None,
    content_hash: str | None = None,
    require_group_member: bool = True,
    commit: bool = True,
) -> Image:
    group = _get_group_or_raise(db, group_id)

    if require_group_member and gm.get_membership(
        db, group_id, uploader_account_id
    ) is None:
        raise PermissionDeniedError(
            f'account {uploader_account_id} is not a member of group {group_id}'
        )
    if not storage_key:
        raise ValidationError('storage_key 不能为空')
    if not filename:
        raise ValidationError('filename 不能为空')
    if size_bytes is None or size_bytes < 0:
        raise ValidationError('size_bytes 必须是非负整数')

    # storage_key 在组内唯一（含软删行：key 被占用就不能再写同一个 blob）
    dup = db.scalar(
        select(Image).where(
            Image.group_id == group_id, Image.storage_key == storage_key
        )
    )
    if dup is not None:
        raise ConflictError(
            f'storage_key {storage_key!r} already used by image {dup.id} in group {group_id}'
        )

    used_images, used_bytes = get_usage(db, group_id)
    _assert_image_quota(group, used_images, 1)
    _assert_storage_quota(group, used_bytes, size_bytes)

    image = Image(
        group_id=group_id,
        uploader_account_id=uploader_account_id,
        storage_key=storage_key,
        filename=filename,
        content_hash=content_hash,
        width=width,
        height=height,
        size_bytes=size_bytes,
    )
    db.add(image)
    if commit:
        db.commit()
        db.refresh(image)
    else:
        db.flush()
    return image


def _active_task_refs(db: Session, image_id: int) -> list[int]:
    stmt = (
        select(TaskImage.task_id)
        .join(Task, Task.id == TaskImage.task_id)
        .where(
            TaskImage.image_id == image_id,
            Task.deleted_at.is_(None),
            Task.status.notin_(TERMINAL_TASK_STATUSES),
        )
    )
    return list(db.scalars(stmt).all())


def delete_image(db: Session, image_id: int, *, commit: bool = True) -> Image:
    image = get_image_or_raise(db, image_id)
    if image.deleted_at is not None:
        raise ImageDeletedError(image_id)

    refs = _active_task_refs(db, image_id)
    if refs:
        raise ConflictError(
            f'image {image_id} is referenced by active task(s) {refs}; '
            'cancel or complete them first'
        )

    image.deleted_at = utcnow()
    if commit:
        db.commit()
        db.refresh(image)
    else:
        db.flush()
    return image
