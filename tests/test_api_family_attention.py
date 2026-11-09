"""家庭健康总览关注提示的边界规则测试。"""
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


def _attention_for(client, name):
    data = client.get("/api/family/overview", params={"days": 7}).json()
    return next(
        item for item in data["profiles"] if item["name"] == name
    )["attention"]


def test_attention_ignores_stale_consultation_risk(client_and_store):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取药", "due_at": _date(-1),
    })
    store.save_exchange("stale-risk", "头晕", "**高危**，尽快就医。")
    store.set_session_profile("stale-risk", "妈妈")
    old = (datetime.now() - timedelta(days=40)).isoformat(
        timespec="seconds"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            (old, "stale-risk"),
        )

    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    mom = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )

    assert mom["consultations"]["count"] == 0
    assert mom["attention"]["label"] == "逾期提醒 1 条"


def test_attention_uses_max_severity_in_window(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头痛", "severity": 9,
        "recorded_at": f"{_date(-1)}T08:00",
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕", "severity": 2,
        "recorded_at": f"{_date(0)}T08:00",
    })

    attention = _attention_for(client, "妈妈")

    assert attention["level"] == "warning"
    assert attention["label"] == "近期重度症状"
    assert "头痛" in attention["detail"]


def test_attention_ignores_old_labs(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "LDL", "value": 4.9,
        "ref_high": 3.4, "tested_at": f"{_date(-100)}T09:00",
    })

    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    mom = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )

    assert mom["labs"]["abnormal"] == 0
    assert mom["attention"]["level"] == "ok"


def test_attention_care_detail_only_overdue(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(-2),
    })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "肝功能复查",
        "interval_days": 30, "next_due_at": _date(2),
    })

    attention = _attention_for(client, "妈妈")

    assert attention["label"] == "复查逾期 1 项"
    assert "眼底检查" in attention["detail"]
    assert "肝功能复查" not in attention["detail"]


def test_attention_prefers_near_care_over_far_appointment(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "年度体检",
        "scheduled_at": f"{_date(500)}T09:00",
    })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "肝功能复查",
        "interval_days": 30, "next_due_at": _date(2),
    })

    attention = _attention_for(client, "妈妈")

    assert attention["label"] == "复查临近 1 项"


def test_attention_reminder_due_today(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取药", "due_at": _date(0),
    })

    attention = _attention_for(client, "妈妈")

    assert attention["level"] == "info"
    assert attention["label"] == "今天有 1 条提醒"


def test_attention_metric_over_target(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 130, "value2": 80,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 150, "value2": 95,
        "measured_at": f"{_date(0)}T08:00",
    })

    attention = _attention_for(client, "妈妈")

    assert attention["level"] == "warning"
    assert attention["label"] == "指标超标 1 项"


def test_attention_expiring_medication(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍",
        "expiry_date": _date(10),
    })

    attention = _attention_for(client, "妈妈")

    assert attention["level"] == "info"
    assert attention["label"] == "药物即将过期 1 种"
