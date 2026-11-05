"""记忆压缩、身份、用量与保留策略的回归测试（全部离线）。"""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest

import api_server
from core.pricing import get_pricing
from core.usage import merge_usage
from memory import ShortTermMemory
from memory.agent_identity import AgentIdentity, AgentIdentityManager
from memory.entropy_manager import MemoryEntropyManager
from memory.session_store import SessionStore
from memory.session_summary import SessionSummary
from swarm.shared_context import SharedContext, SubTask


def test_get_history_keeps_summary_and_starts_with_user():
    memory = ShortTermMemory(storage_type="memory")
    session_id = "summary-history-test"
    memory.clear_session(session_id)
    try:
        for i in range(20):
            memory.add_message(session_id, "user", f"问 {i}")
            memory.add_message(session_id, "assistant", f"答 {i}")

        history = memory.get_history(session_id, limit=5)

        assert history[0]["role"] == "system"
        assert "历史摘要" in history[0]["content"]
        assert history[1]["role"] == "user"
        assert history[-1]["content"] == "答 19"
    finally:
        memory.clear_session(session_id)


def test_compression_keeps_tail_when_window_has_no_user():
    manager = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "原始问题"},
        {"role": "assistant", "content": "原始回答"},
    ] + [
        {"role": "assistant", "content": f"补充 {i}"} for i in range(12)
    ]

    compressed = manager.compress_session_history(messages, max_messages=10)

    assert len(compressed) == 10
    assert compressed[0]["role"] == "system"
    assert compressed[-1]["content"] == "补充 11"


def test_get_history_keeps_tail_assistant_without_user():
    memory = ShortTermMemory(storage_type="memory")
    session_id = "tail-assistant-test"
    memory.clear_session(session_id)
    try:
        memory.add_message(session_id, "user", "原始问题")
        memory.add_message(session_id, "assistant", "原始回答")
        for i in range(12):
            memory.add_message(session_id, "assistant", f"补充 {i}")

        history = memory.get_history(session_id, limit=5)

        assert history[0]["role"] == "system"
        assert any("补充 11" in message["content"] for message in history)
    finally:
        memory.clear_session(session_id)


def test_compression_keeps_tool_turn_final_answer_and_tool_names():
    manager = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "问 1"},
        {"role": "assistant", "content": "调用工具：search_knowledge"},
        {"role": "tool", "content": "search_knowledge: {...}"},
        {"role": "assistant", "content": "最终答复 1"},
        {"role": "user", "content": "问 2"},
        {"role": "assistant", "content": "答复 2"},
        {"role": "user", "content": "问 3"},
        {"role": "assistant", "content": "答复 3"},
    ]

    compressed = manager.compress_session_history(messages, max_messages=4)

    summary = compressed[0]["content"]
    assert "问 1" in summary
    assert "最终答复 1" in summary
    assert "search_knowledge" in summary
    assert "问 2" in summary
    assert compressed[1]["role"] == "user"


def test_compression_keeps_consecutive_user_messages():
    manager = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "问题 A"},
        {"role": "user", "content": "问题 B"},
        {"role": "assistant", "content": "答复 B"},
        {"role": "user", "content": "问题 C"},
        {"role": "assistant", "content": "答复 C"},
    ]

    compressed = manager.compress_session_history(messages, max_messages=4)

    summary = compressed[0]["content"]
    assert "问题 A" in summary
    assert "问题 B" in summary
    assert "答复 B" in summary


def test_cleanup_old_memories_rejects_invalid_age_and_parses_z():
    manager = MemoryEntropyManager()
    old = (
        datetime.now(timezone.utc) - timedelta(days=365)
    ).strftime("%Y-%m-%dT%H:%M:%SZ")
    recent = (
        datetime.now(timezone.utc) - timedelta(days=1)
    ).isoformat()

    cleaned = manager.cleanup_old_memories([
        {"session_id": "old", "timestamp": old},
        {"session_id": "new", "timestamp": recent},
    ])

    assert [item["session_id"] for item in cleaned] == ["new"]
    with pytest.raises(ValueError):
        manager.cleanup_old_memories(
            [{"session_id": "new", "timestamp": recent}],
            max_age_days=-1,
        )
    with pytest.raises(ValueError):
        manager.cleanup_old_memories(
            [{"session_id": "new", "timestamp": recent}],
            max_age_days=0,
        )


