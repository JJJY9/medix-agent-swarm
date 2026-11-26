# -*- coding: utf-8 -*-
"""第一百一十三轮审查修复的回归测试。"""
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
    "ctx",
    [
        {"active_medications": "布洛芬 0.3g 每日两次"},
        {"medications": "布洛芬"},
        {"medications": "华法林、布洛芬"},
        {"medications": "布洛芬、华法林"},
    ],
)
def test_missed_dose_does_not_swallow_context_drug(ctx):
    # 漏服否定不能越过拼接边界吞掉上下文在用药物（第一百一十三轮 [1]）
    result = check_medication_safety("今天没吃华法林", ctx)
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_missed_dose_control():
    result = check_medication_safety("今天没吃华法林，能吃布洛芬吗", {})
    assert "华法林与非甾体抗炎药" in _titles(result)


# ---------------------------------------------------------------- [13]
def test_chat_context_allergy_statement_not_active():
    result = check_medication_safety(
        "这些药能一起吃吗？",
        {"medications": "华法林 3mg 每日一次，对阿司匹林过敏"},
    )
    # 过敏陈述不能在聊天预检里被当成在用药物（第一百一十三轮 [13]）
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [17]
def test_resumed_taking_other_drug():
    result = check_medication_safety(
        "I stopped warfarin two years ago, resumed taking aspirin"
        " instead. Can I take ibuprofen?",
        {},
    )
    # resumed taking aspirin 的宾语是另一种药（第一百一十三轮 [17]）
    assert "华法林与非甾体抗炎药" not in _titles(result), result


# ---------------------------------------------------------------- [2]
def test_menstrual_nickname_period_context():
    assert "抗凝治疗期间头部外伤" in _labels(
        "大姨妈期间吃华法林，今天摔到头"
    )
    for text in [
        "大姨妈期间吃华法林，能吃布洛芬吗",
        "我大姨妈期间吃华法林，能吃布洛芬吗",
    ]:
        result = check_medication_safety(text, {})
        assert "华法林与非甾体抗炎药" in _titles(result), (text, result)


def test_menstrual_nickname_controls():
    assert "抗凝治疗期间头部外伤" in _labels(
        "月经期间吃华法林，今天摔到头"
    )
    assert _level("今天发烧39度", {"history": "大姨妈在化疗"}) == "none"
    assert (
        _level("今天头撞到门框", {"history": "大姨妈在吃华法林"})
        == "none"
    )


# ---------------------------------------------------------------- [4][5]
@pytest.mark.parametrize(
    "text",
    ["怀孕8周，阴道没有出血了", "孕8周，现在已经不出血了"],
)
def test_pregnancy_bleeding_stopped_with_le(text):
    # 出血词后带「了」说明曾经出血已停（第一百一十三轮 [4]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_bleeding_stopped_history():
    assert "孕产期出血" in _labels(
        "下面已经不流血了", {"history": "孕8周"}
    )


@pytest.mark.parametrize(
    "text",
    [
        "孕8周，现在没有出血",
        "孕8周，最近没有出血",
        "孕8周，目前未出血",
        "孕8周，阴道不再流血了",
    ],
)
def test_pregnancy_bleeding_plain_negation(text):
    # 单纯否定与「不再」仍按否认（第一百一十三轮 [5]）
    assert _level(text) == "none", _red(text)


def test_pregnancy_bleeding_control():
    assert "孕产期出血" in _labels("怀孕8周，阴道出血已经没有了")


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么样的感觉",
        "胸痛是啥感觉",
        "咯血是什么样的感觉",
        "抽搐是什么样的感觉",
    ],
)
def test_experiential_definition_variants(text):
    # 体验式定义问句变体按科普过滤（第一百一十三轮 [6]）
    assert _level(text) == "none", _red(text)


