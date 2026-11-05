"""会话总结性能指标的离线测试。"""
from datetime import datetime, timedelta

from memory.session_summary import SessionSummary
from swarm.shared_context import (
    Contribution,
    SharedContext,
    SubTask,
    TaskStatus,
)


def _summary(context, start, end):
    return SessionSummary.from_shared_context(
        "session-1", "头晕怎么办", context, "建议监测血压。", start, end,
    )


def test_summary_metrics_reflect_collaboration():
    now = datetime.now()
    start = now - timedelta(seconds=10)
    context = SharedContext("session-1")
    context.task_decomposition = {
        "t1": SubTask(
            id="t1", type="risk", description="风险评估",
            assigned_agent="agent_a",
        ),
        "t2": SubTask(
            id="t2", type="diagnosis", description="鉴别诊断",
            assigned_agent="agent_b",
        ),
    }
    context.task_decomposition["t1"].started_at = start
    context.complete_subtask("t1", "agent_a", {"risk_level": "高危"})
    context.task_decomposition["t2"].status = TaskStatus.FAILED
    context.task_decomposition["t2"].result = {"error": "超时"}
    context.agent_contributions["agent_b"].append(Contribution(
        agent_id="agent_b",
        subtask_id="t1",
        result={"risk_level": "高危"},
    ))

    summary = _summary(context, start, now)

    assert summary.subtasks_created == 2
    assert summary.subtasks_completed == 1
    assert summary.performance.agent_count == 2
    assert summary.performance.information_coverage == 0.5
    assert summary.performance.redundancy == 0.5
    assert 0.4 <= summary.performance.parallel_efficiency <= 0.6
    assert 0.9 <= summary.performance.speedup_vs_single <= 1.1
    assert len(summary.lessons_learned) == 1
    assert "超时" in summary.lessons_learned[0].description
    assert summary.lessons_learned[0].agent_id == "agent_b"


def test_summary_metrics_without_subtasks():
    now = datetime.now()
    context = SharedContext("session-2")

    summary = _summary(context, now - timedelta(seconds=5), now)

    assert summary.performance.information_coverage == 1.0
    assert summary.performance.redundancy == 0.0
    assert summary.performance.parallel_efficiency == 1.0
    assert summary.lessons_learned == []
