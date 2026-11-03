"""来源合并与科室意见汇总的单元测试。"""
from core.citations import merge_sources
from knowledge.sources import build_sources
from swarm.shared_context import SharedContext, SubTask
from swarm.swarm_coordinator import SwarmCoordinator


def test_build_sources_from_kb_results():
    results = [
        {
            "content": "高血压患者应低盐饮食。" * 20,
            "metadata": {
                "disease": "hypertension",
                "source": "生活方式建议数据库",
                "filename": "01_lifestyle_hypertension.txt",
                "type": "lifestyle",
            },
            "score": 0.8557,
        }
    ]

    sources = build_sources(results, max_items=1)

    assert sources[0]["title"] == "hypertension"
    assert sources[0]["source"] == "生活方式建议数据库"
    assert sources[0]["score"] == 0.8557
    assert len(sources[0]["snippet"]) <= 160


def test_merge_sources_deduplicates_and_sorts():
    sources = [
        {"source": "库A", "filename": "a.txt", "snippet": "x", "score": 0.5},
        {"source": "库B", "filename": "b.txt", "snippet": "y", "score": 0.9},
        {"source": "库A", "filename": "a.txt", "snippet": "x", "score": 0.5},
    ]

    merged = merge_sources(sources)

    assert len(merged) == 2
    assert merged[0]["source"] == "库B"
    assert merged[1]["source"] == "库A"


def test_merge_sources_limits_results():
    sources = [
        {"source": f"库{i}", "filename": f"{i}.txt", "snippet": "x", "score": i}
        for i in range(10)
    ]

    assert len(merge_sources(sources, limit=3)) == 3


def test_collect_insights_includes_contributions_and_sources():
    context = SharedContext(session_id="insight-test")
    context.add_subtask(SubTask(
        id="s1",
        type="diagnostic_agent_task",
        description="分析症状",
        assigned_agent="diagnostic_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask(
        "s1",
        "diagnostic_agent",
        {
            "answer": "建议急诊评估。",
            "sources": [
                {"source": "临床指南数据库", "filename": "20.txt", "snippet": "x", "score": 0.8}
            ],
        },
    )

    contributions, sources = SwarmCoordinator._collect_insights(context)

    assert contributions[0]["agent_id"] == "diagnostic_agent"
    assert contributions[0]["content"] == "建议急诊评估。"
    assert contributions[0]["task_type"] == "diagnostic_agent_task"
    assert sources[0]["filename"] == "20.txt"


def test_history_metadata_extracts_known_fields():
    metadata = SwarmCoordinator._history_metadata({
        "swarm_enabled": True,
        "sources": [{"title": "指南"}],
        "triage": {"level": "none"},
        "answer": "不保存",
        "foo": 1,
    })

    assert metadata == {
        "swarm_enabled": True,
        "sources": [{"title": "指南"}],
        "triage": {"level": "none"},
    }
