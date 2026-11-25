# -*- coding: utf-8 -*-
"""第一百零一轮审查修复的回归测试。"""
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


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "胸疼，疼得直冒冷汗",
        "我突然胸疼，出了一身冷汗",
        "我胸口有点疼，还冒冷汗",
        "胸口一阵一阵疼",
        "胸前区有点痛",
        "心口有点疼",
        "胸口隐隐作痛",
    ],
)
def test_chest_pain_variant_forms(text):
    assert "胸痛" in _labels(text), text


def test_chest_pain_controls():
    assert "胸痛" in _labels("我胸痛")
    assert _red("没有胸痛")["level"] == "none"


# ---------------------------------------------------------------- [2]
def test_restart_now_eating_form():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"history": "华法林停了半年，现在在吃"}
    )
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林停了半年，现在在吃，今天头撞到门框"
    )


def test_restart_now_using_immuno():
    assert "免疫功能低下伴发热" in _labels(
        "甲氨蝶呤停了半年，目前在用，今天发烧39度"
    )
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"history": "华法林停了半年，正在服用"}
    )


def test_restart_now_eating_medication_safety():
    assert check_medication_safety(
        "华法林停了半年，现在在吃，能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "warfarin was stopped 2 years ago but now I am on it again."
        " Can I take ibuprofen?", {}
    )["level"] == "warning"


def test_restart_control_still_stopped():
    assert _red(
        "华法林停了半年，能吃布洛芬吗", {}
    )["level"] == "none" or check_medication_safety(
        "华法林停了半年，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "摔了一跤，头皮破了",
        "摔了一跤，头皮流血",
        "头皮摔破了",
        "头皮出血了",
        "磕到脑门了",
        "撞到脑壳了",
        "太阳穴撞到桌角",
    ],
)
def test_scalp_injury_forms(text):
    assert "抗凝治疗期间头部外伤" in _labels(
        text, {"history": "华法林"}
    ), text


def test_scalp_injury_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "头皮磕破了", {"history": "华法林"}
    )


# ---------------------------------------------------------------- [4]
def test_tail_edited_reminder_twin_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x4-rem")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_reminder("无关", _day(30), note="x", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊",
             "due_at": _day(6), "note": "n1", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        mom = next(
            r for r in store.list_reminders(False, "妈妈")
            if r["note"] == "n1"
        )
        store._now = lambda: LATER
        store.complete_reminder(int(mom["id"]))
        again = store.import_data(backup)
        rows = [
            (r["profile"], int(r["done"] or 0))
            for r in store.list_reminders(True, None)
            if r["note"] == "n1"
        ]
        assert again.get("reminders", 0) == 0, (again, rows)
        assert sorted(rows) == sorted([("妈妈", 1), ("爸爸", 1)]), rows
    finally:
        store.close()


def test_tail_edited_medication_twin_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x4-med")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "爸爸", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        mom = next(
            m for m in store.list_medications("妈妈", limit=10)
            if m["note"] == "n1"
        )
        store._now = lambda: LATER
        store.update_medication(int(mom["id"]), active=False)
        again = store.import_data(backup)
        rows = [
            (m["profile"], int(m["active"] or 0))
            for m in store.list_medications(None, limit=100)
            if m["note"] == "n1"
        ]
        assert again.get("medications", 0) == 0, (again, rows)
        assert sorted(rows) == sorted([("妈妈", 0), ("爸爸", 0)]), rows
    finally:
        store.close()


def test_tail_edited_appointment_twin_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x4-appt")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_appointment(
            "妈妈", "无关", scheduled_at=_day(30) + "T09:00"
        )
        backup = {"appointments": [
            {"id": 1, "profile": "爸爸", "title": "复诊",
             "scheduled_at": _day(6) + "T09:00", "status": "completed",
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "scheduled_at": _day(6) + "T09:00", "status": "upcoming",
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        mom = next(
            a for a in store.list_appointments(None, None, False, 100)
            if a["profile"] == "妈妈" and a["note"] == "n1"
        )
        store._now = lambda: LATER
        store.update_appointment_partial(int(mom["id"]), status="completed")
        again = store.import_data(backup)
        rows = [
            (a["profile"], a["status"])
            for a in store.list_appointments(None, None, False, 100)
            if a["note"] == "n1"
        ]
        assert again.get("appointments", 0) == 0, (again, rows)
        assert rows == [("妈妈", "completed"), ("爸爸", "completed")], rows
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    ["胸痛、出汗都没有", "出汗、胸痛都没有", "咳嗽很厉害、胸痛没有"],
)
def test_cross_enum_cue_not_overreach(text):
    assert _red(text)["level"] == "none", text


def test_trailing_negation_window_still_positive():
    assert _labels("胸痛很厉害、呼吸困难没有") == ["胸痛"]
    assert _labels("持续胸痛、呼吸困难都没有") == ["胸痛"]


# ---------------------------------------------------------------- [6]
def test_fever_trailing_negation_infant():
    assert _labels("宝宝发烧已经没有了", {"age": "2个月"}) == []
    assert "3 个月以下婴儿发热" in _labels(
        "宝宝发烧38.5度", {"age": "2个月"}
    )


def test_fever_trailing_negation_immuno():
    assert _labels("发烧没有了", {"history": "正在化疗"}) == []


def test_bleeding_trailing_still_emergency():
    assert "孕产期出血" in _labels("怀孕8周，阴道出血已经没有了", {})


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "history",
    ["华法林吃了三年，去年停了", "华法林吃了三年，上个月停了",
     "华法林吃了三年，前年停了"],
)
def test_relative_time_stop_attribution(history):
    assert _red("今天头撞到门框", {"history": history})["level"] == "none"


def test_relative_time_stop_single_clause():
    assert _red(
        "华法林吃了三年，去年停了，今天头撞到门框"
    )["level"] == "none"
    assert _red(
        "甲氨蝶呤吃了两年，去年停了，今天发烧39度"
    )["level"] == "none"


def test_relative_time_other_drug_not_revived():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林吃了三年，阿司匹林去年停了，今天头撞到门框"
    )


