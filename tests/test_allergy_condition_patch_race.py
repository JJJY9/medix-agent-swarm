"""过敏史与慢病 PATCH 的字段级更新。"""
import threading

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


def test_partial_allergy_patch_keeps_other_fields(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/allergies", json={
        "profile": "妈妈",
        "substance": "青霉素",
        "reaction": "皮疹",
        "severity": "mild",
        "kind": "drug",
    }).json()["allergy"]

    response = client.patch(
        f"/api/allergies/{created['id']}", json={"severity": "severe"}
    )
    assert response.status_code == 200
    item = response.json()["allergy"]
    assert item["severity"] == "severe"
    assert item["substance"] == "青霉素"
    assert item["reaction"] == "皮疹"
    assert item["profile"] == "妈妈"


def test_partial_condition_patch_keeps_other_fields(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/conditions", json={
        "profile": "奶奶",
        "name": "高血压",
        "icd10": "I10",
        "status": "active",
        "severity": "moderate",
        "note": "定期复查",
    }).json()["condition"]

    response = client.patch(
        f"/api/conditions/{created['id']}", json={"status": "controlled"}
    )
    assert response.status_code == 200
    item = response.json()["condition"]
    assert item["status"] == "controlled"
    assert item["name"] == "高血压"
    assert item["icd10"] == "I10"
    assert item["note"] == "定期复查"


def test_concurrent_allergy_updates_do_not_overwrite(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    barrier = threading.Barrier(2)
    errors = []

    try:
        allergy = store.add_allergy(
            "妈妈", "花生", "皮疹", "mild", "food",
        )

        def update_note():
            barrier.wait()
            try:
                store.update_allergy(allergy["id"], note="外出就餐时注意")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        def update_severity():
            barrier.wait()
            try:
                store.update_allergy(allergy["id"], severity="severe")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [
            threading.Thread(target=update_note),
            threading.Thread(target=update_severity),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        final = store.get_allergy(allergy["id"])
    finally:
        store.close()

    assert not errors
    assert final["note"] == "外出就餐时注意"
    assert final["severity"] == "severe"


def test_patch_missing_records_return_404(client_and_store):
    client, _store = client_and_store
    assert client.patch(
        "/api/allergies/99999", json={"note": "x"}
    ).status_code == 404
    assert client.patch(
        "/api/conditions/99999", json={"note": "x"}
    ).status_code == 404
