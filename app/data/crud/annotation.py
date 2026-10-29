from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, selectinload

from app.data.annotation import ANNOTATION_TYPES, Annotation
from app.data.project_category import ProjectCategory
from app.data.task import Task
from app.data.task_image import TaskImage
from app.errors import InvalidAnnotationError
from app.utils.time import utcnow

REQUIRED_ITEM_KEYS = ('label', 'annotation_type', 'payload')


def list_current(db: Session, task_image_id: int) -> list[Annotation]:
    stmt = (
        select(Annotation)
        .where(
            Annotation.task_image_id == task_image_id,
            Annotation.is_current.is_(True),
        )
        .order_by(Annotation.id)
    )
    return list(db.scalars(stmt).all())


def list_history(
    db: Session, task_image_id: int, *, revision: int | None = None
) -> list[Annotation]:
    stmt = select(Annotation).where(Annotation.task_image_id == task_image_id)
    if revision is not None:
        stmt = stmt.where(Annotation.revision == revision)
    stmt = stmt.order_by(Annotation.revision, Annotation.id)
    return list(db.scalars(stmt).all())


def latest_revision(db: Session, task_image_id: int) -> int | None:
    stmt = select(Annotation.revision).where(
        Annotation.task_image_id == task_image_id
    )
    values = [r for r in db.scalars(stmt).all() if r is not None]
    return max(values) if values else None


def deactivate_current(db: Session, task_image_id: int) -> int:
    result = db.execute(
        update(Annotation)
        .where(
            Annotation.task_image_id == task_image_id,
            Annotation.is_current.is_(True),
        )
        .values(is_current=False)
        .execution_options(synchronize_session='fetch')
    )
    return int(result.rowcount or 0)


def _validate_item(item: dict) -> None:
    for key in REQUIRED_ITEM_KEYS:
        if key not in item:
            raise InvalidAnnotationError(f'annotation item missing key {key!r}')
    if not str(item['label'] or '').strip():
        raise InvalidAnnotationError('annotation label 不能为空')
    if item['annotation_type'] not in ANNOTATION_TYPES:
        raise InvalidAnnotationError(
            f"invalid annotation_type {item['annotation_type']!r}; "
            f'must be one of {ANNOTATION_TYPES}'
        )
    if not isinstance(item['payload'], dict):
        raise InvalidAnnotationError('annotation payload 必须是 dict')


def _validate_category(db: Session, row: TaskImage, item: dict) -> None:
    '''category_id 必须存在，且属于该任务所在项目。'''
    category_id = item.get('category_id')
    if category_id is None:
        return

    category = db.get(ProjectCategory, category_id)
    if category is None:
        raise InvalidAnnotationError(f'category {category_id} not found')

    project_id = row.task.project_id
    if category.project_id != project_id:
        raise InvalidAnnotationError(
            f'category {category_id} 属于项目 {category.project_id}，'
            f'与任务所在项目 {project_id} 不一致'
        )


def _validate_item_and_category(db: Session, row: TaskImage, item: dict) -> None:
    _validate_item(item)
    _validate_category(db, row, item)


def _build(
    row: TaskImage,
    item: dict,
    *,
    source: str,
    created_by_account_id: int | None,
) -> Annotation:
    return Annotation(
        task_image_id=row.id,
        label=str(item['label']).strip(),
        annotation_type=item['annotation_type'],
        payload=item['payload'],
        confidence=item.get('confidence'),
        source=source,
        category_id=item.get('category_id'),
        revision=row.revision,
        is_current=True,
        created_by_account_id=created_by_account_id,
    )


def replace_current(
    db: Session,
    row: TaskImage,
    items: list[dict],
    *,
    source: str = 'manual',
    created_by_account_id: int | None = None,
    commit: bool = True,
) -> list[Annotation]:
    for item in items:
        _validate_item_and_category(db, row, item)

    deactivate_current(db, row.id)

    created: list[Annotation] = []
    for item in items:
        ann = _build(
            row,
            item,
            source=source,
            created_by_account_id=created_by_account_id,
        )
        db.add(ann)
        created.append(ann)
    db.flush()

    if commit:
        db.commit()
    return created


