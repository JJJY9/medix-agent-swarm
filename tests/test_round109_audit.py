# -*- coding: utf-8 -*-
"""第一百零九轮审查修复的回归测试。"""
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
        "摔了一跤，头也撞到了",
        "头都撞出血了",
        "头真的撞到门框了",
        "头确实撞到了",
        "头一下撞到门框",
        "头上也撞了个包",
    ],
)
def test_head_trauma_adverb_variants(text):
    # 常见副词/系词插入不改变头部外伤性质（第一百零九轮 [1]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), text


def test_head_trauma_adverb_controls():
    assert _level("头差点撞到门框", WARFARIN) == "none"
    assert "抗凝治疗期间头部外伤" in _labels("头撞到门框", WARFARIN)


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "孕12+3，出血了",
        "孕12+3，下体出血",
        "孕37+2，见红",
        "孕35+5，阴道流血",
    ],
)
def test_pregnancy_week_shorthand(text):
    # 产检简写「孕12+3」（12 周+3 天）同样是妊娠证据（第一百零九轮 [2]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_week_shorthand_controls():
    assert "孕产期出血" in _labels(
        "今天阴道出血", {"history": "孕12+3"}
    )
    assert "孕产期出血" in _labels("怀孕12+3周，出血了")
    assert "孕产期出血" in _labels("孕12周+3天，出血了")


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "摔了一跤，后脑勺还在流血",
        "摔了一跤，后脑勺一直流血",
        "后脑勺在流血",
        "后脑勺流了点血",
        "后脑勺还在渗血",
        "摔倒后头顶还在流血",
        "额头还在出血",
    ],
)
def test_head_trauma_continuous_bleeding(text):
    # 持续/量化出血语序同属头部外伤（第一百零九轮 [4]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), text


def test_head_trauma_continuous_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "摔了一跤，后脑勺流血了", WARFARIN
    )


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，我有点疼",
        "胸痛是什么病，现在还有点疼",
        "胸痛是什么病，还是疼",
    ],
)
def test_definition_current_symptom_clause(text):
    # 未带固定严重度词的口语当前症状同样是病程证据（第一百零九轮 [6]）
    assert "胸痛" in _labels(text), _red(text)


def test_definition_current_symptom_controls():
    assert "抽搐" in _labels("抽搐是什么病，孩子现在还在抽")
    assert "胸痛" in _labels("胸痛是什么病，现在还疼")
    assert _level("什么是胸痛") == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "我想问问什么是咯血",
        "帮我查一下什么是胸痛",
        "我想问个问题：什么是咯血",
    ],
)
def test_educational_question_frames(text):
    # 科普提问框架剥离后纯定义问句不能触发急症横幅（第一百零九轮 [7]）
    assert _level(text) == "none", _red(text)


def test_educational_question_controls():
    assert _level("我想知道什么是咯血") == "none"


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，我爸咳嗽，需要去医院吗",
        "什么是胸痛？我爸咳嗽，需要去医院吗",
    ],
)
def test_family_help_clause_not_patient(text):
    # 家属分句及其无主语的求助续句不升级患者本人定义问句
    # （第一百零九轮 [14]）
    assert _level(text) == "none", _red(text)


def test_family_help_clause_controls():
    assert "胸痛" in _labels("胸痛是什么病，现在还疼，需要去医院吗")


# ---------------------------------------------------------------- [5]
def test_comma_boundary_before_list_stop():
    result = check_medication_safety(
        "今天吃了华法林，阿司匹林、氯吡格雷都停了两年，能吃布洛芬吗",
        {},
    )
    # 逗号分句边界后的共享停药不能归因给前一句的在用药
    # （第一百零九轮 [5]）
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_comma_boundary_before_list_controls():
    result = check_medication_safety(
        "华法林、阿司匹林，都停了两年，能吃布洛芬吗", {}
    )
    assert result["level"] == "none"
    result = check_medication_safety(
        "华法林在用，阿司匹林都停了两年，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result)


# ---------------------------------------------------------------- [16]
def test_switch_away_suffix_chat():
    # 「华法林两年前就改成氯吡格雷了」：本药已换掉（第一百零九轮 [16]）
    result = check_medication_safety(
        "华法林两年前就改成氯吡格雷了，现在吃布洛芬", {}
    )
    assert result["level"] == "none", result
    result = check_medication_safety("华法林改为5mg了，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result)


def test_switch_away_prefix_profile():
    names = api_server._context_medication_names
    got = names(
        {"medications": "两年前把华法林改为氯吡格雷，现在吃布洛芬"}
    )
    assert not any("华法林" in name for name in got), got
    got = names({"medications": "把华法林改为5mg，现在吃布洛芬"})
    assert any("华法林" in name for name in got), got


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林。都停了",
        "华法林、阿司匹林。都停了两年",
    ],
)
def test_sentence_boundary_group_stop(text):
    # 句号隔开的群标记停作用于前一句枚举（第一百零九轮 [11]）
    assert api_server._context_medication_names(
        {"medications": text}
    ) == [], text


