"""用药 PATCH 的字段级更新与并发安全。"""
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


def test_partial_patch_keeps_unmentioned_fields(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "二甲双胍",
        "dose": "500 mg",
        "frequency": "每日两次",
        "note": "原备注",
    }).json()["medication"]

    response = client.patch(
        f"/api/medications/{created['id']}", json={"note": "饭后"}
    )
    assert response.status_code == 200
    item = response.json()["medication"]
    assert item["note"] == "饭后"
    assert item["dose"] == "500 mg"
    assert item["frequency"] == "每日两次"


def test_concurrent_partial_updates_do_not_overwrite(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    barrier = threading.Barrier(2)
    errors = []

    try:
        medication = store.add_medication(
            "妈妈", "氨氯地平", "5 mg", "每日一次",
        )

        def update_note():
            barrier.wait()
            try:
                store.update_medication(medication["id"], note="饭后服用")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        def update_dose():
            barrier.wait()
            try:
                store.update_medication(medication["id"], dose="10 mg")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [
            threading.Thread(target=update_note),
            threading.Thread(target=update_dose),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        final = store.get_medication(medication["id"])
    finally:
        store.close()

    assert not errors
    assert final["note"] == "饭后服用"
    assert final["dose"] == "10 mg"


def test_patch_missing_medication_returns_404(client_and_store):
    client, _store = client_and_store
    response = client.patch("/api/medications/99999", json={"note": "x"})
    assert response.status_code == 404
