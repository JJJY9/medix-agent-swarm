"""维护清理必须同时清除进程内短期记忆。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory import ShortTermMemory
from memory.session_store import SessionStore

SECRET = "维护清理后不应再出现的旧对话"


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_purge_clears_short_term_memory(client_and_store):
    client, store = client_and_store
    session_id = "purge-short-term-secret"
    store.save_exchange(session_id, SECRET, "旧回答")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            ("2020-01-01T00:00:00", session_id),
        )

    short_term = ShortTermMemory()
    old_storage = short_term.storage_type
    old_client = short_term.redis_client
    short_term.storage_type = "memory"
    short_term.redis_client = None
    short_term.clear_session(session_id)
    short_term.add_message(session_id, "user", SECRET)
    short_term.add_message(session_id, "assistant", "旧回答")
    try:
        response = client.post(
            "/api/maintenance/purge", json={"days": 1}
        )
        assert response.status_code == 200
        assert response.json()["sessions"] == 1

        assert store.get_session(session_id) is None
        assert short_term.get_session(session_id) is None

        messages = client.get(
            f"/api/sessions/{session_id}/messages", params={"limit": 10}
        )
        assert SECRET not in messages.text
    finally:
        short_term.clear_session(session_id)
        short_term.storage_type = old_storage
        short_term.redis_client = old_client
