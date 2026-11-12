"""Excel/WPS 风格日期在导入与写入路径上的兼容性测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import (
    SessionStore,
    _canonical_local_time,
    _normalize_datetime_text,
)


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026/1/1", "2026-01-01"),
        ("2026-1-1", "2026-01-01"),
        ("2026.01.01", "2026-01-01"),
        ("2026年1月1日", "2026-01-01"),
        ("2026/1/1 8:05", "2026-01-01T08:05"),
        ("2026年1月1日 8:05:09", "2026-01-01T08:05:09"),
        ("2026/1/1T8:05", "2026-01-01T08:05"),
        ("2026-01-01", "2026-01-01"),
        ("2026-01-01T08:05", "2026-01-01T08:05"),
    ],
)
def test_normalize_datetime_text_variants(raw, expected):
    assert _normalize_datetime_text(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026/1/1", "2026-01-01T00:00"),
        ("2026/1/1 8:05", "2026-01-01T08:05"),
        ("2026年1月1日 8:05", "2026-01-01T08:05"),
        ("2026-1-1", "2026-01-01T00:00"),
    ],
)
def test_canonical_local_time_accepts_excel_dates(raw, expected):
    assert _canonical_local_time(raw) == expected


@pytest.mark.parametrize(
    "raw",
    ["2026/2/30", "2026/13/1", "2026/1/1 25:00", "not-a-date", ""],
)
def test_canonical_local_time_rejects_invalid_dates(raw):
    assert _canonical_local_time(raw) is None


def test_metric_write_accepts_excel_date(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "weight",
            "value1": 55.5,
            "measured_at": "2026/1/1 8:05",
        },
    )
    assert created.status_code == 200
    assert created.json()["metric"]["measured_at"] == "2026-01-01T08:05"


def test_reminder_write_accepts_excel_date(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={
            "title": "复查血压",
            "due_at": "2026/12/1 9:00",
        },
    )
    assert created.status_code == 200
    assert created.json()["reminder"]["due_at"] == "2026-12-01T09:00"


def test_reminder_write_rejects_invalid_excel_date(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/reminders",
        json={"title": "复查血压", "due_at": "2026/2/30"},
    )
    assert created.status_code == 400
