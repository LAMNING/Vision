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
    'TaskListItem',
    'TaskDetailRead',
    'ImageTaskRef',
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
    # False = 标完直接通过，不进复核；True 走原来的逐图复核 + 任务级裁决
    review_required: bool = True

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
    review_required: bool = True
    created_at: datetime
    completed_at: datetime | None


class TaskListItem(TaskRead):
    '''任务列表项：额外带图片总数、已标注数与逐图复核计数。

    列表里要显示「已标注 5 / 12」这种进度，但每次打开列表都去拉详情太重，
    所以在列表响应里直接把计数带上（由路由层一次聚合查询算出）。
    「待复核」的任务还要显示「复核 2 / 4 张」，所以再带两个逐图复核计数。
    '''

    image_total: int = 0
    image_annotated: int = 0
    review_approved: int = 0
    review_rejected: int = 0


class TaskDetailRead(TaskRead):
    '''任务详情：带任务内的图片（含图片元数据）。'''

    images: list[TaskImageRead] = Field(default_factory=list)


class ImageTaskRef(ORMModel):
    '''「这张图在哪些任务里」。

    看图页要提供「在标注台打开」，得先知道这张图落在了哪个任务、对应哪条
    task_image、以及能不能改；一次查完，前端不用自己拼。
    '''

    task_id: int
    task_name: str | None
    task_status: TaskStatus
    task_mode: TaskMode
    task_image_id: int
    annotation_status: str
    review_status: str
    # 看图页的「在标注台打开 / 审核这张」直接看这两个布尔，不让前端自己拼权限
    can_annotate: bool = False
    can_review: bool = False


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

