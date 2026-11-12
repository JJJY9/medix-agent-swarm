"""指标保存重试窗口的回归测试。"""
from datetime import datetime, timedelta

from memory.session_store import SessionStore


def test_adjacent_minute_same_value_is_new_reading(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.add_metric("妈妈", "glucose", 6.0)
        # 模拟一分钟前的合法复测：创建时间已超过 30 秒重试窗口
        old_created = (
            datetime.now() - timedelta(seconds=60)
        ).isoformat(timespec="microseconds")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE health_metrics SET created_at = ? WHERE id = ?",
                (old_created, first["id"]),
            )
        second = store.add_metric("妈妈", "glucose", 6.0)
        rows = store.list_metrics("妈妈")
    finally:
        store.close()

    assert not second.get("duplicate")
    assert second["id"] != first["id"]
    assert len(rows) == 2


def test_immediate_retry_is_still_deduplicated(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.add_metric("妈妈", "weight", 60)
        second = store.add_metric("妈妈", "weight", 60)
    finally:
        store.close()

    assert second["duplicate"] is True
    assert second["id"] == first["id"]
