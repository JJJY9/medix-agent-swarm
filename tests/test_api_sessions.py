"""会话列表用量汇总 API 的离线测试。"""
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


def test_sessions_api_includes_usage_summary(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "第一问", "第一答",
        metadata={
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            "llm_calls": 2,
        },
    )
    store.save_exchange(
        "s1", "第二问", "第二答",
        metadata={
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25},
            "llm_calls": 3,
        },
    )
    store.save_exchange("s2", "另一个问题", "另一个答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions")
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    sessions = {item["id"]: item for item in data["sessions"]}

    assert sessions["s1"]["usage"]["total_tokens"] == 40
    assert sessions["s1"]["llm_calls"] == 5
    assert sessions["s1"]["turns"] == 2
    assert sessions["s2"]["usage"]["total_tokens"] == 0
    assert sessions["s2"]["llm_calls"] == 0
    assert data["summary"] == {
        "sessions": 2,
        "turns": 3,
        "llm_calls": 5,
        "usage": {
            "prompt_tokens": 30,
            "completion_tokens": 10,
            "total_tokens": 40,
        },
    }


def test_sessions_api_summary_respects_search(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "糖尿病怎么控制饮食？", "控制碳水。",
        metadata={
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            "llm_calls": 1,
        },
    )
    store.save_exchange(
        "s2", "高血压怎么运动？", "适量有氧。",
        metadata={
            "usage": {"prompt_tokens": 30, "completion_tokens": 9, "total_tokens": 39},
            "llm_calls": 4,
        },
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions", params={"q": "糖尿病"})
    finally:
        store.close()

    data = response.json()
    assert [item["id"] for item in data["sessions"]] == ["s1"]
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["usage"]["total_tokens"] == 10
    assert data["summary"]["llm_calls"] == 1
