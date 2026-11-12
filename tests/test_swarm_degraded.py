"""Swarm 降级子任务不得计入成功贡献。"""
import asyncio

from swarm.shared_context import SharedContext, SubTask
from swarm.swarm_coordinator import SwarmCoordinator


class DegradedWorker:
    agent_id = "consultation_agent"

    async def process_subtask(self, subtask, shared_context=None):
        return {
            "answer": "抱歉，未能生成有效答复，请稍后重试或简化问题。",
            "degraded": True,
            "warning": "max_iterations_reached",
            "usage": {"total_tokens": 12},
            "llm_calls": 2,
            "sources": [],
        }


def test_degraded_subtask_is_recorded_as_failure():
    coordinator = object.__new__(SwarmCoordinator)
    context = SharedContext(session_id="degraded-test")
    subtask = SubTask(
        id="t1",
        type="consultation",
        description="测试",
        assigned_agent="consultation_agent",
    )
    context.add_subtask(subtask)
    assert context.start_subtask("t1")

    asyncio.run(
        coordinator._execute_single_subtask(
            DegradedWorker(), subtask, context
        )
    )

    # 道歉文本不能成为科室贡献，子任务应记为失败
    assert context.get_contributions() == []
    assert context.get_subtask("t1").status.value == "failed"
    failures = context.data.get("failed_usage") or []
    assert failures and failures[0]["llm_calls"] == 2
    assert failures[0]["usage"]["total_tokens"] == 12
