# -*- coding: utf-8 -*-
"""第一百二十四轮审查修复的回归测试。"""
import os
import sqlite3
from datetime import datetime, timedelta
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


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "华法林停了半年，昨天开始吃了，今天头撞到门框",
        "华法林停了半年，昨天吃上了，今天头撞到门框",
        "华法林停了半年，昨天才开始吃，今天头撞到门框",
        "华法林停了半年，前天开始吃了，今天头撞到门框",
        "华法林停了半年，上周开始吃了，今天头撞到门框",
        "华法林停了半年，周一开始吃了，今天头撞到门框",
        "华法林停了半年，三天前开始吃了，今天头撞到门框",
    ],
)
def test_restart_recent_forms_triage(text):
    # 近期时间词 + 开始吃/吃上了是续用（第一百二十四轮 [1]）
    r = _red(text, {"age": "40岁"})
    assert "抗凝治疗期间头部外伤" in r["matches"], r


def test_restart_recent_forms_medication():
    for text in [
        "华法林停了半年，昨天开始吃了，能吃布洛芬吗",
        "华法林停了半年，昨天吃上了，能吃布洛芬吗",
        "华法林停了半年，昨天才开始吃，能吃布洛芬吗",
        "warfarin was stopped 6 months ago but I started taking it"
        " yesterday. Can I take ibuprofen?",
    ]:
        result = check_medication_safety(text, {})
        assert "华法林与非甾体抗炎药" in _titles(result), result


def test_restart_english_triage():
    r = _red(
        "warfarin was stopped 6 months ago but I started taking it"
        " yesterday. 今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in r["matches"], r


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "华法林最近停了，能吃布洛芬吗",
        "华法林近来停了，能吃布洛芬吗",
        "warfarin recently stopped, can I take ibuprofen?",
    ],
)
def test_recent_stop_zui_jin(text):
    # 「最近/近来停了」按近期停药保守处理（第一百二十四轮 [2]）
    result = check_medication_safety(text, {})
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [3][5]
@pytest.mark.parametrize(
    "text,label",
    [
        ("血氧85，呼吸困难见于什么病", "呼吸困难"),
        ("孩子体温39度，抽搐见于什么病", "抽搐"),
        ("血压200，胸痛见于什么病", "胸痛"),
        ("血氧饱和度85，呼吸困难是什么病", "呼吸困难"),
        ("体温三十九度，抽搐是什么病", "抽搐"),
        ("血压飙到200，胸痛是什么病", "胸痛"),
        ("收缩压200，胸痛是什么病", "胸痛"),
    ],
)
def test_vitals_context_variants(text, label):
    # 生命体征建立症状语境并支持常见写法（第一百二十四轮 [3][5]）
    r = _red(text)
    assert label in r["matches"], r


