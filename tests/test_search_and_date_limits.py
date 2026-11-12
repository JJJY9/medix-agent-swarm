"""搜索词长度与极端日期的边界回归测试。"""
from datetime import datetime

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


def test_timeline_accepts_far_future_date(client_and_store):
    """9999-12-31 是合法日期，加一天溢出时不能返回 500。"""
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 60,
        "measured_at": f"{today}T08:00",
    })

    response = client.get(
        "/api/timeline", params={"date": "9999-12-31"}
    )

    assert response.status_code == 200
    assert response.json()["events"] == []


def test_long_session_query_matches_exactly(client_and_store):
    """80 字以上的正常搜索词不能被截断成前缀而误命中其它会话。"""
    client, store = client_and_store
    prefix = "血压" * 40
    store.save_exchange("sess-a", "问题", prefix + "甲")
    store.save_exchange("sess-b", "问题", prefix + "乙")

    data = client.get(
        "/api/sessions", params={"q": prefix + "甲"}
    ).json()

    assert [item["id"] for item in data["sessions"]] == ["sess-a"]


def test_overlong_search_query_does_not_crash_lists(client_and_store):
    """超长搜索词会超出 SQLite LIKE 模式复杂度，但接口不应 500。"""
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 60,
        "note": "晨起空腹",
        "measured_at": f"{today}T08:00",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素", "reaction": "皮疹",
    })
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": today, "diagnosis": "高血压",
    })
    # 超过 SQLite LIKE 模式上限（约 5 万字符）才真正覆盖截断修复
    long_query = "a" * 60000

    for path in ("/api/metrics", "/api/allergies", "/api/visits"):
        response = client.get(path, params={"q": long_query})
        assert response.status_code == 200, path


def test_search_query_still_escapes_like_wildcards(client_and_store):
    """搜索词中的 % / _ 按字面匹配，不能被当成通配符。"""
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "磺胺", "reaction": "皮疹 50%",
    })
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素", "reaction": "皮疹 50 分钟",
    })

    literal = client.get(
        "/api/allergies", params={"q": "50%"}
    ).json()["allergies"]
    assert len(literal) == 1
    assert literal[0]["substance"] == "磺胺"

    wildcard = client.get(
        "/api/allergies", params={"q": "%"}
    ).json()["allergies"]
    assert [item["substance"] for item in wildcard] == ["磺胺"]
