# -*- coding: utf-8 -*-
"""第一百二十六轮审查修复的回归测试。"""
import os
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


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    st = _store(tmp_path, "api")
    monkeypatch.setattr(memory, "get_session_store", lambda: st)
    try:
        yield TestClient(api_server.app), st
    finally:
        st.close()


# ---------------------------------------------------------------- [1][3]
@pytest.mark.parametrize(
    "text,label",
    [
        ("胸痛会不会有事", "胸痛"),
        ("胸口疼会死吗", "胸痛"),
        ("孕早期出血怎么治疗", "孕产期出血"),
        ("咯血怎么治疗", "咯血"),
        ("大出血怎么治疗", "大量出血"),
        ("过敏性休克怎么治疗", "过敏性休克"),
        ("自杀念头怎么缓解", "自伤风险"),
        ("有自杀想法怎么治疗", "自伤风险"),
        ("胸口疼怎么缓解都不管用", "胸痛"),
        ("胸痛会不会很严重", "胸痛"),
    ],
)
def test_help_semantics_not_educational(text, label):
    # 同分句求助/治疗失败语义优先于科普过滤（第一百二十六轮 [1][3]）
    r = _red(text)
    assert label in r["matches"], r


@pytest.mark.parametrize(
    "text",
    ["哪些人容易胸痛", "胸痛会遗传吗", "心梗会胸痛吗",
     "感冒会引起胸痛吗", "胸痛怎么预防", "胸痛挂什么科"],
)
def test_educational_controls_still_dropped(text):
    assert _red(text)["level"] == "none", _red(text)


# ---------------------------------------------------------------- [2][4]
@pytest.mark.parametrize(
    "text",
    [
        "剖宫产两个多月，阴道出血",
        "剖宫产两个半月，阴道出血",
        "剖宫产2个来月，阴道出血",
        "剖宫产半年，阴道出血",
        "剖腹产半年，阴道出血",
        "剖宫产满月，阴道出血",
        "剖宫产一个多月了，阴道出血",
        "剖宫产一个半月了，阴道出血",
        "剖宫产俩月了，阴道出血",
    ],
)
def test_caesarean_month_forms(text):
    # 剖宫产月龄/半年/满月写法（第一百二十六轮 [2][4]）
    r = _red(text)
    assert "孕产期出血" in r["matches"], r


@pytest.mark.parametrize(
    "text", ["剖宫产一年半，阴道出血", "剖宫产两年，阴道出血"]
)
def test_caesarean_year_controls(text):
    assert _red(text)["level"] == "none", _red(text)


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text", ["易孕期出血", "备孕期出血", "待孕期出血"]
)
def test_bei_yun_phrase_not_pregnancy(text):
    # 备孕/易孕/待孕期出血不是孕产期出血（第一百二十六轮 [5]）
    assert _red(text)["level"] == "none", _red(text)


def test_pregnancy_phrase_control():
    assert "孕产期出血" in _red("孕期出血")["matches"]


# ---------------------------------------------------------------- [6]
def test_metric_gap_backup_idempotent_second_guard(tmp_path):
    # 身份+内容完全一致的同主键读数重复恢复不补插（第一百二十六轮 [6]）
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
        assert target.import_data(backup).get("metrics") == 2
        assert target.import_data(backup).get("metrics") == 0
    finally:
        src.close()
        target.close()


