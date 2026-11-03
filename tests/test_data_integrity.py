"""备份、非有限浮点与搜索通配符等数据完整性回归测试。"""
import json

import pytest

from core.serialization import sanitize_json
from memory.session_store import SessionStore


def test_sanitize_json_replaces_non_finite_values():
    value = {
        "ok": 1.5,
        "bad": float("inf"),
        "nested": [float("nan"), {"x": float("-inf")}],
    }

    cleaned = sanitize_json(value)

    assert cleaned["ok"] == 1.5
    assert cleaned["bad"] is None
    assert cleaned["nested"][0] is None
    assert cleaned["nested"][1]["x"] is None


def test_save_exchange_sanitizes_infinite_metadata(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange(
            "s1", "问题", "答复",
            metadata={
                "usage": {"total_tokens": float("inf")},
                "llm_calls": float("inf"),
            },
        )

        messages = store.get_messages("s1")
        metadata = messages[1]["metadata"]
        assert metadata["usage"]["total_tokens"] is None
        assert metadata["llm_calls"] is None

        # 汇总接口不能因为历史脏数据崩溃
        totals = store.usage_totals()
        assert totals["usage"]["total_tokens"] == 0
        assert totals["llm_calls"] == 0
    finally:
        store.close()


def test_import_sanitizes_non_finite_json(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    payload = {
        "version": 1,
        "sessions": [{
            "id": "import-inf",
            "title": "异常数值",
            "messages": [{
                "role": "assistant",
                "content": "答复",
                "metadata": {"usage": {"total_tokens": 1e999}},
                "created_at": "2026-01-01T00:00:00",
            }],
        }],
    }
    try:
        result = store.import_data(payload)
        assert result["imported"] == 1

        messages = store.get_messages("import-inf")
        assert messages[0]["metadata"]["usage"]["total_tokens"] is None
        assert store.usage_totals()["usage"]["total_tokens"] == 0
    finally:
        store.close()


def test_export_data_includes_all_sessions(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        for index in range(205):
            store.save_exchange(f"s{index}", f"问题{index}", "答复")

        data = store.export_data()

        assert data["session_count"] == 205
        assert len(data["sessions"]) == 205
        assert {item["id"] for item in data["sessions"]} == {
            f"s{index}" for index in range(205)
        }
    finally:
        store.close()


def test_get_all_messages_does_not_truncate(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "问题", "首条答复")
        with store._lock, store._conn:
            store._conn.executemany(
                """
                INSERT INTO messages(session_id, role, content, metadata, created_at)
                VALUES (?, 'assistant', ?, '{}', ?)
                """,
                [
                    ("s1", f"答复 {index}", "2026-01-01T00:00:00")
                    for index in range(505)
                ],
            )

        assert len(store.get_messages("s1", limit=500)) == 500
        assert len(store.get_all_messages("s1")) == 507
    finally:
        store.close()


def test_search_treats_wildcards_as_literals(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "100%有效的方法", "答复一")
        store.save_exchange("s2", "a_b 测试", "答复二")
        store.save_exchange("s3", "普通会话", "答复三")

        percent = store.list_sessions(query="%")
        underscore = store.list_sessions(query="_")

        assert [item["id"] for item in percent] == ["s1"]
        assert [item["id"] for item in underscore] == ["s2"]
        assert store.count_sessions("%") == 1
        assert store.usage_totals("%")["turns"] == 1
    finally:
        store.close()
