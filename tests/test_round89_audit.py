# -*- coding: utf-8 -*-
"""第八十九轮审查修复的回归测试。"""
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
OTHER = "2026-05-01T09:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    return TestClient(api_server.app), store


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text,context,label",
    [
        ("2个月大，主要表现是发烧39度", {"age": "2个月"}, "3 个月以下婴儿发热"),
        ("症状包括发烧38.5度", {"age": "2个月"}, "3 个月以下婴儿发热"),
        ("表现为发热38.9度", {"age": "2个月"}, "3 个月以下婴儿发热"),
        ("正在化疗，主要表现是发烧39度", {"history": "正在化疗"},
         "免疫功能低下伴发热"),
    ],
)
def test_educational_marker_fever_kept(text, context, label):
    assert label in _red(text, context)["matches"], text


# ---------------------------------------------------------------- [3]
def test_fever_howto_with_article_words_kept():
    result = _red("宝宝发烧怎么处理看了文章", {"age": "2个月"})
    assert "3 个月以下婴儿发热" in result["matches"], result
    result = _red("化疗病人发烧怎么处理看了文章", {"history": "正在化疗"})
    assert "免疫功能低下伴发热" in result["matches"], result


def test_fever_topic_article_still_filtered():
    assert _red("看了一篇宝宝发烧怎么办的文章", {"age": "2个月"})[
        "level"
    ] == "none"
    assert _red("宝宝发烧的护理文章", {"age": "1个月"})["level"] == "none"


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林，现在减到每天2.5mg，今天头撞到门框",
        "我以前吃华法林，现在改为每天5mg，今天头撞到门框",
        "我以前吃华法林，现在加到每天一片，今天头撞到门框",
    ],
)
def test_dose_change_with_daily_kept(text):
    result = _red(text, {"age": "40岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_dose_change_with_daily_medication_safety():
    result = check_medication_safety(
        "我以前吃华法林，现在改为每天一片，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [5]
def test_english_still_taking_same_drug_is_continuation():
    for tail in (
        "but I am still taking warfarin.",
        "but I am still on warfarin.",
    ):
        result = _red(
            "今天头撞到门框。warfarin was stopped 2 years ago " + tail,
            {"age": "40岁"},
        )
        assert "抗凝治疗期间头部外伤" in result["matches"], (tail, result)


def test_english_still_take_other_drug_not_continuation():
    result = _red(
        "今天头撞到门框。warfarin was stopped 2 years ago but I still"
        " take amlodipine.",
        {"age": "40岁"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "请问什么病会引起胸痛？",
        "哪些疾病会导致咯血？",
        "咯血是什么病的症状？",
        "呼吸困难见于什么病",
        "胸痛的原因有哪些",
    ],
)
def test_definition_question_not_emergency(text):
    assert _red(text)["level"] == "none", text


def test_definition_with_course_kept():
    assert _red("胸痛两天了是什么病")["level"] == "emergency"


# ---------------------------------------------------------------- [7]
def test_object_before_restart_names_other_drug():
    assert _red(
        "华法林停了两年，阿司匹林还在吃，今天头撞到门框", {"age": "40岁"}
    )["level"] == "none"


def test_object_before_restart_medication_safety():
    for text in (
        "华法林停了两年，阿司匹林还在吃，能吃布洛芬吗",
        "华法林停了两年，还在吃阿司匹林，能吃布洛芬吗",
    ):
        result = check_medication_safety(text, {})
        assert not any(
            "华法林" in (a.get("title") or "")
            for a in result.get("alerts") or []
        ), (text, result)


def test_same_drug_restart_still_warns():
    result = check_medication_safety(
        "华法林停了两年，还在吃华法林，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "讲座上说抽搐要按人中",
        "电视说胸痛要马上就医",
        "广播说宝宝发烧38.5度要马上就医",
        "报纸说咯血要马上去医院",
        "收音机说胸痛要重视",
    ],
)
def test_quote_phrase_sources_filtered(text):
    assert _red(text, {"age": "2个月"})["level"] == "none", text


def test_media_time_context_kept():
    assert _red("看电视时胸痛")["level"] == "emergency"


# ---------------------------------------------------------------- [15]
def test_english_long_stop_vocabulary():
    assert _red(
        "今天头撞到门框。I stopped taking warfarin two years ago",
        {"age": "40岁"},
    )["level"] == "none"
    assert _red(
        "今天头撞到门框。I have been off warfarin for two years",
        {"age": "40岁"},
    )["level"] == "none"
    assert _red(
        "今天头撞到门框。I stopped taking warfarin a week ago",
        {"age": "40岁"},
    )["level"] == "emergency"


# ---------------------------------------------------------------- [2]
def test_same_second_stranger_same_id_import_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        # 目标库已有与备份同秒、撞号（id=1）的无关记录
        store.import_data({"medications": [{
            "id": 1, "profile": "妈妈", "name": "B", "dose": "100mg",
            "frequency": "qd", "note": "",
            "created_at": CREATED, "updated_at": CREATED,
        }]})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "n0",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "n4",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        assert store.import_data(backup)["medications"] == 0
        rows = [(m["name"], m["note"])
                for m in store.list_medications(None, limit=100)]
        assert rows.count(("A", "n0")) == 1, rows
        assert rows.count(("A", "n4")) == 1, rows
    finally:
        store.close()


def test_same_second_stranger_keeps_twin_tail_on_restore(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.import_data({"medications": [{
            "id": 1, "profile": "妈妈", "name": "B", "dose": "100mg",
            "frequency": "qd", "note": "",
            "created_at": CREATED, "updated_at": CREATED,
        }]})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "n0",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "n4",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        n4 = next(
            m for m in store.list_medications(None, limit=100)
            if m["note"] == "n4"
        )
        store.delete_medication(int(n4["id"]))
        store.import_data(backup)
        rows = [(m["name"], m["note"])
                for m in store.list_medications(None, limit=100)]
        assert ("A", "n4") in rows, rows
        assert rows.count(("A", "n0")) == 1, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def _moved_twin_store(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    store.import_data({"medications": [{
        "id": 1, "profile": "爸爸", "name": "B", "dose": "100mg",
        "frequency": "qd", "note": "", "created_at": OTHER,
        "updated_at": OTHER,
    }]})
    backup = {"medications": [
        {"id": 1, "profile": "妈妈", "name": "A", "dose": "5mg",
         "frequency": "qd", "note": "n0",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "name": "A", "dose": "5mg",
         "frequency": "qd", "note": "n0",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 3, "profile": "妈妈", "name": "A", "dose": "5mg",
         "frequency": "qd", "note": "n4",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    mom_n0 = next(
        m for m in rows if m["profile"] == "妈妈" and m["note"] == "n0"
    )
    store.update_medication(int(mom_n0["id"]), profile="爸爸")
    return backup


def test_drifted_moved_twin_does_not_resurrect_copy(tmp_path):
    store = _store(tmp_path)
    try:
        backup = _moved_twin_store(store)
        store.import_data(backup)
        rows = [(m["profile"], m["name"], m["note"])
                for m in store.list_medications(None, limit=100)]
        assert ("妈妈", "A", "n0") not in rows, rows
        assert ("妈妈", "A", "n4") in rows, rows
        assert store.import_data(backup)["medications"] == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_system_merge_does_not_write_tombstone(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        appt_day = _day(6)
        client.post("/api/sessions/import", json={
            "reminders": [{
                "id": 501, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(5), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": appt_day,
                "followup_appointment_id": 0, "followup_reminder_id": 501,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        appt = store.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at=appt_day + "T09:00",
            owner_visit_id=1, note="由就诊记录 #1 自动创建",
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?"
                " WHERE id = 1", (int(appt["id"]),),
            )
        client.post(f"/api/appointments/{int(appt['id'])}/reminder")
        assert client.post("/api/visits/1/schedule-followup").status_code == 200
        assert store.get_app_state(
            f"reminder_deleted_{int(appt['id'])}"
        ) == ""
        client.patch(
            f"/api/appointments/{int(appt['id'])}",
            json={"status": "cancelled"},
        )
        client.patch(
            f"/api/appointments/{int(appt['id'])}",
            json={"status": "upcoming"},
        )
        pending = store.list_reminders(False, "妈妈")
        assert pending, "系统合并后恢复待就诊应重建提醒"
        assert int(store.get_visit_record(1)["followup_reminder_id"] or 0)
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def _linked_backup(date_str):
    return {
        "appointments": [{
            "id": 1, "profile": "妈妈", "title": "复诊：高血压",
            "note": "", "owner_visit_id": 1, "status": "upcoming",
            "scheduled_at": date_str + "T09:00",
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "reminders": [{
            "id": 601, "profile": "妈妈", "title": "复诊：高血压",
            "due_at": _day(5), "appointment_id": 0, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [{
            "id": 1, "profile": "妈妈", "visit_date": _day(-2),
            "diagnosis": "高血压", "next_followup_date": date_str,
            "followup_appointment_id": 0, "followup_reminder_id": 601,
            "created_at": CREATED, "updated_at": CREATED,
        }],
    }


def _link_visit_to_appointment(store):
    appt_id = store.list_appointments(None, None)[0]["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?"
            " WHERE id = 1", (int(appt_id),),
        )
    return int(appt_id)


def test_import_respects_reminder_tombstone(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        backup = _linked_backup(_day(6))
        client.post("/api/sessions/import", json=backup)
        appt_id = _link_visit_to_appointment(store)
        client.patch(
            f"/api/appointments/{appt_id}",
            json={"scheduled_at": _day(12) + "T09:00"},
        )
        pending = store.list_reminders(False, "妈妈")
        assert pending and pending[0]["due_at"].startswith(_day(11))
        client.delete(f"/api/reminders/{int(pending[0]['id'])}")
        client.post("/api/sessions/import", json=backup)
        assert store.list_reminders(False, "妈妈") == []
        assert int(
            store.get_visit_record(1)["followup_reminder_id"] or 0
        ) == 0
    finally:
        store.close()


def test_import_recomputes_due_for_rescheduled_appointment(
    tmp_path, monkeypatch
):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        backup = _linked_backup(_day(6))
        client.post("/api/sessions/import", json=backup)
        appt_id = _link_visit_to_appointment(store)
        client.patch(
            f"/api/appointments/{appt_id}",
            json={"scheduled_at": _day(12) + "T09:00"},
        )
        client.post("/api/sessions/import", json=backup)
        pending = store.list_reminders(False, "妈妈")
        assert pending and pending[0]["due_at"].startswith(_day(11)), pending
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"] or 0) == int(
            pending[0]["id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def _shared_reminder_backup():
    twin_day = _day(6)
    return {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊A", "note": "",
             "status": "upcoming", "scheduled_at": twin_day + "T09:00",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊B", "note": "",
             "status": "upcoming", "scheduled_at": twin_day + "T10:00",
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [{
            "id": 701, "profile": "妈妈", "title": "复诊",
            "due_at": _day(5), "appointment_id": 0, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [
            {"id": 1, "profile": "妈妈", "visit_date": _day(-2),
             "diagnosis": "高血压", "next_followup_date": twin_day,
             "followup_appointment_id": 1, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": _day(-2),
             "diagnosis": "高血压", "next_followup_date": twin_day,
             "followup_appointment_id": 2, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }


def test_cancel_keeps_shared_detached_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_reminder_backup())
        assert len(store.list_reminders(False, "妈妈")) == 1
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert int(
            store.get_visit_record(2)["followup_reminder_id"] or 0
        ) == int(pending[0]["id"])
    finally:
        store.close()


def test_delete_appointment_keeps_shared_detached_reminder(
    tmp_path, monkeypatch
):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_reminder_backup())
        assert client.delete("/api/appointments/1").status_code in (200, 204)
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert int(
            store.get_visit_record(2)["followup_reminder_id"] or 0
        ) != 0
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
def test_family_target_ignores_out_of_window_reading(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {"age": "60"})
        client.put("/api/metric-targets", json={
            "profile": "妈妈", "metric": "glucose", "value1": 7.0,
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 13.0,
            "context": "fasting",
            "measured_at": _day(-100) + "T09:00",
        })
        data = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
        assert (mom.get("attention") or {}).get("label") != "指标未达标 1 项"
        metric = next(
            m for m in mom["metrics"] if m["metric"] == "glucose"
        )
        assert metric["over_target"] is None, metric
        export = client.get(
            "/api/family/overview/export.csv", params={"days": 7}
        )
        text = export.content.decode("utf-8-sig")
        assert "（超标）" not in text and "（低于目标）" not in text
    finally:
        store.close()


def test_family_target_recent_reading_still_flagged(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {"age": "60"})
        client.put("/api/metric-targets", json={
            "profile": "妈妈", "metric": "glucose", "value1": 7.0,
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 13.0,
            "context": "fasting",
            "measured_at": _day(-1) + "T09:00",
        })
        data = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
        assert (mom.get("attention") or {}).get("label") == "指标未达标 1 项"
    finally:
        store.close()


# ---------------------------------------------------------------- [13][16]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_open_session_keeps_pending_new_draft():
    source = _web_source()
    assert "const pendingNewDraft = Boolean(" in source
    # 待新建会话草稿的归属允许「空」或「尚未落盘（null）」
    # （第一百零二轮 [17]）
    assert "pendingDraftOwner === ''" in source
    assert "keepPendingReset || draftConflict || pendingNewDraft" in source
    # 待重置标记不能在结算草稿前被无条件清除
    assert "} else if (!keepPendingReset && !pendingNewDraft) {" in source


def test_reconcile_strips_matched_text():
    source = _web_source()
    assert "const matchedInflight = (" in source
    assert (
        "inflightInputMatchesRestored(restoredForSession)" in source
    )
    assert "stripInflightQuestion(els.input.value, matchedInflight)" in source
