"""聊天请求的长度与体积限制。"""
import json

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_question_over_limit_is_rejected(client, path):
    response = client.post(path, json={"question": "x" * 2001})
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_oversized_context_is_rejected(client, path):
    response = client.post(path, json={
        "question": "你好",
        "context": {"history": "x" * 9000},
    })
    assert response.status_code == 422


def test_too_many_context_keys_are_rejected(client):
    response = client.post("/api/chat", json={
        "question": "你好",
        "context": {f"k{index}": "v" for index in range(51)},
    })
    assert response.status_code == 422


@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/stream"])
def test_chat_body_size_limit(client, path):
    payload = json.dumps({"question": "x" * 70000})
    response = client.post(
        path,
        content=payload.encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 413
