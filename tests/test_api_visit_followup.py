"""就诊复诊一键安排与复查完成自动提醒测试。"""
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


def _create_visit(client, followup_days=30, **extra):
    payload = {
        "profile": "妈妈",
        "visit_date": _date(0),
        "department": "心内科",
        "doctor": "王医生",
        "facility": "市医院",
        "diagnosis": "高血压复诊",
        "next_followup_date": _date(followup_days),
        **extra,
    }
    return client.post("/api/visits", json=payload).json()["visit"]


def test_schedule_followup_creates_appointment_and_reminder(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client)

    response = client.post(f"/api/visits/{visit['id']}/schedule-followup")

    assert response.status_code == 200
    data = response.json()
    assert data["appointment_created"] is True
    assert data["reminder_created"] is True
    appointment = data["appointment"]
    assert appointment["profile"] == "妈妈"
    assert appointment["title"] == "复诊：高血压复诊"
    assert appointment["department"] == "心内科"
    assert appointment["doctor"] == "王医生"
    assert appointment["location"] == "市医院"
    assert appointment["scheduled_at"] == f"{_date(30)}T09:00"
    assert appointment["status"] == "upcoming"
    assert f"#{visit['id']}" in appointment["note"]

    reminder = data["reminder"]
    assert reminder["profile"] == "妈妈"
    assert reminder["title"] == "复诊：高血压复诊"
    assert reminder["due_at"] == _date(29)
    assert reminder["appointment_id"] == appointment["id"]

    listed = client.get(
        "/api/visits", params={"profile": "妈妈"}
    ).json()["visits"][0]
    assert listed["followup_scheduled"] is True
    assert listed["followup_appointment_id"] == appointment["id"]
    assert listed["followup_reminder_id"] == reminder["id"]

    reminders = client.get("/api/reminders").json()["reminders"]
    assert [row["id"] for row in reminders] == [reminder["id"]]

    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]
    assert "visit.followup" in actions


def test_visit_followup_links_survive_backup_restore(
    client_and_store, tmp_path
):
    client, store = client_and_store
    visit = _create_visit(client)
    followup = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()
    backup = store.export_data()

    target = SessionStore(str(tmp_path / "restored-followup.db"))
    try:
        # 目标库已有预约/提醒，导入后的自增编号会与备份内编号不同
        target.add_appointment(
            "爸爸", "已存在预约", scheduled_at=f"{_date(1)}T09:00"
        )
        target.add_reminder("已存在提醒", _date(1), profile="爸爸")
        result = target.import_data(backup)

        restored = target.list_visit_records("妈妈")[0]
        appointments = [
            row for row in target.list_appointments()
            if row["profile"] == "妈妈"
        ]
        reminders = [
            row for row in target.list_reminders(include_done=True)
            if row["profile"] == "妈妈"
        ]
        assert result["visit_records"] == 1
        assert restored["followup_appointment_id"] == appointments[0]["id"]
        assert restored["followup_reminder_id"] == reminders[0]["id"]
        # 关联已按目标库重新映射，而不是沿用备份内的旧编号
        assert appointments[0]["id"] != followup["appointment"]["id"]
        assert reminders[0]["id"] != followup["reminder"]["id"]
    finally:
        target.close()


def test_schedule_followup_is_idempotent(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client)

    first = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()
    second = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()

    assert second["appointment_created"] is False
    assert second["reminder_created"] is False
    assert second["appointment"]["id"] == first["appointment"]["id"]
    assert second["reminder"]["id"] == first["reminder"]["id"]
    reminders = client.get("/api/reminders").json()["reminders"]
    assert len(reminders) == 1


def test_schedule_followup_requires_followup_date(client_and_store):
    client, _store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": _date(0),
        "diagnosis": "体检",
    }).json()["visit"]

    missing = client.post(f"/api/visits/{visit['id']}/schedule-followup")
    gone = client.post("/api/visits/99999/schedule-followup")

    assert missing.status_code == 400
    assert "下次复诊" in missing.json()["detail"]
    assert gone.status_code == 404
    assert client.get("/api/appointments").json()["appointments"] == []


