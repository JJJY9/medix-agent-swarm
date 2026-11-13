"""
Disease Code Skill
疾病编码查询 Skill（自包含，无需依赖tools）
"""
from typing import Dict, Any, Optional
import asyncio
import re
from loguru import logger
from knowledge.sources import (
    build_sources,
    sanitize_untrusted_text,
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
    logger.info(
        f"Searching ICD-10 code: disease_length={len(str(disease_name or ''))}"
    )

    # 使用知识库单例
    kb = await asyncio.to_thread(get_knowledge_base)

    # 使用 Milvus 检索疾病编码；多取几条，按疾病名称定位正确小节
    results = await asyncio.to_thread(kb.search,
        query=f"{disease_name} ICD-10编码 疾病分类",
        top_k=3,
        filter_type="disease_classification"
    )

    for doc in results:
        if not isinstance(doc, dict) or doc.get("score", 0) <= 0.1:
            continue
        content = doc.get("content") or ""
        details = extract_icd_details(content, disease_name)
        if details is None:
            continue
        metadata = doc.get("metadata") or {}
        return {
            "answer": format_code_info(disease_name, content),
            "icd10_code": metadata.get("icd10_code")
            or details["icd10_code"],
            "category": sanitize_untrusted_text(
                details["category"] or metadata.get("category", ""), 80
            ),
            "source": "向量数据库",
            "sources": build_sources([doc], max_items=1),
        }

    # 未找到匹配小节
    logger.warning(
        "No ICD-10 code found in vector DB "
        f"(disease_length={len(str(disease_name or ''))})"
    )
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
_CATEGORY_PATTERN = re.compile(r"(?:疾病分类|类别)\s*[：:]\s*([^\n]+)")
#: 部分文档用「疾病名（编码）」作小节标题，如「流行性感冒（J10-J11）」
_HEADING_PATTERN = re.compile(
    r"^[ \t]*(?P<name>[^\n（(]+?)[ \t]*[（(][ \t]*"
    r"(?P<code>[A-Z]\d{2}(?:\.\d+)?(?:\s*[-–—]\s*[A-Z]?\d{2,3})?)"
    r"[ \t]*[）)]",
    re.MULTILINE,
)
#: 范围编码（J10-J11）对外返回首个单码，正文保留原始范围
_SINGLE_CODE_PATTERN = re.compile(r"^[A-Z]\d{2}(?:\.\d+)?")
_CHAPTER_NAME_PATTERN = re.compile(r"^第[一二三四五六七八九十百零〇]+章")


def _extract_heading_details(
    content: str, target: str
) -> Optional[Dict[str, str]]:
    """按「疾病名（编码）」小节标题提取编码与分类。"""
    for match in _HEADING_PATTERN.finditer(content):
        name = match.group("name").strip()
        if not name:
            continue
        # 「类别：传染病和寄生虫病（A00-B99）」「第九章（I00-I99）」
        # 描述的是分类/章节，不是疾病条目
        if "：" in name or ":" in name:
            continue
        if _CHAPTER_NAME_PATTERN.match(name):
            continue
        if not (target in name or name in target):
            continue
        code = match.group("code").strip()
        single = _SINGLE_CODE_PATTERN.match(code)
        category_match = _CATEGORY_PATTERN.search(content)
        return {
            "icd10_code": single.group(0) if single else code,
            "category": (
                category_match.group(1).strip()
                if category_match else ""
            ),
        }
    return None


def extract_icd_details(
    content: str, disease_name: str
) -> Optional[Dict[str, str]]:
    """提取与疾病名匹配小节内的编码与分类。

    找不到匹配小节时返回 None（单疾病文档没有「疾病名称」小节时才
    退回全文第一条编码），避免把文档里其他疾病的编码返回给用户。
    """
    target = str(disease_name or "").strip()
    if target and "疾病名称" in content:
        sections = content.split("疾病名称")
        for section in sections[1:]:
            name_match = re.match(r"\s*[：:]\s*([^\n]+)", section)
            name = name_match.group(1).strip() if name_match else ""
            if name and (target in name or name in target):
                code_match = _ICD_PATTERN.search(section)
                category_match = _CATEGORY_PATTERN.search(section)
                return {
                    "icd10_code": code_match.group(1) if code_match else "",
                    "category": (
                        category_match.group(1).strip()
                        if category_match else ""
                    ),
                }
        return None

    if target:
        heading_details = _extract_heading_details(content, target)
        if heading_details is not None:
            return heading_details

    code_match = _ICD_PATTERN.search(content)
    if code_match is None:
        return None
    category_match = _CATEGORY_PATTERN.search(content)
    return {
        "icd10_code": code_match.group(1),
        "category": (
            category_match.group(1).strip() if category_match else ""
        ),
    }


def format_code_info(disease_name: str, content: str) -> str:
    """格式化疾病编码信息"""
    output = [
        f"【疾病编码信息】\n",
        wrap_untrusted_source(content)
    ]

    return "\n".join(output)


def disease_code_sync(disease_name: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(disease_code(disease_name))
