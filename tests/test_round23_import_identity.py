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


def test_restore_after_identity_field_edits_does_not_duplicate(store):
    """剂量/预约时间等可编辑字段改动后恢复，不能复活旧记录。"""
    medication = store.add_medication("妈妈", "氨氯地平", "5mg", "每日一次")
    appointment = store.add_appointment(
        "妈妈", "心内科复诊", "心内科", scheduled_at="2026-12-01 09:00",
    )
    backup = store.export_data()

    store.update_medication(medication["id"], dose="10mg")
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-08 09:00",
    )

    result = store.import_data(backup)

    assert result["medications"] == 0
    assert result["appointments"] == 0
    meds = store.list_medications("妈妈")
    assert len(meds) == 1
    assert meds[0]["dose"] == "10mg"
    appointments = store.list_appointments("妈妈")
    assert len(appointments) == 1
    assert appointments[0]["scheduled_at"] == "2026-12-08T09:00"


def test_restore_after_all_editable_fields_changed(store):
    """七类实体的可编辑字段都被修改后恢复，不能复制旧记录。"""
    med = store.add_medication("妈妈", "氨氯地平", "5mg", "每日一次")
    appt = store.add_appointment(
        "妈妈", "心内科复诊", "心内科", scheduled_at="2026-12-01 09:00",
    )
    vacc = store.add_vaccination(
        "妈妈", "流感疫苗", 1, vaccinated_at="2026-10-01",
    )
    allergy = store.add_allergy("妈妈", "青霉素", reaction="皮疹")
    care = store.add_care_item("妈妈", "糖化血红蛋白", 90)
    condition = store.add_condition(
        "妈妈", "高血压", diagnosed_at="2026-01-01",
    )
    symptom = store.add_symptom(
        "妈妈", "头痛", 3, recorded_at="2026-11-12",
    )
    backup = store.export_data()

    store.update_medication(med["id"], name="苯磺酸氨氯地平", dose="10mg")
    store.update_appointment_partial(
        appt["id"], title="心内科复查", scheduled_at="2026-12-08 09:00",
    )
    store.update_vaccination_partial(
        vacc["id"], vaccinated_at="2026-10-02", dose_number=2,
    )
    store.update_allergy(
        allergy["id"], substance="阿莫西林", noted_at="2026-01-02",
    )
    store.update_care_item_partial(
        care["id"], title="糖化血红蛋白复查", interval_days=180,
    )
    store.update_condition(
        condition["id"], name="原发性高血压", diagnosed_at="2026-02-01",
    )
    store.update_symptom_partial(
        symptom["id"], symptom="偏头痛", recorded_at="2026-11-13",
    )

    result = store.import_data(backup)

    assert result["medications"] == 0
    assert result["appointments"] == 0
    assert result["vaccinations"] == 0
    assert result["allergies"] == 0
    assert result["care_items"] == 0
    assert result["conditions"] == 0
    assert result["symptoms"] == 0

    assert len(store.list_medications("妈妈")) == 1
    assert len(store.list_appointments("妈妈")) == 1
    assert len(store.list_vaccinations("妈妈")) == 1
    assert len(store.list_allergies("妈妈")) == 1
    assert len(store.list_care_items("妈妈")) == 1
    assert len(store.list_conditions("妈妈")) == 1
    assert len(store.list_symptoms("妈妈")) == 1


def test_restore_recreates_deleted_twin_without_duplicating(store):
    """同秒双胞胎删掉一条后恢复：被删的还原，存活的不复制。"""
    first = store.add_medication("妈妈", "维生素D", "5mg", "每日一次")
    store.add_medication("妈妈", "维生素D", "10mg", "每日一次")
    backup = store.export_data()

    assert store.delete_medication(first["id"]) is True

    result = store.import_data(backup)

    # 主键复用让仍存在的那条不重复，缺失的主键行按原内容还原
    assert result["medications"] == 1
    doses = sorted(
        item["dose"] for item in store.list_medications("妈妈")
    )
    assert doses == ["10mg", "5mg"]

    again = store.import_data(backup)
    assert again["medications"] == 0
    assert len(store.list_medications("妈妈")) == 2


def test_restore_after_visit_edit_does_not_duplicate(store):
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压复诊",
    )
    backup = store.export_data()

    store.update_visit_record_partial(
        visit["id"], diagnosis="原发性高血压"
    )

    result = store.import_data(backup)

    assert result["visit_records"] == 0
    records = store.list_visit_records("妈妈")
    assert len(records) == 1
    assert records[0]["diagnosis"] == "原发性高血压"
