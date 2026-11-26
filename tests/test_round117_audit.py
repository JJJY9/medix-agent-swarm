# -*- coding: utf-8 -*-
"""第一百一十七轮审查修复的回归测试。"""
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


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "tail", ["现在也吃", "现在也在吃", "目前也吃", "现在也吃着"]
)
def test_present_also_continues_stopped_drug(tail):
    # 「我以前吃华法林，现在也吃」是续用（第一百一十七轮 [1]）
    text = f"我以前吃华法林，{tail}，今天头撞到门框了"
    assert "抗凝治疗期间头部外伤" in _labels(text), _red(text)
    result = check_medication_safety(
        f"我以前吃华法林，{tail}，能吃布洛芬吗", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_present_also_other_drug_control():
    # 「现在也吃阿司匹林」不能复活华法林
    assert "抗凝治疗期间头部外伤" not in _labels(
        "我以前吃华法林，现在也吃阿司匹林，今天头撞到门框了"
    )


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "晚上胸痛是什么病",
        "晚上咯血是什么病",
        "晚上呼吸困难是什么病",
        "每天胸痛是什么病",
        "频繁胸痛是什么病",
        "胸痛是什么病，晚上疼",
        "胸痛是什么病，每天疼",
    ],
)
def test_definition_time_frequency_cues(text):
    # 时段/频次词说明在描述真实症状（第一百一十七轮 [2]）
    assert _level(text) == "emergency", _red(text)


def test_definition_time_cue_control():
    assert _level("夜间胸痛是什么病") == "emergency"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "怀孕了，不确定有没有出血",
        "怀孕8周，不知道有没有出血",
        "孕8周，不确定有没有流血",
    ],
)
def test_pregnancy_bleeding_uncertainty(text):
    # 不确定有没有出血按待排查处理（第一百一十七轮 [3]）
    assert "孕产期出血" in _labels(text), _red(text)


def test_pregnancy_bleeding_uncertainty_controls():
    assert "孕产期出血" in _labels("怀孕8周，不确定有没有阴道出血")
    assert _level("孕8周，阴道不再流血了") == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "key", ["history", "medications"]
)
def test_restart_window_with_child_clause(key):
    context = {
        "age": "40岁",
        key: "华法林吃了三年，停了一年多，孩子出生后我又吃上了",
    }
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框", context
    ), _red("今天头撞到门框", context)


def test_restart_window_family_control():
    assert _level(
        "今天头撞到门框",
        {"age": "40岁", "history": "华法林停了一年多，我爸又吃上了"},
    ) == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "没有发烧、咳嗽、咽痛、乏力、恶心、呕吐、腹痛、腹泻、胸痛",
        "否认发热、咳嗽、咽痛、乏力、恶心、呕吐、腹痛、腹泻、皮疹、胸痛",
        "没有高血压、糖尿病、冠心病、脑梗、肝病、肾病、胃病、哮喘、"
        "结核、胸痛",
    ],
)
def test_long_enumeration_negation(text):
    # 长枚举否定覆盖胸痛（第一百一十七轮 [7]）
    assert _level(text) == "none", _red(text)


def test_long_enumeration_positive_control():
    assert _level("发烧、咳嗽、胸痛") == "emergency"


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停药两周了，现在牙疼能吃布洛芬吗？",
        "华法林停药2周了，现在牙疼能吃布洛芬吗？",
        "华法林停药两个星期了，现在牙疼能吃布洛芬吗？",
    ],
)
def test_two_week_stop_still_recent(text):
    # 1–2 周仍在抗凝风险窗口（第一百一十七轮 [12]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_two_week_stop_controls():
    result = check_medication_safety(
        "华法林两周前停了，现在牙疼能吃布洛芬吗？", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result
    # 两周半按已停（round103/104 契约）
    names = api_server._context_medication_names(
        {"medications": "华法林（停药2周）"}
    )
    assert any("华法林" in n for n in names), names
    names = api_server._context_medication_names(
        {"medications": "华法林（停药三周）"}
    )
    assert not any("华法林" in n for n in names), names


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "I stopped taking warfarin two years ago, but I take it now,"
        " can I take ibuprofen?",
        "I stopped taking warfarin two years ago, but I take it daily"
        " now, can I take ibuprofen?",
        "I stopped taking warfarin two years ago, but I take it every"
        " day, can I take ibuprofen?",
        "I stopped taking warfarin two years ago, but I have been"
        " taking it since last year, can I take ibuprofen?",
    ],
)
def test_english_present_tense_restart(text):
    # I take it now / I have been taking it（第一百一十七轮 [13]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_english_present_tense_triage():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {
            "age": "40岁",
            "history": "I stopped taking warfarin two years ago,"
            " but I take it now",
        },
    )


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "超声提示低危结节，定期随访即可。",
        "注意避免高危行为，正确使用安全套。",
        "少吃高风险食物，保持饮食清淡。",
        "复查提示中危结节，建议随访。",
    ],
)
def test_risk_keywords_with_object_nouns(text):
    # 对象名词后缀不是风险分级（第一百一十七轮 [5]）
    assert api_server.parse_risk_level(text) == ""


