"""会话持久化与上下文恢复的单元测试。"""
from datetime import datetime, timedelta

import pytest

from memory import ShortTermMemory
from memory.session_store import SessionStore
from swarm.swarm_coordinator import SwarmCoordinator


@pytest.fixture
def store(tmp_path):
    instance = SessionStore(str(tmp_path / "sessions.db"))
    yield instance
    instance.close()


def test_save_and_list_sessions(store):
    store.save_exchange("s1", "我最近头痛，应该怎么办？", "建议休息并观察。")
    store.save_exchange("s1", "需要吃药吗？", "请遵医嘱。")
    store.save_exchange("s2", "高血压饮食注意什么？", "低盐饮食。")

    sessions = store.list_sessions()

    assert {s["id"] for s in sessions} == {"s1", "s2"}
    first = next(s for s in sessions if s["id"] == "s1")
    assert first["message_count"] == 4
    assert first["title"] == "我最近头痛，应该怎么办？"
    assert first["preview"].startswith("请遵医嘱")


def test_messages_are_returned_in_order(store):
    store.save_exchange("s1", "第一问", "第一答")
    store.save_exchange("s1", "第二问", "第二答")

    messages = store.get_messages("s1")

    assert [m["role"] for m in messages] == ["user", "assistant", "user", "assistant"]
    assert [m["content"] for m in messages] == ["第一问", "第一答", "第二问", "第二答"]


def test_empty_answer_is_not_saved_but_question_is(store):
    store.save_exchange("s1", "只有问题", "")

    messages = store.get_messages("s1")
    assert [m["role"] for m in messages] == ["user"]


def test_search_by_title(store):
    store.save_exchange("s1", "糖尿病怎么控制饮食？", "控制碳水。")
    store.save_exchange("s2", "高血压怎么运动？", "适量有氧。")

    results = store.list_sessions(query="糖尿病")

    assert [s["id"] for s in results] == ["s1"]


def test_search_by_message_content(store):
    store.save_exchange("s1", "最近头痛", "建议先测量血压并充分休息。")
    store.save_exchange("s2", "最近咳嗽", "多饮温水。")

    assert [s["id"] for s in store.list_sessions(query="血压")] == ["s1"]
    assert [s["id"] for s in store.list_sessions(query="温水")] == ["s2"]
    assert store.count_sessions("血压") == 1


def test_search_returns_message_snippet(store):
    store.save_exchange(
        "s1",
        "最近头痛",
        "建议先测量血压并充分休息。" * 8,
    )

    results = store.list_sessions(query="血压")

    snippet = results[0]["match_snippet"]
    assert "血压" in snippet
    assert snippet.endswith("…")
    assert len(snippet) < 200


def test_rename_and_delete(store):
    store.save_exchange("s1", "原始主诉", "答复")

    assert store.rename_session("s1", "我的会诊") is True
    assert store.get_session("s1")["title"] == "我的会诊"

    assert store.delete_session("s1") is True
    assert store.get_session("s1") is None
    assert store.get_messages("s1") == []
    assert store.count() == 0


def test_persists_across_instances(tmp_path):
    db = str(tmp_path / "sessions.db")
    first = SessionStore(db)
    first.save_exchange("s1", "跨进程问题", "跨进程答复")
    first.close()

    second = SessionStore(db)

    assert second.get_session("s1")["title"] == "跨进程问题"
    assert second.get_messages("s1")[1]["content"] == "跨进程答复"
    second.close()


def test_coordinator_restores_short_term_context(store):
    store.save_exchange("restore-test", "第一问", "第一答")

    memory = ShortTermMemory(storage_type="memory")
    memory.clear_session("restore-test")

    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    coordinator.session_store = store
    coordinator.short_term_memory = memory

    coordinator._restore_short_term_context("restore-test")

    messages = memory.get_recent_messages("restore-test", limit=10)
    assert [m["role"] for m in messages] == ["user", "assistant"]
    assert messages[0]["content"] == "第一问"


def test_coordinator_saves_exchange(store):
    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    coordinator.session_store = store

    assistant_id = coordinator._save_exchange("save-test", "主诉", "答复")

    assert isinstance(assistant_id, int)
    assert store.get_messages("save-test")[-1]["content"] == "答复"

    # 空答复不落库，避免把失败请求写成历史记录
    assert coordinator._save_exchange("save-empty", "主诉", "   ") is None
    assert store.get_messages("save-empty") == []


