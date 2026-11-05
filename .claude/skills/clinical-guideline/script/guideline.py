"""
Clinical Guideline Skill
临床指南检索 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
import asyncio
from loguru import logger
from knowledge.sources import build_sources

# 全局知识库实例
_kb_instance = None

def get_knowledge_base():
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
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
    logger.info(f"Searching clinical guidelines for: {query} (max_results={max_results})")

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
        # max_results 是调用方要求的条数，全部格式化而不是只返回第一条
        answer_parts = [
            format_guideline(item["content"], item["metadata"])
            for item in results[:max_results]
            if isinstance(item, dict) and item.get("content")
        ]

        return {
            "answer": "\n\n".join(answer_parts),
            "guideline_title": f"{metadata.get('disease', query)}相关临床指南",
            "organization": metadata.get("organization", "N/A"),
            "year": metadata.get("year", "N/A"),
            "source": "向量数据库",
            "sources": build_sources(results, max_items=max_results),
        }
    else:
        # 未找到相关内容
        logger.warning(f"No clinical guidelines found in vector DB for {query}")
        return {
            "answer": f"未找到'{query}'的相关临床指南，建议使用更具体的疾病名称或联系专业机构获取权威指南。",
            "guideline_title": "",
            "organization": "",
            "source": "未找到",
            "sources": [],
        }


def format_guideline(content: str, metadata: Dict[str, Any]) -> str:
    """格式化临床指南信息"""
    output = [
        "【临床诊疗指南】\n",
        f"指南名称：{metadata.get('disease', 'N/A')}相关临床指南",
        f"发布机构：{metadata.get('organization', 'N/A')}",
        f"发布年份：{metadata.get('year', 'N/A')}",
        f"\n内容：\n{content}"
    ]

    return "\n".join(output)


def clinical_guideline_sync(query: str, max_results: int = 1) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(clinical_guideline(query, max_results))
