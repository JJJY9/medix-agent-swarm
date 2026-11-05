"""数据库索引存在性测试：时间线、周报与用量查询有索引可用。"""
from memory.session_store import SessionStore

EXPECTED_INDEXES = (
    "idx_messages_role_created",
    "idx_sessions_created",
    "idx_sessions_profile_created",
    "idx_medication_logs_profile_time",
    "idx_symptoms_profile_time",
    "idx_metrics_profile_time",
)


def test_expected_indexes_are_created(tmp_path):
    store = SessionStore(str(tmp_path / "indexes.db"))
    try:
        names = {
            row[0]
            for row in store._conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
    finally:
        store.close()

    for expected in EXPECTED_INDEXES:
        assert expected in names, expected
