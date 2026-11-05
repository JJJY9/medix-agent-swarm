"""家庭健康总览 API 的离线测试。"""
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


def test_family_overview_summarizes_profiles(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    today = now.isoformat(timespec="minutes")
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    client.post("/api/profiles", json={
        "name": "妈妈",
        "context": {"age": "62", "sex": "女"},
    })
    client.post("/api/profiles", json={
        "name": "孩子",
        "context": {"age": "7", "sex": "男"},
    })
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": today,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight",
        "value1": 60, "measured_at": today,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 4, "recorded_at": today,
    })
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": today},
    )
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "复查血压", "due_at": yesterday,
    })
    store.save_exchange("family-session", "妈妈血压高怎么办", "建议复查。")
    store.set_session_profile("family-session", "妈妈")

    data = client.get("/api/family/overview").json()
    profiles = {item["name"]: item for item in data["profiles"]}

    assert set(profiles) == {"妈妈", "孩子"}
    mom = profiles["妈妈"]
    assert mom["context"] == {"age": "62", "sex": "女"}
    metrics = {item["metric"]: item for item in mom["metrics"]}
    assert metrics["bp"]["value"] == "140/95"
    assert metrics["bp"]["over_target"] is True
    assert metrics["weight"]["over_target"] is None
    assert mom["medications"]["active"] == 1
    assert mom["medications"]["taken_today"] == 1
    assert mom["medications"]["names"] == ["氨氯地平"]
    assert mom["medications"]["last_taken_at"]
    assert mom["medications"]["adherence"] is None
    assert mom["reminders"] == {
        "pending": 1, "overdue": 1, "due_today": 0,
    }
    assert mom["symptoms"]["count"] == 1
    assert mom["symptoms"]["latest"]["symptom"] == "头晕"
    assert mom["consultations"]["count"] == 1
    assert mom["last_activity"]

    child = profiles["孩子"]
    assert child["metrics"] == []
    assert child["medications"]["active"] == 0
    assert child["symptoms"]["count"] == 0
    assert child["last_activity"] == ""


def test_family_overview_medication_activity_and_due_today(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    taken_at = datetime.now().isoformat(timespec="minutes")

    medication = client.post("/api/medications", json={
        "profile": "爸爸", "name": "二甲双胍",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": taken_at},
    )
    client.post("/api/reminders", json={
        "profile": "爸爸", "title": "复诊", "due_at": today,
    })

    data = client.get("/api/family/overview").json()
    dad = {item["name"]: item for item in data["profiles"]}["爸爸"]

    assert dad["last_activity"]
    assert dad["reminders"] == {
        "pending": 1, "overdue": 0, "due_today": 1,
    }
    assert dad["medications"]["last_taken_at"]


def test_family_overview_medication_adherence(client_and_store):
    client, store = client_and_store
    now = datetime.now()

    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍", "frequency": "每日两次",
    }).json()["medication"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ? WHERE id = ?",
            (
                (now - timedelta(days=3)).isoformat(timespec="seconds"),
                medication["id"],
            ),
        )

    taken_at = now.isoformat(timespec="minutes")
    for _ in range(5):
        client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"taken_at": taken_at},
        )

    data = client.get("/api/family/overview").json()
    mom = {item["name"]: item for item in data["profiles"]}["妈妈"]

    assert mom["medications"]["adherence"] == {
        "expected": 8, "taken": 5, "rate": 63,
    }


def test_family_overview_includes_deleted_profiles(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/symptoms", json={
        "profile": "爷爷", "symptom": "咳嗽",
        "severity": 3, "recorded_at": now,
    })

    data = client.get("/api/family/overview", params={"days": 30}).json()
    profiles = {item["name"]: item for item in data["profiles"]}

    assert profiles["爷爷"]["context"] == {}
    assert profiles["爷爷"]["symptoms"]["count"] == 1
    assert data["days"] == 30
