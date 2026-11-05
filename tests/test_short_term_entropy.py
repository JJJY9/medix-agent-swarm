"""短期记忆熵压缩可达性测试。"""
from memory import ShortTermMemory


def test_recent_messages_compresses_long_history():
    memory = ShortTermMemory(storage_type="memory")
    session_id = "entropy-window-test"
    memory.clear_session(session_id)

    for i in range(40):
        memory.add_message(session_id, "user", f"问 {i}")
        memory.add_message(session_id, "assistant", f"答 {i}")

    messages = memory.get_recent_messages(session_id, limit=10)

    assert len(messages) == 10
    assert messages[0]["role"] == "system"
    assert "历史摘要" in messages[0]["content"]
    assert messages[-1]["content"] == "答 39"
    memory.clear_session(session_id)


def test_recent_messages_short_history_unchanged():
    memory = ShortTermMemory(storage_type="memory")
    session_id = "entropy-short-test"
    memory.clear_session(session_id)
    memory.add_message(session_id, "user", "头痛怎么办")
    memory.add_message(session_id, "assistant", "建议休息")

    messages = memory.get_recent_messages(session_id, limit=10)

    assert [m["role"] for m in messages] == ["user", "assistant"]
    memory.clear_session(session_id)
