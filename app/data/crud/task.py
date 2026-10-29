import os
from datetime import datetime, timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.data.notification import Notification
from app.data.project import Project
from app.data.review import Review
from app.data.task import (
    ALLOWED_TRANSITIONS,
    TASK_MODES,
    TASK_PRIORITY_MAX,
    TASK_PRIORITY_MIN,
    TERMINAL_TASK_STATUSES,
    Task,
)
from app.data.task_image import TaskImage
from app.data.crud import audit, image as im
from app.data.crud import notification as notif_crud
from app.data.crud import project as pj
from app.data.crud import project_member as pmm
from app.data.crud.project import UNSET
from app.errors import (
    ImageCrossGroupError,
    ImageDeletedError,
    ImageNotFoundError,
    InvalidTaskTransitionError,
    NoReviewerAvailableError,
    ReviewerNotAllowedError,
    ReviewRequiredError,
    SubmitterNotAllowedError,
    TaskConcurrencyError,
    TaskNotFoundError,
    TaskRetryExhaustedError,
    ValidationError,
)
from app.utils.time import utcnow

MAX_RETRY = 3

SUBMIT_ROLES = ('annotator', 'manager')
REVIEW_ROLES = ('reviewer', 'manager')

# 自动标注 worker 的租约（秒）：worker 每处理一张图续一次；
# 进程崩溃/卡死后 locked_until 过期，启动回收逻辑把它标成 failed。
LEASE_SECONDS_ENV = 'IMAGELABEL_WORKER_LEASE_SECONDS'
DEFAULT_LEASE_SECONDS = 600
LEASE_MIN_SECONDS = 30
STALE_LEASE_MESSAGE = 'worker 超时或中断（租约已过期），任务已自动回收，可重投'


REVIEW_OUTCOME_DECISION = {
    'approved': 'approve',
    'rejected_fix': 'reject_fix',
    'rejected_reannotate': 'reject_reannotate',
}


def get_task(
    db: Session, task_id: int, *, include_deleted: bool = False
) -> Task | None:
    stmt = select(Task).where(Task.id == task_id)
    if not include_deleted:
        stmt = stmt.where(Task.deleted_at.is_(None))
    return db.scalar(stmt)


def get_task_or_raise(
    db: Session, task_id: int, *, include_deleted: bool = False
) -> Task:
    task = get_task(db, task_id, include_deleted=include_deleted)
    if task is None:
        raise TaskNotFoundError(task_id)
    return task


def list_project_tasks(
    db: Session,
    project_id: int,
    *,
    status: str | None = None,
    mode: str | None = None,
    assignee_account_id: int | None = None,
    include_deleted: bool = False,
) -> list[Task]:
    '''优先级高的排前面，其次按新建顺序倒序。'''
    stmt = select(Task).where(Task.project_id == project_id)
    if not include_deleted:
        stmt = stmt.where(Task.deleted_at.is_(None))
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if mode is not None:
        stmt = stmt.where(Task.mode == mode)
    if assignee_account_id is not None:
        stmt = stmt.where(Task.assignee_account_id == assignee_account_id)
    stmt = stmt.order_by(Task.priority.desc(), Task.id.desc())
    return list(db.scalars(stmt).all())


def list_my_tasks(
    db: Session,
    account_id: int,
    *,
    role: str = 'assignee',
    status: str | None = None,
    mode: str | None = None,
    limit: int = 100,
) -> list[Task]:
    '''跨项目的「我的任务」。

    role:
      - ``assignee``: 指派给我的
      - ``submitter``: 我提交的
      - ``reviewer``: 我负责复核的
      - ``any``: 三者并集
    '''
    if role not in ('assignee', 'submitter', 'reviewer', 'any'):
        raise ValidationError(f'role 必须是 assignee/submitter/reviewer/any，收到 {role!r}')

    stmt = select(Task).where(Task.deleted_at.is_(None))
    conditions = []
    if role in ('assignee', 'any'):
        conditions.append(Task.assignee_account_id == account_id)
    if role in ('submitter', 'any'):
        conditions.append(Task.submitter_account_id == account_id)
    if role in ('reviewer', 'any'):
        conditions.append(Task.reviewer_account_id == account_id)
    stmt = stmt.where(or_(*conditions))
    if status is not None:
        stmt = stmt.where(Task.status == status)
    if mode is not None:
        stmt = stmt.where(Task.mode == mode)
    stmt = stmt.order_by(Task.priority.desc(), Task.id.desc()).limit(max(1, min(limit, 200)))
    return list(db.scalars(stmt).all())