def test_message_metadata_roundtrip(store):
    metadata = {
        "swarm_enabled": True,
        "sources": [{"title": "指南", "score": 0.8}],
        "agent_contributions": [{"agent_id": "consultation_agent", "content": "意见"}],
    }
    store.save_exchange("meta-1", "问题", "答复", metadata=metadata)

    messages = store.get_messages("meta-1")

    assert messages[0]["metadata"] == {}
    assert messages[1]["metadata"] == metadata


def test_usage_by_session_aggregates_metadata(store):
    store.save_exchange(
        "s1", "第一问", "第一答",
        metadata={
            "usage": {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12},
            "llm_calls": 2,
        },
    )
    store.save_exchange(
        "s1", "第二问", "第二答",
        metadata={
            "usage": {"prompt_tokens": 20, "completion_tokens": 4, "total_tokens": 24},
            "llm_calls": 3,
        },
    )
    store.save_exchange(
        "s2", "另一个问题", "另一个答复",
        metadata={
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            "llm_calls": 1,
        },
    )
    store.save_exchange("s3", "旧会话问题", "旧会话答复")

    stats = store.usage_by_session(["s1", "s2", "s3", "missing"])

    assert stats["s1"]["usage"] == {
        "prompt_tokens": 30,
        "completion_tokens": 6,
        "total_tokens": 36,
    }
    assert stats["s1"]["llm_calls"] == 5
    assert stats["s1"]["turns"] == 2
    assert stats["s2"]["usage"]["total_tokens"] == 5
    assert stats["s3"]["usage"]["total_tokens"] == 0
    assert stats["s3"]["llm_calls"] == 0
    assert "missing" not in stats


def test_usage_by_session_ignores_invalid_values(store):
    store.save_exchange(
        "s1", "问题", "答复",
        metadata={
            "usage": {"prompt_tokens": "bad", "total_tokens": None},
            "llm_calls": "unknown",
        },
    )

    stats = store.usage_by_session(["s1"])

    assert stats["s1"]["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "total_tokens": 0,
    }
    assert stats["s1"]["llm_calls"] == 0


def set_session_time(store, session_id, timestamp):
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = ? WHERE id = ?",
            (timestamp, session_id),
        )


def test_list_sessions_paginates_and_counts(store):
    for index in range(5):
        store.save_exchange(f"s{index}", f"问题{index}", "答复")
        set_session_time(store, f"s{index}", f"2026-01-01T00:00:0{index}")

    first = store.list_sessions(limit=2)
    second = store.list_sessions(limit=2, offset=2)

    assert [s["id"] for s in first] == ["s4", "s3"]
    assert [s["id"] for s in second] == ["s2", "s1"]
    assert store.count_sessions() == 5
    assert store.count_sessions("问题3") == 1


def test_usage_totals_cover_all_matching_sessions(store):
    store.save_exchange(
        "s1", "糖尿病饮食", "答复一",
        metadata={
            "usage": {"prompt_tokens": 4, "completion_tokens": 1, "total_tokens": 5},
            "llm_calls": 1,
        },
    )
    store.save_exchange(
        "s2", "高血压运动", "答复二",
        metadata={
            "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
            "llm_calls": 2,
        },
    )
    store.save_exchange("s3", "旧会话", "答复三")

    totals = store.usage_totals()
    assert totals["turns"] == 3
    assert totals["llm_calls"] == 3
    assert totals["usage"] == {
        "prompt_tokens": 13,
        "completion_tokens": 3,
        "total_tokens": 16,
    }

    filtered = store.usage_totals("糖尿病")
    assert filtered["turns"] == 1
    assert filtered["llm_calls"] == 1
    assert filtered["usage"]["total_tokens"] == 5

    # 搜索命中答复正文时，也应统计整个会话的用量
    content_filtered = store.usage_totals("答复二")
    assert content_filtered["turns"] == 1
    assert content_filtered["llm_calls"] == 2
    assert content_filtered["usage"]["total_tokens"] == 11


def test_daily_usage_groups_by_date(store):
    store.save_exchange(
        "s1", "昨天的问题", "昨天的答复",
        metadata={
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            "llm_calls": 1,
        },
    )
    store.save_exchange(
        "s2", "今天的问题", "今天的答复",
        metadata={
            "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
            "llm_calls": 2,
        },
    )

    yesterday = (datetime.now() - timedelta(days=1)).date().isoformat()
    today = datetime.now().date().isoformat()
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 's1'",
            (f"{yesterday}T12:00:00",),
        )

    data = store.daily_usage(days=2)

    assert [day["date"] for day in data["days"]] == [yesterday, today]
    assert data["days"][0]["usage"]["total_tokens"] == 10
    assert data["days"][0]["llm_calls"] == 1
    assert data["days"][1]["usage"]["total_tokens"] == 20
    assert data["days"][1]["llm_calls"] == 2
    assert data["totals"]["usage"]["total_tokens"] == 30
    assert data["totals"]["llm_calls"] == 3
    assert data["totals"]["turns"] == 2


