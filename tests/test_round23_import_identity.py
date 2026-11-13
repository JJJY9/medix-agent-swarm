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


def test_restore_after_metric_edit_does_not_duplicate(store):
    metric = store.add_metric("妈妈", "weight", 62.5)
    backup = store.export_data()

    store.update_metric(metric["id"], value1=63.0)

    result = store.import_data(backup)

    # 身份命中：不再复制旧读数；跨来源同内容的去重仍由内容计数兜底
    assert result["metrics"] == 0
    rows = store.list_metrics("妈妈", "weight")
    assert len(rows) == 1
    assert rows[0]["value1"] == 63.0


def test_repeated_restore_with_primary_key_holes_is_idempotent(
    store, tmp_path
):
    """备份主键有空洞（源库删过记录）时重复恢复仍保持幂等。"""
    ids = []
    for index in range(10):
        med = store.add_medication("妈妈", f"药{index}", "1粒", "每日一次")
        store.add_medication_log(med["id"], taken_at="2026-11-13T08:00")
        ids.append(med["id"])
    # 创建时间各不相同（真实数据的常见形态）
    with store._lock, store._conn:
        for index, med_id in enumerate(ids):
            store._conn.execute(
                "UPDATE medications SET created_at = ? WHERE id = ?",
                (f"2026-11-01T08:00:{index:02d}", med_id),
            )
    assert store.delete_medication(ids[2]) is True
    backup = store.export_data()

    target = SessionStore(str(tmp_path / "holes.db"))
    try:
        first = target.import_data(backup)
        second = target.import_data(backup)
        third = target.import_data(backup)

        assert first["medications"] == 9
        assert second["medications"] == 0
        assert third["medications"] == 0
        assert second["medication_logs"] == 0
        assert third["medication_logs"] == 0
        assert len(target.list_medications("妈妈")) == 9
        logs = target.list_medication_logs(None, "妈妈", 100, 0, 30)
        assert len(logs) == 9
    finally:
        target.close()


def test_repeated_restore_with_shifted_ids_and_distinct_created(
    store, tmp_path
):
    """目标库已有本地记录造成 id 后移、且创建时间各不相同时保持幂等。"""
    meds = []
    for index in range(4):
        med = store.add_medication("妈妈", f"药{index}", "1粒", "每日一次")
        store.add_medication_log(med["id"], taken_at="2026-11-13T08:00")
        meds.append(med)
    with store._lock, store._conn:
        for index, med in enumerate(meds):
            store._conn.execute(
                "UPDATE medications SET created_at = ? WHERE id = ?",
                (f"2026-11-01T09:00:{index:02d}", med["id"]),
            )
    backup = store.export_data()

    target = SessionStore(str(tmp_path / "shift2.db"))
    try:
        target.add_medication("妈妈", "本地药", "1粒", "每日一次")
        first = target.import_data(backup)
        second = target.import_data(backup)
        third = target.import_data(backup)

        assert first["medications"] == 4
        assert second["medications"] == 0
        assert third["medications"] == 0
        assert second["medication_logs"] == 0
        assert third["medication_logs"] == 0
        assert len(target.list_medications("妈妈")) == 5
        logs = target.list_medication_logs(None, "妈妈", 100, 0, 30)
        assert len(logs) == 4
    finally:
        target.close()


def test_restore_after_metric_delete_restores_reading(store):
    """同批同秒导入的指标删除一条后，恢复要还原该读数。"""
    store.import_data({
        "health_metrics": [
            {
                "profile": "妈妈",
                "metric": "weight",
                "value1": 63.0 + index / 10,
                "measured_at": f"2026-11-0{index + 1}T08:00",
                "context": "",
                "note": "",
            }
            for index in range(5)
        ],
    })
    backup = store.export_data()
    rows = store.list_metrics("妈妈", "weight")
    assert len(rows) == 5

    newest = max(rows, key=lambda row: row["value1"])
    assert store.delete_metric(newest["id"]) is True

    result = store.import_data(backup)

    assert result["metrics"] == 1
    values = sorted(
        row["value1"] for row in store.list_metrics("妈妈", "weight")
    )
    assert len(values) == 5
    assert abs(values[-1] - newest["value1"]) < 1e-9
    # 再次导入仍然幂等
    assert store.import_data(backup)["metrics"] == 0


def test_repeated_restore_with_shifted_ids_keeps_log_mapping(
    store, tmp_path
):
    """目标库 id 与备份主键漂移时，重复恢复不能复制或错挂用药打卡。"""
    for name in ("药A", "药B", "药C", "药D"):
        med = store.add_medication("妈妈", name, "1粒", "每日一次")
        store.add_medication_log(med["id"], taken_at="2026-11-13T08:00")
    # 固定为同一秒，确定性地构造「同秒身份组」（避免跨越秒边界）
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = '2026-11-13T08:00:00'"
        )
    backup = store.export_data()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        # 目标库先有一条本地用药，备份行的本地 id 整体后移一位
        target.add_medication("妈妈", "本地药", "1粒", "每日一次")

        first = target.import_data(backup)
        assert first["medications"] == 4
        logs_first = target.list_medication_logs(
            None, "妈妈", 100, 0, 30
        )
        assert len(logs_first) == 4

        again = target.import_data(backup)
        assert again["medications"] == 0
        logs_second = target.list_medication_logs(
            None, "妈妈", 100, 0, 30
        )
        assert len(logs_second) == 4, "重复恢复不能复制打卡"

        names_by_id = {
            row["id"]: row["name"]
            for row in target.list_medications("妈妈")
        }
        attached = sorted(
            names_by_id[log["medication_id"]] for log in logs_second
        )
        assert attached == ["药A", "药B", "药C", "药D"]
    finally:
        target.close()
