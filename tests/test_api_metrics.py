"""健康指标 API 的离线测试。"""
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


def test_metrics_api_roundtrip(client):
    created = client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 138,
            "value2": 86,
            "measured_at": "2026-01-02T08:00",
            "note": "晨起",
        },
    )
    weight = client.post(
        "/api/metrics",
        json={"profile": "妈妈", "metric": "weight", "value1": 62.5},
    )
    listed = client.get("/api/metrics")
    filtered = client.get("/api/metrics", params={"metric": "bp"})
    deleted = client.delete(f"/api/metrics/{created.json()['metric']['id']}")
    after_delete = client.get("/api/metrics")
    missing = client.delete("/api/metrics/999999")

    assert created.status_code == 200
    item = created.json()["metric"]
    assert item["unit"] == "mmHg"
    assert item["value1"] == 138
    assert weight.status_code == 200

    assert listed.status_code == 200
    assert len(listed.json()["metrics"]) == 2
    assert [row["metric"] for row in filtered.json()["metrics"]] == ["bp"]

    assert deleted.status_code == 200
    assert [row["metric"] for row in after_delete.json()["metrics"]] == ["weight"]
    assert missing.status_code == 404


def test_metrics_api_validation(client):
    missing_diastolic = client.post(
        "/api/metrics",
        json={"metric": "bp", "value1": 130},
    )
    reversed_bp = client.post(
        "/api/metrics",
        json={"metric": "bp", "value1": 80, "value2": 120},
    )
    unknown_metric = client.post(
        "/api/metrics",
        json={"metric": "unknown", "value1": 1},
    )
    invalid_time = client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 60,
            "measured_at": "not-a-date",
        },
    )
    invalid_list_filter = client.get(
        "/api/metrics", params={"metric": "unknown"}
    )

    assert missing_diastolic.status_code == 400
    assert reversed_bp.status_code == 400
    assert unknown_metric.status_code == 422
    assert invalid_time.status_code == 400
    assert invalid_list_filter.status_code == 400


def test_import_api_accepts_metrics_only(client):
    response = client.post(
        "/api/sessions/import",
        json={
            "health_metrics": [
                {
                    "profile": "孩子",
                    "metric": "temperature",
                    "value1": 38.2,
                    "measured_at": "2026-02-01T20:00",
                }
            ]
        },
    )
    listed = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.json()["metrics"] == 1
    assert listed.json()["metrics"][0]["value1"] == 38.2
