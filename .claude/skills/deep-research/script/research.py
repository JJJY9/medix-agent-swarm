"""
Deep Research Skill
深度研究 Skill（依赖 RAG 知识库 + Web Search）
整合网络搜索和 Milvus 医学知识库进行深度研究
"""
from typing import Dict, Any
import asyncio
from loguru import logger


async def deep_research(query: str, max_results_scale: int = 2) -> Dict[str, Any]:
    """
    深度研究

    Args:
        query: 研究问题
        max_results_scale: 检索规模倍数（默认 2，限制 1-5）；
            网络结果上限 = 倍数 × 5，知识库上限 = 倍数 × 3

    Returns:
        {
            "answer": "格式化的研究报告",
            "findings": ["发现1", "发现2"],
            "confidence": "high/medium/low",
            "source_count": 来源数量,
            "evidence_level": "A/B/C",
            "status": "completed/error",
            "data_sources": 实际使用的数据源
        }
    """
    logger.info(
        "Starting deep research: "
        f"query_length={len(str(query or ''))}, "
        f"max_results_scale={max_results_scale}"
    )

    try:
        scale = max(1, min(int(max_results_scale or 2), 5))
    except (TypeError, ValueError):
        scale = 2

    usage: Dict[str, int] = {}

    try:
        # 调用深度研究工作流
        import sys
        from pathlib import Path
        # 仓库根目录：parents[4] 依次为 script/技能/ skills/.claude/仓库根
        project_root = Path(__file__).resolve().parents[4]
        if str(project_root) not in sys.path:
            sys.path.insert(0, str(project_root))

        from research.deep_research_workflow import DeepResearchWorkflow

        # 构造函数会加载知识库/Embedding 模型，必须放到线程里，
        # 否则冷启动首次调用会阻塞整个 FastAPI 事件循环
        workflow = await asyncio.to_thread(DeepResearchWorkflow)

        # 执行研究
        # 注意：DeepResearchWorkflow.run() 返回的是 ResearchReport 对象，不是 dict
        report = await workflow.run(
            question=query,  # 参数名是 question，不是 query
            max_web_results=scale * 5,
            max_kb_results=scale * 3,
            usage_out=usage,
        )

        # report 是 ResearchReport 对象，有以下属性：
        # - key_findings: List[str]
        # - evidence_level: str
        # - confidence: float
        # - sources: List[Dict]
        # - summary: str
        # - recommendations: List[str]

        data_sources = _describe_data_sources(report.sources)
        failed = bool(getattr(report, "failed", False))
        return {
            "answer": format_research_report(query, report, data_sources),
            "findings": report.key_findings,
            "confidence": "high" if report.confidence > 0.7 else "medium" if report.confidence > 0.4 else "low",
            "source_count": len(report.sources),
            "evidence_level": report.evidence_level,
            "status": "error" if failed else "completed",
            "data_sources": data_sources,
            "sources": _report_sources(report),
            "usage": usage,
            "llm_calls": 2,
        }

    except Exception as e:
        logger.error(f"Deep research failed: {e}")
        return {
            "answer": "深度研究失败，请稍后重试；如持续失败请检查网络与知识库状态。",
            "findings": [],
            "confidence": "low",
            "source_count": 0,
            "status": "error",
            "data_sources": "",
            "usage": usage,
            "llm_calls": 0,
        }


def _describe_data_sources(sources) -> str:
    """按实际来源类型描述数据源，避免声称使用了并不存在的网络搜索。"""
    types = {
        str(item.get("type") or "")
        for item in (sources or [])
        if isinstance(item, dict)
    }
    parts = []
    if "web" in types:
        parts.append("网络搜索")
    if "knowledge_base" in types:
        parts.append("医学知识库（Milvus RAG）")
    parts.append("证据综合")
    return " + ".join(parts)


def _report_sources(report) -> list:
    """把研究报告的来源映射为前端参考来源卡片可用的结构。

    score 只使用来源自带的真实相关度（知识库 COSINE 分数）；网页
    来源没有可比的分数时不写 score，不能把报告级 confidence 冒充
    为每条来源的相关度（还会让同分来源挤掉真实命中的知识库来源）。
    """
    sources = []
    for item in getattr(report, "sources", []) or []:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "医学知识")
        if item.get("type") == "web":
            url = str(item.get("url") or "")
            sources.append({
                "title": title,
                "source": "网络搜索",
                "filename": title,
                "url": url,
                "snippet": str(item.get("snippet") or url),
            })
        else:
            filename = str(item.get("filename") or "") or title
            snippet = str(item.get("snippet") or "")
            source = {
                "title": title,
                "source": "医学知识库",
                "filename": filename,
                "snippet": snippet,
            }
            try:
                score = float(item.get("score"))
            except (TypeError, ValueError, OverflowError):
                score = None
            if score is not None:
                source["score"] = round(score, 4)
            sources.append(source)
    return sources


def format_research_report(query: str, report, data_sources: str = "") -> str:
    """
    格式化研究报告

    Args:
        query: 原始查询
        report: ResearchReport 对象（来自 evidence_synthesizer.py）
    """
    output = [
        "【深度研究报告】\n",
        f"研究问题：{query}\n"
    ]

    # 关键发现
    if report.key_findings:
        output.append("关键发现：")
        for i, finding in enumerate(report.key_findings, 1):
            output.append(f"{i}. {finding}")
        output.append("")

    # 综合总结
    if report.summary:
        output.append(f"综合分析：\n{report.summary}\n")

    # 证据等级；综合失败时没有等级可言
    if report.evidence_level:
        output.append(f"证据等级：{report.evidence_level} 级")
    else:
        output.append("证据等级：未生成（综合失败）")

    # 置信度
    confidence_percent = f"{report.confidence:.0%}"
    output.append(f"置信度：{confidence_percent}")

    # 信息冲突（如果有）
    if report.conflicts:
        output.append("\n信息冲突：")
        for conflict in report.conflicts:
            output.append(f"- {conflict}")

    # 建议（如果有）
    if report.recommendations:
        output.append("\n建议：")
        for i, rec in enumerate(report.recommendations, 1):
            output.append(f"{i}. {rec}")

    # 来源数量
    if report.sources:
        output.append(f"\n参考来源数量：{len(report.sources)}")

    output.append(
        f"\n💡 数据来源：{data_sources or '证据综合'}"
    )

    return "\n".join(output)


def deep_research_sync(query: str, max_results_scale: int = 2) -> Dict[str, Any]:
    import asyncio
    return asyncio.run(deep_research(query, max_results_scale))
