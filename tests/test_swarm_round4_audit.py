"""第四轮 Swarm 编排审计回归：原始主诉、未完成模块与用量统计。"""
import asyncio
from types import SimpleNamespace

from agents.base_agent import BaseAgent
from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse
from swarm.lead_agent import LeadAgent
from swarm.shared_context import SharedContext, SubTask
from swarm.swarm_coordinator import SwarmCoordinator


class RecordingLoop:
    def __init__(self):
        self.input_data = None
        self.memory_question = None

    async def run(self, agent, input_data, **kwargs):
        self.input_data = input_data
        self.memory_question = kwargs.get("memory_question")
        return {"answer": "ok"}


class _TestWorker(BaseAgent):
    def get_system_prompt(self):
        return ""

    def register_tools(self):
        pass


def test_worker_prompt_includes_original_question():
    agent = object.__new__(_TestWorker)
    agent.agent_id = "diagnostic_agent"
    agent.loop = RecordingLoop()
    shared = SimpleNamespace(
        session_id="s1",
        data={
            "original_question": "胸口压榨样疼痛放射到左臂，伴出汗",
            "patient_context": {"age": 68},
        },
    )
    subtask = SimpleNamespace(
        id="t1",
        type="diagnostic_agent_task",
        description="评估胸痛症状的风险等级和紧急程度",
    )

    result = asyncio.run(agent.process_subtask(subtask, shared_context=shared))

    assert result["answer"] == "ok"
    question = agent.loop.input_data["question"]
    assert "胸口压榨样疼痛放射到左臂" in question
    assert "本次任务重点" in question
    assert agent.loop.memory_question == "胸口压榨样疼痛放射到左臂，伴出汗"


class CapturingLLM:
    def __init__(self, reply="汇总答复"):
        self.reply = reply
        self.messages = None

    async def chat(self, messages):
        self.messages = messages
        return self.reply


def _context_with_failed_task():
    context = SharedContext(session_id="synthesis")
    context.add_subtask(SubTask(
        id="s1",
        type="consultation_agent_task",
        description="给出建议",
        assigned_agent="consultation_agent",
    ))
    context.add_subtask(SubTask(
        id="s2",
        type="research_agent_task",
        description="查指南",
        assigned_agent="research_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask("s1", "consultation_agent", {"answer": "建议休息。"})
    context.start_subtask("s2")
    context.fail_subtask("s2", "research_agent", "research boom")
    return context


def test_synthesis_discloses_failed_modules_without_timeout():
    llm = CapturingLLM()
    context = _context_with_failed_task()

    result = asyncio.run(LeadAgent(llm_client=llm).synthesize_results(
        "胸痛怎么办", context, timeout_occurred=False
    ))

    assert result == "汇总答复"
    prompt = llm.messages[0]["content"]
    assert "research_agent_task" in prompt
    assert "部分分析结果" in prompt
    assert "如果有分析模块未完成" in prompt


class UsageFailingAgent:
    agent_id = "finalize_agent"

    async def post_process_result(self, result, final_answer):
        raise RuntimeError("post process boom")


def test_finalize_failure_keeps_usage():
    loop = AgentLoop()
    agent = UsageFailingAgent()
    agent.config = {"temperature": 0.7}

    class OneShotUsageLLM:
        async def chat_with_tools(self, messages, tools=None, **kwargs):
            return LLMResponse(
                content="最终答复",
                tool_calls=[],
                finish_reason="stop",
                usage={"total_tokens": 99},
            )

    agent.llm_client = OneShotUsageLLM()
    agent.get_system_prompt = lambda: "系统提示"
    agent.format_user_input = lambda data: data["question"]
    agent.get_tools_for_llm = lambda: []

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    # 后处理失败不再回退成通用错误文案，回答与用量都保留
    assert result["answer"] == "最终答复"
    assert result.get("warning") != "finalize_failed"
    assert result["usage"]["total_tokens"] == 99
    assert result["llm_calls"] >= 1


class FailingUsageError(RuntimeError):
    def __init__(self, message, usage, llm_calls):
        super().__init__(message)
        self.usage = usage
        self.llm_calls = llm_calls


class FailingWorker:
    agent_id = "research_agent"

    async def process_subtask(self, subtask, shared_context=None):
        raise FailingUsageError("boom", {"total_tokens": 42}, 2)


def test_failed_subtask_usage_is_collected():
    context = SharedContext(session_id="usage")
    context.add_subtask(SubTask(
        id="s1",
        type="research_agent_task",
        description="查指南",
        assigned_agent="research_agent",
    ))
    context.start_subtask("s1")
    coordinator = object.__new__(SwarmCoordinator)

    asyncio.run(coordinator._execute_single_subtask(
        FailingWorker(), context.get_subtask("s1"), context
    ))

    assert context.data["failed_usage"] == [
        {"usage": {"total_tokens": 42}, "llm_calls": 2}
    ]
