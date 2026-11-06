"""会话持久化与上下文恢复的单元测试。"""
import asyncio
import json
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

    asyncio.run(coordinator._restore_short_term_context("restore-test"))

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


def set_feedback_time(store, session_id, timestamp):
    with store._lock, store._conn:
        row = store._conn.execute(
            """
            SELECT id, metadata FROM messages
             WHERE session_id = ? AND role = 'assistant'
             ORDER BY id DESC LIMIT 1
            """,
            (session_id,),
        ).fetchone()
        metadata = json.loads(row["metadata"] or "{}")
        metadata["feedback"]["updated_at"] = timestamp
        store._conn.execute(
            "UPDATE messages SET metadata = ? WHERE id = ?",
            (json.dumps(metadata, ensure_ascii=False), row["id"]),
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


def test_session_totals_combines_usage_and_feedback(store):
    store.save_exchange(
        "s1", "问题一", "答复一",
        metadata={
            "usage": {"total_tokens": 10},
            "llm_calls": 2,
            "feedback": {"rating": 1},
        },
    )
    store.save_exchange(
        "s2", "问题二", "答复二",
        metadata={
            "usage": {"total_tokens": 20},
            "llm_calls": 1,
            "feedback": {"rating": -1, "comment": "需要补充"},
        },
    )

    combined = store.session_totals()

    assert combined["turns"] == 2
    assert combined["llm_calls"] == 3
    assert combined["usage"]["total_tokens"] == 30
    assert combined["feedback"] == {
        "total": 2,
        "helpful": 1,
        "unhelpful": 1,
        "rate": 50.0,
    }

    filtered = store.session_totals(query="问题一")
    assert filtered["turns"] == 1
    assert filtered["usage"]["total_tokens"] == 10
    assert filtered["feedback"]["total"] == 1


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
            "profiles": 0,
            "metrics": 0,
            "targets": 0,
            "reminders": 0,
            "symptoms": 0,
            "medications": 0,
            "medication_logs": 0,
            "vaccinations": 0,
            "appointments": 0,
            "audit_log": 0,
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


def test_profile_crud(store):
    assert store.list_profiles() == []

    saved = store.save_profile(
        "妈妈",
        {"age": "58", "sex": "女", "medications": "氨氯地平"},
    )
    assert saved["name"] == "妈妈"
    assert saved["context"] == {
        "age": "58",
        "sex": "女",
        "medications": "氨氯地平",
    }

    profiles = store.list_profiles()
    assert [profile["name"] for profile in profiles] == ["妈妈"]
    assert profiles[0]["context"]["age"] == "58"

    updated = store.save_profile("妈妈", {"age": 60, "allergies": "青霉素"})
    assert updated["created_at"] == saved["created_at"]
    assert updated["context"] == {"age": "60", "allergies": "青霉素"}

    assert store.delete_profile("妈妈") is True
    assert store.delete_profile("妈妈") is False
    assert store.list_profiles() == []


def test_profile_validation_and_normalization(store):
    assert store.save_profile("", {"age": "30"}) is None
    assert store.save_profile("name", {}) is None
    assert store.save_profile("name", "not-a-dict") is None
    assert store.save_profile("name", {"unknown": "value"}) is None

    saved = store.save_profile(
        "  长期   档案  ",
        {
            "history": ["高血压", "糖尿病"],
            "age": 30,
            "medications": {"早": "氨氯地平", "晚": "阿托伐他汀"},
        },
    )

    assert saved["name"] == "长期 档案"
    assert saved["context"]["history"] == "高血压；糖尿病"
    assert saved["context"]["age"] == "30"
    assert "早=氨氯地平" in saved["context"]["medications"]


def test_rename_profile_updates_metrics(store):
    store.save_profile("妈妈", {"age": "58"})
    store.add_metric("妈妈", "weight", 62.5, measured_at="2026-01-02T08:00")
    store.save_exchange("s1", "妈妈的头痛", "答复")
    store.ensure_session("s1", "", "妈妈")
    store.add_reminder("复查", "2026-11-10", profile="妈妈")
    store.add_symptom("妈妈", "头晕", 5, "2026-11-01T08:00")
    store.set_metric_target("妈妈", "weight", 70)
    medication = store.add_medication("妈妈", "氨氯地平", "5 mg")
    store.add_medication_log(medication["id"], "2026-11-01T08:00")

    assert store.rename_profile("妈妈", "母亲") == "母亲"
    assert [p["name"] for p in store.list_profiles()] == ["母亲"]
    assert store.list_metrics()[0]["profile"] == "母亲"
    assert [item["id"] for item in store.list_sessions(profile="母亲")] == ["s1"]
    assert [
        item["title"] for item in store.list_reminders(profile="母亲")
    ] == ["复查"]
    assert [
        item["metric"] for item in store.list_metric_targets("母亲")
    ] == ["weight"]
    assert [
        item["symptom"] for item in store.list_symptoms(profile="母亲")
    ] == ["头晕"]
    assert [
        item["name"] for item in store.list_medications(profile="母亲")
    ] == ["氨氯地平"]
    renamed_logs = store.list_medication_logs(profile="母亲")
    assert len(renamed_logs) == 1
    assert renamed_logs[0]["profile"] == "母亲"
    assert "妈妈" not in store.list_used_profile_names()
    assert "母亲" in store.list_used_profile_names()

    assert store.rename_profile("missing", "x") is None
    assert store.rename_profile("母亲", "母亲") == "母亲"

    store.save_profile("其他", {"age": "30"})
    with pytest.raises(ValueError):
        store.rename_profile("母亲", "其他")

    with pytest.raises(ValueError):
        store.rename_profile("母亲", "   ")


def test_set_session_profile_only_updates_existing(store):
    store.save_exchange("s1", "问题", "答复")

    assert store.set_session_profile("s1", "母亲") is True
    assert store.get_session("s1")["profile"] == "母亲"
    assert store.set_session_profile("s1", "孩子") is False
    assert store.get_session("s1")["profile"] == "母亲"
    assert store.set_session_profile("missing", "母亲") is False
    assert store.get_session("missing") is None
    assert store.set_session_profile("s1", "   ") is False


def test_list_session_profiles(store):
    store.save_exchange("s1", "妈妈的头痛", "答复")
    store.ensure_session("s1", "", "母亲")
    store.save_exchange("s2", "孩子的发烧", "答复")
    store.ensure_session("s2", "", "孩子")
    store.save_exchange("s3", "未指定档案", "答复")

    profiles = {
        item["name"]: item["count"]
        for item in store.list_session_profiles()
    }
    assert profiles == {"母亲": 1, "孩子": 1}


def test_list_used_profile_names(store):
    store.save_profile("档案A", {"age": "30"})
    store.save_exchange("s1", "问题", "答复")
    store.ensure_session("s1", "", "档案B")
    store.add_reminder("复查", "2026-11-10", profile="档案C")
    store.set_metric_target("档案D", "weight", 70)
    store.add_metric(
        "档案E", "weight", 60, measured_at="2026-11-01T08:00"
    )
    store.add_symptom("档案F", "头晕", 5, "2026-11-01T08:00")

    assert set(store.list_used_profile_names()) == {
        "档案A", "档案B", "档案C", "档案D", "档案E", "档案F",
    }


def test_profiles_survive_export_and_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_exchange("s1", "问题", "答复")
    source.save_profile("妈妈", {"age": "58"})
    data = source.export_data()
    source.close()

    assert data["profiles"][0]["name"] == "妈妈"

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)

        assert result["profiles"] == 1
        assert target.list_profiles()[0]["name"] == "妈妈"

        again = target.import_data(data)
        assert again["profiles"] == 0
    finally:
        target.close()


