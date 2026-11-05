"""记忆核心模块边界的回归测试（离线）。"""
import json
from datetime import datetime, timedelta, timezone

from core.state_manager import StateManager, TaskStatus
from memory import ShortTermMemory
from memory.entropy_manager import MemoryEntropyManager
from memory.session_summary import SessionSummary
from swarm.shared_context import SharedContext, SubTask


def test_compressed_history_has_no_gap():
    manager = MemoryEntropyManager()
    messages = []
    for i in range(15):
        messages.append({"role": "user", "content": f"问 {i}"})
        messages.append({"role": "assistant", "content": f"答 {i}"})

    compressed = manager.compress_session_history(messages, max_messages=10)

    assert len(compressed) <= 10
    assert compressed[0]["role"] == "system"
    # 边界消息（第 21 条，即「问 10」）必须进入摘要，不能两头都丢
    assert "问 10" in compressed[0]["content"]
    # 摘要之后从用户消息开始，避免模型看到没有提问的回答
    assert compressed[1]["role"] == "user"
    assert compressed[-1]["content"] == "答 14"


def test_cleanup_old_memories_handles_aware_timestamps():
    manager = MemoryEntropyManager()
    old = datetime.now(timezone.utc) - timedelta(days=365)
    recent = datetime.now(timezone.utc) - timedelta(days=1)

    cleaned = manager.cleanup_old_memories([
        {"session_id": "old", "timestamp": old.isoformat()},
        {"session_id": "new", "timestamp": recent.isoformat()},
    ])

    assert [item["session_id"] for item in cleaned] == ["new"]


def test_state_manager_update_state_converts_status():
    manager = StateManager()
    manager.create_state("t1", "agent", {}, 3)

    assert manager.update_state("t1", status="completed") is True
    state = manager.get_state("t1")
    assert state.status is TaskStatus.COMPLETED
    assert state.is_completed() is True

    # 不允许覆盖方法，也不接受非法状态
    manager.update_state("t1", is_completed=True, status="bogus")
    assert callable(state.is_completed)
    assert state.status is TaskStatus.COMPLETED

    assert manager.update_state("missing", status="completed") is False


class BrokenRedis:
    def get(self, key):
        raise RuntimeError("redis down")

    def setex(self, *args, **kwargs):
        raise AssertionError("读取失败时不应写入")


def test_redis_load_failure_does_not_overwrite_history():
    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "redis"
    memory.redis_client = BrokenRedis()
    session_id = "redis-broken-test"
    memory.sessions.pop(session_id, None)
    try:
        memory.add_message(session_id, "user", "内容")
        # 读取失败时不写 Redis（setex 会断言失败），但保留进程内副本
        assert session_id in memory.sessions
        assert memory.sessions[session_id].messages[0]["content"] == "内容"
    finally:
        memory.storage_type = "memory"
        memory.redis_client = None


class BrokenRedisDelete:
    def delete(self, key):
        raise RuntimeError("redis down")

    def get(self, key):
        raise RuntimeError("redis down")

    def setex(self, *args, **kwargs):
        raise AssertionError("读取失败时不应写入")


class StaleRedis:
    """删除失败但读取仍可用，Redis 中残留旧会话。"""

    def __init__(self, value):
        self.value = value
        self.set_calls = []

    def get(self, key):
        return self.value

    def setex(self, key, ttl, value):
        self.value = value
        self.set_calls.append(value)

    def delete(self, key):
        raise RuntimeError("redis delete failed")


class StaleRedisSetex(StaleRedis):
    def setex(self, key, ttl, value):
        raise RuntimeError("redis setex failed")


