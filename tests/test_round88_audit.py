# -*- coding: utf-8 -*-
"""第八十八轮审查修复的回归测试。"""
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


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林，现在改吃5mg，今天头撞到门框",
        "我以前吃华法林，现在改成5mg，今天头撞到门框",
        "我以前吃华法林，现在改为5mg，今天头撞到门框",
        "我以前吃华法林，现在换为5mg，今天头撞到门框",
        "我以前吃华法林，现在调到5mg，今天头撞到门框",
        "我以前吃华法林，现在换成5mg，今天头撞到门框",
        "我以前吃华法林，现在每天半片，今天头撞到门框",
        "我以前吃华法林，现在一片，今天头撞到门框",
    ],
)
def test_dose_change_synonyms_keep_anticoagulant(text):
    result = _red(text, {"age": "40岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_dose_change_synonym_medication_safety():
    result = check_medication_safety(
        "我以前吃华法林，现在改吃5mg，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [1][4]
@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林，现在吃5mg阿司匹林，今天头撞到门框",
        "我以前吃华法林，现在吃5mg氨氯地平，今天头撞到门框",
    ],
)
def test_other_drug_dose_not_attributed(text):
    assert _red(text, {"age": "40岁"})["level"] == "none"


def test_other_drug_dose_not_reviving_methotrexate():
    result = _red(
        "我以前吃甲氨蝶呤，现在吃5mg氨氯地平，今天发烧了",
        {"age": "40岁"},
    )
    assert result["level"] == "none", result


def test_medication_safety_other_drug_dose_not_warfarin():
    result = check_medication_safety(
        "我以前吃华法林，现在吃5mg氨氯地平，能吃布洛芬吗", {}
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "看电视时胸痛",
        "看电视时咯血了",
        "听广播时胸痛",
        "看报纸时胸痛",
        "看讲座时抽搐了",
        "做广播体操时胸痛",
    ],
)
def test_media_time_context_keeps_symptom(text):
    assert _red(text)["level"] == "emergency", text


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "电视上说胸痛要马上就医，是真的吗",
        "报道说胸痛要马上就医",
        "书上说咯血要立刻去医院，对吗",
        "讲座里说抽搐要按人中，对吗",
    ],
)
def test_quote_sources_still_filtered(text):
    assert _red(text)["level"] == "none", text


def test_tv_fever_quote_not_patient_fever():
    result = _red("电视上说宝宝发烧38.5度要马上就医", {"age": "2个月"})
    assert result["level"] == "none", result


def test_instruction_sheet_dose_keeps_symptom():
    # 「说明书上」不是科普引用：按说明书剂量服药后胸痛是真实主诉
    result = _red("按说明书上说的剂量吃了药后胸痛")
    assert result["level"] == "emergency", result


# ---------------------------------------------------------------- [5][7]
@pytest.mark.parametrize(
    "text",
    [
        "warfarin was stopped 2 years ago and I take amlodipine daily",
        "warfarin was stopped 2 years ago and I walk daily",
        "warfarin was stopped 2 years ago but take aspirin daily",
    ],
)
def test_english_daily_not_continuation(text):
    assert _red("今天头撞到门框。" + text, {"age": "40岁"})["level"] == "none"


