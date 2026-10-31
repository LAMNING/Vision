from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, joinedload

from app.data.task_image import TaskImage
from app.errors import InvalidImageReviewError, TaskImageNotFoundError
from app.utils.time import utcnow


def progress_by_task(db: Session, task_ids: list[int]) -> dict[int, tuple[int, int]]:
    '''一次聚合查出多个任务的（图片总数，已标注数）。

    任务列表要显示进度，逐条查详情会变成 N+1，所以这里用一条 GROUP BY 拿全。
    返回 {task_id: (total, annotated)}；没有图片的任务不在结果里，调用方按 (0, 0) 处理。
    '''
    if not task_ids:
        return {}
    annotated = func.sum(case((TaskImage.annotation_status == 'annotated', 1), else_=0))
    rows = db.execute(
        select(TaskImage.task_id, func.count(TaskImage.id), annotated)
        .where(TaskImage.task_id.in_(task_ids))
        .group_by(TaskImage.task_id)
    ).all()
    return {int(task_id): (int(total or 0), int(done or 0)) for task_id, total, done in rows}


def review_progress_by_task(db: Session, task_ids: list[int]) -> dict[int, tuple[int, int]]:
    '''一次聚合出「逐图复核」的两个计数：{task_id: (approved, rejected)}。

    任务列表里「待复核」那一行要能显示「复核 2 / 4 张」，审核员才知道还剩几张没看；
    否则任务卡在待复核时界面上看不出任何进展。
    '''
    if not task_ids:
        return {}
    approved = func.sum(case((TaskImage.review_status == 'approved', 1), else_=0))
    rejected = func.sum(case((TaskImage.review_status == 'rejected', 1), else_=0))
    rows = db.execute(
        select(TaskImage.task_id, approved, rejected)
        .where(TaskImage.task_id.in_(task_ids))
        .group_by(TaskImage.task_id)
    ).all()
    return {int(task_id): (int(ok or 0), int(bad or 0)) for task_id, ok, bad in rows}


def list_task_images(db: Session, task_id: int) -> list[TaskImage]:
    stmt = (
        select(TaskImage)
        .options(joinedload(TaskImage.image))
        .where(TaskImage.task_id == task_id)
        .order_by(TaskImage.id)
    )
    return list(db.scalars(stmt).unique().all())


def get_task_image(
    db: Session, task_id: int, image_id: int
) -> TaskImage | None:
    return db.scalar(
        select(TaskImage).where(
            TaskImage.task_id == task_id, TaskImage.image_id == image_id
        )
    )


def get_task_image_or_raise(
    db: Session, task_id: int, image_id: int
) -> TaskImage:
    row = get_task_image(db, task_id, image_id)
    if row is None:
        raise TaskImageNotFoundError(task_id, image_id)
    return row


def get_by_id(db: Session, task_image_id: int) -> TaskImage | None:
    return db.get(TaskImage, task_image_id)


def mark_annotating_image(db: Session, row: TaskImage) -> TaskImage:
    row.annotation_status = 'pending'
    row.annotated_at = None
    db.flush()
    return row


def mark_annotated(db: Session, row: TaskImage, *, commit: bool = True) -> TaskImage:
    row.annotation_status = 'annotated'
    row.annotated_at = utcnow()
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def mark_annotation_failed(
    db: Session, row: TaskImage, reason: str | None = None, *, commit: bool = True
) -> TaskImage:
    row.annotation_status = 'failed'
    row.annotated_at = None
    if reason:
        row.reject_reason = None if row.review_status == 'approved' else reason
    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def set_review(
    db: Session,
    row: TaskImage,
    *,
    approved: bool,
    reason: str | None = None,
    reviewer_account_id: int | None = None,
    commit: bool = True,
) -> TaskImage:
    if not approved and not reason:
        raise InvalidImageReviewError(
            f'task_image {row.id}: reject 必须提供 reject_reason'
        )

    row.review_status = 'approved' if approved else 'rejected'
    row.reject_reason = None if approved else reason
    row.reviewed_by_account_id = reviewer_account_id
    row.reviewed_at = utcnow()

    if commit:
        db.commit()
        db.refresh(row)
    else:
        db.flush()
    return row


def apply_revision_bump(db: Session, row: TaskImage) -> TaskImage:
    row.revision += 1
    row.annotation_status = 'pending'
    row.annotated_at = None
    db.flush()
    return row
