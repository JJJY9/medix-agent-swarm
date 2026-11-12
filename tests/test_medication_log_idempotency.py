"""用药打卡的幂等性：重复请求不能重复扣库存。"""
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
