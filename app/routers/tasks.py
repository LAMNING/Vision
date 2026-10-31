'''项目任务：创建、列表、详情、加图、提交（进自动标注队列）、重投、取消。'''

from typing import Annotated

from fastapi import APIRouter, Query, status
from sqlalchemy.orm import Session

from app.data import Account, Task
from app.data.crud import annotation as annotation_crud
from app.data.crud import audit as audit_crud
from app.data.crud import project_category as category_crud
from app.data.crud import review as review_crud
from app.data.crud import task as task_crud
from app.data.crud import task_image as task_image_crud
from app.data.crud.project_access import (
    ProjectAccess,
    can_edit_task,
    require_view,
)
from app.data.task_image import TaskImage
from app.deps import CurrentAccount, DBSession
from app.errors import (
    PermissionDeniedError,
    TaskNotFoundError,
    ValidationError,
)
from app.schemas.annotation import AnnotationReplaceRequest
from app.schemas.review import ReviewCreate, ReviewRead
from app.schemas.task import (
    TaskCreate,
    TaskDetailRead,
    TaskImageAttach,
    TaskListItem,
    TaskMode,
    TaskRead,
    TaskStatus,
    TaskUpdate,
)
from app.schemas.task_image import TaskImageRead, TaskImageReview

router = APIRouter(prefix='/projects/{project_id}/tasks', tags=['tasks'])


# ---- 辅助 ----


def _task_in_project(db: Session, project_id: int, task_id: int) -> Task:
    '''任务必须属于路径里的项目，否则按「任务不存在」处理。'''
    task = task_crud.get_task_or_raise(db, task_id)
    if task.project_id != project_id:
        raise TaskNotFoundError(task_id)
    return task


def _require_operable(
    db: Session, access: ProjectAccess, account: Account, task: Task
) -> None:
    '''任务的提交者或项目管理者才能操作任务。'''
    if access.can_manage or task.submitter_account_id == account.id:
        return
    raise PermissionDeniedError('只有任务提交者或项目 manager 可以操作这个任务')


def _require_can_create(access: ProjectAccess) -> None:
    if access.can_manage or 'annotator' in access.roles:
        return
    raise PermissionDeniedError('只有项目 annotator/manager 可以创建任务')


def _can_edit_annotations(
    access: ProjectAccess, account: Account, task: Task
) -> bool:
    '''人工标注/返修：管理者、提交者、被指派人、项目 annotator 都可以改。

    口径住在 ``project_access.can_edit_task``——「图像」列表与看图页也要用它，
    三处各写一遍迟早会飘。
    '''
    return can_edit_task(
        access,
        account.id,
        submitter_id=task.submitter_account_id,
        assignee_id=task.assignee_account_id,
    )


def _require_enabled_categories(db: Session, project_id: int) -> None:
    if not category_crud.list_categories(db, project_id):
        raise ValidationError('项目还没有启用的类目，无法自动标注')


def _require_reviewable(db: Session, account: Account, task: Task) -> None:
    '''复核：任务必须处于 auto_annotated，且当前账号是项目的 reviewer/manager。'''
    if task.status != 'auto_annotated':
        raise ValidationError('只有 auto_annotated 状态的任务可以复核')
    review_crud.require_reviewer_allowed(db, task, account.id)


def _load_task_image(db: Session, task: Task, task_image_id: int) -> TaskImage:
    row = task_image_crud.get_by_id(db, task_image_id)
    if row is None or row.task_id != task.id:
        raise TaskNotFoundError(task_image_id)
    return row


# ---- 任务 ----


@router.get('', response_model=list[TaskListItem])
def list_project_tasks(
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
    task_status: Annotated[TaskStatus | None, Query(alias='status')] = None,
    mode: TaskMode | None = None,
    assignee_account_id: Annotated[int | None, Query(gt=0)] = None,
    assigned_to_me: bool = False,
) -> list[TaskListItem]:
    '''项目里的任务列表；可按状态 / 类型 / 指派人过滤。

    顺带带回每个任务的图片总数与已标注数，前端列表据此直接画进度，
    不必为每一行再拉一次详情。
    '''
    require_view(db, account, project_id)
    if assigned_to_me:
        assignee_account_id = account.id
    tasks = task_crud.list_project_tasks(
        db,
        project_id,
        status=task_status,
        mode=mode,
        assignee_account_id=assignee_account_id,
    )
    progress = task_image_crud.progress_by_task(db, [task.id for task in tasks])
    reviewed = task_image_crud.review_progress_by_task(
        db, [task.id for task in tasks]
    )
    items: list[TaskListItem] = []
    for task in tasks:
        total, done = progress.get(task.id, (0, 0))
        approved, rejected = reviewed.get(task.id, (0, 0))
        items.append(
            TaskListItem.model_validate(task).model_copy(
                update={
                    'image_total': total,
                    'image_annotated': done,
                    'review_approved': approved,
                    'review_rejected': rejected,
                }
            )
        )
    return items


