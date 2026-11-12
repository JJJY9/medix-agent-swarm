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


class MultiToolLLM(FakeLLM):
    """单次响应里请求多个工具调用。"""

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append(tools is not None)
        if tools is None:
            return LLMResponse(
                content="最终答复", tool_calls=[], finish_reason="stop"
            )

        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(
                    id=f"call-{index}-{len(self.calls)}",
                    name="fake_skill",
                    arguments={},
                )
                for index in range(3)
            ],
            finish_reason="tool_calls",
        )


def test_budget_caps_batched_tool_calls():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = MultiToolLLM()
    agent = FakeAgent(llm)

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    assert result["answer"] == "最终答复"
    assert result["tool_calls_used"] == 2
    assert len(agent.executed) == 2
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


class SourceAgent(FakeAgent):
    async def execute_tool(self, tool_name, arguments):
        self.executed.append(tool_name)
        return {
            "ok": True,
            "sources": [{
                "source": "临床指南数据库",
                "filename": "20.txt",
                "snippet": "指南片段",
                "score": 0.8,
            }],
        }


def test_tool_sources_are_collected_and_deduplicated():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = FakeLLM()
    agent = SourceAgent(llm)

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    assert result["answer"] == "最终答复"
    assert len(result["sources"]) == 1
    assert result["sources"][0]["filename"] == "20.txt"


class IntSourcesAgent(FakeAgent):
    async def execute_tool(self, tool_name, arguments):
        self.executed.append(tool_name)
        # deep_research 历史上把 sources 用作“来源数量”
        return {"ok": True, "sources": 5}


class RaisingToolAgent(FakeAgent):
    async def execute_tool(self, tool_name, arguments):
        raise RuntimeError("skill boom")


class UnserializableAgent(FakeAgent):
    async def execute_tool(self, tool_name, arguments):
        return {"ok": True, "obj": object()}


def test_non_list_sources_are_ignored():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    agent = IntSourcesAgent(FakeLLM())

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    assert result["answer"] == "最终答复"
    assert result["sources"] == []


def test_tool_exception_still_produces_tool_response():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    agent = RaisingToolAgent(FakeLLM())

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    # 工具抛异常也应被记录为 tool 结果，历史保持合法，最终仍能收尾
    assert result["answer"] == "最终答复"


def test_unserializable_tool_result_falls_back():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    agent = UnserializableAgent(FakeLLM())

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    assert result["answer"] == "最终答复"


def test_max_iterations_fallback_records_user_question():
    """强制收尾也要把用户问题写进短期记忆，不能留下孤立回答。"""
    from memory import ShortTermMemory

    memory = ShortTermMemory(storage_type="memory")
    session_id = "max-iterations-memory"
    memory.clear_session(session_id)
    try:
        loop = AgentLoop(
            max_iterations=1,
            max_tool_calls=10,
            short_term_memory=memory,
        )
        result = asyncio.run(loop.run(
            FakeAgent(FakeLLM()),
            {"question": "需要记录的原始问题"},
            session_id=session_id,
        ))

        assert result["warning"] == "max_iterations_reached"
        history = memory.get_session(session_id).messages
        # 工具轮次与最终回答按时间顺序写入，提问必须在最前面
        assert history[0]["role"] == "user"
        assert history[0]["content"] == "需要记录的原始问题"
        assert any(
            row["role"] == "tool" for row in history
        )
        assert history[-1]["role"] == "assistant"
        assert history[-1]["content"] == result["answer"]
    finally:
        memory.clear_session(session_id)


class EmptyFinalLLM(FakeLLM):
    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append(tools is not None)
        if tools is None:
            return LLMResponse(
                content="", tool_calls=[], finish_reason="stop"
            )
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="call-empty", name="fake_skill", arguments={})
            ],
            finish_reason="tool_calls",
        )


def test_empty_final_answer_is_marked_degraded():
    loop = AgentLoop(max_iterations=5, max_tool_calls=1)
    agent = FakeAgent(EmptyFinalLLM())

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    # 兜底道歉不是有效答复，Swarm/统计需要能识别
    assert result["degraded"] is True
    assert result["warning"] == "empty_answer"
    assert "抱歉" in result["answer"]


class TruncatedFinalLLM(FakeLLM):
    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls.append(tools is not None)
        if tools is None:
            return LLMResponse(
                content="部分医疗建议", tool_calls=[], finish_reason="length"
            )
        return LLMResponse(
            content=None,
            tool_calls=[
                ToolCall(id="call-trunc", name="fake_skill", arguments={})
            ],
            finish_reason="tool_calls",
        )


def test_truncated_non_stream_answer_gets_notice():
    loop = AgentLoop(max_iterations=5, max_tool_calls=0)
    agent = FakeAgent(TruncatedFinalLLM())

    result = asyncio.run(loop.run(agent, {"question": "测试问题"}))

    assert "部分医疗建议" in result["answer"]
    assert "达到长度上限" in result["answer"]
