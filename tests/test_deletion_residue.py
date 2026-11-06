"""删除残留回归：WAL 明文、迟到消息复活与恢复后墓碑。"""
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore
from memory.short_term import ShortTermMemory


def _memory_short_term():
    instance = object.__new__(ShortTermMemory)
    instance.storage_type = "memory"
    instance.redis_client = None
    instance.sessions = {}
    instance._local_fallback_at = {}
    instance._cleared_sessions = set()
    instance._sealed_sessions = set()
    instance._state_lock = threading.RLock()
    instance.entropy_manager = None
    instance._initialized = True
    return instance


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_deleted_message_is_removed_from_db_and_wal(tmp_path):
    db_path = tmp_path / "secure.db"
    marker = "SECRET-WAL-MARKER-9f2c"
    store = SessionStore(str(db_path))
    store.save_exchange("s1", marker, marker + "-answer")
    assert store.delete_session("s1") is True
    store.close()

    payload = db_path.read_bytes()
    wal_path = Path(str(db_path) + "-wal")
    if wal_path.exists():
        payload += wal_path.read_bytes()
    assert marker.encode("utf-8") not in payload


def test_late_short_term_message_is_dropped_after_delete():
    memory_obj = _memory_short_term()
    memory_obj.clear_session("s1")
    memory_obj.seal_session("s1")

    memory_obj.add_message("s1", "user", "迟到的敏感提问")

    assert memory_obj.get_session("s1") is None


def test_messages_api_does_not_fall_back_to_short_term_after_delete(
    client_and_store,
):
    client, store = client_and_store
    session_id = "deleted-session"
    store.save_exchange(session_id, "旧问题", "旧回答")

    short_term = memory.ShortTermMemory()
    short_term.create_session(session_id)
    short_term.add_message(session_id, "user", "迟到的敏感提问")

    assert store.delete_session(session_id) is True
    response = client.get(f"/api/sessions/{session_id}/messages")

    assert response.status_code == 200
    assert response.json()["messages"] == []


def test_import_after_delete_clears_short_term_tombstone(client_and_store):
    client, store = client_and_store
    short_term = memory.ShortTermMemory()
    short_term.clear_session("restored")

    response = client.post(
        "/api/sessions/import",
        json={
            "sessions": [{
                "id": "restored",
                "title": "恢复的会诊",
                "messages": [
                    {
                        "role": "user",
                        "content": "旧问题",
                        "created_at": "2026-11-01T10:00:00",
                    },
                    {
                        "role": "assistant",
                        "content": "旧回答",
                        "created_at": "2026-11-01T10:01:00",
                    },
                ],
            }],
        },
    )

    assert response.status_code == 200
    short_term.add_message("restored", "user", "恢复后的新问题")
    history = short_term.get_session("restored")
    assert history is not None
    assert any(
        message["content"] == "恢复后的新问题"
        for message in history.messages
    )
