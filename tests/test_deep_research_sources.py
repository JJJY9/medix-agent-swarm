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
    # 网页来源没有可比的相关度：不能把报告级 confidence 冒充为逐条分数
    assert "score" not in sources[0]
    # 知识库来源没有真实片段时保持为空，不能把内部 id 当成原文
    assert sources[1]["snippet"] == ""
    assert "doc-1" not in str(sources[1])
    assert "score" not in sources[1]


def test_report_sources_keep_real_kb_metadata():
    module = _load_skill()
    report = SimpleNamespace(
        confidence=0.8,
        sources=[{
            "type": "knowledge_base",
            "title": "高血压管理",
            "id": "doc-1",
            "filename": "guideline.md",
            "snippet": "限盐并规律运动",
            "score": 0.62,
        }],
    )

    sources = module._report_sources(report)

    assert sources[0]["filename"] == "guideline.md"
    assert sources[0]["snippet"] == "限盐并规律运动"
    assert sources[0]["score"] == 0.62


def test_skill_result_includes_sources_field():
    text = SCRIPT.read_text(encoding="utf-8")

    assert '"sources": _report_sources(report)' in text
