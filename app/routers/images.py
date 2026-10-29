import mimetypes
from typing import Annotated, Literal

from fastapi import APIRouter, File, Header, Response, UploadFile
from fastapi.responses import StreamingResponse

from app.data.crud import audit as audit_crud
from app.data.crud import group as group_crud
from app.data.crud import group_member as gm
from app.data.crud import image as image_crud
from app.data.crud import project_image as project_image_crud
from app.deps import CurrentAccount, DBSession
from app.errors import (
    GroupNotFoundError,
    ImageBlobMissingError,
    PermissionDeniedError,
)
from app.schemas.image import ImageRead, ImageUsageRead
from app.services import storage
from app.services.image_upload import (
    MAX_UPLOAD_BYTES,
    delete_variants,
    ingest_image,
    make_variant,
    variant_key,
)
from app.services.unifile import FileNotFound

router = APIRouter(tags=['images'])

UPLOADER_MANAGER_ROLES = ('owner', 'admin')

# content 接口的三种规格：原图 / 详情预览 / 列表缩略图。
ImageSize = Literal['full', 'thumb', 'preview']
_IMAGE_CACHE_CONTROL = 'private, max-age=604800, immutable'


def _require_member(db, group_id: int, account_id: int):
    membership = gm.get_membership(db, group_id, account_id)
    if membership is None:
        raise PermissionDeniedError(f'account {account_id} is not a member of group {group_id}')
    return membership


def _require_read_access(db, image, account_id: int) -> None:
    '''下载权限：同组成员，或该图所在项目的成员。'''
    membership = gm.get_membership(db, image.group_id, account_id)
    if membership is None and not project_image_crud.account_can_access_via_project(
        db, image.id, account_id
    ):
        raise PermissionDeniedError(
            f'account {account_id} is not a member of group {image.group_id}'
        )


@router.post('/groups/{group_id}/images', response_model=ImageRead, status_code=201)
def upload_image(
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
    file: UploadFile = File(...),
):
    '''上传一张图。

    - 非组成员 403（额度、去重、软删都按组算）
    - 同组内 sha256 相同直接复用已有记录（不重复存 blob）
    - 先写存储再落库；落库失败就把刚写的 blob 删掉
    '''
    _require_member(db, group_id, account.id)

    data = file.file.read()
    filename = file.filename or 'image'
    image, created = ingest_image(
        db,
        group_id=group_id,
        uploader_account_id=account.id,
        data=data,
        filename=filename,
        commit=True,
    )

    if created:
        audit_crud.record(
            db,
            action='image.uploaded',
            resource_type='image',
            resource_id=image.id,
            actor_account_id=account.id,
            group_id=group_id,
            after={
                'filename': filename,
                'size_bytes': len(data),
                'storage_key': image.storage_key,
            },
            commit=True,
        )
    return image


@router.get('/groups/{group_id}/images', response_model=list[ImageRead])
def list_images(
    group_id: int,
    account: CurrentAccount,
    db: DBSession,
    include_deleted: bool = False,
):
    _require_member(db, group_id, account.id)
    return image_crud.list_group_images(db, group_id, include_deleted=include_deleted)


@router.get('/groups/{group_id}/images/usage', response_model=ImageUsageRead)
def image_usage(group_id: int, account: CurrentAccount, db: DBSession):
    _require_member(db, group_id, account.id)
    group = group_crud.get_group(db, group_id)
    if group is None:
        raise GroupNotFoundError(group_id)
    count, used_bytes = image_crud.get_usage(db, group_id)
    return ImageUsageRead(
        image_count=count,
        used_bytes=used_bytes,
        quota_images=group.quota_images,
        quota_storage_mb=group.quota_storage_mb,
    )


def _etag_matches(if_none_match: str | None, etag: str) -> bool:
    if not if_none_match:
        return False
    raw = if_none_match.strip()
    if raw == '*':
        return True
    # 支持 "a", "b" 与 W/"a" 两种写法。
    for token in raw.split(','):
        candidate = token.strip()
        if candidate.startswith('W/'):
            candidate = candidate[2:]
        if candidate.strip('"') == etag.strip('"'):
            return True
    return False


def _ensure_variant(image, size: str) -> str:
    '''返回缩略图对象 key；老的图没有缩略图时按需生成并落存储。'''
    key = variant_key(image.storage_key, size)
    if storage.exists(key):
        return key
    try:
        original = storage.read_bytes(image.storage_key)
    except FileNotFound:
        raise ImageBlobMissingError(image.id) from None
    payload = make_variant(original, size)
    if payload is None:
        raise ImageBlobMissingError(image.id)
    storage.save_bytes(key, payload)
    return key


@router.get('/images/{image_id}/content')
def download_image(
    image_id: int,
    account: CurrentAccount,
    db: DBSession,
    size: ImageSize = 'full',
    if_none_match: Annotated[str | None, Header(alias='If-None-Match')] = None,
):
    '''取图片字节（流式），支持缩略图与 ETag/304。

    - `size=full`（默认）原图；`size=thumb` 320px 缩略图；`size=preview` 1280px 预览
    - 缩略图在老图缺失时按需生成并回写存储
    - 带 `If-None-Match` 且与 ETag 相同 → 304（浏览器缓存直接生效）
    '''
    image = image_crud.get_image_or_raise(db, image_id)
    _require_read_access(db, image, account.id)

    if size == 'full':
        key = image.storage_key
        media_type = mimetypes.guess_type(image.filename)[0] or 'application/octet-stream'
        etag = f'"{image.content_hash}"'
    else:
        key = _ensure_variant(image, size)
        media_type = 'image/jpeg'
        etag = f'"{image.content_hash}-{size}"'

    headers = {
        'ETag': etag,
        'Cache-Control': _IMAGE_CACHE_CONTROL,
        'Content-Disposition': 'inline',
        'Content-Type': media_type,
    }
    if _etag_matches(if_none_match, etag):
        return Response(status_code=304, headers=headers)

    info = storage.stat_info(key)
    if info is None:
        raise ImageBlobMissingError(image_id)
    try:
        stream = storage.open_binary(key)
    except FileNotFound:
        raise ImageBlobMissingError(image_id) from None
    headers['Content-Length'] = str(info.size)
    return StreamingResponse(
        storage.iter_chunks(stream),
        media_type=media_type,
        headers=headers,
    )


@router.delete('/images/{image_id}', status_code=204)
def delete_image(image_id: int, account: CurrentAccount, db: DBSession):
    '''软删记录并删掉存储对象；上传者或空间 owner/admin 才能删。'''
    image = image_crud.get_image_or_raise(db, image_id)
    membership = _require_member(db, image.group_id, account.id)

    if (
        image.uploader_account_id != account.id
        and membership.role not in UPLOADER_MANAGER_ROLES
    ):
        raise PermissionDeniedError('只有上传者或空间管理员可以删除图片')

    image_crud.delete_image(db, image_id)
    storage.delete(image.storage_key)
    delete_variants(image.storage_key)

    audit_crud.record(
        db,
        action='image.deleted',
        resource_type='image',
        resource_id=image_id,
        actor_account_id=account.id,
        group_id=image.group_id,
        before={'filename': image.filename, 'size_bytes': image.size_bytes},
        commit=True,
    )
    return Response(status_code=204)
