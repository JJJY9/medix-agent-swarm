"""长期记忆检索结果去重的离线测试。"""
from memory.entropy_manager import MemoryEntropyManager


def test_deduplicate_sessions_uses_content_field():
    manager = MemoryEntropyManager()
    sessions = [
        {"memory_id": "1", "content": "高血压生活方式建议", "score": 0.9},
        {"memory_id": "2", "content": "糖尿病饮食建议", "score": 0.8},
        {"memory_id": "3", "content": "高血压生活方式建议", "score": 0.7},
    ]

    unique = manager.deduplicate_sessions(sessions)

    assert [item["memory_id"] for item in unique] == ["1", "2"]


def test_deduplicate_sessions_falls_back_to_question_summary():
    manager = MemoryEntropyManager()
    sessions = [
        {"session_id": "a", "question": "头痛", "summary": "建议监测"},
        {"session_id": "b", "question": "头痛", "summary": "建议监测"},
        {"session_id": "c", "question": "胸痛", "summary": "急诊"},
    ]

    unique = manager.deduplicate_sessions(sessions)

    assert [item["session_id"] for item in unique] == ["a", "c"]


def test_deduplicate_messages_keeps_repeated_questions():
    manager = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "高血压怎么办？", "timestamp": "2026-01-01"},
        {"role": "assistant", "content": "建议低盐饮食。", "timestamp": "2026-01-01"},
        {"role": "user", "content": "高血压怎么办？", "timestamp": "2026-02-01"},
    ]

    unique = manager.deduplicate_messages(messages)

    # 不同时间重复提问是真实的新轮次：删掉较早的用户消息会让
    # 它对应的回答变成没有提问的孤儿
    assert [item["role"] for item in unique] == [
        "user", "assistant", "user",
    ]
    assert unique[-1]["timestamp"] == "2026-02-01"


def test_compress_session_history_respects_max_messages():
    manager = MemoryEntropyManager()
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"问题 {i}"})
        messages.append({"role": "assistant", "content": f"回答 {i}"})

    compressed = manager.compress_session_history(messages, max_messages=10)

    assert len(compressed) <= 10
    assert compressed[0]["role"] == "system"
    assert "历史摘要" in compressed[0]["content"]
    assert "问题 0" in compressed[0]["content"]
    # 最近窗口必须从用户消息开始，不能以 assistant 回答开头
    assert compressed[1]["role"] == "user"
    assert compressed[-1]["content"] == "回答 19"


def test_compress_keeps_unpaired_trailing_user_message():
    manager = MemoryEntropyManager()
    messages = []
    for i in range(5):
        messages.append({"role": "user", "content": f"问题 {i}"})
        messages.append({"role": "assistant", "content": f"回答 {i}"})
    messages.append({"role": "user", "content": "最后一个问题"})

    compressed = manager.compress_session_history(messages, max_messages=4)

    assert compressed[0]["role"] == "system"
    assert "问题 3" in compressed[0]["content"]
    assert compressed[-1]["content"] == "最后一个问题"
