"""
Assess Risk Skill
风险评估 Skill（依赖 RAG 知识库）
"""
from typing import Dict, Any, List
import asyncio
from loguru import logger
from knowledge.sources import (
    build_sources,
    wrap_source_snippets,
    wrap_untrusted_source,
)

# 全局知识库实例（避免重复加载模型）
_kb_instance = None


def get_knowledge_base():
    """获取知识库单例"""
    global _kb_instance
    if _kb_instance is None:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        _kb_instance = MedicalKnowledgeBase()
        from knowledge.service import ensure_knowledge_index_fresh
        ensure_knowledge_index_fresh(_kb_instance)
    return _kb_instance


async def assess_risk(symptoms: str) -> Dict[str, Any]:
    """
    评估症状风险等级

    Args:
        symptoms: 症状描述（字符串）

    Returns:
        {
            "answer": "格式化的风险评估结果",
            "risk_level": "low/medium/high/emergency",
            "recommendation": "就医建议"
        }
    """
    logger.info(f"Assessing risk: symptom_length={len(str(symptoms or ''))}")

    symptoms = str(symptoms or "").strip()
    symptom_list = [s.strip() for s in symptoms.split(",") if s.strip()]
    if not symptoms or not symptom_list:
        # 缺少症状描述时不能默认判为低危，否则是危险的假阴性
        return {
            "answer": "未提供症状描述，无法评估风险。请补充具体症状、持续时间和严重程度。",
            "risk_level": "unknown",
            "recommendation": (
                "请补充症状描述后再评估；如出现胸痛、呼吸困难、意识模糊等"
                "急症表现，请立即就医或拨打 120。"
            ),
            "kb_advice": None,
            "sources": [],
        }

    # 急症红旗优先：胸痛伴呼吸困难、意识改变等直接判为紧急
    from core.triage import detect_red_flags, has_positive_mention

    triage = detect_red_flags(symptoms)

    # 高风险症状列表
    high_risk_symptoms = [
        "胸痛", "呼吸困难", "意识模糊", "严重出血", "剧烈头痛",
        "持续呕吐", "高热不退", "突然晕厥", "剧烈腹痛", "面部下垂"
    ]

    # 中风险症状关键词
    medium_risk_keywords = ["持续", "加重", "反复", "严重", "剧烈"]

    risk_level = "low"
    reasons = []

    if triage["level"] == "emergency":
        risk_level = "emergency"
        reasons.append(
            "检测到急症警示信号：" + "、".join(triage.get("matches") or [])
        )

    # 检查高风险症状（急症已确定时不再降级覆盖）
    if risk_level != "emergency":
        for symptom in symptom_list:
            for high_risk in high_risk_symptoms:
                if has_positive_mention(symptom, high_risk):
                    risk_level = "high"
                    reasons.append(f"检测到高风险症状：{symptom}")
                    break

    # 检查中等风险特征
    if risk_level == "low":
        for symptom in symptom_list:
            if any(
                has_positive_mention(symptom, keyword)
                for keyword in medium_risk_keywords
            ):
                risk_level = "medium"
                reasons.append(f"症状描述提示需要关注：{symptom}")

    # 生成建议
    if risk_level == "emergency":
        recommendation = (
            "⚠️ 出现急症警示信号，请立即拨打 120 或前往急诊科，"
            "不要等待在线回复。"
        )
    elif risk_level == "high":
        recommendation = "⚠️ 建议立即就医或拨打急救电话120"
    elif risk_level == "medium":
        recommendation = "建议尽快就医，不要拖延，必要时前往医院"
    else:
        recommendation = "建议密切观察症状变化，如果症状加重或持续不缓解，应及时就医"

    # 从 RAG 知识库获取风险相关的医学建议；急症时不等待检索
    kb_advice = None
    sources = []
    if risk_level != "emergency":
        try:
            kb = await asyncio.to_thread(get_knowledge_base)
            # 根据风险等级查询相关医学知识
            risk_query = f"{symptoms} 紧急程度 风险评估 就医建议"
            results = await asyncio.to_thread(kb.search,
                query=risk_query,
                top_k=1,
                filter_type=None
            )
            if results and results[0]["score"] > 0.5:
                # 知识库可能包含用户上传文档：先声明不可信边界再放入结果
                kb_advice = wrap_untrusted_source(
                    results[0]["content"][:300]
                )
                sources = wrap_source_snippets(
                    build_sources(results, max_items=1)
                )
        except Exception as e:
            logger.warning(f"Failed to get KB advice: {e}")

    return {
        "answer": format_assessment(symptoms, risk_level, reasons, recommendation, kb_advice),
        "risk_level": risk_level,
        "recommendation": recommendation,
        "kb_advice": kb_advice,
        "sources": sources,
    }


def format_assessment(symptoms: str, level: str, reasons: list, recommendation: str, kb_advice: str = None) -> str:
    """格式化风险评估结果"""
    level_map = {
        "low": "低危 🟢",
        "medium": "中危 🟡",
        "high": "高危 🔴",
        "emergency": "紧急 🚨"
    }

    output = [
        f"【症状风险评估】",
        f"\n症状描述：{symptoms}",
        f"\n风险等级：{level_map.get(level, level)}",
    ]

    if reasons:
        output.append("\n风险因素：")
        for reason in reasons:
            output.append(f"  • {reason}")

    output.append(f"\n就医建议：{recommendation}")

    # 添加来自知识库的医学建议
    if kb_advice:
        output.append("\n【医学知识库补充】")
        output.append(kb_advice)

    if level == "high" or level == "emergency":
        output.append("\n⚠️ 请立即就医或拨打 120！")

    output.append("\n💡 数据来源：风险规则引擎 + 医学知识库（Milvus RAG）")

    return "\n".join(output)


def assess_risk_sync(symptoms: str) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(assess_risk(symptoms))
