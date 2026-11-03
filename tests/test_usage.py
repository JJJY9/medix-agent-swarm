"""Token 用量统计的单元测试（全部使用假实现，不访问网络）。"""
import asyncio
import json
from types import SimpleNamespace

from api_server import _serialize_result
from core.agent_loop import AgentLoop
from core.llm_client import LLMClient, LLMResponse, ToolCall, usage_to_dict
from core.usage import merge_usage, merge_usages
from swarm.lead_agent import LeadAgent
from swarm.shared_context import SharedContext, SubTask
from swarm.swarm_coordinator import SwarmCoordinator


# ===== 用量合并 =====

def test_merge_usage_adds_all_keys():
    total = {}
    merge_usage(total, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    merge_usage(total, {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})

    assert total == {"prompt_tokens": 13, "completion_tokens": 7, "total_tokens": 20}


def test_merge_usage_ignores_missing_and_invalid_values():
    total = merge_usage({}, None)
    merge_usage(total, {"prompt_tokens": "12", "total_tokens": "not-a-number"})

    assert total["prompt_tokens"] == 12
    assert total["completion_tokens"] == 0
    assert total["total_tokens"] == 0


def test_merge_usages_handles_multiple_sources():
    assert merge_usages(
        {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        None,
        {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5},
    ) == {"prompt_tokens": 3, "completion_tokens": 4, "total_tokens": 7}


# ===== LLM 客户端解析 =====

def test_usage_to_dict_accepts_sdk_object():
    raw = SimpleNamespace(prompt_tokens=11, completion_tokens=7, total_tokens=18)
    assert usage_to_dict(raw) == {
        "prompt_tokens": 11,
        "completion_tokens": 7,
        "total_tokens": 18,
    }


def test_usage_to_dict_fills_missing_total():
    raw = SimpleNamespace(prompt_tokens=4, completion_tokens=6, total_tokens=None)
    assert usage_to_dict(raw)["total_tokens"] == 10


def test_usage_to_dict_returns_none_without_fields():
    assert usage_to_dict(None) is None
    assert usage_to_dict(SimpleNamespace()) is None


def text_chunk(content):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=content))]
    )


def usage_chunk(prompt, completion):
    return SimpleNamespace(
        choices=[],
        usage=SimpleNamespace(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
        ),
    )


class FakeCompletions:
    def __init__(self, chunks):
        self.chunks = chunks
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)

        async def generate():
            for item in self.chunks:
                yield item

        return generate()


def make_client(chunks):
    client = LLMClient.__new__(LLMClient)
    client.model_type = "openai_compatible"
    client.model_name = "test-model"
    client.temperature = 0.7
    client.max_tokens = 128
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FakeCompletions(chunks))
    )
    return client


