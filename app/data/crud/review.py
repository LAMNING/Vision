from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data.review import REVIEW_DECISIONS, Review
from app.data.task import Task
from app.data.crud import annotation as ann
from app.data.crud import audit
from app.data.crud import notification as notif_crud
from app.data.crud import project_member as pmm
from app.data.crud import task as tsk
from app.data.crud import task_image as ti
from app.errors import (
    IncompleteReviewError,
    InvalidReviewDecisionError,
    InvalidTaskTransitionError,
    ReviewerNotAllowedError,
)
from app.utils.time import utcnow

REVIEWER_ROLES = ('reviewer', 'manager')

_DECISION_TO_STATUS = {
    'approve': 'approved',
    'reject_fix': 'rejected_fix',
    'reject_reannotate': 'rejected_reannotate',
}


def list_reviews(db: Session, task_id: int) -> list[Review]:
    stmt = (
        select(Review)
        .where(Review.task_id == task_id)
        .order_by(Review.round)
    )
    return list(db.scalars(stmt).all())


def next_round(db: Session, task_id: int) -> int:
    current = db.scalar(
        select(func.max(Review.round)).where(Review.task_id == task_id)
    )
    return int(current or 0) + 1


def _assert_reviewer_allowed(
    db: Session, task: Task, reviewer_account_id: int
) -> None:
    membership = pmm.get_membership(db, task.project_id, reviewer_account_id)
    active = (
        membership is not None
        and membership.status == 'active'
        and (
            membership.expires_at is None
            or membership.expires_at > utcnow()
        )
        and bool(set(membership.roles or []) & set(REVIEWER_ROLES))
    )
    if not active:
        raise ReviewerNotAllowedError(
            f'account {reviewer_account_id} is not an active reviewer/manager '
            f'of project {task.project_id}'
        )
    if (
        task.reviewer_account_id is not None
        and reviewer_account_id != task.reviewer_account_id
        and 'manager' not in set(membership.roles or [])
    ):
        raise ReviewerNotAllowedError(
            f'task {task.id} is assigned to reviewer '
            f'{task.reviewer_account_id}, not {reviewer_account_id}'
        )


def require_reviewer_allowed(db: Session, task: Task, reviewer_account_id: int) -> None:
    '''公开的复核权限检查（router 逐图复核时复用同一套规则）。'''
    _assert_reviewer_allowed(db, task, reviewer_account_id)


def submit_review(
    db: Session,
    task: Task,
    *,
    reviewer_account_id: int,
    decision: str,
    comment: str | None = None,
    commit: bool = True,
) -> Review:
    if decision not in REVIEW_DECISIONS:
        raise InvalidReviewDecisionError(
            f'invalid decision {decision!r}; must be one of {REVIEW_DECISIONS}'
        )
    if task.status != 'auto_annotated':
        raise InvalidTaskTransitionError(task.status, _DECISION_TO_STATUS[decision])

    _assert_reviewer_allowed(db, task, reviewer_account_id)

    images = ti.list_task_images(db, task.id)
    if not images:
        raise IncompleteReviewError(f'task {task.id} has no images to review')

    approved = [r for r in images if r.review_status == 'approved']
    rejected = [r for r in images if r.review_status == 'rejected']
    undecided = [r for r in images if r.review_status == 'pending']

    if decision == 'approve':
        if undecided or rejected:
            raise IncompleteReviewError(
                f'cannot approve: {len(undecided)} image(s) undecided, '
                f'{len(rejected)} rejected'
            )
    else:
        if not rejected:
            raise IncompleteReviewError(
                f'cannot reject: no image marked rejected '
                f'({len(approved)} approved, {len(undecided)} undecided)'
            )
        not_annotated = [r for r in rejected if r.annotation_status != 'annotated']
        if not_annotated:
            raise IncompleteReviewError(
                f'{len(not_annotated)} rejected image(s) were never annotated'
            )

    review = Review(
        task_id=task.id,
        reviewer_account_id=reviewer_account_id,
        round=next_round(db, task.id),
        decision=decision,
        comment=comment,
        approved_count=len(approved),
        rejected_count=len(rejected),
    )
    db.add(review)
    db.flush()

    if decision != 'approve':
        for row in rejected:
            ti.apply_revision_bump(db, row)
            ann.deactivate_current(db, row.id)

    tsk.transition_task(
        db,
        task,
        _DECISION_TO_STATUS[decision],
        actor_account_id=reviewer_account_id,
        commit=False,
    )

    audit.record(
        db,
        action='review.created',
        resource_type='review',
        resource_id=review.id,
        actor_account_id=reviewer_account_id,
        group_id=task.project.group_id if task.project is not None else None,
        after=audit.snapshot(review),
    )

    if commit:
        db.commit()
        db.refresh(review)
        _notify_review_outcome(db, task, decision)
    return review


