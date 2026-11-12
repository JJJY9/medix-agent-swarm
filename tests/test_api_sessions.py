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
        "feedback": {
            "total": 0,
            "helpful": 0,
            "unhelpful": 0,
            "rate": None,
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
        content_response = client.get("/api/sessions", params={"q": "碳水"})
    finally:
        store.close()

    data = response.json()
    assert [item["id"] for item in data["sessions"]] == ["s1"]
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["usage"]["total_tokens"] == 10
    assert data["summary"]["llm_calls"] == 1

    # 搜索也能命中答复正文
    content_data = content_response.json()
    assert [item["id"] for item in content_data["sessions"]] == ["s1"]
    assert "碳水" in content_data["sessions"][0]["match_snippet"]
    assert content_data["summary"]["usage"]["total_tokens"] == 10


def test_favorite_api_and_filter(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "收藏问题", "收藏答复",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "普通问题", "普通答复",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        patched = client.patch(
            "/api/sessions/s1/favorite", json={"favorite": True}
        )
        listed = client.get("/api/sessions", params={"favorite": "true"})
        daily = client.get(
            "/api/usage/daily", params={"days": 1, "favorite": "true"}
        )
        missing = client.patch(
            "/api/sessions/missing/favorite", json={"favorite": True}
        )
    finally:
        store.close()

    assert patched.status_code == 200
    assert patched.json()["favorite"] is True
    assert missing.status_code == 404

    data = listed.json()
    assert [item["id"] for item in data["sessions"]] == ["s1"]
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["usage"]["total_tokens"] == 10

    usage = daily.json()
    assert usage["totals"]["usage"]["total_tokens"] == 10


def test_bulk_tags_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    for session_id in ("s1", "s2"):
        store.save_exchange(session_id, f"问题 {session_id}", "答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.patch(
            "/api/bulk/sessions/tags",
            json={
                "ids": ["s1", "s2", "missing"],
                "tags": [" 高血压 ", "高血压", "复诊"],
            },
        )
        listed = client.get("/api/sessions")
        tags = client.get("/api/tags")
    finally:
        store.close()

    assert response.status_code == 200
    assert response.json()["updated"] == 2
    assert response.json()["missing"] == ["missing"]
    assert response.json()["tags"] == ["高血压", "复诊"]

    assert all(
        item["tags"] == ["高血压", "复诊"]
        for item in listed.json()["sessions"]
    )
    counts = {item["name"]: item["count"] for item in tags.json()["tags"]}
    assert counts == {"高血压": 2, "复诊": 2}


def test_bulk_archive_and_delete(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    for session_id in ("s1", "s2", "s3"):
        store.save_exchange(session_id, f"问题 {session_id}", "答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        archived = client.patch(
            "/api/bulk/sessions/archive",
            json={"ids": ["s1", "s2", "missing"], "archived": True},
        )
        archived_list = client.get(
            "/api/sessions", params={"archived": "true"}
        )
        deleted = client.post(
            "/api/bulk/sessions/delete",
            json={"ids": ["s1", "missing"]},
        )
        remaining = client.get("/api/sessions")
        empty = client.post(
            "/api/bulk/sessions/delete", json={"ids": []}
        )
    finally:
        store.close()

    assert archived.status_code == 200
    assert archived.json()["updated"] == 2
    assert archived.json()["missing"] == ["missing"]
    assert {item["id"] for item in archived_list.json()["sessions"]} == {
        "s1", "s2",
    }

    assert deleted.status_code == 200
    assert deleted.json()["deleted"] == 1
    assert [item["id"] for item in remaining.json()["sessions"]] == ["s3"]
    assert empty.status_code == 422


def test_tags_api_and_filter(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "高血压复诊", "答复一",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "糖尿病饮食", "答复二",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        updated = client.patch(
            "/api/sessions/s1/tags",
            json={"tags": [" 高血压 ", "高血压", "复诊"]},
        )
        tags = client.get("/api/tags")
        filtered = client.get("/api/sessions", params={"tag": "高血压"})
        missing = client.patch(
            "/api/sessions/missing/tags", json={"tags": ["x"]}
        )
    finally:
        store.close()

    assert updated.status_code == 200
    assert updated.json()["tags"] == ["高血压", "复诊"]

    tag_names = {item["name"]: item["count"] for item in tags.json()["tags"]}
    assert tag_names == {"高血压": 1, "复诊": 1}

    data = filtered.json()
    assert [item["id"] for item in data["sessions"]] == ["s1"]
    assert data["sessions"][0]["tags"] == ["高血压", "复诊"]
    assert data["summary"]["sessions"] == 1
    assert data["summary"]["usage"]["total_tokens"] == 10
    assert missing.status_code == 404


def test_risk_filter_summary_includes_feedback(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "risk.db"))
    assistant_id = store.save_exchange(
        "s1", "胸痛", "风险评估：高危，请立即就医。",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.set_message_feedback("s1", assistant_id, 1)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        data = client.get(
            "/api/sessions", params={"risk": "high"}
        ).json()
    finally:
        store.close()

    assert data["summary"]["feedback"] == {
        "total": 1,
        "helpful": 1,
        "unhelpful": 0,
        "rate": 100.0,
    }


def test_feedback_api_roundtrip(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    assistant_id = store.save_exchange(
        "s1", "最近头痛", "建议先测量血压并充分休息。",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        created = client.post(
            "/api/sessions/s1/feedback",
            json={"message_id": assistant_id, "rating": 1},
        )
        messages = client.get("/api/sessions/s1/messages")
        summary = client.get("/api/sessions").json()["summary"]
        changed = client.post(
            "/api/sessions/s1/feedback",
            json={
                "message_id": assistant_id,
                "rating": -1,
                "comment": "依据不足",
            },
        )
        cleared = client.post(
            "/api/sessions/s1/feedback",
            json={"message_id": assistant_id, "rating": 0},
        )
        after_clear = client.get("/api/sessions/s1/messages").json()
        missing = client.post(
            "/api/sessions/s1/feedback",
            json={"message_id": 999999, "rating": 1},
        )
        invalid = client.post(
            "/api/sessions/s1/feedback",
            json={"message_id": assistant_id, "rating": 5},
        )
    finally:
        store.close()

    assert created.status_code == 200
    assert created.json()["rating"] == 1

    assistant = messages.json()["messages"][1]
    assert assistant["id"] == assistant_id
    assert assistant["feedback"]["rating"] == 1

    assert summary["feedback"] == {
        "total": 1,
        "helpful": 1,
        "unhelpful": 0,
        "rate": 100.0,
    }

    assert changed.status_code == 200
    assert changed.json()["comment"] == "依据不足"
    assert cleared.status_code == 200
    assert after_clear["messages"][1]["feedback"] is None
    assert missing.status_code == 404
    assert invalid.status_code == 422


def test_audit_csv_export(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.log_action("session.delete", "s1", "用户删除")
    store.log_action("session.import", detail="imported=2")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/audit/export.csv")
    finally:
        store.close()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    assert text.startswith("时间")
    assert "session.delete" in text
    assert "s1" in text
    assert "用户删除" in text
    # 按时间从早到晚导出
    assert text.index("session.delete") < text.index("session.import")


def test_audit_log_api_records_session_actions(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("s1", "删除问题", "答复一")
    store.save_exchange("s2", "归档问题", "答复二")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        deleted = client.delete("/api/sessions/s1")
        archived = client.patch(
            "/api/sessions/s2/archive", json={"archived": True}
        )
        audit = client.get("/api/audit")
    finally:
        store.close()

    assert deleted.status_code == 200
    assert archived.status_code == 200
    actions = audit.json()["actions"]
    assert [item["action"] for item in actions] == [
        "session.archive", "session.delete",
    ]
    assert actions[0]["target"] == "s2"
    assert actions[1]["target"] == "s1"


def test_auto_archive_sessions_archives_old(monkeypatch, tmp_path):
    import asyncio
    from datetime import datetime, timedelta

    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("old", "旧问题", "旧答复")
    store.save_exchange("new", "新问题", "新答复")
    old_time = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = 'old'",
            (old_time,),
        )

    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", "30")
    try:
        asyncio.run(api_server.auto_archive_sessions())
        archived = store.list_sessions(archived=True)
        active = store.list_sessions(archived=False)
    finally:
        store.close()

    assert [item["id"] for item in archived] == ["old"]
    assert [item["id"] for item in active] == ["new"]


def test_quality_insights_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "胸痛", "答复",
        metadata={
            "triage": {
                "level": "emergency",
                "matches": ["胸痛"],
                "message": "",
            },
            "medication_safety": {
                "level": "warning",
                "alerts": [{"title": "示例提示", "detail": "示例"}],
            },
            "feedback": {"rating": 1},
            "total_time": 2.0,
            "usage": {"total_tokens": 10},
        },
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/insights/quality", params={"days": 30})
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    assert data["days"] == 30
    assert data["sessions"] == 1
    assert data["emergency"] == 1
    assert data["medication_alerts"] == 1
    assert data["rate"] == 100.0
    assert data["top_risks"] == [{"label": "胸痛", "count": 1}]


def test_feedback_csv_export(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    first = store.save_exchange("s1", "问题一", "答复一")
    second = store.save_exchange("s2", "问题二", "答复二")
    store.set_message_feedback("s1", first, -1, "依据不足")
    store.set_message_feedback("s2", second, 1)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        unhelpful = client.get("/api/feedback/export.csv")
        helpful = client.get(
            "/api/feedback/export.csv", params={"rating": 1}
        )
        invalid = client.get(
            "/api/feedback/export.csv", params={"rating": 5}
        )
    finally:
        store.close()

    assert unhelpful.status_code == 200
    text = unhelpful.content.decode("utf-8-sig")
    assert text.startswith("更新时间")
    assert "待改进" in text
    assert "依据不足" in text
    assert "s2" not in text
    assert helpful.content.decode("utf-8-sig").count("有用") == 1
    assert invalid.status_code == 400


def test_recent_feedback_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    first = store.save_exchange("s1", "问题一", "答复一")
    second = store.save_exchange("s2", "问题二", "答复二")
    store.set_message_feedback("s1", first, -1, "依据不足")
    store.set_message_feedback("s2", second, 1)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        unhelpful = client.get("/api/feedback/recent")
        helpful = client.get("/api/feedback/recent", params={"rating": 1})
        invalid = client.get("/api/feedback/recent", params={"rating": 5})
    finally:
        store.close()

    assert unhelpful.status_code == 200
    items = unhelpful.json()["feedback"]
    assert [item["session_id"] for item in items] == ["s1"]
    assert items[0]["comment"] == "依据不足"
    assert items[0]["message_id"] == first

    assert [item["session_id"] for item in helpful.json()["feedback"]] == ["s2"]
    assert invalid.status_code == 400


def test_archive_api_and_filter(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "归档问题", "归档答复",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "普通问题", "普通答复",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        patched = client.patch(
            "/api/sessions/s1/archive", json={"archived": True}
        )
        active = client.get("/api/sessions")
        archived = client.get("/api/sessions", params={"archived": "true"})
        unarchived = client.patch(
            "/api/sessions/s1/archive", json={"archived": False}
        )
        missing = client.patch(
            "/api/sessions/missing/archive", json={"archived": True}
        )
    finally:
        store.close()

    assert patched.status_code == 200
    assert patched.json()["archived"] is True

    active_data = active.json()
    assert [item["id"] for item in active_data["sessions"]] == ["s2"]
    assert active_data["summary"]["sessions"] == 1
    assert active_data["summary"]["usage"]["total_tokens"] == 20

    archived_data = archived.json()
    assert [item["id"] for item in archived_data["sessions"]] == ["s1"]
    assert archived_data["sessions"][0]["archived"] is True
    assert archived_data["summary"]["sessions"] == 1
    assert archived_data["summary"]["usage"]["total_tokens"] == 10

    assert unarchived.status_code == 200
    assert missing.status_code == 404


def test_serialize_result_keeps_assistant_message_id():
    payload = api_server._serialize_result(
        {"answer": "答复", "assistant_message_id": 42, "session_id": "s1"},
        api_server.AGENT_LABELS,
    )

    assert payload["assistant_message_id"] == 42


def test_serialize_result_defaults_medication_safety():
    payload = api_server._serialize_result(
        {"answer": "答复", "session_id": "s1"},
        api_server.AGENT_LABELS,
    )

    assert payload["medication_safety"] == {"level": "none", "alerts": []}

    warning = {"level": "warning", "alerts": [{"title": "示例"}]}
    payload = api_server._serialize_result(
        {"answer": "答复", "session_id": "s1", "medication_safety": warning},
        api_server.AGENT_LABELS,
    )
    assert payload["medication_safety"] == warning


def test_usage_daily_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "今天的问题", "今天的答复",
        metadata={
            "usage": {"prompt_tokens": 8, "completion_tokens": 4, "total_tokens": 12},
            "llm_calls": 2,
        },
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/usage/daily", params={"days": 3})
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    assert len(data["days"]) == 3
    assert data["days"][-1]["usage"]["total_tokens"] == 12
    assert data["totals"]["usage"]["total_tokens"] == 12
    assert data["totals"]["llm_calls"] == 2


def test_sessions_import_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    payload = {
        "version": 1,
        "sessions": [{
            "id": "imported-1",
            "title": "导入的会诊",
            "created_at": "2026-01-01T10:00:00",
            "updated_at": "2026-01-01T10:00:00",
            "messages": [
                {
                    "role": "user",
                    "content": "导入的问题",
                    "metadata": {},
                    "created_at": "2026-01-01T10:00:00",
                },
                {
                    "role": "assistant",
                    "content": "导入的答复",
                    "metadata": {"usage": {"total_tokens": 5}},
                    "created_at": "2026-01-01T10:00:00",
                },
            ],
        }],
    }

    try:
        client = TestClient(api_server.app)
        response = client.post("/api/sessions/import", json=payload)
        again = client.post("/api/sessions/import", json=payload)
        empty = client.post("/api/sessions/import", json={"sessions": []})
    finally:
        store.close()

    assert response.status_code == 200
    assert response.json()["imported"] == 1
    assert again.json()["skipped"] == 1
    assert empty.status_code == 400


def test_import_api_accepts_profiles_only(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.post(
            "/api/sessions/import",
            json={
                "profiles": [
                    {"name": "孩子", "context": {"age": "6"}},
                ]
            },
        )
        profiles = client.get("/api/profiles")
    finally:
        store.close()

    assert response.status_code == 200
    assert response.json()["profiles"] == 1
    assert [item["name"] for item in profiles.json()["profiles"]] == ["孩子"]


def test_sessions_api_filters_by_days(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "recent", "最近问题", "最近答复",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "older", "较早问题", "较早答复",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )
    from datetime import datetime, timedelta

    old_time = (datetime.now() - timedelta(days=10)).isoformat(timespec="seconds")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = 'older'",
            (old_time,),
        )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        week = client.get("/api/sessions", params={"days": 7}).json()
        month = client.get("/api/sessions", params={"days": 30}).json()
    finally:
        store.close()

    assert [item["id"] for item in week["sessions"]] == ["recent"]
    assert week["summary"]["sessions"] == 1
    assert week["summary"]["usage"]["total_tokens"] == 10
    assert {item["id"] for item in month["sessions"]} == {"recent", "older"}
    assert month["summary"]["usage"]["total_tokens"] == 30


def test_messages_limit_zero_returns_all(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    for index in range(3):
        store.save_exchange("s1", f"问题{index}", f"答复{index}")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions/s1/messages", params={"limit": 0})
    finally:
        store.close()

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 6


def test_usage_export_csv(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_PRICE_INPUT", raising=False)
    monkeypatch.delenv("MEDIX_PRICE_OUTPUT", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "今天的问题", "今天的答复",
        metadata={
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            "llm_calls": 2,
        },
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/usage/export.csv", params={"days": 3})
    finally:
        store.close()

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]

    text = response.text
    assert text.startswith("﻿日期,输入tokens,输出tokens,总tokens")
    assert "合计" in text
    assert ",10," in text


def test_sessions_export_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "导出问题", "导出答复",
        metadata={"usage": {"total_tokens": 8}, "llm_calls": 1},
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions/export")
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    assert data["version"] == 1
    assert data["session_count"] == 1
    assert data["sessions"][0]["id"] == "s1"
    assert "risk_text" not in data["sessions"][0]
    assert len(data["sessions"][0]["messages"]) == 2
    assert data["sessions"][0]["messages"][1]["metadata"]["usage"]["total_tokens"] == 8


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


def test_sessions_api_filters_by_profile(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("s1", "妈妈的头痛", "答复一")
    store.ensure_session("s1", "", "母亲")
    store.save_exchange("s2", "孩子发烧", "答复二")
    store.ensure_session("s2", "", "孩子")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        filtered = client.get("/api/sessions", params={"profile": "母亲"})
        missing = client.get("/api/sessions", params={"profile": "不存在"})
    finally:
        store.close()

    assert filtered.status_code == 200
    data = filtered.json()
    assert [item["id"] for item in data["sessions"]] == ["s1"]
    assert data["sessions"][0]["profile"] == "母亲"
    assert data["summary"]["sessions"] == 1
    assert missing.status_code == 200
    assert missing.json()["sessions"] == []


def test_sessions_csv_export_respects_filters(tmp_path, monkeypatch):
    import csv
    import io

    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "s1", "妈妈的头痛", "答复一",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.ensure_session("s1", "", "母亲")
    store.set_tags("s1", ["复诊"])
    store.save_exchange("s2", "孩子发烧", "答复二")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get(
            "/api/sessions/export.csv", params={"profile": "母亲"}
        )
    finally:
        store.close()

    assert response.status_code == 200
    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[0][0] == "会话编号"
    assert len(rows) == 2
    row = rows[1]
    assert row[0] == "s1"
    assert row[1] == "妈妈的头痛"
    assert row[2] == "母亲"
    assert row[3] == "复诊"
    assert row[6] == "2"
    assert row[7] == "1"
    assert row[8] == "10"
    assert row[9] == "1"


def test_sessions_csv_includes_risk_level(tmp_path, monkeypatch):
    import csv
    import io

    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "r1", "胸痛", "风险等级：**紧急**，请立即就医。"
    )
    store.save_exchange("r2", "头晕", "建议休息。")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions/export.csv")
        high = client.get(
            "/api/sessions/export.csv", params={"risk": "high"}
        )
        invalid = client.get(
            "/api/sessions/export.csv", params={"risk": "unknown"}
        )
    finally:
        store.close()

    rows = list(
        csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
    )
    assert rows[0][12] == "风险"
    by_id = {row[0]: row for row in rows[1:]}
    assert by_id["r1"][12] == "紧急"
    assert by_id["r2"][12] == ""

    high_rows = list(
        csv.reader(io.StringIO(high.content.decode("utf-8-sig")))
    )
    assert len(high_rows) == 2
    assert high_rows[1][0] == "r1"
    assert invalid.status_code == 400


def test_sessions_csv_escapes_formula_like_fields(tmp_path, monkeypatch):
    import csv
    import io

    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("=1+1", "可疑会话", "答复")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = '=2+2', "
            "updated_at = '=3+3' WHERE id = '=1+1'"
        )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.get("/api/sessions/export.csv")
    finally:
        store.close()

    text = response.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][0] == "'=1+1"
    assert rows[1][10] == "'=2+2"
    assert rows[1][11] == "'=3+3"


def test_maintenance_purge_api(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("s1", "旧问题", "旧答复")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = '2020-01-01T00:00:00' WHERE id = 's1'"
        )
    store.save_exchange("s2", "新问题", "新答复")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        response = client.post(
            "/api/maintenance/purge", json={"days": 30}
        )
        remaining = client.get("/api/sessions")
        invalid = client.post(
            "/api/maintenance/purge", json={"days": 0}
        )
        audit = client.get("/api/audit")
    finally:
        store.close()

    assert response.status_code == 200
    data = response.json()
    assert data["sessions"] == 1
    assert data["messages"] == 2
    assert [item["id"] for item in remaining.json()["sessions"]] == ["s2"]
    assert invalid.status_code == 422
    actions = [item["action"] for item in audit.json()["actions"]]
    assert "maintenance.purge" in actions


def test_sessions_api_exposes_consultation_risk(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange(
        "risk-1", "最近头晕", "风险等级：高危。建议尽快就诊。"
    )
    store.save_exchange("risk-2", "普通咨询", "建议多休息。")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        data = client.get("/api/sessions").json()
    finally:
        store.close()

    by_id = {item["id"]: item for item in data["sessions"]}
    assert by_id["risk-1"]["risk"] == "high"
    assert by_id["risk-2"]["risk"] == ""
    assert all("risk_text" not in item for item in data["sessions"])


def test_sessions_api_filters_by_risk(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "sessions.db"))
    store.save_exchange("low-1", "头晕", "风险等级：低危。建议观察。")
    store.save_exchange("high-1", "胸痛", "风险等级：高危。建议尽快就诊。")
    store.save_exchange(
        "emg-1", "剧烈胸痛", "风险等级：**紧急**，请立即就医。"
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        high = client.get(
            "/api/sessions", params={"risk": "high"}
        ).json()
        emergency = client.get(
            "/api/sessions", params={"risk": "emergency"}
        ).json()
        medium = client.get(
            "/api/sessions", params={"risk": "medium"}
        ).json()
        invalid = client.get(
            "/api/sessions", params={"risk": "unknown"}
        )
    finally:
        store.close()

    assert {item["id"] for item in high["sessions"]} == {
        "high-1", "emg-1",
    }
    assert high["summary"]["sessions"] == 2
    assert [item["id"] for item in emergency["sessions"]] == ["emg-1"]
    assert {item["id"] for item in medium["sessions"]} == {
        "high-1", "emg-1",
    }
    assert invalid.status_code == 400


def test_sessions_risk_parses_answer_beyond_1000_chars(
    tmp_path, monkeypatch
):
    store = SessionStore(str(tmp_path / "sessions.db"))
    prefix = "注意休息。" * 250
    store.save_exchange(
        "risk-long", "胸痛", prefix + "风险等级：高危。建议立即就医。"
    )
    monkeypatch.setattr(memory, "get_session_store", lambda: store)

    try:
        client = TestClient(api_server.app)
        data = client.get("/api/sessions").json()
    finally:
        store.close()

    by_id = {item["id"]: item for item in data["sessions"]}
    assert by_id["risk-long"]["risk"] == "high"