def list_overdue_tasks(db: Session, *, now: datetime | None = None) -> list[Task]:
    '''已过截止时间、还没结束、且还没发过逾期提醒的任务。'''
    moment = now or utcnow()
    reminded = select(Notification.resource_id).where(
        Notification.kind == 'task_overdue',
        Notification.resource_type == 'task',
    )
    stmt = (
        select(Task)
        .where(
            Task.deleted_at.is_(None),
            Task.due_at.is_not(None),
            Task.due_at < moment,
            Task.status.not_in(('approved', 'cancelled', 'failed')),
            Task.id.not_in(reminded),
        )
        .order_by(Task.id)
    )
    return list(db.scalars(stmt).all())


def list_tasks_as_submitter(
    db: Session, account_id: int, *, limit: int | None = None
) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.submitter_account_id == account_id, Task.deleted_at.is_(None))
        .order_by(Task.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def list_tasks_as_reviewer(
    db: Session, account_id: int, *, limit: int | None = None
) -> list[Task]:
    stmt = (
        select(Task)
        .where(Task.reviewer_account_id == account_id, Task.deleted_at.is_(None))
        .order_by(Task.id.desc())
    )
    if limit:
        stmt = stmt.limit(limit)
    return list(db.scalars(stmt).all())


def _active_roles(
    db: Session, project_id: int, account_id: int
) -> list[str] | None:
    membership = pmm.get_membership(db, project_id, account_id)
    if membership is None or membership.status != 'active':
        return None
    if membership.expires_at is not None and membership.expires_at <= utcnow():
        return None
    return list(membership.roles or [])


def _has_any_role(
    db: Session, project_id: int, account_id: int, allowed: tuple[str, ...]
) -> bool:
    roles = _active_roles(db, project_id, account_id)
    return bool(roles) and bool(set(roles) & set(allowed))


def resolve_reviewer(
    db: Session,
    project: Project,
    submitter_account_id: int,
    reviewer_account_id: int | None,
) -> int:
    if reviewer_account_id is not None:
        if not _has_any_role(
            db, project.id, reviewer_account_id, REVIEW_ROLES
        ):
            raise ReviewerNotAllowedError(
                f'account {reviewer_account_id} is not an active reviewer/manager '
                f'of project {project.id}'
            )
        if (
            project.default_reviewer == 'project_manager'
            and reviewer_account_id == submitter_account_id
        ):
            raise ReviewerNotAllowedError(
                f'project {project.id} requires four-eyes review; '
                f'reviewer must differ from submitter'
            )
        return reviewer_account_id

    if project.default_reviewer == 'project_manager':
        candidates = [
            m
            for m in pmm.list_members(db, project.id)
            if 'manager' in (m.roles or [])
            and m.account_id != submitter_account_id
        ]
        if not candidates:
            raise NoReviewerAvailableError(
                f'project {project.id} defaults to manager review but has no '
                f'active manager other than the submitter'
            )
        return candidates[0].account_id

    return submitter_account_id


def attach_images(
    db: Session,
    task: Task,
    image_ids: list[int],
    *,
    commit: bool = False,
) -> list[TaskImage]:
    project = task.project if task.project is not None else db.get(Project, task.project_id)
    if project is None:
        raise TaskNotFoundError(task.id)
    group_id = project.group_id

    out: list[TaskImage] = []
    for image_id in dict.fromkeys(image_ids):  # 去重且保序
        image = im.get_image(db, image_id, include_deleted=True)
        if image is None:
            raise ImageNotFoundError(image_id)
        if image.deleted_at is not None:
            raise ImageDeletedError(image_id)
        if image.group_id != group_id:
            raise ImageCrossGroupError(image_id, image.group_id, group_id)

        existing = db.scalar(
            select(TaskImage).where(
                TaskImage.task_id == task.id, TaskImage.image_id == image_id
            )
        )
        if existing is not None:
            out.append(existing)
            continue

        row = TaskImage(task_id=task.id, image_id=image_id)
        db.add(row)
        out.append(row)

    db.flush()
    return out


def _clean_priority(priority: int) -> int:
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValidationError('priority 必须是 0/1/2 的整数')
    if not TASK_PRIORITY_MIN <= priority <= TASK_PRIORITY_MAX:
        raise ValidationError(
            f'priority 必须在 {TASK_PRIORITY_MIN}..{TASK_PRIORITY_MAX} 之间'
        )
    return priority


def _clean_note(note: str | None) -> str | None:
    if note is None:
        return None
    text = note.strip()
    return text[:500] or None


def _assert_assignee(db: Session, project_id: int, account_id: int) -> None:
    if not _has_any_role(db, project_id, account_id, SUBMIT_ROLES):
        raise ValidationError(
            f'account {account_id} 不是项目 {project_id} 的 annotator/manager，不能指派'
        )


def _notify_assigned(db: Session, task: Task, *, commit: bool = True) -> None:
    if task.assignee_account_id is None:
        return
    notif_crud.create(
        db,
        task.assignee_account_id,
        kind='task_assigned',
        title=f'任务 #{task.id} 指派给你',
        body=task.name or '(未命名任务)',
        resource_type='task',
        resource_id=task.id,
        commit=commit,
    )


def create_task(
    db: Session,
    *,
    project_id: int,
    submitter_account_id: int,
    name: str | None = None,
    reviewer_account_id: int | None = None,
    image_ids: list[int] | None = None,
    mode: str = 'auto',
    assignee_account_id: int | None = None,
    priority: int = 0,
    due_at: datetime | None = None,
    note: str | None = None,
    commit: bool = True,
) -> Task:
    project = pj.get_archivable_project(db, project_id)

    if not _has_any_role(db, project.id, submitter_account_id, SUBMIT_ROLES):
        raise SubmitterNotAllowedError(
            f'account {submitter_account_id} is not an active annotator/manager '
            f'of project {project.id}'
        )

    if mode not in TASK_MODES:
        raise ValidationError(f'mode 必须是 {TASK_MODES} 之一')
    priority = _clean_priority(priority)
    if assignee_account_id is not None:
        _assert_assignee(db, project.id, assignee_account_id)

    reviewer_id = resolve_reviewer(
        db, project, submitter_account_id, reviewer_account_id
    )

    task = Task(
        project_id=project.id,
        name=name,
        status='draft',
        submitter_account_id=submitter_account_id,
        reviewer_account_id=reviewer_id,
        mode=mode,
        assignee_account_id=assignee_account_id,
        priority=priority,
        due_at=due_at,
        note=_clean_note(note),
    )
    db.add(task)
    db.flush()

    if image_ids:
        attach_images(db, task, image_ids)

    audit.record(
        db,
        action='task.created',
        resource_type='task',
        resource_id=task.id,
        actor_account_id=submitter_account_id,
        group_id=project.group_id,
        after=audit.snapshot(task),
    )

    if assignee_account_id is not None:
        _notify_assigned(db, task, commit=False)

    if commit:
        db.commit()
        db.refresh(task)
    else:
        db.flush()
    return task


def update_task_meta(
    db: Session,
    task: Task,
    *,
    name: str | None | object = UNSET,
    assignee_account_id: int | None | object = UNSET,
    priority: int | object = UNSET,
    due_at: datetime | None | object = UNSET,
    note: str | None | object = UNSET,
    actor_account_id: int | None = None,
    commit: bool = True,
) -> Task:
    '''更新任务协作字段（名称/指派人/优先级/截止/备注）。

    UNSET 表示「没传」；显式传 None 表示清空。
    '''
    before = audit.snapshot(task)
    changed: dict = {}

    if name is not UNSET:
        task.name = name
        changed['name'] = name
    if assignee_account_id is not UNSET:
        if assignee_account_id is not None:
            _assert_assignee(db, task.project_id, assignee_account_id)
        if task.assignee_account_id != assignee_account_id:
            task.assignee_account_id = assignee_account_id
            changed['assignee_account_id'] = assignee_account_id
            if assignee_account_id is not None:
                _notify_assigned(db, task, commit=False)
    if priority is not UNSET:
        task.priority = _clean_priority(priority)
        changed['priority'] = task.priority
    if due_at is not UNSET:
        task.due_at = due_at
        changed['due_at'] = due_at
    if note is not UNSET:
        task.note = _clean_note(note)
        changed['note'] = task.note

    if changed:
        db.add(task)
        audit.record(
            db,
            action='task.updated',
            resource_type='task',
            resource_id=task.id,
            actor_account_id=actor_account_id,
            group_id=task.project.group_id if task.project is not None else None,
            before=before,
            after=audit.snapshot(task),
        )
    if commit:
        db.commit()
        db.refresh(task)
    return task


def transition_task(
    db: Session,
    task: Task,
    to_status: str,
    *,
    actor_account_id: int | None = None,
    error_message: str | None = None,
    commit: bool = True,
) -> Task:
    from_status = task.status
    if to_status not in ALLOWED_TRANSITIONS.get(from_status, ()):
        raise InvalidTaskTransitionError(from_status, to_status)

    expected_decision = REVIEW_OUTCOME_DECISION.get(to_status)
    if expected_decision is not None:
        latest = db.scalar(
            select(Review)
            .where(Review.task_id == task.id)
            .order_by(Review.round.desc())
            .limit(1)
        )
        if latest is None or latest.decision != expected_decision:
            raise ReviewRequiredError(task.id, to_status)

    values: dict = {'status': to_status, 'version': Task.version + 1}

    if to_status == 'failed':
        values['error_message'] = error_message
    elif from_status == 'failed' and to_status == 'queued':
        if task.retry_count >= MAX_RETRY:
            raise TaskRetryExhaustedError(
                f'task {task.id} already retried {task.retry_count}/{MAX_RETRY} times'
            )
        values['retry_count'] = Task.retry_count + 1
        values['error_message'] = None

    if to_status in TERMINAL_TASK_STATUSES:
        values['completed_at'] = utcnow()

    before = {'status': from_status, 'version': task.version}
    result = db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.version == task.version,
            Task.status == from_status,
        )
        .values(**values)
        .execution_options(synchronize_session=False)
    )
    if result.rowcount == 0:
        raise TaskConcurrencyError(
            f'task {task.id} modified concurrently '
            f'(expected version {task.version}, status {from_status!r})'
        )

    db.refresh(task)
    audit.record(
        db,
        action='task.status_changed',
        resource_type='task',
        resource_id=task.id,
        actor_account_id=actor_account_id,
        group_id=task.project.group_id if task.project is not None else None,
        before=before,
        after={'status': to_status, 'version': task.version},
    )

    if commit:
        db.commit()
        db.refresh(task)
    return task


