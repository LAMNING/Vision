'''图片入库的共享流程：校验字节、按组去重、写 unifile、落库。

`app/routers/images.py`（组内上传）和 `app/routers/projects.py`（项目批量上传）
都走这里，保证去重、额度、软删语义一致。
'''

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from io import BytesIO

from PIL import Image as PILImage
from sqlalchemy.orm import Session

from app.data.crud import image as image_crud
from app.data.image import Image
from app.errors import AppError, ValidationError
from app.services import storage

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 单张图上限 50MB

# 缩略图规格：thumb 用于列表网格，preview 用于详情/画框（前端拿到就能直接画）。
THUMB_MAX_SIDE = 320
PREVIEW_MAX_SIDE = 1280
VARIANT_QUALITY = 82
VARIANT_SIZES = ('thumb', 'preview')
_VARIANT_MAX = {'thumb': THUMB_MAX_SIDE, 'preview': PREVIEW_MAX_SIDE}


def image_size(data: bytes) -> tuple[int | None, int | None]:
    '''宽高只作展示/排版用，读不出来就存 None，不挡上传。'''
    try:
        with PILImage.open(BytesIO(data)) as img:
            return img.size
    except Exception:
        return None, None


def validate_upload_size(data: bytes) -> None:
    if not data:
        raise ValidationError('文件为空')
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValidationError(
            f'文件超过 {MAX_UPLOAD_BYTES // (1024 * 1024)} MB 上限'
        )


def variant_key(key: str, size: str) -> str:
    '''缩略图对象路径：原图 key 加后缀（同目录，删除时一起清）。'''
    if size not in _VARIANT_MAX:
        raise ValueError(f'未知的缩略图规格：{size}')
    return f'{key}.{size}.jpg'


def make_variant(data: bytes, size: str) -> bytes | None:
    '''生成缩略图；不是图片/解码失败返回 None（不挡上传）。'''
    try:
        with PILImage.open(BytesIO(data)) as img:
            rgb = img.convert('RGB')
            rgb.thumbnail((_VARIANT_MAX[size], _VARIANT_MAX[size]))
            buf = BytesIO()
            rgb.save(buf, format='JPEG', quality=VARIANT_QUALITY, optimize=True)
            return buf.getvalue()
    except Exception:
        return None


def generate_variants(key: str, data: bytes) -> dict[str, int]:
    '''上传后生成 thumb/preview 两档缩略图，返回 {规格: 字节数}。'''
    saved: dict[str, int] = {}
    for size in VARIANT_SIZES:
        payload = make_variant(data, size)
        if payload is None:
            continue
        try:
            storage.save_bytes(variant_key(key, size), payload)
            saved[size] = len(payload)
        except Exception:  # noqa: BLE001 —— 缩略图失败不影响原图
            logger.warning('缩略图写入失败 key=%s size=%s', key, size, exc_info=True)
    return saved


def delete_variants(key: str) -> None:
    for size in VARIANT_SIZES:
        storage.delete(variant_key(key, size))


@dataclass(frozen=True)
class PreparedImage:
    '''已落盘、还没写库的一张图。

    ``existing`` 非空表示命中去重（同组已有同 sha256 的图），此时
    ``storage_key`` 为 None——没有写新 blob，回滚时也不该删别人的文件。
    '''

    filename: str
    size_bytes: int
    content_hash: str
    width: int | None
    height: int | None
    storage_key: str | None
    existing: Image | None

    @property
    def deduplicated(self) -> bool:
        return self.existing is not None


def prepare_image(
    db: Session, *, group_id: int, data: bytes, filename: str
) -> PreparedImage:
    '''校验 + 去重查询 + 写存储，全程不写数据库。

    这一半只有读和磁盘 I/O，可以安全地放在数据库事务外面；批量上传先对整批
    调用它，再在最后用一个短事务统一落库（见 app/routers/projects.py）。
    '''
    validate_upload_size(data)

    digest = hashlib.sha256(data).hexdigest()
    duplicates = image_crud.find_by_hash(db, group_id, digest)
    if duplicates:
        return PreparedImage(
            filename=filename,
            size_bytes=len(data),
            content_hash=digest,
            width=None,
            height=None,
            storage_key=None,
            existing=duplicates[0],
        )

    width, height = image_size(data)
    key = storage.build_key(group_id, filename)
    storage.save_bytes(key, data)
    generate_variants(key, data)
    return PreparedImage(
        filename=filename,
        size_bytes=len(data),
        content_hash=digest,
        width=width,
        height=height,
        storage_key=key,
        existing=None,
    )


def discard_prepared(prepared: PreparedImage) -> None:
    '''回滚/失败时清掉 `prepare_image` 刚写进存储的 blob（含缩略图）。'''
    if prepared.storage_key is None:
        return
    storage.delete(prepared.storage_key)
    delete_variants(prepared.storage_key)


def persist_image(
    db: Session,
    prepared: PreparedImage,
    *,
    group_id: int,
    uploader_account_id: int,
    require_group_member: bool = True,
    commit: bool = True,
) -> tuple[Image, bool]:
    '''把 `prepare_image` 的结果落库，返回 `(image, 是否新建)`。'''
    if prepared.existing is not None:
        return prepared.existing, False

    # 同一批里可能有字节完全相同的图：`prepare_image` 阶段还没写库，查不到彼此；
    # 这里在落库前再查一次（前一张此时已经 flush 进本事务，session 内可见），
    # 命中就删掉刚写的 blob、复用已有记录——和旧的「逐张 instant 上传」语义一致。
    duplicates = image_crud.find_by_hash(db, group_id, prepared.content_hash)
    if duplicates:
        discard_prepared(prepared)
        return duplicates[0], False

    try:
        image = image_crud.create_image(
            db,
            group_id=group_id,
            uploader_account_id=uploader_account_id,
            storage_key=prepared.storage_key,
            filename=prepared.filename,
            size_bytes=prepared.size_bytes,
            width=prepared.width,
            height=prepared.height,
            content_hash=prepared.content_hash,
            require_group_member=require_group_member,
            commit=commit,
        )
    except AppError:
        discard_prepared(prepared)
        raise
    return image, True


def ingest_image(
    db: Session,
    *,
    group_id: int,
    uploader_account_id: int,
    data: bytes,
    filename: str,
    require_group_member: bool = True,
    commit: bool = True,
) -> tuple[Image, bool]:
    '''把一批字节变成 `Image` 记录。

    - 同组内 sha256 相同直接复用已有记录（不重复存 blob），返回 `(image, False)`
    - 新建时先写存储（原图 + thumb/preview 缩略图）再落库；落库失败就把刚写的 blob 删掉
    - `commit=False` 时只 flush，交给调用方控制事务（批量上传用）
    - `require_group_member=False` 给「项目成员但不是组成员」的上传路径用

    批量上传（app/routers/projects.py）请改用下面拆开的 `prepare_image` +
    `persist_image`：写存储/生成缩略图是慢 I/O，不能和数据库事务挤在一起，
    否则 SQLite 写锁会被长时间占住。
    '''
    prepared = prepare_image(db, group_id=group_id, data=data, filename=filename)
    return persist_image(
        db,
        prepared,
        group_id=group_id,
        uploader_account_id=uploader_account_id,
        require_group_member=require_group_member,
        commit=commit,
    )
