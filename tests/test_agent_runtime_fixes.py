"""核心运行时边界修复的回归测试（全部使用本地假实现，不访问网络）。"""
import asyncio
from types import SimpleNamespace

import pytest

from core.agent_loop import AgentLoop
from core.context import format_context
from core.llm_client import LLMClient, LLMError, LLMResponse


class FakeCompletions:
    def __init__(self, response):
        self.response = response
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return self.response


def _llm_with_response(response):
    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.7
    client.max_tokens = 8192
    completions = FakeCompletions(response)
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=completions)
    )
    return client, completions


def _text_response(text="答复"):
    message = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=None,
    )


def test_chat_with_usage_preserves_zero_temperature():
    client, completions = _llm_with_response(_text_response())

    asyncio.run(client.chat_with_usage([{"role": "user", "content": "x"}], temperature=0))

    assert completions.kwargs["temperature"] == 0


def test_chat_with_tools_preserves_zero_temperature():
    client, completions = _llm_with_response(_text_response())

    asyncio.run(client.chat_with_tools([{"role": "user", "content": "x"}], temperature=0))

    assert completions.kwargs["temperature"] == 0


def test_truncated_tool_arguments_raise():
    tool_call = SimpleNamespace(
        id="call-1",
        function=SimpleNamespace(name="search", arguments='{"query": "高血压'),
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(
            message=SimpleNamespace(content=None, tool_calls=[tool_call]),
            finish_reason="length",
        )],
        usage=None,
    )
    client, _ = _llm_with_response(response)

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(client.chat_with_tools([{"role": "user", "content": "x"}]))

    assert exc_info.value.code == "llm_truncated"
    assert exc_info.value.retryable is True


class FakeStreamClient:
    def __init__(self):
        self.chat_stream = self._chat_stream

    async def _chat_stream(self, *args, **kwargs):
        raise AssertionError("不应直接调用 chat_stream")

    async def chat_stream_retry(self, messages, on_token=None, usage_out=None, **kwargs):
        on_token("已推送的部分")
        raise LLMError("llm_rate_limit", "限流", retryable=True)


class FakeStreamAgent:
    agent_id = "stream_agent"
    config = {"temperature": 0.7}

    def __init__(self):
        self.llm_client = FakeStreamClient()


def test_complete_text_keeps_partial_stream_on_error():
    loop = AgentLoop()
    chunks = []

    text = asyncio.run(loop._complete_text(
        FakeStreamAgent(), [], on_token=chunks.append
    ))

    assert text == "已推送的部分"
    assert chunks == ["已推送的部分"]


class FailingPostProcessAgent:
    agent_id = "finalize_agent"
    config = {"temperature": 0.7}

    def __init__(self):
        self.llm_client = None

    async def post_process_result(self, result, final_answer):
        raise ValueError("post process boom")


class OneShotLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        return LLMResponse(content="最终答复", tool_calls=[], finish_reason="stop")


def test_finalize_failure_is_not_retried():
    loop = AgentLoop()
    llm = OneShotLLM()
    agent = FailingPostProcessAgent()
    agent.llm_client = llm
    agent.get_system_prompt = lambda: "系统提示"
    agent.format_user_input = lambda data: data["question"]
    agent.get_tools_for_llm = lambda: []

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    assert llm.calls == 1
    assert result["warning"] == "finalize_failed"
    assert "finalize_failed" not in result["answer"]


async def _post_process_noop(result, final_answer):
    return result


def test_finalize_empty_answer_gets_fallback():
    loop = AgentLoop()
    agent = SimpleNamespace(
        agent_id="empty_agent",
        post_process_result=_post_process_noop,
    )
    state = loop.state_manager.create_state("t", "empty_agent", {}, 3)

    result = asyncio.run(loop._finalize(agent, state, "  ", None, 0))

    assert result["answer"]
    assert "未能生成有效答复" in result["answer"]


def test_format_context_filters_nested_none():
    text = format_context({
        "recent_metrics": {"weight": 70, "blood_pressure": None},
        "recent_symptoms": ["头晕", None],
    })

    assert "None" not in text
    assert "weight=70" in text
    assert "头晕" in text


class FakeMemory:
    def __init__(self):
        self.added = []

    def get_history(self, session_id, limit=5):
        return []

    def add_message(self, session_id, role, content):
        self.added.append((role, content))


def test_short_term_memory_records_raw_question():
    memory = FakeMemory()
    loop = AgentLoop(short_term_memory=memory)
    agent = SimpleNamespace(
        agent_id="mem_agent",
        config={},
        llm_client=OneShotLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: (
            f"背景信息：{data.get('context')}\n用户问题：{data['question']}"
        ),
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )

    asyncio.run(loop.run(
        agent,
        {"question": "原始问题", "context": {"age": 40}},
        session_id="s1",
    ))

    assert memory.added[0] == ("user", "原始问题")
