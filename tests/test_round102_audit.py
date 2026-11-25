# -*- coding: utf-8 -*-
"""第一百零二轮审查修复的回归测试。"""
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


def _level(text, context=None):
    return _red(text, context)["level"]


# ---------------------------------------------------------------- [1][6]
@pytest.mark.parametrize(
    "text",
    [
        "胸部中间疼",
        "胸骨后有点疼痛",
        "胸口一阵阵疼",
        "胸口有一点痛",
        "胸口中间疼",
        "胸骨后隐痛",
        "胸口轻微疼痛",
        "胸口一点点痛",
    ],
)
def test_chest_pain_position_variants(text):
    assert "胸痛" in _labels(text), text


@pytest.mark.parametrize(
    "text",
    [
        "胸口剧痛，冒冷汗",
        "胸部剧痛",
        "心口剧痛",
        "胸口持续性疼痛",
        "胸口针刺样疼痛",
        "胸口撕裂样疼痛",
        "胸口阵发性疼痛",
        "胸口刀割样疼痛",
        "胸口绞着疼",
        "胸口扯着疼",
    ],
)
def test_chest_pain_severity_variants(text):
    assert "胸痛" in _labels(text), text


def test_chest_pain_controls():
    assert "胸痛" in _labels("胸口剧烈疼痛")
    assert _level("没有胸痛") == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "不确定撞没撞到头",
        "不知道碰没碰到头",
        "不记得磕没磕到头",
        "早上摔了一跤，不确定撞没撞到头",
    ],
)
def test_head_trauma_anot_a_question(text):
    assert "抗凝治疗期间头部外伤" in _labels(
        text, {"medications": "华法林"}
    ), text


def test_head_trauma_anot_a_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "不确定有没有撞到头", {"medications": "华法林"}
    )
    assert _level("没有撞到头", {"medications": "华法林"}) == "none"


# ---------------------------------------------------------------- [3]
def test_fever_before_quote_verb_infant():
    assert "3 个月以下婴儿发热" in _labels(
        "宝宝发烧38.5度看了文章说要去医院", {"age": "2个月"}
    )
    assert "3 个月以下婴儿发热" in _labels(
        "宝宝发烧38.5度看了文章", {"age": "2个月"}
    )


def test_fever_before_quote_verb_immuno():
    assert "免疫功能低下伴发热" in _labels(
        "发烧39度网上说要用退烧药", {"history": "正在化疗"}
    )


def test_quote_first_still_filtered():
    assert _level("网上说发烧38度要就医", {"age": "2个月"}) == "none"


# ---------------------------------------------------------------- [7][13]
@pytest.mark.parametrize(
    "history",
    [
        "华法林停了两年，现在在吃阿司匹林",
        "华法林停了两年，目前在用阿司匹林",
        "华法林停了两年，正在服用阿司匹林",
        "华法林停了两年，现在在吃维生素C",
        "华法林停了两年，现在在吃火锅",
    ],
)
def test_progressive_restart_object_attribution(history):
    assert _level("今天头撞到门框", {"history": history}) == "none"


def test_progressive_restart_non_medication_objects():
    assert _level(
        "华法林停了半年，目前在用中药调理，今天头撞到门框"
    ) == "none"
    assert _level(
        "华法林停了半年，现在在用电脑，今天头撞到门框"
    ) == "none"


def test_progressive_restart_current_drug_kept():
    assert _level("华法林停了半年，现在吃着，今天头撞到门框") == "emergency"
    assert _level("华法林停了半年，目前用着，今天头撞到门框") == "emergency"
    assert _level(
        "今天头撞到门框",
        {"history": "华法林停了两年，现在在吃华法林"},
    ) == "emergency"
    assert _level(
        "今天头撞到门框",
        {"history": "warfarin was stopped 2 years ago but now I am using"
                    " it again"},
    ) == "emergency"


# ---------------------------------------------------------------- [7][13] 用药
def test_medication_restart_other_drug_titles():
    result = check_medication_safety(
        "华法林停了两年，现在在吃阿司匹林，能吃布洛芬吗", {}
    )
    assert [a.get("title") for a in result["alerts"]] == [
        "重复使用非甾体抗炎药"
    ], result
    result = check_medication_safety(
        "warfarin was stopped 2 years ago but now I am on aspirin,"
        " can I take ibuprofen", {}
    )
    assert [a.get("title") for a in result["alerts"]] == [
        "重复使用非甾体抗炎药"
    ], result


def test_medication_restart_non_medication_objects():
    assert check_medication_safety(
        "华法林停了半年，目前在用中药调理，能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "华法林停了半年，现在在用手机，能吃布洛芬吗", {}
    )["level"] == "none"


