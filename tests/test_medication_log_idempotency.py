"""用药打卡的幂等性：重复请求不能重复扣库存。"""
from datetime import datetime, timedelta

from memory.session_store import SessionStore


def test_same_minute_log_is_idempotent(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        medication = store.add_medication(
            "妈妈", "氨氯地平", stock=10, stock_unit="片",
        )
        first = store.add_medication_log(medication["id"])
        second = store.add_medication_log(medication["id"])

        logs = store.list_medication_logs(medication["id"])
        refreshed = store.get_medication(medication["id"])
    finally:
        store.close()

    assert "duplicate" not in first
    assert first["stock_delta"] == -1
    assert second["duplicate"] is True
    assert second["id"] == first["id"]
    assert second["stock_delta"] == -1
    assert refreshed["stock"] == 9
    assert len(logs) == 1


def test_different_minute_log_is_recorded(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        medication = store.add_medication(
            "妈妈", "二甲双胍", stock=10, stock_unit="片",
        )
        first = store.add_medication_log(
            medication["id"], taken_at="2026-11-01T08:00",
        )
        second = store.add_medication_log(
            medication["id"], taken_at="2026-11-01T20:00",
        )

        logs = store.list_medication_logs(medication["id"])
        refreshed = store.get_medication(medication["id"])
    finally:
        store.close()

    assert first["id"] != second["id"]
    assert not second.get("duplicate")
    assert refreshed["stock"] == 8
    assert len(logs) == 2


def test_cross_minute_retry_is_idempotent(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        medication = store.add_medication(
            "妈妈", "氨氯地平", stock=10, stock_unit="片",
        )
        now = datetime.now()
        previous_minute = (now - timedelta(minutes=1)).isoformat(
            timespec="minutes"
        )
        with store._lock, store._conn:
            store._conn.execute(
                """
                INSERT INTO medication_logs(
                    medication_id, profile, taken_at, note,
                    stock_delta, skipped, skip_reason, created_at
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    medication["id"], "妈妈", previous_minute, "",
                    -1.0, 0, "",
                    (now - timedelta(seconds=90)).isoformat(
                        timespec="microseconds"
                    ),
                ),
            )
        duplicate = store.add_medication_log(medication["id"])
        logs = store.list_medication_logs(medication["id"])
        refreshed = store.get_medication(medication["id"])
    finally:
        store.close()

    # 跨分钟重试识别为同一次打卡，不新增记录也不再扣库存
    assert duplicate["duplicate"] is True
    assert len(logs) == 1
    assert refreshed["stock"] == 10