def test_english_still_taking_it_is_continuation():
    result = _red(
        "今天头撞到门框。warfarin was stopped 2 years ago besides"
        " that I still take it",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_english_daily_other_drug_not_methotrexate():
    result = _red(
        "今天发烧了。methotrexate was stopped 2 years ago and I take"
        " folic acid daily",
        {"age": "40岁"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text,context",
    [
        ("看了一篇宝宝发烧怎么办的文章", {"age": "2个月"}),
        ("看了化疗病人发烧怎么处理的文章", {"history": "正在化疗"}),
        ("宝宝发烧的护理文章", {"age": "1个月"}),
    ],
)
def test_fever_topic_article_not_patient_fever(text, context):
    assert _red(text, context)["level"] == "none", text


# ---------------------------------------------------------------- [8]
def test_same_second_medication_twins_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("爸爸", {})
        for _ in range(3):
            store.add_medication("爸爸", "A", "1", frequency="qd", note="n2")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE medications SET created_at = ?", (CREATED,)
            )
        backup = store.export_data()
        store.update_medication(1, dose="8")
        store.update_medication(3, dose="9")
        store.delete_medication(2)
        assert store.import_data(backup)["medications"] == 1
        rows = len(store.list_medications(None, limit=100))
        assert store.import_data(backup)["medications"] == 0
        assert store.import_data(backup)["medications"] == 0
        assert len(store.list_medications(None, limit=100)) == rows == 3
    finally:
        store.close()


def test_same_second_reminder_twins_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        for _ in range(3):
            store.add_reminder("复诊", _day(3), profile="妈妈")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET created_at = ?", (CREATED,)
            )
        backup = store.export_data()
        store.update_reminder(1, due_at=_day(5))
        store.update_reminder(3, due_at=_day(5))
        store.delete_reminder(2)
        assert store.import_data(backup)["reminders"] == 1
        rows = len(store.list_reminders(False, "妈妈"))
        assert store.import_data(backup)["reminders"] == 0
        assert store.import_data(backup)["reminders"] == 0
        assert len(store.list_reminders(False, "妈妈")) == rows == 3
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_same_second_care_item_rename_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("爸爸", {})
        store.add_care_item(
            "爸爸", "量血压", 7, next_due_at="2026-06-01", note=""
        )
        store.add_care_item(
            "爸爸", "量血压", 7, next_due_at="2026-06-01", note="n1"
        )
        store.add_care_item(
            "爸爸", "测血糖", 30, last_done_at="2026-05-01",
            next_due_at="2026-06-01", note="n1",
        )
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE care_items SET created_at = ?", (CREATED,)
            )
        backup = store.export_data()
        store.update_care_item_partial(1, title="测血糖")
        store.update_care_item_partial(3, title="换药")
        store.delete_care_item(2)
        assert store.import_data(backup)["care_items"] == 1
        rows = len(store.list_care_items(None, limit=100))
        titles = sorted(
            item["title"] for item in store.list_care_items(None, limit=100)
        )
        assert store.import_data(backup)["care_items"] == 0
        assert store.import_data(backup)["care_items"] == 0
        assert len(store.list_care_items(None, limit=100)) == rows == 3
        assert titles == ["换药", "测血糖", "量血压"]
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_imported_detached_reminder_merges_into_local_appointment(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day = _day(6)
        store.import_data({
            "appointments": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "", "owner_visit_id": 1, "status": "upcoming",
                "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(5), "appointment_id": 1, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 1, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        # 旧备份：就诊只带「仅提醒链接」的脱钩提醒
        store.import_data({
            "reminders": [{
                "id": 501, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(5), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0, "followup_reminder_id": 501,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert int(pending[0]["appointment_id"] or 0) == 1
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"] or 0) == int(
            pending[0]["id"]
        )
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_tombstone_restore_cleans_detached_reminder(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day = _day(6)
        store.import_data({
            "appointments": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "", "owner_visit_id": 1, "status": "cancelled",
                "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(5), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0, "followup_reminder_id": 2,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = 1"
                " WHERE id = 1"
            )
        store.set_app_state("reminder_deleted_1", store._now())
        store.update_appointment_partial(1, status="upcoming")
        assert store.list_reminders(False, "妈妈") == []
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"] or 0) == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
def test_family_glucose_mean_not_diluted_by_stale_readings(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("爸爸", {"age": "60"})
        for offset in (-100, -80, -60, -40):
            store.add_metric(
                "爸爸", "glucose", 5.5, measured_at=_day(offset) + "T09:00",
                context="fasting",
            )
        store.add_metric(
            "爸爸", "glucose", 12.0, measured_at=_day(-1) + "T09:00",
            context="fasting",
        )
        client = TestClient(api_server.app)
        overview = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        dad = next(p for p in overview["profiles"] if p["name"] == "爸爸")
        assert (dad.get("attention") or {}).get("label") == "血糖偏高", dad
        insights = client.get(
            "/api/health/insights", params={"days": 30}
        ).json()["insights"]
        assert [
            item for item in insights
            if item["key"] == "metric_glucose_fasting"
        ]
    finally:
        store.close()


def test_family_glucose_old_high_not_flagged_now(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {"age": "60"})
        for offset in (-120, -100):
            store.add_metric(
                "妈妈", "glucose", 13.0, measured_at=_day(offset) + "T09:00",
                context="fasting",
            )
        store.add_metric(
            "妈妈", "glucose", 5.4, measured_at=_day(-1) + "T09:00",
            context="fasting",
        )
        client = TestClient(api_server.app)
        overview = client.get(
            "/api/family/overview", params={"days": 7}
        ).json()
        mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
        assert (mom.get("attention") or {}).get("label") != "血糖偏高", mom
    finally:
        store.close()


# ---------------------------------------------------------------- [13][14][16]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_send_start_keeps_snapshot_for_same_question():
    source = _web_source()
    start = source.index("async function send(question")
    # send 前部新增启动等待持久化（第一百一十九轮 [9]）：窗口放宽
    head = source[start:start + 1600]
    assert (
        "if (String(question || '').trim() !== lastRestoredComposerQuestion)"
        in head
    )
    # 同一问题重试时不清空快照：定界在 if 内
    unconditional = (
        "  lastRestoredComposerText = '';\n"
        "  lastRestoredComposerQuestion = '';"
    )
    assert source.find(unconditional, start, start + 800) == -1


def test_dedupe_branch_binds_question_snapshot():
    source = _web_source()
    assert (
        "lastRestoredComposerText = remainder;\n"
        "      lastRestoredComposerQuestion = remainder;"
    ) in source


# ---------------------------------------------------------------- [15]
def test_deleted_detached_reminder_not_revived_by_reschedule(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day = _day(6)
        store.import_data({
            "appointments": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "", "owner_visit_id": 1, "status": "upcoming",
                "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": _day(5), "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": _day(-2),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 1, "followup_reminder_id": 2,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert store.delete_reminder(int(pending[0]["id"]))
        store.update_appointment_partial(1, scheduled_at=_day(12) + "T09:00")
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [12] static
def test_family_overview_glucose_uses_grade_window():
    source = (
        ROOT / "api_server.py"
    ).read_text(encoding="utf-8")
    assert "max(days, 30)" in source
    assert "for item in grade_metrics:" in source