def test_sentence_boundary_group_stop_controls():
    got = api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次；阿司匹林、氯吡格雷。都停了"}
    )
    assert got == ["华法林 3mg 每日一次"], got


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林，都停了两天",
        "华法林、阿司匹林，都停了一周",
    ],
)
def test_recent_group_stop_keeps_active(text):
    # 天/周级近期停药仍按在用药物检查（第一百零九轮 [13]）
    assert api_server._context_medication_names(
        {"medications": text}
    ) == ["华法林", "阿司匹林"], text


def test_recent_group_stop_controls():
    names = api_server._context_medication_names
    assert names({"medications": "华法林、阿司匹林都停了两天"}) == [
        "华法林"
    ]
    assert names({"medications": "华法林、阿司匹林，都停了两年"}) == []


# ---------------------------------------------------------------- [3]
def test_merged_backup_orphan_restore(tmp_path):
    store = _store(tmp_path, "x3")
    src_a = _store(tmp_path, "x3a")
    src_c = _store(tmp_path, "x3c")
    try:
        for src in (src_a, src_c, store):
            src._now = lambda: CREATED
            src.save_profile("妈妈", {})
        src_a.add_reminder("A", _day(19), note="n1", profile="妈妈")
        backup_a = src_a.export_data()
        src_c.add_reminder("B", _day(21), note="n2", profile="妈妈")
        backup_c = src_c.export_data()
        store.import_data(backup_a)
        store.import_data(backup_c)
        rows = store.list_reminders(True, None)
        a_row = next(r for r in rows if r["title"] == "A")
        b_row = next(r for r in rows if r["title"] == "B")
        store._now = lambda: "2026-10-01T09:00:00"
        store.delete_reminder(int(a_row["id"]))
        store.update_reminder(int(b_row["id"]), note="changed")
        added = store.import_data(backup_a).get("reminders")
        titles = sorted(r["title"] for r in store.list_reminders(True, None))
        # 另一份备份创建的同秒已编辑行不能顶替被删记录（第一百零九轮 [3]）
        assert added == 1, titles
        assert "A" in titles, titles
        assert store.import_data(backup_a).get("reminders") == 0
    finally:
        store.close()
        src_a.close()
        src_c.close()


# ---------------------------------------------------------------- [8]
def test_cross_profile_lookalike_not_claimed(tmp_path):
    store = _store(tmp_path, "x8")
    src_a = _store(tmp_path, "x8a")
    src_c = _store(tmp_path, "x8c")
    try:
        for src in (src_a, src_c, store):
            src._now = lambda: CREATED
            src.save_profile("grandma", {})
            src.save_profile("dad", {})
        src_a.add_reminder("C", _day(19), note="n1", profile="grandma")
        backup_a = src_a.export_data()
        src_c.add_reminder("C", _day(21), note="", profile="dad")
        backup_c = src_c.export_data()
        store.import_data(backup_a)
        store.import_data(backup_c)
        rows = store.list_reminders(True, None)
        g_row = next(r for r in rows if r["profile"] == "grandma")
        d_row = next(r for r in rows if r["profile"] == "dad")
        store._now = lambda: "2026-10-01T09:00:00"
        store.delete_reminder(int(g_row["id"]))
        store.update_reminder(int(d_row["id"]), note="edited")
        added = store.import_data(backup_a).get("reminders")
        rows = store.list_reminders(True, None)
        grandma_rows = [r for r in rows if r["profile"] == "grandma"]
        # 跨档案同秒同标题的已编辑行属于它自己那份备份（第一百零九轮 [8]）
        assert added == 1, rows
        assert len(grandma_rows) == 1, rows
    finally:
        store.close()
        src_a.close()
        src_c.close()


