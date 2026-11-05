"""API 数据导入/导出与时间边界的回归测试。"""
import asyncio
import csv
import io
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from knowledge.documents import MAX_DOCUMENT_CHARS, load_document
from memory.session_store import (
    MAX_IMPORT_MESSAGE_CHARS,
    MAX_IMPORT_TOTAL_CHARS,
    SessionStore,
)


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_memory_scope_is_derived_from_profile_or_session():
    profile_scope = api_server._memory_scope("母亲", "s1")
    assert profile_scope.startswith("medix_profile_")
    # 外部记忆服务会持久化 user_id，命名空间里不能出现档案姓名
    assert "母亲" not in profile_scope
    session_scope = api_server._memory_scope("", "s1")
    assert session_scope.startswith("medix_session_")
    assert "s1" not in session_scope
    # 同一档案必须稳定复用同一命名空间
    assert profile_scope == api_server._memory_scope(" 母亲 ", "s2")
    # 清洗后同名但原值不同的档案必须落在不同命名空间
    assert api_server._memory_scope("张 三", "s1") != api_server._memory_scope(
        "张_三", "s1"
    )


def test_resolve_memory_profile_prefers_bound_session_profile(client_and_store):
    _client, store = client_and_store
    store.save_exchange("bound-session", "问题", "回答")
    store.ensure_session("bound-session", "", "母亲")

    profile = asyncio.run(api_server._resolve_memory_profile(
        "bound-session", "孩子"
    ))

    assert profile == "母亲"