def test_experiential_definition_control():
    assert "胸痛" in _labels("胸痛疼得受不了是什么病")


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text", ["鼻头撞到桌角了", "乳头被宝宝撞到", "龟头碰到床沿"]
)
def test_non_head_impact_not_trauma(text):
    # 鼻头/乳头/龟头撞击不是头部外伤（第一百一十三轮 [12]）
    assert _level(text, WARFARIN) == "none", _red(text, WARFARIN)


def test_non_head_impact_control():
    assert "抗凝治疗期间头部外伤" in _labels("头上撞了个包", WARFARIN)


# ---------------------------------------------------------------- [10]
def test_family_medication_excluded_from_context():
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg 每日一次，我爸吃布洛芬"}
    )
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女", [], []
    )
    # 家属用药分句不能从 medications 上下文回到患者名单
    # （第一百一十三轮 [10]）
    assert "华法林与非甾体抗炎药" not in _titles(result), result
    result = api_server._evaluate_medication_safety(
        names, [], "62", "女",
        [], [{"name": "胃溃疡", "status": "active"}],
    )
    assert not any(
        "布洛芬" in (
            str(a.get("title") or "") + str(a.get("detail") or "")
        )
        for a in (result.get("alerts") or [])
    ), result


# ---------------------------------------------------------------- [3]
def test_merge_backup_provenance_not_swallowed(tmp_path):
    src_a = _store(tmp_path, "f3a")
    src_b = _store(tmp_path, "f3b")
    target = _store(tmp_path, "f3t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder(
            "复查", _day(7), note="", advance_days=1, repeat_days=7,
            profile="妈妈",
        )
        backup_a = src_a.export_data()
        src_b.add_reminder("复查", _day(21), note="n1", profile="妈妈")
        src_b.add_reminder("吃药", _day(19), note="n2", profile="妈妈")
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        added_b = target.import_data(backup_b).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        titles = sorted(r["title"] for r in rows)
        # 合并备份时另一来源的行不能被提前认领吞掉（第一百一十三轮 [3]）
        assert added_b == 2, [
            (r["title"], str(r["due_at"])[:10]) for r in rows
        ]
        assert titles.count("复查") == 2, titles
        assert target.import_data(backup_b).get("reminders") == 0
        a_row = next(
            r for r in target.list_reminders(True, "妈妈")
            if str(r["due_at"])[:10] == _day(7)
        )
        a_origins = (
            target._get_import_origin_map("reminders").get(str(a_row["id"]))
            or []
        )
        assert len(a_origins) == 1, a_origins
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [7]
def test_no_id_twin_tail_edit_reimport_idempotent(tmp_path):
    store = _store(tmp_path, "f7")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "复诊", "due_at": _day(19),
             "note": "n1", "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "title": "吃药", "due_at": _day(19),
             "note": "n2", "advance_days": 1, "created_at": CREATED,
             "updated_at": CREATED},
        ]}
        assert store.import_data(backup).get("reminders") == 2
        med = next(
            r for r in store.list_reminders(True, "妈妈")
            if r["title"] == "吃药"
        )
        store._now = lambda: "2026-10-01T09:00:00"
        store.update_reminder(int(med["id"]), note="e2")
        second = store.import_data(backup).get("reminders")
        notes = sorted(
            r["note"] for r in store.list_reminders(True, "妈妈")
        )
        # 无 id 孪生编辑尾字段后重复导入幂等（第一百一十三轮 [7]）
        assert second == 0, notes
        assert notes == ["e2", "n1"], notes
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_old_backup_marker_leftovers_deduped(tmp_path):
    store = _store(tmp_path, "f8")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.import_data({
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-10-01",
                 "diagnosis": "高血压", "next_followup_date": _day(20),
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30 + i, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(20 + i * 4) + "T09:00",
                 "status": "upcoming", "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED}
                for i in range(3)
            ],
            "reminders": [
                {"id": 40 + i, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(19 + i * 4), "note": "复诊",
                 "appointment_id": 30 + i, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED}
                for i in range(3)
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
        pending = store.list_reminders(False, "妈妈")
        # 旧备份 fa=0 的标记型遗留同样单归属去重（第一百一十三轮 [8]）
        assert len(owned) == 1, owned
        assert len(pending) == 1, pending
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_schedule_prefers_upcoming_over_completed_leftover(tmp_path):
    store = _store(tmp_path, "f9")
    store._now = lambda: CREATED
    try:
        store.save_profile("妈妈", {})
        store.import_data({
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-10-01",
                 "diagnosis": "高血压", "next_followup_date": _day(20),
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(20) + "T09:00", "status": "completed",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": _day(24) + "T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(23), "note": "复诊",
                 "appointment_id": 31, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        result = api_server._schedule_followup_sync(store, vid)
        visit = store.get_visit_record(vid)
        upcoming = [
            a for a in store.list_appointments("妈妈", None)
            if a["status"] == "upcoming"
        ]
        pending = store.list_reminders(False, "妈妈")
        # 优先复用/改期待就诊预约，completed 遗留不能抢占
        # （第一百一十三轮 [9]）
        assert result.get("appointment_created") is False
        assert len(upcoming) == 1, upcoming
        assert str(upcoming[0]["scheduled_at"])[:10] == _day(20)
        assert int(visit["followup_appointment_id"] or 0) == int(
            upcoming[0]["id"]
        )
        assert len(pending) == 1
        assert str(pending[0]["due_at"])[:10] == _day(19)
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
def test_dup_cleanup_keeps_other_visit_plan(tmp_path):
    source = _store(tmp_path, "f14s")
    try:
        source._now = lambda: CREATED
        source.save_profile("妈妈", {})
        v = int(source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(24),
        )["id"])
        a_new = int(source.add_appointment(
            "妈妈", "复诊：复查", scheduled_at=_day(24) + "T09:00",
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
    store = _store(tmp_path, "f14")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v1 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date=_day(24),
        )["id"])
        d_appt = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=_day(20) + "T09:00",
            note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
        )["id"])
        r_old = int(store.add_reminder(
            "复诊：高血压", _day(19), note="复诊",
            profile="妈妈", appointment_id=d_appt,
        )["id"])
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-10-02", diagnosis="高血压",
            next_followup_date=_day(19),
        )["id"])
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?,"
                " followup_reminder_id = ?, updated_at = ? WHERE id = ?",
                (d_appt, r_old, CREATED, v2),
            )
        store.import_data(backup)
        v2_row = store.get_visit_record(v2)
        reminders = store.list_reminders(True, "妈妈")
        # 仍被其它就诊链接的重复预约是其复诊计划：提醒与引用保留
        # （第一百一十三轮 [14]）
        assert int(v2_row["followup_appointment_id"] or 0) == d_appt
        assert int(v2_row["followup_reminder_id"] or 0) == r_old
        assert any(int(r["id"]) == r_old for r in reminders)
    finally:
        store.close()


# ---------------------------------------------------------------- [11][15][16]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_elseif_branch_counts_queued_send():
    source = _app_js()
    # 无轮次会话的换档回滚同样识别排队提交（第一百一十三轮 [11]）
    assert (
        "busy || sendInFlight || (!ok && sessionId !== attemptSession)"
        in source
    )


def test_web_programmatic_fill_not_profile_change():
    source = _app_js()
    # 程序化回填下拉（重连/备份导入）不算换档（第一百一十三轮 [15]）
    assert "const selectNow = els.profileSelect.value || '';" in source
    assert "&& selectNow !== (sendProfile || '')" in source


def test_web_save_refreshes_options_on_seq_change():
    source = _app_js()
    start = source.index("async function savePatientProfile(")
    block = source[start:start + 3000]
    # 请求期间换档后仍要重建下拉，新档案立即可选（第一百一十三轮 [16]）
    assert (
        "refreshProfileOptions(els.profileSelect.value || activeProfileName);"
        in block
    )
