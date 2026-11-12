"""疫苗接种/预约/化验/复查/就诊/症状 PATCH 的事务内合并测试。"""
import inspect
import threading

import api_server
from memory.session_store import SessionStore


def test_patch_endpoints_use_transactional_partial_updates():
    source = inspect.getsource(api_server)

    for name in (
        "update_vaccination_partial",
        "update_appointment_partial",
        "update_lab_result_partial",
        "update_care_item_partial",
        "update_visit_record_partial",
        "update_symptom_partial",
    ):
        assert f"store.{name}" in source, name
    # 不再在端点里用锁外快照拼整行写回
    assert "merged = {**existing, **_patch_payload(request)}" not in source


def test_vaccination_partial_patch_keeps_other_fields(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        row = store.add_vaccination(
            "妈妈", "乙肝疫苗", 1, "2026-01-01", "2027-01-01", "首剂",
        )
        updated = store.update_vaccination_partial(
            row["id"], note="已完成接种"
        )
    finally:
        store.close()

    assert updated["note"] == "已完成接种"
    assert updated["next_due"] == "2027-01-01"
    assert updated["vaccine_name"] == "乙肝疫苗"


def test_lab_and_visit_partial_patch_keep_other_fields(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        lab = store.add_lab_result(
            "妈妈", "血糖", 5.6, "mmol/L", 3.9, 6.1,
            tested_at="2026-11-01T08:00",
        )
        lab_updated = store.update_lab_result_partial(
            lab["id"], note="空腹"
        )
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-11-01", department="内科",
        )
        visit_updated = store.update_visit_record_partial(
            visit["id"], note="复查"
        )
    finally:
        store.close()

    assert lab_updated["note"] == "空腹"
    assert lab_updated["ref_low"] == 3.9
    assert lab_updated["ref_high"] == 6.1
    assert visit_updated["note"] == "复查"
    assert visit_updated["department"] == "内科"


def test_concurrent_vaccination_updates_do_not_overwrite(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    barrier = threading.Barrier(2)
    errors = []
    try:
        row = store.add_vaccination("妈妈", "乙肝疫苗", 1, "2026-01-01")

        def update_note():
            barrier.wait()
            try:
                store.update_vaccination_partial(
                    row["id"], note="社区医院"
                )
            except Exception as error:  # pragma: no cover
                errors.append(error)

        def update_due():
            barrier.wait()
            try:
                store.update_vaccination_partial(
                    row["id"], next_due="2028-01-01"
                )
            except Exception as error:  # pragma: no cover
                errors.append(error)

        threads = [
            threading.Thread(target=update_note),
            threading.Thread(target=update_due),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(5)

        final = store.get_vaccination(row["id"])
    finally:
        store.close()

    assert not errors
    assert final["note"] == "社区医院"
    assert final["next_due"] == "2028-01-01"
