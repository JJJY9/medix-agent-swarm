# -*- coding: utf-8 -*-
"""第九十九轮审查修复的回归测试。"""
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
EXPORTED = "2026-09-01T09:00:00"
LATER = "2026-11-01T09:00:00"


def _store(tmp_path, tag="sessions"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "华法林吃了三年，停用阿司匹林一年了，今天头撞到门框",
        "华法林吃了三年，停掉了阿司匹林一年了，今天头撞到门框",
    ],
)
def test_stop_object_after_verb_triage(text):
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (text, result)


def test_stop_object_after_verb_immuno():
    result = _red("甲氨蝶呤吃了三年，停用布洛芬两个月了，今天发烧39度")
    assert "免疫功能低下伴发热" in result["matches"], result


def test_stop_object_after_verb_self_stop_still_applies():
    assert _red(
        "停用华法林一年了，今天头撞到门框", {"age": "55岁"}
    )["level"] == "none"
    assert _red(
        "华法林吃了三年，停用华法林一年了，今天头撞到门框",
        {"age": "55岁"},
    )["level"] == "none"


def test_stop_object_after_verb_structured_medications():
    result = detect_red_flags(
        "今天头撞到门框",
        {"age": "55岁", "medications": "华法林；停用阿司匹林一年了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


@pytest.mark.parametrize(
    "text",
    [
        "华法林吃了三年，停用阿司匹林一年了，能吃布洛芬吗",
        "华法林吃了三年，停掉了阿司匹林一年了，能吃布洛芬吗",
    ],
)
def test_stop_object_after_verb_medication_safety(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_stop_object_after_verb_medication_self_stop():
    assert check_medication_safety(
        "华法林停用两年了，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [2][5]
@pytest.mark.parametrize(
    "text",
    [
        "今天没吃他达拉非，能吃硝酸甘油吗",
        "今天没吃辛伐他汀，能吃克拉霉素吗",
        "今天没吃阿托伐他汀，能喝西柚汁吗",
        "今天没吃倍他米松，能吃布洛芬吗",
        "今天没吃华法林，能吃布洛芬吗",
    ],
)
def test_missed_dose_drug_names_with_ta(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_missed_dose_tacrolimus_triage():
    result = _red("甲氨蝶呤停用两年了但今天没吃他克莫司，今天发烧39度")
    assert "免疫功能低下伴发热" in result["matches"], result


def test_missed_dose_pronoun_object_still_dropped():
    # 「我已经」这类代词宾语不能当成药名（第六十三轮口径）
    assert check_medication_safety(
        "我已经两天没吃华法林了，能吃布洛芬吗", {}
    )["level"] == "warning"


# ---------------------------------------------------------------- [3][13]
@pytest.mark.parametrize(
    "tail",
    [
        "出了好多汗",
        "出了很多汗",
        "浑身是汗",
        "汗很多",
        "一身汗",
        "流了好多汗",
        "出汗得厉害",
        "出汗得特别厉害",
        "汗出得厉害",
        "出汗得很厉害",
        "出汗得严重",
        "出汗特别厉害",
    ],
)
def test_sweat_intensity_forms(tail):
    result = _red("胸痛是什么病，" + tail)
    assert "胸痛" in result["matches"], (tail, result)


@pytest.mark.parametrize(
    "tail", ["天气热容易出汗", "没有出汗"]
)
def test_sweat_non_patient_forms_filtered(tail):
    assert _red("胸痛是什么病？" + tail)["level"] == "none", tail


# ---------------------------------------------------------------- [4]
def _seed_twin_meds(store):
    store._now = lambda: CREATED
    store.save_profile("爸爸", {})
    for dose in ("1", "2", "3", "4", "5"):
        store.add_medication(
            "爸爸", "A", dose=dose, frequency="qd", note="n1"
        )
    for row in store.list_medications("爸爸", limit=10)[:2]:
        store.delete_medication(row["id"])
    store._now = lambda: EXPORTED
    return store.export_data()


def test_drift_twin_delete_edit_restores_deleted(tmp_path):
    source = _store(tmp_path, "src-loss")
    backup = _seed_twin_meds(source)
    source.close()

    store = _store(tmp_path, "dst-loss")
    store._now = lambda: CREATED
    store.save_profile("爸爸", {})
    for idx in ("1", "2", "3"):
        store.add_medication("爸爸", "X" + idx, dose="1", frequency="qd")
    store._now = lambda: EXPORTED
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    victim = next(
        m for m in rows if m["name"] == "A" and m["dose"] == "3"
    )
    edited = next(
        m for m in rows if m["name"] == "A" and m["dose"] == "4"
    )
    store._now = lambda: LATER
    store.delete_medication(victim["id"])
    store.update_medication(edited["id"], dose="9")
    again = store.import_data(backup)
    doses = sorted(
        m["dose"] for m in store.list_medications(None, limit=100)
        if m["name"] == "A"
    )
    # 宁重复勿丢：被删的 3 必须补回、编辑后的 9 保留；尾字段无法
    # 唯一归属时编辑前旧副本（4）允许重复插入（第三十七轮口径）
    assert again.get("medications", 0) >= 1, (again, doses)
    assert {"3", "5", "9"} <= set(doses), doses
    store.close()


def test_sparse_twin_delete_edit_restores_deleted(tmp_path):
    source = _store(tmp_path, "src-sparse")
    backup = _seed_twin_meds(source)
    source.close()

    store = _store(tmp_path, "dst-sparse")
    store._now = lambda: EXPORTED
    store.save_profile("爸爸", {})
    store.import_data(backup)
    rows = {m["dose"]: m for m in store.list_medications(None, limit=100)}
    store._now = lambda: LATER
    store.delete_medication(rows["3"]["id"])
    store.update_medication(rows["4"]["id"], dose="9")
    again = store.import_data(backup)
    doses = sorted(
        m["dose"] for m in store.list_medications(None, limit=100)
    )
    assert again.get("medications", 0) >= 1, (again, doses)
    assert {"3", "5", "9"} <= set(doses), doses
    store.close()


def test_twin_edit_without_delete_stays_idempotent(tmp_path):
    store = _store(tmp_path, "edit-only")
    store._now = lambda: CREATED
    store.save_profile("爸爸", {})
    for dose in ("1", "2"):
        store.add_medication("爸爸", "A", dose=dose, frequency="qd")
    store._now = lambda: EXPORTED
    backup = store.export_data()
    store._now = lambda: LATER
    rows = {m["dose"]: m for m in store.list_medications(None, limit=100)}
    store.update_medication(rows["1"]["id"], dose="7")
    again = store.import_data(backup)
    doses = sorted(
        m["dose"] for m in store.list_medications(None, limit=100)
    )
    assert again.get("medications", 0) == 0, (again, doses)
    assert doses == ["2", "7"], doses
    store.close()


def test_twin_reminder_due_edit_keeps_deleted(tmp_path):
    store = _store(tmp_path, "rem-twin")
    store._now = lambda: CREATED
    store.save_profile("妈妈", {})
    for due in ("2026-06-11", "2026-06-12"):
        store.add_reminder(
            "复诊", due, note="n1", profile="妈妈"
        )
    store._now = lambda: EXPORTED
    backup = store.export_data()
    store._now = lambda: LATER
    rows = sorted(
        store.list_reminders(True, "妈妈"),
        key=lambda item: item["due_at"],
    )
    store.delete_reminder(rows[0]["id"])
    store.update_reminder(rows[1]["id"], due_at="2026-06-20")
    again = store.import_data(backup)
    dues = sorted(
        str(item["due_at"])[:10]
        for item in store.list_reminders(True, "妈妈")
    )
    assert again.get("reminders", 0) == 1, (again, dues)
    assert "2026-06-11" in dues, dues
    assert "2026-06-20" in dues, dues
    store.close()


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "when", ["今天早上", "昨天晚上", "今天中午", "今日早上"]
)
def test_tokenized_time_missed_dose(when):
    result = _red(
        "华法林停用两年了但" + when + "利伐沙班没吃，刚才头撞到门框",
        {"age": "55岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], (when, result)


def test_tokenized_time_immuno_direction():
    result = _red(
        "甲氨蝶呤停用两年了但昨天晚上环孢素没吃，现在发烧39度",
        {"age": "55岁"},
    )
    assert "免疫功能低下伴发热" in result["matches"], result


def test_tokenized_time_low_risk_drug_not_revived():
    assert _red(
        "华法林停用两年了但今天早上二甲双胍没吃，刚才头撞到门框",
        {"age": "55岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "发烧、咳嗽、胸痛都没有",
        "胸痛、呼吸困难都没有",
        "胸痛没有了",
        "胸口痛已经没了",
        "胸痛都不再有",
    ],
)
def test_trailing_negation_filtered(text):
    assert _red(text)["level"] == "none", text


def test_trailing_negation_positive_controls():
    assert "胸痛" in _red("胸痛没有缓解")["matches"]
    assert "胸痛" in _red("胸痛不是没有了")["matches"]


# ---------------------------------------------------------------- [8]
def _noid_twin_backup():
    return {"medications": [
        {"profile": "爸爸", "name": "A", "dose": "2", "frequency": "qd",
         "note": "n1", "active": 1,
         "created_at": CREATED, "updated_at": CREATED},
        {"profile": "爸爸", "name": "A", "dose": "1", "frequency": "qd",
         "note": "n1", "active": 1,
         "created_at": CREATED, "updated_at": CREATED},
    ]}


def test_noid_same_tail_twin_move_idempotent(tmp_path):
    store = _store(tmp_path, "noid-move")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        backup = _noid_twin_backup()
        store.import_data(backup)
        rows = {m["dose"]: m for m in store.list_medications(None, limit=100)}
        store.update_medication(rows["2"]["id"], profile="妈妈")
        again = store.import_data(backup)
        final = sorted(
            (m["profile"], m["dose"])
            for m in store.list_medications(None, limit=100)
        )
        assert again.get("medications", 0) == 0, (again, final)
        assert final == [("妈妈", "2"), ("爸爸", "1")], final
    finally:
        store.close()


def test_noid_same_tail_twin_move_delete_restores_once(tmp_path):
    store = _store(tmp_path, "noid-move-del")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        backup = _noid_twin_backup()
        store.import_data(backup)
        rows = {m["dose"]: m for m in store.list_medications(None, limit=100)}
        store.update_medication(rows["2"]["id"], profile="妈妈")
        store.delete_medication(rows["1"]["id"])
        again = store.import_data(backup)
        final = sorted(
            (m["profile"], m["dose"])
            for m in store.list_medications(None, limit=100)
        )
        assert again.get("medications", 0) == 1, (again, final)
        assert final == [("妈妈", "2"), ("爸爸", "1")], final
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_restore_appointment_rejects_unlinked_owned_upcoming(tmp_path):
    day = _day(6)
    store = _store(tmp_path, "restore-appt")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        backup = {
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：G",
                 "note": "由就诊记录 #10 自动创建",
                 "owner_visit_id": 10, "status": "cancelled",
                 "scheduled_at": day + "T09:00",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 40, "profile": "妈妈", "title": "复诊：G",
                 "note": "由就诊记录 #10 自动创建",
                 "owner_visit_id": 10, "status": "upcoming",
                 "scheduled_at": day + "T09:00",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0,
                "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        appointments = {
            a["status"]: a
            for a in store.list_appointments(None, None, False, 100)
        }
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 0, visit
        with pytest.raises(ValueError):
            store.update_appointment_partial(
                appointments["cancelled"]["id"], status="upcoming"
            )
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def _legacy_shared_setup(store):
    day = _day(8)
    due = _day(7)
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO visit_records(profile, visit_date, diagnosis,"
            " next_followup_date, followup_appointment_id,"
            " followup_reminder_id, created_at, updated_at)"
            " VALUES ('妈妈','2026-11-01','高血压',?,1,0,?,?)",
            (day, CREATED, EXPORTED),
        )
        store._conn.execute(
            "INSERT INTO visit_records(profile, visit_date, diagnosis,"
            " next_followup_date, followup_appointment_id,"
            " followup_reminder_id, created_at, updated_at)"
            " VALUES ('妈妈','2026-11-02','复查',?,1,1,?,?)",
            (day, CREATED, EXPORTED),
        )
        store._conn.execute(
            "INSERT INTO reminders(title, note, due_at, profile, done,"
            " appointment_id, created_at, updated_at)"
            " VALUES ('复诊：乙','',?,'妈妈',0,0,?,?)",
            (due, CREATED, EXPORTED),
        )
        store._conn.execute(
            "INSERT INTO appointments(profile, title, scheduled_at, status,"
            " note, owner_visit_id, created_at, updated_at)"
            " VALUES ('妈妈','复诊：乙',?,'upcoming',"
            " '由就诊记录 #1 自动创建',1,?,?)",
            (day + "T09:00", CREATED, EXPORTED),
        )
    store._conn.commit()


def _legacy_state(store):
    reminder = store._conn.execute(
        "SELECT id FROM reminders WHERE title = '复诊：乙'"
    ).fetchone()
    refs = store._conn.execute(
        "SELECT COUNT(*) AS n FROM visit_records"
        " WHERE followup_reminder_id = 1"
    ).fetchone()["n"]
    return (reminder is not None, int(refs))


@pytest.mark.parametrize("action", ["update", "delete", "schedule"])
def test_legacy_shared_link_cleans_detached_reminder(tmp_path, action):
    store = _store(tmp_path, "legacy-" + action)
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _legacy_shared_setup(store)
        store._now = lambda: LATER
        if action == "update":
            store.update_visit_record_partial(
                2, next_followup_date=_day(20)
            )
        elif action == "delete":
            store.delete_visit_record(2)
        else:
            store.set_visit_followup_links(1, 1, 0)
        assert _legacy_state(store) == (False, 0)
    finally:
        store.close()


def test_legacy_shared_link_kept_when_other_visit_references(tmp_path):
    store = _store(tmp_path, "legacy-shared")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _legacy_shared_setup(store)
        # 再加一条就诊共享同一提醒：清理时必须保留提醒
        with store._lock, store._conn:
            store._conn.execute(
                "INSERT INTO visit_records(profile, visit_date, diagnosis,"
                " followup_reminder_id, created_at, updated_at)"
                " VALUES ('妈妈','2026-11-03','复诊',1,?,?)",
                (CREATED, EXPORTED),
            )
        store._conn.commit()
        store._now = lambda: LATER
        store.delete_visit_record(2)
        exists, refs = _legacy_state(store)
        assert exists is True and refs == 1, (exists, refs)
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，停掉了",
        "华法林已停掉了",
        "华法林 3mg 每日一次，停止了",
        "华法林，停了两个月",
        "华法林，停了两个月了",
        "停掉了布洛芬",
    ],
)
def test_stop_annotation_forms_removed(text):
    assert api_server._context_medication_names({"medications": text}) == [], (
        text,
        api_server._context_medication_names({"medications": text}),
    )


def test_stop_annotation_forms_control():
    assert api_server._context_medication_names(
        {"medications": "华法林、二甲双胍"}
    ) == ["华法林", "二甲双胍"]
    assert api_server._context_medication_names(
        {"medications": "华法林、已停掉了阿司匹林"}
    ) == ["华法林"]
    assert api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次 已停用两个月、布洛芬"}
    ) == ["布洛芬"]


# ---------------------------------------------------------------- [12]
def test_web_adopted_profile_conflict_covers_empty_session_profile():
    source = _web_source()
    start = source.index("adoptedSessionProfile")
    block = source[start:start + 460]
    assert "adoptedSessionProfile" in block, block
    assert "String(data.profile || '').trim()" in block, block
    assert "&& data.profile\n" not in block, block


# ---------------------------------------------------------------- [13]
def test_sweat_de_complement_kept_in_definition_question():
    for tail in ("出汗得厉害", "汗出得厉害", "出汗得特别厉害"):
        result = _red("胸痛是什么病，" + tail)
        assert "胸痛" in result["matches"], (tail, result)


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "metric,context",
    [("weight", "晨起"), ("bp", "MORNING"), ("bp", "morning"),
     ("weight", "")],
)
def test_metric_context_fingerprint_idempotent(tmp_path, metric, context):
    store = _store(tmp_path, "metric-ctx")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        item = {
            "id": 1, "profile": "妈妈", "metric": metric,
            "value1": 60, "measured_at": "2026-06-01T08:00",
            "context": context, "note": "",
            "created_at": CREATED, "updated_at": CREATED,
        }
        if metric == "bp":
            item["value1"] = 120
            item["value2"] = 80
        backup = {"health_metrics": [item]}
        first = store.import_data(backup).get("metrics", 0)
        second = store.import_data(backup).get("metrics", 0)
        assert (first, second) == (1, 0), (first, second)
    finally:
        store.close()


# ---------------------------------------------------------------- [15]
def _completed_visit_store(store, reminder_appointment=0):
    day = _day(3)
    due = _day(2)
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO appointments(profile, title, scheduled_at, status,"
            " note, owner_visit_id, created_at, updated_at)"
            " VALUES ('妈妈','复诊：乙',?,'completed',?,1,?,?)",
            (day + "T09:00", "由就诊记录 #1 自动创建", CREATED, EXPORTED),
        )
        store._conn.execute(
            "INSERT INTO reminders(title, note, due_at, profile, done,"
            " appointment_id, created_at, updated_at)"
            " VALUES ('复诊：乙','',?,'妈妈',0,?,?,?)",
            (due, reminder_appointment, CREATED, EXPORTED),
        )
        store._conn.execute(
            "INSERT INTO visit_records(profile, visit_date, diagnosis,"
            " next_followup_date, followup_appointment_id,"
            " followup_reminder_id, created_at, updated_at)"
            " VALUES ('妈妈','2026-11-01','高血压',?,1,1,?,?)",
            (day, CREATED, EXPORTED),
        )
    store._conn.commit()


def test_schedule_completed_cleans_detached_pending_reminder(tmp_path):
    store = _store(tmp_path, "completed-detached")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _completed_visit_store(store)
        result = api_server._schedule_followup_sync(store, 1)
        visit = store.get_visit_record(1)
        assert store.get_reminder(1) is None
        assert int(visit["followup_reminder_id"] or 0) == 0
        assert result["reminder"] is None
        assert result["reminder_created"] is False
    finally:
        store.close()


def test_schedule_completed_keeps_appointment_attached_reminder(tmp_path):
    store = _store(tmp_path, "completed-attached")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _completed_visit_store(store, reminder_appointment=1)
        result = api_server._schedule_followup_sync(store, 1)
        visit = store.get_visit_record(1)
        assert store.get_reminder(1) is not None
        assert int(visit["followup_reminder_id"] or 0) == 1
        assert result["reminder_created"] is False
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_web_inflight_restore_treats_empty_profile_as_mismatch():
    source = _web_source()
    start = source.index("function restoreInflightForOpenSession(")
    block = source[start:start + 2600]
    assert "if (profile && profile !== currentProfile) return;" in block
    assert "profile && currentProfile && profile !== currentProfile" not in block


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "我吃华法林，差点就撞到头了",
        "我吃华法林，险些就撞到头",
        "我吃华法林，几乎就撞到头了",
        "我吃华法林，差一点就撞到头",
    ],
)
def test_almost_smash_forms_not_trauma(text):
    assert _red(text, {})["level"] == "none", text


def test_real_smash_still_trauma():
    result = _red("我吃华法林，撞到头", {})
    assert "抗凝治疗期间头部外伤" in result["matches"], result
