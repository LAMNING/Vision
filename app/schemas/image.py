from datetime import datetime

from pydantic import Field

from app.schemas.base import ContentHash, FileName, InputModel, ORMModel

__all__ = [
    'ImageCreate',
    'ImageRead',
    'ImageUsageRead',
    'ProjectImageRead',
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


class ProjectImageRead(ImageRead):
    '''项目「图像」列表项：图片元数据 + 标注/审核进度。

    「这张图标注过没有、审核过没有」只有把 Image 和它落在的任务
    （TaskImage）连起来才知道，所以由路由层一次聚合算好带下来，
    前端不用为每张图单独查一次。

    stage 是给列表标签用的合并态，取值见 crud.project_image.STAGES；
    annotated / review_status 两个布尔/枚举则直接对应界面上那两个小标签。
    '''

    stage: str = 'none'
    annotated: bool = False
    review_status: str = 'none'
    task_count: int = 0
    task_id: int | None = None
    task_name: str | None = None
    task_mode: str | None = None
    task_image_id: int | None = None
    can_annotate: bool = False
    can_review: bool = False
