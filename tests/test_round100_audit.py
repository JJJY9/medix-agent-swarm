# -*- coding: utf-8 -*-
"""第一百轮审查修复的回归测试。"""
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
        "怀孕8周，阴道出血已经没有了",
        "孕8周，见红已经没有了",
        "怀孕6周，昨天有一点出血今天没有了",
        "怀孕8周，阴道出血没有了",
    ],
)
def test_bleeding_stopped_still_emergency(text):
    assert "孕产期出血" in _labels(text, {}), text


def test_bleeding_negated_front_still_filtered():
    assert _red("怀孕8周，阴道没有出血", {})["level"] == "none"
    assert "孕产期出血" in _labels("怀孕8周，今天阴道出血", {})


def test_trailing_negation_red_flags_still_work():
    for text in (
        "发烧、咳嗽、胸痛都没有", "胸痛、呼吸困难都没有",
        "胸痛没有了", "胸口痛已经没了", "胸痛都不再有",
    ):
        assert _red(text)["level"] == "none", text
    assert "胸痛" in _labels("胸痛没有缓解")
    assert "胸痛" in _labels("胸痛不是没有了")


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "我在吃华法林，摔了一跤，头好像撞到了",
        "我在吃华法林，摔了一跤，头可能磕到了",
        "我在吃华法林，头似乎撞到门框了",
        "我在吃华法林，头估计撞到墙了",
        "我在吃华法林，头好像被撞了一下",
    ],
)
def test_head_trauma_speculation_words(text):
    assert "抗凝治疗期间头部外伤" in _labels(text, {}), text


