# -*- coding: utf-8 -*-
"""第一百一十六轮审查修复的回归测试。"""
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


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1][5]
@pytest.mark.parametrize(
    "context",
    [
        {"age": "40岁", "history": "孩子出生后我一直吃华法林"},
        {"age": "40岁", "history": "我和孩子他爸都吃华法林"},
        {"age": "40岁", "history": "孩子他爸和我都吃华法林"},
        {"age": "40岁", "history": "我和孩子都吃华法林"},
        {"age": "60岁", "history": "我和娃都吃华法林"},
        {"age": "40岁", "medications": "我和孩子都吃华法林"},
    ],
)
def test_parallel_child_subject_keeps_anticoagulant(context):
    # 含「我」的并列主语句不能按孩子家属整句丢弃
    # （第一百一十六轮 [1][5]）
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框了", context
    ), _red("今天头撞到门框了", context)


def test_parallel_child_subject_fever():
    assert "免疫功能低下伴发热" in _labels(
        "今天发烧38.5度",
        {"age": "40岁", "history": "孩子出生后我一直在化疗"},
    )


def test_child_family_controls():
    # 裸「孩子在吃华法林」「我孩子吃华法林」仍按家属处理
    assert _level(
        "今天头撞到门框了",
        {"age": "60岁", "medications": "孩子在吃华法林"},
    ) == "none"
    assert _level(
        "今天头撞到门框了",
        {"age": "60岁", "history": "我孩子吃华法林"},
    ) == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "history",
    [
        "华法林吃了三年，去年胃出血，停了一年多，现在又吃上了",
        "华法林吃了三年，停了一年多，因为胃出血，现在又吃上了",
        "华法林吃了三年，停了一年多，后来医生说可以吃，现在又吃上了",
    ],
)
def test_restart_after_gap_clause(history):
    # 续用线索可能隔着一两个原因/事件分句（第一百一十六轮 [2]）
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", {"age": "40岁", "history": history}
    )


def test_restart_after_gap_control():
    assert _level(
        "今天头撞到门框",
        {"age": "40岁", "history": "华法林吃了三年，停了一年多"},
    ) == "none"


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "怀孕12周，下面见血我就赶紧去了医院",
        "怀孕12周，内裤上见血我就赶紧去了医院",
        "怀孕12周，见血都两天了",
        "怀孕12周，阴道见血我都吓死了",
    ],
)
def test_jianxue_real_bleeding_with_location(text):
    # 带出血部位或「见血我就去了医院/见血都两天了」是真实出血
    # （第一百一十六轮 [4]）
    assert "孕产期出血" in _labels(text), _red(text)


@pytest.mark.parametrize(
    "text",
    [
        "怀孕12周了，我见血就头晕",
        "怀孕12周，你说得一针见血",
        "孕12周，抽血时见到血我就紧张",
        "怀孕12周，刚才刷到一个见血封喉的新闻",
    ],
)
def test_jianxue_idioms_stay_negative(text):
    assert _level(text) == "none", _red(text)


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周，否认腹痛及阴道出血",
        "孕8周，无腹痛、阴道出血",
        "孕8周，没有腹痛和阴道流血",
    ],
)
def test_pregnancy_bleeding_enum_negation(text):
    # 枚举否定覆盖到出血部位时不是孕产期出血（第一百一十六轮 [6]）
    assert _level(text) == "none", _red(text)


def test_pregnancy_bleeding_enum_controls():
    assert "孕产期出血" in _labels("孕8周，阴道出血")
    assert "孕产期出血" in _labels("孕8周，下面出血了")


# ---------------------------------------------------------------- [7][15]
@pytest.mark.parametrize(
    "text",
    [
        "I stopped taking warfarin two years ago, but I am taking it"
        " now, can I take ibuprofen?",
        "I stopped taking warfarin over a year ago, now I take it"
        " daily, can I take ibuprofen?",
        "I stopped taking warfarin over a year ago, but now I take it"
        " every day, can I take ibuprofen?",
        "I stopped taking warfarin two years ago, I started taking it"
        " last month, can I take ibuprofen?",
    ],
)
def test_english_restart_pronoun_forms(text):
    # I am taking it now / now I take it daily / started taking it
    # last month 都是续用（第一百一十六轮 [7][15]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_english_restart_triage():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {
            "age": "40岁",
            "history": "I stopped taking warfarin two years ago,"
            " but I am taking it now",
        },
    )


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    ["华法林（停用三天）", "华法林（已停药一周）"],
)
def test_context_names_inline_recent_stop(text):
    # 括号式天/周级近期停药仍按在用药物检查（第一百一十六轮 [11]）
    names = api_server._context_medication_names({"medications": text})
    assert any("华法林" in name for name in names), names


