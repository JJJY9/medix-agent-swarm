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
