"""备份导出超过导入上限时的错误处理测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
import memory.session_store as store_module
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_sessions_export_over_limit_returns_400(
    client_and_store, monkeypatch
):
    client, _store = client_and_store
    for value in (60, 61):
        response = client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight", "value1": value,
        })
        assert response.status_code == 200
    monkeypatch.setitem(
        store_module.IMPORT_LIST_LIMITS, "health_metrics", 1
    )

    response = client.get("/api/sessions/export")

    assert response.status_code == 400
    assert "健康指标" in response.json()["detail"]