def test_risk_tagged_and_keyword_controls():
    assert api_server.parse_risk_level("风险等级：高危，请尽快就医。") == "high"
    assert api_server.parse_risk_level("情况高危，请立即就医。") == "high"


def test_latest_risk_prefers_tagged_answer():
    answers = [
        {"profile": "妈妈", "answered_at": "2026-11-21",
         "content": "风险等级：高危，请尽快就医。", "message_id": 1},
        {"profile": "妈妈", "answered_at": "2026-11-25",
         "content": "超声提示低危结节，定期随访即可。", "message_id": 2},
    ]
    latest = api_server._latest_parseable_risk(answers)
    assert (latest.get("妈妈") or {}).get("risk") == "high"


# ---------------------------------------------------------------- [17]
def test_family_attention_adherence_level():
    # 0% 依从与健康提示同为 warning（第一百一十七轮 [17]）
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={"worst_rate": 0,
                     "adherence": {"expected": 14, "rate": 0}},
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert attention.get("level") == "warning"
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={"worst_rate": 55,
                     "adherence": {"expected": 14, "rate": 55}},
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert attention.get("level") == "info"


# ---------------------------------------------------------------- [4]
def test_fallback_sole_claim_respects_source(tmp_path):
    # fallback 锚点 sole 认领不能吞另一来源的提醒
    # （第一百一十七轮 [4]）
    src_a = _store(tmp_path, "s4a")
    src_b = _store(tmp_path, "s4b")
    target = _store(tmp_path, "s4t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("爸爸", {})
        row = src_a.add_reminder(
            "吃药", "2026-06-08", note="n2", profile="爸爸",
            advance_days=1, repeat_days=7,
        )["id"]
        src_a._now = lambda: "2026-06-05T08:00:00"
        src_a.update_reminder(int(row), note="n1")
        backup_a = src_a.export_data()
        src_b.add_reminder("复诊", "2026-06-08", note="", profile="爸爸")
        src_b.add_reminder(
            "测血压", "2026-06-08", note="n1", profile="爸爸",
            advance_days=1, repeat_days=7,
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        assert target.import_data(backup_b).get("reminders") == 2
        rows = target.list_reminders(True, "爸爸")
        assert any(r["title"] == "复诊" for r in rows)
        assert any(r["title"] == "测血压" for r in rows)
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [8]
def test_no_created_visits_keep_own_appointments(tmp_path):
    # 无 created_at 时同一备份里不同就诊的自动复诊预约不能被合并
    # （第一百一十七轮 [8]）
    store = _store(tmp_path, "s8")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-10",
                 "diagnosis": "高血压", "department": "内科",
                 "doctor": "张医生", "next_followup_date": "2026-12-16",
                 "followup_appointment_id": 30,
                 "followup_reminder_id": 0},
                {"id": 11, "profile": "妈妈", "visit_date": "2026-11-20",
                 "diagnosis": "高血压", "department": "内科",
                 "doctor": "张医生", "next_followup_date": "2026-12-16",
                 "followup_appointment_id": 31,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "department": "内科",
                 "doctor": "张医生", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建"},
                {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "department": "内科",
                 "doctor": "张医生", "status": "upcoming",
                 "owner_visit_id": 11,
                 "note": "由就诊记录 #11 自动创建"},
            ],
        }
        result = store.import_data(forged)
        assert result.get("appointments") == 2
        assert len(store.list_appointments("妈妈", None)) == 2
        visits = store.list_visit_records("妈妈")
        assert all(int(v["followup_appointment_id"] or 0) for v in visits)
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_deleted_other_source_reminder_restored(tmp_path):
    # 8725 兜底不能把另一来源提醒认领为本行，删除后仍能恢复
    # （第一百一十七轮 [9]）
    src_a = _store(tmp_path, "s9a")
    src_b = _store(tmp_path, "s9b")
    target = _store(tmp_path, "s9t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: "2026-06-01T08:00:01"
            st.save_profile("爸爸", {})
        row = src_a.add_reminder(
            "吃药", "2026-06-08", note="n2", profile="爸爸",
            advance_days=1, repeat_days=7,
        )["id"]
        src_a._now = lambda: "2026-06-05T08:00:00"
        src_a.update_reminder(int(row), note="n1")
        backup_a = src_a.export_data()
        src_b._now = lambda: "2026-06-01T08:00:01"
        src_b.add_reminder("吃药", "2026-06-08", note="n1", profile="爸爸")
        src_b.add_reminder(
            "测血压", "2026-06-08", note="", profile="爸爸",
            advance_days=1, repeat_days=7,
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rows = target.list_reminders(True, "爸爸")
        b_row = next(
            r for r in rows
            if r["title"] == "吃药" and int(r["advance_days"] or 0) == 0
        )
        target.delete_reminder(int(b_row["id"]))
        added = target.import_data(backup_b).get("reminders")
        rows = target.list_reminders(True, "爸爸")
        restored = [
            r for r in rows
            if r["title"] == "吃药" and int(r["advance_days"] or 0) == 0
        ]
        assert added == 1
        assert len(restored) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [10]
def test_visit_date_change_respects_reminder_tombstone(tmp_path):
    # 改复诊日期不能复活用户删除过的提醒（第一百一十七轮 [10]）
    store = _store(tmp_path, "s10")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-01",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-01",
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-15", "note": "复诊",
                 "appointment_id": 30, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        appt = store.list_appointments("妈妈", None)[0]
        reminder = store.list_reminders(False, "妈妈")[0]
        store.delete_reminder(int(reminder["id"]))
        store.update_visit_record(
            vid, "妈妈", next_followup_date="2026-12-16"
        )
        assert store.list_reminders(False, "妈妈") == []
        visit_row = store.get_visit_record(vid)
        assert int(visit_row["followup_appointment_id"] or 0) == int(
            appt["id"]
        )
        assert int(visit_row["followup_reminder_id"] or 0) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [15]
def test_merged_backups_single_followup_plan(tmp_path):
    # 合并两份备份后同一就诊只保留一条自动复诊预约与一条待办
    # （第一百一十七轮 [15]）
    src_a = _store(tmp_path, "s15a")
    src_b = _store(tmp_path, "s15b")
    target = _store(tmp_path, "s15t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        vid = int(src_a.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-12-16",
        )["id"])
        a_appt = int(src_a.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-16T09:00",
            note=f"由就诊记录 #{vid} 自动创建",
        )["id"])
        src_a.add_reminder(
            "复诊：高血压", "2026-12-15", note="复诊", profile="妈妈",
            appointment_id=a_appt,
        )
        backup_a = src_a.export_data()
        src_b.import_data(backup_a)
        src_b._now = lambda: "2026-06-02T08:00:00"
        vid_b = int(src_b.list_visit_records("妈妈")[0]["id"])
        b_appt = int(src_b.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-16T09:00",
            note=f"由就诊记录 #{vid_b} 自动创建",
        )["id"])
        src_b.add_reminder(
            "复诊：高血压", "2026-12-15", note="复诊", profile="妈妈",
            appointment_id=b_appt,
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rows = target.list_appointments("妈妈", None)
        owned = [
            r for r in rows
            if r["status"] == "upcoming"
            and (
                r.get("owner_visit_id")
                or "由就诊记录 #" in str(r.get("note") or "")
            )
        ]
        pending = target.list_reminders(False, "妈妈")
        assert len(owned) == 1
        assert len(pending) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [11][14][16]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_deferred_draft_writeback():
    source = _app_js()
    start = source.index("function restoreDeferredCrossOwnerDraft()")
    block = source[start:start + 1600]
    # 目标会话不匹配时也要写回存储（第一百一十七轮 [11][14]）
    assert block.count("safeSessionSet(DRAFT_KEY, deferred.text);") >= 2
    assert "initialSessionFromUrl && deferredCrossOwnerDraft" in source


def test_web_copy_text_care_and_today():
    source = _app_js()
    start = source.index("function familyOverviewText(")
    block = source[start:start + 6500]
    assert "profile.care_plan" in block
    assert "nextCare" in block
    assert "今日已记录" in block
