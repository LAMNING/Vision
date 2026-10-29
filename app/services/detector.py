"""Lazy access to the vendored LLMDet runtime (detector + Opus-MT translator).

Model files are NOT in the repo (1.31 GB fp32 / 0.68 GB fp16 ONNX, 425 MB /
212 MB Marian). Point the environment at them, or let a platform admin save
the same fields in the DB (admin UI wins over env):

    IMAGELABEL_LLMDET_DIR=D:\\Ideas\\onnx-runtime\\onnx     # + precision picks file
    # or an explicit file:
    IMAGELABEL_LLMDET_ONNX=D:\\Ideas\\onnx-runtime\\onnx\\llmdet_large_dynamic_fp16.onnx
    IMAGELABEL_LLMDET_PRECISION=fp16     # fp32 | fp16  (default fp16)
    IMAGELABEL_LLMDET_DEVICE=cpu         # cpu | cuda | auto (default cpu)
    IMAGELABEL_MARIAN_DIR=D:\\Ideas\\onnx-runtime\\models\\opus-mt-zh-en
    IMAGELABEL_MARIAN_PRECISION=fp16     # fp32 | fp16 (default fp32)

Everything is loaded on first use and cached, so importing this module costs
nothing and a server without models still boots (task-run fails with a clear
message instead).
"""
from __future__ import annotations

import os
from functools import lru_cache

from app.services.model import LLMDet

DETECTOR_ONNX_ENV = 'IMAGELABEL_LLMDET_ONNX'
DETECTOR_DIR_ENV = 'IMAGELABEL_LLMDET_DIR'
DETECTOR_PRECISION_ENV = 'IMAGELABEL_LLMDET_PRECISION'
DETECTOR_DEVICE_ENV = 'IMAGELABEL_LLMDET_DEVICE'
DETECTOR_SIZE_ENV = 'IMAGELABEL_LLMDET_SIZE'
DETECTOR_MAX_SIZE_ENV = 'IMAGELABEL_LLMDET_MAX_SIZE'
TRANSLATOR_DIR_ENV = 'IMAGELABEL_MARIAN_DIR'
TRANSLATOR_PRECISION_ENV = 'IMAGELABEL_MARIAN_PRECISION'

DEFAULT_PRECISION = 'fp16'
DEFAULT_DEVICE = 'cpu'
DEFAULT_SIZE = 800
DEFAULT_MAX_SIZE = 1333
DEFAULT_TRANSLATOR_PRECISION = 'fp32'

PRECISIONS = ('fp32', 'fp16')
DEVICES = ('cpu', 'cuda', 'auto')
TRANSLATOR_PRECISIONS = ('fp32', 'fp16')

# Same file names as D:\\Ideas\\onnx-runtime\\runtime\\detector.py.
MODEL_FILES = {
    'fp32': 'llmdet_large_dynamic.onnx',
    'fp16': 'llmdet_large_dynamic_fp16.onnx',
}

# 平台管理员在 DB 里保存的覆盖值（None/缺省 = 跟随环境变量）。
_override: dict[str, object] = {}


class DetectorNotConfiguredError(RuntimeError):
    """No detection model path is configured."""


class TranslatorNotConfiguredError(RuntimeError):
    """Auto-config asked for translation but no Marian checkpoint is configured."""


# --------------------------------------------------------------------------- #
# overrides (admin-configured values win over env)
# --------------------------------------------------------------------------- #
def set_override_config(values: dict[str, object] | None) -> None:
    """Apply DB-saved model config; None/empty values fall back to env."""
    global _override
    _override = {
        key: value
        for key, value in (values or {}).items()
        if value is not None and value != ''
    }
    reset_detector_cache()


def override_config() -> dict[str, object]:
    return dict(_override)


def _value(env_name: str, override_key: str, default: str | None = None) -> str | None:
    value = _override.get(override_key)
    if value is not None:
        return str(value)
    raw = os.environ.get(env_name)
    return default if raw is None else raw


def _value_int(env_name: str, override_key: str, default: int) -> int:
    value = _override.get(override_key)
    raw = str(value) if value is not None else os.environ.get(env_name, '').strip()
    raw = raw.strip() if isinstance(raw, str) else raw
    if raw in (None, ''):
        return default
    try:
        return int(raw)
    except (TypeError, ValueError) as exc:
        raise DetectorNotConfiguredError(
            f'{env_name} must be an integer, got {raw!r}'
        ) from exc


