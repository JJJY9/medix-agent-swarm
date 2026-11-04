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


def test_reminders_api_reopen(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-11-10"},
    ).json()["reminder"]
    client.post(f"/api/reminders/{created['id']}/complete")

    reopened = client.post(f"/api/reminders/{created['id']}/reopen")
    listed = client.get("/api/reminders")
    missing = client.post("/api/reminders/99999/reopen")
    audit = client.get("/api/audit")

    assert reopened.status_code == 200
    assert [item["id"] for item in listed.json()["reminders"]] == [
        created["id"]
    ]
    assert missing.status_code == 404
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "reminder.reopen" in actions


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


def test_reminders_api_profile_filter(client_and_store):
    client, _store = client_and_store
    client.post(
        "/api/reminders",
        json={"title": "妈妈复查", "due_at": "2026-11-10", "profile": "母亲"},
    )
    client.post(
        "/api/reminders",
        json={"title": "孩子复诊", "due_at": "2026-11-11", "profile": "孩子"},
    )

    filtered = client.get("/api/reminders", params={"profile": "母亲"})
    everything = client.get("/api/reminders")

    assert [
        item["title"] for item in filtered.json()["reminders"]
    ] == ["妈妈复查"]
    assert filtered.json()["reminders"][0]["profile"] == "母亲"
    assert len(everything.json()["reminders"]) == 2


def test_reminders_csv_export(client_and_store):
    client, _store = client_and_store
    client.post(
        "/api/reminders",
        json={"title": "妈妈复查", "due_at": "2026-11-10", "profile": "母亲"},
    )
    client.post(
        "/api/reminders",
        json={"title": "孩子复诊", "due_at": "2026-11-11", "profile": "孩子"},
    )

    response = client.get(
        "/api/reminders/export.csv", params={"profile": "母亲"}
    )
    text = response.content.decode("utf-8-sig")
    lines = text.strip().splitlines()

    assert response.status_code == 200
    assert lines[0] == "到期日,内容,档案,状态,备注"
    assert any("妈妈复查" in line for line in lines)
    assert all("孩子复诊" not in line for line in lines)


def test_reminders_ics_export(client_and_store):
    client, _store = client_and_store
    client.post(
        "/api/reminders",
        json={
            "title": "复查;血常规",
            "due_at": "2026-11-10",
            "note": "空腹,带报告",
            "profile": "母亲",
        },
    )

    response = client.get(
        "/api/reminders/export.ics", params={"profile": "母亲"}
    )
    text = response.content.decode("utf-8")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/calendar")
    assert "BEGIN:VCALENDAR" in text
    assert "END:VCALENDAR" in text
    assert "DTSTART;VALUE=DATE:20261110" in text
    assert "DTEND;VALUE=DATE:20261111" in text
    assert "SUMMARY:复查\\;血常规" in text
    assert "档案：母亲" in text


def test_import_backup_preserves_reminders(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "sessions": [],
            "profiles": [],
            "health_metrics": [],
            "reminders": [
                {"title": "复查", "due_at": "2026-11-10", "profile": "母亲"}
            ],
        },
    )
    listed = client.get("/api/reminders", params={"profile": "母亲"})

    assert response.status_code == 200
    assert response.json()["reminders"] == 1
    assert listed.json()["reminders"][0]["title"] == "复查"


def test_reminders_api_rejects_invalid_dates(client_and_store):
    client, _store = client_and_store

    invalid = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-02-30"},
    )
    overflow = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "9999-12-31"},
    )

    assert invalid.status_code == 400
    assert overflow.status_code == 400


def test_reminders_csv_escapes_formulas(client_and_store):
    client, _store = client_and_store
    client.post(
        "/api/reminders",
        json={
            "title": '=HYPERLINK("http://example.com","open")',
            "due_at": "2026-11-10",
        },
    )

    text = client.get("/api/reminders/export.csv").content.decode(
        "utf-8-sig"
    )

    assert "'=HYPERLINK" in text


def test_reminders_only_import_is_accepted(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "reminders": [
                {"title": "复查", "due_at": "2026-11-10"}
            ]
        },
    )
    listed = client.get("/api/reminders")

    assert response.status_code == 200
    assert response.json()["reminders"] == 1
    assert listed.json()["reminders"][0]["title"] == "复查"


def test_reminder_audit_targets_share_id(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-11-10"},
    ).json()["reminder"]
    client.post(f"/api/reminders/{created['id']}/complete")
    audit = client.get("/api/audit").json()["actions"]

    by_action = {item["action"]: item["target"] for item in audit}
    prefix = f"{created['id']}·"
    assert by_action["reminder.create"].startswith(prefix)
    assert by_action["reminder.complete"].startswith(prefix)


def test_reminders_ics_folds_long_lines(client_and_store):
    client, _store = client_and_store
    client.post(
        "/api/reminders",
        json={
            "title": "复查" * 20,
            "due_at": "2026-11-10",
            "note": "备注" * 30,
        },
    )

    text = client.get("/api/reminders/export.ics").content.decode("utf-8")

    for line in text.split("\r\n"):
        assert len(line.encode("utf-8")) <= 75, line


def test_reminders_csv_import(client_and_store):
    client, _store = client_and_store

    lines = [
        "到期日,内容,档案,状态,备注",
        "2026-11-10,复查血常规,母亲,未完成,空腹",
        "2026-11-10,复查血常规,母亲,未完成,空腹",
        "2026-11-12,接种疫苗,孩子,已完成,",
        "2026-02-30,无效日期,,,",
        ",缺少日期,,,",
    ]
    content = chr(0xFEFF) + "\n".join(lines)
    first = client.post(
        "/api/reminders/import",
        files={"file": ("reminders.csv", content.encode("utf-8"), "text/csv")},
    )
    listed = client.get("/api/reminders", params={"include_done": True})
    again = client.post(
        "/api/reminders/import",
        files={"file": ("reminders.csv", content.encode("utf-8"), "text/csv")},
    )
    empty = client.post(
        "/api/reminders/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )
    audit = client.get("/api/audit")

    assert first.status_code == 200
    assert first.json()["imported"] == 2
    assert first.json()["skipped"] == 2
    assert first.json()["invalid"] == 1
    titles = sorted(item["title"] for item in listed.json()["reminders"])
    assert titles == ["复查血常规", "接种疫苗"]
    done = [
        item["title"] for item in listed.json()["reminders"] if item["done"]
    ]
    assert done == ["接种疫苗"]
    assert again.status_code == 200
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 4
    assert empty.status_code == 400
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "reminder.import" in actions
