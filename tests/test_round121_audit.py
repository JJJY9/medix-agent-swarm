# -*- coding: utf-8 -*-
"""第一百二十一轮审查修复的回归测试。"""
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


def _labels(text, context=None):
    return _red(text, context)["matches"]


def _level(text, context=None):
    return _red(text, context)["level"]


def _titles(result):
    return [a.get("title") for a in result.get("alerts") or []]


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "history,text,label",
    [
        ("停药3周后发现怀孕了", "阴道出血", "孕产期出血"),
        (
            "停用华法林3周后改成利伐沙班了",
            "今天头撞到门框",
            "抗凝治疗期间头部外伤",
        ),
        (
            "停用地塞米松3周后改成泼尼松",
            "发烧39度",
            "免疫功能低下伴发热",
        ),
    ],
)
def test_long_stop_tail_keeps_current_drug(history, text, label):
    # 远期停药分句只剥离被停的药物，同句的现孕/换药保留
    # （第一百二十一轮 [1]）
    assert label in _labels(text, {"history": history}), _red(
        text, {"history": history}
    )


def test_long_stop_tail_control():
    assert (
        _level(
            "今天头撞到门框",
            {"age": "40岁", "medications": "华法林（停药12周）"},
        )
        == "none"
    )


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "咯血约50毫升见于什么病",
        "咯血了一口见于什么病",
        "抽搐了一会儿见于什么病",
        "咯血了一口的原因有哪些",
    ],
)
def test_definition_lead_quantity_symptom(text):
    # 「见于什么病/的原因有哪些」定义的量词/短时主诉仍算急症
    # （第一百二十一轮 [3]）
    assert _level(text) == "emergency", _red(text)


def test_definition_lead_quantity_control():
    assert _level("咯血见于什么病") == "none"


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "不确定有没有怀孕，牙龈出血",
        "不确定怀孕，大便出血",
        "不知道有没有怀孕，痔疮出血",
        "不确定有没有怀孕，鼻子出血",
    ],
)
def test_uncertain_pregnancy_non_pregnancy_site(text):
    # 不确定怀孕 + 牙龈/大便/痔疮/鼻出血不是孕产期出血
    # （第一百二十一轮 [4]）
    assert _level(text) == "none", _red(text)


def test_uncertain_pregnancy_control():
    result = _red("不确定有没有怀孕，阴道出血")
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "meds",
    ["华法林（停药十二周）", "华法林（停药三周半）"],
)
def test_long_stop_weeks_forms(meds):
    # 十二周/三周半同样超过 3 周远期停药窗口（第一百二十一轮 [5]）
    assert (
        _level("今天头撞到门框", {"age": "40岁", "medications": meds})
        == "none"
    ), _red("今天头撞到门框", {"age": "40岁", "medications": meds})


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，两周半前停了",
        "华法林 3mg 每日一次，十二周前停了",
        "华法林 3mg 每日一次，12周前停了",
        "华法林 3mg 每日一次，30天前停的",
    ],
)
def test_recent_stop_window_upper_bound(text):
    # 超过 2 周/14 天的停药不再按近期停药报警
    # （第一百二十一轮 [6]）
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": text}
    )
    assert "华法林与非甾体抗炎药" not in _titles(result), result


@pytest.mark.parametrize(
    "text",
    [
        "华法林 3mg 每日一次，两周前停了",
        "华法林 3mg 每日一次，10天前停了",
        "华法林 3mg 每日一次，一周半前停了",
    ],
)
def test_recent_stop_window_control(text):
    result = check_medication_safety(
        "牙疼，能吃布洛芬吗", {"medications": text}
    )
    assert "华法林与非甾体抗炎药" in _titles(result), result