def test_import_profiles_only_backup(store):
    result = store.import_data({
        "profiles": [
            {"name": "孩子", "context": {"age": "6", "allergies": "青霉素"}},
        ]
    })

    assert result["profiles"] == 1
    assert result["imported"] == 0
    assert store.list_profiles()[0]["name"] == "孩子"


def test_archive_filters_and_counts(store):
    store.save_exchange(
        "s1", "归档问题", "答复一",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "普通问题", "答复二",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )

    assert store.set_archived("s1", True) is True
    assert store.set_archived("missing", True) is False
    assert store.list_sessions(archived=True)[0]["archived"] is True

    assert [item["id"] for item in store.list_sessions(archived=False)] == ["s2"]
    assert [item["id"] for item in store.list_sessions(archived=True)] == ["s1"]
    assert {item["id"] for item in store.list_sessions()} == {"s1", "s2"}

    assert store.count_sessions(archived=False) == 1
    assert store.count_sessions(archived=True) == 1
    assert store.usage_totals(archived=False)["usage"]["total_tokens"] == 20
    assert store.usage_totals(archived=True)["usage"]["total_tokens"] == 10

    store.set_archived("s1", False)
    assert store.list_sessions(archived=True) == []
    assert store.count_sessions(archived=True) == 0


def test_feedback_totals_respects_archive(store):
    archived_id = store.save_exchange("s1", "归档问题", "答复一")
    active_id = store.save_exchange("s2", "普通问题", "答复二")
    store.set_message_feedback("s1", archived_id, 1)
    store.set_message_feedback("s2", active_id, -1)
    store.set_archived("s1", True)

    assert store.feedback_totals(archived=False)["unhelpful"] == 1
    assert store.feedback_totals(archived=True)["helpful"] == 1
    assert store.feedback_totals()["total"] == 2


def test_archive_survives_export_and_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_exchange("s1", "归档问题", "答复")
    source.set_archived("s1", True)
    data = source.export_data()
    source.close()

    assert data["sessions"][0]["archived"] is True

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.import_data(data)
        assert [item["id"] for item in target.list_sessions(archived=True)] == ["s1"]
    finally:
        target.close()


def test_recent_feedback_returns_latest_by_rating(store):
    first = store.save_exchange("s1", "问题一", "答复一")
    second = store.save_exchange("s2", "问题二", "答复二")
    third = store.save_exchange("s3", "问题三", "答复三")
    store.set_message_feedback("s1", first, -1, "依据不足")
    store.set_message_feedback("s2", second, 1)
    store.set_message_feedback("s3", third, -1, "缺少剂量")

    set_feedback_time(store, "s1", "2026-01-01T00:00:00")
    set_feedback_time(store, "s3", "2026-01-02T00:00:00")

    unhelpful = store.recent_feedback(limit=10)
    assert [item["session_id"] for item in unhelpful] == ["s3", "s1"]
    assert unhelpful[0]["comment"] == "缺少剂量"
    assert unhelpful[0]["session_title"] == "问题三"

    helpful = store.recent_feedback(rating=1)
    assert [item["session_id"] for item in helpful] == ["s2"]
    assert store.recent_feedback(limit=1)[0]["session_id"] == "s3"
    assert store.recent_feedback(rating=5) == []


def test_quality_insights_aggregates(store):
    store.save_exchange(
        "s1", "胸痛", "答复一",
        metadata={
            "swarm_enabled": True,
            "triage": {
                "level": "emergency",
                "matches": ["胸痛", "呼吸困难"],
                "message": "",
            },
            "medication_safety": {
                "level": "warning",
                "alerts": [{
                    "title": "华法林与非甾体抗炎药",
                    "detail": "增加出血风险",
                }],
            },
            "feedback": {"rating": -1, "comment": "依据不足"},
            "total_time": 4.0,
            "usage": {"total_tokens": 100},
        },
    )
    store.save_exchange(
        "s2", "头痛", "答复二",
        metadata={
            "swarm_enabled": False,
            "triage": {"level": "none", "matches": []},
            "medication_safety": {"level": "none", "alerts": []},
            "feedback": {"rating": 1},
            "total_time": 2.0,
            "usage": {"total_tokens": 50},
        },
    )

    data = store.quality_insights(days=30)

    assert data["sessions"] == 2
    assert data["turns"] == 2
    assert data["emergency"] == 1
    assert data["medication_sessions"] == 1
    assert data["medication_alerts"] == 1
    assert data["feedback_total"] == 2
    assert data["helpful"] == 1
    assert data["unhelpful"] == 1
    assert data["rate"] == 50.0
    assert data["swarm_turns"] == 1
    assert data["avg_time"] == 3.0
    assert data["avg_tokens"] == 75
    assert {item["label"] for item in data["top_risks"]} == {"胸痛", "呼吸困难"}
    assert data["top_medication_alerts"] == [
        {"title": "华法林与非甾体抗炎药", "count": 1},
    ]


