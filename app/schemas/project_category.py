from datetime import datetime

from pydantic import Field

from app.schemas.base import (
    CategoryEnNameInput,
    CategoryZhName,
    InputModel,
    ORMModel,
    Threshold,
)

__all__ = [
    'CategoryCreate',
    'CategoryUpdate',
    'CategoryRead',
]


class CategoryCreate(InputModel):
    '''新增项目类目；en_name 即 LLMDet 的英文检测提示词。

    只填中文名也可以：保存时会用翻译模型把中文名翻成英文提示词。
    '''

    zh_name: CategoryZhName
    en_name: CategoryEnNameInput = ''
    threshold_override: Threshold | None = None
    sort_order: int = Field(default=0, ge=0)
    enabled: bool = True


class CategoryUpdate(InputModel):
    '''全部可选；未传的字段保持原值，threshold_override 传 null 表示取消覆盖。

    en_name 传空字符串表示「重新按当前中文名翻译」。
    '''

    zh_name: CategoryZhName | None = None
    en_name: CategoryEnNameInput | None = None
    threshold_override: Threshold | None = None
    sort_order: int | None = Field(default=None, ge=0)
    enabled: bool | None = None


class CategoryRead(ORMModel):
    id: int
    project_id: int
    zh_name: str
    en_name: str
    threshold_override: float | None
    sort_order: int
    enabled: bool
    created_at: datetime
