"""第二十七轮审查：备份导入配对、复诊联动与重复提醒回归测试。"""
from datetime import datetime, timedelta

import pytest

from memory.session_store import SessionStore, _next_repeat_due


@pytest.fixture
def store(tmp_path):
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def _twin_medication_backup():
    return {
        "medications": [
            {
                "id": 2, "profile": "妈妈", "name": "氨氯地平",
                "dose": "5mg", "frequency": "每日一次",
                "created_at": "2026-11-01T10:00:00",
            },
            {
                "id": 3, "profile": "妈妈", "name": "氨氯地平",
                "dose": "10mg", "frequency": "每日一次",
                "created_at": "2026-11-01T10:00:00",
            },
        ],
    }


def test_import_restores_deleted_twin_after_id_drift(store):
    # 目标库已有其它药物，导入后本地 id 整体后移
    for index in range(5):
        store.add_medication("爸爸", f"药物{index}", "1片", "每日一次")
    store.import_data(_twin_medication_backup())
    rows = store.list_medications("妈妈", False, 100)
    assert sorted(row["dose"] for row in rows) == ["10mg", "5mg"]

    five = next(row for row in rows if row["dose"] == "5mg")
    store.delete_medication(five["id"])

    # 备份主键已被其它药物占用，且一条同身份记录已被删除
    store.import_data(_twin_medication_backup())
    rows = store.list_medications("妈妈", False, 100)

    assert sorted(row["dose"] for row in rows) == ["10mg", "5mg"]


def _twin_reminder_backup():
    return {
        "reminders": [
            {
                "id": 1, "title": "吃降压药", "profile": "妈妈",
                "note": "早晚各一次", "due_at": "2026-11-13T08:00:00",
                "created_at": "2026-11-01T10:00:00",
            },
            {
                "id": 2, "title": "吃降压药", "profile": "妈妈",
                "note": "早晚各一次", "due_at": "2026-11-13T20:00:00",
                "created_at": "2026-11-01T10:00:00",
            },
        ],
    }


def test_import_restores_deleted_reminder_twin(store):
    store.import_data(_twin_reminder_backup())
    reminders = store.list_reminders(include_done=True)
    assert len(reminders) == 2

    evening = next(
        item for item in reminders
        if str(item["due_at"]).endswith("20:00:00")
    )
    store.delete_reminder(evening["id"])

    store.import_data(_twin_reminder_backup())
    reminders = store.list_reminders(include_done=True)

    assert len(reminders) == 2


def test_import_renamed_reminder_is_not_duplicated(store):
    backup = {
        "reminders": [{
            "id": 1, "title": "复查血压", "profile": "妈妈",
            "note": "", "due_at": "2026-12-01",
            "created_at": "2026-11-01T10:00:00",
        }],
    }
    store.import_data(backup)
    reminder = store.list_reminders(include_done=True)[0]
    store.update_reminder(reminder["id"], title="复查血压（心内科）")

    result = store.import_data(backup)

    assert result["reminders"] == 0
    titles = [
        item["title"] for item in store.list_reminders(include_done=True)
    ]
    assert titles == ["复查血压（心内科）"]


def test_import_after_profile_rename_merges_old_backup(store):
    medication = store.add_medication("妈妈", "二甲双胍", "500mg", "每日两次")
    backup = store.export_data()

    store.rename_profile("妈妈", "母亲")
    result = store.import_data(backup)

    assert result["medications"] == 0
    rows = store.list_medications(None, False, 100)
    assert [row["profile"] for row in rows] == ["母亲"]
    assert rows[0]["id"] == medication["id"]
    names = store.list_used_profile_names()
    assert "母亲" in names
    assert "妈妈" not in names


def test_update_appointment_keeps_postponed_reminder(store):
    appointment = store.add_appointment(
        "妈妈", "心内科复诊", scheduled_at="2026-12-01T09:00"
    )
    reminder, created = store.ensure_reminder(
        "就诊：心内科复诊", "2026-11-30", "", "", "妈妈",
        appointment_id=appointment["id"],
    )
    assert created is True
    store.update_reminder(reminder["id"], due_at="2026-12-08")

    store.update_appointment_partial(
        appointment["id"], note="带上次化验单"
    )

    postponed = store.get_reminder(reminder["id"])
    assert postponed["due_at"] == "2026-12-08"


