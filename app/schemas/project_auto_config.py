from datetime import datetime
from typing import Literal

from pydantic import Field

from app.data.project_auto_config import TRANSLATE_STYLES
from app.schemas.base import InputModel, ORMModel, Threshold

__all__ = [
    'TranslateStyle',
    'AutoConfigUpdate',
    'AutoConfigRead',
]

TranslateStyle = Literal[*TRANSLATE_STYLES]


class AutoConfigUpdate(InputModel):
    '''自动标注参数（对应 LLMDet.predict 的 threshold/text_threshold/top_k）。

    全部可选；未传的字段保持原值。
    '''

    auto_annotate_enabled: bool | None = None
    box_threshold: Threshold | None = None
    text_threshold: Threshold | None = None
    top_k: int | None = Field(default=None, ge=1)
    translate_enabled: bool | None = None
    translate_style: TranslateStyle | None = None


class AutoConfigRead(ORMModel):
    project_id: int
    auto_annotate_enabled: bool
    box_threshold: float
    text_threshold: float
    top_k: int | None
    translate_enabled: bool
    translate_style: TranslateStyle
    created_at: datetime
    updated_at: datetime
