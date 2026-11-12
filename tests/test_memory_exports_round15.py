"""memory 包公开导出的回归测试。"""


def test_memory_star_import_has_no_missing_names():
    namespace = {}
    exec("from memory import *", namespace)

    assert "SessionStore" in namespace
    assert "LongTermMemory" in namespace
    assert "LearningRecord" not in namespace
