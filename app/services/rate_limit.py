"""进程内滑动窗口限流（登录/注册用）。

单实例够用；多实例部署时每个实例各自计数（要全局精确就换 Redis，
接口约定不变：``check(action, key)`` 不合格抛 ``TooManyRequestsError``）。

环境变量：
    IMAGELABEL_SIGNIN_LIMIT   窗口内允许的登录尝试次数，默认 10
    IMAGELABEL_SIGNIN_WINDOW  窗口秒数，默认 60
"""

from __future__ import annotations

import os
import threading
import time
from collections import deque

from app.errors import TooManyRequestsError

LIMIT_ENV = 'IMAGELABEL_SIGNIN_LIMIT'
WINDOW_ENV = 'IMAGELABEL_SIGNIN_WINDOW'

DEFAULT_LIMIT = 10
DEFAULT_WINDOW = 60.0


def _int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return max(minimum, int(raw))
    except (TypeError, ValueError):
        return default


def signin_limit() -> int:
    return _int_env(LIMIT_ENV, DEFAULT_LIMIT)


def signin_window() -> float:
    raw = os.environ.get(WINDOW_ENV)
    if raw is None:
        return DEFAULT_WINDOW
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return DEFAULT_WINDOW


class SlidingWindowLimiter:
    '''按 key 记时间戳，窗口内超过 limit 次就拒绝。'''

    def __init__(self, limit: int, window: float) -> None:
        self.limit = max(1, int(limit))
        self.window = max(1.0, float(window))
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, key: str, now: float | None = None) -> tuple[bool, float]:
        '''返回 (是否放行, 需要等待的秒数)。'''
        now = time.monotonic() if now is None else now
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            cutoff = now - self.window
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = bucket[0] + self.window - now
                return False, max(0.0, retry_after)
            bucket.append(now)
            return True, 0.0

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


_limiters: dict[str, SlidingWindowLimiter] = {}
_limiters_lock = threading.Lock()


def _limiter(action: str) -> SlidingWindowLimiter:
    with _limiters_lock:
        limiter = _limiters.get(action)
        if limiter is None:
            limiter = SlidingWindowLimiter(signin_limit(), signin_window())
            _limiters[action] = limiter
        return limiter


def check(action: str, key: str) -> None:
    '''记一次尝试；超限抛 TooManyRequestsError（带 retry_after）。'''
    ok, retry_after = _limiter(action).hit(key)
    if not ok:
        raise TooManyRequestsError(
            f'尝试太频繁，请 {max(1, round(retry_after))} 秒后再试',
            retry_after=round(retry_after),
        )


def clear(action: str, key: str) -> None:
    '''登录成功后清掉该 key 的计数，避免正常用户被自己刷限。'''
    with _limiters_lock:
        limiter = _limiters.get(action)
    if limiter is not None:
        limiter.reset(key)


def reset(action: str | None = None) -> None:
    '''测试/运维用：清空计数。'''
    if action is None:
        with _limiters_lock:
            for limiter in _limiters.values():
                limiter.reset()
        return
    _limiter(action).reset()
