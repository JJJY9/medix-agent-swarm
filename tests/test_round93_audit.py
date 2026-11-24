# -*- coding: utf-8 -*-
"""第九十三轮审查修复的回归测试。"""
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
        (
            "warfarin stopped 2 years ago, still taking it，今天头撞到门框",
            {"age": "55岁"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "warfarin stopped 2 years ago, still on it，今天头撞到门框",
            {"age": "55岁"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "methotrexate stopped 2 years ago, still taking it，今天发烧了",
            {"age": "55岁"},
            "免疫功能低下伴发热",
        ),
    ],
)
def test_cross_clause_still_taking_it(text, context, label):
    assert label in _red(text, context)["matches"], text


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "岳父胸痛是什么病",
        "公公胸痛是什么病",
        "老爷子胸痛是什么病",
        "闺女胸痛是什么病",
        "岳父抽搐是什么病",
        "岳父晕厥是什么病",
        "孕妇胸痛是什么病",
        "产妇呼吸困难是什么病",
        "怀孕了抽搐是什么病",
    ],
)
def test_family_pregnancy_definition_question(text):
    assert _red(text)["level"] == "emergency", text


# ---------------------------------------------------------------- [3][11][15]
def test_context_medication_stop_annotations():
    names = api_server._context_medication_names(
        {"medications": "阿司匹林（已停用）华法林"}
    )
    assert "华法林" in names, names
    names = api_server._context_medication_names(
        {"medications": "华法林 阿司匹林（已停用）"}
    )
    assert names == ["华法林"], names
    assert api_server._context_medication_names(
        {"medications": "华法林，已停药两个月"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林，每日一次，已停用"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林，1片，已停用"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林、二甲双胍"}
    ) == ["华法林", "二甲双胍"]


# ---------------------------------------------------------------- [4]
def test_object_first_restart_keeps_active_drug():
    result = _red(
        "发烧39度。华法林停用两年了但甲氨蝶呤还在服用", {"age": "55岁"}
    )
    assert "免疫功能低下伴发热" in result["matches"], result
    result = _red(
        "今天头撞到门框。化疗停用两年了但华法林还在服用", {"age": "55岁"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    result = _red(
        "发烧39度。华法林停用两年了但甲氨蝶呤仍在吃", {"age": "55岁"}
    )
    assert "免疫功能低下伴发热" in result["matches"], result


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize("text", ["孕8周，阴唇出血", "怀孕了，阴唇出血"])
def test_labial_bleeding_still_pregnancy_emergency(text):
    result = _red(text, {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result


def test_tooth_bleeding_still_filtered():
    assert _red("孕8周，牙齿出血", {"age": "30岁"})["level"] == "none"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    ["我并非没有吃华法林，今天头撞到门框", "我不是没吃华法林，今天头撞到门框"],
)
def test_medication_double_negation(text):
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_medication_double_negation_safety():
    result = check_medication_safety("我不是没吃华法林，能吃布洛芬吗", {})
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text", ["华法林两年前停的，今天头撞到门框", "华法林停三年了，今天头撞到门框"]
)
def test_long_stop_variants(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


@pytest.mark.parametrize(
    "text",
    ["华法林两年前停的，能吃布洛芬吗", "华法林停三年了，能吃阿司匹林吗"],
)
def test_long_stop_variants_medication_safety(text):
    assert check_medication_safety(text, {})["level"] == "none", text


# ---------------------------------------------------------------- [8]
def test_twin_restore_double_import_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "1",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "B", "dose": "1",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(1, profile="爸爸", dose="7")
        store.delete_medication(2)
        assert store.import_data(backup)["medications"] == 1
        second = store.import_data(backup)["medications"]
        rows = [
            (m["profile"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert second == 0, (second, rows)
        assert ("妈妈", "1", "n1") not in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_moved_edit_with_sibling_content_edit(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "1",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "name": "B2", "dose": "2",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "name": "B", "dose": "1",
             "frequency": "qd", "note": "n3",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(1, profile="爸爸", note="n1m")
        store.update_medication(2, frequency="bid")
        store.import_data(backup)
        rows = [
            (m["profile"], m["name"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert ("妈妈", "B", "1", "n1") not in rows, rows
        assert ("爸爸", "B", "1", "n1m") in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_downgrade_cleans_orphan_detached_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        twin_day = _day(6)
        backup = {
            "appointments": [
                {"id": 1, "profile": "妈妈", "title": "复诊A", "note": "",
                 "owner_visit_id": 1, "status": "upcoming",
                 "scheduled_at": twin_day + "T09:00",
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 2, "profile": "妈妈", "title": "复诊B", "note": "",
                 "status": "upcoming",
                 "scheduled_at": twin_day + "T10:00",
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
                 "followup_appointment_id": 1,
                 "followup_reminder_id": 701,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 2, "profile": "妈妈", "visit_date": _day(-2),
                 "diagnosis": "高血压", "next_followup_date": twin_day,
                 "followup_appointment_id": 2,
                 "followup_reminder_id": 0,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        }
        client.post("/api/sessions/import", json=backup)
        client.patch(
            "/api/appointments/1", json={"scheduled_at": _day(-1) + "T09:00"}
        )
        pending = store.list_reminders(False, "妈妈")
        detached = [
            x for x in pending if int(x["appointment_id"] or 0) == 0
        ]
        assert detached == [], pending
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 0, visit
        assert int(visit["followup_reminder_id"] or 0) == 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
def test_still_eating_other_drug_attribution():
    result = _red(
        "今天头撞到门框。华法林停用两年了但我还在吃阿司匹林",
        {"age": "55岁"},
    )
    assert result["level"] == "none", result
    result = _red(
        "发烧39度。化疗停用两年了但我还在吃阿司匹林", {"age": "55岁"}
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "我正在准备考试。咯血见于什么病？",
        "我最近在减肥。胸痛见于什么病？",
        "胸痛见于什么病？最近在准备婚礼",
    ],
)
def test_definition_question_life_context_only(text):
    assert _red(text)["level"] == "none", text


def test_definition_question_symptom_context_kept():
    assert _red("我爸咳嗽三天了，咯血见于什么病")["level"] == "emergency"


# ---------------------------------------------------------------- [12][16][17]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_open_session_inflight_owner_guard():
    source = _web_source()
    assert "if (owner !== targetId) return;" in source


def test_inflight_record_kept_everywhere():
    source = _web_source()
    start = source.index("function restoreInflightQuestion(")
    end = source.index("window.addEventListener('beforeunload'", start)
    block = source[start:end]
    assert block.count("safeSessionRemove(INFLIGHT_QUESTION_KEY);") == 1
    assert "在途记录保留" in source
