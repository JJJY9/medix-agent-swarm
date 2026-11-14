"""第三轮 API 审计回归：删除竞态、记忆作用域、导入 id 与备份轮转。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.backup import _prune_backups
from core.session_context import session_memory_scope
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


def test_deleted_session_cannot_be_resurrected_by_late_save(tmp_path):
    store = SessionStore(str(tmp_path / "late.db"))
    try:
        store.save_exchange("s1", "问题", "回答")
        assert store.delete_session("s1") is True
        assert store.is_session_deleted("s1") is True

        # 进行中的会诊在删除后完成，save_exchange 必须拒绝写回
        assert store.save_exchange("s1", "迟到的提问", "迟到的回答") is None
        assert store.get_session("s1") is None
        assert store.get_messages("s1") == []
    finally:
        store.close()


def test_explicit_recreate_clears_tombstone(tmp_path):
    store = SessionStore(str(tmp_path / "recreate.db"))
    try:
        store.save_exchange("s1", "问题", "回答")
        store.delete_session("s1")
        store.ensure_session("s1", title="新会诊")

        assert store.is_session_deleted("s1") is False
        assert store.save_exchange("s1", "新问题", "新回答") is not None
    finally:
        store.close()


def test_erase_long_term_clears_session_and_profile_scopes(monkeypatch):
    calls = {"scopes": [], "sessions": []}

    class FakeMemory:
        enabled = True

        def delete_scope(self, scope):
            calls["scopes"].append(scope)

        def delete_session_summary(self, session_id, user_id=""):
            calls["sessions"].append((session_id, user_id))

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: FakeMemory()
    )

    # 会话可能先以无档案状态写入，再绑定档案：两个命名空间都要清理
    api_server._erase_long_term_entries([("妈妈", "s1"), ("", "s2")])

    assert session_memory_scope("s1") in calls["scopes"]
    assert session_memory_scope("s2") in calls["scopes"]
    assert ("s1", api_server._memory_scope("妈妈", "s1")) in calls["sessions"]
    # 旧版本单科会诊可能把无档案会话写进共享 medix_local：
    # 按 metadata.session_id 精确清理该会话残留
    assert ("s2", "medix_local") in calls["sessions"]
    # 旧格式（可读命名空间 + 原始 session_id）同样必须清理，
    # 否则历史云端摘要永远无法删除
    for legacy_scope in ("medix_user", "medix_local", "medix_profile_妈妈"):
        assert ("s1", legacy_scope) in calls["sessions"]
    for legacy_scope in ("medix_user", "medix_local", "medix_session_s2"):
        assert ("s2", legacy_scope) in calls["sessions"]


def test_huge_feedback_message_id_is_rejected(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/s1/feedback",
        json={"message_id": 10**30, "rating": 1},
    )

    assert response.status_code == 422


def test_huge_reminder_medication_id_is_rejected(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/reminders",
        json={
            "title": "复查",
            "due_at": "2026-12-01",
            "medication_id": 10**30,
        },
    )

    assert response.status_code == 422


def test_reminder_csv_import_tolerates_huge_ids(client_and_store):
    client, _store = client_and_store
    content = (
        "到期日,内容,关联药物ID,重复来源ID,ID\n"
        f"2026-12-01,复查,{10**30},{10**30},{10**30}\n"
    )

    response = client.post(
        "/api/reminders/import",
        files={"file": ("reminders.csv", content.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200, response.text


def test_json_import_tolerates_huge_reminder_ids(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "reminders": [
                {
                    "id": 10**30,
                    "title": "复查",
                    "due_at": "2026-12-01",
                    "created_at": "2026-11-01T00:00:00",
                }
            ]
        },
    )

    assert response.status_code == 200, response.text


def test_profile_rename_with_extra_spaces_does_not_erase_memories(
    client_and_store, monkeypatch
):
    client, store = client_and_store
    erased = []
    monkeypatch.setattr(
        api_server,
        "_erase_long_term_entries",
        lambda entries: erased.append(list(entries)) or True,
    )
    assert store.save_profile("a b", {"age": 50}) is not None

    response = client.patch(
        "/api/profiles/a%20%20b", json={"new_name": "a b"}
    )

    assert response.status_code == 200
    assert erased == []


def test_profile_rename_of_missing_profile_returns_404(client_and_store):
    client, _store = client_and_store

    response = client.patch("/api/profiles/ghost", json={"new_name": "ghost"})

    assert response.status_code == 404


def test_prune_backups_ignores_unmanaged_files(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    newest = backup_dir / "medix-backup-20260102-000000.json"
    older = backup_dir / "medix-backup-20260101-000000.json"
    notes = backup_dir / "medix-backup-notes.json"
    for path in (newest, older, notes):
        path.write_text("{}", encoding="utf-8")

    _prune_backups(backup_dir, 1)

    assert newest.exists()
    assert not older.exists()
    assert notes.exists()


def test_session_outputs_removed_if_deleted_during_save():
    from pathlib import Path

    source = (
        Path(__file__).resolve().parent.parent
        / "swarm" / "swarm_coordinator.py"
    ).read_text(encoding="utf-8")

    summary_save = source.index("save_summary, summary")
    summary_cleanup = source.index("delete_summary, session_id")
    memory_save = source.index("add_session_summary,")
    memory_cleanup = source.index(
        "delete_session_summary,", memory_save
    )

    assert summary_save < summary_cleanup
    assert memory_save < memory_cleanup

    # 非 Swarm 与 Swarm 两条保存路径都必须在写入后复核删除墓碑
    assert source.count("add_session_summary,") == 2
    assert source.count("delete_session_summary,") == 2
    second_save = source.index("add_session_summary,", memory_save + 1)
    second_cleanup = source.index(
        "delete_session_summary,", second_save
    )
    assert second_save < second_cleanup


def test_revive_session_allows_new_exchange(tmp_path):
    store = SessionStore(str(tmp_path / "revive.db"))
    try:
        store.save_exchange("s1", "旧问题", "旧回答")
        store.delete_session("s1")
        assert store.is_session_deleted("s1") is True
        assert store.save_exchange("s1", "迟到提问", "迟到回答") is None

        store.revive_session("s1")
        assert store.is_session_deleted("s1") is False
        assert store.save_exchange("s1", "新问题", "新回答") is not None

        messages = store.get_messages("s1")
        assert [item["content"] for item in messages] == [
            "新问题", "新回答",
        ]
    finally:
        store.close()
