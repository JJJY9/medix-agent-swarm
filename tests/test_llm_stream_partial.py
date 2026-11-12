"""流式中断时保留已生成文本与用量的回归测试。"""
import asyncio
from types import SimpleNamespace

import pytest

from core.llm_client import LLMClient, LLMError
from swarm.lead_agent import LeadAgent
from swarm.shared_context import SharedContext, SubTask


class PartialFailLeadLLM:
    async def chat_stream_retry(
        self, messages, on_token=None, usage_out=None, **kwargs
    ):
        if on_token:
            on_token("已生成部分")
        if usage_out is not None:
            usage_out.update({"total_tokens": 7})
        error = LLMError("llm_error", "连接中断", retryable=False)
        error.partial_text = "已生成部分"
        raise error


def _context():
    context = SharedContext(session_id="partial-test")
    context.add_subtask(SubTask(
        id="s1",
        type="test_task",
        description="测试任务",
        assigned_agent="consultation_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask("s1", "consultation_agent", {"answer": "贡献"})
    return context


def test_lead_agent_keeps_partial_synthesis_on_stream_error():
    usage = {}
    tokens = []

    text = asyncio.run(
        LeadAgent(llm_client=PartialFailLeadLLM()).synthesize_results(
            "测试问题",
            _context(),
            on_token=tokens.append,
            usage_out=usage,
        )
    )

    assert text.startswith("已生成部分")
    assert "中断" in text
    assert usage["total_tokens"] == 7
    assert tokens == ["已生成部分"]


def _chunk(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content),
            finish_reason=None,
        )],
        usage=None,
    )


class FailAfterFirstChunk:
    def __init__(self):
        self._sent = False

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._sent:
            self._sent = True
            return _chunk("部分")
        raise RuntimeError("connection reset")

    async def aclose(self):
        return None


class FailingCompletions:
    async def create(self, **kwargs):
        return FailAfterFirstChunk()


def _stream_client():
    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.7
    client.max_tokens = 100
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FailingCompletions())
    )
    return client


def test_stream_retry_carries_partial_text_on_error():
    client = _stream_client()
    tokens = []

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(client.chat_stream_retry(
            [{"role": "user", "content": "x"}], on_token=tokens.append
        ))

    assert tokens == ["部分"]
    assert exc_info.value.partial_text == "部分"
