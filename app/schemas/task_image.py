from datetime import datetime
from typing import Literal

from pydantic import Field, model_validator

from app.data.task_image import ANNOTATION_STATUSES, REVIEW_STATUSES
from app.schemas.base import InputModel, ORMModel
from app.schemas.image import ImageRead

__all__ = [
    'AnnotationStatus',
    'ReviewStatus',
    'TaskImageRead',
    'TaskImageReview',
]

AnnotationStatus = Literal[*ANNOTATION_STATUSES]
ReviewStatus = Literal[*REVIEW_STATUSES]


class TaskImageRead(ORMModel):
    id: int
    task_id: int
    image_id: int
    annotation_status: AnnotationStatus
    review_status: ReviewStatus
    revision: int
    reject_reason: str | None
    reviewed_by_account_id: int | None
    reviewed_at: datetime | None
    created_at: datetime
    annotated_at: datetime | None
    image: ImageRead | None = None


class TaskImageReview(InputModel):
    '''单张图片的复检结论，对应 crud.set_review。

    reject 必须给理由（crud 同样校验），理由上限 255 与列宽一致。
    '''

    approved: bool
    reason: str | None = Field(default=None, max_length=255)

    @model_validator(mode='after')
    def _reject_needs_reason(self) -> 'TaskImageReview':
        if not self.approved and not (self.reason or '').strip():
            raise ValueError('reject 必须提供 reject_reason')
        return self
