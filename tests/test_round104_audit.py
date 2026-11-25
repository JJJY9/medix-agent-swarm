# -*- coding: utf-8 -*-
"""第一百零四轮审查修复的回归测试。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-02T09:00:00"


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


def _alert_titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了两年，最近在吃，今天头撞到门框",
        "华法林停了两年，现在吃上了，今天头撞到门框",
        "华法林停了两年，最近吃上了，今天头撞到门框",
        "华法林停了两年，近来一直服用，今天头撞到门框",
    ],
)
def test_recent_restart_text_keeps_anticoagulation(text):
    assert "抗凝治疗期间头部外伤" in _labels(text), text


@pytest.mark.parametrize(
    "text",
    [
        "华法林停了两年，最近在吃，能吃布洛芬吗",
        "华法林停了两年，现在吃上了，能吃布洛芬吗",
        "华法林停了两年，最近吃上了，能吃布洛芬吗",
    ],
)
def test_recent_restart_text_medication_channel(text):
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _alert_titles(result), result


def test_recent_restart_object_attribution():
    # 换药后不能复活被停药物
    result = check_medication_safety(
        "华法林停了两年，最近在吃阿司匹林，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" not in _alert_titles(result), result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "上个月剖宫产，阴道出血",
        "一个月前剖腹产，阴道出血",
        "上周剖宫产，阴道出血",
        "两周前剖宫产，阴道出血",
        "十天前剖宫产了，阴道出血",
        "两个月前剖宫产，阴道出血",
        "六周前剖宫产，阴道出血",
    ],
)
def test_recent_caesarean_time_before(text):
    assert "孕产期出血" in _labels(text), text


def test_recent_caesarean_history_context():
    assert "孕产期出血" in _labels(
        "阴道出血", {"history": "上个月剖宫产"}
    )


def test_recent_caesarean_controls():
    assert _level("三年前剖宫产，阴道出血") == "none"
    assert _level("一年前剖宫产，阴道出血") == "none"
    assert _level("十二个月前剖宫产，阴道出血") == "none"
    assert "孕产期出血" in _labels("剖宫产术后10天，阴道出血")


# ---------------------------------------------------------------- [3]
def test_tied_sibling_survive_respects_missing_group_rows(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("爸爸", {})
        store.save_profile("妈妈", {})
        backup_a = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊", "due_at": _day(20),
             "note": "a1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊", "due_at": _day(10),
             "note": "a2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "title": "吃药", "due_at": _day(20),
             "note": "a3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        backup_b = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "due_at": _day(20),
             "note": "b1", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊", "due_at": _day(20),
             "note": "b2", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "妈妈", "title": "吃药", "due_at": _day(10),
             "note": "b3", "done": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_a)
        store.import_data(backup_b)
        store._now = lambda: LATER
        ids = {
            (r["profile"], r["note"]): int(r["id"])
            for r in store.list_reminders(None)
        }
        store.delete_reminder(ids[("爸爸", "a1")])
        store.delete_reminder(ids[("爸爸", "a2")])
        added = store.import_data(backup_a).get("reminders")
        rows = {
            (r["profile"], r["note"])
            for r in store.list_reminders(None)
        }
        # 组内确有缺行：被删的 a1 必须按原内容补回，兄弟行的存活
        # 行不能证明候选（b2）属于 a1（第一百零四轮 [3]）
        assert added == 2, rows
        assert ("爸爸", "a1") in rows, rows
        assert store.import_data(backup_a).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_same_origin_key_collision_restores_deleted_twin(tmp_path):
    store = _store(tmp_path, "x4")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup_a = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "due_at": _day(20),
             "note": "a1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        backup_b = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊", "due_at": _day(20),
             "note": "b1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "复诊", "due_at": _day(20),
             "note": "b2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_a)
        store.import_data(backup_b)
        ids = {
            (r["profile"], r["note"]): int(r["id"])
            for r in store.list_reminders(None)
        }
        store._now = lambda: LATER
        store.delete_reminder(ids[("妈妈", "a1")])
        store.delete_reminder(ids[("爸爸", "b2")])
        store.import_data(backup_b)
        notes = [r["note"] for r in store.list_reminders(None)]
        # 同源自认领不能把兄弟行判成本行存活版本：b2 补回且 b1 不复制
        assert notes.count("b2") == 1, notes
        assert notes.count("b1") == 1, notes
        assert store.import_data(backup_b).get("reminders") == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "胸部正中偏左压榨样疼痛，冒冷汗",
        "胸部正中偏右压榨样疼痛",
        "胸部正中偏上疼痛",
        "胸部正中偏下疼痛",
        "胸骨中段偏左疼痛",
        "胸部中间偏左疼痛，大汗",
        "胸部正中央偏左疼痛",
        "胸部中线偏左疼痛",
        "胸部偏左疼痛",
        "胸部左侧疼痛",
        "胸部右边疼痛",
    ],
)
def test_chest_pain_direction_variants(text):
    assert "胸痛" in _labels(text), text


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "试管婴儿移植后14天，出血",
        "做试管婴儿移植后第10天，阴道出血",
    ],
)
def test_ivf_test_tube_infant_transfer_bleeding(text):
    assert "孕产期出血" in _labels(text), text


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "我的父亲在吃华法林，我今天头撞到门框",
        "我的妈妈在吃华法林，我今天头撞到门框",
        "我的父亲在化疗，我今天发烧38度",
    ],
)
def test_my_family_member_not_patient_subject(text):
    assert _level(text, {"age": "40岁"}) == "none", text


def test_my_family_subject_controls():
    assert _level("我父亲在吃华法林，我今天头撞到门框", {"age": "40岁"}) == "none"
    assert "抗凝治疗期间头部外伤" in _labels(
        "我在吃华法林，今天头撞到门框", {"age": "40岁"}
    )


# ---------------------------------------------------------------- [8]
def test_foreign_backup_row_uses_claim_owner(tmp_path):
    store = _store(tmp_path, "x8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup_a = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "复诊", "due_at": _day(20),
             "note": "a1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        backup_b = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药", "due_at": _day(20),
             "note": "b1", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药", "due_at": _day(20),
             "note": "b2", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "title": "吃药", "due_at": _day(10),
             "note": "b3", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup_a)
        store.import_data(backup_b)
        ids = {
            (r["profile"], r["note"]): int(r["id"])
            for r in store.list_reminders(None)
        }
        store._now = lambda: LATER
        store.delete_reminder(ids[("爸爸", "b1")])
        store.update_reminder(ids[("妈妈", "b2")], profile="爸爸")
        added = store.import_data(backup_b).get("reminders")
        pairs = {
            (r["profile"], r["note"])
            for r in store.list_reminders(None)
        }
        # 兄弟行本轮自己认领的恢复副本算它的存活证据：改挂行仍归
        # 本行，不再插入旧档案副本（第一百零四轮 [8]）
        assert added == 1, pairs
        assert ("妈妈", "b2") not in pairs, pairs
        assert ("爸爸", "b2") in pairs, pairs
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_kept_appointment_adopts_attached_reminder(tmp_path):
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0, d1 = _day(5), _day(10)
        visit = store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )
        vid = int(visit["id"])
        a1 = store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            owner_visit_id=vid,
        )
        r1 = store.add_reminder(
            "复诊：X", _day(4), note="复诊 " + d0, profile="妈妈",
            appointment_id=int(a1["id"]),
        )
        store.set_visit_followup_links(vid, int(a1["id"]), int(r1["id"]))
        store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d1 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )
        store._now = lambda: LATER
        store.update_visit_record_partial(vid, next_followup_date=d1)
        v = store.get_visit_record(vid)
        pending = store.list_reminders(False, "妈妈")
        # 提醒仍挂在旧链接预约上时先脱钩再接管：改期后仍要有待办
        # 提醒且挂在新预约上（第一百零四轮 [9]）
        assert int(v["followup_appointment_id"] or 0) > 0, v
        assert int(v["followup_reminder_id"] or 0) > 0, v
        assert len(pending) == 1, pending
        assert int(pending[0]["appointment_id"] or 0) == int(
            v["followup_appointment_id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_reschedule_leftover_not_hijack_visit_reminder(tmp_path):
    store = _store(tmp_path, "x10")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        d0, d1, d2 = _day(5), _day(10), _day(15)
        visit = store.add_visit_record(
            "妈妈", visit_date=_day(-3), diagnosis="高血压",
            next_followup_date=d0,
        )
        vid = int(visit["id"])
        a1 = store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d0 + "T09:00",
            owner_visit_id=vid,
        )
        r1 = store.add_reminder(
            "复诊：X", _day(4), note="复诊 " + d0, profile="妈妈",
        )
        store.set_visit_followup_links(vid, int(a1["id"]), int(r1["id"]))
        leftover = store.add_appointment(
            "妈妈", "复诊：X", scheduled_at=d1 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )
        store._now = lambda: LATER
        store.update_appointment_partial(
            int(leftover["id"]), scheduled_at=d2 + "T09:00"
        )
        rem = store.get_reminder(int(r1["id"]))
        # 就诊已链接另一条待就诊预约：改期遗留预约不能劫持其提醒
        assert int(rem["appointment_id"] or 0) == 0, rem
        assert str(rem["due_at"])[:10] == _day(4), rem
        store.update_appointment_partial(int(a1["id"]), status="cancelled")
        rem_after = store.get_reminder(int(r1["id"]))
        # 取消真正的复诊预约后不能留下被改写成 E 日期的幽灵提醒
        assert rem_after is None or (
            int(rem_after["appointment_id"] or 0) == 0
            and str(rem_after["due_at"])[:10] == _day(4)
        ), rem_after
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "停用阿司匹林后吃氯吡格雷",
        "停药后服用氯吡格雷",
        "停用阿司匹林后服用氯吡格雷",
        "停用阿司匹林现在在吃氯吡格雷",
        "停用阿司匹林改用氯吡格雷",
    ],
)
def test_stop_then_start_new_drug_kept(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["氯吡格雷"], text


# ---------------------------------------------------------------- [12][16]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林 一年多前已停用",
        "华法林、阿司匹林 一年半前已停用",
        "华法林、阿司匹林 半年多前已停用",
        "华法林、阿司匹林 两周半前已停用",
        "华法林、阿司匹林 三个月左右前已停用",
        "华法林、阿司匹林 两年半前已停用",
        "华法林、阿司匹林 两年多前已停用",
        "华法林、阿司匹林 两个月零五天前已停用",
    ],
)
def test_compound_relative_stop_duration(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_compound_relative_stop_control():
    assert api_server._context_medication_names(
        {"medications": "华法林、阿司匹林 两个月前已停用"}
    ) == ["华法林"]


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林（停用两个月零五天）",
        "华法林、阿司匹林（停用两周半）",
        "华法林、阿司匹林（停用了两周半）",
        "华法林、阿司匹林停用两个月零五天",
    ],
)
def test_bracket_compound_stop_duration(text):
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林"], text


def test_chat_bracket_compound_stop_duration():
    assert check_medication_safety(
        "阿司匹林（停用两周半），能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "阿司匹林（停用两个月零五天），能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    ["华法林，不吃了", "华法林，停止服用", "华法林，停止使用了", "华法林，不再吃了"],
)
def test_standalone_stop_annotation(text):
    assert api_server._context_medication_names({"medications": text}) == [], text


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "obj",
    [
        "这个药物", "这种药物", "那个药物", "该药物", "此药物",
        "这种药片", "原来的药", "之前的药", "这个药", "那种药",
    ],
)
def test_restart_pronoun_drug_forms(obj):
    result = check_medication_safety(
        f"华法林停了半年，现在在吃{obj}，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _alert_titles(result), (obj, result)


def test_restart_pronoun_drug_forms_control():
    result = check_medication_safety(
        "华法林停了半年，现在在吃感冒药，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" not in _alert_titles(result)


# ---------------------------------------------------------------- [13][18]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_open_session_strips_persisted_inflight():
    source = _app_js()
    start = source.index("function restoreInflightForOpenSession")
    end = source.index("function restoreInflightQuestion", start)
    block = source[start:end]
    # 已落库分支要登记并剥离输入框里的同一主诉（第一百零四轮 [13]）
    persisted = block[block.index("if (persisted) {"):]
    persisted = persisted[:persisted.index("removeInflightRecord(record);")]
    assert "notePersistedInflight(question, targetId);" in persisted
    assert "stripInflightQuestion(els.input.value, question)" in persisted
    assert "clearDraft();" in persisted
    # 注入后返回 true 供调用方保留提示（[18]）
    assert "return true;\n}" in block


def test_web_inflight_notice_not_overwritten():
    source = _app_js()
    # openSession：只有未注入时才写「历史会诊」
    assert "if (!inflightRestored) setVitals('idle', '历史会诊');" in source
    start = source.index("inflightReconciledThisRestore = inflightReconciled;")
    block = source[start:start + 900]
    assert "inflightRestored = Boolean(restoredInflight);" in block
    assert "} else if (inflightRestored) {" in block
    assert "上次中断的主诉已放回输入框，请确认后发送" in block
