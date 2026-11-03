"""把知识库检索结果整理成前端可展示的来源卡片。"""
from __future__ import annotations

from typing import Any, Dict, List


def build_sources(results: List[Dict[str, Any]], max_items: int = 3) -> List[Dict[str, Any]]:
    """从 kb.search() 结果中提取来源、相关度与原文片段。"""
    sources: List[Dict[str, Any]] = []
    for doc in results[:max_items]:
        metadata = doc.get("metadata") or {}
        content = (doc.get("content") or "").strip()
        sources.append({
            "title": metadata.get("disease") or metadata.get("filename") or "医学知识库",
            "source": metadata.get("source") or "医学知识库",
            "filename": metadata.get("filename") or "",
            "type": metadata.get("type") or "",
            "score": round(float(doc.get("score") or 0.0), 4),
            "snippet": content[:160],
        })
    return sources
