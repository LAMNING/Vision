from sqlalchemy.orm import Session

from app.data.crud.project import UNSET, get_project_or_raise
from app.data.project_auto_config import (
    DEFAULT_AUTO_ANNOTATE_ENABLED,
    DEFAULT_BOX_THRESHOLD,
    DEFAULT_TEXT_THRESHOLD,
    DEFAULT_TOPK,
    DEFAULT_TRANSLATE_ENABLED,
    DEFAULT_TRANSLATE_STYLE,
    TRANSLATE_STYLES,
    ProjectAutoConfig,
)
from app.errors import InvalidAutoConfigError


def get_config(db: Session, project_id: int) -> ProjectAutoConfig | None:
    return db.get(ProjectAutoConfig, project_id)


def _clean_threshold(name: str, value: float | None) -> float:
    if value is None:
        raise InvalidAutoConfigError(f'{name} 不能为 null')
    value = float(value)
    if not 0.0 <= value <= 1.0:
        raise InvalidAutoConfigError(f'{name} 必须在 0 到 1 之间')
    return value


def _clean_top_k(value: int | None) -> int | None:
    if value is None:
        return None
    value = int(value)
    if value < 1:
        raise InvalidAutoConfigError('top_k 必须 >= 1（或传 null 表示不限）')
    return value


def _clean_style(value: str) -> str:
    if value not in TRANSLATE_STYLES:
        raise InvalidAutoConfigError(
            f'translate_style 必须是 {TRANSLATE_STYLES} 之一，收到 {value!r}'
        )
    return value


def get_or_create_config(db: Session, project_id: int) -> ProjectAutoConfig:
    get_project_or_raise(db, project_id)
    config = get_config(db, project_id)
    if config is None:
        config = ProjectAutoConfig(
            project_id=project_id,
            auto_annotate_enabled=DEFAULT_AUTO_ANNOTATE_ENABLED,
            box_threshold=DEFAULT_BOX_THRESHOLD,
            text_threshold=DEFAULT_TEXT_THRESHOLD,
            top_k=DEFAULT_TOPK,
            translate_enabled=DEFAULT_TRANSLATE_ENABLED,
            translate_style=DEFAULT_TRANSLATE_STYLE,
        )
        db.add(config)
        db.commit()
        db.refresh(config)
    return config


def update_config(
    db: Session,
    project_id: int,
    *,
    auto_annotate_enabled: bool | None = None,
    box_threshold: float | None | object = UNSET,
    text_threshold: float | None | object = UNSET,
    top_k: int | None | object = UNSET,
    translate_enabled: bool | None = None,
    translate_style: str | None = None,
) -> ProjectAutoConfig:
    config = get_or_create_config(db, project_id)

    if auto_annotate_enabled is not None:
        config.auto_annotate_enabled = bool(auto_annotate_enabled)

    if box_threshold is not UNSET:
        config.box_threshold = _clean_threshold('box_threshold', box_threshold)  # type: ignore[arg-type]

    if text_threshold is not UNSET:
        config.text_threshold = _clean_threshold('text_threshold', text_threshold)  # type: ignore[arg-type]

    if top_k is not UNSET:
        config.top_k = _clean_top_k(top_k)  # type: ignore[arg-type]

    if translate_enabled is not None:
        config.translate_enabled = bool(translate_enabled)

    if translate_style is not None:
        config.translate_style = _clean_style(translate_style)

    db.commit()
    db.refresh(config)
    return config
