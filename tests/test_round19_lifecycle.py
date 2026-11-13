"""第十九轮审查：删除墓碑顺序、WAL 残留与 CSV 表头错误回归测试。"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
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


def _wal_bytes(store):
    wal = store.db_path.with_name(store.db_path.name + "-wal")
    return wal.read_bytes() if wal.exists() else b""


def test_rename_session_truncates_wal(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "秘密症状标题", "答复")
        store.rename_session("s1", "已脱敏标题")

        assert "秘密症状标题".encode("utf-8") not in _wal_bytes(store)
    finally:
        store.close()


def test_set_session_profile_truncates_wal(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "问题", "答复")
        store.set_session_profile("s1", "妈妈")

        assert "妈妈".encode("utf-8") not in _wal_bytes(store)
    finally:
        store.close()


def test_legacy_backup_checksum_is_accepted(tmp_path):
    from memory.session_store import _legacy_backup_checksum

    payload = {"sessions": [], "profiles": []}
    payload["checksum"] = _legacy_backup_checksum(payload)
    store = SessionStore(str(tmp_path / "legacy-checksum.db"))
    try:
        result = store.import_data(payload)
        assert isinstance(result, dict)
        assert store.list_profiles() == []
    finally:
        store.close()


def test_delete_profile_detaches_sessions(tmp_path):
    store = SessionStore(str(tmp_path / "detach.db"))
    try:
        store.save_profile("妈妈", {"age": "60"})
        store.save_exchange("s1", "问题", "答复")
        store.set_session_profile("s1", "妈妈")

        assert store.delete_profile("妈妈") is True
        assert store.get_session("s1")["profile"] == ""
    finally:
        store.close()


def test_stream_init_failure_releases_profile_claim(
    client_and_store, monkeypatch
):
    client, _store = client_and_store

    def boom():
        raise RuntimeError("coordinator unavailable")

    monkeypatch.setattr(api_server.state, "get_coordinator", boom)
    session_id = "claim-stream-release"

    response = client.post("/api/chat/stream", json={
        "question": "头痛",
        "session_id": session_id,
        "patient_profile": "母亲",
    })

    assert response.status_code == 200
    with api_server._PROFILE_CLAIMS_LOCK:
        assert session_id not in api_server._PROFILE_CLAIMS


def test_memory_cleanup_retries_transient_failures(monkeypatch):
    calls = []

    class FlakyMemory:
        enabled = True
        last_error = ""

        def delete_scope(self, scope):
            calls.append(scope)
            if len(calls) < 3:
                raise RuntimeError("network down")
            self.last_error = ""

        def delete_session_summary(self, session_id, user_id=""):
            raise AssertionError("无档案会话不应清理档案作用域")

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: FlakyMemory()
    )

    api_server._erase_long_term_entries([("", "session-x")])

    assert len(calls) == 3


def test_memory_cleanup_retries_on_reported_error(monkeypatch):
    calls = []

    class ErrorMemory:
        enabled = True
        last_error = "partial failure"

        def delete_scope(self, scope):
            calls.append(scope)
            self.last_error = "partial failure" if len(calls) == 1 else ""

        def delete_session_summary(self, session_id, user_id=""):
            pass

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: ErrorMemory()
    )

    api_server._erase_long_term_entries([("妈妈", "session-y")])

    assert len(calls) == 2


def test_delete_session_seals_before_clear(client_and_store, monkeypatch):
    client, store = client_and_store
    store.save_exchange("seal-order", "问题", "答复")
    calls = []

    class Recorder:
        def seal_session(self, session_id):
            calls.append(("seal", session_id))

        def clear_session(self, session_id):
            calls.append(("clear", session_id))

    monkeypatch.setattr(memory, "ShortTermMemory", Recorder, raising=False)

    response = client.delete("/api/sessions/seal-order")

    assert response.status_code == 200
    assert calls == [
        ("seal", "seal-order"),
        ("clear", "seal-order"),
    ]


def test_csv_header_parse_error_returns_400(client_and_store):
    client, _store = client_and_store
    # 超长表头会触发 csv 字段大小限制（csv.Error），不能变成 500
    oversized = b"a" * 200000 + b"\n"

    response = client.post(
        "/api/medications/import",
        files={"file": ("bad.csv", oversized, "text/csv")},
    )

    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]
