# -*- coding: utf-8 -*-
"""第一百一十一轮审查修复的回归测试。"""
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


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "网上说我的胸痛可能是心绞痛",
        "文章说我的呼吸困难要重视",
        "网上说我的咯血可能是肺癌",
    ],
)
def test_quote_clause_with_deictic_symptom(text):
    # 引述分句里的「我的+症状」是患者本人主诉（第一百一十一轮 [1]）
    assert bool(_labels(text)), _red(text)


def test_quote_clause_controls():
    assert bool(_labels("网上说我胸痛要小心"))
    assert _level("网上说胸痛是什么病") == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "没有发烧、咳嗽并有胸痛",
        "无发热、咳嗽并有咯血",
        "没有发烧、咳嗽还有胸痛",
        "没有发烧、咳嗽同时有胸痛",
        "无发热、咳嗽并有呼吸困难",
    ],
)
def test_enum_negation_connectors(text):
    # 「并有/还有/同时有」引出的是新增阳性症状（第一百一十一轮 [2]）
    assert bool(_labels(text)), _red(text)


def test_enum_negation_control():
    assert _level("没有发烧、咳嗽") == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "孕周12，阴道出血",
        "孕周12+3，阴道出血",
        "孕周：12+3，阴道出血",
    ],
)
def test_pregnancy_week_reversed(text):
    # 「孕周12」反向写法同样是妊娠证据（第一百一十一轮 [6]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_week_reversed_controls():
    assert "孕产期出血" in _labels(
        "今天阴道出血", {"history": "孕周12"}
    )
    assert "孕产期出血" in _labels("孕12周，阴道出血")


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "什么是咯血，我明天要去医院体检",
        "什么是咯血，我要去医院拿药",
        "咯血是什么病，周末要去医院看朋友",
    ],
)
def test_urgent_help_requires_question_tail(text):
    # 陈述式「去医院」不是求助（第一百一十一轮 [14]）
    assert _level(text) == "none", _red(text)


def test_urgent_help_controls():
    assert bool(_labels("胸痛是什么病，需要马上去医院吗"))
    assert bool(_labels("胸痛是什么病，要不要马上去医院"))


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "I stopped warfarin 2 years ago, now I take it again."
        " Can I take ibuprofen?",
        "Warfarin was stopped 2 years ago but I resumed it."
        " Can I take ibuprofen?",
    ],
)
def test_english_restart_words(text):
    # resumed / now I take it again 是续用（第一百一十一轮 [7]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [11]