def test_messages_endpoint_limit_zero_returns_more_than_500(client_and_store):
    client, store = client_and_store
    store.save_exchange("many-messages", "问题", "回答")
    rows = [
        ("many-messages", "user", f"消息 {i}", "{}", "2026-11-01T08:00:00")
        for i in range(600)
    ]
    with store._conn:
        store._conn.executemany(
            """
            INSERT INTO messages(session_id, role, content, metadata, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            rows,
        )

    response = client.get("/api/sessions/many-messages/messages?limit=0")

    assert response.status_code == 200
    assert len(response.json()["messages"]) == 602


def test_audit_csv_sanitizes_imported_formula_cells(client_and_store):
    client, store = client_and_store
    store.import_data({
        "audit_log": [{
            "action": "=HYPERLINK(\"http://evil\",\"x\")",
            "target": "=1+1",
            "detail": "=cmd|' /C calc'!A0",
            "created_at": "=2+2",
        }],
    })

    response = client.get("/api/audit/export.csv")
    text = response.content.decode("utf-8-sig")

    assert response.status_code == 200
    rows = list(csv.reader(io.StringIO(text)))
    assert rows[1][0].startswith("'=")
    assert rows[1][1].startswith("'=")
    assert rows[1][2].startswith("'=")
    assert rows[1][3].startswith("'=")


def test_status_hides_internal_details_without_token(monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "status-test-token")
    old_startup, old_kb = api_server.state.startup_error, api_server.state.kb_error
    api_server.state.startup_error = "D:\\secret\\startup.py failed"
    api_server.state.kb_error = "/home/user/private/kb error"
    try:
        client = TestClient(api_server.app)
        response = client.get("/api/status")
        data = response.json()

        assert response.status_code == 200
        assert "secret" not in str(data)
        assert "/home/user" not in str(data)
        assert data["model_name"] is None
        assert data["base_url"] is None
        assert "服务初始化失败" in data["startup_error"]
    finally:
        api_server.state.startup_error = old_startup
        api_server.state.kb_error = old_kb


def test_import_rejects_oversized_message(tmp_path):
    store = SessionStore(str(tmp_path / "import.db"))
    try:
        payload = {
            "version": 1,
            "sessions": [{
                "id": "big-message",
                "title": "超大消息",
                "messages": [{
                    "role": "user",
                    "content": "x" * (MAX_IMPORT_MESSAGE_CHARS + 1),
                }],
            }],
        }
        with pytest.raises(ValueError):
            store.import_data(payload)
    finally:
        store.close()


def test_import_caps_fit_request_body_limit():
    # 导入上限按 UTF-8 最坏 4 字节/字符估算，不能超过中间件请求体上限，
    # 否则合法备份会在到达 SessionStore 之前被 413 拒绝
    body_limit = api_server._body_limit_for_path("/api/sessions/import")
    assert MAX_IMPORT_TOTAL_CHARS * 4 <= body_limit


def test_document_ids_include_extension(tmp_path):
    md = tmp_path / "same.md"
    txt = tmp_path / "same.txt"
    md.write_text("# 文档\n\n内容一", encoding="utf-8")
    txt.write_text("# 文档\n\n内容二", encoding="utf-8")

    assert load_document(md)["id"] != load_document(txt)["id"]


def test_document_text_over_limit_rejected(tmp_path):
    path = tmp_path / "too-long.txt"
    path.write_text("x" * (MAX_DOCUMENT_CHARS + 1), encoding="utf-8")

    with pytest.raises(ValueError):
        load_document(path)


def test_reminder_csv_roundtrip_keeps_session_and_repeat_link(client_and_store):
    client, store = client_and_store
    store.import_data({"reminders": [
        {
            "id": 1, "title": "复查", "due_at": "2026-11-10",
            "session_id": "session-42", "created_at": "2026-11-01T08:00:00",
        },
        {
            "id": 2, "title": "复查（续）", "due_at": "2026-11-17",
            "session_id": "session-42", "repeats_from": 1,
            "created_at": "2026-11-08T08:00:00",
        },
    ]})
    reminders_before = {
        item["title"]: item for item in store.list_reminders(include_done=True)
    }
    assert reminders_before["复查（续）"]["repeats_from"] == reminders_before["复查"]["id"]

    export = client.get("/api/reminders/export.csv", params={"include_done": True})
    assert export.status_code == 200
    store._conn.execute("DELETE FROM reminders")
    store._conn.commit()

    imported = client.post(
        "/api/reminders/import",
        files={"file": ("reminders.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    reminders = {
        item["title"]: item for item in store.list_reminders(include_done=True)
    }

    assert reminders["复查"]["session_id"] == "session-42"
    assert reminders["复查（续）"]["session_id"] == "session-42"
    assert reminders["复查（续）"]["repeats_from"] == reminders["复查"]["id"]


def test_future_timestamp_accepts_timezone_skew(client_and_store):
    _client, store = client_and_store
    future = (datetime.now() + timedelta(hours=8)).isoformat(timespec="seconds")

    metric = store.add_metric("母亲", "weight", 60, None, future, "")

    assert metric is not None
    assert metric["measured_at"].startswith(future[:10])


def test_csv_parser_error_returns_400(client_and_store):
    client, _store = client_and_store
    big_field = '"' + ("x" * 140000) + '"'
    content = "药物名称,剂量\n" + big_field + ",5 mg\n"

    response = client.post(
        "/api/medications/import",
        files={"file": ("big.csv", content.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 400
    assert "CSV" in response.json()["detail"]


def test_patch_empty_timestamp_is_rejected(client_and_store):
    client, _store = client_and_store
    created = client.post(
        "/api/metrics",
        json={
            "profile": "母亲", "metric": "weight", "value1": 60,
            "measured_at": "2026-11-01T08:00",
        },
    ).json()["metric"]

    response = client.patch(
        f"/api/metrics/{created['id']}", json={"measured_at": ""}
    )

    assert response.status_code == 400
    assert client.get("/api/metrics").json()["metrics"][0]["measured_at"].startswith(
        "2026-11-01"
    )


def test_metrics_import_list_cap_returns_400(client_and_store, monkeypatch):
    client, _store = client_and_store
    import memory.session_store as session_store_module
    monkeypatch.setitem(
        session_store_module.IMPORT_LIST_LIMITS, "health_metrics", 1
    )
    content = (
        "测量时间,档案,指标,数值1,单位\n"
        "2026-11-01T08:00,母亲,体重,60,kg\n"
        "2026-11-02T08:00,母亲,体重,61,kg\n"
    )

    response = client.post(
        "/api/metrics/import",
        files={"file": ("metrics.csv", content.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 400
    assert "最多导入" in response.json()["detail"]
