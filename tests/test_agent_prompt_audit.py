"""Agent 提示词与运行时契约的回归测试。"""
import asyncio

from agents.base_agent import BaseAgent
from agents.consultation_agent import ConsultationAgent
from agents.diagnostic_agent import DiagnosticAgent
from agents.research_agent import ResearchAgent
from swarm.lead_agent import normalize_agent_id
from swarm.swarm_coordinator import SwarmCoordinator


class DummyAgent(BaseAgent):
    def get_system_prompt(self):
        return "系统提示"

    def register_tools(self):
        pass


def test_agent_loop_uses_configured_tool_budget():
    default_agent = DummyAgent("dummy", {}, llm_client=object())
    custom_agent = DummyAgent(
        "dummy2", {"max_tool_calls": 4}, llm_client=object()
    )

    assert default_agent.loop.max_tool_calls == 2
    assert custom_agent.loop.max_tool_calls == 4


def test_builtin_agents_allow_documented_skill_count():
    diagnostic = DiagnosticAgent(llm_client=object())
    research = ResearchAgent(llm_client=object())
    consultation = ConsultationAgent({})

    assert diagnostic.config["max_tool_calls"] == 3
    assert research.config["max_tool_calls"] == 3
    assert consultation.config["max_tool_calls"] == 3


def test_agent_aliases_cover_prompt_titles():
    assert normalize_agent_id("健康咨询专家") == "consultation_agent"
    assert normalize_agent_id("诊断推理专家") == "diagnostic_agent"
    assert normalize_agent_id("症状诊断") == "diagnostic_agent"
    assert normalize_agent_id("循证医学专家") == "research_agent"
    assert normalize_agent_id("医学研究") == "research_agent"


def test_single_agent_question_keeps_subtask_focus():
    routed = SwarmCoordinator._routed_question(
        "我最近胸痛", {"description": "评估胸痛风险并给出处理建议"}
    )

    assert routed.startswith("我最近胸痛")
    assert "评估胸痛风险并给出处理建议" in routed
    assert SwarmCoordinator._routed_question(
        "我最近胸痛", {"description": ""}
    ) == "我最近胸痛"


def test_consultation_extracts_risk_level():
    agent = object.__new__(ConsultationAgent)

    result = asyncio.run(agent.post_process_result(
        {},
        "【风险评估】\n风险等级：高\n【回答】\n建议尽快就医。",
    ))
    no_risk = asyncio.run(agent.post_process_result(
        {}, "【回答】\n普通建议。"
    ))

    assert result["risk_level"] == "high"
    assert "risk_level" not in no_risk


def test_research_counts_explicit_literature_field():
    agent = object.__new__(ResearchAgent)

    provided = asyncio.run(agent.post_process_result(
        {},
        "【文献检索结果】\n找到相关文献：3 篇\n【证据等级】\nB级",
    ))
    missing = asyncio.run(agent.post_process_result(
        {}, "【文献检索结果】\n未找到高质量证据。"
    ))

    assert provided["literature_count"] == 3
    assert provided["evidence_provided"] is True
    assert missing["literature_count"] == 0
    assert missing["evidence_provided"] is False
