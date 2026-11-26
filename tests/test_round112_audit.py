# -*- coding: utf-8 -*-
"""第一百一十二轮审查修复的回归测试。"""
from datetime import date, timedelta
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


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


WARFARIN = {"age": "60岁", "medications": "华法林 3mg 每日一次"}


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，疼得受不了",
        "胸痛是什么病，很疼",
        "胸痛是什么病，特别疼",
        "胸痛是什么病，疼得直冒汗",
    ],
)
def test_definition_severity_colloquial(text):
    # 口语严重度分句同样是病程证据（第一百一十二轮 [1]）
    assert "胸痛" in _labels(text), _red(text)


def test_definition_severity_controls():
    assert "胸痛" in _labels("胸痛是什么病，疼得厉害")


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周，已经不流血了",
        "孕8周，现在不出血了",
        "孕8周，没再流血了",
    ],
)
def test_pregnancy_bleeding_stopped(text):
    # 曾经出血、现已停止仍需排查宫外孕/先兆流产（第一百一十二轮 [2]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_bleeding_stopped_controls():
    assert _level("孕8周，没有流血") == "none"
    assert "孕产期出血" in _labels("怀孕8周，阴道出血已经没有了")


# ---------------------------------------------------------------- [3][5]
@pytest.mark.parametrize(
    "text",
    [
        "咯血是什么病，需要去医院检查一下吗",
        "胸痛是什么病，需要去医院么",
        "抽搐是什么病，要去医院看一下吗",
        "咯血是什么病，需要去医院抽血吗",
    ],
)
def test_urgent_help_later_question_mark(text):
    # 疑问标记可在同分句稍后出现（第一百一十二轮 [3]）
    assert bool(_labels(text)), _red(text)


def test_urgent_help_urgent_adverb():
    # 紧迫副词 + 医院可省略疑问标记（第一百一十二轮 [5]）
    assert "胸痛" in _labels("胸痛是什么病，需要马上去医院")


def test_urgent_help_statement_still_none():
    assert _level("什么是咯血，我明天要去医院体检") == "none"


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize("text", ["乳头上起了个包", "鼻头起了个大包", "龟头起了个包"])
def test_non_head_swelling_not_trauma(text):
    # 乳头/鼻头/龟头起包不是头部外伤（第一百一十二轮 [15]）
    assert _level(text, WARFARIN) == "none", _red(text, WARFARIN)


def test_head_swelling_control():
    assert "抗凝治疗期间头部外伤" in _labels("头上起了个大包", WARFARIN)


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "I stopped taking warfarin three days ago, can I take ibuprofen?",
        "I stopped taking warfarin four days ago, can I take ibuprofen?",
        "I stopped taking warfarin several days ago,"
        " can I take ibuprofen?",
    ],
)
def test_english_recent_stop_days(text):
    # 英文三天/四天/数天前的近期停药仍按在用（第一百一十二轮 [4]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_english_recent_stop_control():
    result = check_medication_safety(
        "I stopped taking warfarin two years ago, can I take ibuprofen?",
        {},
    )
    assert result["level"] == "none"


