"""系统自检「测试模型」接口的离线测试。"""
from fastapi.testclient import TestClient

import api_server
import core.llm_client as llm_client
from core.llm_client import LLMError


class FakeClient:
    async def chat_with_usage_retry(self, messages, **kwargs):
        return "OK", {"total_tokens": 3}


class FailingClient:
    async def chat_with_usage_retry(self, messages, **kwargs):
        raise LLMError("auth_error", "密钥无效", retryable=False)


def test_diagnose_llm_success(monkeypatch):
    monkeypatch.setattr(llm_client, "LLMClient", FakeClient)
    client = TestClient(api_server.app)

    response = client.post("/api/diagnose/llm")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["reply"] == "OK"
    assert data["usage"]["total_tokens"] == 3


def test_diagnose_llm_returns_readable_error(monkeypatch):
    monkeypatch.setattr(llm_client, "LLMClient", FailingClient)
    client = TestClient(api_server.app)

    response = client.post("/api/diagnose/llm")

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is False
    assert data["code"] == "auth_error"
    assert "密钥无效" in data["error"]
