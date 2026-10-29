import os

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.data import PlatformSetting
from app.data.crud.audit import record as audit_record, snapshot
from app.data.crud.project import UNSET
from app.errors import ValidationError
from app.services import detector, storage
from app.utils.time import utcnow

SETTING_ID = 1

MODEL_OVERRIDE_KEYS = (
    'detector_onnx',
    'detector_dir',
    'detector_precision',
    'detector_device',
    'detector_size',
    'detector_max_size',
    'translator_dir',
    'translator_precision',
)

DETECTOR_PRECISIONS = ('fp32', 'fp16')
DETECTOR_DEVICES = ('cpu', 'cuda', 'auto')
TRANSLATOR_PRECISIONS = ('fp32', 'fp16')

SIZE_MIN, SIZE_MAX = 64, 4096


def read_setting(db: Session) -> PlatformSetting | None:
    return db.get(PlatformSetting, SETTING_ID)


def get_setting(db: Session) -> PlatformSetting:
    '''取平台设置，没有就建默认行。

    并发首次访问会撞车：管理页一次并发拉 storage-policy 和 model-config，
    两个请求都读不到 id=1，都去插入，后提交的那个拿 UNIQUE 冲突。
    这里把冲突当成「对方已经建好」，回滚后直接读那一行。
    '''
    setting = read_setting(db)
    if setting is not None:
        return setting

    setting = PlatformSetting(id=SETTING_ID)
    db.add(setting)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = read_setting(db)
        if existing is None:
            raise
        return existing
    db.refresh(setting)
    return setting


def model_overrides(setting: PlatformSetting | None) -> dict[str, object]:
    """DB 中非空的模型配置（按优先级覆盖环境变量）。"""
    if setting is None:
        return {}
    return {
        key: getattr(setting, key)
        for key in MODEL_OVERRIDE_KEYS
        if getattr(setting, key) is not None
    }