@router.post('', response_model=TaskRead, status_code=status.HTTP_201_CREATED)
def create_task(
    payload: TaskCreate,
    project_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> Task:
    '''创建 draft 任务；之后用 /submit 提交进自动标注队列。'''
    access = require_view(db, account, project_id)
    if payload.project_id != project_id:
        raise ValidationError('project_id 与路径不一致')
    _require_can_create(access)

    return task_crud.create_task(
        db,
        project_id=project_id,
        submitter_account_id=account.id,
        name=payload.name,
        reviewer_account_id=payload.reviewer_account_id,
        image_ids=payload.image_ids,
        mode=payload.mode,
        assignee_account_id=payload.assignee_account_id,
        priority=payload.priority,
        due_at=payload.due_at,
        note=payload.note,
        review_required=payload.review_required,
    )


@router.get('/{task_id}', response_model=TaskDetailRead)
def get_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> TaskDetailRead:
    require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    images = [
        TaskImageRead.model_validate(row)
        for row in task_image_crud.list_task_images(db, task.id)
    ]
    return TaskDetailRead(
        **TaskRead.model_validate(task).model_dump(), images=images
    )


@router.post(
    '/{task_id}/images',
    response_model=list[TaskImageRead],
    status_code=status.HTTP_201_CREATED,
)
def attach_task_images(
    payload: TaskImageAttach,
    project_id: int,
    task_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> list[TaskImageRead]:
    '''给 draft 任务追加图片（crud 会去重；跨组用图会 400）。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    if task.status != 'draft':
        raise ValidationError('只有 draft 状态的任务可以加图片')

    task_crud.attach_images(db, task, payload.image_ids)
    db.commit()
    return [
        TaskImageRead.model_validate(row)
        for row in task_image_crud.list_task_images(db, task.id)
    ]


@router.post('/{task_id}/submit', response_model=TaskRead)
def submit_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''draft -> queued：交给后台 worker 自动标注。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)

    if not task_image_crud.list_task_images(db, task.id):
        raise ValidationError('任务没有图片，不能提交')
    _require_enabled_categories(db, project_id)

    return task_crud.submit_task(db, task, actor_account_id=account.id)


@router.post('/{task_id}/retry', response_model=TaskRead)
def retry_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''failed -> queued：重投自动标注（次数上限 MAX_RETRY=3）。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    return task_crud.requeue_failed(db, task, actor_account_id=account.id)


@router.post('/{task_id}/cancel', response_model=TaskRead)
def cancel_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''非终态任务可取消（终态任务再取消会 409）。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    return task_crud.cancel_task(db, task, actor_account_id=account.id)


@router.patch('/{task_id}', response_model=TaskRead)
def update_task_meta(
    payload: TaskUpdate,
    project_id: int,
    task_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> Task:
    '''编辑任务协作字段：名称 / 指派人 / 优先级 / 截止时间 / 备注。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    return task_crud.update_task_meta(
        db,
        task,
        actor_account_id=account.id,
        **payload.model_dump(exclude_unset=True),
    )


@router.post('/{task_id}/complete', response_model=TaskRead)
def complete_manual_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''manual 任务：所有图都标注完后 queued -> auto_annotated（进入复核）。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    if not _can_edit_annotations(access, account, task):
        raise PermissionDeniedError(
            '只有任务提交者、指派人或项目 annotator/manager 可以完成标注'
        )
    return task_crud.complete_manual_task(
        db, task, actor_account_id=account.id
    )


# ---- 复核（审核流程）----


@router.get('/{task_id}/reviews', response_model=list[ReviewRead])
def list_task_reviews(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> list[ReviewRead]:
    '''任务的全部复核记录（按轮次）。项目成员可见。'''
    require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    return [
        ReviewRead.model_validate(row)
        for row in review_crud.list_reviews(db, task.id)
    ]


@router.post('/{task_id}/review', response_model=ReviewRead)
def submit_task_review(
    payload: ReviewCreate,
    project_id: int,
    task_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> ReviewRead:
    '''任务级裁决：approve / reject_fix / reject_reannotate。

    前置：每张图先逐图复核（approve 要求全部通过，reject 要求至少一张退回）。
    '''
    require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_reviewable(db, account, task)
    row = review_crud.submit_review(
        db,
        task,
        reviewer_account_id=account.id,
        decision=payload.decision,
        comment=payload.comment,
    )
    return ReviewRead.model_validate(row)


@router.post(
    '/{task_id}/images/{task_image_id}/review', response_model=TaskImageRead
)
def review_task_image(
    payload: TaskImageReview,
    project_id: int,
    task_id: int,
    task_image_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> TaskImageRead:
    '''逐图复核：approved=true 通过；false 必须给 reason（退回返修）。'''
    require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_reviewable(db, account, task)

    row = _load_task_image(db, task, task_image_id)
    if row.annotation_status != 'annotated' and payload.approved:
        raise ValidationError('这张图还没有标注完成，不能通过')

    task_image_crud.set_review(
        db,
        row,
        approved=payload.approved,
        reason=payload.reason,
        reviewer_account_id=account.id,
        commit=False,
    )
    audit_crud.record(
        db,
        action='task_image.reviewed',
        resource_type='task_image',
        resource_id=row.id,
        actor_account_id=account.id,
        group_id=task.project.group_id if task.project is not None else None,
        after={
            'review_status': row.review_status,
            'reject_reason': row.reject_reason,
            'task_id': task.id,
        },
    )
    # 逐图都通过就顺手收口：图像里已经审完，任务不该还挂在「待复核」。
    # 只要还有一张没通过（未审核 / 已退回 / 未标注），这里就返回 None，等人工裁决。
    finished = review_crud.finalize_approved(
        db,
        task,
        actor_account_id=account.id,
        comment='逐图复核全部通过',
        commit=False,
    )

    db.commit()
    db.refresh(row)
    if finished is not None:
        review_crud.notify_outcome(db, task, 'approve')
    return TaskImageRead.model_validate(row)


@router.put(
    '/{task_id}/images/{task_image_id}/annotations', response_model=TaskImageRead
)
def replace_image_annotations(
    payload: AnnotationReplaceRequest,
    project_id: int,
    task_id: int,
    task_image_id: int,
    account: CurrentAccount,
    db: DBSession,
) -> TaskImageRead:
    '''人工修正 / 人工标注：整轮覆盖某张图的标注。

    manual 任务的标注员在 queued 状态画框；复核退回的任务在
    rejected_fix / rejected_reannotate 状态返修。
    '''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    if not _can_edit_annotations(access, account, task):
        raise PermissionDeniedError(
            '只有任务提交者、指派人或项目 annotator/manager 可以编辑标注'
        )
    manual_editing = task.mode == 'manual' and task.status == 'queued'
    if not manual_editing and task.status not in (
        'rejected_fix',
        'rejected_reannotate',
    ):
        raise ValidationError(
            '只有 manual 任务（queued）或 rejected_fix / rejected_reannotate '
            '状态的任务可以编辑标注'
        )

    row = _load_task_image(db, task, task_image_id)
    items = [item.model_dump() for item in payload.items]
    annotation_crud.replace_current(
        db,
        row,
        items,
        source=payload.source,
        created_by_account_id=account.id,
        commit=False,
    )
    task_image_crud.mark_annotated(db, row, commit=False)
    audit_crud.record(
        db,
        action='annotation.manually_replaced',
        resource_type='task_image',
        resource_id=row.id,
        actor_account_id=account.id,
        group_id=task.project.group_id if task.project is not None else None,
        after={'task_id': task.id, 'count': len(items)},
    )
    db.commit()
    db.refresh(row)
    return TaskImageRead.model_validate(row)


@router.post('/{task_id}/requeue', response_model=TaskRead)
def requeue_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''rejected_reannotate -> queued：退回重标后重新排队给 worker。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    if task.status != 'rejected_reannotate':
        raise ValidationError('只有 rejected_reannotate 状态的任务可以重新排队')
    return task_crud.transition_task(
        db, task, 'queued', actor_account_id=account.id
    )


@router.post('/{task_id}/resubmit', response_model=TaskRead)
def resubmit_task(
    project_id: int, task_id: int, account: CurrentAccount, db: DBSession
) -> Task:
    '''rejected_fix -> auto_annotated：退回返修后重新提交复检。'''
    access = require_view(db, account, project_id)
    task = _task_in_project(db, project_id, task_id)
    _require_operable(db, access, account, task)
    if task.status != 'rejected_fix':
        raise ValidationError('只有 rejected_fix 状态的任务可以重新提交')
    return task_crud.resubmit_after_fix(db, task, actor_account_id=account.id)
