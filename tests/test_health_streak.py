"""健康记录连续天数 API 的离线测试。"""
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


def _at(days_ago):
    return (
        datetime.now() - timedelta(days=days_ago)
    ).isoformat(timespec="minutes")


def test_health_streak_current_and_best(client_and_store):
    client, _store = client_and_store

    for days_ago in (0, 1, 2):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": 60, "measured_at": _at(days_ago),
        })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 3, "recorded_at": _at(4),
    })

    data = client.get(
        "/api/health/streak", params={"profile": "妈妈"}
    ).json()

    assert data["current"] == 3
    assert data["best"] == 3
    assert data["recorded_today"] is True
    assert data["days_last_30"] == 4


def test_health_streak_counts_through_yesterday(client_and_store):
    client, _store = client_and_store

    for days_ago in (1, 2):
        client.post("/api/metrics", json={
            "profile": "爸爸", "metric": "weight",
            "value1": 70, "measured_at": _at(days_ago),
        })

    data = client.get(
        "/api/health/streak", params={"profile": "爸爸"}
    ).json()

    assert data["recorded_today"] is False
    assert data["current"] == 2


def test_health_streak_includes_consultations(client_and_store):
    client, store = client_and_store

    store.save_exchange("streak-session", "最近头晕", "建议休息。")
    store.set_session_profile("streak-session", "爷爷")

    data = client.get(
        "/api/health/streak", params={"profile": "爷爷"}
    ).json()

    assert data["current"] == 1
    assert data["recorded_today"] is True


def test_health_streak_ignores_future_records(client_and_store):
    client, _store = client_and_store
    tomorrow = (
        datetime.now() + timedelta(days=1)
    ).isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight",
        "value1": 60, "measured_at": tomorrow,
    })

    data = client.get(
        "/api/health/streak", params={"profile": "妈妈"}
    ).json()

    assert data["recorded_today"] is False
    assert data["current"] == 0
    assert data["best"] == 0
    assert data["days_last_30"] == 0


def test_health_streak_exposed_in_weekly_and_family_overview(
    client_and_store
):
    client, _store = client_and_store

    for days_ago in (0, 1):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": 60, "measured_at": _at(days_ago),
        })

    report = client.get(
        "/api/reports/weekly", params={"profile": "妈妈"}
    ).json()
    assert report["streak"]["current"] == 2
    assert report["streak"]["recorded_today"] is True

    overview = client.get("/api/family/overview").json()
    mom = {item["name"]: item for item in overview["profiles"]}["妈妈"]
    assert mom["streak"] == 2
