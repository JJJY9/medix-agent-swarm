"""健康月历 API 的离线测试。"""
from datetime import datetime

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


def test_health_calendar_aggregates_month(client_and_store):
    client, store = client_and_store

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": "2026-10-02T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight",
        "value1": 60, "measured_at": "2026-10-02T09:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 4, "recorded_at": "2026-10-05T10:00",
    })
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": "2026-10-02T12:00"},
    )
    pending = client.post("/api/reminders", json={
        "profile": "妈妈", "title": "复查", "due_at": "2026-10-02",
    }).json()["reminder"]
    done = client.post("/api/reminders", json={
        "profile": "妈妈", "title": "复诊", "due_at": "2026-10-06",
    }).json()["reminder"]
    client.post(f"/api/reminders/{done['id']}/complete")
    assert pending["id"] != done["id"]

    store.save_exchange("calendar-session", "血压复查咨询", "建议复查。")
    store.set_session_profile("calendar-session", "妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            ("2026-10-03T09:00:00", "calendar-session"),
        )

    client.post("/api/metrics", json={
        "profile": "孩子", "metric": "weight",
        "value1": 25, "measured_at": "2026-10-02T08:00",
    })

    data = client.get(
        "/api/health/calendar",
        params={"profile": "妈妈", "month": "2026-10"},
    ).json()

    assert data["month"] == "2026-10"
    assert data["start"] == "2026-10-01"
    assert data["end"] == "2026-10-31"
    day_two = data["days"]["2026-10-02"]
    assert day_two["metrics"] == 2
    assert day_two["doses"] == 1
    assert day_two["reminders_due"] == 1
    assert data["days"]["2026-10-03"]["consultations"] == 1
    assert data["days"]["2026-10-05"]["symptoms"] == 1
    assert data["days"]["2026-10-06"]["reminders_done"] == 1
    assert data["totals"]["metrics"] == 2
    assert data["totals"]["consultations"] == 1

    child = client.get(
        "/api/health/calendar",
        params={"profile": "孩子", "month": "2026-10"},
    ).json()
    assert child["days"]["2026-10-02"]["metrics"] == 1
    assert child["totals"]["metrics"] == 1

    family = client.get(
        "/api/health/calendar", params={"month": "2026-10"}
    ).json()
    assert family["days"]["2026-10-02"]["metrics"] == 3

    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 150, "value2": 95, "measured_at": "2026-10-08T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "胸闷",
        "severity": 8, "recorded_at": "2026-10-08T09:00",
    })
    store.save_exchange(
        "calendar-alert-session", "胸痛咨询", "风险等级：高危。建议立即就医。"
    )
    store.set_session_profile("calendar-alert-session", "妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            ("2026-10-09T09:00:00", "calendar-alert-session"),
        )

    alerts = client.get(
        "/api/health/calendar", params={"month": "2026-10"}
    ).json()
    assert alerts["days"]["2026-10-08"]["alerts"] == 2
    assert alerts["days"]["2026-10-09"]["alerts"] == 1
    assert alerts["totals"]["alerts"] == 3

    unassigned = client.get(
        "/api/health/calendar",
        params={"profile": "", "month": "2026-10"},
    ).json()
    assert unassigned["days"] == {}


def test_health_calendar_defaults_to_current_month(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")

    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头痛",
        "severity": 3, "recorded_at": f"{today}T10:00",
    })

    data = client.get("/api/health/calendar").json()

    assert data["month"] == today[:7]
    assert data["days"][today]["symptoms"] == 1


def test_health_calendar_validates_month(client_and_store):
    client, _store = client_and_store

    assert client.get(
        "/api/health/calendar", params={"month": "2026-13"}
    ).status_code == 400
    assert client.get(
        "/api/health/calendar", params={"month": "1999-01"}
    ).status_code == 400
    assert client.get(
        "/api/health/calendar", params={"month": "not-a-month"}
    ).status_code == 400


def test_health_calendar_month_boundaries(client_and_store):
    client, _store = client_and_store

    for day in ("2026-12-31", "2027-01-01", "2028-02-29"):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": 60, "measured_at": f"{day}T08:00",
        })

    december = client.get(
        "/api/health/calendar",
        params={"profile": "妈妈", "month": "2026-12"},
    ).json()
    assert december["end"] == "2026-12-31"
    assert december["days"]["2026-12-31"]["metrics"] == 1
    assert "2027-01-01" not in december["days"]

    january = client.get(
        "/api/health/calendar",
        params={"profile": "妈妈", "month": "2027-01"},
    ).json()
    assert january["days"]["2027-01-01"]["metrics"] == 1

    february = client.get(
        "/api/health/calendar",
        params={"profile": "妈妈", "month": "2028-02"},
    ).json()
    assert february["end"] == "2028-02-29"
    assert february["days"]["2028-02-29"]["metrics"] == 1


def test_health_calendar_empty_month(client_and_store):
    client, _store = client_and_store

    data = client.get(
        "/api/health/calendar", params={"month": "2026-09"}
    ).json()

    assert data["days"] == {}
    assert data["totals"] == {
        "metrics": 0, "symptoms": 0, "doses": 0,
        "consultations": 0, "reminders_due": 0, "reminders_done": 0,
        "alerts": 0,
    }