def test_head_trauma_controls():
    assert "抗凝治疗期间头部外伤" in _labels("我在吃华法林，好像撞到头了", {})
    assert _red("华法林吃了三年，差点就要撞到头", {})["level"] == "none"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "能停用华法林吗，能吃布洛芬吗",
        "可以停用华法林吗，能吃布洛芬吗",
        "可否停用华法林，能吃布洛芬吗",
    ],
)
def test_modal_stop_question_still_in_use(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_modal_stop_controls():
    assert check_medication_safety(
        "华法林能停用吗，能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "打算停用华法林，能吃布洛芬吗", {}
    )["level"] == "warning"


# ---------------------------------------------------------------- [4]
def test_cross_profile_twin_tail_tie_reminder_no_loss(tmp_path):
    store = _store(tmp_path, "x4-rem")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_reminder("无关", _day(30), note="x", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊",
             "due_at": _day(9), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = sorted(store.list_reminders(True, None), key=lambda r: r["id"])
        mom = next(
            r for r in rows if r["profile"] == "妈妈" and r["note"] == "n1"
        )
        dad = next(r for r in rows if r["profile"] == "爸爸")
        store._now = lambda: LATER
        store.delete_reminder(int(mom["id"]))
        store.update_reminder(int(dad["id"]), due_at=_day(20))
        store.import_data(backup)
        final = [
            (r["profile"], str(r["due_at"])[:10])
            for r in store.list_reminders(True, None) if r["note"] == "n1"
        ]
        mom_due = [due for prof, due in final if prof == "妈妈"]
        dad_due = [due for prof, due in final if prof == "爸爸"]
        assert _day(6) in mom_due, final
        assert _day(20) in dad_due, final
    finally:
        store.close()


def test_cross_profile_twin_tail_tie_appointment_no_loss(tmp_path):
    store = _store(tmp_path, "x4-appt")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_appointment(
            "妈妈", "无关预约", scheduled_at=_day(30) + "T09:00"
        )
        backup = {"appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊：心内",
             "department": "心内科", "note": "n1", "status": "upcoming",
             "scheduled_at": _day(6) + "T09:00",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊：心内",
             "department": "心内科", "note": "n1", "status": "upcoming",
             "scheduled_at": _day(9) + "T09:00",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_appointments(None, None, False, 100)
        mom = next(
            a for a in rows if a["profile"] == "妈妈" and a["note"] == "n1"
        )
        dad = next(a for a in rows if a["profile"] == "爸爸")
        store._now = lambda: LATER
        store.delete_appointment(int(mom["id"]))
        store.update_appointment_partial(
            int(dad["id"]), scheduled_at=_day(20) + "T09:00"
        )
        store.import_data(backup)
        final = [
            (a["profile"], str(a["scheduled_at"])[:10])
            for a in store.list_appointments(None, None, False, 100)
            if a["note"] == "n1"
        ]
        mom_due = [d for prof, d in final if prof == "妈妈"]
        dad_due = [d for prof, d in final if prof == "爸爸"]
        assert _day(6) in mom_due, final
        assert _day(20) in dad_due, final
    finally:
        store.close()


def test_cross_profile_twin_tail_tie_noid_no_loss(tmp_path):
    store = _store(tmp_path, "x4-noid")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        backup = {"medications": [
            {"profile": "爸爸", "name": "A", "dose": "2",
             "frequency": "qd", "note": "n2", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "3",
             "frequency": "qd", "note": "n2", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        dad = next(m for m in rows if m["profile"] == "爸爸")
        mom = next(m for m in rows if m["profile"] == "妈妈")
        store._now = lambda: LATER
        store.delete_medication(int(dad["id"]))
        store.update_medication(int(mom["id"]), dose="8")
        store.import_data(backup)
        final = [
            (m["profile"], m["dose"])
            for m in store.list_medications(None, limit=100)
        ]
        dad_doses = [d for prof, d in final if prof == "爸爸"]
        mom_doses = [d for prof, d in final if prof == "妈妈"]
        assert "2" in dad_doses, final
        assert "8" in mom_doses, final
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text,label",
    [
        ("胸痛很厉害、呼吸困难没有", "胸痛"),
        ("说话不清、手脚麻木没有", "言语不清"),
        ("持续胸痛、呼吸困难都没有", "胸痛"),
        ("反复胸痛、呼吸困难都没有", "胸痛"),
    ],
)
def test_trailing_negation_no_overreach(text, label):
    assert label in _labels(text), text


def test_trailing_negation_group_still_negates():
    assert _red("发烧、咳嗽、胸痛都没有")["level"] == "none"
    assert _red("胸痛、呼吸困难都没有")["level"] == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "运动后胸痛是什么病",
        "跑步时胸痛是什么病",
        "打球时胸口疼是什么病",
        "搬东西时胸口疼是什么病",
        "干活的时候胸口痛是什么病",
    ],
)
def test_exertional_definition_question_kept(text):
    assert "胸痛" in _labels(text), text


def test_definition_question_controls():
    assert "胸痛" in _labels("活动后胸痛是什么病")
    assert _red("胸痛是什么病")["level"] == "none"


# ---------------------------------------------------------------- [7]
def test_drift_pure_content_edit_stays_idempotent(tmp_path):
    source = _store(tmp_path, "x7-src")
    source._now = lambda: CREATED
    source.save_profile("妈妈", {})
    source.add_medication("妈妈", "A", dose="1", frequency="qd")
    source.add_medication("妈妈", "A", dose="2", frequency="qd")
    source._now = lambda: EXPORTED
    backup = source.export_data()
    source.close()

    store = _store(tmp_path, "x7-dst")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        for idx in ("X1", "X2", "X3"):
            store.add_medication("妈妈", idx, dose="9", frequency="qd")
        store._now = lambda: EXPORTED
        store.import_data(backup)
        rows = {
            m["dose"]: m
            for m in store.list_medications(None, 100)
            if m["name"] == "A"
        }
        store._now = lambda: LATER
        store.update_medication(rows["1"]["id"], dose="7")
        again = store.import_data(backup)
        doses = sorted(
            m["dose"]
            for m in store.list_medications(None, 100)
            if m["name"] == "A"
        )
        assert again.get("medications", 0) == 0, (again, doses)
        assert doses == ["2", "7"], doses
    finally:
        store.close()


def test_drift_delete_edit_still_restores_deleted(tmp_path):
    source = _store(tmp_path, "x7l-src")
    source._now = lambda: CREATED
    source.save_profile("爸爸", {})
    for dose in ("1", "2", "3", "4", "5"):
        source.add_medication(
            "爸爸", "A", dose=dose, frequency="qd", note="n1"
        )
    for row in source.list_medications("爸爸", limit=10)[:2]:
        source.delete_medication(row["id"])
    source._now = lambda: EXPORTED
    backup = source.export_data()
    source.close()

    store = _store(tmp_path, "x7l-dst")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        for idx in ("1", "2", "3"):
            store.add_medication("爸爸", "X" + idx, dose="1",
                                 frequency="qd")
        store._now = lambda: EXPORTED
        store.import_data(backup)
        rows = {
            m["dose"]: m
            for m in store.list_medications(None, 100)
            if m["name"] == "A"
        }
        store._now = lambda: LATER
        store.delete_medication(rows["3"]["id"])
        store.update_medication(rows["4"]["id"], dose="9")
        again = store.import_data(backup)
        doses = sorted(
            m["dose"]
            for m in store.list_medications(None, 100)
            if m["name"] == "A"
        )
        assert again.get("medications", 0) >= 1, (again, doses)
        assert {"3", "5", "9"} <= set(doses), doses
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_profile_change_does_not_resurrect_deleted_reminder(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        appt = store.add_appointment(
            "爸爸", "复诊：心内", scheduled_at=_day(6) + "T09:00"
        )
        reminder = store.add_reminder(
            "复诊：心内", _day(5), note="复诊 " + _day(6),
            profile="爸爸", appointment_id=int(appt["id"]),
        )
        store.complete_reminder(int(reminder["id"]))
        store.set_app_state(
            f"reminder_deleted_{int(appt['id'])}", LATER
        )
        done_before = store.get_reminder(int(reminder["id"]))["updated_at"]
        store._now = lambda: "2026-11-02T09:00:00"
        store.update_appointment_partial(
            int(appt["id"]), profile="妈妈",
            scheduled_at=_day(8) + "T09:00",
        )
        marker = store.get_app_state(
            f"reminder_deleted_{int(appt['id'])}"
        )
        pending = [
            r for r in store.list_reminders(False, None)
            if int(r["appointment_id"] or 0) == int(appt["id"])
        ]
        done_after = store.get_reminder(int(reminder["id"]))
        assert marker == LATER, marker
        assert pending == [], pending
        assert done_after["updated_at"] == done_before
        assert done_after["profile"] == "妈妈"
    finally:
        store.close()


def test_visit_profile_migration_keeps_tombstone_time(tmp_path):
    store = _store(tmp_path, "x8b")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        appt = store.add_appointment(
            "爸爸", "复诊：心内", scheduled_at=_day(6) + "T09:00"
        )
        reminder = store.add_reminder(
            "复诊：心内", _day(5), note="复诊 " + _day(6),
            profile="爸爸", appointment_id=int(appt["id"]),
        )
        store.complete_reminder(int(reminder["id"]))
        visit = store.add_visit_record(
            "爸爸", visit_date=_day(-2), diagnosis="高血压",
            next_followup_date=_day(6),
        )
        store.set_visit_followup_links(int(visit["id"]), int(appt["id"]), 0)
        done_before = store.get_reminder(int(reminder["id"]))["updated_at"]
        store._now = lambda: "2026-11-02T09:00:00"
        store.update_visit_record_partial(int(visit["id"]), profile="妈妈")
        done_after = store.get_reminder(int(reminder["id"]))
        assert done_after["updated_at"] == done_before
        assert done_after["profile"] == "妈妈"
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_reschedule_writes_back_owner_visit_links(tmp_path):
    store = _store(tmp_path, "x9")
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
            "reminders": [{
                "id": 20, "profile": "妈妈", "title": "复诊：G",
                "due_at": _day(5), "appointment_id": 30, "done": 0,
                "note": "复诊 " + _day(6),
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": _day(6),
                "followup_appointment_id": 0, "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 0
        store._now = lambda: LATER
        store.update_appointment_partial(
            1, scheduled_at=_day(8) + "T09:00"
        )
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 1, visit
        assert int(visit["followup_reminder_id"] or 0) == 1, visit
        assert visit.get("followup_scheduled") is True, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_web_fresh_start_uses_any_matching_question():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("else if (freshStart)")
    block = source[start:start + 720]
    assert "messages.some(" in block, block
    assert "lastUserIndex" not in block, block
    start = source.index("persistedThisRound = messages.some(")
    assert "inflightQuestionText" in source[start:start + 400]


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    ["差点就要撞到头", "差一点点就撞到头", "几乎快要撞到头"],
)
def test_almost_double_adverb_forms(text):
    assert _red(
        "华法林吃了三年，" + text, {}
    )["level"] == "none", text


def test_real_trauma_still_emergency():
    assert "抗凝治疗期间头部外伤" in _labels(
        "华法林吃了三年，撞到头", {}
    )


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "tail", ["没有出很多汗", "没有浑身是汗", "没有汗如雨下",
             "没有流了好多汗"],
)
def test_negated_sweat_intensity_filtered(tail):
    assert _red("胸痛是什么病？" + tail)["level"] == "none", tail


def test_positive_sweat_intensity_kept():
    assert "胸痛" in _labels("胸痛是什么病，出了好多汗")


# ---------------------------------------------------------------- [14][15]
@pytest.mark.parametrize(
    "text",
    [
        "华法林，阿司匹林（停了）",
        "华法林，阿司匹林（不吃了）",
        "华法林，阿司匹林（停了两个月）",
        "华法林，阿司匹林（已停用两个月）",
    ],
)
def test_inline_stop_annotation_forms(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_inline_stop_annotation_control():
    assert api_server._context_medication_names(
        {"medications": "华法林，阿司匹林"}
    ) == ["华法林", "阿司匹林"]
