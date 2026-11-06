"""第五轮 LLM 与限流审计回归：端口、额度、截断用量、代理码位与取消。"""
import asyncio
from types import SimpleNamespace

import pytest
from loguru import logger

import api_server
import core.security as security
from core.llm_client import LLMClient, LLMError, classify_llm_error
from swarm.shared_context import SharedContext, SubTask
from swarm.swarm_coordinator import SwarmCoordinator


def test_sanitize_base_url_tolerates_invalid_port():
    url = security.sanitize_base_url("http://192.168.1.9:8000x/v1")

    assert isinstance(url, str)
    assert "8000x" in url


def test_insufficient_quota_is_not_retryable():
    class QuotaError(Exception):
        status_code = 429

    error = classify_llm_error(
        QuotaError(
            "Error code: 429 - insufficient_quota: You exceeded your current quota"
        )
    )

    assert error.code == "llm_balance"
    assert error.retryable is False


def _llm_client_with_response(response):
    client = object.__new__(LLMClient)
    client.model_name = "test-model"
    client.model_type = "openai_compatible"
    client.temperature = 0.7
    client.max_tokens = 100

    async def create(**kwargs):
        return response

    client.client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)
        )
    )
    return client


def test_truncated_tool_call_error_carries_usage():
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=None,
                    tool_calls=[
                        SimpleNamespace(
                            id="t1",
                            function=SimpleNamespace(
                                name="search", arguments='{"query":'
                            ),
                        )
                    ],
                ),
                finish_reason="length",
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=10, completion_tokens=5, total_tokens=15
        ),
    )
    client = _llm_client_with_response(response)

    with pytest.raises(LLMError) as excinfo:
        asyncio.run(client.chat_with_tools([]))

    error = excinfo.value
    assert error.code == "llm_truncated"
    assert error.usage["total_tokens"] == 15
    assert error.llm_calls == 1


def test_chat_with_usage_strips_lone_surrogates():
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="a\ud800b"))],
        usage=None,
    )
    client = _llm_client_with_response(response)

    content, _usage = asyncio.run(client.chat_with_usage([]))

    assert "\ud800" not in content
    assert "a" in content and "b" in content


def test_serialized_result_strips_lone_surrogates():
    payload = api_server._serialize_result(
        {"answer": "结论\ud800如下", "suggestions": ["建议\udfff"]}, {}
    )

    assert "\ud800" not in payload["answer"]
    assert all("\udfff" not in item for item in payload["suggestions"])


def test_invalid_log_level_keeps_a_handler(monkeypatch):
    monkeypatch.setenv("MEDIX_LOG_LEVEL", "verbose")
    monkeypatch.setattr(security, "_server_logging_configured", False)

    security.configure_server_logging()

    assert logger._core.handlers


class CancelledWorker:
    agent_id = "research_agent"

    async def process_subtask(self, subtask, shared_context=None):
        error = asyncio.CancelledError()
        error.usage = {"total_tokens": 21}
        error.llm_calls = 3
        raise error


def test_cancelled_worker_usage_is_recorded():
    context = SharedContext(session_id="cancel")
    context.add_subtask(SubTask(
        id="s1",
        type="research_agent_task",
        description="查指南",
        assigned_agent="research_agent",
    ))
    context.start_subtask("s1")
    coordinator = object.__new__(SwarmCoordinator)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(coordinator._execute_single_subtask(
            CancelledWorker(), context.get_subtask("s1"), context
        ))

    assert context.data["failed_usage"] == [
        {"usage": {"total_tokens": 21}, "llm_calls": 3}
    ]
