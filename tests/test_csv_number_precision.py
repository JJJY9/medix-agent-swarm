"""CSV 浮点导出应保留可往返的精度。"""
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


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1234567.89, "1234567.89"),
        (0.000123456789, "0.000123456789"),
        (55.0, "55"),
        (0.1, "0.1"),
    ],
)
def test_csv_number_preserves_precision(value, text):
    assert api_server.csv_number(value) == text


def test_csv_number_rejects_invalid_and_non_finite():
    assert api_server.csv_number("bad") == ""
    assert api_server.csv_number(float("nan")) == ""
    assert api_server.csv_number(float("inf")) == ""


def test_lab_csv_roundtrip_preserves_high_precision_value(client_and_store):
    client, store = client_and_store
    value = 1234567.89
    created = client.post("/api/lab-results", json={
        "profile": "妈妈",
        "item": "ALT",
        "value": value,
        "unit": "U/L",
        "tested_at": "2026-11-01T08:00",
    }).json()["lab_result"]

    exported = client.get("/api/lab-results/export.csv")
    assert b"1234567.89" in exported.content
    client.delete(f"/api/lab-results/{created['id']}")

    imported = client.post(
        "/api/lab-results/import",
        files={"file": ("labs.csv", exported.content, "text/csv")},
    )
    assert imported.status_code == 200
    rows = store.list_lab_results(profile="妈妈")
    assert rows and rows[0]["value"] == value
