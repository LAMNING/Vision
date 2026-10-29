from datetime import datetime
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from app.data.task import TASK_MODES, TASK_STATUSES
from app.schemas.base import InputModel, ORMModel
from app.schemas.task_image import TaskImageRead

__all__ = [
    'TaskStatus',
    'TaskMode',
    'TaskName',
    'TaskCreate',
    'TaskUpdate',
    'TaskRead',
    'TaskDetailRead',
    'MyTaskRead',
    'TaskImageAttach',
    'TaskFailRequest',
]

TaskStatus = Literal[*TASK_STATUSES]
TaskMode = Literal[*TASK_MODES]


def _dedupe(ids: list[int]) -> list[int]:
    '''去重且保序——与 crud.attach_images 里的 dict.fromkeys 保持一致。'''
    return list(dict.fromkeys(ids))

TaskName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)
]


class TaskCreate(InputModel):
    '''创建任务。

    image_ids 会由 crud 去重保序；跨组用图会被 ImageCrossGroupError 拒绝。
    status 不由客户端指定（一律从 draft 开始），后续迁移走 /tasks/{id}/submit 等端点。
    mode=manual 时提交后不交给 worker，由标注员在标注工作台里画框。
    '''

    project_id: int = Field(gt=0)
    name: TaskName | None = None
    # 不传时由 crud.resolve_reviewer 按项目 default_reviewer 推断
    reviewer_account_id: int | None = Field(default=None, gt=0)
    image_ids: list[int] = Field(default_factory=list)
    mode: TaskMode = 'auto'
    # 指派给项目里的 annotator/manager；提交 / 改派时会发站内通知
    assignee_account_id: int | None = Field(default=None, gt=0)
    priority: int = Field(default=0, ge=0, le=2)
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)

    @field_validator('image_ids')
    @classmethod
    def _dedupe_image_ids(cls, ids: list[int]) -> list[int]:
        return _dedupe(ids)


class TaskUpdate(InputModel):
    '''PATCH 任务协作字段：只更新传了的键；显式 null 表示清空。'''

    name: TaskName | None = None
    assignee_account_id: int | None = Field(default=None, gt=0)
    priority: int | None = Field(default=None, ge=0, le=2)
    due_at: datetime | None = None
    note: str | None = Field(default=None, max_length=500)


class TaskRead(ORMModel):
    id: int
    project_id: int
    name: str | None
    status: TaskStatus
    mode: TaskMode
    submitter_account_id: int
    reviewer_account_id: int | None
    assignee_account_id: int | None
    priority: int
    due_at: datetime | None
    note: str | None
    retry_count: int
    error_message: str | None
    version: int
    created_at: datetime
    completed_at: datetime | None


class TaskDetailRead(TaskRead):
    '''任务详情：带任务内的图片（含图片元数据）。'''

    images: list[TaskImageRead] = Field(default_factory=list)


class MyTaskRead(TaskRead):
    '''跨项目的「我的任务」：附带项目名，方便列表展示。'''

    project_name: str | None = None


class TaskImageAttach(InputModel):
    '''给已存在的任务追加图片。'''

    image_ids: list[int] = Field(min_length=1)

    @field_validator('image_ids')
    @classmethod
    def _dedupe_image_ids(cls, ids: list[int]) -> list[int]:
        return _dedupe(ids)


class TaskFailRequest(InputModel):
    '''auto_annotating -> failed 时携带的失败原因。'''

    error_message: str = Field(min_length=1, max_length=2000)

