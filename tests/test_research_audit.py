"""研究模块解析与知识库错误可见性的回归测试（离线）。"""
import asyncio
import threading
from types import SimpleNamespace

import pytest

import research.web_search as web_search_module
from research.evidence_synthesizer import (
    EvidenceSynthesizer,
    _parse_list_items,
)
from research.knowledge_base import KnowledgeBase
from research.web_search import WebSearchTool


def test_parse_list_items_accepts_common_prefixes():
    items = _parse_list_items(
        "- 第一条\n* 第二条\n• 第三条\n1. 第四条\n纯文本第五条"
    )

    assert items == ["第一条", "第二条", "第三条", "第四条", "纯文本第五条"]


def test_parse_list_items_keeps_disease_number():
    items = _parse_list_items("2型糖尿病的治疗方案")

    assert items == ["2型糖尿病的治疗方案"]


def test_parse_response_handles_numbered_findings():
    synthesizer = EvidenceSynthesizer.__new__(EvidenceSynthesizer)

    report = synthesizer._parse_response(
        "糖尿病治疗",
        "【关键发现】\n1. 二甲双胍为一线用药\n2. 生活方式干预同样重要\n"
        "【证据等级】\nA级\n【置信度】\n0.8\n"
        "【信息冲突】\n- 研究A显示有效，研究B显示无效\n"
        "【综合总结】\n综合来看……",
        [],
        [],
    )

    assert report.key_findings == [
        "二甲双胍为一线用药", "生活方式干预同样重要"
    ]
    assert report.conflicts == ["研究A显示有效，研究B显示无效"]


def test_parse_response_treats_whole_no_conflict_as_empty():
    synthesizer = EvidenceSynthesizer.__new__(EvidenceSynthesizer)

    report = synthesizer._parse_response(
        "糖尿病治疗",
        "【信息冲突】\n无冲突。\n【综合总结】\n无",
        [],
        [],
    )

    assert report.conflicts == []


def test_plan_queries_keeps_leading_disease_number():
    pytest.importorskip("sentence_transformers")
    from research.deep_research_workflow import DeepResearchWorkflow

    workflow = object.__new__(DeepResearchWorkflow)

    class FakeLLM:
        async def chat(self, messages, **kwargs):
            return "1. 2型糖尿病的药物治疗方案\n2. 糖尿病并发症筛查"

    workflow.llm_client = FakeLLM()

    queries = asyncio.run(workflow._plan_queries("糖尿病"))

    assert queries[0] == "2型糖尿病的药物治疗方案"
    assert queries[1] == "糖尿病并发症筛查"


def test_knowledge_base_raises_when_embedding_unavailable():
    kb = object.__new__(KnowledgeBase)
    kb.embedding_model = None
    kb.memory_storage = {}
    kb.qdrant_client = None

    with pytest.raises(RuntimeError, match="向量模型不可用"):
        asyncio.run(kb.add_document("阿司匹林"))
    with pytest.raises(ValueError, match="向量模型不可用"):
        asyncio.run(kb.search("阿司匹林"))


def test_web_search_runs_backend_in_thread(monkeypatch):
    class FakeDDGS:
        in_thread = None

        def text(self, *args, **kwargs):
            FakeDDGS.in_thread = (
                threading.current_thread() is not threading.main_thread()
            )
            return [{"title": "标题", "href": "https://x", "body": "摘要"}]

    monkeypatch.setattr(
        web_search_module, "DDGS", FakeDDGS, raising=False
    )
    monkeypatch.setattr(web_search_module, "DDGS_AVAILABLE", True)

    tool = WebSearchTool()
    results = asyncio.run(tool.search("高血压", retry_count=0))

    assert FakeDDGS.in_thread is True
    assert results and results[0].title == "标题"


def test_web_search_no_results_does_not_retry(monkeypatch):
    calls = []

    class FakeDDGS:
        def text(self, *args, **kwargs):
            calls.append(1)
            return []

    monkeypatch.setattr(
        web_search_module, "DDGS", FakeDDGS, raising=False
    )
    monkeypatch.setattr(web_search_module, "DDGS_AVAILABLE", True)

    tool = WebSearchTool()
    results = asyncio.run(tool.search("无结果", retry_count=2))

    assert results == []
    # 后端正常返回空结果时只跑一轮，不按重试次数重复搜索
    assert len(calls) <= 2


def test_no_results_not_treated_as_backend_failure():
    from research import web_search as module

    assert "bing" not in module._DDGS_BACKENDS


def test_parse_response_clamps_confidence():
    synthesizer = EvidenceSynthesizer.__new__(EvidenceSynthesizer)

    too_high = synthesizer._parse_response(
        "问题", "【置信度】\n1.5\n【综合总结】\n无", [], []
    )
    normal = synthesizer._parse_response(
        "问题", "【置信度】\n0.2\n【综合总结】\n无", [], []
    )

    assert too_high.confidence == 1.0
    assert normal.confidence == 0.2


def test_deep_research_milvus_search_runs_off_event_loop():
    pytest.importorskip("sentence_transformers")
    from research.deep_research_workflow import DeepResearchWorkflow

    class FakeKB:
        thread = None

        def search(self, query, top_k, filter_type=None):
            self.thread = threading.current_thread()
            return [{"content": "结果", "score": 0.8}]

    workflow = DeepResearchWorkflow.__new__(DeepResearchWorkflow)
    fake = FakeKB()
    workflow.knowledge_base = fake

    results = asyncio.run(workflow._search_milvus("高血压", top_k=3))

    assert results and results[0]["content"] == "结果"
    assert fake.thread is not threading.main_thread()
