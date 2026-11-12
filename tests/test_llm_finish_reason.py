"""LLM 截断/内容过滤响应的展示回归测试。"""
import asyncio
from types import SimpleNamespace

from core.agent_loop import AgentLoop
from core.llm_client import LLMClient, LLMResponse


async def _post_process_noop(result, final_answer):
    return result


class FinishReasonLLM:
    def __init__(self, finish_reason, content="部分答复"):
        self.finish_reason = finish_reason
        self.content = content

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        return LLMResponse(
            content=self.content,
            tool_calls=[],
            finish_reason=self.finish_reason,
        )


def _run_agent(finish_reason):
    loop = AgentLoop()
    agent = SimpleNamespace(
        agent_id="consultation_agent",
        config={},
        llm_client=FinishReasonLLM(finish_reason),
        get_system_prompt=lambda: "系统提示",
        format_user_input=lambda data: data["question"],
        get_tools_for_llm=lambda: [],
        post_process_result=_post_process_noop,
    )
    return asyncio.run(loop.run(agent, {"question": "测试问题"}))


def test_length_finish_reason_marks_answer_truncated():
    result = _run_agent("length")

    assert "部分答复" in result["answer"]
    assert "截断" in result["answer"]


def test_content_filter_finish_reason_is_reported():
    result = _run_agent("content_filter")

    assert "部分答复" in result["answer"]
    assert "安全策略" in result["answer"]


def _chunk(content=None, finish_reason=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(
            delta=SimpleNamespace(content=content),
            finish_reason=finish_reason,
        )],
        usage=None,
    )


class FakeStream:
    def __init__(self, chunks):
        self._chunks = iter(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return next(self._chunks)
        except StopIteration:
            raise StopAsyncIteration

    async def aclose(self):
        return None


class FakeStreamCompletions:
    def __init__(self, chunks):
        self.chunks = chunks

    async def create(self, **kwargs):
        return FakeStream(self.chunks)


def _stream_client(chunks):
    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.7
    client.max_tokens = 100
    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=FakeStreamCompletions(chunks)
        )
    )
    return client


def test_stream_length_finish_reason_appends_notice():
    client = _stream_client([
        _chunk("部分"),
        _chunk("答复", finish_reason="length"),
    ])
    tokens = []

    text = asyncio.run(
        client.chat_stream(
            [{"role": "user", "content": "x"}], on_token=tokens.append
        )
    )

    assert "部分答复" in text
    assert "截断" in text
    assert any("截断" in token for token in tokens)
