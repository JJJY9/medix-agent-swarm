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
