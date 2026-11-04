"""操作审计筛选 API 的离线测试。"""
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


def test_audit_api_filters_and_lists_actions(client_and_store):
    client, store = client_and_store
    store.log_action("session.delete", "s1", "用户删除")
    store.log_action("session.import", "backup.zip", "imported=2")

    all_log = client.get("/api/audit")
    filtered = client.get("/api/audit", params={"action": "session.delete"})
    keyword = client.get("/api/audit", params={"q": "backup"})
    missing = client.get("/api/audit", params={"action": "missing"})
    bad_date = client.get("/api/audit", params={"start": "2026/01/01"})

    assert all_log.status_code == 200
    assert all_log.json()["available_actions"] == [
        "session.delete", "session.import",
    ]
    assert [item["target"] for item in filtered.json()["actions"]] == ["s1"]
    assert filtered.json()["available_actions"] == [
        "session.delete", "session.import",
    ]
    assert [item["target"] for item in keyword.json()["actions"]] == ["backup.zip"]
    assert missing.json()["actions"] == []
    assert bad_date.status_code == 400


def test_audit_csv_export_respects_filters(client_and_store):
    client, store = client_and_store
    store.log_action("session.delete", "s1", "用户删除")
    store.log_action("session.import", "backup.zip", "imported=2")

    response = client.get(
        "/api/audit/export.csv", params={"action": "session.import"}
    )

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    assert "session.import" in text
    assert "session.delete" not in text
    assert text.startswith("时间,动作,对象,说明")


def test_audit_date_range_filter(client_and_store, monkeypatch):
    client, store = client_and_store
    times = iter(["2026-01-01T09:00:00", "2026-03-20T11:30:00"])
    monkeypatch.setattr(SessionStore, "_now", staticmethod(lambda: next(times)))
    store.log_action("session.delete", "s1", "用户删除")
    store.log_action("session.delete", "s2", "用户删除")

    response = client.get(
        "/api/audit", params={"start": "2026-02-01", "end": "2026-02-28"}
    )
    assert response.status_code == 200
    assert response.json()["actions"] == []

    march = client.get("/api/audit", params={"start": "2026-03-01"})
    assert [item["target"] for item in march.json()["actions"]] == ["s2"]
