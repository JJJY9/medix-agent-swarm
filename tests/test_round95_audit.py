# -*- coding: utf-8 -*-
"""第九十五轮审查修复的回归测试。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-01T09:00:00"


def _store(tmp_path, tag="sessions"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "这不是胸痛吗，已经疼了两小时",
        "我这不是胸痛吗，疼了一天了",
        "我爸这不是胸痛吗，疼了两小时了",
    ],
)
def test_rhetorical_negation_is_positive(text):
    result = _red(text, {"age": "55岁"})
    assert "胸痛" in result["matches"], (text, result)


def test_rhetorical_negation_pregnancy_bleeding():
    result = _red("怀孕8周，这不是阴道出血吗", {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result


def test_real_negation_still_filtered():
    assert _red("这不是胸痛，只是胃不舒服", {"age": "55岁"})["level"] == "none"
    assert _red("我没有胸痛", {"age": "55岁"})["level"] == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "月子期间，阴道出血",
        "月子期间，下面流血了",
        "刚出月子，有少量出血",
        "出了月子，阴道出血",
        "生孩子后一个月，阴道出血",
    ],
)
def test_recent_postpartum_variants(text):
    result = _red(text, {"age": "30岁"})
    assert "孕产期出血" in result["matches"], (text, result)


def test_postpartum_years_still_past():
    result = _red("生孩子后两年，阴道出血", {"age": "30岁"})
    assert "孕产期出血" not in result["matches"], result


# ---------------------------------------------------------------- [3]
def _twin_backup(tmp_path):
    src = _store(tmp_path, "s3src")
    src._now = lambda: CREATED
    src.save_profile("妈妈", {})
    src.save_profile("爸爸", {})
    for profile in ("妈妈", "爸爸"):
        for note in ("n1", "n2"):
            src.add_medication(profile, "A", "1", frequency="qd", note=note)
    backup = src.export_data()
    src.close()
    return backup


def test_cross_profile_twin_swap_restores_deleted(tmp_path):
    backup = _twin_backup(tmp_path)
    store = _store(tmp_path, "s3")
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_medication("妈妈", "D1", "1")
        store.add_medication("爸爸", "D2", "1")
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom_n2 = next(
            r for r in rows
            if r["profile"] == "妈妈" and r["note"] == "n2"
        )
        store.delete_medication(mom_n2["id"])
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom_notes = sorted(
            r["note"] for r in rows if r["profile"] == "妈妈"
        )
        dad_n2 = [
            r for r in rows
            if r["profile"] == "爸爸" and r["note"] == "n2"
        ]
        assert "n2" in mom_notes, (mom_notes, rows)
        assert len(dad_n2) == 1, rows
        again = store.import_data(backup)
        assert again.get("medications", 0) == 0, again
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_moved_restored_row_claimed_no_stale_copy(tmp_path):
    src = _store(tmp_path, "s4src")
    src._now = lambda: CREATED
    src.save_profile("妈妈", {})
    for note in ("n1", "n2", "n3"):
        src.add_medication("妈妈", "A", "1", frequency="qd", note=note)
    backup = src.export_data()
    src.close()

    store = _store(tmp_path, "s4")
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data(backup)
        store.delete_medication(1)
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        restored = next(r for r in rows if r["note"] == "n1")
        store.update_medication(
            restored["id"], profile="爸爸", dose="9"
        )
        store.delete_medication(3)
        first = store.import_data(backup)
        second = store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        old_copy = [
            r for r in rows
            if r["profile"] == "妈妈" and r["note"] == "n1"
        ]
        assert first.get("medications") == 1, first
        assert second.get("medications", 0) == 0, (second, rows)
        assert old_copy == [], rows
        assert any(
            r["profile"] == "爸爸" and r["note"] == "n1"
            and r["dose"] == "9"
            for r in rows
        ), rows
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text,want",
    [
        ("已停用阿司匹林改用氯吡格雷", ["氯吡格雷"]),
        ("已停用阿司匹林换用华法林", ["华法林"]),
        ("已停药改用氯吡格雷", ["氯吡格雷"]),
    ],
)
def test_switch_medication_after_stop_annotation(text, want):
    names = api_server._context_medication_names({"medications": text})
    assert names == want, names


def test_stop_annotation_without_switch_still_dropped():
    assert api_server._context_medication_names(
        {"medications": "已停用阿司匹林"}
    ) == []


# ---------------------------------------------------------------- [6][8]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病？最近工作压力很严重",
        "胸痛是什么病？网上说这病很严重",
        "胸痛是什么病？会恶化吗",
        "胸痛见于什么病？网上说得挺严重的",
        "咯血见于什么病？朋友说很厉害",
    ],
)
def test_definition_question_severity_life_or_quote(text):
    assert _red(text)["level"] == "none", text


def test_definition_question_patient_severity_kept():
    assert _red("什么原因引起的胸痛，疼得厉害")["level"] == "emergency"
    assert _red("胸痛是什么病啊，痛得我直冒冷汗")["level"] == "emergency"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停药时间不长，能吃布洛芬吗",
        "华法林停药没多久，能吃布洛芬吗",
        "华法林停药不长时间，能吃布洛芬吗",
        "华法林停用时间不长，能吃布洛芬吗",
        "华法林已停药时间不长，能吃布洛芬吗",
    ],
)
def test_recent_stop_time_short_keeps_use(text):
    result = check_medication_safety(text, {})
    assert result["level"] == "warning", (text, result)


def test_long_stop_still_removed_safety():
    assert check_medication_safety(
        "华法林，已停药两个月，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [9]
def test_no_id_backup_delete_and_move_restores(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n2", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.delete_medication(1)
        store.update_medication(2, profile="爸爸")
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom_n1 = [
            r for r in rows
            if r["profile"] == "妈妈" and r["note"] == "n1"
        ]
        assert len(mom_n1) == 1, rows
        again = store.import_data(backup)
        assert again.get("medications", 0) == 0, (again, rows)
    finally:
        store.close()


def test_no_id_move_only_reimport_idempotent(tmp_path):
    """round86 [8] 契约：无 id 备份仅改挂、不删除时重复导入必须幂等。"""
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "B", "dose": "3",
             "frequency": "qd", "note": "a", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "爸爸", "name": "B", "dose": "3",
             "frequency": "qd", "note": "b", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom = next(r for r in rows if r["profile"] == "妈妈")
        store.update_medication(mom["id"], profile="爸爸")
        assert store.import_data(backup)["medications"] == 0
        assert store.import_data(backup)["medications"] == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_shared_detached_reminder_not_deleted_before_link(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        twin_day = _day(6)
        backup = {
            "appointments": [
                {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
                 "note": "", "owner_visit_id": 1, "status": "upcoming",
                 "scheduled_at": twin_day + "T09:00",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 2, "profile": "妈妈", "title": "复诊：高血压",
                 "note": "", "owner_visit_id": 2, "status": "upcoming",
                 "scheduled_at": twin_day + "T10:00",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [{
                "id": 701, "profile": "妈妈", "title": "复诊",
                "due_at": _day(5), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [
                {"id": 1, "profile": "妈妈", "visit_date": _day(-2),
                 "diagnosis": "高血压", "next_followup_date": twin_day,
                 "followup_appointment_id": 1,
                 "followup_reminder_id": 701,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 2, "profile": "妈妈", "visit_date": _day(-2),
                 "diagnosis": "高血压", "next_followup_date": twin_day,
                 "followup_appointment_id": 2,
                 "followup_reminder_id": 701,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(backup)
        store.delete_appointment(2)
        store.update_visit_record_partial(
            1, next_followup_date=_day(9)
        )
        store.import_data(backup)
        pending = store.list_reminders(False, "妈妈")
        visit2 = store.get_visit_record(2)
        assert len(pending) == 1, pending
        assert int(visit2["followup_appointment_id"] or 0) != 0, visit2
        assert int(visit2["followup_reminder_id"] or 0) == int(
            pending[0]["id"]
        ), (visit2, pending)
        store.import_data(backup)
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [11][15]
@pytest.mark.parametrize(
    "text",
    [
        "华法林，已停药快一年了",
        "华法林，已停用近半年",
        "华法林，已停用至今",
        "华法林，已停药大半年",
        "华法林，已停药将近一年",
        "华法林，已停药好几年",
        "华法林，已停药一年了",
    ],
)
def test_stop_annotation_modified_duration(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == [], text


def test_stop_annotation_plain_duration_still_dropped():
    assert api_server._context_medication_names(
        {"medications": "华法林，已停药两个月"}
    ) == []


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "阿司匹林 0.5g（已停用）",
        "华法林 2.5mg 每天（已停用）",
        "华法林 2.5 mg（已停用）",
        "华法林 1/2片（已停用）",
    ],
)
def test_inline_annotation_decimal_dose(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == [], text


def test_inline_annotation_integer_dose_still_works():
    assert api_server._context_medication_names(
        {"medications": "华法林 阿司匹林 100mg（已停用）"}
    ) == ["华法林"]


# ---------------------------------------------------------------- [13]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_input_cleanup_respects_owner_and_busy():
    source = _web_source()
    start = source.index("els.input.addEventListener('input'")
    end = source.index("els.input.addEventListener('keydown'", start)
    block = source[start:end]
    assert "restoredOwner === (sessionId || '')" in block
    assert "!busy" in block
    assert "!sendInFlight" in block


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize("word", ["仍在用", "继续用", "还用", "仍用"])
def test_triage_restart_object_words(word):
    result = _red(
        f"甲氨蝶呤停用两年了但华法林{word}，今天发烧39度",
        {"age": "55岁"},
    )
    assert result["level"] == "none", (word, result)
    result = _red(
        f"华法林停用两年了但甲氨蝶呤{word}，今天头撞到门框",
        {"age": "55岁"},
    )
    assert result["level"] == "none", (word, result)


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "text,context",
    [
        ("华法林停用两年了但阿司匹林没吃三天了，今天头撞到门框",
         {"age": "55岁"}),
        ("甲氨蝶呤停用两年了但二甲双胍没吃三天了，今天发烧39度",
         {"age": "55岁"}),
    ],
)
def test_other_drug_missed_dose_not_revive_stop(text, context):
    assert _red(text, context)["level"] == "none", text


def test_other_drug_missed_dose_medication_safety():
    result = check_medication_safety(
        "华法林停用两年了但二甲双胍没吃三天了，能吃布洛芬吗", {}
    )
    assert result["level"] == "none", result


def test_own_missed_dose_still_active():
    result = _red("没吃华法林三天了，今天头撞到门框", {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = _red("华法林没吃三天了，今天头撞到门框", {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = check_medication_safety("没吃华法林三天了，能吃布洛芬吗", {})
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [17]
def test_reschedule_today_does_not_create_overdue(tmp_path):
    store = _store(tmp_path)
    try:
        today = date.today().strftime("%Y-%m-%d")
        store.save_profile("妈妈", {})
        appointment = store.add_appointment(
            "妈妈", "复诊：高血压",
            scheduled_at=_day(10) + "T09:00",
        )
        store.add_reminder(
            "复诊：高血压", _day(9), profile="妈妈",
            appointment_id=appointment["id"],
        )
        store.update_appointment_partial(
            appointment["id"], scheduled_at=today + "T09:00"
        )
        dues = [
            str(r["due_at"])[:10]
            for r in store.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appointment["id"]
        ]
        assert dues and all(due >= today for due in dues), dues
    finally:
        store.close()


# ---------------------------------------------------------------- [18]
@pytest.mark.parametrize(
    "text,want",
    [
        ("阿司匹林（停用）和华法林", ["华法林"]),
        ("阿司匹林（已停）和华法林", ["华法林"]),
        ("阿司匹林（停药）和华法林", ["华法林"]),
    ],
)
def test_bracket_bare_stop_annotation(text, want):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == want


def test_bracket_english_stop_annotation():
    names = api_server._context_medication_names(
        {"medications": "Aspirin (stopped) and Warfarin"}
    )
    assert "Aspirin" not in names, names
    assert "Warfarin" in names, names