def test_cleared_session_does_not_resurrect_when_redis_write_fails():
    from memory.short_term import ConversationHistory

    old = ConversationHistory(session_id="redis-write-failure")
    old.add_message("user", "SECRET_DELETED_QUESTION")
    old.add_message("assistant", "SECRET_DELETED_ANSWER")
    stale = StaleRedisSetex(json.dumps(old.to_dict()))

    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "redis"
    memory.redis_client = stale
    session_id = "redis-write-failure"
    memory.sessions.pop(session_id, None)
    memory._cleared_sessions.discard(session_id)
    try:
        assert memory.clear_session(session_id) is False
        assert memory.get_session(session_id) is None

        memory.add_message(session_id, "user", "NEW_QUESTION")

        history = memory.get_session(session_id)
        assert history is not None
        assert [item["content"] for item in history.messages] == [
            "NEW_QUESTION"
        ]
        recent = memory.get_recent_messages(session_id, limit=10)
        assert all(
            "SECRET_DELETED" not in item["content"] for item in recent
        )
    finally:
        memory.storage_type = "memory"
        memory.redis_client = None
        memory.sessions.pop(session_id, None)
        memory._cleared_sessions.discard(session_id)


def test_cleared_session_does_not_merge_stale_redis_history():
    from memory.short_term import ConversationHistory

    old = ConversationHistory(session_id="redis-stale-history")
    old.add_message("user", "SECRET_OLD_QUESTION")
    old.add_message("assistant", "SECRET_OLD_ANSWER")
    stale = StaleRedis(json.dumps(old.to_dict()))

    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "redis"
    memory.redis_client = stale
    session_id = "redis-stale-history"
    memory.sessions.pop(session_id, None)
    memory._cleared_sessions.discard(session_id)
    try:
        assert memory.clear_session(session_id) is False
        assert memory.get_session(session_id) is None

        memory.add_message(session_id, "user", "NEW_QUESTION")

        assert "SECRET_OLD_QUESTION" not in stale.value
        assert "SECRET_OLD_ANSWER" not in stale.value
        assert "NEW_QUESTION" in stale.value
        history = memory.get_session(session_id)
        assert history is not None
        assert [item["content"] for item in history.messages] == [
            "NEW_QUESTION"
        ]
    finally:
        memory.storage_type = "memory"
        memory.redis_client = None
        memory.sessions.pop(session_id, None)
        memory._cleared_sessions.discard(session_id)


def test_cleared_session_survives_redis_delete_failure():
    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "redis"
    memory.redis_client = BrokenRedisDelete()
    session_id = "redis-clear-failure"
    memory.sessions.pop(session_id, None)
    memory._cleared_sessions.discard(session_id)
    try:
        memory.add_message(session_id, "user", "旧内容")
        assert memory.clear_session(session_id) is False
        # 删除失败时进程内仍视为已清空
        assert memory.get_session(session_id) is None
        # 新消息应解除墓碑，允许同一会话继续
        memory.add_message(session_id, "user", "新内容")
        history = memory.get_session(session_id)
        assert history is not None
        assert [item["content"] for item in history.messages] == ["新内容"]
    finally:
        memory.storage_type = "memory"
        memory.redis_client = None
        memory.sessions.pop(session_id, None)
        memory._cleared_sessions.discard(session_id)


def test_cleared_session_tombstones_are_capped():
    memory = ShortTermMemory(storage_type="memory")
    memory.storage_type = "memory"
    memory._cleared_sessions.clear()
    try:
        for index in range(1005):
            memory.clear_session(f"tombstone-cap-{index}")
        assert len(memory._cleared_sessions) <= 1000
    finally:
        memory._cleared_sessions.clear()


def test_session_summary_uses_real_tool_calls_and_subtask_time():
    context = SharedContext(session_id="s1")
    subtask = SubTask(
        id="t1", type="diagnosis", description="诊断",
        assigned_agent="diagnostic_agent",
    )
    context.add_subtask(subtask)
    subtask.started_at = datetime(2026, 1, 1, 10, 0, 0)
    context.complete_subtask(
        "t1", "diagnostic_agent",
        {"success": True, "tool_calls_used": 2},
    )
    subtask.completed_at = datetime(2026, 1, 1, 10, 0, 30)

    summary = SessionSummary.from_shared_context(
        session_id="s1",
        question="问题",
        shared_context=context,
        final_answer="答复",
        start_time=datetime(2026, 1, 1, 9, 59, 0),
        end_time=datetime(2026, 1, 1, 10, 1, 0),
    )

    participation = summary.agents_participated[0]
    assert participation.tool_calls == 2
    assert participation.execution_time == 30.0
