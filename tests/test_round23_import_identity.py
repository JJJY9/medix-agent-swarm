"""第二十三轮审查：恢复备份不会复活本地已编辑的医疗记录。"""
import pytest

from memory.session_store import SessionStore


@pytest.fixture
def store(tmp_path):
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


def test_restore_after_local_edits_does_not_duplicate(store):
    medication = store.add_medication("妈妈", "氨氯地平", "5mg", "每日一次")
    appointment = store.add_appointment(
        "妈妈", "心内科复诊", "心内科", scheduled_at="2026-12-01 09:00",
    )
    care = store.add_care_item("妈妈", "糖化血红蛋白", 90)
    condition = store.add_condition(
        "妈妈", "高血压", diagnosed_at="2026-01-01"
    )
    allergy = store.add_allergy("妈妈", "青霉素", reaction="皮疹")
    symptom = store.add_symptom(
        "妈妈", "头痛", 3, recorded_at="2026-11-12"
    )

    backup = store.export_data()

    # 备份后本地编辑：停药、完成预约、完成复查、慢病转已控制、反应升级
    store.update_medication(
        medication["id"], active=False,
        stopped_at="2026-11-13", stop_reason="医嘱停用",
    )
    store.update_appointment_partial(appointment["id"], status="completed")
    store.update_care_item_partial(
        care["id"], last_done_at="2026-11-13",
        next_due_at="2027-02-11",
    )
    store.update_condition(condition["id"], status="controlled")
    store.update_allergy(
        allergy["id"], reaction="过敏性休克", severity="severe"
    )
    store.update_symptom_partial(symptom["id"], severity=8, note="加重")

    result = store.import_data(backup)

    assert result["medications"] == 0
    assert result["appointments"] == 0
    assert result["care_items"] == 0
    assert result["conditions"] == 0
    assert result["allergies"] == 0
    assert result["symptoms"] == 0

    # 本地编辑后的状态保留，且没有出现重复记录
    meds = store.list_medications("妈妈")
    assert len(meds) == 1
    assert meds[0]["active"] in (0, False)

    appointments = store.list_appointments("妈妈")
    assert len(appointments) == 1
    assert appointments[0]["status"] == "completed"

    care_items = store.list_care_items("妈妈")
    assert len(care_items) == 1
    assert care_items[0]["next_due_at"] == "2027-02-11"

    conditions = store.list_conditions("妈妈")
    assert len(conditions) == 1
    assert conditions[0]["status"] == "controlled"

    allergies = store.list_allergies("妈妈")
    assert len(allergies) == 1
    assert allergies[0]["reaction"] == "过敏性休克"

    symptoms = store.list_symptoms("妈妈")
    assert len(symptoms) == 1
    assert symptoms[0]["severity"] == 8


def test_import_preserves_same_second_duplicates(store, tmp_path):
    store.add_medication("妈妈", "维生素D", "1粒", "每日一次")
    store.add_medication("妈妈", "维生素D", "1粒", "每日一次")

    backup = store.export_data()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        first = target.import_data(backup)
        assert first["medications"] == 2
        assert len(target.list_medications("妈妈")) == 2

        # 同一备份重复导入保持幂等
        again = target.import_data(backup)
        assert again["medications"] == 0
        assert len(target.list_medications("妈妈")) == 2
    finally:
        target.close()