def _check_quota(name: str, value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError(f'{name} 必须是整数')
    if value < 0:
        raise ValidationError(f'{name} 不能为负数')
    return value


def _clean_text(value: object, name: str, *, max_len: int = 255) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if len(text) > max_len:
        raise ValidationError(f'{name} 过长（上限 {max_len} 字符）')
    return text or None


def _clean_choice(value: object, name: str, choices: tuple[str, ...]) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text not in choices:
        raise ValidationError(f'{name} 必须是 {" / ".join(choices)} 之一')
    return text


def _clean_size(value: object, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f'{name} 必须是整数')
    if not SIZE_MIN <= value <= SIZE_MAX:
        raise ValidationError(f'{name} 必须在 {SIZE_MIN}~{SIZE_MAX} 之间')
    return value


def _check_detector_onnx(path: str) -> None:
    if os.path.isdir(path):
        raise ValidationError(
            'detector_onnx 要填 .onnx 文件，不能填目录；目录请填到 detector_dir'
        )
    if not os.path.isfile(path):
        raise ValidationError(f'detector_onnx 文件不存在：{path}')


def _check_detector_dir(path: str, precision: str) -> None:
    if not os.path.isdir(path):
        raise ValidationError(f'detector_dir 目录不存在：{path}')
    file_name = detector.MODEL_FILES[precision]
    if not os.path.isfile(os.path.join(path, file_name)):
        raise ValidationError(
            f'detector_dir 里没有 {file_name}（precision={precision}）：{path}'
        )


def _check_translator_dir(path: str) -> None:
    if not os.path.isdir(path):
        raise ValidationError(f'translator_dir 目录不存在：{path}')
    onnx_dir = os.path.join(path, 'onnx')
    if not os.path.isdir(onnx_dir):
        raise ValidationError(
            'translator_dir 要指向 Marian 模型目录（里面有 onnx/、source.spm、'
            f'vocab.json），不要指到 onnx/ 子目录：{path}'
        )
    for required in ('source.spm', 'vocab.json'):
        if not os.path.isfile(os.path.join(path, required)):
            raise ValidationError(
                f'translator_dir 缺少 {required}，要指向 Marian 模型目录：{path}'
            )


def _validate_detector_effective(setting: PlatformSetting) -> None:
    """保存后真正会被加载的检测模型路径必须存在（onnx 文件优先于目录）。"""
    if setting.detector_onnx:
        _check_detector_onnx(setting.detector_onnx)
    elif setting.detector_dir:
        precision = setting.detector_precision or detector.DEFAULT_PRECISION
        _check_detector_dir(setting.detector_dir, precision)


def _validate_translator_effective(setting: PlatformSetting) -> None:
    if setting.translator_dir:
        _check_translator_dir(setting.translator_dir)


def update_setting(
    db: Session,
    *,
    storage_uri: str | None | object = UNSET,
    team_quota_images: int | object = UNSET,
    team_quota_storage_mb: int | object = UNSET,
    detector_onnx: str | None | object = UNSET,
    detector_dir: str | None | object = UNSET,
    detector_precision: str | None | object = UNSET,
    detector_device: str | None | object = UNSET,
    detector_size: int | None | object = UNSET,
    detector_max_size: int | None | object = UNSET,
    translator_dir: str | None | object = UNSET,
    translator_precision: str | None | object = UNSET,
    actor_account_id: int | None = None,
) -> PlatformSetting:
    setting = get_setting(db)
    before = snapshot(setting)

    if storage_uri is not UNSET:
        value = (storage_uri or '').strip() or None
        if value is not None:
            try:
                value = storage.validate_storage_uri(value)
            except ValueError as exc:
                raise ValidationError(str(exc)) from exc
        setting.storage_uri = value
    if team_quota_images is not UNSET and team_quota_images is not None:
        setting.team_quota_images = _check_quota('team_quota_images', team_quota_images)
    if team_quota_storage_mb is not UNSET and team_quota_storage_mb is not None:
        setting.team_quota_storage_mb = _check_quota(
            'team_quota_storage_mb', team_quota_storage_mb
        )

    if detector_onnx is not UNSET:
        setting.detector_onnx = _clean_text(detector_onnx, 'detector_onnx')
    if detector_dir is not UNSET:
        setting.detector_dir = _clean_text(detector_dir, 'detector_dir')
    if detector_precision is not UNSET:
        setting.detector_precision = _clean_choice(
            detector_precision, 'detector_precision', DETECTOR_PRECISIONS
        )
    if detector_device is not UNSET:
        setting.detector_device = _clean_choice(
            detector_device, 'detector_device', DETECTOR_DEVICES
        )
    if detector_size is not UNSET:
        setting.detector_size = _clean_size(detector_size, 'detector_size')
    if detector_max_size is not UNSET:
        setting.detector_max_size = _clean_size(
            detector_max_size, 'detector_max_size'
        )
    if translator_dir is not UNSET:
        setting.translator_dir = _clean_text(translator_dir, 'translator_dir')
    if translator_precision is not UNSET:
        setting.translator_precision = _clean_choice(
            translator_precision, 'translator_precision', TRANSLATOR_PRECISIONS
        )

    if any(
        value is not UNSET
        for value in (detector_onnx, detector_dir, detector_precision)
    ):
        _validate_detector_effective(setting)
    if any(
        value is not UNSET
        for value in (translator_dir, translator_precision)
    ):
        _validate_translator_effective(setting)

    if (
        setting.detector_size is not None
        and setting.detector_max_size is not None
        and setting.detector_max_size <= setting.detector_size
    ):
        raise ValidationError('detector_max_size 必须大于 detector_size')

    setting.updated_at = utcnow()
    setting.updated_by_account_id = actor_account_id
    db.commit()
    db.refresh(setting)

    audit_record(
        db,
        action='platform.storage_policy_updated',
        resource_type='platform_setting',
        resource_id=setting.id,
        actor_account_id=actor_account_id,
        before=before,
        after=snapshot(setting),
        commit=True,
    )
    return setting
