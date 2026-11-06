"""用药新增/修改时的安全提示测试。"""
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


def test_new_medication_checks_structured_allergy(client_and_store):
    client, _store = client_and_store
    client.post("/api/allergies", json={
        "profile": "妈妈",
        "substance": "青霉素",
        "severity": "severe",
        "kind": "drug",
    })
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿莫西林",
    })
    assert response.status_code == 200
    safety = response.json()["safety"]
    assert safety["level"] == "warning"
    assert any("过敏" in row.get("title", "") for row in safety["alerts"])


def test_new_medication_checks_active_interactions(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林",
    })
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿司匹林",
    })
    assert response.status_code == 200
    safety = response.json()["safety"]
    assert safety["level"] == "warning"
    assert safety["alerts"]


def test_medication_without_risk_has_no_alerts(client_and_store):
    client, _store = client_and_store
    response = client.post("/api/medications", json={
        "profile": "妈妈", "name": "维生素 D",
    })
    assert response.status_code == 200
    assert response.json()["safety"]["alerts"] == []


def test_update_medication_rechecks_safety(client_and_store):
    client, _store = client_and_store
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "磺胺", "kind": "drug",
    })
    item = client.post("/api/medications", json={
        "profile": "妈妈", "name": "维生素 D",
    }).json()["medication"]
    response = client.patch(
        f"/api/medications/{item['id']}", json={"name": "复方新诺明"}
    )
    assert response.status_code == 200
    assert response.json()["safety"]["alerts"]