# ---------------------------------------------------------------- [4]
def test_foreign_terminal_twin_not_claimed(tmp_path):
    # B 先 A 后：另一来源的终态同秒行不能吞掉 A（第一百二十四轮 [4]）
    target = _store(tmp_path, "s4t")
    src_b = _store(tmp_path, "s4b")
    try:
        for st in (target, src_b):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_b.add_reminder("吃药", "2026-06-08", note="n", profile="妈妈")
        src_b.add_reminder("吃药", "2026-06-08", note="n2", profile="妈妈")
        for row in src_b.list_reminders(False, "妈妈"):
            src_b.complete_reminder(int(row["id"]))
        target.import_data(src_b.export_data())
        target._now = lambda: "2026-06-02T09:00:00"
        backup_a = {
            "source_id": "a" * 32,
            "reminders": [
                {"id": 1, "profile": "妈妈", "title": "吃药",
                 "due_at": "2026-06-10", "note": "n", "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        added = target.import_data(backup_a).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 1, [(r["due_at"], r["done"]) for r in rows]
        assert any(str(r["due_at"]) == "2026-06-10" for r in rows)
    finally:
        target.close()
        src_b.close()


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    ["避孕期间，阴道出血", "备孕期间，内裤上有血", "备孕期，见红"],
)
def test_bei_yun_not_pregnancy(text):
    # 备孕/避孕期间的「孕期」子串不是妊娠（第一百二十四轮 [6]）
    assert _red(text)["level"] == "none", _red(text)


def test_bei_yun_control():
    assert "孕产期出血" in _red("怀孕8周，阴道出血")["matches"]


# ---------------------------------------------------------------- [7]
def test_fallback_foreign_winner_inserted(tmp_path):
    # fallback 评分胜者需跨来源守卫（第一百二十四轮 [7]）
    target = _store(tmp_path, "s7t")
    src_b = _store(tmp_path, "s7b")
    try:
        for st in (target, src_b):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_b.add_metric(
            "妈妈", "bp", 120, 80, measured_at="2026-06-01T07:00"
        )
        src_b.add_metric(
            "妈妈", "bp", 130, 85, measured_at="2026-06-01T08:00"
        )
        src_b._now = lambda: "2026-06-02T09:00:00"
        for row in src_b.list_metrics(limit=100):
            src_b.update_metric(int(row["id"]), note="edited")
        target.import_data(src_b.export_data())
        backup_a = {
            "source_id": "a" * 32,
            "health_metrics": [
                {"id": 1, "profile": "妈妈", "metric": "bp",
                 "value1": 120, "value2": 80,
                 "measured_at": "2026-06-01T09:00", "note": "",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        added = target.import_data(backup_a).get("metrics")
        rows = target.list_metrics(limit=100)
        assert added == 1, [r["measured_at"] for r in rows]
        assert any(
            str(r["measured_at"]) == "2026-06-01T09:00" for r in rows
        )
    finally:
        target.close()
        src_b.close()


# ---------------------------------------------------------------- [8][18]
def test_appointment_id_batching(tmp_path):
    # 预约 id 的 IN 查询分批执行（第一百二十四轮 [8][18]）
    target = _store(tmp_path, "s8")
    try:
        target._now = lambda: CREATED
        target.save_profile("妈妈", {})
        if not hasattr(target._conn, "setlimit"):
            pytest.skip("sqlite3.Connection.setlimit 需要 Python 3.11+")
        target._conn.setlimit(
            sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 500
        )
        apps = [
            {"id": i, "profile": "妈妈", "title": "复诊%d" % i,
             "scheduled_at": "2026-12-%02dT09:00" % (i % 28 + 1),
             "status": "upcoming", "owner_visit_id": 0, "note": "",
             "created_at": CREATED, "updated_at": CREATED}
            for i in range(1, 601)
        ]
        result = target.import_data({
            "source_id": "d" * 32,
            "appointments": apps,
        })
        assert result.get("appointments") == 600
    finally:
        if hasattr(target._conn, "setlimit"):
            target._conn.setlimit(
                sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 32766
            )
        target.close()


# ---------------------------------------------------------------- [9]
def test_adopt_respects_tombstone(tmp_path):
    # 保留同日自动预约时不接管墓碑预约的提醒（第一百二十四轮 [9]）
    st = _store(tmp_path, "s9")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = _day(20)
        d2 = _day(25)
        st.import_data({
            "source_id": "a" * 32,
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": _day(-30),
                 "diagnosis": "高血压", "next_followup_date": d1,
                 "followup_appointment_id": 0, "followup_reminder_id": 40,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": d2 + "T09:00", "status": "upcoming",
                 "owner_visit_id": 10,
                 "note": "由就诊记录 #10 自动创建",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": _day(19), "note": "复诊 " + d1,
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        vid = int(st.list_visit_records("妈妈")[0]["id"])
        appt = int(st.list_appointments("妈妈", None, False, 100)[0]["id"])
        rem = int(st.list_reminders(False, "妈妈")[0]["id"])
        with st._lock, st._conn:
            st._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = 0"
                " WHERE id = ?",
                (vid,),
            )
            st._conn.execute(
                "UPDATE visit_records SET followup_reminder_id = ?"
                " WHERE id = ?",
                (rem, vid),
            )
        st._now = lambda: "2026-11-27T10:00:00"
        extra = int(st.add_reminder(
            "复诊", d2, note="复诊 " + d2, profile="妈妈",
            appointment_id=appt,
        )["id"])
        st.delete_reminder(extra)
        st.update_visit_record_partial(vid, next_followup_date=d2)
        visit = st.get_visit_record(vid)
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        assert int(visit["followup_appointment_id"] or 0) == appt
        assert int(visit["followup_reminder_id"] or 0) == 0
        assert pending == []
    finally:
        st.close()


# ---------------------------------------------------------------- [10][15][16]
def _client(tmp_path, tag, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    st = _store(tmp_path, tag)
    monkeypatch.setattr(memory, "get_session_store", lambda: st)
    return TestClient(api_server.app), st


def test_today_new_med_windows(tmp_path, monkeypatch):
    # 当天新开药在周报/健康提示/档案导出中都不算 0% 漏服
    # （第一百二十四轮 [10][16]）
    client, st = _client(tmp_path, "s10", monkeypatch)
    try:
        st.save_profile("妈妈", {})
        today = datetime.now().strftime("%Y-%m-%d")
        st.add_medication(
            "妈妈", "氨氯地平", dose="5mg", frequency="每日一次",
            started_at=today,
        )
        weekly = client.get(
            "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
        ).json()
        adherence = weekly["medications"].get("adherence")
        rows = [
            r for r in weekly["medications"].get("by_medication") or []
            if r.get("name") == "氨氯地平"
        ]
        assert adherence is None or int(
            adherence.get("expected") or 0
        ) == 0
        assert not rows or rows[0].get("expected") is None
        insights = client.get(
            "/api/health/insights", params={"profile": "妈妈"}
        ).json()["insights"]
        assert [i for i in insights if i.get("key") == "adherence"] == []
        dossier = client.get(
            "/api/health/dossier.md",
            params={"profile": "妈妈", "days": 30},
        ).text
        assert "依从率 0%" not in dossier
    finally:
        st.close()


def test_high_frequency_today_med_exempt(tmp_path, monkeypatch):
    # 每3小时一次的当天新药同样豁免（第一百二十四轮 [16]）
    client, st = _client(tmp_path, "s16", monkeypatch)
    try:
        st.save_profile("爸爸", {})
        today = datetime.now().strftime("%Y-%m-%d")
        st.add_medication(
            "爸爸", "氯化钾", dose="1片", frequency="每3小时一次",
            started_at=today,
        )
        insights = client.get(
            "/api/health/insights", params={"profile": "爸爸", "days": 30}
        ).json()["insights"]
        overview = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        dad = next(
            m for m in overview["profiles"] if m["name"] == "爸爸"
        )
        assert [i for i in insights if i.get("key") == "adherence"] == []
        assert not dad["medications"].get("adherence")
    finally:
        st.close()


def test_resumed_med_consistent(tmp_path, monkeypatch):
    # 当天重新启用的药在各界面口径一致（第一百二十四轮 [15]）
    client, st = _client(tmp_path, "s15", monkeypatch)
    try:
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
        insights = client.get(
            "/api/health/insights", params={"profile": "爷爷"}
        ).json()["insights"]
        overview = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        grandpa = next(
            m for m in overview["profiles"] if m["name"] == "爷爷"
        )
        insight_adherence = [
            i for i in insights if i.get("key") == "adherence"
        ]
        family_attention = grandpa.get("attention") or {}
        family_warn = "用药打卡" in str(
            family_attention.get("label") or ""
        )
        assert bool(insight_adherence) == family_warn, (
            insight_adherence, family_attention
        )
    finally:
        st.close()


# ---------------------------------------------------------------- [11][17]
def test_web_launch_profile_snapshot():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function send(")
    block = source[start:start + 4200]
    assert block.find("const launchDraftProfile") < block.find(
        "await Promise.race(["
    )
    assert "pendingComposerProfile" in block
    assert "pendingComposerProfile = null;" in block


def test_web_guard_no_overwrite():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert (
        "if (!els.input.value.trim()) {\n"
        "        els.input.value = question;" in source
    )


# ---------------------------------------------------------------- [12]
def test_risk_sex_risk_all_levels():
    assert api_server.parse_risk_level(
        "低危性行为一般不需要阻断。"
    ) == ""
    assert api_server.parse_risk_level("中危性行为需要评估。") == ""
    assert api_server.parse_risk_level("目前情况低危，注意休息。") == "low"


def test_web_obj_suffix_sex():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "|性行为|性接触|性伴侣|性活动" in source


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize("text", ["孕8周，今天有抽血，下午拿报告", "孕8周，有验血"])
def test_colloquial_blood_test_not_bleeding(text):
    assert _red(text)["level"] == "none", _red(text)


# ---------------------------------------------------------------- [14]
def test_apply_links_respects_tombstone(tmp_path):
    # 导入 apply_links 不复活墓碑预约的提醒（第一百二十四轮 [14]）
    st = _store(tmp_path, "s14")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = _day(20)
        vid = int(st.add_visit_record(
            "妈妈", visit_date=_day(-10), diagnosis="高血压",
            next_followup_date=d1,
        )["id"])
        api_server._schedule_followup_sync(st, vid)
        backup = st.export_data()
        visit = st.get_visit_record(vid)
        appt = int(visit["followup_appointment_id"])
        rem = int(visit["followup_reminder_id"])
        st.delete_reminder(rem)
        st._now = lambda: "2026-11-27T11:00:00"
        st.update_appointment_partial(appt, scheduled_at=_day(-5) + "T09:00")
        st.update_appointment_partial(appt, scheduled_at=d1 + "T09:00")
        st.import_data(backup)
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        assert pending == []
        assert st.get_app_state(f"reminder_deleted_{appt}") != ""
    finally:
        st.close()
