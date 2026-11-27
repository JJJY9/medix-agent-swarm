# -*- coding: utf-8 -*-
"""第一百二十三轮审查修复的回归测试。"""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text,label",
    [
        ("孕12周，阴道出血见于什么病", "孕产期出血"),
        ("停经45天，阴道出血见于什么病", "孕产期出血"),
        ("抽搐吐白沫见于什么病", "抽搐"),
        ("化疗期间，发烧39度见于什么病", "免疫功能低下伴发热"),
        ("胸痛伴呼吸困难见于什么病", "胸痛"),
        ("左侧肢体无力、说话不清见于什么病", "言语不清"),
    ],
)
def test_definition_mid_q_keeps_real_complaints(text, label):
    # 「见于什么病」定义问句与「是什么病」后缀分支同口径
    # （第一百二十三轮 [1]）
    r = _red(text)
    assert label in r["matches"], r


@pytest.mark.parametrize(
    "text",
    [
        "胸痛见于什么病",
        "咯血见于什么病",
        "我正在准备考试。咯血见于什么病？",
        "胸痛见于什么病？最近在准备婚礼",
    ],
)
def test_definition_mid_q_controls(text):
    assert _red(text)["level"] == "none", _red(text)


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text,label",
    [
        ("体温39度，抽搐是什么病", "抽搐"),
        ("孩子体温39度，抽搐是什么病", "抽搐"),
        ("血压200，胸痛是什么病", "胸痛"),
        ("血氧85，呼吸困难是什么病", "呼吸困难"),
        ("血糖2.0，意识模糊是什么病", "意识改变"),
        ("心率150，胸痛是什么病", "胸痛"),
    ],
)
def test_vital_signs_establish_patient_context(text, label):
    # 生命体征读数建立患者语境（第一百二十三轮 [2]）
    r = _red(text)
    assert label in r["matches"], r


