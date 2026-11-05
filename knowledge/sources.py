"""把知识库检索结果整理成前端可展示的来源卡片。"""
from __future__ import annotations

import math
from typing import Any, Dict, List


def _score(doc: Dict[str, Any]) -> float:
    try:
        value = float(doc.get("score") or 0.0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    return value if math.isfinite(value) else 0.0


def build_sources(results: List[Dict[str, Any]], max_items: int = 3) -> List[Dict[str, Any]]:
    """从 kb.search() 结果中提取来源、相关度与原文片段。

    同一文档的多个向量块只保留得分最高的一块，避免来源卡片被
    同一篇长文档占满。
    """
    best: Dict[str, Dict[str, Any]] = {}
    for doc in results:
        if not isinstance(doc, dict):
            continue
        metadata = doc.get("metadata") or {}
        key = str(
            metadata.get("doc_id")
            or metadata.get("filename")
            or doc.get("id")
            or ""
        )
        current = best.get(key)
        if current is None or _score(doc) > _score(current):
            best[key] = doc

    ordered = sorted(best.values(), key=_score, reverse=True)

    sources: List[Dict[str, Any]] = []
    for doc in ordered[:max_items]:
        metadata = doc.get("metadata") or {}
        content = (doc.get("content") or "").strip()
        sources.append({
            "title": metadata.get("disease") or metadata.get("filename") or "医学知识库",
            "source": metadata.get("source") or "医学知识库",
            "filename": metadata.get("filename") or "",
            "type": metadata.get("type") or "",
            "score": round(_score(doc), 4),
            "snippet": content[:160],
        })
    return sources
