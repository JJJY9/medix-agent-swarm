# -*- coding: utf-8 -*-
"""第九十六轮审查修复的回归测试。"""
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
        "胸痛是什么病，直冒冷汗",
        "胸痛是什么病，还出冷汗",
        "胸痛是什么病，大汗淋漓",
        "胸痛是什么病，放射到左臂",
        "胸痛是什么病，查了心电图没事但晚上疼得厉害",
        "胸痛是什么病，工作的时候疼得厉害",
        "咯血是什么病，直冒冷汗",
    ],
)
def test_definition_question_hard_signs_kept(text):
    assert _red(text, {"age": "55岁"})["level"] == "emergency", text


@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病？最近工作压力很严重",
        "胸痛是什么病？网上说这病很严重",
        "胸痛是什么病？会恶化吗",
        "咯血见于什么病？朋友说很厉害",
    ],
)
def test_definition_question_life_or_quote_still_filtered(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [2]
def test_noid_reminder_moved_and_edited_tail_restores(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"profile": "爸爸", "title": "复诊", "due_at": "2026-12-05",
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "title": "复诊", "due_at": "2026-12-01",
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_reminder(
            1, profile="妈妈", due_at="2026-12-09", note="e2"
        )
        store.delete_reminder(2)
        store.import_data(backup)
        rows = [
            (r["profile"], str(r["due_at"]), r["note"])
            for r in store.list_reminders(None)
        ]
        assert ("妈妈", "2026-12-01", "n1") in rows, rows
        assert ("爸爸", "2026-12-05", "n2") not in rows, rows
        again = store.import_data(backup)
        assert again.get("reminders", 0) == 0, (again, rows)
    finally:
        store.close()


# ---------------------------------------------------------------- [3]
def test_noid_moved_row_not_inflate_group(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "2",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "爸爸", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n2", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(2, profile="妈妈")
        store.delete_medication(3)
        store.import_data(backup)
        rows = [
            (m["profile"], m["name"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert ("妈妈", "A", "1", "n2") in rows, rows
        assert (
            sum(
                1 for r in rows
                if r[0] == "妈妈" and r[1] == "A" and r[2] == "1"
                and r[3] == "n1"
            )
        ) == 1, rows
        again = store.import_data(backup)
        assert again.get("medications", 0) == 0, (again, rows)
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "这不是明显的胸痛吗，疼了两小时",
        "这不是有胸痛吗，疼了两小时",
        "这不是真的胸痛吗，疼了两小时",
    ],
)
def test_rhetorical_negation_with_fillers(text):
    assert "胸痛" in _red(text, {"age": "55岁"})["matches"], text


def test_rhetorical_negation_pregnancy_filler():
    result = _red("怀孕8周，这不是明显的阴道出血吗", {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result


def test_reported_negation_not_rhetorical():
    assert _red("你不是说不是胸痛吗")["level"] == "none"


def test_rhetorical_direct_form_still_positive():
    assert "胸痛" in _red(
        "这不是胸痛吗，疼了两小时", {"age": "55岁"}
    )["matches"]


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "我在网上看到说胸痛很严重",
        "我在网上看到说胸痛很厉害",
        "我刷视频看到胸痛很严重",
        "我看了篇科普说胸痛很厉害",
    ],
)
def test_quote_clause_reader_subject_not_patient(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [6][11]
@pytest.mark.parametrize(
    "text",
    [
        "甲氨蝶呤停用两年了但华法林没吃三天了，今天发烧39度",
        "华法林停用两年了但甲氨蝶呤没吃三天了，今天头撞到门框",
        "二甲双胍今天没吃但华法林停用两年了，今天头撞到门框",
        "华法林没吃三天了但甲氨蝶呤停用两年了，今天发烧39度",
    ],
)
def test_other_drug_missed_dose_with_long_stop_not_revived(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


def test_own_missed_dose_still_emergency():
    assert _red(
        "没吃华法林三天了，今天头撞到门框", {"age": "55岁"}
    )["level"] == "emergency"


def test_non_high_risk_other_missed_dose_filtered():
    assert _red(
        "华法林停用两年了但阿司匹林没吃三天了，今天头撞到门框",
        {"age": "55岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [7]
def test_twin_content_and_tail_edited_second_import_idempotent(tmp_path):
    src = _store(tmp_path, "s7src")
    src._now = lambda: CREATED
    src.save_profile("妈妈", {})
    src.add_medication("妈妈", "B", "2", frequency="qd", note="n2")
    src.add_medication("妈妈", "B", "2", frequency="qd", note="n1")
    backup = src.export_data()
    src.close()

    store = _store(tmp_path, "s7")
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data(backup)
        store.update_medication(1, profile="爸爸", dose="4", note="e1")
        store.delete_medication(2)
        seq = [
            store.import_data(backup).get("medications", 0)
            for _ in range(3)
        ]
        rows = [
            (m["profile"], m["name"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert seq == [1, 0, 0], (seq, rows)
        assert ("妈妈", "B", "2", "n2") not in rows, rows
        assert ("爸爸", "B", "4", "e1") in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_sibling_deleted_still_claims_moved_row(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊",
             "due_at": "2026-12-05", "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "due_at": "2026-12-01", "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_reminder(1, profile="妈妈", due_at="2026-12-09")
        store.delete_reminder(2)
        store.import_data(backup)
        rows = [
            (r["profile"], str(r["due_at"]), r["note"])
            for r in store.list_reminders(None)
        ]
        assert ("妈妈", "2026-12-09", "n2") in rows, rows
        assert ("爸爸", "2026-12-05", "n2") not in rows, rows
        again = store.import_data(backup)
        assert again.get("reminders", 0) == 0, (again, rows)
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def _shared_followup_backup():
    twin_day = _day(6)
    return {
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


def test_shared_reminder_all_links_rejected_no_ghost(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = _shared_followup_backup()
        store.import_data(backup)
        store.update_visit_record_partial(1, next_followup_date=_day(15))
        store.update_visit_record_partial(2, next_followup_date=_day(15))
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


def test_shared_reminder_one_link_kept(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = _shared_followup_backup()
        store.import_data(backup)
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        visits = [store.get_visit_record(1), store.get_visit_record(2)]
        assert any(
            int(v["followup_reminder_id"] or 0) == int(pending[0]["id"])
            for v in visits
        ), visits
        store.import_data(backup)
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "生孩子后十五个月，阴道出血",
        "生孩子后二十个月，阴道出血",
        "生孩子后十三个月，阴道出血",
        "产后十五个月，阴道出血",
    ],
)
def test_postpartum_chinese_month_guards(text):
    result = _red(text, {"age": "30岁"})
    assert "孕产期出血" not in result["matches"], (text, result)


def test_postpartum_recent_control_still_positive():
    result = _red("生孩子后一个月，阴道出血", {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "word",
    ["还吃", "仍吃", "接着吃", "还服用", "仍在使用", "一直在用",
     "继续使用", "还在使用"],
)
def test_restart_object_words_synced(word):
    result = _red(
        f"华法林停用两年了但甲氨蝶呤{word}，今天头撞到门框",
        {"age": "55岁"},
    )
    assert result["level"] == "none", (word, result)


@pytest.mark.parametrize(
    "word", ["仍在使用", "还吃", "一直在用", "继续使用"],
)
def test_restart_object_words_synced_medication_safety(word):
    result = check_medication_safety(
        f"华法林停用两年了但阿司匹林{word}，能吃布洛芬吗", {}
    )
    titles = [a.get("title", "") for a in result.get("alerts", [])]
    assert "华法林与非甾体抗炎药" not in titles, (word, result)


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "text,want",
    [
        ("华法林，停药两个月", []),
        ("华法林，停用两个月", []),
        ("阿司匹林已停用", []),
        ("华法林 阿司匹林已停用", ["华法林"]),
        ("华法林，已停药两个月", []),
    ],
)
def test_comma_and_suffix_bare_stop(text, want):
    names = api_server._context_medication_names({"medications": text})
    assert names == want, (text, names)


def test_comma_bare_stop_plain_drugs_still_listed():
    names = api_server._context_medication_names(
        {"medications": "华法林、二甲双胍"}
    )
    assert names == ["华法林", "二甲双胍"], names


# ---------------------------------------------------------------- [10][14][15]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_restore_session_restores_backup_inflight():
    source = _web_source()
    start = source.index("async function restoreCurrentSession(")
    end = source.index("function restoreComposer(", start)
    block = source[start:end]
    assert "restorePending" in block
    assert "restoreInflightForOpenSession(expectedSession, messages);" in block
    assert "restoreCurrentSession('', 0, true)" in source


def test_web_input_cleanup_uses_record_owner():
    source = _web_source()
    start = source.index("els.input.addEventListener('input'")
    end = source.index("els.input.addEventListener('keydown'", start)
    block = source[start:end]
    assert "clearInflightForCurrentSession();" in block


def test_web_read_inflight_skips_unowned_main_slot():
    source = _web_source()
    start = source.index("function readInflightRecordFor(")
    end = source.index("function restoreInflightForOpenSession(", start)
    block = source[start:end]
    assert "if (!owner || owner === targetId)" not in block
    assert "if (owner === targetId)" in block
