# -*- coding: utf-8 -*-
"""第九十八轮审查修复的回归测试。"""
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


def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    ["胸痛是什么病，出汗特别厉害", "胸痛是什么病，出汗明显"],
)
def test_sweat_intensity_kept(text):
    assert "胸痛" in _red(text, {"age": "55岁"})["matches"], text


@pytest.mark.parametrize(
    "text",
    ["胸痛是什么病？天气热容易出汗", "胸痛是什么病？没有出汗"],
)
def test_sweat_weather_or_negated_filtered(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "phrase", ["still take it", "still use it", "still using it"],
)
def test_english_still_take_use_continuation(phrase):
    result = check_medication_safety(
        "warfarin was stopped 2 years ago but I "
        f"{phrase}. Can I take ibuprofen?",
        {},
    )
    assert result["level"] == "warning", (phrase, result)


def test_english_still_other_drug_not_revive():
    result = check_medication_safety(
        "warfarin was stopped 2 years ago but I still use aspirin.",
        {},
    )
    titles = [a.get("title", "") for a in result.get("alerts", [])]
    assert "华法林与非甾体抗炎药" not in titles, result


# ---------------------------------------------------------------- [3]
def test_noid_single_row_move_tail_edit_no_old_copy(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(1, profile="爸爸", note="n2")
        again = store.import_data(backup)
        rows = [
            (m["profile"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert again.get("medications", 0) == 0, (again, rows)
        assert rows == [("爸爸", "n2")], rows
    finally:
        store.close()


def test_noid_single_reminder_move_complete_no_ghost(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "测血压",
             "due_at": "2026-06-02", "note": "", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_reminder(1, profile="爸爸")
        store.complete_reminder(1)
        again = store.import_data(backup)
        rows = store.list_reminders(True, None)
        assert again.get("reminders", 0) == 0, (again, rows)
        assert len(rows) == 1 and int(rows[0]["done"] or 0) == 1, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "今天没吃华法林和二甲双胍，能吃布洛芬吗",
        "今天没吃二甲双胍和华法林，能吃布洛芬吗",
        "今天华法林没吃，能吃布洛芬吗",
    ],
)
def test_parallel_missed_dose_objects_medication_safety(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "甲氨蝶呤停用两年了但今天没吃华法林和二甲双胍，今天头撞到门框",
        "甲氨蝶呤停用两年了但华法林和二甲双胍今天没吃，今天头撞到门框",
        "甲氨蝶呤停用两年了但今天华法林没吃，今天头撞到门框",
    ],
)
def test_parallel_missed_dose_objects_triage(text):
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (text, result)


def test_parallel_low_risk_objects_not_revived():
    assert _red(
        "甲氨蝶呤停用两年了但今天没吃二甲双胍和布洛芬，今天头撞到门框",
        {"age": "55岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "I finished chemotherapy a few days ago. My temperature is 39.2°C.",
        "I finished chemotherapy a couple of days ago. "
        "My temperature is 39.2°C.",
        "I finished chemotherapy ten days ago. My temperature is 39.2°C.",
        "化疗结束快两周，发烧39度",
        "化疗结束好几周了，发烧39度",
        "化疗结束将近两周，发烧39度",
    ],
)
def test_recent_chemo_wide_forms(text):
    result = _red(text)
    assert "免疫功能低下伴发热" in result["matches"], (text, result)


def test_past_chemo_still_filtered():
    assert _red(
        "I finished chemotherapy two years ago. My temperature is 39.2°C."
    )["level"] == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "看到新闻里胸痛猝死的案例，我有点担心",
        "刚看到新闻里胸痛猝死的案例",
        "刷到电视上胸痛急救的科普",
        "看了新闻里胸痛猝死的案例",
        "新闻里胸痛猝死的案例，是真的吗",
    ],
)
def test_news_topic_reference_filtered(text):
    assert _red(text, {"age": "30岁"})["level"] == "none", text


