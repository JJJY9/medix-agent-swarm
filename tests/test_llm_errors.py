"""LLM 错误分类与超时配置的单元测试。"""
import asyncio

import pytest

from core import llm_client
from core.agent_loop import AgentLoop
from core.llm_client import LLMClient, LLMError, classify_llm_error


class FakeStatusError(Exception):
    def __init__(self, status, message="error"):
        super().__init__(message)
        self.status_code = status


class FakeTimeout(Exception):
    pass


class FakeConnection(Exception):
    pass


def test_balance_error_is_not_retryable():
    error = classify_llm_error(FakeStatusError(402, "Insufficient Balance"))

    assert error.code == "llm_balance"
    assert error.retryable is False
    assert "余额不足" in error.user_message


def test_auth_error_is_not_retryable():
    error = classify_llm_error(FakeStatusError(401, "bad key"))

    assert error.code == "llm_auth"
    assert error.retryable is False


def test_rate_limit_is_retryable():
    error = classify_llm_error(FakeStatusError(429, "rate limit"))

    assert error.code == "llm_rate_limit"
    assert error.retryable is True


def test_timeout_is_retryable():
    error = classify_llm_error(FakeTimeout("request timed out"))

    assert error.code == "llm_timeout"
    assert error.retryable is True


def test_connection_error_is_retryable():
    error = classify_llm_error(FakeConnection("connection refused"))

    assert error.code == "llm_network"
    assert error.retryable is True


def test_server_error_is_retryable():
    error = classify_llm_error(FakeStatusError(503, "unavailable"))

    assert error.code == "llm_server"
    assert error.retryable is True


def test_unknown_error_is_wrapped():
    error = classify_llm_error(ValueError("boom"))

    assert error.code == "llm_error"
    assert error.retryable is False
    assert "boom" in error.user_message


def test_existing_llm_error_is_passed_through():
    original = LLMError("llm_balance", "余额不足")
    assert classify_llm_error(original) is original


def test_default_timeout_and_env_override(monkeypatch):
    monkeypatch.setitem(llm_client.LLM_CONFIG, "api_key", "test-key")
    monkeypatch.delenv("MEDIX_TIMEOUT", raising=False)
    assert LLMClient().request_timeout == 60.0

    monkeypatch.setenv("MEDIX_TIMEOUT", "12.5")
    assert LLMClient().request_timeout == 12.5


class FatalLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        raise LLMError("llm_balance", "模型账户余额不足。", retryable=False)


class FakeAgent:
    agent_id = "test_agent"
    config = {"temperature": 0.7}

    def __init__(self, llm):
        self.llm_client = llm

    def get_system_prompt(self):
        return ""

    def format_user_input(self, input_data):
        return input_data["question"]

    def get_tools_for_llm(self):
        return []

    async def execute_tool(self, tool_name, arguments):
        return {"ok": True}


def test_fatal_llm_error_is_not_retried():
    llm = FatalLLM()
    agent = FakeAgent(llm)
    loop = AgentLoop(max_iterations=10)

    with pytest.raises(LLMError) as exc_info:
        asyncio.run(loop.run(agent, {"question": "测试"}))

    assert exc_info.value.code == "llm_balance"
    assert llm.calls == 1, "401/402 这类错误不应该被反复重试"


class RetryLLM:
    def __init__(self):
        self.calls = 0

    async def chat_with_tools(self, messages, tools=None, **kwargs):
        self.calls += 1
        raise LLMError("llm_rate_limit", "请求过于频繁", retryable=True)


def test_retry_exhaustion_returns_fallback_answer(monkeypatch):
    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", fast_sleep)
    llm = RetryLLM()
    agent = FakeAgent(llm)
    loop = AgentLoop(max_iterations=2)

    result = asyncio.run(loop.run(agent, {"question": "测试"}))

    assert isinstance(result, dict)
    assert result.get("answer")
    assert result.get("answer") != "{}"
