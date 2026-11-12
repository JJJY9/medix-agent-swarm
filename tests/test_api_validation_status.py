"""已存在记录的非法字段应返回 400，而不是 404。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


def test_invalid_metric_value_returns_400(client):
    metric = client.post("/api/metrics", json={
        "metric": "bp", "value1": 120, "value2": 80,
        "measured_at": "2026-01-02T08:00",
    }).json()["metric"]

    response = client.patch(
        f"/api/metrics/{metric['id']}", json={"value1": 500}
    )

    assert response.status_code == 400
    assert "范围" in response.json()["detail"]


def test_invalid_medication_expiry_returns_400(client):
    medication = client.post("/api/medications", json={
        "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]

    response = client.patch(
        f"/api/medications/{medication['id']}",
        json={"expiry_date": "2026-13-99"},
    )

    assert response.status_code == 400
    assert "内容不正确" in response.json()["detail"]


def test_invalid_symptom_time_returns_400(client):
    symptom = client.post("/api/symptoms", json={
        "symptom": "头晕", "severity": 4,
        "recorded_at": "2026-01-02T08:00",
    }).json()["symptom"]

    response = client.patch(
        f"/api/symptoms/{symptom['id']}",
        json={"recorded_at": "not-a-date"},
    )

    assert response.status_code == 400
    assert "时间" in response.json()["detail"]


def test_medication_log_bad_time_and_missing_medication(client):
    medication = client.post("/api/medications", json={
        "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]

    invalid = client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": "2999-01-01T00:00"},
    )
    missing = client.post(
        "/api/medications/999999/logs",
        json={"taken_at": "2026-01-02T08:00"},
    )

    assert invalid.status_code == 400
    assert missing.status_code == 404
