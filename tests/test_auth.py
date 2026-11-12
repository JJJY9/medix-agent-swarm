"""可选访问令牌校验的离线测试。"""
import pytest
from fastapi.testclient import TestClient
from loguru import logger

import api_server
import memory
from core.ratelimit import SlidingWindowLimiter
from memory.session_store import SessionStore


@pytest.fixture
def client(monkeypatch, tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("s1", "测试问题", "测试答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    # 其他测试可能留下假 Coordinator；状态接口只读取 long_term_memory，
    # 这里置空避免测试间互相污染。
    original_coordinator = api_server.state.coordinator
    api_server.state.coordinator = None

    yield TestClient(api_server.app)

    api_server.state.coordinator = original_coordinator
    store.close()


def test_status_reports_auto_archive_days(client, monkeypatch):
    monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", "30")

    status = client.get("/api/status")

    assert status.status_code == 200
    assert status.json()["auto_archive_days"] == 30


def test_auth_disabled_by_default(client, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)

    status = client.get("/api/status")

    assert status.status_code == 200
    assert status.json()["auth_required"] is False
    assert client.get("/api/sessions").status_code == 200
    assert client.get("/api/auth/verify").status_code == 200


def test_status_and_static_are_public_but_api_requires_token(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")

    status = client.get("/api/status")

    assert status.status_code == 200
    assert status.json()["auth_required"] is True
    assert client.get("/").status_code == 200
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/sessions").status_code == 401
    assert client.get("/api/auth/verify").status_code == 401


def test_token_accepted_via_header_and_bearer(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")

    assert client.get(
        "/api/sessions", headers={"X-Medix-Token": "secret-token"}
    ).status_code == 200
    assert client.get(
        "/api/sessions", headers={"Authorization": "Bearer secret-token"}
    ).status_code == 200
    # 查询串会进入访问日志，不再接受 ?token=
    assert client.get(
        "/api/sessions", params={"token": "secret-token"}
    ).status_code == 401

    for kwargs in (
        {"headers": {"X-Medix-Token": "wrong"}},
        {"headers": {"Authorization": "Bearer wrong"}},
        {"params": {"token": "wrong"}},
    ):
        assert client.get("/api/sessions", **kwargs).status_code == 401


def test_blank_token_keeps_auth_disabled(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "   ")

    status = client.get("/api/status")

    assert status.status_code == 200
    assert status.json()["auth_required"] is False
    assert client.get("/api/sessions").status_code == 200


def test_token_is_not_echoed_in_status(client, monkeypatch):
    token = "very-secret-token-value"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)

    response = client.get("/api/status")

    assert token not in response.text


def test_non_ascii_token_is_compared_safely(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "中文令牌🔒")

    ok = client.get(
        "/api/sessions",
        headers={"X-Medix-Token": "中文令牌🔒".encode("utf-8")},
    )
    wrong = client.get(
        "/api/sessions",
        headers={"X-Medix-Token": "错误令牌".encode("utf-8")},
    )

    assert ok.status_code == 200
    assert wrong.status_code == 401


def test_remote_wrong_tokens_are_rate_limited(client, monkeypatch):
    token = "a-secure-random-token-123"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(2, window=300),
    )
    monkeypatch.setattr(
        api_server, "_is_loopback_client", lambda request: False
    )
    wrong = {"X-Medix-Token": "bad-guess"}

    first = client.get("/api/sessions", headers=wrong)
    second = client.get("/api/sessions", headers=wrong)
    limited = client.get("/api/sessions", headers=wrong)

    assert first.status_code == 401
    assert second.status_code == 401
    assert limited.status_code == 429
    assert limited.json()["code"] == "auth_rate_limited"
    assert limited.headers.get("Retry-After")

    # 正确令牌不进入失败计数，不会被错误尝试连累
    ok = client.get("/api/sessions", headers={"X-Medix-Token": token})
    assert ok.status_code == 200


def test_short_access_token_warns_once(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "short")
    api_server._short_access_token_warned = False
    messages = []
    sink = logger.add(
        lambda message: messages.append(str(message)), level="WARNING"
    )
    try:
        client.get("/api/sessions")
        client.get("/api/sessions")
    finally:
        logger.remove(sink)
        api_server._short_access_token_warned = False

    warnings = [
        message for message in messages if "长度不足 16" in message
    ]
    assert len(warnings) == 1
