"""Swarm 上下文传递、记忆隔离与风险解析的回归测试（全部离线）。"""
import asyncio
from types import SimpleNamespace

from agents.base_agent import BaseAgent
from agents.diagnostic_agent import DiagnosticAgent
from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse
from swarm.lead_agent import LeadAgent
from swarm.shared_context import SharedContext, SubTask


class DummyAgent(BaseAgent):
    def get_system_prompt(self):
        return "系统提示"

    def register_tools(self):
        pass


class RecordingLoop:
    def __init__(self):
        self.calls = []

    async def run(self, agent, input_data, session_id=None, on_token=None,
                  persist_memory=True, memory_question=None):
        self.calls.append((input_data, session_id, persist_memory))
        return {"answer": "ok"}


def _subtask():
    return SimpleNamespace(description="分析症状", id="t1", type="diagnosis")


def test_process_subtask_uses_explicit_shared_context():
    agent = DummyAgent("dummy", {}, llm_client=object())
    loop = RecordingLoop()
    agent.loop = loop

    sc = SharedContext(session_id="s1")
    sc.data["patient_context"] = {"age": 50}
    result = asyncio.run(agent.process_subtask(_subtask(), shared_context=sc))

    assert result["answer"] == "ok"
    input_data, session_id, persist_memory = loop.calls[0]
    assert session_id == "s1"
    assert input_data["context"] == {"age": 50}
    assert input_data["session_id"] == "s1"
    assert persist_memory is False


def test_process_subtask_falls_back_to_attached_context():
    agent = DummyAgent("dummy", {}, llm_client=object())
    loop = RecordingLoop()
    agent.loop = loop

    sc = SharedContext(session_id="s2")
    sc.data["patient_context"] = {"sex": "女"}
    agent.attach_shared_context(sc)

    asyncio.run(agent.process_subtask(_subtask()))

    input_data, session_id, _ = loop.calls[0]
    assert session_id == "s2"
    assert input_data["context"] == {"sex": "女"}


class StrictMemory:
    def __init__(self):
        self.added = []

    def get_history(self, session_id, limit=5):
        raise AssertionError("persist_memory=False 不应读取历史")

    def add_message(self, session_id, role, content):
        self.added.append((role, content))


class OneShotLLM:
    async def chat_with_tools(self, messages, tools=None, **kwargs):
        return LLMResponse(content="最终答复", tool_calls=[], finish_reason="stop")


async def _post_process_noop(result, final_answer):
    return result


def test_agent_loop_skips_memory_when_disabled():
    memory = StrictMemory()
    loop = AgentLoop(short_term_memory=memory)
    agent = SimpleNamespace(
        agent_id="mem_agent",
        config={},
        llm_client=OneShotLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: data["question"],
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )

    result = asyncio.run(loop.run(
        agent,
        {"question": "问题"},
        session_id="s1",
        persist_memory=False,
    ))

    assert result["answer"] == "最终答复"
    assert memory.added == []


def test_diagnostic_risk_prefers_explicit_label():
    agent = object.__new__(DiagnosticAgent)

    result = asyncio.run(agent.post_process_result(
        {},
        "【风险评估】\n风险等级：低\n患者有高血压病史，属于高风险人群。",
    ))

    assert result["risk_level"] == "low"


def test_diagnostic_risk_emergency_label():
    agent = object.__new__(DiagnosticAgent)

    result = asyncio.run(agent.post_process_result(
        {},
        "风险等级：紧急\n建议立即就医。",
    ))

    assert result["risk_level"] == "emergency"


class PromptCaptureLLM:
    def __init__(self):
        self.prompts = []

    async def chat_with_usage(self, messages, **kwargs):
        self.prompts.append(messages[-1]["content"])
        return "汇总答复", {}


def test_lead_lists_failed_subtasks_as_incomplete():
    llm = PromptCaptureLLM()
    lead = object.__new__(LeadAgent)
    lead.llm_client = llm

    sc = SharedContext(session_id="s1")
    sc.add_subtask(SubTask(
        id="t1", type="diagnosis", description="诊断",
        assigned_agent="diagnostic_agent",
    ))
    sc.add_subtask(SubTask(
        id="t2", type="research", description="研究",
        assigned_agent="research_agent",
    ))
    sc.complete_subtask("t1", "diagnostic_agent", {"answer": "ok"})
    sc.fail_subtask("t2", "research_agent", "系统超时")

    text = asyncio.run(lead.synthesize_results("问题", sc, timeout_occurred=True))

    assert text == "汇总答复"
    assert "research" in llm.prompts[0]
