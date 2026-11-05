"""
Disease Code Skill
疾病编码查询 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any
import asyncio
import re
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


async def disease_code(disease_name: str) -> Dict[str, Any]:
    """
    查询疾病 ICD-10 编码

    Args:
        disease_name: 疾病名称

    Returns:
        {
            "answer": "格式化的疾病编码信息",
            "icd10_code": "ICD-10 编码",
            "category": "疾病分类"
        }
    """
    logger.info(f"Searching ICD-10 code for disease: {disease_name}")

    # 使用知识库单例
    kb = await asyncio.to_thread(get_knowledge_base)

    # 使用 Milvus 检索疾病编码
    results = await asyncio.to_thread(kb.search,
        query=f"{disease_name} ICD-10编码 疾病分类",
        top_k=1,
        filter_type="disease_classification"
    )

    if results and results[0]["score"] > 0.1:
        doc = results[0]
        metadata = doc["metadata"]

        content = doc["content"]
        icd10_code = metadata.get("icd10_code", "")
        if not icd10_code:
            icd10_code = extract_icd_code(content, disease_name)

        return {
            "answer": format_code_info(disease_name, content),
            "icd10_code": icd10_code,
            "category": metadata.get("category", ""),
            "source": "向量数据库",
            "sources": build_sources(results, max_items=1),
        }
    else:
        # 未找到相关内容
        logger.warning(f"No ICD-10 code found in vector DB for {disease_name}")
        return {
            "answer": f"未找到'{disease_name}'的ICD-10编码，建议使用更标准的疾病名称或联系医生咨询。",
            "icd10_code": "",
            "category": "",
            "source": "未找到",
            "sources": [],
        }


_ICD_PATTERN = re.compile(
    r"ICD-10\s*编码\s*[：:]\s*([A-Z]\d{2}(?:\.\d+)?)", re.IGNORECASE
)


def extract_icd_code(content: str, disease_name: str) -> str:
    """优先取与疾病名匹配小节内的编码，避免返回文档里的第一条编码。"""
    target = str(disease_name or "").strip()
    if target:
        sections = content.split("疾病名称")
        for section in sections[1:]:
            name_match = re.match(r"\s*[：:]\s*([^\n]+)", section)
            name = name_match.group(1).strip() if name_match else ""
            if name and (target in name or name in target):
                code_match = _ICD_PATTERN.search(section)
                if code_match:
                    return code_match.group(1)
    fallback = _ICD_PATTERN.search(content)
    return fallback.group(1) if fallback else ""


def format_code_info(disease_name: str, content: str) -> str:
    """格式化疾病编码信息"""
    output = [
        f"【疾病编码信息】\n",
        content
    ]

    return "\n".join(output)


def disease_code_sync(disease_name: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(disease_code(disease_name))
