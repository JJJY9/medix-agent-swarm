"""来源与科室意见的合并工具。"""
from __future__ import annotations

import math
from typing import Any, Dict, Iterable, List


def _score(item: Dict[str, Any]) -> float:
    try:
        value = float(item.get("score") or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def merge_sources(
    sources: Iterable[Dict[str, Any]],
    limit: int = 5
) -> List[Dict[str, Any]]:
    """按来源/文件/片段去重，保留相关度最高的一批。"""
    # 同一 key 保留分数最高的一条：先去重再排序会留下先出现的低分副本，
    # 真正高相关的来源反而被挤掉
    best: Dict[tuple, Dict[str, Any]] = {}
    for source in sources:
        if not isinstance(source, dict):
            continue
        key = (
            source.get("source") or "",
            source.get("filename") or "",
            source.get("snippet") or "",
        )
        current = best.get(key)
        if current is None or _score(source) > _score(current):
            best[key] = source

    merged = sorted(best.values(), key=_score, reverse=True)
    return merged[:limit]