def test_quality_insights_empty_and_time_filter(store):
    empty = store.quality_insights(days=7)
    assert empty["sessions"] == 0
    assert empty["turns"] == 0
    assert empty["rate"] is None
    assert empty["top_risks"] == []
    assert empty["top_medication_alerts"] == []

    store.save_exchange("s1", "旧问题", "旧答复")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 's1'",
            ("2026-01-01T00:00:00",),
        )

    assert store.quality_insights(days=7)["turns"] == 0


def test_health_metrics_crud_and_filters(store):
    bp = store.add_metric(
        "妈妈", "bp", 138, 86,
        measured_at="2026-01-02T08:00", note="晨起",
    )
    weight = store.add_metric(
        "妈妈", "weight", 62.5,
        measured_at="2026-01-01T07:30",
    )
    temperature = store.add_metric("", "temperature", 36.7)

    assert bp["unit"] == "mmHg"
    assert weight["unit"] == "kg"
    assert temperature["measured_at"]

    all_items = store.list_metrics()
    assert [item["id"] for item in all_items] == [
        temperature["id"], bp["id"], weight["id"],
    ]
    assert [item["metric"] for item in store.list_metrics(profile="妈妈")] == [
        "bp", "weight",
    ]
    assert [item["metric"] for item in store.list_metrics(metric="bp")] == ["bp"]
    assert store.list_metrics(profile="不存在") == []
    assert store.list_metrics(metric="unknown") == []

    assert store.delete_metric(bp["id"]) is True
    assert store.delete_metric(bp["id"]) is False
    assert all(item["id"] != bp["id"] for item in store.list_metrics())


def test_list_metrics_days_filter(store):
    from datetime import datetime

    store.add_metric("", "weight", 60, measured_at="2020-01-01T08:00")
    store.add_metric(
        "", "weight", 61,
        measured_at=datetime.now().isoformat(timespec="minutes"),
    )
    assert store.add_metric(
        "", "weight", 62, measured_at="2099-01-01T08:00"
    ) is None

    assert len(store.list_metrics()) == 2
    recent = store.list_metrics(days=30)
    assert [item["value1"] for item in recent] == [61]


def test_health_metrics_validation(store):
    assert store.add_metric("", "bp", 120, None) is None
    assert store.add_metric("", "bp", 90, 100) is None
    assert store.add_metric("", "bp", 400, 90) is None
    assert store.add_metric("", "weight", 5) is None
    assert store.add_metric("", "glucose", 100) is None
    assert store.add_metric("", "temperature", 50) is None
    assert store.add_metric("", "unknown", 1) is None
    assert store.add_metric(
        "", "weight", 60, measured_at="not-a-date"
    ) is None
    assert store.list_metrics() == []


def test_update_health_metric(store):
    item = store.add_metric(
        "妈妈", "bp", 138, 86,
        measured_at="2026-01-02T08:00", note="晨起",
    )

    updated = store.update_metric(
        item["id"], "妈妈", "weight", 62.5,
        measured_at="2026-01-03T07:30", note="复测",
    )

    assert updated["metric"] == "weight"
    assert updated["value1"] == 62.5
    assert updated["unit"] == "kg"
    assert updated["note"] == "复测"

    listed = store.list_metrics()
    assert len(listed) == 1
    assert listed[0]["metric"] == "weight"
    assert listed[0]["measured_at"] == "2026-01-03T07:30"

    assert store.update_metric(999999, "妈妈", "weight", 60) is None
    assert store.update_metric(item["id"], "妈妈", "bp", 80, 120) is None
    assert store.update_metric(item["id"], "妈妈", "weight", 0) is None


def test_screener_score_metrics(store):
    phq9 = store.add_metric("", "phq9", 10, note="PHQ-9 筛查")
    gad7 = store.add_metric("", "gad7", 21, note="GAD-7 筛查")

    assert phq9["unit"] == "分"
    assert gad7["unit"] == "分"
    assert store.add_metric("", "phq9", 28) is None
    assert store.add_metric("", "gad7", 22) is None
    assert [item["metric"] for item in store.list_metrics(metric="phq9")] == [
        "phq9"
    ]


def test_health_metrics_survive_export_and_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_exchange("s1", "问题", "答复")
    source.add_metric(
        "妈妈", "bp", 130, 80,
        measured_at="2026-01-03T08:00", note="服药后",
    )
    data = source.export_data()
    source.close()

    assert data["health_metrics"][0]["metric"] == "bp"

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)
        assert result["metrics"] == 1
        items = target.list_metrics()
        assert items[0]["profile"] == "妈妈"
        assert items[0]["value1"] == 130
        assert items[0]["value2"] == 80

        again = target.import_data(data)
        assert again["metrics"] == 0
        assert len(target.list_metrics()) == 1
    finally:
        target.close()


def test_import_metrics_only_backup(store):
    result = store.import_data({
        "health_metrics": [
            {
                "profile": "孩子",
                "metric": "temperature",
                "value1": 38.2,
                "measured_at": "2026-02-01T20:00",
            }
        ]
    })

    assert result["metrics"] == 1
    assert store.list_metrics()[0]["value1"] == 38.2


def test_archive_older_than(store):
    store.save_exchange("old", "旧问题", "旧答复")
    store.save_exchange("new", "新问题", "新答复")
    old_time = (datetime.now() - timedelta(days=40)).isoformat(timespec="seconds")
    set_session_time(store, "old", old_time)

    assert store.archive_older_than(30) == 1
    assert [item["id"] for item in store.list_sessions(archived=True)] == ["old"]
    assert [item["id"] for item in store.list_sessions(archived=False)] == ["new"]

    assert store.archive_older_than(30) == 0
    assert store.archive_older_than(0) == 0


def test_audit_log(store):
    assert store.list_audit_log() == []

    first = store.log_action("session.delete", "s1", "用户删除")
    second = store.log_action("session.import", detail="imported=2")

    assert first > 0
    assert second > first

    actions = store.list_audit_log()
    assert [item["action"] for item in actions] == [
        "session.import", "session.delete",
    ]
    assert actions[0]["detail"] == "imported=2"
    assert actions[1]["target"] == "s1"
    assert len(store.list_audit_log(limit=1)) == 1
    assert store.log_action("") == 0


