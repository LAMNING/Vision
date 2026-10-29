"""跨项目视图：当前账号的「我的任务」。"""

from typing import Annotated, Literal

from fastapi import APIRouter, Query
from sqlalchemy import select

from app.data import Project
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
    if tasks:
        project_ids = {task.project_id for task in tasks}
        rows = db.scalars(select(Project).where(Project.id.in_(project_ids))).all()
        names = {project.id: project.name for project in rows}
    out: list[MyTaskRead] = []
    for task in tasks:
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
                    'created_at',
                    'completed_at',
                )
            },
            'project_name': names.get(task.project_id),
        }
        out.append(MyTaskRead.model_validate(data))
    return out