def test_medication_restart_current_drug_kept():
    assert check_medication_safety(
        "华法林停了半年，现在吃着，能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "warfarin was stopped 2 years ago but now I am using it again."
        " Can I take ibuprofen?", {}
    )["level"] == "warning"


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "停用华法林可不可以，能吃布洛芬吗",
        "停用华法林好不好，能吃布洛芬吗",
        "停用华法林能停吗，能吃布洛芬吗",
        "华法林停用可不可以，能吃布洛芬吗",
    ],
)
def test_stop_question_synonyms(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_stop_statement_control():
    assert check_medication_safety(
        "停用华法林，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [18]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林（停用 2 个月）",
        "华法林、阿司匹林（停了 2 个月）",
        "华法林、阿司匹林（已停用）",
        "华法林、阿司匹林（停用）",
        "华法林、阿司匹林（不吃了）",
    ],
)
def test_chat_bracket_stop_annotations(text):
    assert check_medication_safety(
        "今天有点头疼", {"medications": text}
    )["level"] == "none", text


def test_chat_bracket_control_still_in_use():
    assert check_medication_safety(
        "今天有点头疼", {"medications": "华法林、阿司匹林"}
    )["level"] == "warning"


# ---------------------------------------------------------------- [4]
def test_same_second_unrelated_reminder_not_mask_deleted(tmp_path):
    store = _store(tmp_path, "x4-rem")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.add_reminder("无关0", _day(40), note="u", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "due_at": _day(9), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = sorted(store.list_reminders(False, "妈妈"),
                      key=lambda r: r["due_at"])
        store._now = lambda: LATER
        store.delete_reminder(int(rows[0]["id"]))
        rows = store.list_reminders(False, "妈妈")
        target = next(r for r in rows if str(r["due_at"])[:10] == _day(9))
        store.update_reminder(int(target["id"]), due_at=_day(20))
        store.import_data(backup)
        dues = sorted(
            str(r["due_at"])[:10]
            for r in store.list_reminders(False, "妈妈")
            if r["note"] == "n1"
        )
        assert _day(6) in dues, dues
        assert _day(20) in dues, dues
    finally:
        store.close()


def test_same_second_unrelated_appointment_not_mask_deleted(tmp_path):
    store = _store(tmp_path, "x4-appt")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.add_appointment(
            "妈妈", "无关0", scheduled_at=_day(40) + "T09:00"
        )
        backup = {"appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "scheduled_at": _day(6) + "T09:00", "status": "upcoming",
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "scheduled_at": _day(9) + "T09:00", "status": "upcoming",
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = sorted(
            (a for a in store.list_appointments(None, None, False, 100)
             if a["note"] == "n1"),
            key=lambda a: a["scheduled_at"],
        )
        store._now = lambda: LATER
        store.delete_appointment(int(rows[0]["id"]))
        rows = [
            a for a in store.list_appointments(None, None, False, 100)
            if a["note"] == "n1"
        ]
        store.update_appointment_partial(
            int(rows[0]["id"]), scheduled_at=_day(20) + "T09:00"
        )
        store.import_data(backup)
        days = sorted(
            str(a["scheduled_at"])[:10]
            for a in store.list_appointments(None, None, False, 100)
            if a["note"] == "n1"
        )
        assert _day(6) in days, days
        assert _day(20) in days, days
    finally:
        store.close()


def test_same_second_unrelated_symptom_not_mask_deleted(tmp_path):
    store = _store(tmp_path, "x4-sym")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.add_symptom("妈妈", "头痛", 2, recorded_at=_day(-40))
        backup = {"symptoms": [
            {"id": 1, "profile": "妈妈", "symptom": "胸闷",
             "severity": 3, "note": "n1", "recorded_at": _day(-6),
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "symptom": "胸痛",
             "severity": 4, "note": "n1", "recorded_at": _day(-9),
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = sorted(
            (s for s in store.list_symptoms("妈妈", 100, 0)
             if s["note"] == "n1"),
            key=lambda s: s["recorded_at"],
        )
        store._now = lambda: LATER
        store.delete_symptom(int(rows[0]["id"]))
        rows = [
            s for s in store.list_symptoms("妈妈", 100, 0)
            if s["note"] == "n1"
        ]
        store.update_symptom_partial(
            int(rows[0]["id"]), recorded_at=_day(-20)
        )
        store.import_data(backup)
        days = sorted(
            str(s["recorded_at"])[:10]
            for s in store.list_symptoms("妈妈", 100, 0)
            if s["note"] == "n1"
        )
        assert _day(-6) in days, days
        assert _day(-20) in days, days
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
def test_noid_cross_profile_twin_delete_edit_restores(tmp_path):
    store = _store(tmp_path, "x5")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "复诊", "due_at": _day(6),
             "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "爸爸", "title": "复诊", "due_at": _day(6),
             "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "title": "X", "due_at": _day(12),
             "note": "n3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(False, None)
        mom_t = next(
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "复诊"
        )
        dad = next(r for r in rows if r["profile"] == "爸爸")
        store._now = lambda: LATER
        store.delete_reminder(int(dad["id"]))
        store.update_reminder(int(mom_t["id"]), note="nX")
        store.import_data(backup)
        final = store.list_reminders(False, None)
        dad_rows = [r for r in final if r["profile"] == "爸爸"]
        assert any(r["note"] == "n2" for r in dad_rows), final
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_twin_restore_chain_missing_origin_restores_tail(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "nA", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊",
             "due_at": _day(6), "note": "nB", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        a = next(r for r in store.list_reminders(False, "妈妈")
                 if r["note"] == "nA")
        store.delete_reminder(int(a["id"]))
        store.import_data(backup)
        rows = store.list_reminders(False, "妈妈")
        b = next(r for r in rows if r["note"] == "nB")
        a_copy = next(r for r in rows if r["note"] == "nA")
        store._now = lambda: LATER
        store.delete_reminder(int(b["id"]))
        store.update_reminder(int(a_copy["id"]), note="nX")
        store.import_data(backup)
        notes = sorted(
            r["note"] for r in store.list_reminders(False, "妈妈")
        )
        assert "nB" in notes, notes
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def _half_linked_store(store):
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


def test_cancel_appointment_cleans_half_linked_reminder(tmp_path):
    store = _store(tmp_path, "x9a")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _half_linked_store(store)
        store._now = lambda: LATER
        store.update_appointment_partial(1, status="cancelled")
        visit = store.get_visit_record(1)
        assert store.list_reminders(False, None) == []
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        store.close()


def test_delete_appointment_cleans_half_linked_reminder(tmp_path):
    store = _store(tmp_path, "x9b")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        _half_linked_store(store)
        store._now = lambda: LATER
        store.delete_appointment(1)
        visit = store.get_visit_record(1)
        assert store.list_reminders(False, None) == []
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_delete_half_linked_reminder_writes_tombstone(tmp_path):
    store = _store(tmp_path, "x10")
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
                "due_at": _day(3), "appointment_id": 0, "done": 0,
                "note": "复诊 " + _day(6),
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 10, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": _day(6),
                "followup_appointment_id": 0, "followup_reminder_id": 20,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        store._now = lambda: LATER
        store.delete_reminder(1)
        assert store.get_app_state("reminder_deleted_1"), "墓碑未写入"
        store.update_appointment_partial(
            1, scheduled_at=_day(8) + "T09:00"
        )
        assert store.list_reminders(False, None) == []
    finally:
        store.close()


# ---------------------------------------------------------------- [15]
def test_linked_reschedule_merges_detached_reminder(tmp_path):
    store = _store(tmp_path, "x15")
    store._now = lambda: EXPORTED
    try:
        store.save_profile("妈妈", {})
        backup = {
            "appointments": [{
                "id": 30, "profile": "妈妈", "title": "复诊：G",
                "note": "由就诊记录 #10 自动创建", "owner_visit_id": 10,
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
                "followup_appointment_id": 30, "followup_reminder_id": 20,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        store._now = lambda: LATER
        store.update_appointment_partial(
            1, scheduled_at=_day(8) + "T09:00"
        )
        pending = store.list_reminders(False, None)
        visit = store.get_visit_record(1)
        assert len(pending) == 1, pending
        assert int(visit["followup_reminder_id"] or 0) == int(
            pending[0]["id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [11][12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、停了两个月的阿司匹林",
        "华法林、已停用两个月的阿司匹林",
        "华法林、停药两个月的阿司匹林",
    ],
)
def test_duration_before_drug_name_keeps_previous(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林 两个月前已停用",
        "华法林、阿司匹林 2 个月前已停用",
        "华法林、阿司匹林 半年前已停用",
        "华法林、阿司匹林 1 片已停用",
    ],
)
def test_space_separated_duration_stop_annotation(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_medication_names_controls():
    names = api_server._context_medication_names
    assert names({"medications": "华法林、停用 2 个月"}) == []
    assert names({"medications": "华法林、阿司匹林（停用 2 个月）"}) == [
        "华法林"
    ]
    assert names({"medications": "华法林、二甲双胍"}) == [
        "华法林", "二甲双胍"
    ]
    assert names({"medications": "华法林、已停药（2025年10月）"}) == []


# ---------------------------------------------------------------- [16][17]
def test_web_inflight_clear_keeps_owned_record_without_session():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("function clearInflightForCurrentSession()")
    block = source[start:start + 700]
    assert "if (owner && owner !== (sessionId || '')) return;" in block
    assert "owner && sessionId && owner !== sessionId" not in block


def test_web_pending_new_draft_covers_unflushed_owner():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("const pendingDraftOwner")
    block = source[start:start + 420]
    assert "pendingDraftOwner === null" in block, block
    assert "(owner === '' || owner === null) && pendingSessionReset" in (
        source
    )
