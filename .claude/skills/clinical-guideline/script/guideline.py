"""
Clinical Guideline Skill
临床指南检索 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
import asyncio
import re
from loguru import logger
from knowledge.sources import (
    build_sources,
    sanitize_untrusted_text,
    wrap_source_snippets,
    wrap_untrusted_source,
)

# 全局知识库实例
_kb_instance = None

def get_knowledge_base():
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
        from knowledge.service import ensure_knowledge_index_fresh
        ensure_knowledge_index_fresh(_kb_instance)
    return _kb_instance


async def clinical_guideline(query: str, max_results: int = 1) -> Dict[str, Any]:
    """
    检索临床指南

    Args:
        query: 查询内容（疾病名称或治疗主题）
        max_results: 最大结果数（默认1，仅返回最相关的指南）

    Returns:
        {
            "answer": "格式化的临床指南信息",
            "guideline_title": "指南标题",
            "organization": "发布机构"
        }
    """
    logger.info(
        "Searching clinical guidelines: "
        f"query_length={len(str(query or ''))}, max_results={max_results}"
    )

    try:
        max_results = int(max_results)
    except (TypeError, ValueError):
        max_results = 1
    max_results = max(1, min(max_results, 10))

    # 使用知识库单例
    kb = await asyncio.to_thread(get_knowledge_base)

    # 使用 Milvus 检索临床指南
    results = await asyncio.to_thread(kb.search,
        query=f"{query} 临床指南 诊疗规范",
        top_k=max_results,  # 使用传入的 max_results 参数
        filter_type="clinical_guideline"
    )

    if results and results[0]["score"] > 0.1:
        doc = results[0]
        metadata = doc["metadata"]
        # 只有首块包含标题与发布信息；后续块从中间截断，解析出的“标题”
        # 会是正文片段，因此只在 chunk 0 上解析
        try:
            chunk_id = int(metadata.get("chunk_id") or 0)
        except (TypeError, ValueError):
            chunk_id = 0
        parsed = (
            parse_guideline_meta(doc["content"])
            if chunk_id == 0
            else {"title": "", "organization": "", "year": ""}
        )
        # max_results 是调用方要求的条数，全部格式化而不是只返回第一条
        answer_parts = [
            format_guideline(
                item["content"], item["metadata"], index=index
            )
            for index, item in enumerate(results[:max_results], 1)
            if isinstance(item, dict) and item.get("content")
        ]

        return {
            "answer": "\n\n".join(answer_parts),
            "guideline_title": sanitize_untrusted_text(
                parsed["title"]
                or f"{metadata.get('disease', query)}相关临床指南",
                120,
            ),
            "organization": sanitize_untrusted_text(
                parsed["organization"]
                or metadata.get("organization", "N/A"),
                120,
            ),
            "year": parsed["year"] or metadata.get("year", "N/A"),
            "source": "向量数据库",
            "sources": wrap_source_snippets(
                build_sources(results, max_items=max_results)
            ),
        }
    else:
        # 未找到相关内容
        logger.warning(
            "No clinical guidelines found in vector DB "
            f"(query_length={len(str(query or ''))})"
        )
        return {
            "answer": f"未找到'{query}'的相关临床指南，建议使用更具体的疾病名称或联系专业机构获取权威指南。",
            "guideline_title": "",
            "organization": "",
            "source": "未找到",
            "sources": [],
        }


def parse_guideline_meta(content: str) -> Dict[str, str]:
    """从指南正文解析真实标题、发布机构与年份。"""
    lines = [line.strip() for line in str(content or "").splitlines() if line.strip()]
    title = lines[0] if lines else ""
    organization = ""
    year = ""
    for line in lines[:20]:
        if line.startswith("发布机构"):
            organization = re.split(r"[：:]", line, 1)[-1].strip()
        elif line.startswith("发布年份"):
            year = re.split(r"[：:]", line, 1)[-1].strip()
    return {
        "title": title,
        "organization": organization,
        "year": year,
    }


def format_guideline(
    content: str, metadata: Dict[str, Any], index: int = 1
) -> str:
    """格式化临床指南信息（非首块不解析正文标题）。"""
    try:
        chunk_id = int(metadata.get("chunk_id") or 0)
    except (TypeError, ValueError):
        chunk_id = 0
    parsed = (
        parse_guideline_meta(content)
        if chunk_id == 0
        else {"title": "", "organization": "", "year": ""}
    )
    disease = metadata.get("disease", "N/A")
    output = [
        "【临床诊疗指南】\n",
        f"指南名称：{sanitize_untrusted_text(parsed['title'], 120) or f'{disease}相关临床指南'}",
        f"发布机构：{sanitize_untrusted_text(parsed['organization'], 120) or metadata.get('organization', 'N/A')}",
        f"发布年份：{parsed['year'] or metadata.get('year', 'N/A')}",
        f"\n内容：\n{wrap_untrusted_source(content, index=index)}"
    ]

    return "\n".join(output)


def clinical_guideline_sync(query: str, max_results: int = 1) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(clinical_guideline(query, max_results))