def test_identity_roundtrip_and_updates_preserve_fields(tmp_path):
    manager = AgentIdentityManager(str(tmp_path))
    identity = AgentIdentity(
        agent_id="diagnostic_agent",
        agent_type="diagnostic",
        core_capabilities=["症状分析"],
        expertise_domains=["神经系统"],
    )
    identity.update_tool_stats("search_knowledge", True, 1.0)
    identity.created_at = datetime(2020, 5, 6, 7, 8, 9)
    identity.last_updated = datetime(2000, 1, 1)
    manager.save_identity(identity)

    loaded = manager.load_identity("diagnostic_agent")
    assert loaded is not None
    assert loaded.agent_type == "diagnostic"
    assert loaded.core_capabilities == ["症状分析"]
    assert loaded.expertise_domains == ["神经系统"]
    assert loaded.tool_usage_stats[0].tool_name == "search_knowledge"
    assert loaded.created_at == datetime(2020, 5, 6, 7, 8, 9)
    assert loaded.last_updated == datetime(2000, 1, 1)

    manager.update_tool_stats(
        "diagnostic_agent", "search_knowledge", True, 2.0
    )
    reloaded = manager.load_identity("diagnostic_agent")

    assert reloaded.tool_usage_stats[0].usage_count == 2
    assert reloaded.last_updated > datetime(2000, 1, 1)


def test_identity_rejects_path_traversal(tmp_path):
    manager = AgentIdentityManager(str(tmp_path))

    for agent_id in ("../escape", "C:temp", "D:foo"):
        with pytest.raises(ValueError):
            manager.load_identity(agent_id)


def test_session_summary_keeps_real_zero_tool_calls():
    context = SharedContext(session_id="zero-tools")
    subtask = SubTask(
        id="t1", type="diagnosis", description="诊断",
        assigned_agent="diagnostic_agent",
    )
    context.add_subtask(subtask)
    context.complete_subtask(
        "t1", "diagnostic_agent",
        {"success": True, "tool_calls_used": 0},
    )

    summary = SessionSummary.from_shared_context(
        session_id="zero-tools",
        question="问题",
        shared_context=context,
        final_answer="答复",
        start_time=datetime(2026, 1, 1, 9, 59, 0),
        end_time=datetime(2026, 1, 1, 10, 1, 0),
    )

    assert summary.agents_participated[0].tool_calls == 0


def test_merge_usage_derives_total_from_prompt_and_completion():
    total = merge_usage(
        {}, {"prompt_tokens": 1200, "completion_tokens": 800}
    )
    assert total["total_tokens"] == 2000

    total = merge_usage(
        {}, {
            "prompt_tokens": 1200,
            "completion_tokens": 800,
            "total_tokens": "2000.0",
        }
    )
    assert total["total_tokens"] == 2000


def test_store_usage_totals_derives_missing_total(tmp_path):
    store = SessionStore(str(tmp_path / "usage.db"))
    try:
        store.save_exchange(
            "s1", "问题", "答复",
            metadata={"usage": {"prompt_tokens": 1200, "completion_tokens": 800}},
        )
        store.save_exchange(
            "s2", "问题", "答复",
            metadata={"usage": {
                "prompt_tokens": 1200,
                "completion_tokens": 800,
                "total_tokens": "2000.0",
            }},
        )
        totals = store.usage_totals()
        assert totals["usage"]["total_tokens"] == 4000
    finally:
        store.close()


def test_purge_uses_datetime_comparison_for_space_timestamps(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "purge.db"))
    times = iter(["2026-10-06T20:00:00", "2026-10-07 20:00:00"])
    monkeypatch.setattr(
        SessionStore, "_now", staticmethod(lambda: next(times))
    )
    try:
        store.save_exchange("old", "旧问题", "旧答复")
        store.save_exchange("legacy", "新问题", "新答复")

        result = store.purge_sessions_before("2026-10-07T14:00:00")

        assert result == {"sessions": 1, "messages": 2}
        assert [item["id"] for item in store.list_sessions()] == ["legacy"]
        assert store.get_messages("old") == []
        assert len(store.get_messages("legacy")) == 2
    finally:
        store.close()


def test_purge_cleans_orphan_messages(tmp_path):
    store = SessionStore(str(tmp_path / "orphan.db"))
    try:
        store.save_exchange("s1", "问题", "答复")
        store._conn.execute("DELETE FROM sessions WHERE id = 's1'")
        store._conn.commit()

        result = store.purge_sessions_before("2999-01-01T00:00:00")

        assert result == {"sessions": 0, "messages": 2}
    finally:
        store.close()


def test_pricing_rejects_partially_invalid_configuration(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "bad")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")

    assert get_pricing() is None


def test_pricing_rejects_negative_price(monkeypatch):
    monkeypatch.setenv("MEDIX_PRICE_INPUT", "-5")
    monkeypatch.setenv("MEDIX_PRICE_OUTPUT", "8")

    assert get_pricing() is None


def test_auto_archive_loop_runs_periodically(monkeypatch):
    calls = []

    async def fake_archive():
        calls.append(1)

    monkeypatch.setattr(api_server, "auto_archive_sessions", fake_archive)
    monkeypatch.setattr(
        api_server, "AUTO_ARCHIVE_INTERVAL_SECONDS", 0.01
    )

    async def run_until_timeout():
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(
                api_server.auto_archive_loop(), timeout=0.1
            )

    asyncio.run(run_until_timeout())

    assert len(calls) >= 2
