# -*- coding: utf-8 -*-
"""第一百二十轮审查修复的回归测试。"""
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
WARFARIN = {"medications": "华法林"}


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "咯血了一口是什么病",
        "咯血了两口是什么病",
        "抽搐了一会儿是什么病",
        "呼吸困难了一会儿是什么病",
        "胸口疼了一下是什么病",
    ],
)
def test_definition_quantifier_kept(text):
    # 量词/短时程是真实病程（第一百二十轮 [1]）
    assert _level(text) == "emergency", patient_complaint_text(text)


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text", ["后脑撞了一下", "后脑磕到墙上", "后脑摔到地上"]
)
def test_bare_houhao_trauma(text):
    # 裸「后脑」+撞/磕/摔同样是头部外伤（第一百二十轮 [2]）
    assert "抗凝治疗期间头部外伤" in _labels(text, WARFARIN), _red(
        text, WARFARIN
    )


def test_houhao_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "后脑勺撞了一下", WARFARIN
    )


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周 是否贫血 阴道没有出血",
        "孕8周 是否有必要查血常规 阴道没有出血",
        "孕8周 有没有量血压 内裤上没有血",
    ],
)
def test_uncertain_not_confuse_blood_tests(text):
    # 贫血/血常规/血压不是出血词（第一百二十轮 [6]）
    assert _level(text) == "none", _red(text)


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text", ["不否认胸痛", "不能否认胸痛", "没有否认胸痛"]
)
def test_double_negation_denial_is_positive(text):
    # 「不否认胸痛」是阳性（第一百二十轮 [7]）
    assert _level(text) == "emergency", _red(text)


def test_double_negation_control():
    assert _level("否认胸痛") == "none"


# ---------------------------------------------------------------- [8][15]
@pytest.mark.parametrize(
    "meds",
    [
        "华法林 3 mg（已停用两年）",
        "华法林 3mg/日（已停用两年）",
        "华法林 3mg 每日一次（已停用两年）",
        "华法林 3mg 每晚一次（已停用两年）",
    ],
)
def test_spaced_dose_and_frequency_stop(meds):
    # 剂量单位/频次分片隔开时停药标注仍归因到药名
    # （第一百二十轮 [8][15]）
    assert _level("今天头撞到门框", {"medications": meds}) == "none", _red(
        "今天头撞到门框", {"medications": meds}
    )


def test_spaced_frequency_stop_prednisone():
    assert _level(
        "今天发烧39度",
        {"age": "40岁", "medications": "泼尼松 5mg 每日一次（已停用两年）"},
    ) == "none"


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "meds", ["华法林（停药12周）", "华法林（停药三周）"]
)
def test_weeks_long_stop(meds):
    # ≥3 周按远期停药（第一百二十轮 [16]）
    assert _level(
        "今天头撞到门框", {"age": "40岁", "medications": meds}
    ) == "none", _red(
        "今天头撞到门框", {"age": "40岁", "medications": meds}
    )


def test_two_week_control():
    assert "抗凝治疗期间头部外伤" in _labels(
        "今天头撞到门框",
        {"age": "40岁", "medications": "华法林（停药2周）"},
    )


# ---------------------------------------------------------------- [17]
def test_quote_fever_immuno():
    assert "免疫功能低下伴发热" in _labels(
        "网上说发烧要重视我烧到38.5度", {"history": "正在化疗"}
    )


def test_quote_fever_infant():
    assert "3 个月以下婴儿发热" in _labels(
        "网上说发烧要重视我宝宝烧到38.5度", {"age": "2个月"}
    )


# ---------------------------------------------------------------- [5][11]
@pytest.mark.parametrize(
    "text, expect",
    [
        ("目前出血风险：低，综合风险：高危，请尽快就医评估。", "high"),
        (
            "【风险评估】\n风险等级：**不确定**\n【回答】您的低血糖风险：低，"
            "请规律监测血糖。",
            "",
        ),
        ("风险等级：高危，请立即就医。", "high"),
        (
            "HPV 分为高危型和低危型，低危型 HPV 通常引起尖锐湿疣。",
            "",
        ),
        ("低危HPV 定期复查即可。", ""),
    ],
)
def test_risk_label_and_hpv(text, expect):
    # 出血风险/低危型 HPV 不是整体分级（第一百二十轮 [5][11]）
    assert api_server.parse_risk_level(text) == expect


# ---------------------------------------------------------------- [19]
def test_worst_drug_started_today_not_warning():
    # 老药 100% + 当天新开药 0% 不算 warning（第一百二十轮 [19]）
    today = api_server.datetime.now().strftime("%Y-%m-%d")
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 0,
            "worst_started_at": today + "T08:00:00",
            "oldest_started_at": "2026-10-01T08:00:00",
            "adherence": {"expected": 8, "rate": 88},
        },
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert attention.get("level") != "warning" or (
        "用药打卡" not in str(attention.get("label") or "")
    ), attention


def test_old_worst_drug_still_warning():
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 0,
            "worst_started_at": "2026-06-10T08:00:00",
            "oldest_started_at": "2026-06-10T08:00:00",
            "adherence": {"expected": 1, "rate": 0},
        },
        medication_lab={"count": 1, "level": "info",
                        "alerts": [{"title": "头孢类与酒精"}]},
        vaccinations={}, appointments={},
    )
    assert attention.get("level") == "warning"


