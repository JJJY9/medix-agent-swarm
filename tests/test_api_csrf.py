"""跨站写请求防护的离线测试。"""
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


def _create_reminder(client):
    response = client.post(
        "/api/reminders", json={"title": "复查", "due_at": "2026-11-10"}
    )
    assert response.status_code == 200
    return response.json()["reminder"]["id"]


def test_cross_site_post_is_rejected(client_and_store):
    client, store = client_and_store
    reminder_id = _create_reminder(client)

    response = client.post(
        f"/api/reminders/{reminder_id}/complete",
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_rejected"
    assert store.get_reminder(reminder_id)["done"] is False


def test_cross_site_fetch_metadata_is_rejected(client_and_store):
    client, _store = client_and_store
    reminder_id = _create_reminder(client)

    response = client.post(
        f"/api/reminders/{reminder_id}/complete",
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert response.status_code == 403


def test_same_origin_and_script_clients_are_allowed(client_and_store):
    client, store = client_and_store
    reminder_id = _create_reminder(client)

    same_origin = client.post(
        f"/api/reminders/{reminder_id}/complete",
        headers={
            "Origin": "http://testserver",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert same_origin.status_code == 200
    assert store.get_reminder(reminder_id)["done"] is True

    reopened = client.post(f"/api/reminders/{reminder_id}/reopen")
    assert reopened.status_code == 200
    assert store.get_reminder(reminder_id)["done"] is False


def test_cross_site_get_is_rejected(client_and_store):
    client, _store = client_and_store

    response = client.get(
        "/api/reminders", headers={"Origin": "https://evil.example"}
    )

    assert response.status_code == 403
    assert response.json()["code"] == "csrf_rejected"


def test_untrusted_host_header_is_rejected(client_and_store):
    client, _store = client_and_store

    response = client.get(
        "/api/status", headers={"Host": "evil.example"}
    )

    assert response.status_code == 421
    assert response.json()["code"] == "host_rejected"


def test_ip_and_allowed_hostnames_are_accepted(
    client_and_store, monkeypatch
):
    client, _store = client_and_store
    for host in (
        "127.0.0.1:8000", "192.168.1.20:8000",
        "[::1]:8000", "localhost:8000", "testserver",
    ):
        response = client.get("/api/status", headers={"Host": host})
        assert response.status_code == 200, host

    blocked = client.get(
        "/api/status", headers={"Host": "medix.local"}
    )
    assert blocked.status_code == 421

    monkeypatch.setenv("MEDIX_ALLOWED_HOSTS", "medix.local")
    allowed = client.get(
        "/api/status", headers={"Host": "medix.local"}
    )
    assert allowed.status_code == 200
