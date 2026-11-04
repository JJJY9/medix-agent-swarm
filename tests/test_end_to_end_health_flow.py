"""健康数据主流程的集成测试：档案、指标、症状、用药、打卡、提醒、周报与备份。"""
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


def test_health_journey_end_to_end(client_and_store, tmp_path):
    client, _store = client_and_store
    recent = datetime.now().isoformat(timespec="minutes")

    assert client.post(
        "/api/profiles",
        json={"name": "妈妈", "context": {"age": "58"}},
    ).status_code == 200
    assert client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 128, "value2": 82, "measured_at": recent,
    }).status_code == 200
    assert client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85,
    }).status_code == 200
    assert client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 4,
        "recorded_at": recent,
    }).status_code == 200

    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平",
        "dose": "5 mg", "frequency": "每日一次",
    }).json()["medication"]
    assert client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": recent, "note": "早餐后"},
    ).status_code == 200
    reminder = client.post("/api/reminders", json={
        "profile": "妈妈", "title": "用药：氨氯地平",
        "due_at": recent[:10], "medication_id": medication["id"],
    }).json()["reminder"]
    assert reminder["medication_id"] == medication["id"]

    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈"}
    ).json()
    assert weekly["metrics"]["count"] == 1
    assert weekly["metrics"]["by_metric"][0]["within_target"] == 1
    assert weekly["symptoms"]["count"] == 1
    assert weekly["medications"]["doses"] == 1
    assert weekly["reminders"]["pending"] == 1

    renamed = client.patch(
        "/api/profiles/妈妈", json={"new_name": "母亲"}
    )
    assert renamed.status_code == 200
    assert client.get(
        "/api/metrics", params={"profile": "母亲"}
    ).json()["metrics"]
    assert client.get(
        "/api/medications", params={"profile": "母亲"}
    ).json()["medications"]
    assert client.get(
        "/api/reminders", params={"profile": "母亲"}
    ).json()["reminders"]
    logs = client.get(
        "/api/medication-logs", params={"profile": "母亲"}
    ).json()["logs"]
    assert logs and logs[0]["medication_id"] == medication["id"]

    backup = client.get("/api/sessions/export").json()
    assert len(backup["medication_logs"]) == 1
    assert backup["reminders"][0]["medication_id"] == medication["id"]

    assert client.delete(
        f"/api/medications/{medication['id']}"
    ).status_code == 200
    assert client.get(
        "/api/medications", params={"profile": "母亲"}
    ).json()["medications"] == []
    assert client.get(
        "/api/medication-logs", params={"profile": "母亲"}
    ).json()["logs"] == []
    assert client.get(
        "/api/reminders", params={"profile": "母亲"}
    ).json()["reminders"] == []

    other = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = other.import_data(backup)
        assert result["profiles"] == 1
        assert result["metrics"] == 1
        assert result["symptoms"] == 1
        assert result["medications"] == 1
        assert result["medication_logs"] == 1
        assert result["reminders"] == 1

        restored_medication = other.list_medications(profile="母亲")[0]
        restored_logs = other.list_medication_logs(profile="母亲")
        assert restored_logs[0]["medication_id"] == restored_medication["id"]
        restored_reminder = other.list_reminders(profile="母亲")[0]
        assert restored_reminder["medication_id"] == restored_medication["id"]
        assert restored_reminder["title"] == "用药：氨氯地平"
    finally:
        other.close()
