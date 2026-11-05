"""API 响应缓存策略测试：健康与会诊数据不得被缓存。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "cache.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


def test_api_responses_are_not_cacheable(client):
    response = client.get("/api/sessions")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"


def test_static_assets_keep_their_cache_policy(client):
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert response.headers.get("cache-control") != "no-store"
