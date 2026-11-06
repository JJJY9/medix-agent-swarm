"""第六轮工具与深度研究审计回归：搜索后端、域名、解析与自动修复。"""
import asyncio
from pathlib import Path

import pytest

import research.web_search as web_search_module
from constraints import ConstraintValidator
from research.evidence_synthesizer import EvidenceSynthesizer, ResearchReport
from research.web_search import SearchResult, WebSearchTool
from validation.auto_fixer import AutoFixer

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeDDGS:
    used_backends = []

    def __init__(self, *args, **kwargs):
        pass

    def text(self, query, **kwargs):
        FakeDDGS.used_backends.append(kwargs.get("backend"))
        return [
            {
                "title": "高血压指南",
                "href": "https://pubmed.ncbi.nlm.nih.gov/1",
                "body": "限盐",
            }
        ]


def test_web_search_uses_version_appropriate_backend(monkeypatch):
    FakeDDGS.used_backends = []
    monkeypatch.setattr(
        web_search_module, "DDGS", FakeDDGS, raising=False
    )
    monkeypatch.setattr(web_search_module, "DDGS_AVAILABLE", True)
    monkeypatch.setattr(web_search_module, "_DDGS_BACKENDS", ("auto",))

    results = asyncio.run(WebSearchTool().search("高血压", max_results=1))

    assert len(results) == 1
    assert FakeDDGS.used_backends == ["auto"]


def test_domain_filter_requires_real_hostname_match():
    tool = WebSearchTool()
    results = [
        SearchResult("a", "https://evil.com/?u=pubmed.ncbi.nlm.nih.gov", ""),
        SearchResult("b", "https://pubmed.ncbi.nlm.nih.gov.evil.io/x", ""),
        SearchResult("c", "https://pubmed.ncbi.nlm.nih.gov/123", ""),
    ]

    filtered = tool.filter_by_domain(
        results, allowed_domains=["pubmed.ncbi.nlm.nih.gov"]
    )

    assert [item.url for item in filtered] == [
        "https://pubmed.ncbi.nlm.nih.gov/123"
    ]


def test_synthesizer_parses_sections_with_inner_brackets():
    synthesizer = object.__new__(EvidenceSynthesizer)
    response = (
        "【关键发现】\n"
        "- 【核心原则】限盐 <5g/日\n"
        "- 规律运动\n"
        "【证据等级】\n"
        "B级（现有来源未能达到 A 级标准）\n"
        "【置信度】\n"
        "0.8\n"
        "【信息冲突】\n"
        "无冲突\n"
        "【综合总结】\n"
        "总结文字\n"
        "【建议】\n"
        "- 建议一\n"
    )

    report = synthesizer._parse_response("高血压", response, [], [])

    assert report.key_findings == ["【核心原则】限盐 <5g/日", "规律运动"]
    assert report.evidence_level == "B"
    assert report.confidence == 0.8
    assert report.summary == "总结文字"
    assert report.recommendations == ["建议一"]


class FailingLLM:
    async def chat(self, messages):
        raise RuntimeError("quota exhausted")


def test_synthesis_failure_is_marked_as_failed():
    synthesizer = EvidenceSynthesizer(llm_client=FailingLLM())

    report = asyncio.run(synthesizer.synthesize("测试", [], []))

    assert isinstance(report, ResearchReport)
    assert report.failed is True
    assert report.evidence_level == ""
    assert "综合失败" in report.summary


def _load_guideline_skill():
    path = (
        PROJECT_ROOT / ".claude" / "skills" / "clinical-guideline"
        / "script" / "guideline.py"
    )
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "medix_guideline_round6", path
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guideline_format_ignores_mid_chunk_title():
    module = _load_guideline_skill()

    text = module.format_guideline(
        "或水银柱血压计测量，取两次平均值。",
        {"chunk_id": 3, "disease": "高血压"},
    )

    assert "指南名称：高血压相关临床指南" in text
    assert "指南名称：或水银柱血压计" not in text


def test_length_violation_is_auto_fixable():
    validator = ConstraintValidator()
    output = "建议内容" + "很长的说明" * 600

    result = validator.validate_output("consultation_agent", output)

    assert any("回答过长" in item for item in result["violations"])
    assert "truncate_response:2000" in result["auto_fixable"]

    fixed = AutoFixer().fix_output(output, result["auto_fixable"])

    assert len(fixed) <= 2000
    assert "已截断" in fixed


def test_deep_research_deduplicates_web_urls():
    pytest.importorskip("sentence_transformers")
    from research.deep_research_workflow import DeepResearchWorkflow

    results = [
        SearchResult("a", "https://Example.com/page/", ""),
        SearchResult("b", "https://example.com/page", ""),
        SearchResult("c", "https://other.com/x", ""),
    ]

    deduped = DeepResearchWorkflow._dedupe_web_results(results)

    assert [item.url for item in deduped] == [
        "https://Example.com/page/",
        "https://other.com/x",
    ]
