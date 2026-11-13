"""第十九轮审查：删除墓碑顺序、WAL 残留与 CSV 表头错误回归测试。"""
import asyncio
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore

PROJECT_ROOT = Path(__file__).resolve().parents[1]


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


def test_memory_cleanup_queue_roundtrip(tmp_path):
    store = SessionStore(str(tmp_path / "cleanup.db"))
    try:
        store.queue_memory_cleanup([("妈妈", "s1"), ("", "s2")])
        rows = store.list_memory_cleanup()
        assert {(row["profile"], row["session_id"]) for row in rows} == {
            ("妈妈", "s1"), ("", "s2")
        }

        store.fail_memory_cleanup(rows[0]["id"], "network")
        updated = store.list_memory_cleanup()[0]
        assert updated["attempts"] == 1
        assert updated["last_error"] == "network"

        store.complete_memory_cleanup([("妈妈", "s1"), ("", "s2")])
        assert store.list_memory_cleanup() == []
    finally:
        store.close()


def test_memory_cleanup_queue_retries_until_success(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "cleanup-retry.db"))
    monkeypatch.setattr(
        memory, "get_session_store", lambda: store
    )
    results = [False, True]

    def fake_erase(entries):
        return results.pop(0)

    monkeypatch.setattr(api_server, "_erase_long_term_entries", fake_erase)
    try:
        store.queue_memory_cleanup([("妈妈", "s1")])
        api_server._process_memory_cleanup_queue()
        assert store.list_memory_cleanup()[0]["attempts"] == 1

        api_server._process_memory_cleanup_queue()
        assert store.list_memory_cleanup() == []
    finally:
        store.close()


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


def test_erase_treats_unconfigured_mem0_as_success(monkeypatch):
    """没配置 Mem0 时没有云端数据可清，直接视为成功。"""

    class DisabledMemory:
        enabled = False
        api_key_configured = False

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: DisabledMemory()
    )

    assert api_server._erase_long_term_entries([("妈妈", "s1")]) is True


def test_erase_retries_when_mem0_init_failed(monkeypatch):
    """已配置但初始化瞬时失败必须保留队列重试，不能当作删除成功。"""

    class FailedInitMemory:
        enabled = False
        api_key_configured = True
        init_error = "ConnectError: network down"

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: FailedInitMemory()
    )

    assert api_server._erase_long_term_entries([("妈妈", "s1")]) is False


def test_standalone_memory_rebuilds_after_transient_init_failure(monkeypatch):
    class Disabled:
        enabled = False
        api_key_configured = True

    class Healthy:
        enabled = True
        api_key_configured = True

    import memory.long_term as long_term_module

    monkeypatch.setattr(
        long_term_module, "LongTermMemory", lambda: Healthy()
    )
    monkeypatch.setattr(
        api_server, "_standalone_long_term_memory", Disabled()
    )
    monkeypatch.setattr(api_server, "_standalone_long_term_retry_at", 0.0)
    monkeypatch.setattr(api_server.state, "coordinator", None)

    rebuilt = api_server._get_long_term_memory()

    assert rebuilt.enabled is True


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


def test_delete_profile_records_deletion_tombstone(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_profile("妈妈", {"age": 50})
        past = "2000-01-01T00:00:00"

        assert store.profile_deleted_since("妈妈", past) is False
        assert store.delete_profile("妈妈") is True
        assert store.profile_deleted_since("妈妈", past) is True
        # 删除之后的时间点不受墓碑影响
        assert store.profile_deleted_since(
            "妈妈", "2999-01-01T00:00:00"
        ) is False
    finally:
        store.close()


def test_cleanup_queue_prunes_old_memories_when_profile_recreated(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_profile("妈妈", {"age": 50})
        store.delete_profile("妈妈")
        store.queue_memory_cleanup([("妈妈", "")])
        # 同名档案重建：不能整段清空旧命名空间，只能按删除时间窗清理
        store.save_profile("妈妈", {"age": 50})
        monkeypatch.setattr(memory, "get_session_store", lambda: store)

        prune_calls = []
        monkeypatch.setattr(
            api_server,
            "_erase_long_term_before",
            lambda profile, session_id, cutoff: (
                prune_calls.append((profile, session_id, cutoff)) or True
            ),
        )
        erase_calls = []
        monkeypatch.setattr(
            api_server,
            "_erase_long_term_entries",
            lambda entries: erase_calls.append(entries) or True,
        )

        api_server._process_memory_cleanup_queue()

        assert erase_calls == []
        assert len(prune_calls) == 1
        assert prune_calls[0][0] == "妈妈"
        assert prune_calls[0][1] == ""
        assert prune_calls[0][2], "必须带删除时间窗"
        assert store.list_memory_cleanup() == []
    finally:
        store.close()


def test_cleanup_queue_erases_when_target_not_recreated(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_profile("妈妈", {"age": 50})
        store.delete_profile("妈妈")
        store.queue_memory_cleanup([("妈妈", "")])
        monkeypatch.setattr(memory, "get_session_store", lambda: store)

        calls = []
        monkeypatch.setattr(
            api_server,
            "_erase_long_term_entries",
            lambda entries: calls.append(entries) or True,
        )

        api_server._process_memory_cleanup_queue()

        assert calls == [[("妈妈", "")]]
        assert store.list_memory_cleanup() == []
    finally:
        store.close()


def test_queue_memory_cleanup_refreshes_deletion_time(tmp_path):
    """重复删除同一档案时要刷新时间窗，否则中间一代记忆漏清理。"""
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.queue_memory_cleanup([("妈妈", "")])
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE memory_cleanup_queue"
                " SET created_at = '2000-01-01T00:00:00', attempts = 3"
            )

        store.queue_memory_cleanup([("妈妈", "")])

        row = store.list_memory_cleanup()[0]
        assert row["created_at"] > "2000-01-01T00:00:00"
        assert row["attempts"] == 0
        assert len(store.list_memory_cleanup()) == 1
    finally:
        store.close()


def test_link_profile_skips_when_deleted_during_request(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "头痛", "答案")
        store.save_profile("妈妈", {"age": 50})
        store.delete_profile("妈妈")
        monkeypatch.setattr(memory, "get_session_store", lambda: store)
        api_server._PROFILE_CLAIMS["s1"] = "妈妈"

        asyncio.run(api_server._link_patient_profile(
            "s1", "妈妈", since="2000-01-01T00:00:00"
        ))

        assert (store.get_session("s1")["profile"] or "") == ""
        # 认领必须释放，否则下一轮会被残留认领解析回已删除档案
        assert "s1" not in api_server._PROFILE_CLAIMS
    finally:
        api_server._PROFILE_CLAIMS.pop("s1", None)
        store.close()


def test_delete_profile_clears_inflight_profile_claims(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": 50})
    api_server._PROFILE_CLAIMS["claim-session"] = "妈妈"
    try:
        response = client.delete("/api/profiles/妈妈")

        assert response.status_code == 200
        assert "claim-session" not in api_server._PROFILE_CLAIMS
    finally:
        api_server._PROFILE_CLAIMS.pop("claim-session", None)


def test_coordinator_checks_profile_deletion_tombstone():
    source = (
        PROJECT_ROOT / "swarm" / "swarm_coordinator.py"
    ).read_text(encoding="utf-8")

    assert "profile_deleted_since" in source
    assert "profile=_bound_profile()" in source
    assert "or _profile_gone()" in source
