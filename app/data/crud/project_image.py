from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.image import Image
from app.data.project_image import ProjectImage
from app.data.project_member import ProjectMember
from app.data.task import Task
from app.data.task_image import TaskImage

# 「图像」列表的合并状态。pending 既包括还没进任务的图，也包括进了任务还没画的图，
# 前端用 task_count 区分「未加入任务」和「待标注」。
STAGES = ('none', 'pending', 'annotated', 'approved', 'rejected', 'failed')

# 一张图可能落在多个任务里（比如重标），列表标签取「最该处理」的那条。
_BUCKET_PRIORITY = {
    'editable': 6,
    'reviewable': 5,
    'rejected': 4,
    'approved': 3,
    'annotated': 2,
    'pending': 1,
    'failed': 0,
}

# 这些任务状态下的图可以在标注工作台里画 / 改
_EDITABLE_TASK_STATUSES = ('rejected_fix', 'rejected_reannotate')

_REVIEW_RANK = {'approved': 2, 'rejected': 1, 'pending': 0}


def _bucket(task: Task, row: TaskImage) -> str:
    '''把「任务状态 + 这张图的标注/审核状态」归成一个内部桶。'''
    editable = task.status in _EDITABLE_TASK_STATUSES or (
        task.status == 'queued' and task.mode == 'manual'
    )
    if editable:
        return 'editable'
    if row.review_status == 'approved':
        return 'approved'
    # 已经被退回的图优先显示「已退回」：任务还没裁决时它也是退回状态，
    # 让列表先告诉用户「这张被打回来了」，复审入口留在任务里。
    if row.review_status == 'rejected':
        return 'rejected'
    if task.status == 'auto_annotated' and row.annotation_status == 'annotated':
        return 'reviewable'
    if row.annotation_status == 'annotated':
        return 'annotated'
    if row.annotation_status == 'failed':
        return 'failed'
    return 'pending'


def image_progress(db: Session, project_id: int) -> dict[int, dict]:
    '''项目里每张图的标注/审核进度。

    一次 join 把 Image→TaskImage→Task 全查出来，在 Python 里按 image_id 归并：
    同一张图可能同时出现在多个任务里，取优先级最高的那条作为「可操作目标」。
    '''
    stmt = (
        select(TaskImage, Task)
        .join(Task, Task.id == TaskImage.task_id)
        .where(Task.project_id == project_id, Task.deleted_at.is_(None))
        .order_by(Task.id.desc())
    )

    progress: dict[int, dict] = {}
    for row, task in db.execute(stmt).all():
        image_id = int(row.image_id)
        bucket = _bucket(task, row)
        item = progress.get(image_id)
        if item is None:
            item = {
                'stage': 'none',
                'annotated': False,
                'review_status': 'none',
                'task_count': 0,
                'task_id': None,
                'task_name': None,
                'task_mode': None,
                'task_image_id': None,
                'can_annotate': False,
                'can_review': False,
                # 逐任务的提交人 / 被指派人：列表层要用它复刻 _can_edit_annotations，
                # 否则只读成员也会看到「标注」按钮，点进去被 403 挡回来
                'task_submitter_id': None,
                'task_assignee_id': None,
                '_priority': -1,
            }
            progress[image_id] = item

        item['task_count'] += 1
        if row.annotation_status == 'annotated':
            item['annotated'] = True
        if _REVIEW_RANK.get(row.review_status, 0) > _REVIEW_RANK.get(
            item['review_status'], -1
        ):
            item['review_status'] = row.review_status

        if _BUCKET_PRIORITY[bucket] > item['_priority']:
            item['_priority'] = _BUCKET_PRIORITY[bucket]
            item['stage'] = {
                'editable': 'pending',
                'reviewable': 'annotated',
                'rejected': 'rejected',
                'approved': 'approved',
                'annotated': 'annotated',
                'pending': 'pending',
                'failed': 'failed',
            }[bucket]
            item['task_id'] = int(task.id)
            item['task_name'] = task.name
            item['task_mode'] = task.mode
            item['task_image_id'] = int(row.id)
            item['can_annotate'] = bucket == 'editable'
            item['can_review'] = bucket == 'reviewable'
            item['task_submitter_id'] = task.submitter_account_id
            item['task_assignee_id'] = task.assignee_account_id

    return progress


def list_project_images_with_progress(
    db: Session,
    project_id: int,
    *,
    offset: int = 0,
    limit: int = 50,
    stage: str | None = None,
) -> tuple[list[Image], int, dict[int, dict]]:
    '''项目图片 + 进度。带 stage 筛选时先全量算再切页。

    项目里的图片量由组配额约束（默认上限几千张），这一层扫一遍 id 足够快；
    不带筛选时仍走 SQL 分页，避免大项目白扫。
    '''
    progress = image_progress(db, project_id)
    if stage is None:
        rows, total = list_project_images(db, project_id, offset=offset, limit=limit)
        return rows, total, progress

    rows, _ = list_project_images(db, project_id, offset=0, limit=1_000_000)
    matched = [
        row
        for row in rows
        if (progress.get(int(row.id)) or {}).get('stage', 'none') == stage
    ]
    return matched[offset : offset + limit], len(matched), progress


def get_link(db: Session, project_id: int, image_id: int) -> ProjectImage | None:
    return db.get(ProjectImage, (project_id, image_id))


def link_image(
    db: Session,
    *,
    project_id: int,
    image_id: int,
    added_by_account_id: int | None,
    commit: bool = True,
) -> bool:
    if get_link(db, project_id, image_id) is not None:
        return False

    db.add(
        ProjectImage(
            project_id=project_id,
            image_id=image_id,
            added_by_account_id=added_by_account_id,
        )
    )
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def remove_project_image(
    db: Session, project_id: int, image_id: int, *, commit: bool = True
) -> bool:
    link = get_link(db, project_id, image_id)
    if link is None:
        return False
    db.delete(link)
    if commit:
        db.commit()
    else:
        db.flush()
    return True


def _active_condition():
    return Image.deleted_at.is_(None)


def count_project_images(db: Session, project_id: int) -> int:
    stmt = (
        select(func.count(Image.id))
        .join(ProjectImage, ProjectImage.image_id == Image.id)
        .where(ProjectImage.project_id == project_id, _active_condition())
    )
    return int(db.scalar(stmt) or 0)


def list_project_images(
    db: Session, project_id: int, *, offset: int = 0, limit: int = 50
) -> tuple[list[Image], int]:
    stmt = (
        select(Image)
        .join(ProjectImage, ProjectImage.image_id == Image.id)
        .where(ProjectImage.project_id == project_id, _active_condition())
        .order_by(Image.id)
        .offset(offset)
        .limit(limit)
    )
    rows = list(db.scalars(stmt).all())
    return rows, count_project_images(db, project_id)


def account_can_access_via_project(
    db: Session, image_id: int, account_id: int
) -> bool:
    '''不是组员、但被加进了某个包含这张图的项目 → 也能看。'''
    stmt = (
        select(ProjectMember.project_id)
        .join(ProjectImage, ProjectImage.project_id == ProjectMember.project_id)
        .where(
            ProjectImage.image_id == image_id,
            ProjectMember.account_id == account_id,
            ProjectMember.status == 'active',
        )
        .limit(1)
    )
    return db.scalar(stmt) is not None