def test_daily_usage_includes_empty_days(store):
    data = store.daily_usage(days=7)

    assert len(data["days"]) == 7
    assert all(day["usage"]["total_tokens"] == 0 for day in data["days"])
    assert data["totals"]["turns"] == 0


def test_export_data_includes_sessions_and_messages(store):
    store.save_exchange(
        "s1", "问题一", "答复一",
        metadata={
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            "llm_calls": 2,
        },
    )
    store.save_exchange("s2", "问题二", "答复二")

    data = store.export_data()

    assert data["version"] == 1
    assert data["session_count"] == 2
    sessions = {item["id"]: item for item in data["sessions"]}
    assert len(sessions["s1"]["messages"]) == 2
    assert sessions["s1"]["messages"][1]["metadata"]["usage"]["total_tokens"] == 10
    assert sessions["s2"]["messages"][0]["content"] == "问题二"


def test_favorite_filter_and_usage(store):
    store.save_exchange(
        "s1", "收藏问题", "收藏答复",
        metadata={
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            "llm_calls": 1,
        },
    )
    store.save_exchange(
        "s2", "普通问题", "普通答复",
        metadata={
            "usage": {"prompt_tokens": 15, "completion_tokens": 5, "total_tokens": 20},
            "llm_calls": 2,
        },
    )

    assert store.set_favorite("s1", True) is True
    assert store.set_favorite("missing", True) is False

    sessions = store.list_sessions(favorites_only=True)
    assert [item["id"] for item in sessions] == ["s1"]
    assert sessions[0]["favorite"] is True
    assert store.count_sessions(favorites_only=True) == 1

    totals = store.usage_totals(favorites_only=True)
    assert totals["usage"]["total_tokens"] == 10
    assert totals["llm_calls"] == 1

    daily = store.daily_usage(days=1, favorites_only=True)
    assert daily["totals"]["usage"]["total_tokens"] == 10

    store.set_favorite("s1", False)
    assert store.list_sessions(favorites_only=True) == []


def test_list_sessions_filters_by_days(store):
    store.save_exchange(
        "s1", "最近问题", "最近答复",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "较早问题", "较早答复",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )
    now = datetime.now()
    set_session_time(store, "s1", now.isoformat(timespec="seconds"))
    set_session_time(
        store, "s2", (now - timedelta(days=10)).isoformat(timespec="seconds")
    )

    week = store.list_sessions(days=7)
    month = store.list_sessions(days=30)

    assert [item["id"] for item in week] == ["s1"]
    assert {item["id"] for item in month} == {"s1", "s2"}
    assert store.count_sessions(days=7) == 1
    assert store.usage_totals(days=7)["usage"]["total_tokens"] == 10
    assert store.usage_totals(days=30)["usage"]["total_tokens"] == 30


def test_import_data_roundtrip(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_exchange(
        "s1", "导入问题", "导入答复",
        metadata={
            "usage": {"prompt_tokens": 6, "completion_tokens": 4, "total_tokens": 10},
            "llm_calls": 2,
        },
    )
    data = source.export_data()
    assert data["sessions"][0]["favorite"] is False
    source.set_favorite("s1", True)
    data = source.export_data()
    assert data["sessions"][0]["favorite"] is True
    source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)

        assert result == {
            "imported": 1,
            "skipped": 0,
            "invalid": 0,
            "messages": 2,
        }
        messages = target.get_messages("s1")
        assert messages[0]["content"] == "导入问题"
        assert messages[1]["metadata"]["usage"]["total_tokens"] == 10
        assert target.list_sessions()[0]["favorite"] is True

        again = target.import_data(data)
        assert again["imported"] == 0
        assert again["skipped"] == 1
        assert again["messages"] == 0
    finally:
        target.close()


def test_import_data_rejects_invalid_payload(store):
    for payload in (None, "not a dict", {}, {"sessions": []}, {"sessions": "x"}):
        with pytest.raises(ValueError):
            store.import_data(payload)

    result = store.import_data({
        "sessions": [
            {"id": "", "messages": []},
            {"id": "no-messages", "messages": []},
            {"id": "only-invalid", "messages": [{"role": "tool", "content": "x"}]},
        ]
    })
    assert result["imported"] == 0
    assert result["invalid"] == 3


