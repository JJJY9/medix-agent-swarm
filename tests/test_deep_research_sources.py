"""deep_research 技能返回参考来源的回归测试。"""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = (
    PROJECT_ROOT / ".claude" / "skills" / "deep-research"
    / "script" / "research.py"
)


def _load_skill():
    spec = importlib.util.spec_from_file_location(
        "deep_research_skill", SCRIPT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_sources_map_web_and_knowledge_base():
    module = _load_skill()
    report = SimpleNamespace(
        confidence=0.8,
        sources=[
            {"type": "web", "title": "指南更新", "url": "https://x.test/a"},
            {"type": "knowledge_base", "title": "高血压管理", "id": "doc-1"},
        ],
    )

    sources = module._report_sources(report)

    assert [item["source"] for item in sources] == [
        "网络搜索", "医学知识库",
    ]
    assert sources[0]["snippet"] == "https://x.test/a"
    assert "doc-1" in sources[1]["snippet"]
    assert all(item["score"] == 0.8 for item in sources)


def test_skill_result_includes_sources_field():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"sources": _report_sources(report)' in text
