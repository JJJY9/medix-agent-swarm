# -*- coding: utf-8 -*-
"""第九十四轮审查修复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text,label",
    [
        ("胸痛持续两小时了见于什么病", "胸痛"),
        ("咯血持续三天了见于什么病", "咯血"),
        ("呼吸困难持续半小时了见于什么病", "呼吸困难"),
    ],
)
def test_definition_question_inline_course(text, label):
    result = _red(text, {"age": "55岁"})
    assert result["level"] == "emergency", result
    assert patient_complaint_text(text), text


@pytest.mark.parametrize(
    "text",
    [
        "什么原因引起的胸痛，疼得厉害",
        "胸痛是什么病啊，痛得我直冒冷汗",
    ],
)
def test_definition_question_severity_follows(text):
    assert _red(text, {"age": "55岁"})["level"] == "emergency", text


@pytest.mark.parametrize(
    "text",
    ["胸痛见于什么病", "什么是咯血", "胸痛是什么病"],
)
def test_definition_question_pure_stays_filtered(text):
    assert _red(text)["level"] == "none", text


def test_definition_question_life_context_kept_filtered():
    assert _red("我正在准备考试。咯血见于什么病？")["level"] == "none"
    assert _red("胸痛见于什么病？最近在准备婚礼")["level"] == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "phrase",
    [
        "I kept taking it",
        "I kept on taking it",
        "I continued taking it",
        "I continue to take it",
        "I have kept taking it",
    ],
)
def test_english_restart_pronoun_kept(phrase):
    text = f"warfarin was stopped 2 years ago but {phrase}，今天头撞到门框"
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (phrase, result)


@pytest.mark.parametrize(
    "phrase",
    ["kept taking it", "continued taking it", "continue to take it"],
)
def test_english_restart_pronoun_medication_safety(phrase):
    text = (
        f"warfarin was stopped 2 years ago but I {phrase}. "
        "Can I take ibuprofen?"
    )
    result = check_medication_safety(text, {})
    assert result["level"] == "warning", (phrase, result)


def test_english_still_stop_without_continuation_still_removed():
    result = _red(
        "warfarin was stopped 2 years ago，今天头撞到门框",
        {"age": "55岁"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [3]
def test_same_origin_pool_never_claims_other_backup_row(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "X", "dose": "1",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "X", "dose": "1",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "name": "X", "dose": "2",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.delete_medication(1)
        store.delete_medication(2)
        store.import_data(backup)
        store.delete_medication(4)
        store.update_medication(3, dose="5")
        store.import_data(backup)
        rows = [
            (m["profile"], m["name"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert ("妈妈", "X", "1", "n1") in rows, rows
        assert ("爸爸", "X", "2", "n1") not in rows, rows
        assert not any(store.import_data(backup).values())
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_reminder_cross_profile_tail_any_content_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        decoy = store.add_reminder(
            "本地", "2027-01-01", profile="妈妈"
        )
        store._conn.execute(
            "UPDATE reminders SET created_at = '2025-01-01T00:00:00'"
            " WHERE id = ?",
            (decoy["id"],),
        )
        store._conn.commit()
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药",
             "due_at": "2026-07-02", "note": "n2", "done": 0,
             "appointment_id": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药",
             "due_at": "2026-07-03", "note": "n3", "done": 0,
             "appointment_id": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.delete_reminder(2)
        store.update_reminder(3, profile="爸爸", due_at="2026-10-05")
        store.import_data(backup)
        rows = [
            (r["profile"], r["title"], str(r["due_at"]), r["note"])
            for r in store.list_reminders(None)
        ]
        assert ("爸爸", "吃药", "2026-07-02", "n2") in rows, rows
        assert not any(store.import_data(backup).values())
    finally:
        store.close()


# ---------------------------------------------------------------- [5][13]
def test_prefix_stop_annotation_with_drug_name_keeps_previous():
    names = api_server._context_medication_names(
        {"medications": "华法林、已停用阿司匹林3个月"}
    )
    assert names == ["华法林"], names
    assert api_server._context_medication_names(
        {"medications": "华法林 3mg 每天；已停用阿司匹林"}
    ) == ["华法林 3mg 每天"]
    assert api_server._context_medication_names(
        {"medications": "华法林，已停药两个月"}
    ) == []


# ---------------------------------------------------------------- [11][13]
def test_inline_stop_annotation_with_dose_drops_drug():
    assert api_server._context_medication_names(
        {"medications": "阿司匹林 100mg（已停用）"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "氯吡格雷 75mg 每天一次（已停药）"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林 阿司匹林 100mg（已停用）"}
    ) == ["华法林"]
    assert api_server._context_medication_names(
        {"medications":
         "华法林 3mg 每天；阿司匹林 100mg 每天一次（已停用）"}
    ) == ["华法林 3mg 每天"]


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text,label",
    [
        ("甲氨蝶呤吃了三年，停经两个月，今天发烧39度",
         "免疫功能低下伴发热"),
        ("华法林吃了三年，停经两个月，今天头撞到门框",
         "抗凝治疗期间头部外伤"),
    ],
)
def test_amenorrhea_not_treated_as_long_stop(text, label):
    result = _red(text, {"age": "35岁"})
    assert label in result["matches"], result


def test_amenorrhea_not_treated_as_long_stop_safety():
    result = check_medication_safety(
        "我在吃华法林，停经两个月了，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


@pytest.mark.parametrize(
    "text",
    ["华法林两年前停的，今天头撞到门框", "华法林停三年了，今天头撞到门框"],
)
def test_long_stop_variants_still_removed(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


# ---------------------------------------------------------------- [7]
def test_object_first_missed_dose_keeps_anticoagulation():
    result = _red("没吃华法林三天了，今天头撞到门框", {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = check_medication_safety("没吃华法林三天了，能吃布洛芬吗", {})
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    ["怀孕8周，不是阴道出血，是痔疮出血", "怀孕8周，并不是阴道出血"],
)
def test_pregnancy_bleeding_negation_with_shi(text):
    assert _red(text, {"age": "30岁"})["level"] == "none", text


def test_pregnancy_bleeding_positive_still_emergency():
    result = _red("怀孕8周，阴道出血", {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [9]
def test_twin_restore_second_import_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "爸爸", "name": "B", "dose": "2",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "name": "B", "dose": "2",
             "frequency": "qd", "note": "n3",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.delete_medication(1)
        store.delete_medication(2)
        store.update_medication(3, profile="妈妈", dose="7")
        first = store.import_data(backup)
        second = store.import_data(backup)
        rows = [
            (m["profile"], m["name"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert first.get("medications") == 2, first
        assert second.get("medications", 0) == 0, (second, rows)
        assert ("爸爸", "B", "2", "n3") not in rows, rows
        assert ("妈妈", "B", "7", "n3") in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def _followup_backup(detached: bool):
    return {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
             "note": "", "owner_visit_id": 0, "status": "upcoming",
             "scheduled_at": _day(6) + "T09:00",
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [{
            "id": 101, "profile": "妈妈",
            "title": "复诊" if detached else "复诊：高血压",
            "due_at": _day(5),
            "appointment_id": 0 if detached else 1, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [{
            "id": 1, "profile": "妈妈", "visit_date": _day(-2),
            "diagnosis": "高血压", "next_followup_date": _day(8),
            "followup_appointment_id": 1,
            "followup_reminder_id": 101,
            "created_at": CREATED, "updated_at": CREATED,
        }],
    }


def test_reimport_keeps_attached_reminder_on_retained_appointment(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = _followup_backup(detached=False)
        store.import_data(backup)
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert int(pending[0]["appointment_id"] or 0) == 1, pending
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 0, visit
        assert int(visit["followup_reminder_id"] or 0) == 0, visit
        again = store.import_data(backup)
        assert again.get("reminders", 0) == 0, again
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


def test_reimport_does_not_resurrect_detached_ghost(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = _followup_backup(detached=True)
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "phrase", ["还在用", "仍在用", "继续用", "还用", "仍用"],
)
def test_medication_safety_restart_use_words(phrase):
    result = check_medication_safety(
        f"华法林停过两年，现在{phrase}，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", (phrase, result)


# ---------------------------------------------------------------- [15]
def test_reschedule_shifts_multiple_reminders_by_offset(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        appointment = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-05T09:00",
        )
        store.add_reminder(
            "复诊：高血压", "2026-11-29", profile="妈妈",
            appointment_id=appointment["id"],
        )
        store.add_reminder(
            "复诊：高血压", "2026-12-02", profile="妈妈",
            appointment_id=appointment["id"],
        )
        store.update_appointment_partial(
            appointment["id"], scheduled_at="2026-12-09T09:00"
        )
        dues = sorted(
            str(r["due_at"])[:10]
            for r in store.list_reminders(False, "妈妈")
        )
        assert dues == ["2026-12-03", "2026-12-06"], dues
    finally:
        store.close()


def test_reschedule_single_reminder_still_day_before(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        appointment = store.add_appointment(
            "妈妈", "复诊：糖尿病", scheduled_at="2026-12-05T09:00",
        )
        store.add_reminder(
            "复诊：糖尿病", "2026-12-04", profile="妈妈",
            appointment_id=appointment["id"],
        )
        store.update_appointment_partial(
            appointment["id"], scheduled_at="2026-12-10T09:00"
        )
        dues = [
            str(r["due_at"])[:10]
            for r in store.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appointment["id"]
        ]
        assert dues == ["2026-12-09"], dues
    finally:
        store.close()


# ---------------------------------------------------------------- [18]
def test_stop_de_guard_for_time_phrase():
    result = check_medication_safety(
        "华法林停的时间不长，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result
    assert check_medication_safety(
        "华法林两年前停的，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [12][16][17]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_deleted_session_rebases_inflight_owner():
    source = _web_source()
    start = source.index("function detachInflightOwnerForDeletedSession(")
    end = source.index("function flushDraftNow(", start)
    block = source[start:end]
    assert "if (sessionId) return;" in block
    assert "data.sessionId = '';" in block
    send = source.index("function send(")
    send_end = source.index("function restoreComposer(", send)
    send_block = source[send:send_end]
    assert send_block.count("detachInflightOwnerForDeletedSession();") == 2


def test_web_input_clears_abandoned_inflight_question():
    source = _web_source()
    start = source.index("els.input.addEventListener('input'")
    end = source.index("els.input.addEventListener('keydown'", start)
    block = source[start:end]
    assert "!els.input.value.includes(inflightRestoredText)" in block
    # 清理走带归属守卫的 clearInflightForCurrentSession（第九十六轮
    # 改为按主槽记录自身归属判断），不再直接删除主槽键
    assert "clearInflightForCurrentSession();" in block


def test_web_settle_keeps_own_sessionless_draft():
    source = _web_source()
    start = source.index("function settleDraftForSession(")
    end = source.index("function restoreComposer(", start)
    block = source[start:end]
    assert "if (owner === '' && target && hasText) {" in block
