"""技能脚本的 ICD 解析、空输入、线程化与返回条数回归测试。"""
import asyncio
import importlib.util
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


def test_extract_icd_code_matches_requested_disease():
    code_module = _load_skill(
        ".claude/skills/disease-code/script/code.py", "skill_code"
    )
    content = (
        ROOT / "knowledge/data/documents/10_icd10_cardiovascular.txt"
    ).read_text(encoding="utf-8")

    hypertension = code_module.extract_icd_details(content, "高血压")
    coronary = code_module.extract_icd_details(content, "冠心病")

    assert hypertension["icd10_code"] == "I10"
    assert coronary["icd10_code"] == "I25"


def test_assess_risk_empty_input_is_unknown():
    risk_module = _load_skill(
        ".claude/skills/assess-risk/script/risk.py", "skill_risk"
    )

    empty = asyncio.run(risk_module.assess_risk(""))
    blank = asyncio.run(risk_module.assess_risk("   "))

    assert empty["risk_level"] == "unknown"
    assert blank["risk_level"] == "unknown"
    assert "无法评估" in empty["answer"]


def test_search_history_clamps_zero_limit():
    history_module = _load_skill(
        ".claude/skills/search-history/script/search.py", "skill_history"
    )
    from memory import ShortTermMemory

    memory = ShortTermMemory(storage_type="memory")
    session_id = "skill-history-zero-limit"
    memory.clear_session(session_id)
    for index in range(4):
        memory.add_message(session_id, "user", f"问 {index}")
        memory.add_message(session_id, "assistant", f"答 {index}")

    result = asyncio.run(history_module.search_history(session_id, limit=0))

    assert result["total_messages"] == 2
    memory.clear_session(session_id)


def test_deep_research_uses_repo_root_and_handles_errors(monkeypatch):
    research_module = _load_skill(
        ".claude/skills/deep-research/script/research.py", "skill_research"
    )

    class FakeWorkflow:
        async def run(self, **kwargs):
            return SimpleNamespace(
                key_findings=["发现一"],
                summary="总结",
                evidence_level="B",
                confidence=0.8,
                conflicts=[],
                recommendations=[],
                sources=[{"title": "指南"}],
            )

    monkeypatch.setitem(
        sys.modules,
        "research.deep_research_workflow",
        SimpleNamespace(DeepResearchWorkflow=FakeWorkflow),
    )

    result = asyncio.run(research_module.deep_research("糖尿病"))

    assert result["status"] == "completed"
    assert result["findings"] == ["发现一"]
    assert result["confidence"] == "high"


def test_deep_research_import_failure_returns_structured_error(monkeypatch):
    research_module = _load_skill(
        ".claude/skills/deep-research/script/research.py",
        "skill_research_fail",
    )

    class BrokenWorkflow:
        def __init__(self):
            raise RuntimeError("workflow unavailable")

    monkeypatch.setitem(
        sys.modules,
        "research.deep_research_workflow",
        SimpleNamespace(DeepResearchWorkflow=BrokenWorkflow),
    )

    result = asyncio.run(research_module.deep_research("糖尿病"))

    assert result["status"] == "error"
    assert result["findings"] == []
    assert "失败" in result["answer"]


def test_clinical_guideline_returns_requested_count():
    guideline_module = _load_skill(
        ".claude/skills/clinical-guideline/script/guideline.py",
        "skill_guideline",
    )

    class FakeKB:
        def search(self, **kwargs):
            return [
                {
                    "content": "指南甲内容",
                    "metadata": {"disease": "甲", "filename": "a.txt"},
                    "score": 0.9,
                },
                {
                    "content": "指南乙内容",
                    "metadata": {"disease": "乙", "filename": "b.txt"},
                    "score": 0.8,
                },
            ]

    guideline_module._kb_instance = FakeKB()

    result = asyncio.run(
        guideline_module.clinical_guideline("高血压", max_results=2)
    )

    assert "指南甲内容" in result["answer"]
    assert "指南乙内容" in result["answer"]
    assert len(result["sources"]) == 2


def test_knowledge_skills_run_search_in_thread():
    for relative_path in (
        ".claude/skills/search-knowledge/script/search.py",
        ".claude/skills/assess-risk/script/risk.py",
        ".claude/skills/analyze-symptoms/script/symptoms.py",
        ".claude/skills/clinical-guideline/script/guideline.py",
        ".claude/skills/disease-code/script/code.py",
        ".claude/skills/recommend-lifestyle/script/lifestyle.py",
    ):
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "asyncio.to_thread(kb.search" in source, relative_path
        assert "await asyncio.to_thread(get_knowledge_base)" in source