def test_audit_log_filters(store, monkeypatch):
    times = iter([
        "2026-01-01T09:00:00",
        "2026-02-15T10:00:00",
        "2026-03-20T11:30:00",
    ])
    monkeypatch.setattr(SessionStore, "_now", staticmethod(lambda: next(times)))
    store.log_action("session.delete", "s1", "用户删除")
    store.log_action("session.import", "backup.zip", "imported=2")
    store.log_action("session.delete", "s2", "批量删除")

    deleted = store.list_audit_log(action="session.delete")
    assert [item["target"] for item in deleted] == ["s2", "s1"]
    assert [item["target"] for item in store.list_audit_log(keyword="backup")] == ["backup.zip"]
    assert [item["target"] for item in store.list_audit_log(keyword="批量")] == ["s2"]
    assert [
        item["target"]
        for item in store.list_audit_log(start="2026-02-01", end="2026-02-28")
    ] == ["backup.zip"]
    assert [
        item["target"]
        for item in store.list_audit_log(start="2026-03-20T11:00:00")
    ] == ["s2"]
    assert [item["target"] for item in store.list_audit_log(end="2026-01-01")] == ["s1"]
    assert store.list_audit_log(action="missing") == []
    assert store.list_audit_log(keyword="不存在") == []
    assert store.list_audit_actions() == ["session.delete", "session.import"]


def test_reminders_crud(store):
    first = store.add_reminder("复查血常规", "2026-11-10", note="空腹")
    second = store.add_reminder(
        "内分泌复诊", "2026-11-05T09:30:00", session_id="s1"
    )

    assert first["done"] is False
    assert second["session_id"] == "s1"
    assert [item["title"] for item in store.list_reminders()] == [
        "内分泌复诊", "复查血常规",
    ]

    assert store.complete_reminder(first["id"]) is True
    assert [item["title"] for item in store.list_reminders()] == ["内分泌复诊"]
    assert len(store.list_reminders(include_done=True)) == 2
    assert store.complete_reminder(99999) is False

    assert store.reopen_reminder(first["id"]) is True
    assert len(store.list_reminders()) == 2
    assert store.reopen_reminder(99999) is False

    assert store.complete_reminder(first["id"]) is True
    assert store.delete_reminder(first["id"]) is True
    assert store.delete_reminder(first["id"]) is False
    assert len(store.list_reminders(include_done=True)) == 1

    with pytest.raises(ValueError):
        store.add_reminder("   ", "2026-11-10")
    with pytest.raises(ValueError):
        store.add_reminder("复查", "10/11/2026")
    with pytest.raises(ValueError):
        store.add_reminder("复查", "")


def test_reminders_profile_filter(store):
    store.add_reminder("妈妈复查", "2026-11-10", profile="母亲")
    store.add_reminder("孩子复诊", "2026-11-11", profile="孩子")
    store.add_reminder("通用提醒", "2026-11-12")

    assert [
        item["title"] for item in store.list_reminders(profile="母亲")
    ] == ["妈妈复查"]
    assert [
        item["title"] for item in store.list_reminders(profile="孩子")
    ] == ["孩子复诊"]
    assert len(store.list_reminders()) == 3
    assert store.list_reminders(profile="不存在") == []


def test_reminders_export_import(store, tmp_path):
    store.add_reminder(
        "复查", "2026-11-10", profile="母亲", advance_days=2,
        repeat_days=7, repeat_until="2027-01-01",
    )
    data = store.export_data()
    assert data["reminders"][0]["profile"] == "母亲"
    assert data["reminders"][0]["advance_days"] == 2
    assert data["reminders"][0]["repeat_days"] == 7
    assert data["reminders"][0]["repeat_until"] == "2027-01-01"

    other = SessionStore(str(tmp_path / "copy-reminders.db"))
    try:
        result = other.import_data(data)
        assert result["reminders"] == 1
        reminders = other.list_reminders(profile="母亲")
        assert reminders[0]["title"] == "复查"
        assert reminders[0]["due_at"] == "2026-11-10"
        assert reminders[0]["advance_days"] == 2
        assert reminders[0]["repeat_days"] == 7
        assert reminders[0]["repeat_until"] == "2027-01-01"

        again = other.import_data(data)
        assert again["reminders"] == 0
    finally:
        other.close()


def test_snooze_reminder(store):
    from datetime import date, timedelta

    reminder = store.add_reminder("复查", "2020-01-01")
    snoozed = store.snooze_reminder(reminder["id"], 7)

    assert snoozed["done"] is False
    assert snoozed["due_at"] == (
        date.today() + timedelta(days=7)
    ).isoformat()
    assert store.snooze_reminder(99999, 7) is None

    future = store.add_reminder("复诊", "2099-01-01")
    snoozed_future = store.snooze_reminder(future["id"], 7)
    assert snoozed_future["due_at"] == "2099-01-08"


def test_update_reminder(store):
    reminder = store.add_reminder("复查", "2026-11-10", note="空腹")
    updated = store.update_reminder(
        reminder["id"], title="复查肝功", due_at="2026-11-20"
    )
    assert updated["title"] == "复查肝功"
    assert updated["due_at"] == "2026-11-20"
    assert updated["note"] == "空腹"

    note_only = store.update_reminder(reminder["id"], note="带报告")
    assert note_only["note"] == "带报告"
    assert note_only["title"] == "复查肝功"

    assert store.update_reminder(99999, title="x") is None
    assert store.update_reminder(reminder["id"]) is None
    with pytest.raises(ValueError):
        store.update_reminder(reminder["id"], title="  ")
    with pytest.raises(ValueError):
        store.update_reminder(reminder["id"], due_at="2026/11/20")


def test_purge_sessions_before(store, monkeypatch):
    times = iter(["2026-01-01T09:00:00", "2026-01-02T09:00:00"])
    monkeypatch.setattr(SessionStore, "_now", staticmethod(lambda: next(times)))
    store.save_exchange("s1", "旧问题", "旧答复")
    store.save_exchange("s2", "新问题", "新答复")

    assert store.purge_sessions_before("") == {
        "sessions": 0, "messages": 0, "session_ids": [],
        "session_profiles": {},
    }
    result = store.purge_sessions_before("2026-01-01T12:00:00")

    assert result == {
        "sessions": 1, "messages": 2, "session_ids": ["s1"],
        "session_profiles": {"s1": ""},
    }
    assert [item["id"] for item in store.list_sessions()] == ["s2"]
    assert store.get_messages("s1") == []


