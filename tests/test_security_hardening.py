"""安全加固回归测试：子路径鉴权、令牌日志、脱敏与请求体限制。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import api_server
import core.diagnostics as diagnostics
from core.ratelimit import SlidingWindowLimiter
from core.security import mask_secret, sanitize_base_url


def test_scope_route_path_strips_root_path():
    assert api_server._scope_route_path({
        "path": "/medix/api/sessions",
        "root_path": "/medix",
    }) == "/api/sessions"
    assert api_server._scope_route_path({
        "path": "/api/chat",
        "root_path": "",
    }) == "/api/chat"


def test_root_path_requests_still_require_token(monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")
    client = TestClient(api_server.app, root_path="/medix")

    # TestClient 会把 root_path 放进 scope；鉴权应按去掉前缀后的路径匹配
    response = client.get(
        "/api/sessions",
        headers={"X-Medix-Token": "wrong"},
    )

    assert response.status_code == 401


def test_oversized_json_body_is_rejected():
    client = TestClient(api_server.app)
    big = b"x" * (3 * 1024 * 1024)

    response = client.post(
        "/api/chat",
        content=big,
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 413


def test_rate_limiter_evicts_keys_at_capacity():
    limiter = SlidingWindowLimiter(10)
    limiter.MAX_KEYS = 3

    for index in range(10):
        limiter.check(f"ip:10.0.0.{index}", now=100.0)

    assert len(limiter._hits) <= 3


def test_short_secrets_are_fully_masked():
    assert mask_secret("secret-tok") == "***"
    assert mask_secret("short") == "***"
    long_key = "sk-" + "a" * 30
    assert mask_secret(long_key) == long_key[:4] + "***" + long_key[-4:]


def test_sanitize_base_url_strips_credentials_and_query():
    assert sanitize_base_url(
        "https://user:pass@gateway.example.com/v1?api_key=sk-1234567890"
    ) == "https://gateway.example.com/v1"
    assert sanitize_base_url(
        "https://gateway.example.com/v1#token"
    ) == "https://gateway.example.com/v1"


def test_diagnostics_sanitizes_base_url_and_errors(monkeypatch):
    from core import llm_client

    monkeypatch.setitem(
        llm_client.LLM_CONFIG,
        "base_url",
        "https://user:pass@gateway.example.com/v1?api_key=sk-abcdef123456",
    )

    config = diagnostics.read_llm_config()

    assert config["base_url"] == "https://gateway.example.com/v1"
    assert "api_key" not in config


def test_diagnose_api_returns_sanitized_base_url(monkeypatch):
    from core import llm_client

    monkeypatch.setitem(
        llm_client.LLM_CONFIG,
        "base_url",
        "https://user:pass@gateway.example.com/v1?token=secret",
    )
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    client = TestClient(api_server.app)

    response = client.get("/api/status")

    assert response.status_code == 200
    assert response.json()["base_url"] == "https://gateway.example.com/v1"
