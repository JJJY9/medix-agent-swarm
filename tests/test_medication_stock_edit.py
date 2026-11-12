"""用药编辑与历史库存扣减的回归测试。"""
from memory.session_store import SessionStore


def _medication(store):
    return store.add_medication(
        "妈妈", "氨氯地平", "5 mg", "每日一次",
        stock=10, stock_unit="片",
    )


def test_edit_with_same_stock_keeps_historical_delta(tmp_path):
    store = SessionStore(str(tmp_path / "same.db"))
    try:
        medication = _medication(store)
        log = store.add_medication_log(medication["id"])
        assert store.get_medication(medication["id"])["stock"] == 9

        store.update_medication(
            medication["id"], "妈妈", "氨氯地平", "5 mg", "每日一次",
            stock=9, stock_unit="片",
        )
        store.delete_medication_log(log["id"])

        assert store.get_medication(medication["id"])["stock"] == 10
    finally:
        store.close()


def test_manual_stock_change_resets_historical_deltas(tmp_path):
    store = SessionStore(str(tmp_path / "manual.db"))
    try:
        medication = _medication(store)
        log = store.add_medication_log(medication["id"])
        assert store.get_medication(medication["id"])["stock"] == 9

        store.update_medication(
            medication["id"], "妈妈", "氨氯地平", "5 mg", "每日一次",
            stock=20, stock_unit="片",
        )
        store.delete_medication_log(log["id"])

        assert store.get_medication(medication["id"])["stock"] == 20
    finally:
        store.close()
