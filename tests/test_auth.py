"""可选访问令牌校验的离线测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
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


def test_token_accepted_via_header_bearer_and_query(client, monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")

    assert client.get(
        "/api/sessions", headers={"X-Medix-Token": "secret-token"}
    ).status_code == 200
    assert client.get(
        "/api/sessions", headers={"Authorization": "Bearer secret-token"}
    ).status_code == 200
    assert client.get(
        "/api/sessions", params={"token": "secret-token"}
    ).status_code == 200

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

    ok = client.get("/api/sessions", params={"token": "中文令牌🔒"})
    wrong = client.get("/api/sessions", params={"token": "错误令牌"})

    assert ok.status_code == 200
    assert wrong.status_code == 401
