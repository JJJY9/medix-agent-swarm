"""删除会话/档案时的长期记忆与会诊总结清理回归。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.backup import delete_backup
from memory.long_term import LongTermMemory
from memory.session_store import SessionStore
from memory.session_summary import SessionSummaryManager


class FakeMem0:
    def __init__(self, memories=None):
        self.memories = list(memories or [])
        self.deleted = []
        self.deleted_all = []
        self.get_all_calls = []

    def get_all(self, user_id=None, page=1, page_size=100):
        self.get_all_calls.append(user_id)
        items = [
            item for item in self.memories
            if item.get("user_id") == user_id
        ]
        start = (page - 1) * page_size
        return {"results": items[start:start + page_size]}

    def delete(self, memory_id=None, **kwargs):
        self.deleted.append(memory_id)
        self.memories = [
            item for item in self.memories if item.get("id") != memory_id
        ]
        return {"message": "deleted"}

    def delete_all(self, user_id=None, **kwargs):
        self.deleted_all.append(user_id)
        self.memories = [
            item for item in self.memories
            if item.get("user_id") != user_id
        ]
        return {"message": "deleted", "deleted": 1}


def _long_term(client):
    memory_obj = object.__new__(LongTermMemory)
    memory_obj.enabled = True
    memory_obj.mem0 = client
    memory_obj.entropy_manager = None
    return memory_obj


def test_delete_session_summary_only_removes_matching_session():
    from core.session_context import session_memory_scope

    scope = "medix_profile_abc"
    keep_scope = session_memory_scope("keep")
    drop_scope = session_memory_scope("drop")
    client = FakeMem0([
        {"id": "m1", "user_id": scope, "metadata": {"session_id": drop_scope}},
        {"id": "m2", "user_id": scope, "metadata": {"session_id": keep_scope}},
        {"id": "m3", "user_id": scope, "metadata": {}},
        {"id": "m4", "user_id": "other", "metadata": {"session_id": drop_scope}},
    ])
    memory_obj = _long_term(client)

    deleted = memory_obj.delete_session_summary("drop", user_id=scope)

    assert deleted == 1
    assert client.deleted == ["m1"]
    assert [item["id"] for item in client.memories] == ["m2", "m3", "m4"]


def test_delete_scope_removes_namespace():
    client = FakeMem0([
        {"id": "m1", "user_id": "scope-a", "metadata": {}},
        {"id": "m2", "user_id": "scope-b", "metadata": {}},
    ])
    memory_obj = _long_term(client)

    memory_obj.delete_scope("scope-a")

    assert client.deleted_all == ["scope-a"]
    assert [item["id"] for item in client.memories] == ["m2"]


def test_delete_scope_falls_back_to_per_item_delete():
    class BrokenDeleteAll(FakeMem0):
        def delete_all(self, user_id=None, **kwargs):
            raise RuntimeError("delete_all unsupported")

    client = BrokenDeleteAll([
        {"id": "m1", "user_id": "scope-a", "metadata": {}},
    ])
    memory_obj = _long_term(client)

    assert memory_obj.delete_scope("scope-a") == 1
    assert client.deleted == ["m1"]


def test_session_summary_delete_removes_file_and_empty_dir(tmp_path):
    manager = SessionSummaryManager(str(tmp_path / "summaries"))
    path = manager._get_summary_path("2026-11-06-abc")
    path.write_text("# 总结", encoding="utf-8")
    assert path.exists()

    assert manager.delete_summary("2026-11-06-abc") is True
    assert not path.exists()
    assert not path.parent.exists()
    # 重复删除不报错，返回 False
    assert manager.delete_summary("2026-11-06-abc") is False


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _record_erasures(monkeypatch):
    calls = {"entries": [], "summaries": []}

    def fake_entries(entries):
        calls["entries"].append(list(entries))
        return True

    def fake_summaries(session_ids):
        calls["summaries"].append(list(session_ids))

    monkeypatch.setattr(api_server, "_erase_long_term_entries", fake_entries)
    monkeypatch.setattr(api_server, "_erase_session_summaries", fake_summaries)
    return calls


def test_delete_session_clears_memory_and_summary(client_and_store, monkeypatch):
    client, store = client_and_store
    calls = _record_erasures(monkeypatch)
    store.save_exchange("s1", "头痛", "多休息")
    store.set_session_profile("s1", "妈妈")

    response = client.delete("/api/sessions/s1")

    assert response.status_code == 200
    assert calls["entries"] == [[("妈妈", "s1")]]
    assert calls["summaries"] == [["s1"]]
    assert store.get_session("s1") is None


def test_bulk_delete_clears_memory_and_summary(client_and_store, monkeypatch):
    client, store = client_and_store
    calls = _record_erasures(monkeypatch)
    store.save_exchange("s1", "头痛", "多休息")
    store.set_session_profile("s1", "妈妈")
    store.save_exchange("s2", "咳嗽", "多喝水")

    response = client.post(
        "/api/bulk/sessions/delete", json={"ids": ["s1", "s2", "missing"]}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 2
    assert data["missing"] == ["missing"]
    assert calls["entries"] == [[("妈妈", "s1"), ("", "s2"), ("", "missing")]]
    assert calls["summaries"] == [["s1", "s2", "missing"]]


def test_purge_clears_memory_and_summary(client_and_store, monkeypatch):
    client, store = client_and_store
    calls = _record_erasures(monkeypatch)
    store.save_exchange("old", "旧问题", "旧答复")
    store.set_session_profile("old", "父亲")
    store.save_exchange("new", "新问题", "新答复")
    store._conn.execute(
        "UPDATE sessions SET updated_at = '2020-01-01T00:00:00' WHERE id = 'old'"
    )
    store._conn.commit()

    response = client.post("/api/maintenance/purge", json={"days": 30})

    assert response.status_code == 200
    assert response.json()["sessions"] == 1
    assert calls["entries"] == [[("父亲", "old")]]
    assert calls["summaries"] == [["old"]]
    assert store.get_session("new") is not None


def test_profile_delete_and_rename_clear_old_scope(client_and_store, monkeypatch):
    client, store = client_and_store
    calls = _record_erasures(monkeypatch)
    assert store.save_profile("妈妈", {"age": 60}) is not None

    renamed = client.patch(
        "/api/profiles/妈妈", json={"new_name": "母亲"}
    )
    assert renamed.status_code == 200
    assert calls["entries"] == [[("妈妈", "")]]

    assert store.save_profile("妈妈", {"age": 60}) is not None
    deleted = client.delete("/api/profiles/妈妈")
    assert deleted.status_code == 200
    assert calls["entries"] == [[("妈妈", "")], [("妈妈", "")]]


def test_delete_backup_endpoint(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(tmp_path / "backups"))
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    target = backup_dir / "medix-backup-20260101-010101.json"
    target.write_text("{}", encoding="utf-8")
    client = TestClient(api_server.app)

    def del_backup(name):
        return client.request("DELETE", f"/api/backups/{name}", json={"confirm": True})

    assert client.request(
        "DELETE",
        "/api/backups/medix-backup-20260101-010101.json",
        json={"confirm": False},
    ).status_code == 400

    response = del_backup(target.name)
    assert response.status_code == 200
    assert not target.exists()

    missing = del_backup(target.name)
    assert missing.status_code == 404

    traversal = client.request(
        "DELETE", "/api/backups/..%2Fsecret.json", json={"confirm": True}
    )
    assert traversal.status_code in (400, 404)


def test_delete_backup_helper_rejects_traversal(tmp_path):
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    with pytest.raises(ValueError):
        delete_backup("../secret.json", backup_dir)