# ---------------------------------------------------------------- [3]
def test_other_source_differing_names_imported(tmp_path):
    # 合并备份：另一来源不同药名不能被末位 sole 认领
    # （第一百二十轮 [3]）
    src_a = _store(tmp_path, "s3a")
    src_b = _store(tmp_path, "s3b")
    target = _store(tmp_path, "s3t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "华法林", dose="3mg", frequency="每日一次"
        )
        src_a.add_medication(
            "妈妈", "氨氯地平", dose="5mg", frequency="每日两次"
        )
        backup_a = src_a.export_data()
        src_b.add_medication(
            "妈妈", "二甲双胍", dose="", frequency="每日一次"
        )
        src_b.add_medication(
            "妈妈", "二甲双胍", dose="5mg", frequency="每日两次"
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        assert target.import_data(backup_b).get("medications") == 2
        names = [r["name"] for r in target.list_medications("妈妈", None)]
        assert names.count("二甲双胍") == 2
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [4]
def test_terminal_twin_not_claimed(tmp_path):
    # 独立来源的终态同秒孪生不能被当成继承记录
    # （第一百二十轮 [4]）
    src_a = _store(tmp_path, "s4a")
    src_b = _store(tmp_path, "s4b")
    target = _store(tmp_path, "s4t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder(
            "吃药", "2026-06-08", note="A说明", profile="妈妈"
        )
        backup_a = src_a.export_data()
        src_b.add_reminder(
            "吃药", "2026-06-08", note="B说明", profile="妈妈"
        )
        row_b = src_b.list_reminders(False, "妈妈")[0]
        src_b._now = lambda: "2026-06-02T08:00:00"
        src_b.complete_reminder(int(row_b["id"]))
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        a_row = next(
            r for r in target.list_reminders(True, "妈妈")
            if r["note"] == "A说明"
        )
        target.delete_reminder(int(a_row["id"]))
        added = target.import_data(backup_a).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 1
        assert any(r["note"] == "A说明" for r in rows)
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [9]
def test_sibling_twin_restored(tmp_path):
    # mixed 同源/占位行漂移后删除孪生，重导要补回被删行
    # （第一百二十轮 [9]）
    src_a = _store(tmp_path, "s9a")
    src_b = _store(tmp_path, "s9b")
    target = _store(tmp_path, "s9t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder(
            "吃药", "2026-06-08", note="", profile="妈妈",
            advance_days=1,
        )
        backup_a = src_a.export_data()
        src_b.add_reminder("吃药", "2026-06-08", note="n1", profile="妈妈")
        src_b.add_reminder(
            "吃药", "2026-06-08", note="n1", profile="妈妈",
            repeat_days=7,
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rep7 = next(
            r for r in target.list_reminders(True, "妈妈")
            if int(r["repeat_days"] or 0) == 7
        )
        target.delete_reminder(int(rep7["id"]))
        added = target.import_data(backup_b).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        restored = [
            r for r in rows if int(r["repeat_days"] or 0) == 7
        ]
        assert added >= 1
        assert len(restored) == 1
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [10]
def test_owner_and_marker_auto_appointment_dedupe(tmp_path):
    # owner 型与标记型自动复诊预约同组去重（第一百二十轮 [10]）
    store = _store(tmp_path, "s10")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-01",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-16",
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-16T09:00", "status": "upcoming",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 31, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 10, "note": "",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-15", "note": "复诊",
                 "appointment_id": 30, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 41, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-12-19", "note": "复诊",
                 "appointment_id": 31, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        rows = store.list_appointments("妈妈", None)
        owned = [
            r for r in rows
            if r["status"] == "upcoming"
            and (
                r.get("owner_visit_id")
                or "由就诊记录 #" in str(r.get("note") or "")
            )
        ]
        pending = store.list_reminders(False, "妈妈")
        assert len(owned) == 1
        assert len(pending) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [18]
def test_link_writeback_keeps_other_owner(tmp_path):
    # 写回就诊复诊链接不覆盖其它就诊已有归属（第一百二十轮 [18]）
    store = _store(tmp_path, "s18")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="骨折",
            next_followup_date="2026-12-10",
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：骨折", scheduled_at="2026-12-10T09:00",
            owner_visit_id=v2,
        )["id"])
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-05",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-10",
                 "followup_appointment_id": 31,
                 "followup_reminder_id": 0},
            ],
            "appointments": [
                {"id": 31, "profile": "妈妈", "title": "复诊：骨折",
                 "scheduled_at": "2026-12-10T09:00", "status": "upcoming",
                 "owner_visit_id": 0, "note": "",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        appt_row = store.get_appointment(appt)
        assert int(appt_row.get("owner_visit_id") or 0) == v2
    finally:
        store.close()


# ---------------------------------------------------------------- [12][13][14][20]
def _app_js():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_launch_send_queued_guard():
    source = _app_js()
    assert "launchSendQueued" in source
    assert "!launchSendQueued" in source


def test_web_launch_session_switch_check():
    source = _app_js()
    start = source.index("async function send(")
    block = source[start:start + 2400]
    assert "launchSessionSnapshot" in block
    assert "会话已切换，刚才的主诉未提交" in block


def test_web_open_session_restores_owned_draft():
    source = _app_js()
    start = source.index("async function openSession(")
    block = source[start:start + 3200]
    assert "incomingDraftText" in block


def test_web_fresh_session_not_detached():
    source = _app_js()
    assert "!isFreshPageSession(sessionId)" in source
