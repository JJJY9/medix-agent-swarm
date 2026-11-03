"""AgentLoop 工具调用预算的回归测试（全部使用本地假实现，不访问网络）。"""
import asyncio
import json

from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse, ToolCall


class FakeLLM:
    """始终请求调用工具，只有禁用工具时才返回最终答复。"""

    def __init__(self):
        self.calls = []  # 每次调用是否携带 tools

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append(tools is not None)
        if tools is None:
            return LLMResponse(content="最终答复", tool_calls=[], finish_reason="stop")

        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id=f"call-{len(self.calls)}", name="fake_skill", arguments={})],
            finish_reason="tool_calls",
        )

    def create_tool_message(self, tool_call_id, tool_name, result):
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False),
        }


class FakeAgent:
    agent_id = "test_agent"
    config = {"temperature": 0.7}

    def __init__(self, llm_client):
        self.llm_client = llm_client
        self.executed = []

    def get_system_prompt(self):
        return "你是测试 Agent"

    def format_user_input(self, input_data):
        return input_data["question"]

    def get_tools_for_llm(self):
        return [{
            "type": "function",
            "function": {
                "name": "fake_skill",
                "description": "fake",
                "parameters": {"type": "object", "properties": {}},
            },
        }]

    async def execute_tool(self, tool_name, arguments):
        self.executed.append(tool_name)
        return {"ok": True}


def test_budget_is_enforced_and_final_answer_forced():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = FakeLLM()
    agent = FakeAgent(llm)

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    assert result["answer"] == "最终答复"
    assert result["tool_calls_used"] == 2
    assert len(agent.executed) == 2
    # 两次带工具的调用 + 一次强制收尾（不带工具），而不是空转到 10 轮
    assert llm.calls == [True, True, False]


def test_budget_resets_between_runs():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = FakeLLM()
    agent = FakeAgent(llm)

    first = asyncio.run(loop.run(agent, {"question": "第一次"}))
    second = asyncio.run(loop.run(agent, {"question": "第二次"}))

    assert first["answer"] == second["answer"] == "最终答复"
    assert first["tool_calls_used"] == second["tool_calls_used"] == 2
    assert len(agent.executed) == 4


def test_zero_budget_skips_tool_execution():
    loop = AgentLoop(max_iterations=5, max_tool_calls=0)
    llm = FakeLLM()
    agent = FakeAgent(llm)

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    assert result["answer"] == "最终答复"
    assert result["tool_calls_used"] == 0
    assert agent.executed == []
    assert llm.calls == [True, False]


class StreamingFakeLLM(FakeLLM):
    """强制收尾时走流式接口。"""

    def __init__(self):
        super().__init__()
        self.streamed = []

    async def chat_stream(self, messages, on_token=None, **kwargs):
        for piece in ["流式", "最终答复"]:
            self.streamed.append(piece)
            if on_token:
                on_token(piece)
        return "流式最终答复"


def test_forced_final_answer_streams_tokens():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = StreamingFakeLLM()
    agent = FakeAgent(llm)
    tokens = []

    result = asyncio.run(
        loop.run(agent, {"question": "测试问题"}, on_token=tokens.append)
    )

    assert result["answer"] == "流式最终答复"
    assert result["tool_calls_used"] == 2
    assert tokens == ["流式", "最终答复"]
    assert llm.streamed == ["流式", "最终答复"]
    # 工具轮仍走非流式调用；只有收尾使用 chat_stream
    assert llm.calls == [True, True]
