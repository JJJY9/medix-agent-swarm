# -*- coding: utf-8 -*-
"""第一百一十八轮审查修复的回归测试。"""
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周，不确定下面有没有出血",
        "怀孕了，不知道阴道有没有流血",
        "不确定内裤上有没有血",
        "孕10周，不确定下面有没有流血",
        "下面有没有出血不确定",
        "阴道有没有出血说不好",
    ],
)
def test_pregnancy_bleeding_uncertain_with_location(text):
    # 部位词插入/倒装的不确定问句仍按待排查出血（第一百一十八轮 [1]）
    assert "孕产期出血" in _labels(text, {"history": "孕8周"}), _red(
        text, {"history": "孕8周"}
    )


def test_pregnancy_bleeding_uncertain_controls():
    assert "孕产期出血" in _labels("孕8周，不确定有没有出血")
    assert _level("孕8周，阴道不再流血了") == "none"


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了一年多，生完孩子后又吃上了，今天头撞到门框",
        "华法林停了一年多，孩子出生后吃上了，今天头撞到门框",
    ],
)
def test_postpartum_restart_without_subject(text):
    # 主语省略的产后续用句按患者处理（第一百一十八轮 [5]）
    assert "抗凝治疗期间头部外伤" in _labels(text), _red(text)


def test_postpartum_restart_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林停了一年多，孩子出生后我又吃上了，今天头撞到门框"
    )
    assert _level(
        "华法林停了一年多，我爸又吃上了，今天头撞到门框",
        {"age": "70岁"},
    ) == "none"


# ---------------------------------------------------------------- [7]
def test_family_subject_not_patient_restart():
    # 「我妈目前还在吃」不能复活患者既往华法林（第一百一十八轮 [7]）
    assert _level(
        "我以前吃华法林，我妈目前还在吃华法林，今天头撞到门框",
        {"age": "70岁"},
    ) == "none"


def test_parallel_family_subject_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"age": "70岁", "history": "我和我爸都吃华法林"},
    )


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林，现在用药膳调理，今天头撞到门框",
        "我以前吃华法林，现在也吃素，今天头撞到门框",
    ],
)
def test_non_medication_object_not_restart(text):
    # 药膳/吃素不是药物续用（第一百一十八轮 [15]）
    assert "抗凝治疗期间头部外伤" not in _labels(text), _red(text)


def test_non_medication_object_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "我以前吃华法林，现在也吃，今天头撞到门框"
    )


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "text",
    [
        "流产两年后，阴道出血",
        "人流两年后，阴道出血",
        "药流两年后，阴道出血",
        "胎停两年后，阴道出血",
    ],
)
def test_inverted_past_abortion_duration(text):
    # 「流产两年后」是既往史（第一百一十八轮 [16]）
    assert _level(text) == "none", _red(text)


def test_inverted_past_abortion_history():
    assert _level(
        "阴道出血", {"history": "流产两年后", "age": "30岁"}
    ) == "none"
    assert _level("流产后两年，阴道出血") == "none"
    assert "孕产期出血" in _labels("孕8周，阴道出血")


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    ["今晚停了华法林，能吃布洛芬吗", "今夜停了华法林，能吃布洛芬吗"],
)
def test_tonight_stop_recent(text):
    # 今晚/今夜停药按近期处理（第一百一十八轮 [6]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_english_this_morning_stop():
    result = check_medication_safety(
        "I stopped warfarin this morning, can I take ibuprofen?", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_bracket_two_week_stop_active():
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": "华法林（停药2周）"}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": "华法林（停药两个月）"}
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [12]
def test_adherence_warning_beats_info_safety():
    # info 级用药安全提示不能压掉 <40% 依从 warning
    # （第一百一十八轮 [12]）
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={"worst_rate": 0,
                     "adherence": {"expected": 14, "rate": 0}},
        medication_lab={"count": 1, "level": "info",
                        "alerts": [{"title": "头孢类与酒精"}]},
        vaccinations={}, appointments={},
    )
    assert attention.get("level") == "warning"
    assert "用药打卡" in str(attention.get("label") or "")


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "避免自行服用高风险药物，尤其是老年人多种药物联用时；如出现"
        "紧急不适请及时就医。",
        "高风险药品请遵医嘱。",
        "选择合适的高风险手术前需评估。",
    ],
)
def test_risk_keyword_drug_nouns(text):
    # 高风险药物/药品/手术不是风险分级（第一百一十八轮 [13]）
    assert api_server.parse_risk_level(text) == ""


def test_risk_keyword_control():
    assert api_server.parse_risk_level("情况高危，请立即就医。") == "high"


# ---------------------------------------------------------------- [2]
def test_edited_inherited_medication_not_revived(tmp_path):
    # 本地停药的继承记录不能被当作外来行复制旧副本
    # （第一百一十八轮 [2]）
    src_a = _store(tmp_path, "s2a")
    store_b = _store(tmp_path, "s2b")
    target = _store(tmp_path, "s2t")
    try:
        for st in (src_a, store_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "华法林", dose="3mg", frequency="每日一次"
        )
        backup_a = src_a.export_data()
        store_b.import_data(backup_a)
        store_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = store_b.export_data()
        target.import_data(backup_b)
        med = target.list_medications("妈妈", None)[0]
        target._now = lambda: "2026-06-05T08:00:00"
        target.update_medication(
            int(med["id"]), active=False,
            stopped_at="2026-06-05", stop_reason="医嘱停药",
        )
        assert target.import_data(backup_a).get("medications") == 0
        assert target.list_medications("妈妈", True) == []
    finally:
        for st in (src_a, store_b, target):
            st.close()


