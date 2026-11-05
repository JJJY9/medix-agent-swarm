"""血压分级统计的离线测试。"""
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore, classify_bp_grade


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_classify_bp_grade_thresholds():
    assert classify_bp_grade(118, 75) == "normal"
    assert classify_bp_grade(125, 82) == "high_normal"
    assert classify_bp_grade(150, 95) == "grade1"
    assert classify_bp_grade(165, 105) == "grade2"
    assert classify_bp_grade(185, 115) == "grade3"
    assert classify_bp_grade(85, 55) == "low"
    assert classify_bp_grade("bad", None) is None


def test_metric_summary_counts_bp_grades(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    for systolic, diastolic in (
        (118, 75), (125, 82), (150, 95),
        (165, 105), (185, 115), (85, 55),
    ):
        response = client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp",
            "value1": systolic, "value2": diastolic,
            "measured_at": now,
        })
        assert response.status_code == 200

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 190, "value2": 120,
        "measured_at": "2020-01-01T08:00",
    })

    data = client.get(
        "/api/metrics/summary",
        params={"profile": "妈妈", "metric": "bp", "days": 0},
    ).json()
    assert data["count"] == 7
    assert data["bp_grades"] == {
        "normal": 1, "high_normal": 1, "grade1": 1,
        "grade2": 1, "grade3": 2, "low": 1,
    }

    recent = client.get(
        "/api/metrics/summary",
        params={"profile": "妈妈", "metric": "bp", "days": 30},
    ).json()
    assert recent["bp_grades"]["grade3"] == 1

    weight = client.get(
        "/api/metrics/summary",
        params={"profile": "妈妈", "metric": "weight", "days": 0},
    ).json()
    assert weight["bp_grades"] is None
