"""患者档案筛选在各历史面板的一致性的离线测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "妈妈的头痛", "答复一",
        metadata={
            "usage": {"total_tokens": 10},
            "llm_calls": 1,
            "triage": {"level": "emergency", "matches": [], "message": ""},
            "feedback": {"rating": -1, "comment": "需要补充"},
        },
    )
    store.ensure_session("s1", "", "母亲")
    store.set_tags("s1", ["复诊"])
    store.save_exchange(
        "s2", "孩子发烧", "答复二",
        metadata={
            "usage": {"total_tokens": 20},
            "llm_calls": 2,
            "feedback": {"rating": 1},
        },
    )
    store.ensure_session("s2", "", "孩子")
    store.set_tags("s2", ["发烧"])
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_session_profiles_endpoint(client_and_store):
    client, _store = client_and_store

    data = client.get("/api/session-profiles").json()

    counts = {item["name"]: item["count"] for item in data["profiles"]}
    assert counts == {"母亲": 1, "孩子": 1}


def test_messages_endpoint_returns_profile(client_and_store):
    client, _store = client_and_store

    data = client.get("/api/sessions/s1/messages").json()

    assert data["profile"] == "母亲"
    assert len(data["messages"]) == 2


def test_history_panels_respect_profile(client_and_store):
    client, _store = client_and_store

    tags = client.get("/api/tags", params={"profile": "母亲"}).json()["tags"]
    feedback = client.get(
        "/api/feedback/recent",
        params={"rating": -1, "profile": "母亲"},
    ).json()["feedback"]
    quality = client.get(
        "/api/insights/quality", params={"profile": "母亲"}
    ).json()
    other_quality = client.get(
        "/api/insights/quality", params={"profile": "孩子"}
    ).json()
    daily = client.get(
        "/api/usage/daily", params={"days": 7, "profile": "孩子"}
    ).json()

    assert [item["name"] for item in tags] == ["复诊"]
    assert [item["session_id"] for item in feedback] == ["s1"]
    assert quality["sessions"] == 1
    assert quality["emergency"] == 1
    assert other_quality["sessions"] == 1
    assert other_quality["emergency"] == 0
    total = sum(day["usage"]["total_tokens"] for day in daily["days"])
    assert total == 20


def test_feedback_csv_respects_profile(client_and_store):
    client, _store = client_and_store

    response = client.get(
        "/api/feedback/export.csv",
        params={"rating": -1, "profile": "母亲"},
    )
    text = response.content.decode("utf-8-sig")

    assert response.status_code == 200
    assert "s1" in text
    assert "s2" not in text


def test_profile_names_include_all_sources(client_and_store):
    client, store = client_and_store
    store.save_profile("护士", {"age": "30"})
    store.add_reminder("复查", "2026-11-10", profile="外婆")
    store.add_metric(
        "外公", "weight", 70, measured_at="2026-11-01T08:00"
    )

    data = client.get("/api/profile-names").json()

    assert set(data["profiles"]) >= {
        "母亲", "孩子", "护士", "外婆", "外公",
    }