# ---------------------------------------------------------------- [11]
def test_english_resumed_negation():
    result = check_medication_safety(
        "I stopped warfarin two years ago. I never resumed it."
        " Can I take ibuprofen?",
        {},
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result
    result = check_medication_safety(
        "I stopped warfarin two years ago, resumed aspirin instead."
        " Can I take ibuprofen?",
        {},
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result


def test_english_resumed_control():
    result = check_medication_safety(
        "I stopped warfarin last year but I resumed it."
        " Can I take ibuprofen?",
        {},
    )
    assert "华法林与非甾体抗炎药" in _titles(result)


# ---------------------------------------------------------------- [8]
def test_allergy_statement_not_active_drug():
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次，对阿司匹林过敏"}
    )
    result = api_server._evaluate_medication_safety(
        names, ["阿司匹林"], "62", "女", [], []
    )
    # 过敏陈述不能作为在用药物产生假相互作用（第一百一十二轮 [8]）
    assert "华法林与非甾体抗炎药" not in _titles(result), result
    allergy_names = api_server._context_medication_names(
        {"medications": "阿莫西林 0.5g 每日三次"}
    )
    allergy_result = api_server._evaluate_medication_safety(
        allergy_names, ["青霉素"], "62", "女", [], []
    )
    assert any("过敏" in str(t) for t in _titles(allergy_result))


# ---------------------------------------------------------------- [6]
def test_rescheduled_reminder_claims_before_budget(tmp_path):
    store = _store(tmp_path, "x6")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.add_reminder("干扰", _day(9), note="x", profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复查A",
             "due_at": _day(19), "note": "n1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复查B",
             "due_at": _day(19), "note": "n2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(True, "妈妈")
        a_row = next(r for r in rows if r["title"] == "复查A")
        b_row = next(r for r in rows if r["title"] == "复查B")
        store._now = lambda: "2026-10-01T09:00:00"
        store.update_reminder(int(a_row["id"]), due_at=_day(25))
        store.delete_reminder(int(b_row["id"]))
        added = store.import_data(backup).get("reminders")
        rows = store.list_reminders(True, "妈妈")
        a_rows = [r for r in rows if r["title"] == "复查A"]
        # 改期行先于补插预算认领，只补回被删的复查B
        # （第一百一十二轮 [6]）
        assert added == 1, [(r["title"], str(r["due_at"])[:10]) for r in rows]
        assert len(a_rows) == 1
        assert str(a_rows[0]["due_at"])[:10] == _day(25)
    finally:
        store.close()


# ---------------------------------------------------------------- [7]
def test_schedule_reuses_completed_leftover(tmp_path):
    d0 = _day(20)
    store = _store(tmp_path, "x7")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.import_data({
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-10-01",
                 "diagnosis": "高血压", "next_followup_date": d0,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": d0 + "T09:00", "status": "completed",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        result = api_server._schedule_followup_sync(store, vid)
        visit = store.get_visit_record(vid)
        same_day = [
            a for a in store.list_appointments("妈妈", None)
            if str(a.get("scheduled_at") or "")[:10] == d0
        ]
        # 同日已完成预约是复诊历史：回挂而不是新建第二条
        # （第一百一十二轮 [7]）
        assert result.get("appointment_created") is False, result
        assert len(same_day) == 1, same_day
        assert int(visit["followup_appointment_id"] or 0) != 0
    finally:
        store.close()


# ---------------------------------------------------------------- [12][14]
def test_import_cleans_all_marker_leftovers(tmp_path):
    d0 = _day(24)
    store = _store(tmp_path, "x14")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.import_data({
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-10-01",
                 "diagnosis": "高血压", "next_followup_date": d0,
                 "followup_appointment_id": 30,
                 "followup_reminder_id": 20},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": d0 + "T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(18) + "T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 32, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(12) + "T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 20, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(23), "note": "复诊",
                 "appointment_id": 30, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 21, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(17), "note": "复诊",
                 "appointment_id": 31, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 22, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(11), "note": "复诊",
                 "appointment_id": 32, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        owned = [
            a for a in store.list_appointments("妈妈", None)
            if a["status"] == "upcoming"
            and (
                int(a.get("owner_visit_id") or 0) == vid
                or f"由就诊记录 #{vid} 自动创建" in str(a.get("note") or "")
            )
        ]
        # 多条标记型遗留自动预约全部清理（第一百一十二轮 [14]）
        assert len(owned) == 1, owned
    finally:
        store.close()


def test_import_dup_cleanup_clears_shared_reference(tmp_path):
    d0 = _day(24)
    source = _store(tmp_path, "x12s")
    try:
        source._now = lambda: CREATED
        source.save_profile("妈妈", {})
        v = int(source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        a_new = int(source.add_appointment(
            "妈妈", "复诊：复查", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{v} 自动创建", owner_visit_id=v,
        )["id"])
        r_new = int(source.add_reminder(
            "复诊：复查", _day(23), note="复诊",
            profile="妈妈", appointment_id=a_new,
        )["id"])
        source.set_visit_followup_links(v, a_new, r_new)
        backup = source.export_data()
    finally:
        source.close()
    store = _store(tmp_path, "x12")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v1 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d0,
        )["id"])
        a_old = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=_day(18) + "T09:00",
            note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
        )["id"])
        r_old = int(store.add_reminder(
            "复诊：高血压", _day(17), note="复诊",
            profile="妈妈", appointment_id=a_old,
        )["id"])
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-02", diagnosis="高血压",
            next_followup_date=_day(14),
        )["id"])
        store.set_visit_followup_links(v2, 0, r_old)
        store.import_data(backup)
        v2_row = store.get_visit_record(v2)
        # 删除被共享的待办提醒时同步清空其它就诊的引用
        # （第一百一十二轮 [12]）
        assert int(v2_row["followup_reminder_id"] or 0) == 0, v2_row
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
def test_no_id_symptom_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "x13")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        backup = {"symptoms": [
            {"profile": "妈妈", "symptom": "咳嗽",
             "recorded_at": "2026-11-02T09:00:00", "severity": 2,
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "symptom": "咳嗽",
             "recorded_at": "2026-11-02T09:00:00", "severity": 2,
             "note": "n2", "created_at": CREATED, "updated_at": CREATED},
        ]}
        assert store.import_data(backup).get("symptoms") == 2
        rows = store.list_symptoms("妈妈", 100000)
        n1 = next(r for r in rows if r["note"] == "n1")
        n2 = next(r for r in rows if r["note"] == "n2")
        store._now = lambda: "2026-10-01T09:00:00"
        store.delete_symptom(int(n2["id"]))
        store.update_symptom_partial(int(n1["id"]), severity=8)
        first = store.import_data(backup).get("symptoms")
        second = store.import_data(backup).get("symptoms")
        rows = store.list_symptoms("妈妈", 100000)
        # 无 id 症状孪生：重复导入保持幂等（第一百一十二轮 [13]）
        assert first == 1, [(r["note"], r["severity"]) for r in rows]
        assert second == 0
        assert len(rows) == 2
    finally:
        store.close()


# ---------------------------------------------------------------- [9][10]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_inflight_fresh_session_gate():
    source = _app_js()
    start = source.index("function restoreInflightForOpenSession")
    block = source[start:start + 6000]
    # 缺 baseline 的记录只在「本页新建会话」时按文本判已落库
    # （第一百一十二轮 [9]）
    assert "const freshSession = isFreshPageSession(targetId);" in block
    assert "if (!hasIds && freshSession)" in block
    assert "} else if (freshSession) {" in block


def test_web_send_profile_snapshot():
    source = _app_js()
    start = source.index("async function send(")
    # send 前部新增启动快捷方式等待（第一百一十六轮 [17]）：窗口放宽
    block = source[start:start + 14000]
    # 档案守卫比较发送时刻的下拉快照（第一百一十二轮 [10]）
    assert "const selectProfileAtSend = els.profileSelect.value || '';" in block
    assert "!== selectProfileAtSend" in block
    start = source.index("function setActiveProfile(name) {")
    block = source[start:start + 400]
    assert "const cacheLoaded =" in block