def test_save_exchange_returns_assistant_message_id(store):
    assistant_id = store.save_exchange("s1", "问题", "答复")

    messages = store.get_messages("s1")
    assert isinstance(assistant_id, int)
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"
    assert messages[1]["id"] == assistant_id
    assert messages[0]["id"] != assistant_id

    assert store.save_exchange("s2", "只有问题", "") is None


def test_message_feedback_roundtrip(store):
    assistant_id = store.save_exchange(
        "s1", "最近头痛", "建议测量血压并充分休息。",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )

    first = store.set_message_feedback("s1", assistant_id, 1)
    assert first["rating"] == 1
    feedback = store.get_messages("s1")[1]["metadata"]["feedback"]
    assert feedback["rating"] == 1
    assert feedback["comment"] == ""
    assert store.feedback_totals() == {
        "total": 1,
        "helpful": 1,
        "unhelpful": 0,
        "rate": 100.0,
    }

    store.set_message_feedback("s1", assistant_id, -1, "  需要更多   依据 ")
    feedback = store.get_messages("s1")[1]["metadata"]["feedback"]
    assert feedback["rating"] == -1
    assert feedback["comment"] == "需要更多 依据"
    assert store.feedback_totals()["unhelpful"] == 1

    assert store.set_message_feedback("s1", assistant_id, 0)["rating"] == 0
    assert "feedback" not in store.get_messages("s1")[1]["metadata"]
    assert store.feedback_totals() == {
        "total": 0,
        "helpful": 0,
        "unhelpful": 0,
        "rate": None,
    }


def test_feedback_rejects_unknown_or_user_message(store):
    assistant_id = store.save_exchange("s1", "问题", "答复")
    user_id = store.get_messages("s1")[0]["id"]

    assert store.set_message_feedback("s1", 999999, 1) is None
    assert store.set_message_feedback("s1", user_id, 1) is None
    assert store.set_message_feedback("other-session", assistant_id, 1) is None
    assert store.set_message_feedback("s1", assistant_id, 2) is None


def test_feedback_totals_respect_filters(store):
    diabetic = store.save_exchange("s1", "糖尿病怎么控制饮食？", "控制碳水。")
    hypertension = store.save_exchange("s2", "高血压怎么运动？", "适量有氧。")
    store.set_message_feedback("s1", diabetic, 1)
    store.set_message_feedback("s2", hypertension, -1)
    store.set_favorite("s1", True)

    assert store.feedback_totals()["total"] == 2
    assert store.feedback_totals(query="糖尿病")["helpful"] == 1
    assert store.feedback_totals(query="糖尿病")["unhelpful"] == 0
    assert store.feedback_totals(query="适量有氧")["unhelpful"] == 1
    assert store.feedback_totals(favorites_only=True) == {
        "total": 1,
        "helpful": 1,
        "unhelpful": 0,
        "rate": 100.0,
    }


def test_feedback_survives_export_and_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    assistant_id = source.save_exchange("s1", "问题", "答复")
    source.set_message_feedback("s1", assistant_id, -1, "缺少剂量说明")
    data = source.export_data()
    source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)

        assert result["imported"] == 1
        feedback = target.get_messages("s1")[1]["metadata"]["feedback"]
        assert feedback["rating"] == -1
        assert feedback["comment"] == "缺少剂量说明"
        assert target.feedback_totals()["unhelpful"] == 1
    finally:
        target.close()


def test_legacy_messages_table_is_migrated(tmp_path):
    import sqlite3

    db = str(tmp_path / "legacy.db")
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE sessions (
            id TEXT PRIMARY KEY,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO sessions VALUES ('s1', '旧会话', '2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "INSERT INTO messages(session_id, role, content, created_at) "
        "VALUES ('s1', 'assistant', '旧答复', '2026-01-01')"
    )
    conn.commit()
    conn.close()

    migrated = SessionStore(db)
    try:
        assert migrated.get_messages("s1")[0]["metadata"] == {}
        assert migrated.list_sessions()[0]["favorite"] is False

        migrated.save_exchange(
            "s1", "新问题", "新答复", metadata={"sources": [{"title": "x"}]}
        )
        assert migrated.get_messages("s1")[-1]["metadata"]["sources"][0]["title"] == "x"
    finally:
        migrated.close()
