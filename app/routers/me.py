"""跨项目视图：当前账号的「我的任务」。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.data import Project, TaskImage
from app.data.crud import task as task_crud
from app.deps import CurrentAccount, DBSession
from app.schemas.task import MyTaskRead, TaskMode, TaskStatus

router = APIRouter(prefix='/me', tags=['me'])

MyTaskRole = Literal['assignee', 'submitter', 'reviewer', 'any']


@router.get('/tasks', response_model=list[MyTaskRead])
def list_my_tasks(
    account: CurrentAccount,
    db: DBSession,
    role: MyTaskRole = 'assignee',
    status: TaskStatus | None = None,
    mode: TaskMode | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
) -> list[MyTaskRead]:
    '''我指派给我的 / 我提交的 / 我负责复核的任务（默认看指派给我的）。'''
    tasks = task_crud.list_my_tasks(
        db, account.id, role=role, status=status, mode=mode, limit=limit
    )
    names: dict[int, str] = {}
    progress: dict[int, tuple[int, int, int, int]] = {}
    if tasks:
        project_ids = {task.project_id for task in tasks}
        rows = db.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        names = {project.id: project.name for project in rows}

        # 一次聚合把「共几张 / 已标几张 / 复核通过 / 被驳回」算出来，
        # 个人空间的任务行要显示进度，不能为每条任务再打一次请求。
        counts = db.execute(
            select(
                TaskImage.task_id,
                func.count(TaskImage.id),
                func.sum(case((TaskImage.annotation_status == 'annotated', 1), else_=0)),
                func.sum(case((TaskImage.review_status == 'approved', 1), else_=0)),
                func.sum(case((TaskImage.review_status == 'rejected', 1), else_=0)),
            )
            .where(TaskImage.task_id.in_([task.id for task in tasks]))
            .group_by(TaskImage.task_id)
        ).all()
        progress = {
            int(task_id): (int(total or 0), int(annotated or 0), int(passed or 0), int(rejected or 0))
            for task_id, total, annotated, passed, rejected in counts
        }

    out: list[MyTaskRead] = []
    for task in tasks:
        total, annotated, passed, rejected = progress.get(task.id, (0, 0, 0, 0))
        data = {
            **{
                key: getattr(task, key)
                for key in (
                    'id',
                    'project_id',
                    'name',
                    'status',
                    'mode',
                    'submitter_account_id',
                    'reviewer_account_id',
                    'assignee_account_id',
                    'priority',
                    'due_at',
                    'note',
                    'retry_count',
                    'error_message',
                    'version',
                    'review_required',
                    'created_at',
                    'started_at',
                    'completed_at',
                )
            },
            'project_name': names.get(task.project_id),
            'image_total': total,
            'image_annotated': annotated,
            'review_approved': passed,
            'review_rejected': rejected,
        }
        out.append(MyTaskRead.model_validate(data))
    return out
