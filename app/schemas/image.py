from datetime import datetime

from pydantic import Field

from app.schemas.base import ContentHash, FileName, InputModel, ORMModel

__all__ = [
    'ImageCreate',
    'ImageRead',
    'ImageUsageRead',
]


class ImageCreate(InputModel):
    '''图片落库元数据。

    group_id 由路径给、uploader_account_id 由登录态给，都不进请求体；
    storage_key 由上传流程生成后回传（组内唯一，含软删行）。
    '''

    storage_key: str = Field(min_length=1, max_length=255)
    filename: FileName
    size_bytes: int = Field(ge=0)
    width: int | None = Field(default=None, gt=0)
    height: int | None = Field(default=None, gt=0)
    content_hash: ContentHash | None = None


class ImageRead(ORMModel):
    id: int
    group_id: int
    uploader_account_id: int
    storage_key: str
    filename: str
    content_hash: str | None
    width: int | None
    height: int | None
    size_bytes: int
    created_at: datetime


class ImageUsageRead(ORMModel):
    '''组额度用量：给上传前的余量提示用。

    crud 里 quota <= 0 视为不限量（app/data/crud/image.py:84-85）。
    '''

    image_count: int
    used_bytes: int
    quota_images: int
    quota_storage_mb: int
