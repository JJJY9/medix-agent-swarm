# -*- coding: utf-8 -*-
"""第九十二轮审查修复的回归测试。"""
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
    "text,label",
    [
        ("我爸咳嗽三天了，咯血见于什么病", "咯血"),
        ("发烧两天，什么病会引起抽搐", "抽搐"),
    ],
)
def test_definition_question_with_patient_context(text, label):
    result = _red(text)
    assert result["level"] == "emergency", result


# ---------------------------------------------------------------- [3]
def test_chinese_still_using_keeps_other_drug():
    result = _red(
        "今天头撞到门框。化疗停用两年了但我还在用华法林", {"age": "55岁"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = _red(
        "发烧39度。华法林停用两年了但我还在用甲氨蝶呤", {"age": "55岁"}
    )
    assert "免疫功能低下伴发热" in result["matches"], result


def test_chinese_still_using_other_drug_not_reviving():
    result = _red(
        "今天头撞到门框。华法林停用两年了但我还在用阿司匹林",
        {"age": "55岁"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛见于什么病？昨天开始的",
        "咯血见于什么病？昨晚开始的",
        "胸痛见于什么病，一阵一阵的",
        "胸痛见于什么病？反复发作",
    ],
)
def test_definition_question_frequency_course(text):
    assert _red(text)["level"] == "emergency", text


def test_definition_question_life_context_filtered():
    assert _red("胸痛见于什么病？这两天在减肥")["level"] == "none"
    assert _red("什么病会引起胸痛？最近在准备考试")["level"] == "none"


# ---------------------------------------------------------------- [5]
def test_english_still_using_continuation():
    result = _red(
        "今天头撞到门框。chemotherapy was stopped 2 years ago but I am"
        " still using warfarin",
        {"age": "55岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = _red(
        "发烧39度。warfarin was stopped 2 years ago but I am still using"
        " methotrexate",
        {"age": "55岁"},
    )
    assert "免疫功能低下伴发热" in result["matches"], result


# ---------------------------------------------------------------- [6]
def test_english_taking_it_again():
    result = _red(
        "warfarin was stopped 2 years ago but I am taking it again。"
        "今天头撞到门框",
        {"age": "55岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "看视频时胸痛是什么病",
        "我昨天看视频时胸痛是什么病",
        "看视频时抽搐是什么病",
    ],
)
def test_definition_suffix_media_time_context(text):
    assert _red(text)["level"] == "emergency", text


def test_definition_suffix_quote_still_filtered():
    assert _red("网上看到胸痛是什么病")["level"] == "none"


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text", ["孕8周，牙齿出血", "怀孕了，嘴唇出血", "孕妇舌出血"]
)
def test_dental_lip_bleeding_not_pregnancy(text):
    assert _red(text)["level"] == "none", text


def test_vaginal_bleeding_still_pregnancy_emergency():
    assert _red("怀孕了，阴道出血")["level"] == "emergency"


# ---------------------------------------------------------------- [12]
def test_english_stop_without_auxiliary():
    assert _red(
        "今天头撞到门框。warfarin discontinued 2 years ago", {"age": "55岁"}
    )["level"] == "none"
    assert _red(
        "发烧39度。chemotherapy discontinued 2 years ago", {"age": "55岁"}
    )["level"] == "none"
    assert _red(
        "今天头撞到门框。warfarin stopped 2 years ago", {"age": "55岁"}
    )["level"] == "none"


# ---------------------------------------------------------------- [14]
def test_context_medication_stop_annotations():
    names = api_server._context_medication_names(
        {"medications": "华法林，已停用"}
    )
    assert names == [], names
    names = api_server._context_medication_names(
        {"medications": "阿司匹林（已停用）、二甲双胍"}
    )
    assert names == ["二甲双胍"], names
    names = api_server._context_medication_names(
        {"medications": "已停用的华法林"}
    )
    assert names == [], names
    names = api_server._context_medication_names(
        {"medications": "华法林、二甲双胍"}
    )
    assert names == ["华法林", "二甲双胍"], names


# ---------------------------------------------------------------- [2]
def test_same_origin_moved_edited_twin_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "name": "C", "dose": "5mg",
             "frequency": "qd", "note": "n5",
             "created_at": CREATED, "updated_at": CREATED},
        ], "medication_logs": [
            {"id": 1, "medication_id": 1, "taken_at": _day(-1) + "T08:00"},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        dad_c = next(m for m in rows if m["name"] == "C")
        store.update_medication(int(dad_c["id"]), profile="妈妈", dose="9mg")
        store.delete_medication(1)
        store.import_data(backup)
        rows = [
            (m["id"], m["profile"], m["name"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert any(r[1:] == ("妈妈", "B", "n1") for r in rows), rows
        assert len([r for r in rows if r[2] == "C"]) == 1, rows
        restored_id = next(
            r[0] for r in rows if r[1:] == ("妈妈", "B", "n1")
        )
        logs = store.list_medication_logs(None, None, 100)
        assert logs and int(logs[0]["medication_id"] or 0) == int(
            restored_id
        ), (restored_id, logs)
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_same_name_twin_second_restore_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n3",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(1, frequency="bid")
        store.delete_medication(2)
        store.update_medication(3, profile="爸爸")
        store.import_data(backup)
        third = store.import_data(backup)["medications"]
        rows = [
            (m["profile"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert third == 0, (third, rows)
        assert len([r for r in rows if r[1] == "n1"]) == 1, rows
    finally:
        store.close()


# ---------------------------------------------------------------- 共享提醒备份
def _shared_backup(v1_fa, v2_fa):
    twin_day = _day(6)
    return {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊A", "note": "",
             "owner_visit_id": 1, "status": "upcoming",
             "scheduled_at": twin_day + "T09:00",
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
             "followup_appointment_id": v1_fa, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": _day(-2),
             "diagnosis": "高血压", "next_followup_date": twin_day,
             "followup_appointment_id": v2_fa, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }


# ---------------------------------------------------------------- [10]
def test_reschedule_does_not_touch_shared_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(1, 2))
        before = store.list_reminders(False, "妈妈")
        client.patch(
            "/api/appointments/1", json={"scheduled_at": _day(12) + "T09:00"}
        )
        after = store.list_reminders(False, "妈妈")
        assert before[0]["due_at"] == after[0]["due_at"], (before, after)
        assert before[0]["note"] == after[0]["note"], (before, after)
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
def test_adopt_writes_back_appointment_link(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(0, 2))
        client.post("/api/visits/2/schedule-followup")
        client.patch(
            "/api/appointments/1", json={"scheduled_at": _day(12) + "T09:00"}
        )
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 1, visit
        assert int(visit["followup_reminder_id"] or 0) != 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [11][15]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_failure_snapshot_accepts_merged_composer():
    source = _web_source()
    assert (
        'els.input.value.startsWith(questionText + "\\n\\n")' in source
        or "els.input.value.startsWith(questionText + '\n\n')" in source
    )
    assert source.count("startsWith(questionText + ") == 3


def test_inflight_record_kept_until_reconcile():
    source = _web_source()
    start = source.index("function restoreInflightQuestion(")
    end = source.index("window.addEventListener('beforeunload'", start)
    block = source[start:end]
    # 只保留「过期记录」与「主诉已在草稿里」两处清理：合并/恢复
    # 分支必须保留记录到对账确认（第九十二轮 [15]）
    assert block.count("safeSessionRemove(INFLIGHT_QUESTION_KEY);") == 2
    assert "对账确认落库或发送成功" in block
