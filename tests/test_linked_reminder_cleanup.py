"""预约/疫苗/复查删除或改期时关联提醒的级联测试。"""
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


def _reminders(client):
    return client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]


def test_appointment_reschedule_and_delete_sync_reminder(client_and_store):
    client, _store = client_and_store
    appointment = client.post("/api/appointments", json={
        "profile": "妈妈", "title": "心内科复诊",
        "scheduled_at": f"{_date(10)}T09:00",
    }).json()["appointment"]
    reminder = client.post(
        f"/api/appointments/{appointment['id']}/reminder"
    ).json()["reminder"]
    assert reminder["due_at"] == _date(9)

    client.patch(
        f"/api/appointments/{appointment['id']}",
        json={"scheduled_at": f"{_date(15)}T09:00"},
    )
    updated = next(
        row for row in _reminders(client) if row["id"] == reminder["id"]
    )
    assert updated["due_at"] == _date(14)

    client.delete(f"/api/appointments/{appointment['id']}")
    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_vaccination_reschedule_and_delete_sync_reminder(client_and_store):
    client, _store = client_and_store
    vaccination = client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "乙肝疫苗",
        "vaccinated_at": _date(-10), "next_due": _date(10),
    }).json()["vaccination"]
    reminder = client.post(
        f"/api/vaccinations/{vaccination['id']}/reminder"
    ).json()["reminder"]

    client.patch(
        f"/api/vaccinations/{vaccination['id']}",
        json={"next_due": _date(20)},
    )
    updated = next(
        row for row in _reminders(client) if row["id"] == reminder["id"]
    )
    assert updated["due_at"] == _date(20)

    client.delete(f"/api/vaccinations/{vaccination['id']}")
    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_care_item_delete_removes_generated_reminder(client_and_store):
    client, _store = client_and_store
    care_item = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(10),
    }).json()["care_item"]
    reminder = client.post(
        f"/api/care-items/{care_item['id']}/reminder"
    ).json()["reminder"]
    assert reminder["title"] == "复查：眼底检查"

    client.delete(f"/api/care-items/{care_item['id']}")
    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_appointment_completion_removes_pending_reminder(client_and_store):
    client, _store = client_and_store
    appointment = client.post("/api/appointments", json={
        "profile": "妈妈", "title": "心内科复诊",
        "scheduled_at": f"{_date(10)}T09:00",
    }).json()["appointment"]
    reminder = client.post(
        f"/api/appointments/{appointment['id']}/reminder"
    ).json()["reminder"]

    client.patch(
        f"/api/appointments/{appointment['id']}",
        json={"status": "completed"},
    )

    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_vaccination_clearing_next_due_removes_reminder(
    client_and_store,
):
    client, _store = client_and_store
    vaccination = client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "乙肝疫苗",
        "vaccinated_at": _date(-10), "next_due": _date(10),
    }).json()["vaccination"]
    reminder = client.post(
        f"/api/vaccinations/{vaccination['id']}/reminder"
    ).json()["reminder"]

    client.patch(
        f"/api/vaccinations/{vaccination['id']}",
        json={"next_due": ""},
    )

    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_care_item_reschedule_and_deactivate_sync_reminder(
    client_and_store,
):
    client, _store = client_and_store
    care_item = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(10),
    }).json()["care_item"]
    reminder = client.post(
        f"/api/care-items/{care_item['id']}/reminder"
    ).json()["reminder"]
    assert reminder["due_at"] == _date(3)

    client.patch(
        f"/api/care-items/{care_item['id']}",
        json={"next_due_at": _date(17)},
    )
    updated = next(
        row for row in _reminders(client) if row["id"] == reminder["id"]
    )
    assert updated["due_at"] == _date(10)
    assert _date(17) in updated["note"]

    client.patch(
        f"/api/care-items/{care_item['id']}",
        json={"active": False},
    )
    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_stopping_medication_removes_pending_reminder(client_and_store):
    client, _store = client_and_store
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "frequency": "每日一次",
    }).json()["medication"]
    reminder = client.post("/api/reminders", json={
        "title": "用药：氨氯地平",
        "due_at": _date(0),
        "profile": "妈妈",
        "repeat_days": 1,
        "medication_id": medication["id"],
    }).json()["reminder"]

    client.patch(
        f"/api/medications/{medication['id']}",
        json={"active": False, "stop_reason": "医生调整"},
    )

    assert all(
        row["id"] != reminder["id"] for row in _reminders(client)
    )


def test_stopping_medication_keeps_stock_and_expiry_reminders(
    client_and_store,
):
    client, _store = client_and_store
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "stock": 3,
        "low_stock_threshold": 7, "expiry_date": _date(20),
    }).json()["medication"]
    stock = client.post(
        f"/api/medications/{medication['id']}/reminder",
        params={"kind": "stock"},
    ).json()["reminder"]
    expiry = client.post(
        f"/api/medications/{medication['id']}/reminder",
        params={"kind": "expiry"},
    ).json()["reminder"]
    daily = client.post("/api/reminders", json={
        "title": "用药：氨氯地平",
        "due_at": _date(0),
        "profile": "妈妈",
        "repeat_days": 1,
        "medication_id": medication["id"],
    }).json()["reminder"]

    client.patch(
        f"/api/medications/{medication['id']}",
        json={"active": False},
    )

    remaining = {row["id"] for row in _reminders(client)}
    # 停药只停服药提醒；补货与药物到期提醒仍需保留
    assert daily["id"] not in remaining
    assert stock["id"] in remaining
    assert expiry["id"] in remaining


def test_same_title_care_plan_does_not_touch_other_reminder(
    client_and_store,
):
    client, _store = client_and_store
    first = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(10),
    }).json()["care_item"]
    second = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(10),
    }).json()["care_item"]
    reminder = client.post(
        f"/api/care-items/{second['id']}/reminder"
    ).json()["reminder"]
    assert reminder["due_at"] == _date(3)

    client.patch(
        f"/api/care-items/{first['id']}", json={"active": False}
    )
    client.patch(
        f"/api/care-items/{first['id']}",
        json={"next_due_at": _date(30)},
    )

    updated = next(
        row for row in _reminders(client) if row["id"] == reminder["id"]
    )
    # 同名计划无法区分归属时保守跳过同步，不能删除或改写另一条的提醒
    assert updated["due_at"] == _date(3)