def test_schedule_followup_same_day_reminder_due_today(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client, followup_days=0)

    data = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()

    assert data["appointment"]["scheduled_at"] == f"{_date(0)}T09:00"
    assert data["reminder"]["due_at"] == _date(0)


def test_schedule_followup_recreates_cancelled_appointment(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client)
    first = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()

    client.patch(
        f"/api/appointments/{first['appointment']['id']}",
        json={"status": "cancelled"},
    )
    second = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()

    assert second["appointment_created"] is True
    assert second["appointment"]["id"] != first["appointment"]["id"]
    assert second["reminder_created"] is True
    assert (
        second["reminder"]["appointment_id"]
        == second["appointment"]["id"]
    )
    listed = client.get(
        "/api/visits", params={"profile": "妈妈"}
    ).json()["visits"][0]
    assert (
        listed["followup_appointment_id"]
        == second["appointment"]["id"]
    )


def test_delete_appointment_clears_visit_followup_link(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client)
    first = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()

    deleted = client.delete(
        f"/api/appointments/{first['appointment']['id']}"
    )
    listed = client.get(
        "/api/visits", params={"profile": "妈妈"}
    ).json()["visits"][0]

    assert deleted.status_code == 200
    assert listed["followup_scheduled"] is False
    assert listed["followup_appointment_id"] == 0

    again = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()
    assert again["appointment_created"] is True
    assert again["appointment"]["id"] != first["appointment"]["id"]


def test_updating_followup_date_resets_links(client_and_store):
    client, _store = client_and_store
    visit = _create_visit(client)
    client.post(f"/api/visits/{visit['id']}/schedule-followup")

    updated = client.patch(
        f"/api/visits/{visit['id']}",
        json={"next_followup_date": _date(60)},
    ).json()["visit"]

    assert updated["followup_scheduled"] is False
    assert updated["followup_appointment_id"] == 0
    assert updated["followup_reminder_id"] == 0

    again = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()
    assert again["appointment_created"] is True
    assert again["appointment"]["scheduled_at"] == f"{_date(60)}T09:00"

    unchanged = client.patch(
        f"/api/visits/{visit['id']}", json={"diagnosis": "高血压随访"}
    ).json()["visit"]
    assert unchanged["followup_scheduled"] is True


def test_care_done_auto_creates_next_reminder(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "糖化血红蛋白",
        "interval_days": 90,
        "next_due_at": _date(3),
    }).json()["care_item"]

    done = client.post(f"/api/care-items/{item['id']}/done", json={})

    assert done.status_code == 200
    data = done.json()
    assert data["care_item"]["last_done_at"] == _date(0)
    assert data["care_item"]["next_due_at"] == _date(90)
    assert data["reminder_created"] is True
    reminder = data["reminder"]
    assert reminder["title"] == "复查：糖化血红蛋白"
    assert reminder["due_at"] == _date(83)
    assert reminder["profile"] == "妈妈"

    again = client.post(
        f"/api/care-items/{item['id']}/done", json={}
    ).json()
    assert again["reminder_created"] is False
    assert again["reminder"]["id"] == reminder["id"]
    reminders = client.get("/api/reminders").json()["reminders"]
    assert len(reminders) == 1


def test_care_done_skips_reminder_for_inactive_item(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "眼底检查",
        "interval_days": 365,
        "next_due_at": _date(1),
    }).json()["care_item"]
    client.patch(
        f"/api/care-items/{item['id']}", json={"active": False}
    )

    done = client.post(
        f"/api/care-items/{item['id']}/done", json={}
    ).json()

    assert done["reminder"] is None
    assert done["reminder_created"] is False
    assert client.get("/api/reminders").json()["reminders"] == []
