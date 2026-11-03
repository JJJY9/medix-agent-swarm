"""LLM 重试逻辑的离线测试（不访问网络）。"""
import asyncio
import json

import pytest

from core.llm_client import LLMClient, LLMError
from swarm.lead_agent import LeadAgent
from swarm.shared_context import SharedContext, SubTask


def retryable_error() -> LLMError:
    return LLMError("rate_limit", "请求过于频繁", retryable=True)


class StubClient(LLMClient):
    """绕过真实客户端初始化，按预设序列返回结果或抛错。"""

    RETRY_BASE_DELAY = 0

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    async def chat_with_usage(self, messages, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream(self, messages, on_token=None, usage_out=None, **kwargs):
        self.calls += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item

        deltas = item.get("deltas") or []
        for delta in deltas:
            if on_token:
                on_token(delta)

        if item.get("usage") and usage_out is not None:
            usage_out.update(item["usage"])
        if item.get("error_after"):
            raise item["error_after"]
        return item.get("text", "")


def test_usage_retry_succeeds_after_transient_error():
    client = StubClient([
        retryable_error(),
        ("答复", {"total_tokens": 10}),
    ])

    text, usage = asyncio.run(client.chat_with_usage_retry([], max_retries=3))

    assert text == "答复"
    assert usage == {"total_tokens": 10}
    assert client.calls == 2


def test_usage_retry_does_not_retry_non_retryable_error():
    client = StubClient([LLMError("auth_error", "密钥无效")])

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(client.chat_with_usage_retry([], max_retries=3))

    assert exc_info.value.code == "auth_error"
    assert client.calls == 1


def test_usage_retry_exhausts_attempts():
    client = StubClient([retryable_error(), retryable_error(), retryable_error()])

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(client.chat_with_usage_retry([], max_retries=3))

    assert exc_info.value.retryable is True
    assert client.calls == 3


def test_stream_retry_retries_before_any_output():
    client = StubClient([
        retryable_error(),
        {
            "deltas": ["你", "好"],
            "text": "你好",
            "usage": {"total_tokens": 6},
        },
    ])
    tokens = []
    usage = {}

    text = asyncio.run(client.chat_stream_retry(
        [], on_token=tokens.append, usage_out=usage, max_retries=3
    ))

    assert text == "你好"
    assert tokens == ["你", "好"]
    assert usage == {"total_tokens": 6}
    assert client.calls == 2


def test_stream_retry_stops_after_partial_output():
    client = StubClient([{
        "deltas": ["部分"],
        "error_after": retryable_error(),
    }])
    tokens = []

    with pytest.raises(LLMError):
        asyncio.run(client.chat_stream_retry(
            [], on_token=tokens.append, max_retries=3
        ))

    # 已经推送过增量时不能重试，否则会重复输出
    assert tokens == ["部分"]
    assert client.calls == 1


class RetryDecomposeLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_usage_retry(self, messages, **kwargs):
        self.calls += 1
        return (
            json.dumps({
                "subtasks": [{
                    "description": "分析症状",
                    "assigned_agent": "diagnostic_agent",
                }]
            }),
            {"prompt_tokens": 9, "completion_tokens": 3, "total_tokens": 12},
        )


def test_lead_agent_uses_retry_for_decomposition():
    llm = RetryDecomposeLLM()
    usage = {}

    result = asyncio.run(
        LeadAgent(llm_client=llm).assess_and_decompose("头痛", usage_out=usage)
    )

    assert llm.calls == 1
    assert result["subtasks"][0]["assigned_agent"] == "diagnostic_agent"
    assert usage["total_tokens"] == 12


class StreamRetryLLM:
    def __init__(self):
        self.calls = 0

    async def chat_stream_retry(self, messages, on_token=None, usage_out=None, **kwargs):
        self.calls += 1
        if on_token:
            on_token("汇总")
        if usage_out is not None:
            usage_out.update({"total_tokens": 7})
        return "汇总答复"


def make_context() -> SharedContext:
    context = SharedContext(session_id="retry-test")
    context.add_subtask(SubTask(
        id="s1",
        type="consultation_agent_task",
        description="给出建议",
        assigned_agent="consultation_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask("s1", "consultation_agent", {"answer": "建议休息。"})
    return context


def test_lead_agent_synthesis_uses_stream_retry():
    llm = StreamRetryLLM()
    tokens = []
    usage = {}

    text = asyncio.run(LeadAgent(llm_client=llm).synthesize_results(
        "问题", make_context(), on_token=tokens.append, usage_out=usage
    ))

    assert llm.calls == 1
    assert text == "汇总答复"
    assert tokens == ["汇总"]
    assert usage["total_tokens"] == 7