def submit_task(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    task = transition_task(
        db, task, 'queued', actor_account_id=actor_account_id, commit=commit
    )
    if commit and task.mode == 'manual' and task.assignee_account_id is not None:
        notif_crud.create(
            db,
            task.assignee_account_id,
            kind='task_ready',
            title=f'任务 #{task.id} 待人工标注',
            body=task.name or '(未命名任务)',
            resource_type='task',
            resource_id=task.id,
        )
    return task


def complete_manual_task(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    '''manual 任务：所有图都标注完后 queued -> auto_annotated（进入复核）。'''
    if task.mode != 'manual':
        raise ValidationError('只有 manual 任务可以「完成标注」')
    if task.status != 'queued':
        raise ValidationError('只有 queued 状态的 manual 任务可以完成标注')

    images = db.scalars(
        select(TaskImage).where(TaskImage.task_id == task.id)
    ).all()
    if not images:
        raise ValidationError('任务没有图片，不能完成标注')
    pending = [row for row in images if row.annotation_status != 'annotated']
    if pending:
        raise ValidationError(f'还有 {len(pending)} 张图未标注完成，不能结束')

    task = transition_task(
        db, task, 'auto_annotated', actor_account_id=actor_account_id, commit=commit
    )
    if commit and task.reviewer_account_id is not None:
        notif_crud.create(
            db,
            task.reviewer_account_id,
            kind='task_review',
            title=f'任务 #{task.id} 待复核',
            body=task.name or '(未命名任务)',
            resource_type='task',
            resource_id=task.id,
        )
    return task


def mark_annotating(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    return transition_task(
        db, task, 'auto_annotating', actor_account_id=actor_account_id, commit=commit
    )


def mark_auto_annotated(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    return transition_task(
        db, task, 'auto_annotated', actor_account_id=actor_account_id, commit=commit
    )


def mark_failed(
    db: Session,
    task: Task,
    error_message: str,
    *,
    actor_account_id: int | None = None,
    commit: bool = True,
) -> Task:
    return transition_task(
        db,
        task,
        'failed',
        error_message=error_message,
        actor_account_id=actor_account_id,
        commit=commit,
    )


def requeue_failed(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    return transition_task(
        db, task, 'queued', actor_account_id=actor_account_id, commit=commit
    )


def cancel_task(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    return transition_task(
        db, task, 'cancelled', actor_account_id=actor_account_id, commit=commit
    )


def resubmit_after_fix(
    db: Session, task: Task, *, actor_account_id: int | None = None, commit: bool = True
) -> Task:
    return transition_task(
        db, task, 'auto_annotated', actor_account_id=actor_account_id, commit=commit
    )


# ---- 自动标注 worker 租约 ----


def lease_seconds() -> int:
    '''续租时长（秒），可用 IMAGELABEL_WORKER_LEASE_SECONDS 覆盖。'''
    raw = os.environ.get(LEASE_SECONDS_ENV)
    if raw is None:
        return DEFAULT_LEASE_SECONDS
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_LEASE_SECONDS
    return max(LEASE_MIN_SECONDS, value)


def claim_queued_task(
    db: Session,
    *,
    worker_id: str,
    lease: int | None = None,
    now: datetime | None = None,
) -> Task | None:
    '''抢占一个 queued 的 auto 任务：条件 UPDATE，谁抢到算谁的。

    和 transition_task 的区别：这里不需要先读版本号，靠 ``status='queued'``
    作为唯一条件，多个 worker/进程并发时只有一个 rowcount=1。
    '''
    lease = lease_seconds() if lease is None else max(LEASE_MIN_SECONDS, lease)
    now = now or utcnow()
    candidate_ids = db.scalars(
        select(Task.id)
        .where(
            Task.status == 'queued',
            Task.mode == 'auto',
            Task.deleted_at.is_(None),
        )
        .order_by(Task.id)
        .limit(5)
    ).all()
    for task_id in candidate_ids:
        result = db.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.status == 'queued',
                Task.mode == 'auto',
                Task.deleted_at.is_(None),
            )
            .values(
                status='auto_annotating',
                worker_id=worker_id,
                locked_until=now + timedelta(seconds=lease),
                version=Task.version + 1,
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount:
            db.commit()
            task = db.get(Task, task_id)
            if task is None:
                continue
            db.refresh(task)
            # 审计写库必须立刻提交：这条 INSERT 会拿到 SQLite 写锁，
            # 而调用方紧接着要加载模型 + 跑推理（可能几十秒到几分钟）。
            # 锁一直不还，其它请求的写操作就会报 "database is locked"。
            audit.record(
                db,
                action='task.claimed',
                resource_type='task',
                resource_id=task.id,
                group_id=task.project.group_id if task.project is not None else None,
                after={
                    'status': task.status,
                    'worker_id': worker_id,
                    'locked_until': task.locked_until.isoformat()
                    if task.locked_until
                    else None,
                },
                commit=True,
            )
            return task
    db.rollback()
    return None


def renew_lease(
    db: Session,
    task: Task,
    *,
    worker_id: str,
    lease: int | None = None,
    now: datetime | None = None,
    commit: bool = True,
) -> bool:
    '''续租：只有仍持有租约的 worker 能续上，否则返回 False（已被回收）。'''
    lease = lease_seconds() if lease is None else max(LEASE_MIN_SECONDS, lease)
    now = now or utcnow()
    result = db.execute(
        update(Task)
        .where(
            Task.id == task.id,
            Task.status == 'auto_annotating',
            Task.worker_id == worker_id,
        )
        .values(locked_until=now + timedelta(seconds=lease))
        .execution_options(synchronize_session=False)
    )
    if commit:
        db.commit()
    return bool(result.rowcount)


def clear_lease(db: Session, task: Task, *, commit: bool = True) -> None:
    db.execute(
        update(Task)
        .where(Task.id == task.id)
        .values(worker_id=None, locked_until=None)
        .execution_options(synchronize_session=False)
    )
    if commit:
        db.commit()


def recover_stale_tasks(
    db: Session,
    *,
    now: datetime | None = None,
    actor_account_id: int | None = None,
    commit: bool = True,
) -> list[Task]:
    '''回收卡在 auto_annotating 且租约过期的任务（worker 崩溃/被杀）。

    标成 failed 并写错误信息，用户可以在任务详情里看到原因并点「重投」。
    '''
    now = now or utcnow()
    rows = list(
        db.scalars(
            select(Task).where(
                Task.status == 'auto_annotating',
                Task.deleted_at.is_(None),
                or_(Task.locked_until.is_(None), Task.locked_until < now),
            )
        ).all()
    )
    recovered: list[Task] = []
    for task in rows:
        try:
            transition_task(
                db,
                task,
                'failed',
                error_message=STALE_LEASE_MESSAGE,
                actor_account_id=actor_account_id,
                commit=False,
            )
            clear_lease(db, task, commit=False)
            if commit:
                db.commit()
            else:
                db.flush()
        except Exception:  # 并发下可能已经被别人推进，跳过即可
            db.rollback()
            continue
        recovered.append(task)
    return recovered
