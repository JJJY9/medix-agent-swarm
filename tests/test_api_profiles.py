"""常用患者档案 API 的离线测试。"""
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


def test_profiles_api_roundtrip(client):
    empty = client.get("/api/profiles")
    created = client.post(
        "/api/profiles",
        json={"name": "妈妈", "context": {"age": "58", "sex": "女"}},
    )
    listed = client.get("/api/profiles")
    updated = client.post(
        "/api/profiles",
        json={
            "name": "妈妈",
            "context": {"age": "59", "allergies": "青霉素"},
        },
    )
    deleted = client.delete(f"/api/profiles/{quote('妈妈')}")
    after_delete = client.get("/api/profiles")
    missing = client.delete(f"/api/profiles/{quote('不存在')}")

    assert empty.status_code == 200
    assert empty.json()["profiles"] == []

    assert created.status_code == 200
    assert created.json()["profile"]["name"] == "妈妈"
    assert created.json()["profile"]["context"]["age"] == "58"

    profiles = listed.json()["profiles"]
    assert [profile["name"] for profile in profiles] == ["妈妈"]

    assert updated.status_code == 200
    assert updated.json()["profile"]["context"] == {
        "age": "59",
        "allergies": "青霉素",
    }

    assert deleted.status_code == 200
    assert after_delete.json()["profiles"] == []
    assert missing.status_code == 404


def test_profiles_api_validates_payload(client):
    empty_context = client.post(
        "/api/profiles",
        json={"name": "空档案", "context": {}},
    )
    too_long = client.post(
        "/api/profiles",
        json={"name": "x" * 41, "context": {"age": "30"}},
    )
    unknown_fields = client.post(
        "/api/profiles",
        json={"name": "未知字段", "context": {"unexpected": "value"}},
    )

    assert empty_context.status_code == 400
    assert too_long.status_code == 422
    assert unknown_fields.status_code == 400
