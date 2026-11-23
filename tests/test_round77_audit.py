# -*- coding: utf-8 -*-
"""第七十七轮审查修复的回归测试。"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
from core.security import _AccessLogQueryFilter
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


# ---------------------------------------------------------------- [1][6]
@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我也在吃同一种药，今天头撞到门框",
        "我爸吃华法林，我和他一样，今天头撞到门框",
        "我爸吃华法林，我也是，今天头撞到门框",
    ],
)
def test_same_drug_continuation_phrases(text):
    assert _red(text)["level"] == "emergency"
    assert check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗"), {"age": "35岁"}
    )["level"] == "warning"


@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我妈也吃，今天头撞到门框",
        "我爸吃甲氨蝶呤，我老公也在吃，今天发烧39度",
    ],
)
def test_family_subject_continuation_not_patient(text):
    assert _red(text)["level"] == "none"
    assert check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗")
        .replace("今天发烧39度", "能吃布洛芬吗"),
        {"age": "35岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [2]
def test_backup_does_not_merge_recreated_same_name_profile(tmp_path):
    source = _store(tmp_path)
    source.save_profile("妈妈", {"age": "70"})
    assert source.rename_profile("妈妈", "婆婆")
    source.save_profile("妈妈", {"age": "35"})
    source.add_allergy("妈妈", "花生")
    source.add_allergy("婆婆", "青霉素")
    backup = source.export_data()
    source.close()

    target = _store(tmp_path)
    target.import_data(backup)
    profiles = {item["name"]: item for item in target.list_profiles()}
    allergies = sorted(
        (item["profile"], item["substance"])
        for item in target.list_allergies(None)
    )
    target.close()
    assert set(profiles) == {"妈妈", "婆婆"}, profiles
    assert profiles["妈妈"]["context"].get("age") == "35"
    assert ("妈妈", "花生") in allergies and ("婆婆", "青霉素") in allergies


# ---------------------------------------------------------------- [3]
def test_moved_medication_does_not_steal_deleted_twin(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("爸爸", {"age": "62"})
    meds = []
    for profile, name, dose, freq in (
        ("妈妈", "氨氯地平", "10mg", "每日1次"),
        ("妈妈", "氨氯地平", "10mg", "每日2次"),
        ("爸爸", "阿司匹林", "10mg", "每日1次"),
        ("妈妈", "二甲双胍", "10mg", "每日1次"),
    ):
        meds.append(store.add_medication(
            profile, name, dose, frequency=freq
        ))
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ?", (CREATED,)
        )
    backup = store.export_data()
    store.delete_medication(meds[1]["id"])
    store.update_medication(meds[2]["id"], profile="妈妈")
    store.update_medication(meds[3]["id"], name="格华止")
    store.import_data(backup)
    rows = [
        (item["profile"], item["name"], item["frequency"])
        for item in store.list_medications(limit=100)
    ]
    store.close()
    assert any(
        profile == "妈妈" and name == "氨氯地平" and freq == "每日2次"
        for profile, name, freq in rows
    ), rows


# ---------------------------------------------------------------- [4]
def test_direct_client_forwarded_header_does_not_bypass_prelock(
    monkeypatch, tmp_path
):
    token = "r77-direct-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("203.0.113.9", 56501),
    )
    try:
        codes = []
        for index in range(12):
            codes.append(client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": f"10.9.9.{index}",
                },
            ).status_code)
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429, 429], codes
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("198.51.100.7", 56502),
        )
        assert victim.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        ).status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "高危型 HPV 是指与宫颈癌风险相关的病毒亚型，建议定期筛查。",
        "高危妊娠需要增加产检次数。",
        "高危儿指出生前后存在高危因素的新生儿。",
        "风险等级：**不确定**。高危型 HPV 需要结合筛查结果判断。",
    ],
)
def test_risk_parse_ignores_medical_compound_words(text):
    assert api_server.parse_risk_level(text) == ""


def test_risk_parse_still_detects_real_high_risk():
    assert api_server.parse_risk_level("风险等级：高危，请立即就医") == "high"
    assert api_server.parse_risk_level("存在高危表现，建议急诊") == "high"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text,term",
    [
        ("华法林吃了三年，停了一年多了，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("甲氨蝶呤吃了两年，停了一年多了，今天发烧38.9度", "免疫功能低下伴发热"),
    ],
)
def test_long_stop_after_drug_name_suppresses_active(text, term):
    result = _red(text)
    assert term not in result["matches"], result


def test_long_ended_treatment_keeps_other_current_drug():
    result = _red("头撞到门框", {"history": "化疗结束5年了每天吃华法林"})
    assert "抗凝治疗期间头部外伤" in result["matches"]


# ---------------------------------------------------------------- [8]
def test_fever_negation_for_all_terms():
    assert _red("宝宝没有发烧，也没有发高烧", {"age": "2个月"})["level"] == "none"
    assert _red(
        "化疗后没有发烧，也没有再发高烧", {"history": "正在化疗"}
    )["level"] == "none"


# ---------------------------------------------------------------- [9]
@pytest.mark.parametrize(
    "text",
    [
        "我最近看了一篇关于胸痛的文章",
        "我最近想找关于咯血的科普视频",
        "我昨天刷到讲抽搐的视频",
    ],
)
def test_topic_article_with_time_word_not_complaint(text):
    assert _red(text)["level"] == "none"


def test_patient_symptom_then_article_still_emergency():
    assert _red("我最近胸痛，看了一篇关于胸痛的文章")["level"] == "emergency"


# ---------------------------------------------------------------- [10][16][21]
def test_resumed_at_survives_edit_and_backup(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    medication = store.add_medication(
        "妈妈", "二甲双胍", "500mg", frequency="每日两次",
        started_at=(now - timedelta(days=60)).strftime("%Y-%m-%d"),
    )
    store.add_medication_log(
        medication["id"],
        taken_at=(now - timedelta(days=5)).strftime("%Y-%m-%d") + "T08:00",
    )
    store.update_medication(medication["id"], active=False)
    store.update_medication(medication["id"], active=True)
    resumed = next(
        item for item in store.list_medications("妈妈")
        if item["id"] == medication["id"]
    )["resumed_at"]
    assert resumed
    store.update_medication(medication["id"], dose="1000mg")
    edited = next(
        item for item in store.list_medications("妈妈")
        if item["id"] == medication["id"]
    )
    assert edited["resumed_at"] == resumed, edited
    assert edited["week_count"] == 0, edited
    backup = store.export_data()
    store.close()

    target = _store(tmp_path)
    target.import_data(backup)
    restored = next(
        item for item in target.list_medications("妈妈")
        if item["name"] == "二甲双胍"
    )
    target.close()
    assert restored["resumed_at"] == resumed, restored
    assert restored["week_count"] == 0, restored


# ---------------------------------------------------------------- [11]
def test_reminder_fallback_requires_strong_hint(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    appointment = store.add_appointment(
        "妈妈", "体检", scheduled_at="2026-12-11T09:00"
    )
    store.import_data({"reminders": [
        {"title": "复诊：心内科", "due_at": "2026-12-10T09:00",
         "note": "", "profile": "妈妈",
         "appointment_id": appointment["id"]},
    ]})
    row = store.list_reminders(False, "妈妈")[0]
    assert int(row["appointment_id"] or 0) == 0, row
    # 备注里的复诊日期与预约日精确一致时才允许挂接
    store.import_data({"reminders": [
        {"title": "复诊：心内科", "due_at": "2026-12-10T09:00",
         "note": "复诊 2026-12-11 心内科", "profile": "妈妈",
         "appointment_id": appointment["id"]},
    ]})
    rows = store.list_reminders(False, "妈妈")
    linked = next(r for r in rows if r["note"].startswith("复诊 2026-12-11"))
    store.close()
    assert int(linked["appointment_id"] or 0) == appointment["id"]


# ---------------------------------------------------------------- [12]
def test_stale_critical_lab_not_emergency_insight(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    old_day = (datetime.now() - timedelta(days=300)).strftime("%Y-%m-%d")
    store.add_lab_result(
        "妈妈", "血钾", 6.6, "mmol/L", 3.5, 5.3,
        tested_at=f"{old_day}T08:00",
    )
    insights = client.get(
        "/api/health/insights", params={"days": 365, "profile": "妈妈"}
    ).json()["insights"]
    critical = [item for item in insights if item["key"] == "lab_critical"]
    assert critical == [], critical
    report = asyncio.run(api_server._weekly_report_impl(
        "妈妈", 365, 400, False
    ))
    assert report["labs"]["critical"] == 0, report["labs"]


# ---------------------------------------------------------------- [13]
def test_same_day_resume_adherence_consistent(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    today = datetime.now()
    store._now = lambda: today.strftime("%Y-%m-%d") + "T00:01:00"
    medication = store.add_medication(
        "妈妈", "二甲双胍", "500mg", frequency="每日两次",
        started_at=(today - timedelta(days=5)).strftime("%Y-%m-%d"),
    )
    store.add_medication_log(
        medication["id"], taken_at=today.strftime("%Y-%m-%d") + "T00:00"
    )
    store.update_medication(medication["id"], active=False)
    store.update_medication(medication["id"], active=True)
    store._now = lambda: today.strftime("%Y-%m-%d") + "T00:02:00"
    store.add_medication_log(
        medication["id"], taken_at=today.strftime("%Y-%m-%d") + "T00:02"
    )
    overview = client.get("/api/family/overview").json()
    mom = next(item for item in overview["profiles"] if item["name"] == "妈妈")
    weekly = client.get(
        "/api/reports/weekly", params={"days": 7, "profile": "妈妈"}
    ).json()
    assert mom["medications"]["adherence"]["taken"] == 1, mom
    assert weekly["medications"]["adherence"]["taken"] == 1, weekly


# ---------------------------------------------------------------- [14][19][20]
def test_frontend_inflight_retry_and_delete_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function restoreInflightForOpenSession" in source
    assert "restoreInflightForOpenSession(id, data.messages || [])" in source
    assert source.count("preserveDraftProfile(retry.profile, retry.question)") >= 2
    assert "profile: sendProfile" in source
    assert "if (item.id === sessionId && !busy && !sendInFlight)" in source
    assert "if (ids.includes(sessionId) && !busy && !sendInFlight)" in source


# ---------------------------------------------------------------- [15]
def test_web_import_carries_renames_and_exported_at(client_and_store):
    client, store = client_and_store
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "profile_renames: data.profile_renames" in source
    assert "exported_at: data.exported_at" in source
    request = api_server.ImportSessionsRequest(
        profile_renames=[{"old_name": "妈妈", "new_name": "母亲"}],
        exported_at="2026-11-01T10:00:00",
    )
    dumped = request.model_dump()
    assert dumped["profile_renames"] and dumped["exported_at"]


# ---------------------------------------------------------------- [17]
def test_repeated_import_keeps_twin_symptom_count(tmp_path):
    store = _store(tmp_path)
    store.save_profile("爸爸", {"age": "62"})
    store.save_profile("妈妈", {"age": "60"})
    backup = {"symptoms": [
        {"id": 1, "profile": "爸爸", "symptom": "咳嗽", "severity": 2,
         "note": "n1", "recorded_at": "2026-11-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "symptom": "咳嗽", "severity": 2,
         "note": "n1", "recorded_at": "2026-11-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 3, "profile": "妈妈", "symptom": "咳嗽", "severity": 1,
         "note": "", "recorded_at": "2026-11-02T09:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_symptoms(None, 100)
    dad = [r for r in rows if r["profile"] == "爸爸"]
    mom = [r for r in rows if r["profile"] == "妈妈"][0]
    store.update_symptom_partial(
        dad[0]["id"], recorded_at="2026-11-06T09:00"
    )
    store.delete_symptom(dad[1]["id"])
    store.update_symptom_partial(mom["id"], symptom="干咳症状1")
    counts = []
    for _ in range(2):
        store.import_data(backup)
        counts.append(len(store.list_symptoms(None, 100)))
    store.close()
    assert counts == [3, 3], counts


# ---------------------------------------------------------------- [18]
def test_legacy_shared_followup_reminder_survives_import(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    backup = {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
             "note": "", "owner_visit_id": 0, "status": "upcoming",
             "scheduled_at": "2027-01-20T09:00",
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
             "note": "", "due_at": "2027-01-19T09:00",
             "appointment_id": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "visit_records": [
            {"id": 1, "profile": "妈妈", "visit_date": "2026-11-01",
             "diagnosis": "高血压", "next_followup_date": "2027-01-20",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": "2026-11-02",
             "diagnosis": "糖尿病", "next_followup_date": "2027-01-20",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }
    store.import_data(backup)
    reminders = store.list_reminders(False, "妈妈")
    visits = store.list_visit_records("妈妈")
    store.close()
    assert len(reminders) == 1, reminders
    assert reminders[0]["appointment_id"] == 1
    assert any(
        int(item.get("followup_reminder_id") or 0) == reminders[0]["id"]
        for item in visits
    ), visits


# ---------------------------------------------------------------- [22]
def test_access_log_filter_handles_gunicorn_dict_args():
    filt = _AccessLogQueryFilter()
    record = logging.LogRecord(
        "gunicorn.access", logging.INFO, __file__, 1, "%(r)s", {
            "r": "GET /api/health/search?q=抑郁症%20失眠 HTTP/1.1",
            "s": "200",
        }, None,
    )
    filt.filter(record)
    assert record.args["r"] == "GET /api/health/search?<redacted>"
    source = (ROOT / "core" / "security.py").read_text(encoding="utf-8")
    assert '"gunicorn.access"' in source