def test_session_profile_filter(store):
    store.save_exchange(
        "s1", "妈妈的头痛", "答复一",
        metadata={"usage": {"total_tokens": 10}},
    )
    store.ensure_session("s1", "", "母亲")
    store.save_exchange(
        "s2", "孩子发烧", "答复二",
        metadata={"usage": {"total_tokens": 20}},
    )
    store.ensure_session("s2", "", "孩子")

    assert [item["id"] for item in store.list_sessions(profile="母亲")] == ["s1"]
    assert [item["id"] for item in store.list_sessions(profile="孩子")] == ["s2"]
    assert store.list_sessions(profile="不存在") == []
    assert store.count_sessions(profile="母亲") == 1
    assert store.usage_totals(profile="孩子")["usage"]["total_tokens"] == 20
    assert store.feedback_totals(profile="母亲")["total"] == 0
    assert store.get_session("s1")["profile"] == "母亲"

    # 已有档案不会被后续会诊覆盖
    store.ensure_session("s1", "", "其他")
    assert store.get_session("s1")["profile"] == "母亲"
    assert [item["id"] for item in store.list_sessions(profile="母亲")] == ["s1"]


def test_metric_targets_crud(store):
    assert store.list_metric_targets() == []

    target = store.set_metric_target("母亲", "bp", 130, 80)
    assert target["value1"] == 130
    assert target["value2"] == 80
    assert target["unit"] == "mmHg"

    updated = store.set_metric_target("母亲", "bp", 125, 75)
    assert updated["value1"] == 125
    assert len(store.list_metric_targets()) == 1
    assert store.list_metric_targets("母亲")[0]["value2"] == 75
    assert store.list_metric_targets("孩子") == []

    weight = store.set_metric_target("", "weight", 65)
    assert weight["value2"] is None
    assert len(store.list_metric_targets()) == 2

    assert store.set_metric_target("母亲", "bp", 80, 130) is None
    assert store.set_metric_target("母亲", "unknown", 1) is None
    assert store.set_metric_target("母亲", "weight", -1) is None

    assert store.delete_metric_target("母亲", "bp") is True
    assert store.delete_metric_target("母亲", "bp") is False
    assert len(store.list_metric_targets()) == 1


def test_metric_targets_export_import(store, tmp_path):
    store.set_metric_target("母亲", "bp", 130, 80)
    data = store.export_data()
    assert data["metric_targets"][0]["profile"] == "母亲"

    other = SessionStore(str(tmp_path / "copy.db"))
    try:
        result = other.import_data(data)
        assert result["targets"] == 1
        targets = other.list_metric_targets("母亲")
        assert targets[0]["value1"] == 130
        assert targets[0]["value2"] == 80

        again = other.import_data(data)
        assert again["targets"] == 0
    finally:
        other.close()


def test_session_tags_and_filter(store):
    store.save_exchange(
        "s1", "高血压复诊", "答复一",
        metadata={"usage": {"total_tokens": 10}, "llm_calls": 1},
    )
    store.save_exchange(
        "s2", "糖尿病饮食", "答复二",
        metadata={"usage": {"total_tokens": 20}, "llm_calls": 2},
    )

    assert store.set_tags("s1", ["高血压", "复诊"]) == ["高血压", "复诊"]
    assert store.set_tags("s2", ["糖尿病"]) == ["糖尿病"]
    assert store.set_tags("missing", ["x"]) is None

    tags = {item["name"]: item["count"] for item in store.list_tags()}
    assert tags == {"高血压": 1, "复诊": 1, "糖尿病": 1}

    assert [item["id"] for item in store.list_sessions(tag="高血压")] == ["s1"]
    assert store.count_sessions(tag="高血压") == 1
    assert store.usage_totals(tag="高血压")["usage"]["total_tokens"] == 10
    assert store.feedback_totals(tag="高血压")["total"] == 0

    normalized = store.set_tags(
        "s1", [" 高血压 ", "高血压", "a" * 30, "复诊", "", "糖尿病", "x", "y"]
    )
    assert normalized == ["高血压", "a" * 20, "复诊", "糖尿病", "x"]
    assert len(normalized) == 5
    assert len({tag.casefold() for tag in normalized}) == 5

    store.set_archived("s1", True)
    assert "高血压" not in {item["name"] for item in store.list_tags()}
    all_tags = {item["name"] for item in store.list_tags(archived=None)}
    assert "高血压" in all_tags
    assert [item["id"] for item in store.list_sessions(
        tag="高血压", archived=True
    )] == ["s1"]


