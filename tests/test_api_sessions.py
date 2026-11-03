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
        "loaded": 2,
        "offset": 0,
        "limit": 50,
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


def test_sessions_api_paginates_with_global_summary(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    for index, tokens in enumerate((10, 20, 30)):
        store.save_exchange(
            f"s{index}", f"问题{index}", "答复",
            metadata={
                "usage": {
                    "prompt_tokens": tokens,
                    "completion_tokens": 0,
                    "total_tokens": tokens,
                },
                "llm_calls": index + 1,
            },
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (f"2026-01-01T00:00:0{index}", f"s{index}"),
            )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        first = client.get("/api/sessions", params={"limit": 2, "offset": 0}).json()
        second = client.get("/api/sessions", params={"limit": 2, "offset": 2}).json()
    finally:
        store.close()

    assert [item["id"] for item in first["sessions"]] == ["s2", "s1"]
    assert first["summary"]["sessions"] == 3
    assert first["summary"]["loaded"] == 2
    assert first["summary"]["offset"] == 0
    assert first["summary"]["limit"] == 2
    assert first["summary"]["turns"] == 3
    assert first["summary"]["llm_calls"] == 6
    assert first["summary"]["usage"]["total_tokens"] == 60

    assert [item["id"] for item in second["sessions"]] == ["s0"]
    assert second["summary"]["loaded"] == 1
    assert second["summary"]["offset"] == 2
    # 汇总覆盖全部匹配会话，而不是当前页
    assert second["summary"]["sessions"] == 3
    assert second["summary"]["usage"]["total_tokens"] == 60