def test_update_appointment_resyncs_after_reschedule(store):
    appointment = store.add_appointment(
        "妈妈", "心内科复诊", scheduled_at="2026-12-01T09:00"
    )
    reminder, _ = store.ensure_reminder(
        "就诊：心内科复诊", "2026-11-30", "", "", "妈妈",
        appointment_id=appointment["id"],
    )

    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-10T09:00"
    )

    assert store.get_reminder(reminder["id"])["due_at"] == "2026-12-09"


def test_update_vaccination_keeps_postponed_reminder(store):
    vaccination = store.add_vaccination(
        "妈妈", "流感疫苗", 1,
        vaccinated_at="2026-11-01", next_due="2027-11-01",
    )
    reminder, created = store.ensure_reminder(
        "接种：流感疫苗", "2027-11-01", "", "", "妈妈",
        vaccination_id=vaccination["id"],
    )
    assert created is True
    store.update_reminder(reminder["id"], due_at="2027-11-04")

    store.update_vaccination(
        vaccination["id"], "妈妈", "流感疫苗", 1,
        vaccinated_at="2026-11-01", next_due="2027-11-01",
        note="社区医院",
    )

    assert store.get_reminder(reminder["id"])["due_at"] == "2027-11-04"


def test_visit_followup_reschedule_replaces_old_schedule(store):
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01",
        next_followup_date="2026-12-13",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-13T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder, _ = store.ensure_reminder(
        "复诊：高血压", "2026-12-12", "", "", "妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )

    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-12"
    )

    assert store.get_appointment(appointment["id"]) is None
    assert store.get_reminder(reminder["id"]) is None


def test_delete_visit_record_removes_followup_schedule(store):
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01",
        next_followup_date="2026-12-13",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-13T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder, _ = store.ensure_reminder(
        "复诊：高血压", "2026-12-12", "", "", "妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )

    store.delete_visit_record(visit["id"])

    assert store.get_appointment(appointment["id"]) is None
    assert store.get_reminder(reminder["id"]) is None


def test_next_repeat_due_skips_time_already_passed():
    today = datetime.now().date()
    due = datetime.combine(
        today - timedelta(days=7), datetime.min.time()
    ).replace(hour=0, minute=0, second=0)
    due_text = due.isoformat(timespec="seconds")

    result = _next_repeat_due(due_text, 7)

    assert datetime.fromisoformat(result) > datetime.now()


def test_lab_result_local_edit_survives_restore(store):
    lab = store.add_lab_result(
        "妈妈", "ALT", 55, "U/L", tested_at="2026-11-01", panel="肝功能",
    )
    backup = store.export_data()

    store.update_lab_result(
        lab["id"], "妈妈", "ALT", 60, "U/L",
        tested_at="2026-11-01", panel="肝功能",
    )
    result = store.import_data(backup)

    assert result["lab_results"] == 0
    rows = store.list_lab_results("妈妈")
    assert [row["value"] for row in rows] == [60]


def test_lab_result_deleted_restore_readds_backup_row(store):
    store.add_lab_result(
        "妈妈", "ALT", 55, "U/L", tested_at="2026-11-01", panel="肝功能",
    )
    backup = store.export_data()
    for row in store.list_lab_results("妈妈"):
        store.delete_lab_result(row["id"])

    result = store.import_data(backup)

    assert result["lab_results"] == 1
    assert [
        row["value"] for row in store.list_lab_results("妈妈")
    ] == [55]


def test_completed_care_reminder_does_not_spawn_repeat(store):
    reminder = store.add_reminder(
        "复查：眼底检查", "2026-12-06", "", "", "妈妈",
        repeat_days=7,
    )
    store.finish_reminder(reminder["id"])

    pending = store.list_reminders(False, "妈妈")

    assert pending == []