def test_tags_survive_export_and_import(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_exchange("s1", "问题", "答复")
    source.set_tags("s1", ["高血压", "复诊"])
    data = source.export_data()
    source.close()

    assert data["sessions"][0]["tags"] == ["高血压", "复诊"]

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.import_data(data)
        assert target.list_sessions()[0]["tags"] == ["高血压", "复诊"]
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

def test_reminder_rejects_invalid_dates(store):
    with pytest.raises(ValueError):
        store.add_reminder("复查", "2026-02-30")
    with pytest.raises(ValueError):
        store.add_reminder("复查", "9999-12-31")
    with pytest.raises(ValueError):
        store.add_reminder("复查", "2026-13-01")

    reminder = store.add_reminder("复查", "2026-11-10T09:30:00")
    with pytest.raises(ValueError):
        store.update_reminder(reminder["id"], due_at="2026-02-30")


def test_snooze_preserves_time_of_day(store):
    reminder = store.add_reminder("复查", "2026-11-10T09:30:00")

    snoozed = store.snooze_reminder(reminder["id"], 7)

    assert snoozed["due_at"] == "2026-11-17T09:30:00"


def test_repeat_completion_inherits_medication_link(store):
    medication = store.add_medication("母亲", "氨氯地平")
    reminder = store.add_reminder(
        "用药：氨氯地平", "2026-11-05",
        profile="母亲", repeat_days=1,
        medication_id=medication["id"],
    )

    result = store.complete_reminder_with_repeat(reminder["id"])

    assert result["next"]["medication_id"] == medication["id"]
    assert result["next"]["title"] == "用药：氨氯地平"


def test_reminder_import_uses_stable_id(store, tmp_path):
    created = store.add_reminder("复查", "2026-11-10")
    data = store.export_data()

    other = SessionStore(str(tmp_path / "copy-reminder-id.db"))
    try:
        first = other.import_data(data)
        assert first["reminders"] == 1
        imported = other.list_reminders()[0]
        assert imported["id"] == created["id"]

        other.snooze_reminder(created["id"], 7)
        again = other.import_data(data)
        assert again["reminders"] == 0
        assert len(other.list_reminders()) == 1
    finally:
        other.close()


def test_rename_profile_conflicting_targets(store):
    store.save_profile("妈妈", {"age": "58"})
    store.set_metric_target("妈妈", "weight", 70)
    assert store.delete_profile("妈妈") is True

    store.save_profile("父亲", {"age": "60"})
    store.set_metric_target("父亲", "weight", 80)

    assert store.rename_profile("父亲", "妈妈") == "妈妈"
    targets = store.list_metric_targets("妈妈")
    assert len(targets) == 1
    assert targets[0]["value1"] == 80


def test_metric_timezone_is_normalized(store):
    from datetime import datetime

    item = store.add_metric(
        "", "weight", 60, measured_at="2026-01-01T00:00:00Z"
    )
    expected = (
        datetime.fromisoformat("2026-01-01T00:00:00+00:00")
        .astimezone()
        .replace(tzinfo=None)
        .isoformat(timespec="seconds")
    )
    assert item["measured_at"] == expected
    assert "+" not in item["measured_at"]


def test_reminders_advance_days(store):
    item = store.add_reminder("复查", "2026-11-10", advance_days=3)
    assert item["advance_days"] == 3
    assert store.get_reminder(item["id"])["advance_days"] == 3

    updated = store.update_reminder(item["id"], advance_days=5)
    assert updated["advance_days"] == 5
    assert store.snooze_reminder(item["id"])["advance_days"] == 5

    for bad in (-1, 31, "x"):
        with pytest.raises(ValueError):
            store.add_reminder("复查", "2026-11-10", advance_days=bad)
    with pytest.raises(ValueError):
        store.update_reminder(item["id"], advance_days=31)


def test_reminders_repeat_completion(store):
    from datetime import date

    reminder = store.add_reminder(
        "服药", "2020-01-01", repeat_days=7
    )
    result = store.complete_reminder_with_repeat(reminder["id"])
    assert result["completed"] is True
    next_item = result["next"]
    assert next_item is not None
    assert next_item["due_at"] > date.today().isoformat()
    assert next_item["repeat_days"] == 7
    assert next_item["repeats_from"] == reminder["id"]
    assert store.complete_reminder(reminder["id"]) is True
    pendings = store.list_reminders()
    assert [item["title"] for item in pendings] == ["服药"]
    # 已经完成过的提醒再次完成不会重复生成
    again = store.complete_reminder_with_repeat(reminder["id"])
    assert again["next"] is None
    assert len(store.list_reminders()) == 1

    # 恢复原提醒会删除自动生成的待办，再次完成可重新生成
    assert store.reopen_reminder(reminder["id"]) is True
    pending = store.list_reminders()
    assert [item["id"] for item in pending] == [reminder["id"]]
    regenerated = store.complete_reminder_with_repeat(reminder["id"])
    assert regenerated["next"] is not None
    assert len(store.list_reminders()) == 1

    limited = store.add_reminder(
        "限时", "2020-01-01", repeat_days=7, repeat_until="2020-01-08"
    )
    limited_result = store.complete_reminder_with_repeat(limited["id"])
    assert limited_result["next"] is None

    with pytest.raises(ValueError):
        store.add_reminder("复查", "2026-11-10", repeat_days=366)
    with pytest.raises(ValueError):
        store.add_reminder("复查", "2026-11-10", repeat_until="not-a-date")


def test_reminder_schema_migrates_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old-reminders.db"
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            CREATE TABLE reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                due_at TEXT NOT NULL,
                session_id TEXT NOT NULL DEFAULT '',
                profile TEXT NOT NULL DEFAULT '',
                done INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO reminders(title, due_at, created_at, updated_at)
            VALUES ('旧提醒', '2026-11-10', '2026-01-01', '2026-01-01');
            CREATE TABLE medications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                dose TEXT NOT NULL DEFAULT '',
                frequency TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            INSERT INTO medications(
                profile, name, dose, frequency, active,
                created_at, updated_at
            ) VALUES (
                '母亲', '氨氯地平', '5 mg', '每日一次', 1,
                '2026-01-01', '2026-01-01'
            );
            INSERT INTO reminders(
                title, due_at, profile, created_at, updated_at
            ) VALUES (
                '用药：氨氯地平', '2026-11-11', '母亲',
                '2026-01-01', '2026-01-01'
            );
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(str(path))
    try:
        items = store.list_reminders(include_done=True)
    finally:
        store.close()

    assert len(items) == 2
    legacy = next(item for item in items if item["title"] == "旧提醒")
    assert legacy["profile"] == ""
    assert legacy["advance_days"] == 0
    assert legacy["repeat_days"] == 0
    assert legacy["repeat_until"] == ""
    assert legacy["repeats_from"] == 0
    assert legacy["medication_id"] == 0

    linked = next(
        item for item in items if item["title"] == "用药：氨氯地平"
    )
    assert linked["profile"] == "母亲"
    assert linked["medication_id"] == 1


def test_symptoms_crud_and_export_import(store, tmp_path):
    item = store.add_symptom(
        "母亲", "头晕", 6, "2026-11-04T08:00", "起床后"
    )
    assert item["severity"] == 6
    assert item["profile"] == "母亲"
    assert [row["symptom"] for row in store.list_symptoms(profile="母亲")] == [
        "头晕"
    ]

    updated = store.update_symptom(
        item["id"], "母亲", "头晕", 4, "2026-11-05T08:00", ""
    )
    assert updated["severity"] == 4

    data = store.export_data()
    assert len(data["symptoms"]) == 1

    other = SessionStore(str(tmp_path / "symptoms-copy.db"))
    try:
        result = other.import_data({"symptoms": data["symptoms"]})
        assert result["symptoms"] == 1
        imported = other.list_symptoms()
        assert imported[0]["severity"] == 4
        assert imported[0]["note"] == ""
    finally:
        other.close()

    assert store.delete_symptom(item["id"]) is True
    assert store.delete_symptom(item["id"]) is False
    assert store.add_symptom("", "  ", 5) is None
    assert store.add_symptom("", "头晕", 11) is None
    assert store.add_symptom("", "头晕", 5, "not-a-date") is None


def test_symptom_time_is_canonical(store):
    item = store.add_symptom("", "头晕", 5, "2026-11-04 23:00")
    assert item["recorded_at"] == "2026-11-04T23:00"

    store.add_symptom("", "乏力", 3, "2026-11-04T01:00")
    items = store.list_symptoms()
    assert [row["symptom"] for row in items] == ["头晕", "乏力"]


def test_metric_time_is_canonical(store):
    first = store.add_metric(
        "", "weight", 60, measured_at="2026-11-04 23:00"
    )
    assert first["measured_at"] == "2026-11-04T23:00"

    store.add_metric("", "weight", 61, measured_at="2026-11-04T01:00")
    assert [row["value1"] for row in store.list_metrics()] == [60, 61]


def test_legacy_times_are_migrated(tmp_path):
    import sqlite3

    path = tmp_path / "legacy-times.db"
    store = SessionStore(str(path))
    store.add_metric("", "weight", 60, measured_at="2026-11-01T08:00")
    store.add_symptom("", "头晕", 5, "2026-11-01T08:00")
    store.close()

    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "UPDATE health_metrics SET measured_at = '2026-11-05 23:00'"
        )
        conn.execute(
            "UPDATE symptoms SET recorded_at = '2026-11-05 23:00'"
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(str(path))
    try:
        assert store.list_metrics()[0]["measured_at"] == "2026-11-05T23:00"
        assert store.list_symptoms()[0]["recorded_at"] == "2026-11-05T23:00"
    finally:
        store.close()


def test_medications_crud_and_export_import(store, tmp_path):
    item = store.add_medication(
        "母亲", "氨氯地平", "5 mg", "每日一次", "早餐后", True
    )
    assert item["active"] is True
    assert [row["name"] for row in store.list_medications(profile="母亲")] == [
        "氨氯地平"
    ]

    updated = store.update_medication(
        item["id"], "母亲", "氨氯地平", "5 mg", "每日一次", "", False
    )
    assert updated["active"] is False
    assert store.list_medications(active_only=True) == []
    assert len(store.list_medications(active_only=False)) == 1

    data = store.export_data()
    assert len(data["medications"]) == 1

    other = SessionStore(str(tmp_path / "medications-copy.db"))
    try:
        result = other.import_data(data)
        assert result["medications"] == 1
        imported = other.list_medications()
        assert imported[0]["name"] == "氨氯地平"
        assert imported[0]["active"] is False
    finally:
        other.close()

    assert store.delete_medication(item["id"]) is True
    assert store.delete_medication(item["id"]) is False
    assert store.add_medication("", "  ") is None


def test_medication_logs_crud_and_counts(store):
    item = store.add_medication("父亲", "二甲双胍", "500 mg", "每日两次")
    yesterday = (
        datetime.now() - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    first = store.add_medication_log(
        item["id"], f"{yesterday}T08:30", "早餐后"
    )
    second = store.add_medication_log(item["id"], f"{yesterday}T20:00")
    older = store.add_medication_log(item["id"], "2020-01-01T09:00")

    assert first["medication_id"] == item["id"]
    assert first["profile"] == "父亲"
    assert store.add_medication_log(99999) is None
    assert store.add_medication_log(item["id"], "not-a-time") is None

    logs = store.list_medication_logs(item["id"])
    assert [log["id"] for log in logs] == [
        second["id"], first["id"], older["id"]
    ]
    assert logs[0]["medication_name"] == "二甲双胍"
    assert store.list_medication_logs(profile="父亲") == logs

    listed = store.list_medications()[0]
    assert listed["today_count"] == 0
    assert listed["week_count"] == 2
    assert listed["last_taken_at"] == f"{yesterday}T20:00"

    assert store.get_medication_log(first["id"])["note"] == "早餐后"
    assert store.delete_medication_log(first["id"]) is True
    assert store.delete_medication_log(first["id"]) is False
    assert store.get_medication_log(99999) is None


def test_medication_logs_cascade_and_backup(store, tmp_path):
    item = store.add_medication("父亲", "氨氯地平", "5 mg", "每日一次")
    store.add_medication_log(item["id"], "2026-11-05T08:30:00", "早餐后")

    data = store.export_data()
    assert data["medication_logs"][0]["medication_name"] == "氨氯地平"

    other = SessionStore(str(tmp_path / "medication-logs-copy.db"))
    try:
        # 目标库先有一条无关药物，确保导入后 id 必然错位
        other.add_medication("其他人", "维生素D", "400 IU")
        result = other.import_data(data)
        assert result["medication_logs"] == 1
        imported = other.list_medication_logs()
        assert imported[0]["taken_at"] == "2026-11-05T08:30"
        copied_medication = other.list_medications(profile="父亲")[0]
        assert imported[0]["medication_id"] == copied_medication["id"]
        assert imported[0]["medication_id"] != item["id"]
        again = other.import_data(data)
        assert again["medication_logs"] == 0
    finally:
        other.close()

    assert store.delete_medication(item["id"]) is True
    assert store.list_medication_logs(item["id"]) == []


def test_medication_logs_ignore_future_dates_in_stats(store):
    item = store.add_medication("", "测试药")
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_medication_log(item["id"])
    assert store.add_medication_log(item["id"], "2099-01-01T09:00") is None

    listed = store.list_medications()[0]
    assert listed["today_count"] == 1
    assert listed["week_count"] == 1
    assert listed["last_taken_at"].startswith(today)


def test_update_medication_syncs_log_profile(store):
    item = store.add_medication("旧档案", "药A", "1 mg")
    store.add_medication_log(item["id"], "2026-11-01T08:00")

    updated = store.update_medication(
        item["id"], "新档案", "药A", "1 mg", "", "", True
    )
    assert updated["profile"] == "新档案"
    assert store.list_medication_logs(profile="旧档案") == []
    logs = store.list_medication_logs(profile="新档案")
    assert len(logs) == 1
    assert logs[0]["profile"] == "新档案"


def test_import_medication_logs_fallback_by_name(store, tmp_path):
    item = store.add_medication("父亲", "氨氯地平", "5 mg")
    store.add_medication_log(
        item["id"], "2026-11-05T08:30:00", "早餐后"
    )

    data = store.export_data()
    # 模拟外部编辑过的旧备份：去掉校验和后再改写药物编号
    data.pop("checksum", None)
    data["medication_logs"][0]["medication_id"] = 99999

    other = SessionStore(str(tmp_path / "log-fallback.db"))
    try:
        result = other.import_data(data)
        assert result["medication_logs"] == 1
        logs = other.list_medication_logs()
        assert logs[0]["medication_name"] == "氨氯地平"
        assert logs[0]["medication_id"] == other.list_medications()[0]["id"]
    finally:
        other.close()


def test_backup_keeps_same_minute_medication_logs(store, tmp_path):
    item = store.add_medication("父亲", "二甲双胍")
    store.add_medication_log(item["id"], "2026-11-05T10:30")
    store.add_medication_log(item["id"], "2026-11-05T10:30")

    data = store.export_data()
    other = SessionStore(str(tmp_path / "same-minute.db"))
    try:
        result = other.import_data(data)
        assert result["medication_logs"] == 2
        assert len(other.list_medication_logs()) == 2
        again = other.import_data(data)
        assert again["medication_logs"] == 0
    finally:
        other.close()


def test_backup_keeps_identical_timestamp_logs(store, tmp_path):
    item = store.add_medication("父亲", "二甲双胍")
    store.add_medication_log(item["id"], "2026-11-05T10:30")
    store.add_medication_log(item["id"], "2026-11-05T10:30")
    with store._lock, store._conn:
        store._conn.execute(
            """
            UPDATE medication_logs
               SET created_at = '2026-11-05T10:30:00'
             WHERE medication_id = ?
            """,
            (item["id"],),
        )

    data = store.export_data()
    other = SessionStore(str(tmp_path / "same-timestamp.db"))
    try:
        result = other.import_data(data)
        assert result["medication_logs"] == 2
        assert len(other.list_medication_logs()) == 2
        again = other.import_data(data)
        assert again["medication_logs"] == 0
    finally:
        other.close()


def test_update_medication_syncs_reminder_titles(store):
    item = store.add_medication("母亲", "氨氯地平", "5 mg")
    store.add_reminder(
        "用药：氨氯地平", "2026-11-06",
        profile="母亲", medication_id=item["id"],
    )

    updated = store.update_medication(
        item["id"], "母亲", "苯磺酸氨氯地平", "5 mg"
    )

    assert updated["name"] == "苯磺酸氨氯地平"
    titles = [
        reminder["title"]
        for reminder in store.list_reminders(profile="母亲")
    ]
    assert titles == ["用药：苯磺酸氨氯地平"]


def test_delete_medication_removes_pending_reminders(store):
    item = store.add_medication("母亲", "氨氯地平")
    store.add_reminder(
        "用药：氨氯地平", "2026-11-06",
        profile="母亲", medication_id=item["id"],
    )
    store.add_reminder("继续观察", "2026-11-07", profile="母亲")

    assert store.delete_medication(item["id"]) is True

    titles = [
        reminder["title"]
        for reminder in store.list_reminders(profile="母亲")
    ]
    assert titles == ["继续观察"]


def test_backup_preserves_reminder_medication_link(store, tmp_path):
    medication = store.add_medication("母亲", "氨氯地平")
    store.add_reminder(
        "用药：氨氯地平", "2026-11-06",
        profile="母亲", medication_id=medication["id"],
    )

    data = store.export_data()
    other = SessionStore(str(tmp_path / "reminder-link.db"))
    try:
        result = other.import_data(data)
        assert result["reminders"] == 1
        reminder = other.list_reminders(profile="母亲")[0]
        assert reminder["medication_id"] == other.list_medications()[0]["id"]
    finally:
        other.close()


def test_update_medication_keeps_custom_reminder_titles(store):
    item = store.add_medication("母亲", "氨氯地平")
    store.add_reminder(
        "早上吃降压药", "2026-11-06",
        profile="母亲", medication_id=item["id"],
    )

    store.update_medication(
        item["id"], "母亲", "苯磺酸氨氯地平", "5 mg"
    )

    reminder = store.list_reminders(profile="母亲")[0]
    assert reminder["title"] == "早上吃降压药"
    assert reminder["profile"] == "母亲"


def test_reminder_sync_only_touches_linked_medication(store):
    first = store.add_medication("母亲", "氨氯地平", "5 mg")
    second = store.add_medication("母亲", "氨氯地平", "10 mg")
    store.add_reminder(
        "用药：氨氯地平", "2026-11-06",
        profile="母亲", medication_id=first["id"],
    )
    store.add_reminder(
        "用药：氨氯地平", "2026-11-07",
        profile="母亲", medication_id=second["id"],
    )
    store.add_reminder("用药：氨氯地平", "2026-11-08", profile="母亲")

    store.update_medication(
        first["id"], "母亲", "苯磺酸氨氯地平", "5 mg"
    )
    reminders = {
        reminder["medication_id"]: reminder["title"]
        for reminder in store.list_reminders(profile="母亲")
    }
    assert reminders[first["id"]] == "用药：苯磺酸氨氯地平"
    assert reminders[second["id"]] == "用药：氨氯地平"
    assert reminders[0] == "用药：氨氯地平"

    assert store.delete_medication(first["id"]) is True
    remaining = [
        reminder["medication_id"]
        for reminder in store.list_reminders(profile="母亲")
    ]
    assert sorted(remaining) == [0, second["id"]]


def test_medication_import_skips_status_change(store):
    store.add_medication("母亲", "氨氯地平", "5 mg", "每日一次")
    backup = store.export_data()

    assert store.update_medication(
        1, "母亲", "氨氯地平", "5 mg", "每日一次", "", False
    )["active"] is False
    result = store.import_data(backup)

    assert result["medications"] == 0
    items = store.list_medications()
    assert len(items) == 1
    assert items[0]["active"] is False
