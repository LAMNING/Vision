from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.data.annotation import ANNOTATION_SOURCES, ANNOTATION_TYPES
from app.schemas.base import InputModel, Label, ORMModel

__all__ = [
    'AnnotationType',
    'AnnotationSource',
    'AnnotationItem',
    'AnnotationCreate',
    'AnnotationReplaceRequest',
    'AnnotationRead',
    'AnnotationListItem',
]

AnnotationType = Literal[*ANNOTATION_TYPES]
AnnotationSource = Literal[*ANNOTATION_SOURCES]


class AnnotationItem(InputModel):
    '''单条标注。

    payload 的结构随 annotation_type 而定（bbox / polygon / keypoint /
    classification），这里只保证是 dict，具体字段由算法侧与前端约定。
    '''

    label: Label
    category_id: int | None = None
    annotation_type: AnnotationType
    payload: dict[str, Any]
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class AnnotationCreate(AnnotationItem):
    '''对应 crud.add_current(item, source=...)：保留当前标注，追加一条。'''

    source: AnnotationSource = 'manual'


class AnnotationReplaceRequest(InputModel):
    '''对应 crud.replace_current(items, source=...)：整轮覆盖。

    crud 会先把该图已有的 is_current 置 False，再按 revision 写入新行。
    '''

    items: list[AnnotationItem]
    source: AnnotationSource = 'manual'


class AnnotationRead(ORMModel):
    id: int
    task_image_id: int
    category_id: int | None
    label: str
    annotation_type: AnnotationType
    payload: dict[str, Any]
    confidence: float | None
    source: AnnotationSource
    revision: int
    is_current: bool
    created_by_account_id: int | None
    created_at: datetime


class AnnotationListItem(AnnotationRead):
    '''列表项：把任务/图片信息一并带出来，前端不用再逐个查。'''

    task_id: int | None = None
    image_id: int | None = None
    image_filename: str | None = None
