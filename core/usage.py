"""Token 用量的合并工具。"""
from __future__ import annotations

from typing import Any, Dict, Optional

USAGE_KEYS = ("prompt_tokens", "completion_tokens", "total_tokens")


def merge_usage(
    total: Dict[str, int],
    usage: Optional[Dict[str, Any]]
) -> Dict[str, int]:
    """把一次调用的 usage 累加到总用量中。"""
    if not usage:
        return total

    for key in USAGE_KEYS:
        try:
            value = int(usage.get(key) or 0)
        except (TypeError, ValueError):
            value = 0
        total[key] = total.get(key, 0) + value
    return total


def merge_usages(*usages: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """合并多份 usage。"""
    total: Dict[str, int] = {}
    for usage in usages:
        merge_usage(total, usage)
    return total
