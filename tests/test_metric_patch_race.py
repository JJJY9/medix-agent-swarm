"""健康指标 PATCH 的字段级更新与并发安全。"""
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


def test_partial_patch_keeps_other_metric_fields(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": "weight",
        "value1": 60.5,
        "measured_at": "2026-11-01T08:00",
        "note": "晨起",
    }).json()["metric"]

    response = client.patch(
        f"/api/metrics/{created['id']}", json={"value1": 61.2}
    )
    assert response.status_code == 200
    item = response.json()["metric"]
    assert item["value1"] == 61.2
    assert item["profile"] == "妈妈"
    assert item["measured_at"] == "2026-11-01T08:00"
    assert item["note"] == "晨起"


def test_concurrent_metric_updates_do_not_overwrite(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    barrier = threading.Barrier(2)
    errors = []

    try:
        metric = store.add_metric(
            "妈妈", "weight", 60, measured_at="2026-11-01T08:00",
        )

        def update_note():
            barrier.wait()
            try:
                store.update_metric(metric["id"], note="晚饭后")
            except Exception as error:  # pragma: no cover
                errors.append(error)

        def update_value():
            barrier.wait()
            try:
                store.update_metric(metric["id"], value1=61.5)
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [
            threading.Thread(target=update_note),
            threading.Thread(target=update_value),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        final = store.get_metric(metric["id"])
    finally:
        store.close()

    assert not errors
    assert final["note"] == "晚饭后"
    assert final["value1"] == 61.5


def test_patch_missing_metric_returns_404(client_and_store):
    client, _store = client_and_store
    response = client.patch("/api/metrics/99999", json={"note": "x"})
    assert response.status_code == 404
