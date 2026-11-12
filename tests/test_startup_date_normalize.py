"""启动时的历史时间规范化回归测试。"""
from memory.session_store import SessionStore


def test_legacy_timestamps_are_normalized_on_open(tmp_path):
    path = tmp_path / "sessions.db"
    store = SessionStore(str(path))
    try:
        with store._lock, store._conn:
            store._conn.executemany(
                "INSERT INTO health_metrics("
                "profile, metric, value1, value2, unit, measured_at, "
                "context, note, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    ("妈妈", "weight", 60, None, "kg",
                     "2026-01-01 08:00", "", "",
                     "2026-01-01T08:00:00"),
                    ("妈妈", "weight", 61, None, "kg",
                     "2026-01-02", "", "",
                     "2026-01-02T08:00:00"),
                    ("妈妈", "weight", 62, None, "kg",
                     "2026-01-03T08:00", "", "",
                     "2026-01-03T08:00:00"),
                    ("妈妈", "weight", 63, None, "kg",
                     "2026-01-04T08:00:30", "", "",
                     "2026-01-04T08:00:30"),
                ],
            )
    finally:
        store.close()

    reopened = SessionStore(str(path))
    try:
        values = [
            row["measured_at"] for row in reopened.list_metrics("妈妈")
        ]
    finally:
        reopened.close()

    assert sorted(values) == [
        "2026-01-01T08:00",
        "2026-01-02T00:00",
        "2026-01-03T08:00",
        "2026-01-04T08:00:30",
    ]
