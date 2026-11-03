"""Swarm 容错与协作调度的回归测试。"""
import asyncio

import swarm.swarm_coordinator as coordinator_module
from constraints import ConstraintValidator
from swarm.events import EventType
from swarm.shared_context import SharedContext, SubTask, TaskStatus
from swarm.swarm_coordinator import SwarmCoordinator


def _make_context(agent_id, subtask_ids):
    context = SharedContext(session_id="test")
    for subtask_id in subtask_ids:
        context.add_subtask(SubTask(
            id=subtask_id,
            type="test_task",
            description="测试任务",
            assigned_agent=agent_id,
        ))
    return context


class FakeWorker:
    def __init__(self, agent_id, outcomes):
        self.agent_id = agent_id
        self.outcomes = list(outcomes)
        self.started = []
        self.active = 0
        self.max_active = 0

    async def process_subtask(self, subtask):
        self.started.append(subtask.id)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1

        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return {"answer": outcome}


def test_fail_subtask_records_status_and_event():
    context = _make_context("diagnostic_agent", ["s1"])
    context.start_subtask("s1")

    context.fail_subtask("s1", "diagnostic_agent", "模型调用失败")

    subtask = context.get_subtask("s1")
    assert subtask.status is TaskStatus.FAILED
    assert subtask.result == {"error": "模型调用失败"}

    events = context.get_events(event_type=EventType.SUBTASK_FAILED)
    assert len(events) == 1
    assert events[0].data["subtask_id"] == "s1"


def test_worker_serializes_tasks_and_records_failure():
    context = _make_context("diagnostic_agent", ["s1", "s2"])
    worker = FakeWorker("diagnostic_agent", ["ok", RuntimeError("boom")])

    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    asyncio.run(
        SwarmCoordinator._worker_execute_assigned_tasks(coordinator, worker, context)
    )

    assert worker.started == ["s1", "s2"]
    assert worker.max_active == 1, "同一个 Worker 的子任务应串行执行"
    assert context.get_subtask("s1").status is TaskStatus.COMPLETED
    assert context.get_subtask("s2").status is TaskStatus.FAILED


def test_constraints_force_diagnostic_agent_for_high_risk_question():
    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    coordinator.validator = ConstraintValidator()

    assessment = {
        "subtasks": [
            {"description": "给点建议", "assigned_agent": "consultation_agent"}
        ]
    }

    coordinator._enforce_required_agents("我突然胸痛，还呼吸困难", assessment)

    agents = [task["assigned_agent"] for task in assessment["subtasks"]]
    assert "diagnostic_agent" in agents


def test_required_agent_survives_subtask_limit():
    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    coordinator.validator = ConstraintValidator()

    assessment = {
        "subtasks": [
            {"description": f"任务{i}", "assigned_agent": "consultation_agent"}
            for i in range(5)
        ]
    }

    coordinator._enforce_required_agents("胸痛怎么办", assessment)

    agents = [task["assigned_agent"] for task in assessment["subtasks"]]
    assert len(assessment["subtasks"]) == 5
    assert "diagnostic_agent" in agents


def test_process_with_swarm_reuses_coordinator(monkeypatch):
    created = []

    class DummyCoordinator:
        def __init__(self, enable_swarm=True):
            created.append(enable_swarm)
            self.enable_swarm = enable_swarm

        async def process(self, question, context=None, session_id=None):
            return {"answer": "ok", "swarm_enabled": self.enable_swarm}

    monkeypatch.setattr(coordinator_module, "SwarmCoordinator", DummyCoordinator)
    coordinator_module.reset_default_coordinators()
    try:
        first = asyncio.run(coordinator_module.process_with_swarm("第一个问题"))
        second = asyncio.run(coordinator_module.process_with_swarm("第二个问题"))
    finally:
        coordinator_module.reset_default_coordinators()

    assert first["answer"] == second["answer"] == "ok"
    assert created == [True], "同一进程内应复用协调器，避免重复加载 Agent 和 Skills"
