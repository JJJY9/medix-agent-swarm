"""家庭血压监测 API 测试。"""
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


def _bp(client, profile, systolic, diastolic, days, context=""):
    return client.post("/api/metrics", json={
        "profile": profile,
        "metric": "bp",
        "value1": systolic,
        "value2": diastolic,
        "measured_at": f"{_date(days)}T08:00",
        "context": context,
    })


def _monitoring(client, profile="妈妈", **params):
    query = dict(params)
    if profile:
        query["profile"] = profile
    response = client.get("/api/health/bp-monitoring", params=query)
    assert response.status_code == 200
    return response.json()


def test_completion_average_and_missing_slots(client_and_store):
    client, _store = client_and_store
    _bp(client, "妈妈", 140, 90, -6, "morning")
    _bp(client, "妈妈", 130, 80, -6, "evening")
    _bp(client, "妈妈", 135, 85, -5, "morning")
    _bp(client, "妈妈", 150, 95, -3, "evening")
    _bp(client, "妈妈", 128, 78, -1)
    _bp(client, "妈妈", 145, 92, -8, "morning")

    data = _monitoring(
        client, start=_date(-6), days=7
    )

    assert data["start"] == _date(-6)
    assert data["end"] == _date(0)
    assert data["expected_slots"] == 14
    assert data["completed_slots"] == 4
    assert data["completion_rate"] == 29
    assert data["days_completed"] == 1
    assert data["readings"] == 5
    assert data["unlabeled"] == 1
    assert data["average"] == {
        "count": 5,
        "avg_systolic": 136.6,
        "avg_diastolic": 85.6,
    }
    assert data["morning"]["count"] == 2
    assert data["morning"]["avg_systolic"] == 137.5
    assert data["evening"]["count"] == 2
    assert data["evening"]["avg_systolic"] == 140.0
    assert data["morning_evening_systolic_diff"] == -2.5

    missing = {
        (item["date"], tuple(item["missing"])) for item in data["missing"]
    }
    assert (_date(-5), ("evening",)) in missing
    assert (_date(-4), ("morning", "evening")) in missing
    assert (_date(-1), ("morning", "evening")) in missing
    assert len(data["daily"]) == 7
    day = next(
        row for row in data["daily"] if row["date"] == _date(-6)
    )
    assert day == {
        "date": _date(-6),
        "morning": True,
        "evening": True,
        "readings": 2,
    }


def test_historical_custom_window_and_partial_future(client_and_store):
    client, _store = client_and_store
    _bp(client, "妈妈", 150, 95, -30, "morning")
    _bp(client, "妈妈", 120, 80, -23, "evening")

    history = _monitoring(client, start=_date(-30), days=7)
    assert history["end"] == _date(-24)
    assert history["planned_end"] == _date(-24)
    assert history["is_future_plan"] is False
    assert history["expected_slots"] == 14
    assert history["readings"] == 1
    assert len(history["daily"]) == 7

    partial = _monitoring(client, start=_date(-2), days=7)
    assert partial["end"] == _date(0)
    assert partial["planned_end"] == _date(4)
    assert partial["is_future_plan"] is True
    assert partial["expected_slots"] == 6
    assert partial["readings"] == 0


def test_same_day_duplicate_measurement_counts_once_per_slot(
    client_and_store,
):
    client, _store = client_and_store
    _bp(client, "妈妈", 140, 90, -1, "morning")
    first = _monitoring(client, start=_date(-1), days=2)
    _bp(client, "妈妈", 150, 95, -1, "morning")
    second = _monitoring(client, start=_date(-1), days=2)

    assert second["completed_slots"] == first["completed_slots"] == 1
    assert second["days_completed"] == first["days_completed"] == 0
    assert second["readings"] == first["readings"] + 1
    assert second["average"]["count"] == first["average"]["count"] + 1