def add_current(
    db: Session,
    row: TaskImage,
    item: dict,
    *,
    source: str = 'manual',
    created_by_account_id: int | None = None,
    commit: bool = True,
) -> Annotation:
    _validate_item_and_category(db, row, item)
    ann = _build(
        row, item, source=source, created_by_account_id=created_by_account_id
    )
    db.add(ann)
    if commit:
        db.commit()
        db.refresh(ann)
    else:
        db.flush()
    return ann


def count_current(db: Session, task_image_id: int) -> int:
    return len(list_current(db, task_image_id))


def list_project_annotations(
    db: Session,
    project_id: int,
    *,
    task_id: int | None = None,
    task_image_id: int | None = None,
    category_id: int | None = None,
    is_current: bool | None = True,
    offset: int = 0,
    limit: int = 50,
) -> tuple[list[Annotation], int]:
    conds = [Task.project_id == project_id]
    if task_id is not None:
        conds.append(Task.id == task_id)
    if task_image_id is not None:
        conds.append(Annotation.task_image_id == task_image_id)
    if category_id is not None:
        conds.append(Annotation.category_id == category_id)
    if is_current is not None:
        conds.append(Annotation.is_current.is_(is_current))

    base = (
        select(Annotation)
        .join(TaskImage, Annotation.task_image_id == TaskImage.id)
        .join(Task, TaskImage.task_id == Task.id)
        .where(*conds)
    )
    total = db.scalar(select(func.count()).select_from(base.subquery())) or 0

    stmt = (
        base.options(
            selectinload(Annotation.task_image).selectinload(TaskImage.image)
        )
        .order_by(Annotation.created_at.desc(), Annotation.id.desc())
        .offset(offset)
        .limit(limit)
    )
    return list(db.scalars(stmt).all()), total


def _box_to_coco(box: object) -> list[float] | None:
    '''payload.box = [x0, y0, x1, y1]（原图像素）→ COCO 的 [x, y, w, h]。'''
    if not isinstance(box, (list, tuple)) or len(box) != 4:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    return [round(x0, 2), round(y0, 2), round(x1 - x0, 2), round(y1 - y0, 2)]


def export_coco(db: Session, project_id: int) -> dict:
    '''把项目当前的 bbox 标注导出成 COCO detection JSON（只含 is_current）。

    结构：info / licenses / images / annotations / categories。
    - images：有当前标注的图片（id=image.id，尺寸取 Image 的宽高）
    - categories：项目类目（含停用的，保证历史标注有归属）
    - annotations：category_id 为空或 payload.box 非法的记录跳过
    '''
    from app.data.crud.project_category import list_categories

    categories = list_categories(db, project_id, include_disabled=True)
    category_by_id = {c.id: c for c in categories}

    stmt = (
        select(Annotation)
        .join(TaskImage, Annotation.task_image_id == TaskImage.id)
        .join(Task, TaskImage.task_id == Task.id)
        .where(
            Task.project_id == project_id,
            Annotation.is_current.is_(True),
            Annotation.annotation_type == 'bbox',
        )
        .options(selectinload(Annotation.task_image).selectinload(TaskImage.image))
        .order_by(Annotation.id)
    )

    images: dict[int, object] = {}
    annotations: list[dict] = []
    for ann in db.scalars(stmt).all():
        row = ann.task_image
        image = row.image if row is not None else None
        if image is None:
            continue
        box = _box_to_coco((ann.payload or {}).get('box'))
        if box is None:
            continue
        category_id = ann.category_id
        if category_id is None or category_id not in category_by_id:
            continue
        images.setdefault(image.id, image)
        annotations.append(
            {
                'id': ann.id,
                'image_id': image.id,
                'category_id': category_id,
                'bbox': box,
                'area': round(box[2] * box[3], 2),
                'iscrowd': 0,
                'score': ann.confidence,
                'task_id': row.task_id,
                'task_image_id': row.id,
                'revision': ann.revision,
                'source': ann.source,
            }
        )

    return {
        'info': {
            'description': f'ImageLabel project {project_id} annotations',
            'project_id': project_id,
            'version': '1.0',
            'exported_at': utcnow().isoformat(),
        },
        'licenses': [],
        'images': [
            {
                'id': image.id,
                'file_name': image.filename,
                'width': image.width,
                'height': image.height,
            }
            for image in images.values()
        ],
        'annotations': annotations,
        'categories': [
            {
                'id': c.id,
                'name': c.zh_name,
                'en_name': c.en_name,
                'supercategory': 'default',
            }
            for c in categories
        ],
    }