# --------------------------------------------------------------------------- #
# detection model
# --------------------------------------------------------------------------- #
def detector_precision() -> str:
    precision = str(_value(DETECTOR_PRECISION_ENV, 'detector_precision', DEFAULT_PRECISION)).strip().lower()
    if precision not in MODEL_FILES:
        raise DetectorNotConfiguredError(
            f'{DETECTOR_PRECISION_ENV} must be one of {sorted(MODEL_FILES)}, got {precision!r}'
        )
    return precision


def detector_device() -> str:
    device = str(_value(DETECTOR_DEVICE_ENV, 'detector_device', DEFAULT_DEVICE)).strip().lower()
    if device not in DEVICES:
        raise DetectorNotConfiguredError(
            f'{DETECTOR_DEVICE_ENV} must be "cpu", "cuda" or "auto", got {device!r}'
        )
    return device


def detector_size() -> int:
    return _value_int(DETECTOR_SIZE_ENV, 'detector_size', DEFAULT_SIZE)


def detector_max_size() -> int:
    return _value_int(DETECTOR_MAX_SIZE_ENV, 'detector_max_size', DEFAULT_MAX_SIZE)


def detector_path() -> str | None:
    """Resolve the ONNX file: explicit path wins, then dir + precision file name."""
    explicit = str(_value(DETECTOR_ONNX_ENV, 'detector_onnx', '') or '').strip()
    if explicit:
        return explicit
    directory = str(_value(DETECTOR_DIR_ENV, 'detector_dir', '') or '').strip()
    if directory:
        return os.path.join(directory, MODEL_FILES[detector_precision()])
    return None


def detector_configured() -> bool:
    return bool(detector_path())


@lru_cache(maxsize=1)
def get_detector() -> LLMDet:
    """Load (once per process) the LLMDet ONNX session; CPU-bound for ~20 s/image."""
    path = detector_path()
    if path is None:
        raise DetectorNotConfiguredError(
            f'检测模型未配置：请在平台管理里设置模型，或设置 {DETECTOR_ONNX_ENV}（onnx 文件）'
            f' 或 {DETECTOR_DIR_ENV}（onnx 所在目录，按 {DETECTOR_PRECISION_ENV} 选文件）'
        )
    if not os.path.isfile(path):
        raise DetectorNotConfiguredError(f'检测模型文件不存在：{path}')
    return LLMDet(
        onnx_path=path,
        precision=detector_precision(),
        device=detector_device(),
        size=detector_size(),
        max_size=detector_max_size(),
    )


# --------------------------------------------------------------------------- #
# translator (Marian zh->en)
# --------------------------------------------------------------------------- #
def translator_precision() -> str:
    precision = str(
        _value(TRANSLATOR_PRECISION_ENV, 'translator_precision', DEFAULT_TRANSLATOR_PRECISION)
    ).strip().lower()
    return precision if precision in TRANSLATOR_PRECISIONS else DEFAULT_TRANSLATOR_PRECISION


def translator_path() -> str | None:
    directory = str(_value(TRANSLATOR_DIR_ENV, 'translator_dir', '') or '').strip()
    return directory or None


def translator_configured() -> bool:
    return bool(translator_path())


@lru_cache(maxsize=1)
def get_translator():
    """Load (once per process) the Opus-MT zh->en ONNX translator."""
    directory = translator_path()
    if directory is None:
        raise TranslatorNotConfiguredError(
            f'翻译模型未配置：请在平台管理里设置模型，或设置 {TRANSLATOR_DIR_ENV}'
            '（Marian 目录，含 onnx/encoder_model.onnx）'
        )
    if not os.path.isdir(directory):
        raise TranslatorNotConfiguredError(f'翻译模型目录不存在：{directory}')
    from app.services.model import MarianTranslator

    return MarianTranslator(directory, precision=translator_precision())


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #
def detector_status() -> dict:
    """Small dict for /health-style diagnostics (never loads the session)."""
    path = detector_path()
    return {
        'configured': path is not None,
        'path': path,
        'precision': detector_precision() if path else None,
        'device': detector_device(),
        'size': detector_size(),
        'max_size': detector_max_size(),
        'loaded': get_detector.cache_info().currsize > 0,
        'translator_configured': translator_configured(),
        'translator_dir': translator_path(),
        'translator_precision': translator_precision(),
        'translator_loaded': get_translator.cache_info().currsize > 0,
    }


def reset_detector_cache() -> None:
    """Drop cached sessions (tests / config change)."""
    get_detector.cache_clear()
    get_translator.cache_clear()
