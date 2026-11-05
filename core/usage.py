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

    values: Dict[str, int] = {}
    for key in ("prompt_tokens", "completion_tokens"):
        try:
            values[key] = int(usage.get(key) or 0)
        except (TypeError, ValueError, OverflowError):
            values[key] = 0

    raw_total = usage.get("total_tokens")
    try:
        values["total_tokens"] = int(raw_total or 0)
    except (TypeError, ValueError, OverflowError):
        values["total_tokens"] = 0

    # 部分客户端/备份只记录 prompt+completion；没有可信 total 时补算，
    # 否则预算进度会显示 0 而费用列却有值
    if (
        not values["total_tokens"]
        and raw_total in (None, "", 0, "0")
        and (values["prompt_tokens"] or values["completion_tokens"])
    ):
        values["total_tokens"] = (
            values["prompt_tokens"] + values["completion_tokens"]
        )

    for key in USAGE_KEYS:
        total[key] = total.get(key, 0) + values[key]
    return total


def merge_usages(*usages: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """合并多份 usage。"""
    total: Dict[str, int] = {}
    for usage in usages:
        merge_usage(total, usage)
    return total
