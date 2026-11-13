"""deep_research 内部 LLM 调用的用量记账测试。"""
import asyncio
from types import SimpleNamespace

import pytest

from core.agent_loop import AgentLoop
from core.llm_client import LLMResponse, ToolCall


class UsageLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_usage(self, messages, **kwargs):
        self.calls += 1
        return "子查询一\n子查询二", {
            "prompt_tokens": 3,
            "completion_tokens": 2,
            "total_tokens": 5,
        }


def test_deep_research_workflow_accumulates_usage():
    pytest.importorskip("sentence_transformers")
    from research.deep_research_workflow import DeepResearchWorkflow
    from research.web_search import SearchResult

    class FakeWebSearch:
        async def search(self, query, max_results=10):
            return [SearchResult(
                title="高血压管理", url="https://example.com",
                snippet="生活方式干预与规范用药",
            )]

    usage = {}
    workflow = DeepResearchWorkflow(
        llm_client=UsageLLM(),
        use_web_search=True,
        use_knowledge_base=False,
    )
    workflow.web_search = FakeWebSearch()

    asyncio.run(
        workflow.run("高血压如何管理？", usage_out=usage)
    )

    # 查询规划 + 证据综合各一次 LLM 调用
    assert usage["total_tokens"] == 10


class ToolUsageLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                content="",
                tool_calls=[ToolCall(
                    id="tool-1",
                    name="deep_research",
                    arguments={"query": "高血压"},
                )],
                finish_reason="tool_calls",
                usage={
                    "prompt_tokens": 6, "completion_tokens": 4,
                    "total_tokens": 10,
                },
            )
        return LLMResponse(
            content="最终答复",
            tool_calls=[],
            finish_reason="stop",
            usage={
                "prompt_tokens": 3, "completion_tokens": 2,
                "total_tokens": 5,
            },
        )

    def create_tool_message(self, tool_call_id, tool_name, result):
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": "ok",
        }


class ToolUsageAgent:
    agent_id = "consultation_agent"
    config = {}

    def __init__(self):
        self.llm_client = ToolUsageLLM()

    @staticmethod
    def get_system_prompt():
        return "系统提示"

    @staticmethod
    def format_user_input(data):
        return data["question"]

    @staticmethod
    def get_tools_for_llm():
        return []

    @staticmethod
    async def execute_tool(tool_name, arguments):
        return {
            "answer": "研究结果",
            "usage": {
                "prompt_tokens": 4, "completion_tokens": 3,
                "total_tokens": 7,
            },
            "llm_calls": 2,
        }

    @staticmethod
    async def post_process_result(result, final_answer):
        return result


def test_agent_loop_merges_tool_usage_and_llm_calls():
    loop = AgentLoop()
    loop.auto_fixer = None

    result = asyncio.run(
        loop.run(ToolUsageAgent(), {"question": "测试问题"})
    )

    assert result["usage"]["total_tokens"] == 22
    # 2 次 Agent Loop LLM 调用 + deep_research 内部 2 次
    assert result["llm_calls"] == 4
