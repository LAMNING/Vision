"""项目维度统计：任务/图片/标注/复核，供前端看板与 Dashboard 使用。"""

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.data import Annotation, Image, ProjectImage, Task, TaskImage
from app.data.crud.project import get_project_or_raise
from app.data.crud.project_category import list_categories


def _annotation_agg(project_id: int, *columns):
    """标注聚合的公共骨架：显式从 Annotation 起手，再逐个 join 到任务。

    以前这里是 `select(...).select_from(base.subquery())`，但列仍然引用
    Annotation 实体 —— SQLAlchemy 会把 annotation 表再塞进 FROM，
    和那个子查询形成笛卡尔积：每条统计都被放大成「条数 × 条数」倍。
    直接从 Annotation 起手 join，才是唯一一条 JOIN 链。
    """
    return (
        select(*columns)
        .select_from(Annotation)
        .join(TaskImage, TaskImage.id == Annotation.task_image_id)
        .join(Task, Task.id == TaskImage.task_id)
        .where(
            Task.project_id == project_id,
            Task.deleted_at.is_(None),
            Annotation.is_current.is_(True),
        )
    )


def project_stats(db: Session, project_id: int) -> dict:
    project = get_project_or_raise(db, project_id)

    task_rows = db.execute(
        select(Task.status, func.count())
        .where(Task.project_id == project_id, Task.deleted_at.is_(None))
        .group_by(Task.status)
    ).all()
    tasks_by_status = {str(status): int(count) for status, count in task_rows}
    tasks_total = sum(tasks_by_status.values())

    mode_rows = db.execute(
        select(Task.mode, func.count())
        .where(Task.project_id == project_id, Task.deleted_at.is_(None))
        .group_by(Task.mode)
    ).all()
    tasks_by_mode = {str(mode): int(count) for mode, count in mode_rows}

    images_total = int(
        db.scalar(
            select(func.count())
            .select_from(ProjectImage)
            .join(Image, Image.id == ProjectImage.image_id)
            .where(
                ProjectImage.project_id == project_id,
                Image.deleted_at.is_(None),
            )
        )
        or 0
    )

    annotations_current = int(
        db.scalar(_annotation_agg(project_id, func.count())) or 0
    )

    source_rows = db.execute(
        _annotation_agg(project_id, Annotation.source, func.count())
        .group_by(Annotation.source)
    ).all()
    annotations_by_source = {str(s): int(c) for s, c in source_rows}

    category_rows = db.execute(
        _annotation_agg(project_id, Annotation.category_id, func.count())
        .group_by(Annotation.category_id)
    ).all()
    names = {
        category.id: category.zh_name
        for category in list_categories(db, project_id, include_disabled=True)
    }
    annotations_by_category = [
        {
            'category_id': int(category_id) if category_id is not None else None,
            'name': names.get(category_id, '未分类' if category_id is None else f'#{category_id}'),
            'count': int(count),
        }
        for category_id, count in sorted(
            category_rows, key=lambda row: (row[0] is None, row[0] or 0)
        )
    ]

    day_rows = db.execute(
        _annotation_agg(project_id, func.date(Annotation.created_at), func.count())
        .group_by(func.date(Annotation.created_at))
        .order_by(func.date(Annotation.created_at))
    ).all()
    annotations_by_day = [
        {'day': str(day), 'count': int(count)} for day, count in day_rows
    ]

    approved_tasks = tasks_by_status.get('approved', 0)
    reviewed_tasks = (
        approved_tasks
        + tasks_by_status.get('rejected_fix', 0)
        + tasks_by_status.get('rejected_reannotate', 0)
    )
    pass_rate = (approved_tasks / reviewed_tasks) if reviewed_tasks else 0.0

    return {
        'project_id': project.id,
        'tasks_total': tasks_total,
        'tasks_by_status': tasks_by_status,
        'tasks_by_mode': tasks_by_mode,
        'images_total': images_total,
        'annotations_current': annotations_current,
        'annotations_by_source': annotations_by_source,
        'annotations_by_category': annotations_by_category,
        'annotations_by_day': annotations_by_day,
        'approved_tasks': approved_tasks,
        'reviewed_tasks': reviewed_tasks,
        'review_pass_rate': round(pass_rate, 4),
    }
