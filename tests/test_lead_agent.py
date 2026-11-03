"""LeadAgent 任务分解解析的回归测试。"""
import asyncio

from swarm.lead_agent import (
    LeadAgent,
    extract_json_payload,
    normalize_agent_id,
    normalize_subtasks,
)
from swarm.shared_context import SharedContext


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
