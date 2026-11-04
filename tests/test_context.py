"""结构化问诊上下文的离线测试。"""
import asyncio
from types import SimpleNamespace

from agents.base_agent import BaseAgent
from core.context import format_context, public_context


class DummyAgent(BaseAgent):
    """不初始化 LLM / Skill，仅测试输入格式化。"""

    def __init__(self):
        self.agent_id = "dummy"
        self.shared_context = None

    def get_system_prompt(self):
        return ""

    def register_tools(self):
        pass


def test_format_context_uses_labels_and_skips_internal_memory():
    text = format_context({
        "age": 38,
        "sex": "女",
        "duration": "3 天",
        "history": "高血压",
        "medications": "",
        "allergies": "青霉素",
        "recent_history": [{"role": "user", "content": "旧消息"}],
        "custom_note": "夜间加重",
    })

    assert "年龄：38" in text
    assert "性别：女" in text
    assert "症状持续时间：3 天" in text
    assert "既往病史：高血压" in text
    assert "过敏史：青霉素" in text
    assert "recent_history" not in text
    assert "旧消息" not in text
    assert "正在使用的药物" not in text
    assert "custom_note：夜间加重" in text


def test_format_context_handles_invalid_and_long_input():
    assert format_context(None) == ""
    assert format_context("not a dict") == ""
    assert format_context({"age": None, "sex": ""}) == ""

    text = format_context({"history": "长" * 5000}, max_length=100)
    assert len(text) == 100


def test_public_context_removes_internal_fields():
    context = {
        "age": 38,
        "recent_history": [{"role": "user", "content": "x"}],
        "historical_cases": [{"summary": "y"}],
        "empty": "",
    }

    assert public_context(context) == {"age": 38}
    assert public_context(None) == {}


def test_base_agent_formats_session_and_context():
    agent = DummyAgent()

    text = agent.format_user_input({
        "question": "最近头痛",
        "session_id": "s1",
        "context": {"age": 38, "sex": "女"},
    })

    assert "[系统信息] 当前会话ID: s1" in text
    assert "背景信息：" in text
    assert "年龄：38" in text
    assert "性别：女" in text
    assert text.rstrip().endswith("用户问题：最近头痛")


def test_base_agent_formats_plain_question_without_context():
    agent = DummyAgent()

    assert agent.format_user_input({"question": "头痛"}) == "用户问题：头痛"


def test_process_subtask_passes_patient_context_to_workers():
    agent = DummyAgent()
    agent.shared_context = SimpleNamespace(
        data={"patient_context": {"age": 38, "sex": "女"}}
    )

    captured = {}

    async def fake_run_loop(input_data, on_token=None):
        captured.update(input_data)
        return {"answer": "ok"}

    agent.run_loop = fake_run_loop
    subtask = SimpleNamespace(id="s1", type="diagnostic_agent_task", description="分析头痛")

    result = asyncio.run(agent.process_subtask(subtask))

    assert result == {"answer": "ok"}
    assert captured["question"] == "分析头痛"
    assert captured["context"] == {"age": 38, "sex": "女"}


def test_format_context_labels_recent_metrics_and_reminders():
    text = format_context({
        "recent_metrics": "血压 128/82（11-04）",
        "pending_reminders": "复查（11-10）",
    })

    assert "近期健康指标：血压 128/82（11-04）" in text
    assert "待办随访提醒：复查（11-10）" in text


def test_format_context_labels_recent_symptoms():
    text = format_context({"recent_symptoms": "头晕 6/10（11-04）"})
    assert "近期症状：头晕 6/10（11-04）" in text


def test_format_context_labels_active_medications():
    text = format_context({"active_medications": "氨氯地平 5 mg 每日一次"})
    assert "用药清单：氨氯地平 5 mg 每日一次" in text
