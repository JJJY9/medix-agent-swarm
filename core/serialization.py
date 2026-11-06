"""JSON 安全处理：移除无法被标准 JSON 表示的浮点值与孤立代理码位。"""
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


def contains_lone_surrogate(text: str) -> bool:
    """判断字符串是否含 UTF-16 代理码位（无法编码为 UTF-8）。"""
    return any(0xD800 <= ord(ch) <= 0xDFFF for ch in text)


def strip_lone_surrogates(value: Any) -> Any:
    """递归把孤立代理码位替换为 U+FFFD，保证内容能编码为 UTF-8。"""
    if isinstance(value, str):
        if not contains_lone_surrogate(value):
            return value
        return "".join(
            "�" if 0xD800 <= ord(ch) <= 0xDFFF else ch for ch in value
        )
    if isinstance(value, dict):
        return {
            strip_lone_surrogates(str(key)): strip_lone_surrogates(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [strip_lone_surrogates(item) for item in value]
    return value
