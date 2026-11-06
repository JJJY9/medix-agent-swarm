"""第五轮报表与家庭 API 审计回归：窗口上限、档案计数、连续天数与周报风险。"""
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


def _insert_sessions(store, prefix, count, created_at, profile=""):
    sessions = [
        (f"{prefix}{index}", f"标题{index}", created_at, created_at)
        for index in range(count)
    ]
    messages = [
        (
            f"{prefix}{index}", "user", "问题", "{}", created_at
        )
        for index in range(count)
    ]
    with store._lock, store._conn:
        store._conn.executemany(
            "INSERT INTO sessions(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
            sessions,
        )
        store._conn.executemany(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            messages,
        )
        if profile:
            store._conn.execute(
                "UPDATE sessions SET profile = ? WHERE id LIKE ?",
                (profile, f"{prefix}%"),
            )


def test_calendar_month_is_not_truncated_by_newer_sessions(client_and_store):
    client, store = client_and_store
    _insert_sessions(store, "new-", 2001, "2026-06-01T09:00:00")
    _insert_sessions(store, "old-", 1, "2025-01-15T09:00:00")

    data = client.get(
        "/api/health/calendar", params={"month": "2025-01"}
    ).json()

    assert data["days"]["2025-01-15"]["consultations"] == 1


def test_dossier_includes_all_sessions_in_window(client_and_store):
    client, store = client_and_store
    now = datetime.now().replace(microsecond=0)
    _insert_sessions(
        store, "dossier-", 250, now.isoformat(timespec="seconds"),
        profile="妈妈",
    )

    response = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈", "days": 30}
    )

    assert response.status_code == 200
    text = response.text
    assert "标题0" in text
    assert "标题249" in text


def test_family_overview_counts_all_sessions(client_and_store):
    client, store = client_and_store
    now = datetime.now().replace(microsecond=0)
    _insert_sessions(
        store, "family-", 2001, now.isoformat(timespec="seconds"),
        profile="妈妈",
    )

    data = client.get("/api/family/overview", params={"days": 30}).json()

    mom = next(item for item in data["profiles"] if item["name"] == "妈妈")
    assert mom["consultations"]["count"] == 2001


def test_streak_can_exceed_one_year(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    rows = [
        (
            "妈妈", "weight", 60.0, None, "kg",
            (today - timedelta(days=offset)).isoformat(),
            "", (today - timedelta(days=offset)).isoformat(),
        )
        for offset in range(400)
    ]
    with store._lock, store._conn:
        store._conn.executemany(
            "INSERT INTO health_metrics("
            "profile, metric, value1, value2, unit, measured_at, note, "
            "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            rows,
        )

    data = client.get(
        "/api/health/streak", params={"profile": "妈妈"}
    ).json()

    assert data["current"] == 400


def test_timeline_total_and_events_beyond_500(client_and_store):
    client, store = client_and_store
    now = datetime.now().replace(microsecond=0)
    _insert_sessions(
        store, "timeline-", 600, now.isoformat(timespec="seconds"),
        profile="妈妈",
    )

    first = client.get(
        "/api/timeline", params={"profile": "妈妈", "limit": 100}
    ).json()
    assert first["total"] == 600
    assert len(first["events"]) == 100

    full = client.get(
        "/api/timeline", params={"profile": "妈妈", "limit": 600}
    ).json()
    assert len(full["events"]) == 600


def test_weekly_report_risk_for_unassigned_profile(client_and_store):
    client, store = client_and_store
    now = datetime.now().replace(microsecond=0)
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at) "
            "VALUES (?, ?, ?, ?)",
            ("unassigned", "未指定会诊", now.isoformat(timespec="seconds"),
             now.isoformat(timespec="seconds")),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata, created_at) "
            "VALUES (?, 'assistant', ?, '{}', ?)",
            (
                "unassigned",
                "风险等级：高危。建议立即就医。",
                now.isoformat(timespec="seconds"),
            ),
        )

    data = client.get("/api/reports/weekly", params={"profile": ""}).json()

    assert data["consultations"]["count"] == 1
    assert data["consultations"]["risk"] == "high"
