# -*- coding: utf-8 -*-
"""第一百二十五轮审查修复的回归测试。"""
import csv
import io
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path, tag="s"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    st = _store(tmp_path, "api")
    monkeypatch.setattr(memory, "get_session_store", lambda: st)
    try:
        yield TestClient(api_server.app), st
    finally:
        st.close()


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "剖宫产第10天，阴道出血",
        "剖宫产二胎10天，阴道出血",
        "剖宫产已满10天，阴道出血",
        "剖宫产刚10天，阴道出血",
        "剖宫产才10天，阴道出血",
        "剖宫产第十天，阴道出血",
        "剖宫产二十多天，阴道出血",
        "剖宫产半个月，阴道出血",
        "剖腹产二胎一周，阴道出血",
    ],
)
def test_caesarean_recent_forms(text):
    # 剖宫产第N天/二胎N天/二十多天等近期产科写法（第一百二十五轮 [1]）
    r = _red(text)
    assert "孕产期出血" in r["matches"], r


@pytest.mark.parametrize(
    "text",
    [
        "生完二胎三年了，今天阴道出血",
        "顺产二胎三年了，今天阴道出血",
    ],
)
def test_postpartum_ordinal_years_not_recent(text):
    # 生完二胎三年是既往产科史（第一百二十五轮 [1]）
    assert _red(text)["level"] == "none", _red(text)


def test_caesarean_control():
    r = _red("剖宫产术后10天，阴道出血")
    assert "孕产期出血" in r["matches"], r


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "胸口像针扎一样疼",
        "胸口像刀割一样疼",
        "胸口像压了块石头一样疼",
        "心口窝疼",
        "心口窝一阵一阵疼",
    ],
)
def test_chest_pain_variants(text):
    # 比喻式胸痛与心口窝疼（第一百二十五轮 [2]）
    r = _red(text)
    assert "胸痛" in r["matches"], r


# ---------------------------------------------------------------- [3][12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了两年了，昨天吃了火锅，今天头撞到",
        "华法林停了两年了，昨天吃了东西，今天头撞到",
        "甲氨蝶呤停了三年了，昨天吃了火锅，今天发烧39度",
        "华法林停了半年，昨天吃上了阿司匹林，今天头撞到门框",
    ],
)
def test_restart_object_attribution(text):
    # 近期续用词形的宾语参与他药/非药物判定（第一百二十五轮 [3][12]）
    assert _red(text)["level"] == "none", _red(text)


def test_restart_object_control():
    r = _red(
        "华法林停了半年，昨天开始吃了，今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in r["matches"], r


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text,label",
    [
        ("胸痛是什么病，血压200", "胸痛"),
        ("抽搐是什么病，体温39度5", "抽搐"),
        ("胸痛是什么病，血压都200了", "胸痛"),
    ],
)
def test_vitals_after_definition_question(text, label):
    # 定义问句后的生命体征建立患者语境（第一百二十五轮 [4]）
    r = _red(text)
    assert label in r["matches"], r


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "哪些人容易胸痛",
        "什么人容易胸痛",
        "胸痛会遗传吗",
        "心梗会胸痛吗",
        "感冒会引起胸痛吗",
        "胸痛怎么预防",
        "胸痛挂什么科",
    ],
)
def test_educational_question_forms(text):
    # 无患者语境的科普问句不按主诉处理（第一百二十五轮 [5]）
    assert _red(text)["level"] == "none", _red(text)


def test_educational_question_control():
    r = _red("胸痛是什么病，血压200")
    assert r["level"] == "emergency", r


# ---------------------------------------------------------------- [6]
def test_metric_gap_backup_idempotent(tmp_path):
    # 源库删过中间记录后重复恢复不补插重复读数（第一百二十五轮 [6]）
    src = _store(tmp_path, "s6s")
    target = _store(tmp_path, "s6t")
    try:
        for st in (src, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        for value in (120, 125, 130):
            src.add_metric(
                "妈妈", "bp", value, 80,
                measured_at="2026-06-01T0%d:00" % (value % 10),
            )
        rows = src.list_metrics(limit=100)
        src.delete_metric(int(rows[1]["id"]))
        backup = src.export_data()
        first = target.import_data(backup).get("metrics")
        second = target.import_data(backup).get("metrics")
        assert first == 2
        assert second == 0
    finally:
        src.close()
        target.close()


# ---------------------------------------------------------------- [7]
def test_edited_note_reminder_tombstone(tmp_path):
    # 改过备注的附着复诊提醒删除仍写墓碑（第一百二十五轮 [7]）
    st = _store(tmp_path, "s7")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = _day(10)
        vid = int(st.add_visit_record(
            "妈妈", visit_date=_day(-1), diagnosis="高血压",
            next_followup_date=d1,
        )["id"])
        api_server._schedule_followup_sync(st, vid)
        visit = st.get_visit_record(vid)
        appt = int(visit["followup_appointment_id"])
        rem = int(visit["followup_reminder_id"])
        st._now = lambda: "2026-11-27T10:00:00"
        st.update_reminder(rem, note="复诊 " + d1 + " 带化验单")
        st.update_appointment_partial(
            appt, scheduled_at=_day(20) + "T09:00"
        )
        st.delete_reminder(rem)
        assert st.get_app_state(f"reminder_deleted_{appt}") != ""
        api_server._schedule_followup_sync(st, vid)
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        visit = st.get_visit_record(vid)
        assert pending == []
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        st.close()


# ---------------------------------------------------------------- [8][14][18]
def test_today_med_without_started_at_exports(client_and_store):
    # 未填开始日期（created_at 今天）的当天新药导出不显示 0% 依从
    # （第一百二十五轮 [8][14][18]）
    client, st = client_and_store
    st.save_profile("妈妈", {})
    st.add_medication(
        "妈妈", "氨氯地平", dose="5mg", frequency="每日一次",
    )
    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈", "days": 30}
    ).text
    assert "依从率 0%" not in dossier
    csv_resp = client.get("/api/medications/export.csv")
    reader = list(csv.reader(io.StringIO(
        csv_resp.text.lstrip("﻿")
    )))
    row = next(r for r in reader if r and r[0] == "氨氯地平")
    assert row[9] == ""
    assert row[10] == ""