# ---------------------------------------------------------------- [7]
def test_tombstone_keeps_user_appointment_reminder(tmp_path):
    # 墓碑分支不连带删除用户自建的预约提醒（第一百二十六轮 [7]）
    st = _store(tmp_path, "s7")
    try:
        st._now = lambda: CREATED
        st.save_profile("妈妈", {})
        d1 = (datetime.now() + timedelta(days=15)).strftime("%Y-%m-%d")
        backup = {
            "source_id": "a" * 32,
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-01",
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
                 "due_at": "2026-11-30", "note": "复诊 " + d1,
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 41, "profile": "妈妈", "title": "就诊：复诊：高血压",
                 "due_at": "2026-11-30", "note": "",
                 "appointment_id": 30, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        st.import_data(backup)
        appt = int(st.list_appointments("妈妈", None, False, 100)[0]["id"])
        rem = next(
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == 0
        )
        st._now = lambda: "2026-11-27T11:00:00"
        st.delete_reminder(int(rem["id"]))
        st.import_data(backup)
        pending = [
            r for r in st.list_reminders(False, "妈妈")
            if int(r["appointment_id"] or 0) == appt
        ]
        assert any("就诊" in str(r["title"]) for r in pending), pending
    finally:
        st.close()


# ---------------------------------------------------------------- [8]
def test_web_launch_draft_snapshot_used():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function send(")
    block = source[start:start + 4800]
    launch_pos = block.find("const launchDraftProfile")
    assert launch_pos >= 0
    assert "submittedProfileSnapshot" in block[launch_pos:launch_pos + 200]
    assert "profile: submittedProfileSnapshot" in block


# ---------------------------------------------------------------- [9][10]
def test_web_mixed_sentinel_memory_and_detached():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "draftProfileMemory = '__mixed_draft__';" in source
    assert "混合草稿的哨兵不能在脱离清理时丢失" in source
    assert "draftProfileSnapshot() || safeSessionGet(DRAFT_PROFILE_KEY)" in source


# ---------------------------------------------------------------- [11]
def test_restart_today_forms():
    # 今天/今日 + 开始吃/吃上了是续用（第一百二十六轮 [11]）
    r = _red(
        "华法林停了两年了，今天开始吃了，今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in r["matches"], r
    result = check_medication_safety(
        "华法林停了两年了，今天开始吃了，今天吃了布洛芬", {}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


def test_restart_today_object_control():
    # 今天吃火锅/海鲜仍是进食，不复活停药（第一百二十六轮 [11]）
    for text in [
        "华法林停了两年了，今天吃了火锅，今天头撞到门框",
        "甲氨蝶呤停了三年了，今天吃了海鲜，今天发烧39度",
    ]:
        assert _red(text)["level"] == "none", _red(text)


# ---------------------------------------------------------------- [12]
def test_context_recent_stop_synced(client_and_store):
    # 档案「最近停了」与聊天通道一致（第一百二十六轮 [12]）
    client, st = client_and_store
    st.save_profile("妈妈", {"medications": "华法林最近停了，布洛芬"})
    data = client.get("/api/family/overview", params={"days": 7}).json()
    mom = next(m for m in data["profiles"] if m["name"] == "妈妈")
    safety = mom.get("medication_lab_safety") or {}
    assert int(safety.get("count") or 0) >= 1, safety


# ---------------------------------------------------------------- [13]
def test_family_insight_resumed_levels_any_resumed(tmp_path, monkeypatch):
    # 曾恢复、开始日期改为今天：家庭与健康提示级别一致
    # （第一百二十六轮 [13]）
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    st = _store(tmp_path, "s13")
    monkeypatch.setattr(memory, "get_session_store", lambda: st)
    try:
        client = TestClient(api_server.app)
        st.save_profile("爷爷", {})
        old = (datetime.now() - timedelta(days=90)).strftime("%Y-%m-%d")
        med = st.add_medication(
            "爷爷", "二甲双胍", dose="500mg", frequency="每日两次",
            started_at=old,
        )
        st.update_medication(int(med["id"]), active=False)
        st.update_medication(int(med["id"]), active=True)
        with st._lock, st._conn:
            st._conn.execute(
                "UPDATE medications SET resumed_at = ?, started_at = ?"
                " WHERE id = ?",
                (
                    old + "T08:00:00",
                    datetime.now().strftime("%Y-%m-%d"),
                    int(med["id"]),
                ),
            )
        overview = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        grandpa = next(
            m for m in overview["profiles"] if m["name"] == "爷爷"
        )
        insights = client.get(
            "/api/health/insights", params={"profile": "爷爷"}
        ).json()["insights"]
        fam = (grandpa.get("attention") or {}).get("level")
        ins = next(
            i["level"] for i in insights if i.get("key") == "adherence"
        )
        assert fam == ins, (fam, ins)
    finally:
        st.close()


# ---------------------------------------------------------------- [14]
def test_web_empty_session_restores_inflight():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("const messages = data.messages || [];")
    seg = source[start:start + 500]
    assert "restoreInflightForOpenSession(expectedSession, messages);" in seg
