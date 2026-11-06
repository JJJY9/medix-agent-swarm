"""健康指标测量情境的离线测试。"""
import csv
import io
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


def _at(hour=8):
    return datetime.now().replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat(timespec="minutes")


def test_metric_context_roundtrip(client_and_store):
    client, _store = client_and_store
    morning = client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 135, "value2": 85,
        "measured_at": _at(7), "context": "morning",
    })
    evening = client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 145, "value2": 92,
        "measured_at": _at(20), "context": "evening",
    })
    fasting = client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose",
        "value1": 6.1, "measured_at": _at(7), "context": "fasting",
    })

    assert morning.status_code == 200
    assert morning.json()["metric"]["context"] == "morning"
    assert evening.json()["metric"]["context"] == "evening"
    assert fasting.json()["metric"]["context"] == "fasting"

    listed = client.get(
        "/api/metrics", params={"metric": "bp"}
    ).json()["metrics"]
    assert {row["context"] for row in listed} == {"morning", "evening"}

    item = morning.json()["metric"]
    updated = client.patch(
        f"/api/metrics/{item['id']}", json={"context": "evening"}
    )
    assert updated.status_code == 200
    assert updated.json()["metric"]["context"] == "evening"


def test_metric_context_validation(client_and_store):
    client, _store = client_and_store
    bad_bp = client.post("/api/metrics", json={
        "metric": "bp", "value1": 130, "value2": 80,
        "context": "fasting",
    })
    bad_glucose = client.post("/api/metrics", json={
        "metric": "glucose", "value1": 5.6, "context": "morning",
    })
    weight_context = client.post("/api/metrics", json={
        "metric": "weight", "value1": 70, "context": "morning",
    })

    assert bad_bp.status_code == 400
    assert bad_glucose.status_code == 400
    assert weight_context.status_code == 200
    assert weight_context.json()["metric"]["context"] == ""


def test_metric_context_csv_roundtrip(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 135, "value2": 85,
        "measured_at": _at(7), "context": "morning",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose",
        "value1": 8.2, "measured_at": _at(9), "context": "postprandial",
    })

    export = client.get("/api/metrics/export.csv")
    text = export.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0] == [
        "测量时间", "患者档案", "指标", "数值1", "数值2", "单位",
        "情境", "备注",
    ]
    assert "晨起" in text
    assert "餐后" in text

    for item in client.get("/api/metrics").json()["metrics"]:
        client.delete(f"/api/metrics/{item['id']}")
    imported = client.post(
        "/api/metrics/import",
        files={"file": ("metrics.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 2
    restored = client.get("/api/metrics").json()["metrics"]
    assert {row["context"] for row in restored} == {
        "morning", "postprandial",
    }


def test_metric_context_in_timeline_and_dossier(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "bp",
        "value1": 135, "value2": 85,
        "measured_at": _at(7), "context": "morning",
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "metric"}
    ).json()["events"]
    event = next(row for row in timeline if row["title"] == "血压")
    assert "晨起" in event["value"]

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "晨起" in dossier.text
