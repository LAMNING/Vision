from datetime import datetime
from typing import Literal

from pydantic import Field

from app.data.review import REVIEW_DECISIONS
from app.schemas.base import InputModel, ORMModel

__all__ = [
    'ReviewDecision',
    'ReviewCreate',
    'ReviewRead',
]

ReviewDecision = Literal[*REVIEW_DECISIONS]


class ReviewCreate(InputModel):
    '''提交复检结论。

    round / approved_count / rejected_count 由 crud 计算，客户端不传；
    approve 要求全部图片 approved、reject 要求至少一张 rejected（crud 校验）。
    '''

    decision: ReviewDecision
    comment: str | None = Field(default=None, max_length=2000)


class ReviewRead(ORMModel):
    id: int
    task_id: int
    reviewer_account_id: int
    round: int
    decision: ReviewDecision
    comment: str | None
    approved_count: int
    rejected_count: int
    created_at: datetime
