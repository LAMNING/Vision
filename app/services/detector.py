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

import gc
import logging
import os
import threading
import time
from functools import lru_cache

from app.services.model import LLMDet

logger = logging.getLogger(__name__)

DETECTOR_ONNX_ENV = 'IMAGELABEL_LLMDET_ONNX'
DETECTOR_DIR_ENV = 'IMAGELABEL_LLMDET_DIR'
DETECTOR_PRECISION_ENV = 'IMAGELABEL_LLMDET_PRECISION'
DETECTOR_DEVICE_ENV = 'IMAGELABEL_LLMDET_DEVICE'
DETECTOR_SIZE_ENV = 'IMAGELABEL_LLMDET_SIZE'
DETECTOR_MAX_SIZE_ENV = 'IMAGELABEL_LLMDET_MAX_SIZE'
TRANSLATOR_DIR_ENV = 'IMAGELABEL_MARIAN_DIR'
TRANSLATOR_PRECISION_ENV = 'IMAGELABEL_MARIAN_PRECISION'
# 多久没有推理就把模型卸掉（秒）；0 = 常驻不卸载。
IDLE_RELEASE_ENV = 'IMAGELABEL_MODEL_IDLE_SECONDS'

DEFAULT_PRECISION = 'fp16'
DEFAULT_DEVICE = 'cpu'
DEFAULT_SIZE = 800
DEFAULT_MAX_SIZE = 1333
DEFAULT_TRANSLATOR_PRECISION = 'fp32'
# 默认 3 分钟：这台 16G 机器上检测模型常驻约 1.2–1.5G，留着会把可用内存吃光，
# 下一张图就 "bad allocation"。空闲超过这个时间就把 session 放掉。
DEFAULT_IDLE_RELEASE_SECONDS = 180.0

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

# 空闲卸载的状态：最后一次真正跑推理的 monotonic 时间，以及上次释放的时间。
# 用锁是因为 worker 线程和可能的接口线程都会碰它。
_idle_lock = threading.Lock()
_last_used = 0.0
_last_released_at = 0.0


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
# idle release (free RAM when nobody is annotating)
# --------------------------------------------------------------------------- #
def idle_release_seconds() -> float:
    '''多久没有推理就卸载模型；0 表示常驻（不自动释放）。

    这台机器只有 16G，检测模型（fp16）载入后常驻 1.2–1.5G，人一多就容易
    在 GridSample/MatMul 上 bad allocation。空闲时把 session 放掉最稳。
    '''
    raw = os.environ.get(IDLE_RELEASE_ENV, '').strip()
    if not raw:
        return DEFAULT_IDLE_RELEASE_SECONDS
    try:
        value = float(raw)
    except (TypeError, ValueError):
        logger.warning('%s=%r 不是数字，按默认 %.0fs', IDLE_RELEASE_ENV, raw,
                       DEFAULT_IDLE_RELEASE_SECONDS)
        return DEFAULT_IDLE_RELEASE_SECONDS
    return max(0.0, value)


def mark_models_used() -> None:
    '''记录「刚刚用过模型」。推理前后都要调，否则空闲计时会从加载那刻算起。'''
    global _last_used
    with _idle_lock:
        _last_used = time.monotonic()


def models_loaded() -> bool:
    return (
        get_detector.cache_info().currsize > 0
        or get_translator.cache_info().currsize > 0
    )


def models_idle_for() -> float | None:
    '''已经空闲多少秒；从没跑过推理时返回 None。'''
    with _idle_lock:
        if _last_used <= 0:
            return None
        return max(0.0, time.monotonic() - _last_used)


def _rss_mb() -> float:
    '''当前进程常驻内存（MB）；取不到就返回 0，只影响日志。'''
    try:
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes

            class _Counters(ctypes.Structure):
                _fields_ = [
                    ('cb', wintypes.DWORD),
                    ('PageFaultCount', wintypes.DWORD),
                    ('PeakWorkingSetSize', ctypes.c_size_t),
                    ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t),
                    ('PeakPagefileUsage', ctypes.c_size_t),
                ]

            psapi = ctypes.windll.psapi
            kernel32 = ctypes.windll.kernel32
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE, ctypes.POINTER(_Counters), wintypes.DWORD,
            ]
            psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
            kernel32.GetCurrentProcess.restype = wintypes.HANDLE
            counters = _Counters()
            counters.cb = ctypes.sizeof(counters)
            ok = psapi.GetProcessMemoryInfo(
                kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
            )
            return (counters.WorkingSetSize / 1048576.0) if ok else 0.0
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except Exception:  # 只用于日志，绝不能让卸载流程因此失败
        return 0.0


def release_idle_models(*, now: float | None = None) -> list[str]:
    '''空闲超过阈值就丢掉已加载的 session；返回被释放的模型名。

    调用点必须是「确定当前没有推理在跑」的地方（worker 主循环）。释放后
    下一次标注要重新加载（本机约 20–40 秒），这是拿回 1G+ 内存的代价。
    '''
    limit = idle_release_seconds()
    if limit <= 0:
        return []

    global _last_used, _last_released_at
    with _idle_lock:
        if not models_loaded() or _last_used <= 0:
            return []
        idle = (time.monotonic() if now is None else now) - _last_used
        if idle < limit:
            return []

        before = _rss_mb()
        released: list[str] = []
        if get_detector.cache_info().currsize > 0:
            get_detector.cache_clear()
            released.append('detector')
        if get_translator.cache_info().currsize > 0:
            get_translator.cache_clear()
            released.append('translator')
        _last_used = 0.0
        _last_released_at = time.monotonic()

    # 锁外回收：onnxruntime 的 arena 要等引用归零 + gc 才真正还给系统。
    gc.collect()
    after = _rss_mb()
    logger.info(
        '模型空闲 %.0fs（阈值 %.0fs），已释放 %s：进程内存 %.0f MB -> %.0f MB',
        idle, limit, '/'.join(released) or '无', before, after,
    )
    return released


# --------------------------------------------------------------------------- #
# diagnostics
# --------------------------------------------------------------------------- #
def detector_status() -> dict:
    """Small dict for /health-style diagnostics (never loads the session)."""
    path = detector_path()
    idle_for = models_idle_for()
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
        'idle_release_seconds': idle_release_seconds(),
        'idle_for_seconds': idle_for,
        'last_release_seconds_ago': (
            max(0.0, time.monotonic() - _last_released_at) if _last_released_at > 0 else None
        ),
    }


def reset_detector_cache() -> None:
    """Drop cached sessions (tests / config change)."""
    global _last_used
    with _idle_lock:
        _last_used = 0.0
    get_detector.cache_clear()
    get_translator.cache_clear()
