"""健康指标危急值提示的离线测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore, critical_metric_alerts


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_critical_metric_alerts_rules():
    assert critical_metric_alerts("bp", 185, 125)
    assert critical_metric_alerts("bp", 85, 55)
    assert critical_metric_alerts("glucose", 3.2)
    assert critical_metric_alerts("glucose", 18.0)
    assert critical_metric_alerts("temperature", 39.5)
    assert critical_metric_alerts("temperature", 34.0)
    assert critical_metric_alerts("heart_rate", 130)
    assert critical_metric_alerts("heart_rate", 45)
    assert critical_metric_alerts("spo2", 88)

    assert critical_metric_alerts("bp", 120, 80) == []
    assert critical_metric_alerts("glucose", 5.6) == []
    assert critical_metric_alerts("temperature", 37.0) == []
    assert critical_metric_alerts("heart_rate", 72) == []
    assert critical_metric_alerts("spo2", 98) == []
    assert critical_metric_alerts("weight", 70) == []


def test_metric_api_returns_critical_alerts(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": "bp",
        "value1": 185,
        "value2": 125,
    })
    assert created.status_code == 200
    alerts = created.json()["critical_alerts"]
    assert len(alerts) == 1
    assert alerts[0]["level"] == "critical"
    assert "190" not in alerts[0]["message"]
    assert "185/125" in alerts[0]["message"]

    listed = client.get(
        "/api/metrics", params={"profile": "妈妈"}
    ).json()["metrics"]
    assert len(listed[0]["critical_alerts"]) == 1

    latest = client.get(
        "/api/metrics/latest", params={"profile": "妈妈"}
    ).json()["metrics"]
    assert len(latest[0]["critical_alerts"]) == 1

    normal = client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": "glucose",
        "value1": 5.6,
    })
    assert normal.json()["critical_alerts"] == []


def test_critical_alerts_in_family_timeline_and_dossier(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/metrics", json={
        "profile": "奶奶",
        "metric": "spo2",
        "value1": 88,
    })
    client.post("/api/metrics", json={
        "profile": "奶奶",
        "metric": "glucose",
        "value1": 5.6,
    })

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["critical_alerts"]["count"] == 1
    assert "血氧饱和度" in grandma["critical_alerts"]["items"][0]

    export = client.get("/api/family/overview/export.csv")
    text = export.content.decode("utf-8-sig")
    assert "危急值" in text
    assert "血氧饱和度" in text

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "metric"}
    ).json()["events"]
    spo2_event = next(row for row in timeline if row["title"] == "血氧饱和度")
    assert spo2_event["critical"] is True
    assert spo2_event["alert"] is True
    assert "88" in spo2_event["critical_message"]

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "危急值" in dossier.text


def test_critical_alerts_can_be_updated(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "heart_rate", "value1": 130,
    }).json()["metric"]
    updated = client.patch(
        f"/api/metrics/{item['id']}", json={"value1": 72}
    )
    assert updated.status_code == 200
    assert updated.json()["critical_alerts"] == []
