"""LLM 流式接口的单元测试（不访问网络）。"""
import asyncio
from types import SimpleNamespace

from core.llm_client import LLMClient


def chunk(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs

        async def generate():
            for item in self.chunks:
                yield item

        return generate()


class FakeOpenAI:
    def __init__(self, chunks):
        self.chat = SimpleNamespace(completions=FakeCompletions(chunks))


def make_client(chunks):
    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.7
    client.max_tokens = 128
    client.client = FakeOpenAI(chunks)
    return client


def test_chat_stream_accumulates_and_emits_deltas():
    client = make_client([chunk("你好"), chunk("，"), chunk("世界")])
    received = []

    text = asyncio.run(
        client.chat_stream(
            [{"role": "user", "content": "打个招呼"}],
            on_token=received.append,
        )
    )

    assert text == "你好，世界"
    assert received == ["你好", "，", "世界"]
    assert client.client.chat.completions.kwargs["stream"] is True


def test_chat_stream_skips_empty_chunks():
    client = make_client([
        chunk("前"),
        SimpleNamespace(choices=[]),
        chunk(None),
        chunk("后"),
    ])

    received = []
    text = asyncio.run(client.chat_stream([], on_token=received.append))

    assert text == "前后"
    assert received == ["前", "后"]


def test_chat_stream_survives_callback_error():
    client = make_client([chunk("A"), chunk("B")])

    def broken_callback(_delta):
        raise RuntimeError("consumer crashed")

    text = asyncio.run(client.chat_stream([], on_token=broken_callback))

    # 消费方出错不能中断模型调用，完整文本仍应返回
    assert text == "AB"