def test_chat_stream_captures_usage_when_requested():
    client = make_client([
        text_chunk("你好"),
        usage_chunk(12, 3),
        text_chunk("世界"),
    ])
    usage = {}

    text = asyncio.run(client.chat_stream([], usage_out=usage))

    assert text == "你好世界"
    assert usage == {"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15}
    assert client.client.chat.completions.calls[0]["stream_options"] == {
        "include_usage": True
    }


def test_chat_stream_omits_stream_options_without_usage_out():
    client = make_client([text_chunk("A")])

    asyncio.run(client.chat_stream([]))

    assert "stream_options" not in client.client.chat.completions.calls[0]


class FallbackCompletions(FakeCompletions):
    """第一次拒绝 stream_options，模拟不兼容的 OpenAI 兼容服务。"""

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        if "stream_options" in kwargs:
            raise TypeError("unexpected keyword argument 'stream_options'")

        async def generate():
            for item in self.chunks:
                yield item

        return generate()


def test_chat_stream_retries_without_stream_options():
    client = make_client([text_chunk("回退成功")])
    client.client = SimpleNamespace(
        chat=SimpleNamespace(completions=FallbackCompletions([text_chunk("回退成功")]))
    )
    usage = {}

    text = asyncio.run(client.chat_stream([], usage_out=usage))

    assert text == "回退成功"
    assert usage == {}
    assert len(client.client.chat.completions.calls) == 2
    assert "stream_options" not in client.client.chat.completions.calls[1]


# ===== AgentLoop 累计 =====

class FakeLLM:
    """每次调用返回固定用量，工具调用轮之后强制收尾。"""

    def __init__(self):
        self.usage_per_call = [
            {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
        ]
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        usage = self.usage_per_call[min(self.calls, len(self.usage_per_call) - 1)]
        self.calls += 1
        if tools is None:
            return LLMResponse(
                content="最终答复", tool_calls=[], finish_reason="stop", usage=usage
            )
        return LLMResponse(
            content=None,
            tool_calls=[ToolCall(id=f"call-{self.calls}", name="fake_skill", arguments={})],
            finish_reason="tool_calls",
            usage=usage,
        )

    def create_tool_message(self, tool_call_id, tool_name, result):
        return {"role": "tool", "tool_call_id": tool_call_id, "name": tool_name,
                "content": json.dumps(result, ensure_ascii=False)}


class FakeAgent:
    agent_id = "test_agent"
    config = {"temperature": 0.7}

    def __init__(self, llm_client):
        self.llm_client = llm_client

    def get_system_prompt(self):
        return "你是测试 Agent"

    def format_user_input(self, input_data):
        return input_data["question"]

    def get_tools_for_llm(self):
        return [{"type": "function", "function": {"name": "fake_skill",
                 "description": "fake", "parameters": {"type": "object", "properties": {}}}}]

    async def execute_tool(self, tool_name, arguments):
        return {"ok": True}


def test_agent_loop_accumulates_usage_and_call_count():
    loop = AgentLoop(max_iterations=10, max_tool_calls=2)
    llm = FakeLLM()

    result = asyncio.run(loop.run(FakeAgent(llm), {"question": "测试"}))

    assert result["llm_calls"] == 3
    assert result["usage"] == {
        "prompt_tokens": 35,
        "completion_tokens": 13,
        "total_tokens": 48,
    }


class StreamingLLM(FakeLLM):
    async def chat_stream(self, messages, on_token=None, usage_out=None, **kwargs):
        if usage_out is not None:
            usage_out.update(
                {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10}
            )
        return "流式最终答复"


def test_agent_loop_stream_usage_is_merged():
    loop = AgentLoop(max_iterations=10, max_tool_calls=1)
    llm = StreamingLLM()

    result = asyncio.run(
        loop.run(FakeAgent(llm), {"question": "测试"}, on_token=lambda _t: None)
    )

    assert result["answer"] == "流式最终答复"
    assert result["llm_calls"] == 2
    # 工具轮 12 + 流式收尾 10
    assert result["usage"]["total_tokens"] == 22


# ===== LeadAgent / 协调器 =====

class DecomposeLLM:
    async def chat_with_usage(self, messages, **kwargs):
        content = json.dumps({
            "subtasks": [{"description": "分析症状", "assigned_agent": "diagnostic_agent"}]
        })
        return content, {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}


def test_lead_agent_records_decomposition_usage():
    llm = DecomposeLLM()
    usage = {}

    result = asyncio.run(
        LeadAgent(llm_client=llm).assess_and_decompose("头痛", usage_out=usage)
    )

    assert result["subtasks"][0]["assigned_agent"] == "diagnostic_agent"
    assert usage == {"prompt_tokens": 30, "completion_tokens": 10, "total_tokens": 40}


def test_collect_insights_exposes_usage_and_llm_calls():
    context = SharedContext(session_id="usage-test")
    context.add_subtask(SubTask(
        id="s1", type="diagnostic_agent_task",
        description="分析症状", assigned_agent="diagnostic_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask(
        "s1", "diagnostic_agent",
        {
            "answer": "建议急诊评估。",
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "llm_calls": 2,
        },
    )

    contributions, _ = SwarmCoordinator._collect_insights(context)

    assert contributions[0]["usage"]["total_tokens"] == 15
    assert contributions[0]["llm_calls"] == 2


def test_history_metadata_keeps_usage_fields():
    metadata = SwarmCoordinator._history_metadata({
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        "llm_calls": 4,
        "answer": "不保存",
    })

    assert metadata["usage"]["total_tokens"] == 3
    assert metadata["llm_calls"] == 4


# ===== API 序列化 =====

def test_serialize_result_includes_usage():
    payload = _serialize_result({
        "answer": "答复",
        "usage": {"prompt_tokens": 9, "completion_tokens": 6, "total_tokens": 15},
        "llm_calls": 3,
        "patient_context": {"age": 38, "sex": "女"},
    }, {})

    assert payload["usage"]["total_tokens"] == 15
    assert payload["llm_calls"] == 3
    assert payload["patient_context"] == {"age": 38, "sex": "女"}


def test_serialize_result_defaults_usage():
    payload = _serialize_result({"answer": "答复"}, {})

    assert payload["usage"] == {}
    assert payload["llm_calls"] == 0
    assert payload["patient_context"] == {}


def test_serialize_result_sanitizes_non_finite_values():
    payload = _serialize_result({
        "answer": "答复",
        "patient_context": {"age": float("inf")},
    }, {})

    assert payload["patient_context"]["age"] is None
