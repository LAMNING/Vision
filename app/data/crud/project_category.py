from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data.crud.project import UNSET, get_project_or_raise
from app.data.project_category import ProjectCategory
from app.errors import (
    CategoryNameConflictError,
    CategoryNotFoundError,
    InvalidCategoryError,
)

MAX_ZH_NAME = 50
MAX_EN_NAME = 100


def _translate_en(zh_name: str) -> str:
    from app.services.auto_annotation import as_english_phrase
    from app.services.detector import get_translator, translator_configured

    if not translator_configured():
        raise InvalidCategoryError(
            '没有配置翻译模型（IMAGELABEL_MARIAN_DIR），请直接填英文提示词'
        )
    translated = get_translator().translate_many([zh_name])[0]
    return as_english_phrase(translated)[:MAX_EN_NAME]


def _clean_name(
    value: str, *, field: str, max_len: int, allow_empty: bool = False
) -> str:
    value = (value or '').strip()
    if not value and not allow_empty:
        raise InvalidCategoryError(f'{field} 不能为空')
    if len(value) > max_len:
        raise InvalidCategoryError(f'{field} 过长（上限 {max_len} 字符）')
    return value


def _clean_threshold(value: float | None) -> float | None:
    if value is None:
        return None
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise InvalidCategoryError('threshold_override 必须在 0 到 1 之间')
    return value


def _ensure_name_free(
    db: Session,
    project_id: int,
    *,
    zh_name: str | None = None,
    en_name: str | None = None,
    exclude_id: int | None = None,
) -> None:
    if zh_name is not None:
        stmt = select(ProjectCategory.id).where(
            ProjectCategory.project_id == project_id,
            ProjectCategory.zh_name == zh_name,
        )
        if exclude_id is not None:
            stmt = stmt.where(ProjectCategory.id != exclude_id)
        if db.scalar(stmt):
            raise CategoryNameConflictError(zh_name)

    if en_name is not None:
        stmt = select(ProjectCategory.id).where(
            ProjectCategory.project_id == project_id,
            ProjectCategory.en_name == en_name,
        )
        if exclude_id is not None:
            stmt = stmt.where(ProjectCategory.id != exclude_id)
        if db.scalar(stmt):
            raise CategoryNameConflictError(en_name)


def list_categories(
    db: Session, project_id: int, *, include_disabled: bool = False
) -> list[ProjectCategory]:
    stmt = select(ProjectCategory).where(
        ProjectCategory.project_id == project_id
    )
    if not include_disabled:
        stmt = stmt.where(ProjectCategory.enabled.is_(True))
    stmt = stmt.order_by(ProjectCategory.sort_order, ProjectCategory.id)
    return list(db.scalars(stmt).all())


def get_category(db: Session, category_id: int) -> ProjectCategory | None:
    return db.get(ProjectCategory, category_id)


def get_category_or_raise(db: Session, category_id: int) -> ProjectCategory:
    category = get_category(db, category_id)
    if not category:
        raise CategoryNotFoundError(category_id)
    return category


def create_category(
    db: Session,
    *,
    project_id: int,
    zh_name: str,
    en_name: str = '',
    threshold_override: float | None = None,
    sort_order: int = 0,
    enabled: bool = True,
) -> ProjectCategory:
    get_project_or_raise(db, project_id)

    zh_name = _clean_name(zh_name, field='zh_name', max_len=MAX_ZH_NAME)
    en_name = _clean_name(
        en_name,
        field='en_name',
        max_len=MAX_EN_NAME,
        allow_empty=True,
    )
    if not en_name:
        # 只给中文名：用翻译模型补英文检测提示词
        en_name = _translate_en(zh_name)
    threshold_override = _clean_threshold(threshold_override)
    if sort_order < 0:
        raise InvalidCategoryError('sort_order 不能为负数')

    _ensure_name_free(db, project_id, zh_name=zh_name, en_name=en_name)

    category = ProjectCategory(
        project_id=project_id,
        zh_name=zh_name,
        en_name=en_name,
        threshold_override=threshold_override,
        sort_order=sort_order,
        enabled=enabled,
    )
    db.add(category)
    try:
        db.commit()
    except IntegrityError:
        # 并发创建同名类目时唯一约束兜底
        db.rollback()
        raise CategoryNameConflictError(zh_name) from None
    db.refresh(category)
    return category


def update_category(
    db: Session,
    category_id: int,
    *,
    zh_name: str | None = None,
    en_name: str | None = None,
    threshold_override: float | None | object = UNSET,
    sort_order: int | None = None,
    enabled: bool | None = None,
) -> ProjectCategory:
    category = get_category_or_raise(db, category_id)

    if zh_name is not None:
        new_zh = _clean_name(zh_name, field='zh_name', max_len=MAX_ZH_NAME)
        _ensure_name_free(
            db, category.project_id, zh_name=new_zh, exclude_id=category.id
        )
        category.zh_name = new_zh

    if en_name is not None:
        new_en = _clean_name(
            en_name,
            field='en_name',
            max_len=MAX_EN_NAME,
            allow_empty=True,
        )
        if not new_en:
            # 显式传空串：按当前中文名重新翻译
            new_en = _translate_en(category.zh_name)
        _ensure_name_free(
            db, category.project_id, en_name=new_en, exclude_id=category.id
        )
        category.en_name = new_en
    elif zh_name is not None and not (category.en_name or '').strip():
        # 中文改名且英文提示词为空 → 按新中文名补翻译
        category.en_name = _translate_en(category.zh_name)

    if threshold_override is not UNSET:
        category.threshold_override = _clean_threshold(threshold_override)  # type: ignore[assignment]

    if sort_order is not None:
        if sort_order < 0:
            raise InvalidCategoryError('sort_order 不能为负数')
        category.sort_order = sort_order

    if enabled is not None:
        category.enabled = enabled

    db.commit()
    db.refresh(category)
    return category


def delete_category(db: Session, category_id: int) -> None:
    '''硬删除；annotation.category_id 由 FK 的 ON DELETE SET NULL 置空。'''
    category = get_category_or_raise(db, category_id)
    db.delete(category)
    db.commit()
