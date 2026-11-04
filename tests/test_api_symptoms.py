"""症状日记 API 的离线测试。"""
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


def test_symptoms_api_roundtrip(client_and_store):
    client, _store = client_and_store

    created = client.post(
        "/api/symptoms",
        json={
            "profile": "母亲",
            "symptom": "头晕",
            "severity": 6,
            "recorded_at": "2026-11-04T08:00",
            "note": "起床后",
        },
    )
    listed = client.get("/api/symptoms", params={"profile": "母亲"})

    assert created.status_code == 200
    item = created.json()["symptom"]
    assert item["severity"] == 6
    assert item["note"] == "起床后"
    assert [row["id"] for row in listed.json()["symptoms"]] == [item["id"]]

    updated = client.patch(
        f"/api/symptoms/{item['id']}",
        json={
            "profile": "母亲",
            "symptom": "头晕",
            "severity": 4,
            "recorded_at": "2026-11-05T08:00",
            "note": "",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["symptom"]["severity"] == 4

    deleted = client.delete(f"/api/symptoms/{item['id']}")
    missing = client.delete("/api/symptoms/99999")
    audit = client.get("/api/audit")
    actions = [row["action"] for row in audit.json()["actions"]]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "symptom.create" in actions
    assert "symptom.update" in actions
    assert "symptom.delete" in actions


def test_symptoms_api_validation(client_and_store):
    client, _store = client_and_store

    invalid_date = client.post(
        "/api/symptoms",
        json={"symptom": "头晕", "severity": 6, "recorded_at": "bad"},
    )
    invalid_severity = client.post(
        "/api/symptoms", json={"symptom": "头晕", "severity": 11}
    )
    blank = client.post(
        "/api/symptoms", json={"symptom": "   ", "severity": 6}
    )

    assert invalid_date.status_code == 400
    assert invalid_severity.status_code == 422
    assert blank.status_code == 400


def test_symptoms_import_backup(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "symptoms": [
                {
                    "profile": "孩子",
                    "symptom": "咳嗽",
                    "severity": 3,
                    "recorded_at": "2026-11-04T20:00",
                }
            ]
        },
    )
    listed = client.get("/api/symptoms")

    assert response.status_code == 200
    assert response.json()["symptoms"] == 1
    assert listed.json()["symptoms"][0]["symptom"] == "咳嗽"
