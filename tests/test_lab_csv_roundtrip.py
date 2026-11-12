"""化验 CSV 往返保留同一时间的多条不同结果。"""
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


def test_lab_csv_roundtrip_keeps_same_time_distinct_values(
    client_and_store,
):
    client, _store = client_and_store
    created = []
    for value in (55, 60):
        response = client.post("/api/lab-results", json={
            "profile": "妈妈",
            "item": "ALT",
            "value": value,
            "unit": "U/L",
            "ref_low": 7,
            "ref_high": 40,
            "tested_at": "2026-11-01T08:00",
        })
        assert response.status_code == 200
        created.append(response.json()["lab_result"]["id"])

    exported = client.get("/api/lab-results/export.csv")
    assert exported.status_code == 200

    for lab_id in created:
        assert client.delete(f"/api/lab-results/{lab_id}").status_code == 200

    imported = client.post(
        "/api/lab-results/import",
        files={
            "file": ("labs.csv", exported.content, "text/csv"),
        },
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 2

    rows = client.get("/api/lab-results").json()["lab_results"]
    assert {round(float(row["value"])) for row in rows} == {55, 60}

    again = client.post(
        "/api/lab-results/import",
        files={
            "file": ("labs.csv", exported.content, "text/csv"),
        },
    )
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 2