# ---------------------------------------------------------------- [2]
def test_three_source_reminder_restore(tmp_path):
    # 三份备份删除后重导要补回被删行（第一百二十一轮 [2]）
    s0 = _store(tmp_path, "s0")
    s2 = _store(tmp_path, "s2")
    s1 = _store(tmp_path, "s1")
    target = _store(tmp_path, "t2")
    try:
        for st in (s0, s2, s1, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        s0.add_reminder("A", "2026-06-08", note="", profile="妈妈")
        b0 = s0.export_data()
        s2.add_reminder(
            "B", "2026-06-09", note="n1", profile="妈妈",
            advance_days=1, repeat_days=7,
        )
        row_s2 = s2.list_reminders(False, "妈妈")[0]
        s2.finish_reminder(int(row_s2["id"]))
        b2 = s2.export_data()
        s1.add_reminder(
            "B", "2026-06-09", note="", profile="妈妈", advance_days=1
        )
        s1.add_reminder("B", "2026-06-09", note="n1", profile="妈妈")
        row_b1 = next(
            r for r in s1.list_reminders(False, "妈妈")
            if r["note"] == "n1"
        )
        s1.finish_reminder(int(row_b1["id"]))
        b1 = s1.export_data()
        target.import_data(b0)
        target.import_data(b2)
        target.import_data(b1)
        victim = next(
            r for r in target.list_reminders(True, "妈妈")
            if r["note"] == "n1" and int(r["repeat_days"] or 0) == 7
        )
        target.delete_reminder(int(victim["id"]))
        added = target.import_data(b2).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        restored = [
            r for r in rows
            if r["note"] == "n1" and int(r["repeat_days"] or 0) == 7
        ]
        assert added >= 1
        assert len(restored) == 1, [
            (r["note"], r["advance_days"], r["repeat_days"]) for r in rows
        ]
    finally:
        for st in (s0, s2, s1, target):
            st.close()


# ---------------------------------------------------------------- [7]
def test_edited_reimport_keeps_no_old_copy(tmp_path):
    # 本地编辑后重导不插入编辑前的旧副本（第一百二十一轮 [7]）
    src_a = _store(tmp_path, "s7a")
    src_b = _store(tmp_path, "s7b")
    target = _store(tmp_path, "s7t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_reminder(
            "吃药", "2026-06-08", note="n1", profile="妈妈",
            advance_days=1,
        )
        backup_a = src_a.export_data()
        src_b.add_reminder(
            "吃药", "2026-06-08", note="", profile="妈妈",
            repeat_days=7,
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        row_b = next(
            r for r in target.list_reminders(True, "妈妈")
            if not r["note"] and int(r["repeat_days"] or 0) == 7
        )
        target._now = lambda: "2026-06-02T09:00:00"
        target.update_reminder(int(row_b["id"]), note="x")
        added = target.import_data(backup_b).get("reminders")
        rows = target.list_reminders(True, "妈妈")
        assert added == 0, [
            (r["note"], r["advance_days"], r["repeat_days"]) for r in rows
        ]
        assert not any(
            not r["note"] and int(r["repeat_days"] or 0) == 7
            for r in rows
        )
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [8]
def test_deleted_med_restore_cross_source(tmp_path):
    # 另一来源的可编辑列差异不能顶替被删药并吞掉重导
    # （第一百二十一轮 [8]）
    src_a = _store(tmp_path, "s8a")
    src_b = _store(tmp_path, "s8b")
    target = _store(tmp_path, "s8t")
    try:
        for st in (src_a, src_b, target):
            st._now = lambda: CREATED
            st.save_profile("妈妈", {})
        src_a.add_medication(
            "妈妈", "二甲双胍", dose="10mg", frequency="每日两次",
            note="n1",
        )
        src_a.add_medication(
            "妈妈", "二甲双胍", dose="5mg", frequency="每日一次",
            note="n1",
        )
        src_a.add_medication(
            "妈妈", "氨氯地平", dose="5mg", frequency="每日两次"
        )
        backup_a = src_a.export_data()
        src_b.add_medication(
            "妈妈", "二甲双胍", dose="5mg", frequency="每日两次"
        )
        src_b.add_medication(
            "妈妈", "华法林", dose="10mg", frequency="每日两次"
        )
        backup_b = src_b.export_data()
        target.import_data(backup_a)
        target.import_data(backup_b)
        rows = target.list_medications("妈妈", None)
        ten = next(
            r for r in rows
            if r["name"] == "二甲双胍" and str(r["dose"]) == "10mg"
        )
        five_day = next(
            r for r in rows
            if r["name"] == "二甲双胍"
            and str(r["dose"]) == "5mg"
            and r["frequency"] == "每日一次"
        )
        target._now = lambda: "2026-06-02T09:00:00"
        target.update_medication(int(ten["id"]), note="x")
        target.update_medication(
            int(five_day["id"]), active=False,
            stopped_at="2026-06-02", stop_reason="医嘱",
        )
        victim = next(
            r for r in rows
            if r["name"] == "二甲双胍"
            and str(r["dose"]) == "5mg"
            and r["frequency"] == "每日两次"
        )
        target.delete_medication(int(victim["id"]))
        added = target.import_data(backup_b).get("medications")
        rows = target.list_medications("妈妈", None)
        restored = [
            r for r in rows
            if r["name"] == "二甲双胍"
            and str(r["dose"]) == "5mg"
            and r["frequency"] == "每日两次"
        ]
        assert added >= 1
        assert len(restored) == 1, [
            (r["name"], r["dose"], r["frequency"], r["active"])
            for r in rows
        ]
    finally:
        for st in (src_a, src_b, target):
            st.close()


# ---------------------------------------------------------------- [9]
def test_shared_visit_drop_keeps_plan(tmp_path):
    # 删除共享复诊预约的就诊只解除本就诊链接（第一百二十一轮 [9]）
    store = _store(tmp_path, "s9")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        v1 = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-12-01",
        )["id"])
        v2 = int(store.add_visit_record(
            "妈妈", visit_date="2026-11-02", diagnosis="糖尿病",
            next_followup_date="2026-12-01",
        )["id"])
        appt = int(store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-01T09:00",
            note=f"由就诊记录 #{v1} 自动创建", owner_visit_id=v1,
        )["id"])
        rem = int(store.add_reminder(
            "复诊：高血压", "2026-11-30", note="复诊", profile="妈妈",
            appointment_id=appt,
        )["id"])
        store.set_visit_followup_links(v1, appt, rem)
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?,"
                " followup_reminder_id = ? WHERE id = ?",
                (appt, rem, v2),
            )
        store.delete_visit_record(v1)
        assert store.get_appointment(appt) is not None
        assert store.get_reminder(rem) is not None
        assert int(
            store.get_visit_record(v2)["followup_appointment_id"] or 0
        ) == appt
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_stale_detached_reminder_tombstone_date(tmp_path):
    # 旧计划日期的遗留提醒删除不写当前预约墓碑（第一百二十一轮 [10]）
    store = _store(tmp_path, "s10")
    try:
        store._now = lambda: CREATED
        store.save_profile("妈妈", {})
        forged = {
            "visit_records": [
                {"id": 10, "profile": "妈妈", "visit_date": "2026-11-01",
                 "diagnosis": "高血压", "next_followup_date": "2026-12-20",
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 40},
            ],
            "appointments": [
                {"id": 30, "profile": "妈妈", "title": "复诊：高血压",
                 "scheduled_at": "2026-12-20T09:00", "status": "upcoming",
                 "owner_visit_id": 10, "note": "",
                 "created_at": CREATED, "updated_at": CREATED},
            ],
            "reminders": [
                {"id": 40, "profile": "妈妈", "title": "复诊：高血压",
                 "due_at": "2026-11-30", "note": "复诊 2026-11-30",
                 "appointment_id": 0, "done": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        store.import_data(forged)
        vid = int(store.list_visit_records("妈妈")[0]["id"])
        rem = int(store.list_reminders(False, "妈妈")[0]["id"])
        store.delete_reminder(rem)
        api_server._schedule_followup_sync(store, vid)
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "低危 HPV 感染通常可自行消退，建议一年后复查",
        "高危 HPV 持续感染需要复查。",
    ],
)
def test_risk_hpv_text_not_graded(text):
    # HPV 科普里的低危/高危不是风险分级（第一百二十一轮 [11]）
    assert api_server.parse_risk_level(text) == ""


def test_risk_hpv_tag_still_graded():
    assert api_server._risk_tag_level("风险等级：高危") == "high"


# ---------------------------------------------------------------- [12]
def test_today_new_med_no_adherence_fallback(tmp_path, monkeypatch):
    # 当天新开药不触发合并依从兜底（第一百二十一轮 [12]）
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path, "s12")
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        client = TestClient(api_server.app)
        store.save_profile("妈妈", {})
        store.add_medication(
            "妈妈", "氨氯地平", dose="5mg", frequency="每日一次",
            started_at=datetime.now().strftime("%Y-%m-%d"),
        )
        insights = client.get(
            "/api/health/insights", params={"days": 30, "profile": "妈妈"}
        ).json()["insights"]
        assert [i for i in insights if i.get("key") == "adherence"] == []
    finally:
        store.close()