def test_context_names_inline_long_stop_control():
    names = api_server._context_medication_names(
        {"medications": "华法林（停用两个月）"}
    )
    assert not any("华法林" in name for name in names), names


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，上周停了",
        "华法林 3mg 每日一次，上周末停了",
        "华法林 3mg 每日一次，昨天停了",
        "华法林 3mg 每日一次，两周前停了",
    ],
)
def test_context_names_recent_timepoint_stop(text):
    # 档案通道与聊天预检同口径：时间点写法按近期停药保留
    # （第一百一十六轮 [14]）
    names = api_server._context_medication_names({"medications": text})
    assert any("华法林" in name for name in names), names


@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，两个月前停了",
        "华法林 3mg 每日一次，两周半前停了",
    ],
)
def test_context_names_long_stop_controls(text):
    names = api_server._context_medication_names({"medications": text})
    assert not any("华法林" in name for name in names), names


# ---------------------------------------------------------------- [3]
def test_merge_sole_claim_respects_provenance(tmp_path):
    # 合并备份：另一来源创建的同秒提醒不能被 sole 兜底吞掉
    # （第一百一十六轮 [3]）
    src_a = _store(tmp_path, "s3a")
    src_b = _store(tmp_path, "s3b")
    target = _store(tmp_path, "s3t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        r1 = src_a.add_reminder(
            "吃药", "2026-06-07", note="", profile="妈妈"
        )["id"]
        src_a.add_reminder("补钙", "2026-06-07", note="", profile="妈妈")
        src_a._now = lambda: "2026-06-05T08:00:00"
        src_a.update_reminder(int(r1), title="晨间用药")
        rows = src_a.list_reminders(True, "妈妈")
        src_a.delete_reminder(
            next(r for r in rows if r["title"] == "补钙")["id"]
        )
        backup_a = src_a.export_data()
        src_b.add_reminder("吃药", "2026-06-07", note="", profile="妈妈")
        src_b.add_reminder("补钙", "2026-06-07", note="", profile="妈妈")
        rows = src_b.list_reminders(True, "妈妈")
        src_b.delete_reminder(
            next(r for r in rows if r["title"] == "吃药")["id"]
        )
        src_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        added = target.import_data(backup_b).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 1
        assert any(r["title"] == "补钙" for r in rows)
        assert target.import_data(backup_b).get("reminders") == 0
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [8]
def test_soft_empty_candidates_and_pool_profile(tmp_path):
    # stale 过滤清空软字段命中时不能把候选集清空；兜底 pool 不能
    # 跨成员认领（第一百一十六轮 [8]）
    src_a = _store(tmp_path, "s8a")
    src_b = _store(tmp_path, "s8b")
    target = _store(tmp_path, "s8t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
            st.save_profile("爸爸", {})
        src_a.add_reminder("吃药", "2026-06-07", note="n1", profile="妈妈")
        backup_a = src_a.export_data()
        src_b.add_reminder("补钙", "2026-06-07", note="", profile="妈妈")
        src_b.add_reminder(
            "补钙", "2026-06-08", note="", profile="妈妈", advance_days=1
        )
        src_b.add_reminder("补钙", "2026-06-08", note="n2", profile="爸爸")
        src_b.add_reminder("测血压", "2026-06-08", note="n2", profile="妈妈")
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rows = target.list_reminders(True, None)
        victim = next(
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "补钙"
            and str(r["due_at"])[:10] == "2026-06-08"
            and not r["note"]
        )
        target.delete_reminder(int(victim["id"]))
        target.import_data(backup_b)
        rows = target.list_reminders(True, None)
        mom_victim = [
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "补钙"
            and str(r["due_at"])[:10] == "2026-06-08"
            and not r["note"]
        ]
        dad_rows = [r for r in rows if r["profile"] == "爸爸"]
        assert len(mom_victim) == 1
        assert int(mom_victim[0]["advance_days"] or 0) == 1
        assert len(dad_rows) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [9]
def test_soft_full_counts_scope_profile(tmp_path):
    # 跨成员同秒同标题行不能把本档案孪生组误判为已满
    # （第一百一十六轮 [9]）
    src_a = _store(tmp_path, "s9a")
    src_b = _store(tmp_path, "s9b")
    target = _store(tmp_path, "s9t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
            st.save_profile("爸爸", {})
        src_a.add_reminder("吃药", "2026-06-08", note="", profile="妈妈")
        src_a.add_reminder("吃药", "2026-06-08", note="n1", profile="妈妈")
        backup_a = src_a.export_data()
        src_b.add_reminder(
            "测血压", "2026-06-08", note="n2", profile="妈妈",
            advance_days=1, repeat_days=7,
        )
        src_b.add_reminder("测血压", "2026-06-08", note="", profile="妈妈")
        src_b.add_reminder("测血压", "2026-06-07", note="", profile="爸爸")
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rows = target.list_reminders(True, None)
        victim = next(
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "测血压"
            and str(r["due_at"])[:10] == "2026-06-08"
            and r["note"] == "n2"
        )
        target.delete_reminder(int(victim["id"]))
        target.import_data(backup_b)
        rows = target.list_reminders(True, None)
        restored = [
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "测血压"
            and str(r["due_at"])[:10] == "2026-06-08"
            and r["note"] == "n2"
            and int(r["advance_days"] or 0) == 1
            and int(r["repeat_days"] or 0) == 7
        ]
        blank = [
            r for r in rows
            if r["profile"] == "妈妈" and r["title"] == "测血压"
            and str(r["due_at"])[:10] == "2026-06-08"
            and not r["note"]
        ]
        assert len(restored) == 1
        assert len(blank) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [10]
def test_no_created_at_marker_backup_repeat_idempotent(tmp_path):
    # 无 created_at 的标记型旧备份重复导入不新增预约与待办
    # （第一百一十六轮 [10]）
    store = _store(tmp_path, "s10")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈",
                 "visit_date": "2026-11-24", "diagnosis": "高血压",
                 "next_followup_date": "2026-12-16",
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建"},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-15", "note": "复诊",
                 "appointment_id": 30, "done": 0},
            ],
        }
        store.import_data(forged)
        store.import_data(forged)
        assert len(store.list_appointments("妈妈", None)) == 1
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
def test_forged_marker_backup_cannot_delete_local_plan(tmp_path):
    # 备份没有带来就诊记录时，标记型去重不能删除本机既有待办
    # 或清空预约归属（第一百一十六轮 [13]）
    store = _store(tmp_path, "s13")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        vid = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-24", diagnosis="高血压",
            next_followup_date="2026-12-16",
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-16T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )["id"])
        reminder = int(store.add_reminder(
            "复诊：高血压", "2026-12-15", note="复诊", profile="妈妈",
            appointment_id=appt,
        )["id"])
        forged = {
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00",
                 "status": "upcoming", "owner_visit_id": 0,
                 "note": f"由就诊记录 #{vid} 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-10T09:00",
                 "status": "upcoming", "owner_visit_id": 0,
                 "note": f"由就诊记录 #{vid} 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        appt_row = store.get_appointment(appt)
        note = str(appt_row.get("note") or "")
        assert store.get_reminder(reminder) is not None
        assert "由就诊记录 #" in note
        assert int(appt_row.get("owner_visit_id") or 0) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [12][16][17]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_cross_owner_draft_deferred():
    source = _app_js()
    start = source.index("function loadDraft()")
    block = source[start:start + 2600]
    # 跨会话草稿先暂存文本，同时照旧清掉存储键（第九十一轮 [14]）；
    # 目标会话恢复失败时由暂存副本回还（第一百一十六轮 [12]）
    assert "deferredCrossOwnerDraft = {" in block
    assert "safeSessionRemove(DRAFT_KEY);" in block
    assert "function restoreDeferredCrossOwnerDraft()" in source
    assert "restoreDeferredCrossOwnerDraft();" in source


def test_web_family_chip_activity_guard():
    source = _app_js()
    assert "activityRecent" in source
    assert "今日已记录" in source


def test_web_launch_new_session_waits():
    source = _app_js()
    assert "pendingLaunchNewSession" in source
    assert "resolveLaunchNewSession(Boolean(created));" in source
    start = source.index("async function send(")
    block = source[start:start + 900]
    assert "await Promise.race([" in block
    assert "pendingLaunchNewSession" in block
