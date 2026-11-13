"""第二十三轮审查：步数/饮水按自然日累计的今日进度。"""
from datetime import datetime, timedelta
from pathlib import Path

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


def test_steps_daily_total_aggregates_records(client_and_store):
    _client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 3000, measured_at=f"{today}T08:00")
    store.add_metric("妈妈", "steps", 5000, measured_at=f"{today}T18:00")
    store.add_metric("妈妈", "steps", 9000, measured_at=f"{yesterday}T20:00")
    store.set_metric_target("妈妈", "steps", 8000)

    summary = store.metric_summary("妈妈", "steps", 7)

    assert summary["daily_count"] == 2
    assert summary["daily_total"] == 8000.0
    assert summary["daily_within_target"] is True


def test_water_daily_total_below_target(client_and_store):
    _client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "water", 600, measured_at=f"{today}T09:00")
    store.set_metric_target("妈妈", "water", 1500)

    summary = store.metric_summary("妈妈", "water", 7)

    assert summary["daily_count"] == 1
    assert summary["daily_total"] == 600.0
    assert summary["daily_within_target"] is False


def test_non_accumulating_metric_has_no_daily_total(client_and_store):
    _client, store = client_and_store
    store.add_metric("妈妈", "weight", 62.5)

    summary = store.metric_summary("妈妈", "weight", 7)

    assert summary["daily_total"] is None
    assert summary["daily_count"] == 0
    assert summary["daily_within_target"] is None


def test_summary_endpoint_returns_daily_progress(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 4000, measured_at=f"{today}T10:00")
    store.set_metric_target("妈妈", "steps", 6000)

    data = client.get(
        "/api/metrics/summary",
        params={"metric": "steps", "profile": "妈妈", "days": 7},
    ).json()

    assert data["daily_count"] == 1
    assert data["daily_total"] == 4000.0
    assert data["daily_within_target"] is False


def test_frontend_renders_daily_progress_chip():
    app = (
        Path(__file__).resolve().parents[1] / "web" / "app.js"
    ).read_text(encoding="utf-8")

    assert "data.daily_total" in app
    assert "今日累计" in app
    assert "还差" in app
