"""LeadAgent 任务分解解析的回归测试。"""
import asyncio

from swarm.lead_agent import (
    LeadAgent,
    extract_json_payload,
    normalize_agent_id,
    normalize_subtasks,
)
from swarm.shared_context import SharedContext, SubTask


class FakeChatLLM:
    def __init__(self, content):
        self.content = content

    async def chat(self, messages, **kwargs):
        return self.content


def _decompose(content):
    agent = LeadAgent(llm_client=FakeChatLLM(content))
    return asyncio.run(agent.assess_and_decompose("测试问题"))


def test_parses_json_wrapped_in_fences_and_prose():
    content = """好的，我来分解：
```json
{"subtasks": [{"description": "评估风险", "assigned_agent": "DiagnosticAgent"}]}
```
以上是分解结果。"""

    result = _decompose(content)

    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["assigned_agent"] == "diagnostic_agent"
    assert result["subtasks"][0]["description"] == "评估风险"


def test_falls_back_to_single_consultation_on_invalid_json():
    result = _decompose("这不是 JSON")

    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["assigned_agent"] == "consultation_agent"


def test_unknown_agent_is_reassigned_to_consultation():
    content = '{"subtasks": [{"description": "看神经科", "assigned_agent": "神经科"}]}'

    result = _decompose(content)

    assert result["subtasks"][0]["assigned_agent"] == "consultation_agent"


def test_subtask_missing_description_is_dropped():
    content = (
        '{"subtasks": ['
        '{"assigned_agent": "diagnostic_agent"},'
        '{"description": "给出建议", "assigned_agent": "consultation_agent"}'
        ']}'
    )

    result = _decompose(content)

    assert len(result["subtasks"]) == 1
    assert result["subtasks"][0]["description"] == "给出建议"


def test_top_level_list_is_accepted():
    content = '```json\n[{"description": "查指南", "assigned_agent": "research"}]\n```'

    result = _decompose(content)

    assert result["subtasks"][0]["assigned_agent"] == "research_agent"


def test_normalize_agent_id_aliases():
    assert normalize_agent_id("ConsultationAgent") == "consultation_agent"
    assert normalize_agent_id("research-agent") == "research_agent"
    assert normalize_agent_id("诊断") == "diagnostic_agent"
    assert normalize_agent_id("unknown") is None


def test_normalize_subtasks_caps_task_count():
    raw = [
        {"description": f"任务{i}", "assigned_agent": "consultation_agent"}
        for i in range(10)
    ]

    assert len(normalize_subtasks(raw)) == 5


def test_create_subtasks_tolerates_missing_fields():
    agent = LeadAgent(llm_client=FakeChatLLM(""))
    context = SharedContext(session_id="test")
    decomposition = {
        "subtasks": [
            {},  # 缺少所有字段
            {"description": "查证据"},  # 缺少 Agent
        ]
    }

    subtasks = agent.create_subtasks(decomposition, context)

    assert len(subtasks) == 2
    assert all(item.assigned_agent == "consultation_agent" for item in subtasks)
    assert all(item.description for item in subtasks)


def test_extract_json_payload_handles_nested_braces_in_prose():
    content = '说明 {不是 JSON}，结果：{"subtasks": [{"description": "x", "assigned_agent": "consultation_agent"}]}'

    payload = extract_json_payload(content)

    assert payload is not None
    assert payload["subtasks"][0]["description"] == "x"


class FakeStreamLLM:
    def __init__(self):
        self.stream_calls = 0
        self.chat_calls = 0

    async def chat_stream(self, messages, on_token=None, **kwargs):
        self.stream_calls += 1
        for piece in ["完整", "答复"]:
            if on_token:
                on_token(piece)
        return "完整答复"

    async def chat(self, messages, **kwargs):
        self.chat_calls += 1
        return "非流式答复"


def _context_with_contribution():
    context = SharedContext(session_id="stream-test")
    context.add_subtask(SubTask(
        id="s1",
        type="test_task",
        description="测试任务",
        assigned_agent="consultation_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask("s1", "consultation_agent", {"answer": "贡献"})
    return context


def test_synthesize_results_streams_tokens():
    llm = FakeStreamLLM()
    agent = LeadAgent(llm_client=llm)
    tokens = []

    text = asyncio.run(
        agent.synthesize_results(
            "测试问题",
            _context_with_contribution(),
            on_token=tokens.append,
        )
    )

    assert text == "完整答复"
    assert tokens == ["完整", "答复"]
    assert llm.stream_calls == 1
    assert llm.chat_calls == 0


def test_synthesize_results_without_callback_uses_blocking_chat():
    llm = FakeStreamLLM()
    agent = LeadAgent(llm_client=llm)

    text = asyncio.run(agent.synthesize_results("测试问题", _context_with_contribution()))

    assert text == "非流式答复"
    assert llm.stream_calls == 0
    assert llm.chat_calls == 1


class CapturingChatLLM:
    def __init__(self):
        self.messages = None

    async def chat(self, messages, **kwargs):
        self.messages = messages
        return "汇总答复"


def test_synthesis_prompt_uses_answer_text_only():
    context = SharedContext(session_id="prompt-test")
    context.add_subtask(SubTask(
        id="s1",
        type="test_task",
        description="测试任务",
        assigned_agent="consultation_agent",
    ))
    context.start_subtask("s1")
    context.complete_subtask("s1", "consultation_agent", {
        "answer": "贡献正文",
        "iterations": 3,
        "agent_id": "consultation_agent",
        "sources": [{"title": "内部来源"}],
        "usage": {"prompt_tokens": 10},
    })
    llm = CapturingChatLLM()

    asyncio.run(
        LeadAgent(llm_client=llm).synthesize_results("测试问题", context)
    )

    prompt = llm.messages[0]["content"]
    assert "贡献正文" in prompt
    assert "iterations" not in prompt
    assert "'answer'" not in prompt
    assert "内部来源" not in prompt