# ---------------------------------------------------------------- [8]
def test_rename_not_claimed_by_other_member(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        store.add_reminder("无关", "2020-01-05", note="f", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "U",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "T",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        dad = next(r for r in store.list_reminders(False, "爸爸"))
        store._now = lambda: LATER
        store.update_reminder(int(dad["id"]), title="V")
        again = store.import_data(backup)
        rows = sorted(
            (r["profile"], r["title"])
            for r in store.list_reminders(False, None)
            if r["note"] != "f"
        )
        assert again.get("reminders", 0) == 0, (again, rows)
        assert rows == [("妈妈", "T"), ("爸爸", "V")], rows
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_moved_note_edit_twin_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        store.add_reminder("无关", "2020-01-05", note="f", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "吃药",
             "due_at": _day(10), "note": "n1", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "妈妈", "title": "吃药",
             "due_at": _day(6), "note": "n2", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 4, "profile": "妈妈", "title": "复查",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        mom = next(
            r for r in store.list_reminders(False, "妈妈")
            if r["title"] == "复查"
        )
        store._now = lambda: LATER
        store.update_reminder(int(mom["id"]), profile="爸爸", note="nX")
        again = store.import_data(backup)
        rows = [
            (r["profile"], r["title"])
            for r in store.list_reminders(False, None)
            if r["note"] != "f"
        ]
        assert again.get("reminders", 0) == 0, (again, rows)
        assert not any(
            prof == "妈妈" and title == "复查" for prof, title in rows
        ), rows
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_upcoming_completed_rehangs_owner_visit(tmp_path):
    store = _store(tmp_path, "x10")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        d = _day(6)
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建", "owner_visit_id": 10,
                "status": "upcoming", "scheduled_at": d + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": d,
                "followup_appointment_id": 0, "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        store._now = lambda: LATER
        store.update_appointment_partial(1, status="completed")
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 1, visit
        assert visit.get("followup_scheduled") is True, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "停用华法林可以吗，能吃布洛芬吗",
        "停用华法林行吗，能吃布洛芬吗",
        "停用华法林好吗，能吃布洛芬吗",
        "华法林停用可以吗，能吃布洛芬吗",
    ],
)
def test_stop_object_question_still_in_use(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_stop_statement_still_stopped():
    assert check_medication_safety(
        "停用华法林，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [12]
def test_reschedule_merges_stale_detached_reminder(tmp_path):
    store = _store(tmp_path, "x12")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建", "owner_visit_id": 0,
                "status": "upcoming", "scheduled_at": _day(6) + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [
                {"id": 20, "profile": "妈妈", "title": "复诊：G",
                 "due_at": _day(3), "appointment_id": 0, "done": 0,
                 "note": "复诊 " + _day(6),
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 21, "profile": "妈妈", "title": "复诊：G",
                 "due_at": _day(5), "appointment_id": 30, "done": 0,
                 "note": "复诊 " + _day(6),
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": _day(6),
                "followup_appointment_id": 0, "followup_reminder_id": 20,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        store._now = lambda: LATER
        store.update_appointment_partial(
            1, scheduled_at=_day(8) + "T09:00"
        )
        visit = store.get_visit_record(1)
        pending = store.list_reminders(False, None)
        assert int(visit["followup_reminder_id"] or 0) == 2, visit
        assert [
            (r["id"], str(r["due_at"])[:10]) for r in pending
        ] == [(2, _day(7))], pending
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林（停用 2 个月）",
        "华法林、阿司匹林（停了 2 个月）",
        "华法林、阿司匹林（停用2 个月）",
        "华法林、阿司匹林（停用2个月）",
    ],
)
def test_stop_duration_with_spaces(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_stop_duration_space_controls():
    assert api_server._context_medication_names(
        {"medications": "华法林、停用 2 个月"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林、阿司匹林"}
    ) == ["华法林", "阿司匹林"]


# ---------------------------------------------------------------- [14]
def test_web_failure_restore_registers_inflight_source():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    marker = "inflightRestoredKeyName = INFLIGHT_QUESTION_KEY;"
    assert source.count(marker) >= 3, source.count(marker)
    abort_start = source.index("} else if (err.name === 'AbortError') {")
    abort_block = source[abort_start:abort_start + 2600]
    assert "inflightRestoredText = question;" in abort_block, abort_block
    generic_start = source.index("renderError(\n        err.message")
    generic_block = source[generic_start:generic_start + 1200]
    assert "inflightRestoredText = question;" in generic_block


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛、呼吸困难、咯血、呕血都没有",
        "胸痛、呼吸困难、咯血、呕血、黑便都没有",
        "胸痛、头痛、晕厥、抽搐都没有",
    ],
)
def test_long_enumeration_trailing_negation(text):
    assert _red(text)["level"] == "none", text
