"""就诊预约 API、提醒与备份的离线测试。"""
import csv
import io
from datetime import datetime, timedelta

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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_appointments_api_roundtrip(client_and_store):
    client, _store = client_and_store
    scheduled = f"{_date(5)}T09:30"

    created = client.post("/api/appointments", json={
        "profile": "妈妈",
        "title": "心内科复诊",
        "department": "心内科",
        "doctor": "王医生",
        "location": "市医院 3 楼",
        "scheduled_at": scheduled,
        "note": "带化验单",
    })
    assert created.status_code == 200
    item = created.json()["appointment"]
    assert item["status"] == "upcoming"
    assert item["days_until"] == 5
    assert item["soon"] is True

    listed = client.get(
        "/api/appointments", params={"profile": "妈妈"}
    ).json()["appointments"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/appointments/{item['id']}",
        json={"status": "completed", "note": "已复诊"},
    )
    assert updated.status_code == 200
    updated_item = updated.json()["appointment"]
    assert updated_item["status"] == "completed"
    assert updated_item["title"] == "心内科复诊"
    assert updated_item["note"] == "已复诊"

    deleted = client.delete(f"/api/appointments/{item['id']}")
    missing = client.delete("/api/appointments/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "appointment.create" in actions
    assert "appointment.update" in actions
    assert "appointment.delete" in actions


def test_appointment_validation(client_and_store):
    client, _store = client_and_store

    blank = client.post("/api/appointments", json={
        "title": "   ", "scheduled_at": f"{_date(1)}T09:00",
    })
    missing_time = client.post("/api/appointments", json={
        "title": "复诊",
    })
    bad_time = client.post("/api/appointments", json={
        "title": "复诊", "scheduled_at": "not-a-time",
    })
    bad_status = client.post("/api/appointments", json={
        "title": "复诊",
        "scheduled_at": f"{_date(1)}T09:00",
        "status": "unknown",
    })

    assert blank.status_code == 400
    assert missing_time.status_code == 422
    assert bad_time.status_code == 400
    assert bad_status.status_code == 400
    assert client.get("/api/appointments").json()["appointments"] == []


def test_appointments_filters(client_and_store):
    client, _store = client_and_store
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "复诊",
        "scheduled_at": f"{_date(3)}T09:00",
    })
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "体检",
        "scheduled_at": f"{_date(-3)}T09:00",
        "status": "completed",
    })
    client.post("/api/appointments", json={
        "profile": "爸爸", "title": "口腔科",
        "scheduled_at": f"{_date(7)}T10:00",
    })

    upcoming = client.get(
        "/api/appointments", params={"upcoming": True}
    ).json()["appointments"]
    completed = client.get(
        "/api/appointments", params={"status": "completed"}
    ).json()["appointments"]
    mom = client.get(
        "/api/appointments", params={"profile": "妈妈"}
    ).json()["appointments"]

    assert {row["title"] for row in upcoming} == {"复诊", "口腔科"}
    assert [row["title"] for row in completed] == ["体检"]
    assert {row["title"] for row in mom} == {"复诊", "体检"}
    assert completed[0]["is_past"] is True


def test_appointment_reminder_is_idempotent(client_and_store):
    client, _store = client_and_store
    scheduled = f"{_date(5)}T09:30"
    item = client.post("/api/appointments", json={
        "profile": "妈妈",
        "title": "内分泌复诊",
        "department": "内分泌科",
        "scheduled_at": scheduled,
    }).json()["appointment"]

    first = client.post(f"/api/appointments/{item['id']}/reminder")
    second = client.post(f"/api/appointments/{item['id']}/reminder")

    assert first.status_code == 200
    assert first.json()["created"] is True
    reminder = first.json()["reminder"]
    assert reminder["appointment_id"] == item["id"]
    assert reminder["title"] == "就诊：内分泌复诊"
    assert reminder["due_at"] == _date(4)
    assert "09:30" in reminder["note"]
    assert "内分泌科" in reminder["note"]
    assert second.json()["created"] is False

    client.patch(
        f"/api/appointments/{item['id']}", json={"status": "completed"}
    )
    done_item = client.post("/api/appointments", json={
        "title": "已完成预约",
        "scheduled_at": scheduled,
        "status": "completed",
    }).json()["appointment"]
    assert client.post(
        f"/api/appointments/{done_item['id']}/reminder"
    ).status_code == 400


def test_appointments_export_csv(client_and_store):
    client, _store = client_and_store
    client.post("/api/appointments", json={
        "profile": "妈妈",
        "title": "=复诊",
        "department": "心内科",
        "scheduled_at": f"{_date(2)}T08:00",
    })

    export = client.get("/api/appointments/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "就诊时间", "患者档案", "主题", "科室", "医生", "地点",
        "状态", "备注", "ID",
    ]
    assert rows[1][2] == "'=复诊"
    assert rows[1][6] == "待就诊"


def test_appointments_in_timeline_dossier_and_family(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    scheduled = f"{_date(2)}T09:00"
    client.post("/api/appointments", json={
        "profile": "奶奶",
        "title": "眼科复查",
        "department": "眼科",
        "doctor": "李医生",
        "scheduled_at": scheduled,
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "appointment"}
    ).json()["events"]
    assert len(timeline) == 1
    assert timeline[0]["type"] == "appointment"
    assert timeline[0]["title"] == "眼科复查"
    assert "待就诊" in timeline[0]["value"]
    assert timeline[0]["alert"] is True

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 就诊预约" in dossier.text
    assert "眼科复查" in dossier.text
    assert "待就诊" in dossier.text

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["appointments"]["upcoming"] == 1
    assert grandma["appointments"]["next"]["title"] == "眼科复查"
    assert grandma["appointments"]["next"]["days_until"] == 2

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-4] == "下次就诊"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "眼科复查" in row[-4]
    assert "2 天后" in row[-4]


def test_appointments_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    scheduled = f"{_date(6)}T09:00"
    item = client.post("/api/appointments", json={
        "profile": "妈妈",
        "title": "骨科复诊",
        "department": "骨科",
        "scheduled_at": scheduled,
    }).json()["appointment"]
    reminder = client.post(
        f"/api/appointments/{item['id']}/reminder"
    ).json()["reminder"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["appointments"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_appointments()
        reminders = target.list_reminders(True)
    finally:
        target.close()

    assert result["appointments"] == 1
    assert len(restored) == 1
    assert restored[0]["title"] == "骨科复诊"
    assert restored[0]["scheduled_at"] == scheduled
    assert reminders[0]["appointment_id"] == restored[0]["id"]

    store.rename_profile("妈妈", "母亲")
    moved = store.list_appointments("母亲")
    assert [row["id"] for row in moved] == [item["id"]]
