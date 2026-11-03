"""JSON 安全处理：移除无法被标准 JSON 表示的浮点值。"""
from __future__ import annotations

import math
from typing import Any


def sanitize_json(value: Any) -> Any:
    """递归把 NaN / Infinity 转成 None，避免响应或落库时序列化失败。"""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): sanitize_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_json(item) for item in value]
    return value
