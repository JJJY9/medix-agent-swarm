"""健康指标自定义目标 API 的离线测试。"""
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


def test_metric_targets_api_roundtrip(client_and_store):
    client, _store = client_and_store

    empty = client.get("/api/metric-targets")
    created = client.put(
        "/api/metric-targets",
        json={"profile": "母亲", "metric": "bp", "value1": 130, "value2": 80},
    )
    listed = client.get("/api/metric-targets", params={"profile": "母亲"})
    invalid = client.put(
        "/api/metric-targets",
        json={"profile": "母亲", "metric": "weight", "value1": -5},
    )
    missing = client.delete(
        "/api/metric-targets/weight", params={"profile": "母亲"}
    )
    deleted = client.delete(
        "/api/metric-targets/bp", params={"profile": "母亲"}
    )
    after = client.get("/api/metric-targets")
    audit = client.get("/api/audit")

    assert empty.status_code == 200
    assert empty.json()["targets"] == []

    assert created.status_code == 200
    target = created.json()["target"]
    assert target["value1"] == 130
    assert target["value2"] == 80
    assert target["unit"] == "mmHg"

    assert [
        (item["profile"], item["metric"]) for item in listed.json()["targets"]
    ] == [("母亲", "bp")]

    assert invalid.status_code == 400
    assert missing.status_code == 404
    assert deleted.status_code == 200
    assert after.json()["targets"] == []

    actions = [item["action"] for item in audit.json()["actions"]]
    assert "metric_target.set" in actions
    assert "metric_target.delete" in actions


def test_metric_targets_validate_bp_pair(client_and_store):
    client, _store = client_and_store

    missing_diastolic = client.put(
        "/api/metric-targets",
        json={"profile": "母亲", "metric": "bp", "value1": 130},
    )
    reversed_pair = client.put(
        "/api/metric-targets",
        json={"profile": "母亲", "metric": "bp", "value1": 80, "value2": 130},
    )
    bad_metric = client.put(
        "/api/metric-targets",
        json={"profile": "母亲", "metric": "unknown", "value1": 1},
    )

    assert missing_diastolic.status_code == 400
    assert reversed_pair.status_code == 400
    assert bad_metric.status_code == 422


def test_import_backup_preserves_metric_targets(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "sessions": [],
            "profiles": [],
            "health_metrics": [],
            "metric_targets": [
                {"profile": "母亲", "metric": "bp", "value1": 130, "value2": 80}
            ],
        },
    )
    listed = client.get("/api/metric-targets")

    assert response.status_code == 200
    assert response.json()["targets"] == 1
    targets = listed.json()["targets"]
    assert targets[0]["profile"] == "母亲"
    assert targets[0]["value1"] == 130
    assert targets[0]["value2"] == 80
