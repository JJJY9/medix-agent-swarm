"""简单的进程内滑动窗口限流。

用于保护会诊接口，避免误触或局域网内的高频请求持续消耗模型额度。
默认每分钟 30 次；设置 ``MEDIX_RATE_LIMIT=0`` 可关闭。
"""
from __future__ import annotations

import os
import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, Optional, Tuple

RATE_LIMIT_ENV = "MEDIX_RATE_LIMIT"
DEFAULT_RATE_LIMIT = 30
WINDOW_SECONDS = 60.0


def get_rate_limit() -> int:
    """读取每分钟允许的会诊请求数；非法值回退到默认值。"""
    raw = (os.getenv(RATE_LIMIT_ENV) or "").strip()
    if not raw:
        return DEFAULT_RATE_LIMIT
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_RATE_LIMIT


class SlidingWindowLimiter:
    """按 key 统计时间窗口内的请求数。"""

    #: 内存上限：地址轮换不能无限扩大 _hits
    MAX_KEYS = 10000

    def __init__(self, limit: int, window: float = WINDOW_SECONDS):
        self.limit = max(0, int(limit))
        self.window = float(window)
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune_keys(self) -> None:
        """先清理空窗口，再按插入顺序淘汰最早的一批 key。"""
        empty = [key for key, hits in self._hits.items() if not hits]
        for key in empty[: max(1, self.MAX_KEYS // 10)]:
            self._hits.pop(key, None)
        while len(self._hits) >= self.MAX_KEYS:
            oldest = next(iter(self._hits))
            self._hits.pop(oldest, None)

    def check(self, key: str, now: Optional[float] = None) -> Tuple[bool, float]:
        """返回 (是否允许, 建议等待秒数)。limit<=0 时始终放行。"""
        if self.limit <= 0:
            return True, 0.0

        moment = time.monotonic() if now is None else now
        with self._lock:
            if key not in self._hits and len(self._hits) >= self.MAX_KEYS:
                self._prune_keys()
            hits = self._hits[key]
            while hits and moment - hits[0] >= self.window:
                hits.popleft()

            if len(hits) >= self.limit:
                retry_after = max(0.0, self.window - (moment - hits[0]))
                return False, retry_after

            hits.append(moment)
            return True, 0.0

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()