# ---------------------------------------------------------------- [3][6]
def test_restart_trend_complement():
    # 「又重新吃起来了/吃上了」的趋向补语不是另一种药
    # （第一百二十三轮 [3][6]）
    r = _red(
        "华法林停了半年，又重新吃起来了，今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in r["matches"], r
    for text in [
        "华法林停了半年，又重新吃上了，能吃布洛芬吗",
        "华法林停了半年，又重新吃起来了，能吃布洛芬吗",
        "华法林停了半年，又开始吃上了，能吃布洛芬吗",
    ]:
        result = check_medication_safety(text, {})
        assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [4]
def test_terminal_twin_requires_tail_match(tmp_path):
    # 本地未编辑的终态同秒孪生、其余尾字段不同：补插备份行
    # （第一百二十三轮 [4]）
    src = _store(tmp_path, "s4s")
    target = _store(tmp_path, "s4t")
    try:
        for st in (src, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src.add_reminder(
            "复查", "2026-12-01", note="备份备注", profile="妈妈"
        )
        src.add_reminder("吃药", "2026-12-11", note="", profile="妈妈")
        backup = src.export_data()
        target.add_reminder(
            "复查", "2026-12-01", note="本地备注", profile="妈妈"
        )
        row = target.list_reminders(False, "妈妈")[0]
        target.complete_reminder(int(row["id"]))
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE reminders SET created_at = ?, updated_at = ?"
                " WHERE id = ?",
                (CREATED, CREATED, int(row["id"])),
            )
        added = target.import_data(backup).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        notes = sorted(str(r["note"]) for r in rows)
        assert added == 2, [
            (r["note"], r["done"]) for r in rows
        ]
        assert notes == ["", "备份备注", "本地备注"]
    finally:
        src.close()
        target.close()


# ---------------------------------------------------------------- [5][12]
@pytest.mark.parametrize(
    "text",
    [
        "我停用华法林十天了，能吃布洛芬吗",
        "十天前我停了华法林，能吃布洛芬吗",
        "我停用华法林十日了，能吃布洛芬吗",
        "我停用华法林十来天了，能吃布洛芬吗",
        "我上礼拜停了华法林，能吃布洛芬吗",
        "I stopped warfarin thirteen days ago,"
        " can I take ibuprofen?",
    ],
)
def test_recent_stop_ten_days(text):
    # 十天/十日/十来天/thirteen/上礼拜仍在近期窗口
    # （第一百二十三轮 [5][12]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


@pytest.mark.parametrize(
    "text",
    [
        "我停用华法林十五天了，能吃布洛芬吗",
        "我停用华法林二十天了，能吃布洛芬吗",
        "我停用华法林3周了，能吃布洛芬吗",
    ],
)
def test_recent_stop_ten_days_controls(text):
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" not in _titles(result), result


@pytest.mark.parametrize(
    "text",
    ["华法林（停药十天）", "华法林（停药十日）", "华法林（停药十天前）"],
)
def test_context_names_ten_days(text):
    names = api_server._context_medication_names({"medications": text})
    assert any("华法林" in n for n in names), names


def test_context_names_ten_days_control():
    names = api_server._context_medication_names(
        {"medications": "华法林（停药十五天）"}
    )
    assert not any("华法林" in n for n in names), names


# ---------------------------------------------------------------- [7][14]
def test_owner_query_batched(tmp_path):
    # 大备份的 owner 型预约去重查询分批执行（第一百二十三轮 [7][14]）
    target = _store(tmp_path, "s7")
    try:
        target._now = lambda: CREATED
        target.save_profile("妈妈", {})
        target._conn.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500
        )
        visits = [
            {"id": i, "profile": "妈妈", "visit_date": "2026-11-01",
             "diagnosis": "复诊%d" % i, "next_followup_date": "2026-12-20",
             "followup_appointment_id": 0, "followup_reminder_id": 0,
             "created_at": CREATED, "updated_at": CREATED}
            for i in range(1, 601)
        ]
        backup = {
            "source_id": "d" * 32,
            "visit_records": visits,
            "appointments": [
                {"id": 900, "profile": "妈妈", "title": "复诊：复诊1",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 1,
                 "note": "由就诊记录 #1 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        result = target.import_data(backup)
        assert result.get("visit_records") == 600
        assert result.get("appointments") == 1
    finally:
        target._conn.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32766
        )
        target.close()


# ---------------------------------------------------------------- [8]
def test_terminal_marker_kept_on_reimport(tmp_path):
    # 已取消自动预约的归属标记不因重复导入被剥离
    # （第一百二十三轮 [8]）
    store = _store(tmp_path, "s8")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        backup = {
            "source_id": "e" * 32,
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-01",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-20",
                 "followup_appointment_id": 0, "followup_reminder_id": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "cancelled",
                 "owner_visit_id": 0,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(backup)
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-20T09:00",
            note=f"由就诊记录 #{vid} 自动创建", owner_visit_id=vid,
        )
        store.import_data(backup)
        appt = next(
            a for a in store.list_appointments("妈妈", None, False, 100)
            if a["status"] == "cancelled"
        )
        assert "由就诊记录" in str(appt.get("note") or "")
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_risk_sex_risk_object():
    # 「高危性行为」是对象名词，不是风险分级（第一百二十三轮 [9]）
    assert api_server.parse_risk_level(
        "目前情况低危，注意休息；平时避免高危性行为。"
    ) == "low"
    assert api_server.parse_risk_level(
        "如有高危性行为史，建议加做相关检测并咨询医生。"
    ) == ""
    assert api_server.parse_risk_level("情况高危，请立即就医。") == "high"


# ---------------------------------------------------------------- [10]
def test_family_attention_today_new_aggregate(tmp_path, monkeypatch):
    # 同日多种新药合计 expected≥7 不产生用药打卡提示
    # （第一百二十三轮 [10]）
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path, "s10")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        store.save_profile("妈妈", {})
        today = datetime.now().strftime("%Y-%m-%d")
        for i in range(4):
            store.add_medication(
                "妈妈", "药%d" % i, dose="1片", frequency="每日两次",
                started_at=today,
            )
        data = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        attention = data.get("attention") or {}
        label = str(attention.get("label") or "")
        assert (
            attention.get("level") != "warning"
            or "用药打卡" not in label
        ), attention
        member_med = None
        for member in data.get("members") or []:
            if member.get("profile") == "妈妈":
                member_med = member.get("medications") or {}
        if member_med is not None:
            adherence = member_med.get("adherence") or {}
            assert int(adherence.get("expected") or 0) == 0, adherence
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_web_launch_branch_keeps_profile():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function send(")
    block = source[start:start + 3200]
    assert "preserveDraftProfile(launchDraftProfile" in block
    assert "detachedComposerText = els.input.value" in block


# ---------------------------------------------------------------- [13]
def test_web_detect_risk_normalized():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "text.normalize('NFKC')" in source
    assert "\\u3000" in source
    assert "\\s*[\\s*「【]*不确定" in source


# ---------------------------------------------------------------- [15]
def test_web_save_draft_launch_guard():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "if (!busy && !sendInFlight && !launchSendQueued) {" in source


# ---------------------------------------------------------------- [16]
def test_web_beforeunload_launch_guard():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert (
        "if (!busy && !sendInFlight && !launchSendQueued) return;"
        in source
    )


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 三片/日（已停用两年）",
        "华法林 3片每日一次（已停用两年）",
    ],
)
def test_context_names_chinese_dose(text):
    # 中文数字剂量与剂频粘连不是药名（第一百二十三轮 [17]）
    names = api_server._context_medication_names({"medications": text})
    assert not any("华法林" in n for n in names), names