def test_completed_inherited_reminder_not_revived(tmp_path):
    src_a = _store(tmp_path, "s2ra")
    store_b = _store(tmp_path, "s2rb")
    target = _store(tmp_path, "s2rt")
    try:
        for st in (src_a, store_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder("复查", "2026-06-08", profile="妈妈")
        backup_a = src_a.export_data()
        store_b.import_data(backup_a)
        store_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = store_b.export_data()
        target.import_data(backup_b)
        reminder = target.list_reminders(False, "妈妈")[0]
        target._now = lambda: "2026-06-05T08:00:00"
        target.complete_reminder(int(reminder["id"]))
        assert target.import_data(backup_a).get("reminders") == 0
        assert target.list_reminders(False, "妈妈") == []
    finally:
        for st in (src_a, store_b, target):
            st.close()


# ---------------------------------------------------------------- [3][8]
def _forged_two_visits():
    return {
        "visit_records": [
            {"id": 10, "profile": "妈妈", "visit_date": "2026-11-10",
             "diagnosis": "高血压", "next_followup_date": "2026-12-16",
             "followup_appointment_id": 30,
             "followup_reminder_id": 0},
            {"id": 11, "profile": "妈妈", "visit_date": "2026-11-20",
             "diagnosis": "高血压", "next_followup_date": "2026-12-16",
             "followup_appointment_id": 31,
             "followup_reminder_id": 0},
        ],
        "appointments": [
            {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
             "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
             "owner_visit_id": 10,
             "note": "由就诊记录 #10 自动创建"},
            {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
             "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
             "owner_visit_id": 11,
             "note": "由就诊记录 #11 自动创建"},
        ],
    }


def test_no_created_incremental_two_visits(tmp_path):
    # 旧备份增量导入不能把不同就诊的自动预约合并
    # （第一百一十八轮 [3][8]）
    store = _store(tmp_path, "s3a")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = _forged_two_visits()
        one = {
            "visit_records": [forged["visit_records"][0]],
            "appointments": [forged["appointments"][0]],
        }
        store.import_data(one)
        assert store.import_data(forged).get("appointments") == 1
        assert len(store.list_appointments("妈妈", None)) == 2
        visits = store.list_visit_records("妈妈")
        assert all(
            int(v["followup_appointment_id"] or 0) for v in visits
        )
    finally:
        store.close()


def test_no_created_deleted_auto_appointment_restored(tmp_path):
    store = _store(tmp_path, "s3b")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = _forged_two_visits()
        store.import_data(forged)
        rows = store.list_appointments("妈妈", None)
        second = next(
            r for r in rows
            if "#11" in str(r.get("note") or "")
            or int(r.get("owner_visit_id") or 0) == 2
        )
        store.delete_appointment(int(second["id"]))
        assert store.import_data(forged).get("appointments") == 1
        rows = store.list_appointments("妈妈", None)
        assert len(rows) == 2
        visits = store.list_visit_records("妈妈")
        assert all(
            int(v["followup_appointment_id"] or 0) for v in visits
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [4][10]
def test_raw_local_visit_id_collision_keeps_local_plan(tmp_path):
    # raw/local 就诊 id 撞号不能清本地计划的归属
    # （第一百一十八轮 [4][10]）
    store = _store(tmp_path, "s4")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        vid = int(store.add_visit_record(
            "妈妈", visit_date="2026-07-01", diagnosis="糖尿病",
            next_followup_date="2026-07-01",
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：糖尿病", scheduled_at="2026-07-01T09:00",
            note=f"由就诊记录 #{vid} 自动创建", owner_visit_id=vid,
        )["id"])
        reminder = int(store.add_reminder(
            "复诊：糖尿病", "2026-06-30", note="复诊", profile="妈妈",
            appointment_id=appt,
        )["id"])
        store.set_visit_followup_links(vid, appt, reminder)
        forged = {
            "visit_records": [
                {"id": 1, "profile": "妈妈", "visit_date": "2026-11-01",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-20",
                 "followup_appointment_id": 1,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 1,
                 "note": "由就诊记录 #1 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        appt_row = store.get_appointment(appt)
        assert int(appt_row.get("owner_visit_id") or 0) == vid
        assert f"由就诊记录 #{vid}" in str(appt_row.get("note") or "")
        assert store.get_reminder(reminder) is not None
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_inherited_metrics_labs_idempotent(tmp_path):
    # 无尾字段表的继承记录同样可认领（第一百一十八轮 [9]）
    src_a = _store(tmp_path, "s9a")
    store_b = _store(tmp_path, "s9b")
    target = _store(tmp_path, "s9t")
    try:
        for st in (src_a, store_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_metric(
            "妈妈", "weight", 60, measured_at="2026-06-01T07:00",
        )
        src_a.add_lab_result(
            "妈妈", "白细胞", 6.5, unit="10^9/L",
            tested_at="2026-06-01", panel="血常规",
        )
        backup_a = src_a.export_data()
        store_b.import_data(backup_a)
        store_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = store_b.export_data()
        target.import_data(backup_a)
        assert target.import_data(backup_b).get("metrics") == 0
        assert target.import_data(backup_b).get("lab_results") == 0
    finally:
        for st in (src_a, store_b, target):
            st.close()


# ---------------------------------------------------------------- [14]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_draft_writeback_guarded_by_owner():
    source = _app_js()
    start = source.index("function restoreDeferredCrossOwnerDraft()")
    block = source[start:start + 1800]
    # 按属主备份槽暂存：其它会话的恢复/打开不能覆盖或删除
    # （第一百一十八轮 [14]、第一百二十一轮 [13]）
    assert "deferred.owner" in block
    assert "medix.deferredDraft." in block
    assert "safeSessionSet(DRAFT_KEY, deferred.text);" not in block