# ---------------------------------------------------------------- [9]
def test_restore_reschedule_cleans_old_reminder(tmp_path):
    d0, d1 = _day(20), _day(24)
    store = _store(tmp_path, "x9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d1,
        )
        vid = int(visit["id"])
        rid = int(store.add_reminder(
            "复诊：高血压", _day(23), note="复诊 " + _day(23),
            profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, 0, rid)
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            status="cancelled", note=f"由就诊记录 #{vid} 自动创建",
        )
        aid = int(appt["id"])
        store._now = lambda: "2026-06-02T08:00:00"
        store.update_appointment_partial(
            aid, status="upcoming", scheduled_at=d1 + "T09:00",
        )
        reminders = store.list_reminders(False, "妈妈")
        linked = [
            r for r in reminders
            if int(r.get("appointment_id") or 0) == aid
        ]
        ghosts = [
            r for r in reminders
            if int(r.get("appointment_id") or 0) == 0
        ]
        # 被顶替的旧脱钩提醒无引用时清理，不再留幽灵待办
        # （第一百零九轮 [9]）
        assert len(linked) == 1, reminders
        assert ghosts == [], reminders
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_restore_link_failure_clears_reminder_link(tmp_path):
    d0 = _day(20)
    store = _store(tmp_path, "x10")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="",
        )
        vid = int(visit["id"])
        rid = int(store.add_reminder(
            "复诊：高血压", _day(23), note="复诊 " + _day(23),
            profile="妈妈",
        )["id"])
        store.set_visit_followup_links(vid, 0, rid)
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            status="cancelled", note=f"由就诊记录 #{vid} 自动创建",
            owner_visit_id=vid,
        )
        aid = int(appt["id"])
        store._now = lambda: "2026-06-02T08:00:00"
        store.update_appointment_partial(aid, status="upcoming")
        visit = store.get_visit_record(vid)
        # 恢复链接失败：就诊的提醒链接一并切断（第一百零九轮 [10]）
        assert int(visit["followup_reminder_id"] or 0) == 0, visit
        store.update_appointment_partial(aid, status="cancelled")
        pending = store.list_reminders(False, "妈妈")
        assert pending == [], pending
    finally:
        store.close()


# ---------------------------------------------------------------- [15]
def test_remap_marker_respects_existing_link(tmp_path):
    source = _store(tmp_path, "src15")
    target = _store(tmp_path, "dst15")
    d0, d2 = _day(20), _day(28)
    try:
        source._now = lambda: CREATED
        source.save_profile("妈妈", {})
        v0 = source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d0,
        )
        a0 = source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{int(v0['id'])} 自动创建",
            owner_visit_id=int(v0["id"]),
        )
        source.add_reminder(
            "复诊：高血压", _day(19), note="复诊 " + _day(19),
            profile="妈妈", appointment_id=int(a0["id"]),
        )
        backup = source.export_data()
        target._now = lambda: CREATED
        target.save_profile("妈妈", {})
        target.import_data(backup)
        vid = int(target.list_visit_records("妈妈")[0]["id"])
        target._now = lambda: "2026-06-10T08:00:00"
        target.update_visit_record_partial(vid, next_followup_date=d2)
        api_server._schedule_followup_sync(target, vid)
        target._now = lambda: "2026-06-20T08:00:00"
        target.import_data(backup)
        upcoming = [
            a for a in target.list_appointments("妈妈", None)
            if a["status"] == "upcoming"
        ]
        auto = [
            a for a in upcoming
            if "由就诊记录 #" in str(a.get("note") or "")
        ]
        # 就诊已链另一条预约时，新插入的自动预约不能再挂同一标记
        # （第九十八轮 [10] 契约、第一百零九轮 [15]）
        assert len(auto) == 1, upcoming
    finally:
        source.close()
        target.close()


# ---------------------------------------------------------------- [12][17]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_switch_rollback_counts_queued_send():
    source = _app_js()
    # 换档失败回滚把排队中的提交视为接管（第一百零九轮 [12]）
    assert "busy || sendInFlight || sessionId !== attemptSession" in source
    assert "busy || sendInFlight || sessionId !== beforeSession" in source


def test_web_context_clear_blocked_while_sending():
    source = _app_js()
    start = source.index("els.contextClear.addEventListener")
    block = source[start:start + 500]
    # 会诊进行中清空补充信息被拒绝（第一百零九轮 [17]）
    guard = block.index("if (busy || sendInFlight)")
    clear = block.index("applyPatientContext({})")
    assert guard < clear, block
