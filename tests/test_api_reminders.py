"""随访提醒 API 的离线测试。"""
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


def test_reminders_api_roundtrip(client_and_store):
    client, store = client_and_store

    created = client.post(
        "/api/reminders",
        json={
            "title": "复查血常规",
            "due_at": "2026-11-10",
            "note": "空腹",
            "session_id": "s1",
        },
    )
    listed = client.get("/api/reminders")
    audit = client.get("/api/audit")

    assert created.status_code == 200
    reminder = created.json()["reminder"]
    assert reminder["title"] == "复查血常规"
    assert reminder["session_id"] == "s1"
    assert reminder["done"] is False
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()["reminders"]] == [reminder["id"]]
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "reminder.create" in actions

    completed = client.post(f"/api/reminders/{reminder['id']}/complete")
    after_complete = client.get("/api/reminders")
    include_done = client.get(
        "/api/reminders", params={"include_done": True}
    )
    assert completed.status_code == 200
    assert after_complete.json()["reminders"] == []
    assert len(include_done.json()["reminders"]) == 1

    deleted = client.delete(f"/api/reminders/{reminder['id']}")
    assert deleted.status_code == 200
    assert client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"] == []

    missing = client.post("/api/reminders/99999/complete")
    missing_delete = client.delete("/api/reminders/99999")
    assert missing.status_code == 404
    assert missing_delete.status_code == 404


def test_reminders_api_validates_input(client_and_store):
    client, _store = client_and_store

    bad_date = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "10/11/2026"},
    )
    blank_title = client.post(
        "/api/reminders",
        json={"title": "   ", "due_at": "2026-11-10"},
    )
    missing_title = client.post(
        "/api/reminders",
        json={"due_at": "2026-11-10"},
    )
    too_long = client.post(
        "/api/reminders",
        json={"title": "x" * 121, "due_at": "2026-11-10"},
    )

    assert bad_date.status_code == 400
    assert blank_title.status_code == 400
    assert missing_title.status_code == 422
    assert too_long.status_code == 422


def test_reminders_api_snooze(client_and_store):
    from datetime import date, timedelta

    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2020-01-01"},
    ).json()["reminder"]

    snoozed = client.post(
        f"/api/reminders/{created['id']}/snooze", params={"days": 7}
    )
    missing = client.post("/api/reminders/99999/snooze")
    audit = client.get("/api/audit")

    assert snoozed.status_code == 200
    assert snoozed.json()["reminder"]["due_at"] == (
        date.today() + timedelta(days=7)
    ).isoformat()
    assert missing.status_code == 404
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "reminder.snooze" in actions


def test_reminders_api_update(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-11-10"},
    ).json()["reminder"]

    updated = client.patch(
        f"/api/reminders/{created['id']}",
        json={"title": "复查肝功", "note": "空腹"},
    )
    invalid = client.patch(
        f"/api/reminders/{created['id']}", json={"due_at": "2026/11/10"}
    )
    empty = client.patch(f"/api/reminders/{created['id']}", json={})
    missing = client.patch("/api/reminders/99999", json={"title": "x"})
    audit = client.get("/api/audit")

    assert updated.status_code == 200
    reminder = updated.json()["reminder"]
    assert reminder["title"] == "复查肝功"
    assert reminder["note"] == "空腹"
    assert reminder["due_at"] == "2026-11-10"
    assert invalid.status_code == 400
    assert empty.status_code == 400
    assert missing.status_code == 404
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "reminder.update" in actions
