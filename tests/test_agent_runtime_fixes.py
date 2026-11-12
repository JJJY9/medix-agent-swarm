"""核心运行时边界修复的回归测试（全部使用本地假实现，不访问网络）。"""
import asyncio
from types import SimpleNamespace

import pytest

from core.agent_loop import AgentLoop
from core.context import format_context
from core.llm_client import LLMClient, LLMError, LLMResponse, ToolCall


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

    assert text.startswith("已推送的部分")
    assert "回答不完整" in text
    assert chunks[0] == "已推送的部分"
    assert "回答不完整" in chunks[-1]


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

    # 后处理失败只记录警告，不回退成通用错误文案，也不重复调用模型
    assert llm.calls == 1
    assert result["answer"] == "最终答复"
    assert result.get("warning") != "finalize_failed"


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


class CancellingLLM:
    async def chat_with_tools(self, messages, tools=None, **kwargs):
        raise asyncio.CancelledError


def test_cancelled_run_does_not_leave_user_message_in_memory():
    memory = FakeMemory()
    loop = AgentLoop(short_term_memory=memory)
    agent = SimpleNamespace(
        agent_id="cancel_agent",
        config={},
        llm_client=CancellingLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: data["question"],
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(loop.run(agent, {"question": "被取消的问题"}, session_id="s1"))

    assert memory.added == []


def test_finalize_failure_keeps_answer_and_memory():
    memory = FakeMemory()
    loop = AgentLoop(short_term_memory=memory)
    agent = FailingPostProcessAgent()
    llm = OneShotLLM()
    agent.llm_client = llm
    agent.get_system_prompt = lambda: "系统提示"
    agent.format_user_input = lambda data: data["question"]
    agent.get_tools_for_llm = lambda: []

    result = asyncio.run(loop.run(agent, {"question": "测试"}, session_id="s1"))

    # 后处理失败不应作废整轮咨询：回答与记忆保持一致
    assert result["answer"] == "最终答复"
    assert memory.added == [
        ("user", "测试"),
        ("assistant", "最终答复"),
    ]
    memory = FakeMemory()
    loop = AgentLoop(short_term_memory=memory)
    seen = {}
    agent = SimpleNamespace(
        agent_id="memory_question_agent",
        config={},
        llm_client=OneShotLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: seen.setdefault(
            "prompt", data["question"]
        ),
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )

    asyncio.run(loop.run(
        agent,
        {"question": "增强后的提示", "memory_question": "原始问题"},
        session_id="s1",
    ))

    assert seen["prompt"] == "增强后的提示"
    assert memory.added[0] == ("user", "原始问题")


class RecordingValidator:
    def __init__(self):
        self.seen = None

    def validate_output(self, agent_id, output, user_input=""):
        self.seen = (agent_id, user_input)
        return {"valid": True, "violations": [], "auto_fixable": []}


def test_agent_loop_passes_original_question_to_validator():
    loop = AgentLoop()
    validator = RecordingValidator()
    loop.validator = validator
    loop.auto_fixer = None
    agent = SimpleNamespace(
        agent_id="validated_agent",
        config={},
        llm_client=OneShotLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: data["question"],
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )

    asyncio.run(loop.run(
        agent,
        {"question": "增强后的提示", "memory_question": "原始问题"},
    ))

    assert validator.seen == ("validated_agent", "原始问题")


class AlwaysToolLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if tools is None:
            return LLMResponse(
                content="强制收尾答复", tool_calls=[], finish_reason="stop"
            )
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(
                id=f"call-{self.calls}", name="fake_skill", arguments={},
            )],
            finish_reason="tool_calls",
        )

    def create_tool_message(self, tool_call_id, tool_name, result):
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": "{}",
        }


def test_max_iterations_fallback_runs_validator():
    loop = AgentLoop(max_iterations=1, max_tool_calls=10)
    validator = RecordingValidator()
    loop.validator = validator
    loop.auto_fixer = None

    async def execute_tool(tool_name, arguments):
        return {"ok": True}

    agent = SimpleNamespace(
        agent_id="validated_agent",
        config={},
        llm_client=AlwaysToolLLM(),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: data["question"],
        get_tools_for_llm=lambda: [{
            "type": "function",
            "function": {
                "name": "fake_skill",
                "description": "fake",
                "parameters": {"type": "object", "properties": {}},
            },
        }],
        execute_tool=execute_tool,
        post_process_result=_post_process_noop,
    )

    result = asyncio.run(loop.run(
        agent,
        {"question": "增强后的提示", "memory_question": "原始问题"},
    ))

    assert result["warning"] == "max_iterations_reached"
    assert result["answer"] == "强制收尾答复"
    # 强制收尾同样经过 validator，且使用原始用户问题
    assert validator.seen == ("validated_agent", "原始问题")