# ---------------------------------------------------------------- [9]
def test_web_send_profiles_snapshot():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function send(")
    block = source[start:start + 4200]
    assert "const submittedProfileSnapshot" in block
    assert (
        block.find("submittedProfileSnapshot")
        < block.find("await Promise.race([")
    )
    assert "const submittedProfile = submittedProfileSnapshot;" in source


# ---------------------------------------------------------------- [10]
def test_web_mixed_draft_sentinel():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "__mixed_draft__" in source


# ---------------------------------------------------------------- [11]
def test_import_tombstone_keeps_user_reminder(tmp_path):
    # 导入墓碑只删本次新插入的提醒，不误删用户自建待办
    # （第一百二十五轮 [11]）
    st = _store(tmp_path, "s11")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = _day(10)
        vid = int(st.add_visit_record(
            "妈妈", visit_date=_day(-1), diagnosis="高血压",
            next_followup_date=d1,
        )["id"])
        api_server._schedule_followup_sync(st, vid)
        visit = st.get_visit_record(vid)
        appt = int(visit["followup_appointment_id"])
        auto_rem = int(visit["followup_reminder_id"])
        user_rem = int(st.add_reminder(
            "带病历本和医保卡", _day(9), note="", profile="妈妈",
            appointment_id=appt,
        )["id"])
        backup = st.export_data()
        st._now = lambda: "2026-11-27T10:30:00"
        st.delete_reminder(auto_rem)
        st.update_appointment_partial(
            appt, scheduled_at=_day(25) + "T09:00"
        )
        st.import_data(backup)
        user_row = st.get_reminder(user_rem)
        assert user_row is not None
        assert not user_row.get("done")
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        assert all(int(r["id"]) != auto_rem for r in pending)
    finally:
        st.close()


# ---------------------------------------------------------------- [13]
def test_reminder_only_link_tombstone(tmp_path):
    # 仅提醒链接的备份同样复核墓碑（第一百二十五轮 [13]）
    st = _store(tmp_path, "s13")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = _day(15)
        backup = {
            "source_id": "a" * 32,
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": _day(-20),
                 "diagnosis": "高血压", "next_followup_date": d1,
                 "followup_appointment_id": 0, "followup_reminder_id": 40,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": d1 + "T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(14), "note": "复诊 " + d1,
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        st.import_data(backup)
        vid = int(st.list_visit_records("妈妈")[0]["id"])
        appt = int(st.list_appointments("妈妈", None, False, 100)[0]["id"])
        rem = int(st.list_reminders(False, "妈妈")[0]["id"])
        st._now = lambda: "2026-11-27T10:45:00"
        st.delete_reminder(rem)
        assert st.get_app_state(f"reminder_deleted_{appt}") != ""
        st.import_data(backup)
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        visit = st.get_visit_record(vid)
        assert pending == []
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        st.close()


# ---------------------------------------------------------------- [15]
def test_resumed_levels_match(client_and_store):
    # 当天重新启用的药在家庭总览与健康提示同为 warning
    # （第一百二十五轮 [15]）
    client, st = client_and_store
    st.save_profile("爷爷", {})
    yesterday = (
        datetime.now() - timedelta(days=1)
    ).strftime("%Y-%m-%d")
    med = st.add_medication(
        "爷爷", "二甲双胍", dose="500mg", frequency="每日两次",
        started_at=yesterday,
    )
    st.update_medication(int(med["id"]), active=False)
    st.update_medication(int(med["id"]), active=True)
    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    grandpa = next(
        m for m in overview["profiles"] if m["name"] == "爷爷"
    )
    insights = client.get(
        "/api/health/insights", params={"profile": "爷爷"}
    ).json()["insights"]
    family_level = (grandpa.get("attention") or {}).get("level")
    insight_level = next(
        i["level"] for i in insights if i.get("key") == "adherence"
    )
    assert family_level == insight_level == "warning"


# ---------------------------------------------------------------- [16]
def test_web_fresh_marker_cleared():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert (
        "safeSessionRemove(freshSessionMarkKey(expectedSession));" in source
    )


# ---------------------------------------------------------------- [17]
def test_web_consult_context_profile_fallback():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function buildConsultContext(signal)")
    block = source[start:start + 700]
    assert "activeProfileName" in block


# ---------------------------------------------------------------- [14][18]
def test_web_med_card_skips_today_new():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "if (expectedDoses > 0 && !startedToday)" in source


# ---------------------------------------------------------------- [19]
@pytest.mark.parametrize(
    "text",
    ["易孕期，内裤上有血", "易孕期，阴道出血"],
)
def test_yiyunqi_not_pregnancy(text):
    # 易孕期不是妊娠（第一百二十五轮 [19]）
    assert _red(text)["level"] == "none", _red(text)