# ---------------------------------------------------------------- [13][14]
def test_web_deferred_draft_owner_slot():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("function restoreDeferredCrossOwnerDraft()")
    block = source[start:start + 1200]
    assert "medix.deferredDraft." in block
    assert "safeSessionSet(DRAFT_KEY, deferred.text);" not in block


def test_web_launch_new_session_not_switch():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("async function send(")
    block = source[start:start + 1600]
    assert "launchedNewSession" in block
    assert "!launchedNewSession" in block


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "孕8周 不确定要不要抽血 阴道没有出血",
        "孕8周 不确定要不要验血 阴道没有出血",
        "孕8周 不确定有没有尿血 阴道没有出血",
    ],
)
def test_blood_test_words_not_bleeding(text):
    # 抽血/验血/尿血不是出血主诉（第一百二十一轮 [15]）
    assert _level(text) == "none", _red(text)


# ---------------------------------------------------------------- [16]
def test_context_names_slash_dose_stop():
    # 「3mg/日」斜杠剂量片段不是药名 （第一百二十一轮 [16]）
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg/日（已停用两年）"}
    )
    assert not any("华法林" in name for name in names), names


def test_context_names_slash_dose_control():
    names = api_server._context_medication_names(
        {"medications": "华法林 3mg/日，阿司匹林 100mg 每日一次"}
    )
    assert any("华法林" in name for name in names), names


# ---------------------------------------------------------------- [17]
def test_web_detect_risk_aligned():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    assert "const compact = normalized.replace" in source
    assert "风险等级|风险程度|风险评级|综合风险|整体风险" in source
    assert "objSuffix" in source


# ---------------------------------------------------------------- [18]
def test_family_attention_old_worst_rate_warning():
    # 老药低打卡率不被当天新开药稀释成 info（第一百二十一轮 [18]）
    attention = api_server._family_attention(
        critical={}, consultation_risk="", labs={}, reminders={},
        care={}, symptoms={},
        medications={
            "worst_rate": 29,
            "worst_started_at": "2026-06-10T08:00:00",
            "oldest_started_at": "2026-06-10T08:00:00",
            "adherence": {"expected": 8, "rate": 50},
        },
        medication_lab={}, vaccinations={}, appointments={},
    )
    assert attention.get("level") == "warning", attention
