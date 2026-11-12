"""未来日期数据不得进入“今天/近 N 天”窗口。"""
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


def _tomorrow_at(hour=0, minute=30):
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    return f"{tomorrow}T{hour:02d}:{minute:02d}"


def _insert_metric(store, profile, metric, value):
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO health_metrics("
            "profile, metric, value1, value2, unit, measured_at, "
            "context, note, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (
                profile, metric, value, None, "%", _tomorrow_at(),
                "", "", store._now(),
            ),
        )


def test_latest_metrics_by_profile_ignores_future(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        _insert_metric(store, "未来", "spo2", 85)
        rows = store.latest_metrics_by_profile()
    finally:
        store.close()

    assert all(row["profile"] != "未来" for row in rows)


def test_recent_labs_exclude_future(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        with store._lock, store._conn:
            store._conn.execute(
                "INSERT INTO lab_results("
                "profile, panel, item, value, unit, ref_low, ref_high, "
                "tested_at, lab_name, note, created_at, updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    "妈妈", "血常规", "血红蛋白", 120, "g/L",
                    None, None, _tomorrow_at(), "", "",
                    store._now(), store._now(),
                ),
            )
        rows = store.list_lab_results("妈妈", days=7)
    finally:
        store.close()

    assert rows == []


def test_timeline_future_only_keeps_appointments(client_and_store):
    client, store = client_and_store
    _insert_metric(store, "妈妈", "spo2", 85)
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO lab_results("
            "profile, panel, item, value, unit, ref_low, ref_high, "
            "tested_at, lab_name, note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "妈妈", "血常规", "血红蛋白", 120, "g/L",
                None, None, _tomorrow_at(), "", "",
                store._now(), store._now(),
            ),
        )
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "明日复诊",
        "scheduled_at": _tomorrow_at(9, 0),
    })

    events = client.get(
        "/api/timeline", params={"profile": "妈妈", "days": 1}
    ).json()["events"]

    types = [event["type"] for event in events]
    assert "appointment" in types
    assert "lab" not in types
    assert "metric" not in types


def test_quality_insights_ignore_future_messages(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.import_data({
            "sessions": [{
                "id": "future-session",
                "title": "未来会话",
                "created_at": "2999-01-01T00:00:00",
                "updated_at": "2999-01-01T00:00:00",
                "messages": [{
                    "role": "assistant",
                    "content": "未来回答",
                    "created_at": "2999-01-01T00:00:00",
                }],
            }],
        })
        quality = store.quality_insights(days=30)
    finally:
        store.close()

    assert quality["turns"] == 0
    assert quality["sessions"] == 0


def test_family_overview_ignores_future_labs_and_sessions(
    client_and_store,
):
    client, store = client_and_store
    client.post("/api/profiles", json={
        "name": "妈妈", "context": {"age": "60", "sex": "女"},
    })
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO lab_results("
            "profile, panel, item, value, unit, ref_low, ref_high, "
            "tested_at, lab_name, note, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "妈妈", "血常规", "血红蛋白", 120, "g/L",
                None, None, _tomorrow_at(), "", "",
                store._now(), store._now(),
            ),
        )
    store.import_data({
        "sessions": [{
            "id": "future-family",
            "profile": "妈妈",
            "title": "未来会话",
            "created_at": "2999-01-01T00:00:00",
            "updated_at": "2999-01-01T00:00:00",
            "messages": [{
                "role": "assistant",
                "content": "未来回答",
                "created_at": "2999-01-01T00:00:00",
            }],
        }],
    })

    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()["profiles"]
    mom = next(item for item in overview if item["name"] == "妈妈")

    assert mom["labs"]["count"] == 0
    assert mom["consultations"]["count"] == 0