def test_search_lookup_still_emergency():
    assert "胸痛" in _red("我在网上查了胸痛怎么办")["matches"]


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "华法林上个月停药了，今天头撞到门框",
        "华法林去年停了，今天头撞到门框",
        "华法林前年就停了，今天头撞到门框",
        "华法林停了很久了，今天头撞到门框",
        "华法林停药很久了，今天头撞到门框",
    ],
)
def test_relative_time_long_stop(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


def test_relative_time_controls():
    assert _red(
        "华法林停药一个月了，今天头撞到门框", {"age": "55岁"}
    )["level"] == "none"
    assert _red(
        "华法林半个月前停了，今天头撞到门框", {"age": "55岁"}
    )["level"] == "none"


def test_relative_time_medication_safety():
    assert check_medication_safety(
        "华法林上个月停药了，能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "华法林去年停了，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [9]
def test_noid_unedited_twin_does_not_hide_deleted(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "手机备注", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "孪生备注", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        victim = next(r for r in rows if r["note"] == "手机备注")
        store.delete_medication(victim["id"])
        single = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "手机备注", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        again = store.import_data(single)
        notes = [
            m["note"] for m in store.list_medications(None, limit=100)
        ]
        assert again.get("medications", 0) == 1, (again, notes)
        assert "手机备注" in notes, notes
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_import_multiple_upcoming_appointments_single_owner(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        day = _day(6)
        backup = {
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：G",
                 "note": "由就诊记录 #10 自动创建",
                 "owner_visit_id": 10, "status": "upcoming",
                 "scheduled_at": day + "T09:00",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 40, "profile": "妈妈", "title": "复诊：G",
                 "note": "由就诊记录 #10 自动创建",
                 "owner_visit_id": 10, "status": "upcoming",
                 "scheduled_at": day + "T10:00",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 30,
                "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        appointments = store.list_appointments(None, limit=100)
        owned = [
            a for a in appointments
            if int(a["owner_visit_id"] or 0) != 0
            or "自动创建" in str(a["note"] or "")
        ]
        assert len(appointments) == 2, appointments
        assert len(owned) == 1, [
            (a["id"], a["owner_visit_id"], a["note"])
            for a in appointments
        ]
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == int(
            owned[0]["id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_import_completed_link_cleans_detached_reminder(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建",
                "owner_visit_id": 10, "status": "completed",
                "scheduled_at": _day(3) + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 20, "profile": "妈妈", "title": "复诊：G",
                "due_at": _day(1), "appointment_id": 0, "done": 0,
                "note": "复诊 2026-12-01",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-4),
                "diagnosis": "高血压", "next_followup_date": _day(1),
                "followup_appointment_id": 30,
                "followup_reminder_id": 20,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) != 0
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次 已停用两个月、布洛芬",
        "华法林、阿司匹林 100mg 每日一次 停了",
        "华法林、阿司匹林 100mg 每日一次 停用了",
        "阿司匹林，停了；华法林",
    ],
)
def test_stop_annotation_with_duration_or_le(text):
    names = api_server._context_medication_names({"medications": text})
    assert "阿司匹林" not in names, (text, names)
    assert "阿司匹林 100mg 每日一次 停了" not in names, (text, names)
    assert not any("已停用两个月" in n for n in names), (text, names)


def test_plain_drugs_still_listed_after_stop_regex():
    names = api_server._context_medication_names(
        {"medications": "华法林、二甲双胍"}
    )
    assert names == ["华法林", "二甲双胍"], names


# ---------------------------------------------------------------- [13]
def test_web_backup_slot_delete_requires_session_match():
    source = _web_source()
    start = source.index("els.input.addEventListener('input'")
    end = source.index("els.input.addEventListener('keydown'", start)
    block = source[start:end]
    assert (
        "restoredOwner === (sessionId || '')" in block
    ), block


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "我看了一篇胸痛的文章",
        "网上查了胸痛的文章",
        "网上搜了孕期出血的文章",
    ],
)
def test_possessive_article_topic_filtered(text):
    assert _red(text, {"age": "30岁"})["level"] == "none", text


# ---------------------------------------------------------------- [15]
def test_visit_date_update_links_kept_appointment(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        target_day = _day(5)
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建",
                "owner_visit_id": 10, "status": "upcoming",
                "scheduled_at": target_day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-4),
                "diagnosis": "高血压", "next_followup_date": _day(2),
                "followup_appointment_id": 0,
                "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        store.update_visit_record_partial(
            1, next_followup_date=target_day
        )
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) != 0, visit
        assert visit.get("followup_scheduled"), visit
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_web_empty_input_cleanup_of_restored_inflight():
    source = _web_source()
    assert "function clearRestoredInflightIfOwned(" in source
    assert source.count("clearRestoredInflightIfOwned();") >= 2