def test_missing_labels_and_unlabeled_daily_counts(client_and_store):
    client, _store = client_and_store
    _bp(client, "妈妈", 140, 90, -6, "morning")
    _bp(client, "妈妈", 128, 78, -1)

    data = _monitoring(client, start=_date(-6), days=7)
    assert len(data["missing"]) == 7
    day = next(
        row for row in data["daily"] if row["date"] == _date(-1)
    )
    assert day["readings"] == 1
    assert day["morning"] is False
    assert day["evening"] is False
    last_missing = next(
        item for item in data["missing"] if item["date"] == _date(-1)
    )
    assert last_missing["labels"] == ["晨起", "睡前"]


def test_rounding_and_raw_difference(client_and_store):
    client, _store = client_and_store
    _bp(client, "妈妈", 120.06, 80.02, 0, "morning")
    _bp(client, "妈妈", 120.04, 80.04, 0, "evening")

    data = _monitoring(client, start=_date(0), days=1)
    assert data["morning"]["avg_systolic"] == 120.1
    assert data["evening"]["avg_systolic"] == 120.0
    assert data["morning_evening_systolic_diff"] == 0.0

    other, _store2 = client_and_store
    _bp(other, "爸爸", 130, 85, -3, "morning")
    rounded = other.get(
        "/api/health/bp-monitoring",
        params={
            "profile": "爸爸", "start": _date(-3), "days": 30,
        },
    ).json()
    assert rounded["expected_slots"] == 8
    assert rounded["completed_slots"] == 1
    assert rounded["completion_rate"] == 13


def test_ninety_day_boundary(client_and_store):
    client, _store = client_and_store
    allowed = client.get(
        "/api/health/bp-monitoring",
        params={"start": _date(-90), "days": 7},
    )
    assert allowed.status_code == 200
    rejected = client.get(
        "/api/health/bp-monitoring",
        params={"start": _date(-91), "days": 7},
    )
    assert rejected.status_code == 400


def test_empty_profile_is_unassigned_only(client_and_store):
    client, _store = client_and_store
    _bp(client, "", 130, 85, 0, "morning")
    _bp(client, "妈妈", 140, 90, 0, "evening")

    unassigned = client.get(
        "/api/health/bp-monitoring", params={"profile": "", "days": 7}
    ).json()
    family = client.get(
        "/api/health/bp-monitoring", params={"days": 7}
    ).json()

    assert unassigned["readings"] == 1
    assert unassigned["morning"]["count"] == 1
    assert unassigned["evening"]["count"] == 0
    assert family["readings"] == 2


def test_empty_window(client_and_store):
    client, _store = client_and_store
    data = _monitoring(client, start=_date(-2), days=3)
    assert data["expected_slots"] == 6
    assert data["completed_slots"] == 0
    assert data["completion_rate"] == 0
    assert data["readings"] == 0
    assert len(data["missing"]) == 3
    assert data["average"]["avg_systolic"] is None


def test_future_plan_only_counts_due_days(client_and_store):
    client, _store = client_and_store
    data = _monitoring(client, start=_date(0), days=7)
    assert data["end"] == _date(0)
    assert data["planned_end"] == _date(6)
    assert data["is_future_plan"] is True
    assert data["expected_slots"] == 2


def test_window_validation_and_clamp(client_and_store):
    client, _store = client_and_store
    invalid = client.get(
        "/api/health/bp-monitoring", params={"start": "2026-13-01"}
    )
    assert invalid.status_code == 400
    future = client.get(
        "/api/health/bp-monitoring", params={"start": _date(1)}
    )
    assert future.status_code == 400
    too_old = client.get(
        "/api/health/bp-monitoring", params={"start": _date(-100)}
    )
    assert too_old.status_code == 400
    clamped = _monitoring(client, days=100)
    assert clamped["days"] == 30
    assert len(clamped["daily"]) == 30


def test_profile_scope(client_and_store):
    client, _store = client_and_store
    _bp(client, "妈妈", 140, 90, 0, "morning")
    _bp(client, "爸爸", 120, 80, 0, "evening")

    mom = _monitoring(client, "妈妈", days=7)
    dad = _monitoring(client, "爸爸", days=7)
    family = _monitoring(client, "", days=7)

    assert mom["readings"] == 1
    assert mom["morning"]["count"] == 1
    assert dad["readings"] == 1
    assert dad["evening"]["count"] == 1
    assert family["readings"] == 2