def auto_approve(
    db: Session,
    task: Task,
    *,
    actor_account_id: int | None = None,
    comment: str = '创建任务时未要求复核，标注完成即自动通过',
    commit: bool = True,
) -> Review | None:
    '''「不需要审核」的任务：标注完成后直接通过。

    逐图补一条 approve（不留复核人，`reviewed_by_account_id = NULL`），
    再写一条 approve 的 Review 记录，这样历史里仍然能看到它是怎么通过的。
    逐图没标完 / 任务不在 auto_annotated，就什么也不做，交回正常流程。
    '''
    if task.review_required:
        return None

    images = ti.list_task_images(db, task.id)
    if not images:
        return None
    if any(row.annotation_status != 'annotated' for row in images):
        return None

    for row in images:
        if row.review_status != 'approved':
            ti.set_review(
                db,
                row,
                approved=True,
                reviewer_account_id=None,
                commit=False,
            )

    return _write_approval(
        db,
        task,
        actor_account_id=actor_account_id,
        comment=comment,
        count=len(images),
        commit=commit,
    )


def finalize_approved(
    db: Session,
    task: Task,
    *,
    actor_account_id: int | None = None,
    comment: str = '逐图复核全部通过',
    commit: bool = True,
) -> Review | None:
    '''逐图都有结论且全部通过时收口：补一条 approve 记录并把任务推到 approved。

    与 `auto_approve` 的差别是**不看** `review_required` —— 调用方已经确认每张图都被
    人复核过（图像 tab 逐图「审核」、复核工作台逐图「通过这张」），再让人回任务里点一次
    「通过任务」纯属重复，而且会留下「图像里已经审完、任务列表还是待复核」的错位感。
    只要还有一张没通过（未审核 / 已退回 / 未标注），就什么也不做。
    '''
    if task.status != 'auto_annotated':
        return None

    images = ti.list_task_images(db, task.id)
    if not images:
        return None
    if any(row.annotation_status != 'annotated' for row in images):
        return None
    if any(row.review_status != 'approved' for row in images):
        return None

    return _write_approval(
        db,
        task,
        actor_account_id=actor_account_id,
        comment=comment,
        count=len(images),
        commit=commit,
    )


def _write_approval(
    db: Session,
    task: Task,
    *,
    actor_account_id: int | None,
    comment: str,
    count: int,
    commit: bool,
) -> Review:
    '''auto_approve / finalize_approved 共用的收口写库（建 Review + 改状态 + 审计）。'''
    actor = actor_account_id if actor_account_id is not None else task.submitter_account_id

    review = Review(
        task_id=task.id,
        reviewer_account_id=actor,
        round=next_round(db, task.id),
        decision='approve',
        comment=comment,
        approved_count=count,
        rejected_count=0,
    )
    db.add(review)
    db.flush()

    tsk.transition_task(
        db, task, 'approved', actor_account_id=actor, commit=False
    )
    audit.record(
        db,
        action='review.auto_approved',
        resource_type='review',
        resource_id=review.id,
        actor_account_id=actor,
        group_id=task.project.group_id if task.project is not None else None,
        after={'decision': 'approve', 'images': count},
    )

    if commit:
        db.commit()
        db.refresh(review)
        _notify_review_outcome(db, task, 'approve')
    return review


_REVIEW_TITLES = {
    'approve': '任务已通过复核',
    'reject_fix': '任务被退回修正',
    'reject_reannotate': '任务被退回重新标注',
}


def notify_outcome(db: Session, task: Task, decision: str) -> None:
    '''提交者 / 被指派人收到复核结果通知（供路由在 commit 之后调用）。'''
    _notify_review_outcome(db, task, decision)


def _notify_review_outcome(db: Session, task: Task, decision: str) -> None:
    '''复核结果通知提交者与被指派人（去重）。'''
    title = _REVIEW_TITLES.get(decision, '任务复核结果已更新')
    receivers = {
        account_id
        for account_id in (task.submitter_account_id, task.assignee_account_id)
        if account_id is not None
    }
    for account_id in receivers:
        notif_crud.create(
            db,
            account_id,
            kind='task_review',
            title=f'{title}：任务 #{task.id}',
            body=task.name or '(未命名任务)',
            resource_type='task',
            resource_id=task.id,
        )
