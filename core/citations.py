"""来源与科室意见的合并工具。"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List


def merge_sources(
    sources: Iterable[Dict[str, Any]],
    limit: int = 5
) -> List[Dict[str, Any]]:
    """按来源/文件/片段去重，保留相关度最高的一批。"""
    merged: List[Dict[str, Any]] = []
    seen = set()

    for source in sources:
        if not isinstance(source, dict):
            continue
        key = (
            source.get("source") or "",
            source.get("filename") or "",
            source.get("snippet") or "",
        )
        if key in seen:
            continue
        seen.add(key)
        merged.append(source)

    merged.sort(key=lambda item: float(item.get("score") or 0.0), reverse=True)
    return merged[:limit]