def test_family_clause_keeps_self_allergy():
    names = api_server._context_medication_names(
        {"medications": "阿莫西林 0.5g 每日三次，我爸对青霉素过敏，我青霉素过敏"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    # 家属分句不能吞掉本人的过敏陈述（第一百一十一轮 [11]）
    assert any("青霉素类过敏史" in str(t) for t in _titles(result)), result


# ---------------------------------------------------------------- [15]
def test_allergy_only_not_active_drug():
    names = api_server._context_medication_names(
        {"medications": "对阿司匹林过敏"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], [{"name": "胃溃疡", "status": "active"}]
    )
    # 纯过敏陈述不是在用药物，慢病通道不应提示（第一百一十一轮 [15]）
    assert not any(
        "溃疡" in str(a.get("title") or "")
        for a in (result.get("alerts") or [])
    ), result


# ---------------------------------------------------------------- [16]
def test_adult_archive_child_allergy_not_self():
    names = api_server._context_medication_names(
        {"medications": "氯吡格雷 75mg 每日一次，我女儿对青霉素过敏"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    # 成年档案的「我女儿」不是本人（第一百一十一轮 [16]）
    assert result.get("level") == "none", result


# ---------------------------------------------------------------- [3]
def test_dose_edit_twin_restore(tmp_path):
    store = _store(tmp_path, "x3")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.add_medication(
            "妈妈", "干扰药", dose="1mg", frequency="qd", note="",
        )
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "药A", "dose": "1mg",
             "frequency": "每日一次", "note": "n1", "active": True,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "药B", "dose": "2mg",
             "frequency": "每日一次", "note": "n2", "active": True,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications("妈妈", None)
        a_row = next(r for r in rows if r["name"] == "药A")
        b_row = next(r for r in rows if r["name"] == "药B")
        store._now = lambda: "2026-10-01T09:00:00"
        store.update_medication(int(a_row["id"]), dose="99mg")
        store.delete_medication(int(b_row["id"]))
        added = store.import_data(backup).get("medications")
        rows = store.list_medications("妈妈", None)
        active_a = [r for r in rows if r["name"] == "药A"]
        # 剂量编辑是同一记录的常规编辑：只补回被删的药B
        # （第一百一十一轮 [3]）
        assert added == 1, [(r["name"], r["dose"]) for r in rows]
        assert len(active_a) == 1 and str(active_a[0]["dose"]) == "99mg"
    finally:
        store.close()


# ---------------------------------------------------------------- [4][13]
def test_origin_set_restore_both_backups(tmp_path):
    src_a = _store(tmp_path, "f4a")
    store_b = _store(tmp_path, "f4b")
    store_c = _store(tmp_path, "f4c")
    try:
        for st in (src_a, store_b, store_c):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "药M", dose="5mg", frequency="每日一次", note="n1",
        )
        backup_a = src_a.export_data()
        store_b.import_data(backup_a)
        store_b._now = lambda: "2026-06-02T08:00:00"
        backup_b = store_b.export_data()
        store_c.add_medication(
            "妈妈", "干扰药", dose="1mg", frequency="qd", note="",
        )
        store_c.import_data(backup_a)
        assert store_c.import_data(backup_b).get("medications") == 0
        rows = store_c.list_medications("妈妈", None)
        med = next(r for r in rows if r["name"] == "药M")
        store_c._now = lambda: "2026-10-01T09:00:00"
        store_c.update_medication(int(med["id"]), dose="10mg")
        # 一行可同时属于多份备份：恢复任一包含该记录的备份都幂等
        # （第一百一十一轮 [4][13]）
        added_a = store_c.import_data(backup_a).get("medications")
        rows = store_c.list_medications("妈妈", None)
        meds = [r for r in rows if r["name"] == "药M"]
        assert added_a == 0, [(r["name"], r["dose"]) for r in rows]
        assert len(meds) == 1 and str(meds[0]["dose"]) == "10mg"
        assert store_c.import_data(backup_b).get("medications") == 0
    finally:
        for st in (src_a, store_b, store_c):
            st.close()


# ---------------------------------------------------------------- [5]
def test_restored_reminder_not_self_deleted(tmp_path):
    d0 = _day(20)
    store = _store(tmp_path, "f5")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        visit = store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=d0,
        )
        vid = int(visit["id"])
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{vid} 自动创建", owner_visit_id=vid,
        )
        aid = int(appt["id"])
        backup_old = store.export_data()
        rid = int(store.add_reminder(
            "复诊：高血压", _day(19), note="复诊 " + d0,
            profile="妈妈", appointment_id=aid,
        )["id"])
        store.set_visit_followup_links(vid, aid, rid)
        backup_new = store.export_data()
    finally:
        store.close()
    target = _store(tmp_path, "f5t")
    try:
        target._now = lambda: "2026-06-02T08:00:00"
        target.save_profile("妈妈", {})
        target.import_data(backup_old)
        first = target.import_data(backup_new).get("reminders")
        pending = target.list_reminders(False, "妈妈")
        # 本次导入的提醒不能被自己「本地已有待办」判定删除
        # （第一百一十一轮 [5]）
        assert first == 1, (first, pending)
        assert len(pending) == 1, pending
        second = target.import_data(backup_new).get("reminders")
        assert second == 0
        assert len(target.list_reminders(False, "妈妈")) == 1
    finally:
        target.close()


# ---------------------------------------------------------------- [8]
def test_anchor_edited_reminder_claimed(tmp_path):
    source = _store(tmp_path, "f8s")
    try:
        source._now = lambda: CREATED
        source.save_profile("妈妈", {})
        source.add_reminder("提醒1", _day(21), note="n1", profile="妈妈")
        source.add_reminder("提醒2", _day(22), note="n2", profile="妈妈")
        backup = source.export_data()
    finally:
        source.close()
    store = _store(tmp_path, "f8")
    try:
        store._now = lambda: "2026-10-01T08:00:00"
        store.save_profile("妈妈", {})
        store.add_reminder("干扰", _day(9), note="x", profile="妈妈")
        store.import_data(backup)
        rows = store.list_reminders(True, "妈妈")
        r1 = next(r for r in rows if r["title"] == "提醒1")
        r2 = next(r for r in rows if r["title"] == "提醒2")
        store.delete_reminder(int(r1["id"]))
        store.update_reminder(int(r2["id"]), due_at=_day(27))
        added = store.import_data(backup).get("reminders")
        rows = store.list_reminders(True, "妈妈")
        r2_rows = [r for r in rows if r["title"] == "提醒2"]
        # 补足缺行后应认领改期后的存活行，不复制旧到期日
        # （第一百一十一轮 [8]）
        assert added == 1, [(r["title"], str(r["due_at"])[:10]) for r in rows]
        assert len(r2_rows) == 1
        assert str(r2_rows[0]["due_at"])[:10] == _day(27)
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_kept_appointment_gets_new_reminder(tmp_path):
    d0 = _day(20)
    store = _store(tmp_path, "f9")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v1 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(14),
        )["id"])
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(14),
        )["id"])
        rid = int(store.add_reminder(
            "复诊：高血压", _day(13), note="复诊 " + _day(13),
            profile="妈妈",
        )["id"])
        store.set_visit_followup_links(v1, 0, rid)
        store.set_visit_followup_links(v2, 0, rid)
        aid = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=d0 + "T09:00",
            note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
        )["id"])
        store.update_visit_record_partial(v1, next_followup_date=d0)
        visit = store.get_visit_record(v1)
        pending = [
            r for r in store.list_reminders(False, "妈妈")
            if int(r.get("appointment_id") or 0) == aid
        ]
        # 接管因共享失败时要为保留的预约新建提醒
        # （第一百一十一轮 [9]）
        assert int(visit["followup_appointment_id"] or 0) == aid
        assert int(visit["followup_reminder_id"] or 0) != 0
        assert len(pending) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_import_cleans_duplicate_owned_appointment(tmp_path):
    old_src = _store(tmp_path, "f10s")
    try:
        old_src._now = lambda: CREATED
        old_src.save_profile("妈妈", {})
        v = int(old_src.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(14),
        )["id"])
        a_old = int(old_src.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=_day(20) + "T09:00",
            note=f"由就诊记录 #{v} 自动创建", owner_visit_id=v,
        )["id"])
        old_src.add_reminder(
            "复诊：高血压", _day(19), note="复诊 " + _day(19),
            profile="妈妈", appointment_id=a_old,
        )
        backup_old = old_src.export_data()
    finally:
        old_src.close()
    new_src = _store(tmp_path, "f10n")
    try:
        new_src._now = lambda: "2026-06-02T08:00:00"
        new_src.save_profile("妈妈", {})
        v = int(new_src.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(24),
        )["id"])
        a = int(new_src.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=_day(24) + "T09:00",
            note=f"由就诊记录 #{v} 自动创建", owner_visit_id=v,
        )["id"])
        r = int(new_src.add_reminder(
            "复诊：高血压", _day(23), note="复诊 " + _day(23),
            profile="妈妈", appointment_id=a,
        )["id"])
        new_src.set_visit_followup_links(v, a, r)
        backup_new = new_src.export_data()
    finally:
        new_src.close()
    target = _store(tmp_path, "f10t")
    try:
        target._now = lambda: "2026-06-03T08:00:00"
        target.save_profile("妈妈", {})
        target.import_data(backup_old)
        target.import_data(backup_new)
        vid = int(target.list_visit_records("妈妈")[0]["id"])
        owned = [
            a for a in target.list_appointments("妈妈", None)
            if a["status"] == "upcoming"
            and (
                int(a.get("owner_visit_id") or 0) == vid
                or f"由就诊记录 #{vid} 自动创建" in str(a.get("note") or "")
            )
        ]
        # 恢复较新备份后同一就诊最多一条归属的自动预约
        # （第一百一十一轮 [10]）
        assert len(owned) == 1, owned
    finally:
        target.close()


# ---------------------------------------------------------------- [12][17]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_memory_fallback_skips_context(monkeypatch):
    source = _app_js()
    start = source.index("async function restoreCurrentSession(")
    block = source[start:start + 30000]
    # 内存回退的档案/上下文未知：不覆盖用户表单（第一百一十一轮 [12]）
    assert "skipContext: pendingReset || data.source === 'memory'" in block
    assert "skipProfile: pendingReset || data.source === 'memory'" in block
    start = source.index("function renderStoredMessages(")
    block = source[start:start + 6000]
    assert "else if (activeProfileName) {" in block


@pytest.mark.parametrize(
    "name",
    ["savePatientProfile", "renamePatientProfile", "deletePatientProfile"],
)
def test_web_profile_op_seq_guard(name):
    source = _app_js()
    start = source.index(f"async function {name}(")
    block = source[start:start + 3000]
    # 请求在途期间切换档案后，完成回调不能覆盖新选择
    # （第一百一十一轮 [17]）
    assert "const opSeq = profileSelectionSeq;" in block
    assert "profileSelectionSeq !== opSeq" in block
