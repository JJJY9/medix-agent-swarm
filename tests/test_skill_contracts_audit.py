"""技能契约（风险等级、ICD 分类、指南元数据、研究来源）的回归测试。"""
import asyncio
import importlib.util
import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]


def _load_skill(relative_path: str, module_name: str):
    path = ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assess_risk_reports_emergency():
    risk_module = _load_skill(
        ".claude/skills/assess-risk/script/risk.py", "risk_contract"
    )

    result = asyncio.run(risk_module.assess_risk("胸痛,呼吸困难"))

    assert result["risk_level"] == "emergency"
    assert "120" in result["recommendation"]


def test_disease_code_extracts_section_category():
    code_module = _load_skill(
        ".claude/skills/disease-code/script/code.py", "code_contract"
    )
    content = (
        ROOT / "knowledge/data/documents/10_icd10_cardiovascular.txt"
    ).read_text(encoding="utf-8")

    details = code_module.extract_icd_details(content, "冠心病")
    missing = code_module.extract_icd_details(content, "火星病")

    assert details == {
        "icd10_code": "I25",
        "category": "循环系统疾病",
    }
    assert missing is None


def test_disease_code_returns_not_found_for_missing_disease():
    code_module = _load_skill(
        ".claude/skills/disease-code/script/code.py", "code_contract_kb"
    )
    content = (
        ROOT / "knowledge/data/documents/10_icd10_cardiovascular.txt"
    ).read_text(encoding="utf-8")

    class FakeKB:
        def search(self, **kwargs):
            return [{
                "content": content,
                "metadata": {"filename": "10_icd10_cardiovascular.txt"},
                "score": 0.9,
            }]

    code_module._kb_instance = FakeKB()

    found = asyncio.run(code_module.disease_code("冠心病"))
    missing = asyncio.run(code_module.disease_code("火星病"))

    assert found["icd10_code"] == "I25"
    assert found["category"] == "循环系统疾病"
    assert missing["source"] == "未找到"
    assert missing["icd10_code"] == ""


def test_guideline_meta_is_parsed_from_content():
    guideline_module = _load_skill(
        ".claude/skills/clinical-guideline/script/guideline.py",
        "guideline_contract",
    )
    content = (
        ROOT / "knowledge/data/documents/20_guideline_hypertension.txt"
    ).read_text(encoding="utf-8")

    meta = guideline_module.parse_guideline_meta(content)

    assert meta["title"].startswith("中国高血压防治指南")
    assert meta["organization"] == "中华医学会心血管病学分会"
    assert meta["year"] == "2023年"


def test_deep_research_reports_actual_data_sources():
    research_module = _load_skill(
        ".claude/skills/deep-research/script/research.py",
        "research_contract",
    )

    class FakeWorkflow:
        def __init__(self, sources):
            self.sources = sources

        async def run(self, **kwargs):
            return SimpleNamespace(
                key_findings=["发现一"],
                summary="总结",
                evidence_level="B",
                confidence=0.8,
                conflicts=[],
                recommendations=[],
                sources=self.sources,
            )

    def run_with(sources):
        monkeypatch = SimpleNamespace()
        sys.modules["research.deep_research_workflow"] = SimpleNamespace(
            DeepResearchWorkflow=lambda: FakeWorkflow(sources)
        )
        try:
            return asyncio.run(research_module.deep_research("糖尿病"))
        finally:
            sys.modules.pop("research.deep_research_workflow", None)

    kb_only = run_with([{"type": "knowledge_base", "title": "指南"}])
    both = run_with([
        {"type": "web", "title": "网页"},
        {"type": "knowledge_base", "title": "指南"},
    ])

    assert kb_only["data_sources"] == "医学知识库（Milvus RAG） + 证据综合"
    assert "网络搜索" not in kb_only["answer"]
    assert both["data_sources"] == (
        "网络搜索 + 医学知识库（Milvus RAG） + 证据综合"
    )


def test_deep_research_signature_uses_scale_parameter():
    research_module = _load_skill(
        ".claude/skills/deep-research/script/research.py",
        "research_contract_sig",
    )

    parameters = inspect.signature(
        research_module.deep_research
    ).parameters

    assert "max_results_scale" in parameters
    assert "max_iterations" not in parameters


def test_deep_research_error_shape_includes_data_sources():
    research_module = _load_skill(
        ".claude/skills/deep-research/script/research.py",
        "research_contract_err",
    )

    class BrokenWorkflow:
        def __init__(self):
            raise RuntimeError("broken")

    sys.modules["research.deep_research_workflow"] = SimpleNamespace(
        DeepResearchWorkflow=BrokenWorkflow
    )
    try:
        result = asyncio.run(research_module.deep_research("糖尿病"))
    finally:
        sys.modules.pop("research.deep_research_workflow", None)

    assert result["status"] == "error"
    assert result["data_sources"] == ""
