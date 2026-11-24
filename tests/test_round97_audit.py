# -*- coding: utf-8 -*-
"""第九十七轮审查修复的回归测试。"""
from datetime import date, timedelta
from pathlib import Path

import pytest

import api_server
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-01T09:00:00"


def _store(tmp_path, tag="sessions"):
    return SessionStore(str(Path(tmp_path) / f"{tag}.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (date.today() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- [1][7]
@pytest.mark.parametrize(
    "text,label",
    [
        ("华法林停用两年了但利伐沙班今天没吃，今天头撞到门框",
         "抗凝治疗期间头部外伤"),
        ("二甲双胍停用两年了但今天没吃华法林，今天头撞到门框",
         "抗凝治疗期间头部外伤"),
        ("甲氨蝶呤停用两年了但环孢素今天没吃，今天发烧39度",
         "免疫功能低下伴发热"),
        ("甲氨蝶呤停用两年了但今天漏服了华法林，今天头撞到门框",
         "抗凝治疗期间头部外伤"),
        ("甲氨蝶呤停用两年了但华法林今天没吃，今天头撞到门框",
         "抗凝治疗期间头部外伤"),
    ],
)
def test_time_first_missed_dose_high_risk_kept(text, label):
    result = _red(text, {"age": "55岁"})
    assert label in result["matches"], (text, result)


def test_time_first_low_risk_missed_dose_not_revive():
    assert _red(
        "二甲双胍今天没吃但华法林停用两年了，今天头撞到门框",
        {"age": "55岁"},
    )["level"] == "none"


@pytest.mark.parametrize(
    "text,drug",
    [
        ("华法林停用两年了但利伐沙班今天没吃，今天头撞到门框", "利伐沙班"),
        ("甲氨蝶呤停用两年了但今天没吃华法林，今天头撞到门框", "华法林"),
        ("甲氨蝶呤停用两年了但环孢素今天没吃，今天发烧39度", "环孢素"),
    ],
)
def test_time_first_missed_dose_active_term(text, drug):
    from core.triage import _active_medical_text
    active = _active_medical_text({"history": text})
    assert drug in active, active
    # 远期停药的他药不能随片段复活
    assert "甲氨蝶呤" not in active, active


def test_missed_dose_medication_safety_attribution():
    result = check_medication_safety(
        "华法林停用两年了但利伐沙班今天没吃，能吃布洛芬吗", {}
    )
    titles = [a.get("title", "") for a in result.get("alerts", [])]
    assert "华法林与非甾体抗炎药" not in titles, result
    result = check_medication_safety(
        "华法林今天没吃，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text,label",
    [
        ("我在网上查了胸痛怎么办", "胸痛"),
        ("网上查了一下胸痛是怎么回事", "胸痛"),
        ("网上查了呼吸困难怎么办", "呼吸困难"),
        ("网上查了咯血怎么办", "咯血"),
        ("搜索胸痛怎么办", "胸痛"),
    ],
)
def test_red_flag_after_search_words_kept(text, label):
    assert label in _red(text)["matches"], text


def test_search_words_pregnancy_and_trauma():
    result = _red("怀孕8周，网上查了阴道出血怎么办", {"age": "30岁"})
    assert "孕产期出血" in result["matches"], result
    result = _red(
        "我吃华法林，网上查了头撞到门框怎么办", {"age": "55岁"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


@pytest.mark.parametrize(
    "text",
    [
        "我在网上看到胸痛要重视",
        "网上说胸痛持续十分钟要就医",
        "我在网上看到说胸痛很严重",
    ],
)
def test_pure_education_reference_still_filtered(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "I finished chemotherapy yesterday. My temperature is 39.2°C.",
        "I completed chemotherapy last week, my temperature is 38.9°C.",
        "I am done with chemo last month. My temperature is 38.5°C.",
    ],
)
def test_english_recent_chemo_end_keeps_immunosuppression(text):
    result = _red(text)
    assert "免疫功能低下伴发热" in result["matches"], (text, result)


def test_english_past_chemo_end_still_filtered():
    result = _red(
        "I finished chemotherapy two years ago. My temperature is 39.2°C."
    )
    assert result["level"] == "none", result


def test_on_chemo_still_positive():
    result = _red("I am on chemotherapy. My temperature is 39.2°C.")
    assert "免疫功能低下伴发热" in result["matches"], result


# ---------------------------------------------------------------- [4]
def test_noid_single_reminder_tail_edit_idempotent(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "测血压",
             "due_at": "2026-06-02", "note": "", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.complete_reminder(1)
        again = store.import_data(backup)
        rows = store.list_reminders(True, "妈妈")
        assert again.get("reminders", 0) == 0, (again, rows)
        assert len(rows) == 1, rows
        assert int(rows[0]["done"] or 0) == 1, rows
    finally:
        store.close()


def test_noid_single_medication_stop_edit_idempotent(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "阿司匹林", "dose": "100mg",
             "frequency": "qd", "note": "", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(
            1, active=0, stopped_at="2026-10-01", stop_reason="遵医嘱停药"
        )
        again = store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        assert again.get("medications", 0) == 0, (again, rows)
        assert len(rows) == 1, rows
        assert not rows[0]["active"], rows
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、布洛芬、停用了阿司匹林",
        "华法林、布洛芬、停服了阿司匹林",
        "华法林、布洛芬、停药了阿司匹林",
    ],
)
def test_stop_le_drug_keeps_previous_in_use(text):
    names = api_server._context_medication_names({"medications": text})
    assert names == ["华法林", "布洛芬"], (text, names)


def test_stop_annotation_controls_still_removed():
    assert api_server._context_medication_names(
        {"medications": "华法林，已停药两个月"}
    ) == []
    assert api_server._context_medication_names(
        {"medications": "华法林，停药两个月"}
    ) == []


# ---------------------------------------------------------------- [6]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_web_restore_pending_skips_send_in_flight():
    source = _web_source()
    assert "restorePending && expectedSession && !sendInFlight" in source


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "我吃华法林，差点撞到头",
        "我吃华法林，险些撞到头部",
        "我吃华法林，几乎撞到头",
        "我吃华法林，差一点撞到头",
    ],
)
def test_near_miss_head_trauma_not_positive(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


def test_real_head_trauma_still_positive():
    result = _red("我吃华法林，撞到头", {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [9]
def test_noid_moved_edited_sibling_not_resurrected(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "B", "dose": "1",
             "frequency": "qd", "note": "", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "爸爸", "name": "A", "dose": "1",
             "frequency": "qd", "note": "", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(1, dose="9")
        store.update_medication(2, profile="妈妈")
        again = store.import_data(backup)
        rows = [
            (m["profile"], m["name"], m["dose"])
            for m in store.list_medications(None, limit=100)
        ]
        assert again.get("medications", 0) == 0, (again, rows)
        assert not any(r[0] == "爸爸" and r[1] == "A" for r in rows), rows
        assert ("妈妈", "B", "9") in rows and ("妈妈", "A", "1") in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_date_only_appointment_reimport_idempotent(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {"appointments": [
            {"id": 1, "profile": "妈妈", "title": "心内科复诊",
             "scheduled_at": "2027-06-10", "status": "upcoming",
             "note": "带报告",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "口腔检查",
             "scheduled_at": "2027-06-11", "status": "upcoming",
             "note": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_appointment_partial(1, status="completed")
        again = store.import_data(backup)
        rows = [
            (a["title"], str(a["scheduled_at"]), str(a["status"]))
            for a in store.list_appointments(None, limit=100)
        ]
        assert again.get("appointments", 0) == 0, (again, rows)
        assert any(
            r[0] == "心内科复诊" and r[2] == "completed" for r in rows
        ), rows
        assert len(rows) == 2, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_completed_relink_cleans_detached_reminder(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        backup = {
            "appointments": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建",
                "owner_visit_id": 1, "status": "completed",
                "scheduled_at": _day(6) + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 701, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(8), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": _day(9),
                "followup_appointment_id": 0,
                "followup_reminder_id": 701,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"] or 0) == 0
        assert int(visit["followup_reminder_id"] or 0) != 0
        store.update_appointment_partial(
            1, scheduled_at=_day(9) + "T09:00"
        )
        pending = store.list_reminders(False, "妈妈")
        visit = store.get_visit_record(1)
        assert pending == [], (pending, visit)
        assert int(visit["followup_appointment_id"] or 0) == 1, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    [
        "华法林、阿司匹林 100mg 已停用",
        "华法林、阿司匹林 100mg 每日一次 已停药",
    ],
)
def test_suffix_stop_with_dosage_drops_drug(text):
    names = api_server._context_medication_names({"medications": text})
    assert names == ["华法林"], (text, names)


def test_inline_bracket_dosage_control():
    names = api_server._context_medication_names(
        {"medications": "华法林、阿司匹林 100mg（已停用）"}
    )
    assert names == ["华法林"], names


# ---------------------------------------------------------------- [13]
def test_twin_moved_edited_tail_deleted_other_restores(tmp_path):
    store = _store(tmp_path)
    store._now = lambda: LATER
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n1", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "name": "A", "dose": "1",
             "frequency": "qd", "note": "n2", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.update_medication(2, profile="爸爸", note="e2")
        store.delete_medication(1)
        store.import_data(backup)
        rows = [
            (m["profile"], m["note"])
            for m in store.list_medications(None, limit=100)
        ]
        assert ("妈妈", "n1") in rows, rows
        again = store.import_data(backup)
        assert again.get("medications", 0) == 0, (again, rows)
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病？天气热容易出汗",
        "胸痛是什么病？没有出汗",
    ],
)
def test_definition_question_sweat_not_evidence(text):
    assert _red(text)["level"] == "none", text


def test_definition_question_cold_sweat_still_positive():
    result = _red("胸痛是什么病，直冒冷汗", {"age": "55岁"})
    assert "胸痛" in result["matches"], result


# ---------------------------------------------------------------- [15]
def test_family_attention_bp_before_glucose(tmp_path, monkeypatch):
    import memory
    from fastapi.testclient import TestClient

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    client = TestClient(api_server.app)
    try:
        client.post("/api/profiles", json={"name": "妈妈"})
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 8.2,
            "context": "fasting",
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 8.2,
            "context": "fasting",
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp", "value1": 166,
            "value2": 102,
        })
        overview = client.get("/api/family/overview?days=7").json()
        mom = next(
            p for p in overview["profiles"] if p["name"] == "妈妈"
        )
        assert mom["attention"]["label"] == "血压偏高", mom["attention"]
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_web_backup_slot_restore_tracked_and_removed():
    source = _web_source()
    start = source.index("function restoreInflightForOpenSession(")
    end = source.index("function restoreInflightQuestion(", start)
    block = source[start:end]
    assert "inflightRestoredKeyName = recordKey;" in block
    assert "function removeOtherInflightRecord(" in source
    start2 = source.index("els.input.addEventListener('input'")
    end2 = source.index("els.input.addEventListener('keydown'", start2)
    block2 = source[start2:end2]
    assert "removeOtherInflightRecord(restoredOwner, inflightRestoredText)" in block2


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "你不是说没有胸痛吗",
        "医生不是说没有胸痛吗",
        "你不是说没胸痛吗",
    ],
)
def test_reported_negation_not_positive(text):
    assert _red(text, {"age": "55岁"})["level"] == "none", text


def test_real_double_negation_still_positive():
    result = _red("他不是没有胸痛", {"age": "55岁"})
    assert "胸痛" in result["matches"], result
    assert _red("你不是说不是胸痛吗", {"age": "55岁"})["level"] == "none"
