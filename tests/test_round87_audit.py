# -*- coding: utf-8 -*-
"""第八十七轮审查修复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.medication_text import normalize_brand_drug_names
from core.ratelimit import SlidingWindowLimiter
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


# ---------------------------------------------------------------- [1]
def test_infant_fever_with_education_words():
    result = _red("宝宝发烧38.5度看了文章", {"age": "2个月"})
    assert "3 个月以下婴儿发热" in result["matches"], result


def test_chemo_fever_with_search_words():
    result = _red("化疗后发烧网上查了怎么办", {"history": "正在化疗"})
    assert "免疫功能低下伴发热" in result["matches"], result


def test_education_fever_still_filtered():
    assert _red("网上说发烧38度要就医")["level"] == "none"
    assert _red("宝宝没有发烧看了文章", {"age": "2个月"})["level"] == "none"


# ---------------------------------------------------------------- [2]
def test_dose_change_continuation_triage():
    result = _red(
        "我以前吃华法林，现在吃5mg，今天头撞到门框",
        {"age": "40岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林2mg，现在吃5mg，能吃布洛芬吗",
        "我以前吃华法林2mg，现在吃一片，能吃布洛芬吗",
    ],
)
def test_dose_change_continuation_medication_safety(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_past_use_without_dose_still_past():
    safety = check_medication_safety(
        "我以前吃华法林，现在能吃布洛芬吗", {}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [3]
def test_drift_twin_moved_to_sibling_archive_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_medication("妈妈", "Decoy0", "1片")
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "3",
             "frequency": "qd", "note": "a", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "name": "B", "dose": "3",
             "frequency": "qd", "note": "b", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom = next(
            r for r in rows if r["profile"] == "妈妈" and r["name"] == "B"
        )
        dad = next(
            r for r in rows if r["profile"] == "爸爸" and r["name"] == "B"
        )
        store.update_medication(mom["id"], profile="爸爸")
        store.update_medication(mom["id"], note="c")
        store.delete_medication(dad["id"])
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        notes = sorted(r["note"] for r in rows if r["name"] == "B")
        assert "b" in notes, rows
        before = sorted(
            (r["profile"], r["name"], r["note"]) for r in rows
        )
        assert store.import_data(backup)["medications"] == 0
        assert sorted(
            (r["profile"], r["name"], r["note"])
            for r in store.list_medications(None, limit=100)
        ) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_english_other_than_statement_keeps_anticoagulation():
    result = _red(
        "头撞到门框。I am not on any other medication besides warfarin",
        {},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [5][8]
def test_no_id_delete_first_edit_second_restores_deleted(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"allergies": [
            {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
             "noted_at": "2026-03-01", "reaction": "皮疹",
             "severity": "severe", "note": "N0",
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
             "noted_at": "2026-03-01", "reaction": "皮疹",
             "severity": "mild", "note": "N1",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_allergies(None, limit=100)
        first_id = next(r["id"] for r in rows if r["note"] == "N0")
        second_id = next(r["id"] for r in rows if r["note"] == "N1")
        store.delete_allergy(first_id)
        store.update_allergy(second_id, note="EDITED")
        store.import_data(backup)
        pairs = [
            (r["severity"], r["note"])
            for r in store.list_allergies(None, limit=100)
        ]
        assert ("severe", "N0") in pairs, pairs
        assert ("mild", "EDITED") in pairs, pairs
    finally:
        store.close()


# ---------------------------------------------------------------- [6]
def test_english_negated_stop_keeps_anticoagulation():
    result = _red(
        "今天头撞到门框",
        {"history": "warfarin has not been stopped for 2 years"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [7]
def test_moved_to_third_archive_deleted_restored(tmp_path):
    store = _store(tmp_path)
    try:
        for name in ("妈妈", "爸爸", "奶奶"):
            store.save_profile(name, {})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药",
             "due_at": "2027-01-01T09:00", "note": "a", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "吃药",
             "due_at": "2027-01-02T09:00", "note": "b", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(True, None)
        mom = next(r for r in rows if r["profile"] == "妈妈")
        dad = next(r for r in rows if r["profile"] == "爸爸")
        store.update_reminder(
            dad["id"], profile="奶奶", due_at="2027-03-01T09:00"
        )
        store.delete_reminder(mom["id"])
        store.import_data(backup)
        rows = store.list_reminders(True, None)
        assert any(
            r["profile"] == "妈妈"
            and r["due_at"] == "2027-01-01T09:00" for r in rows
        ), rows
        assert len([r for r in rows if r["profile"] == "奶奶"]) == 1, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_no_id_same_title_different_due_restores_deleted(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "吃药",
             "due_at": "2027-01-01T09:00", "note": "N", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "title": "吃药",
             "due_at": "2027-01-02T09:00", "note": "N", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(True, None)
        first = next(
            r for r in rows if r["due_at"] == "2027-01-01T09:00"
        )
        second = next(
            r for r in rows if r["due_at"] == "2027-01-02T09:00"
        )
        store.delete_reminder(first["id"])
        store.update_reminder(second["id"], due_at="2027-03-05T09:00")
        store.import_data(backup)
        dues = sorted(r["due_at"] for r in store.list_reminders(True, None))
        assert "2027-01-01T09:00" in dues, dues
        assert "2027-03-05T09:00" in dues, dues
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_import_overwriting_detached_link_merges_reminder(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day = str(datetime.now().date() + timedelta(days=6))
        store.import_data({
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "备注甲",
                "due_at": str(
                    datetime.now().date() + timedelta(days=5)
                ),
                "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(
                    datetime.now().date() - timedelta(days=2)
                ),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        store.import_data({
            "appointments": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 1,
                "status": "upcoming", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": str(
                    datetime.now().date() + timedelta(days=5)
                ),
                "appointment_id": 2, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(
                    datetime.now().date() - timedelta(days=2)
                ),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 2, "followup_reminder_id": 2,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        pending = store.list_reminders(False, "妈妈")
        detached = [
            r for r in pending if int(r["appointment_id"] or 0) == 0
        ]
        assert detached == [], pending
        assert len(pending) == 1, pending
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_import_stale_undated_reminder_skipped(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day1 = str(datetime.now().date() + timedelta(days=10))
        backup = {
            "appointments": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "upcoming", "scheduled_at": day1 + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": "记得带上次化验单",
                "due_at": str(datetime.strptime(day1, "%Y-%m-%d").date()
                             - timedelta(days=1)),
                "appointment_id": 2, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(
                    datetime.now().date() - timedelta(days=2)
                ),
                "diagnosis": "高血压", "next_followup_date": day1,
                "followup_appointment_id": 2, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        }
        store.import_data(backup)
        appt = store.list_appointments(None, None)[0]
        day2 = str(datetime.now().date() + timedelta(days=2))
        store.update_appointment_partial(
            int(appt["id"]), scheduled_at=day2 + "T09:00"
        )
        for reminder in store.list_reminders(False, "妈妈"):
            store.delete_reminder(reminder["id"])
        store.import_data(backup)
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
def test_family_attention_keeps_earlier_fresh_critical(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {"age": "62"})
        d12 = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d")
        d10 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        store.add_metric("妈妈", "bp", 180, 120, measured_at=d12 + "T09:00")
        store.add_metric("妈妈", "bp", 128, 82, measured_at=d10 + "T09:00")
        client = TestClient(api_server.app)
        overview = client.get("/api/family/overview").json()
        mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
        attention = mom.get("attention") or {}
        assert attention.get("level") != "ok", attention
    finally:
        store.close()


def test_family_glucose_mean_matches_insights(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("爸爸", {"age": "60"})
        d12 = (datetime.now() - timedelta(days=12)).strftime("%Y-%m-%d")
        d10 = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        store.add_metric(
            "爸爸", "glucose", 5.0, measured_at=d12 + "T09:00",
            context="fasting",
        )
        store.add_metric(
            "爸爸", "glucose", 7.5, measured_at=d10 + "T09:00",
            context="fasting",
        )
        client = TestClient(api_server.app)
        overview = client.get("/api/family/overview").json()
        dad = next(p for p in overview["profiles"] if p["name"] == "爸爸")
        attention = dad.get("attention") or {}
        assert attention.get("label") != "血糖偏高", attention
    finally:
        store.close()


# ---------------------------------------------------------------- [13][14]
def test_restored_snapshot_bound_to_question():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "let lastRestoredComposerQuestion = '';" in source
    assert "composerText === questionText" in source
    assert (
        "String(question || '').trim() === lastRestoredComposerQuestion"
        in source
    )
    # 同一主诉的重试保留快照，只有换问题发送时才清空旧快照
    # （第八十八轮起）
    start = source.index("async function send(question")
    # send 前部新增启动等待持久化（第一百一十九轮 [9]）：窗口放宽
    head = source[start:start + 1600]
    assert (
        "String(question || '').trim() !== lastRestoredComposerQuestion"
        in head
    )


# ---------------------------------------------------------------- [15]
def test_postpartum_eleven_months_recent():
    result = _red("产后十一个月，阴道出血")
    assert "孕产期出血" in result["matches"], result
    assert _red("产后12个月，阴道出血")["level"] == "none"


# ---------------------------------------------------------------- [16]
def test_daiwen_literature_words_not_drug():
    assert normalize_brand_drug_names("现代文学") == "现代文学"
    assert normalize_brand_drug_names("当代文艺") == "当代文艺"
    safety = check_medication_safety(
        "我是现代文学老师，最近膝盖痛能吃布洛芬吗", {}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "电视上说胸痛要马上就医，是真的吗",
        "报道说胸痛要马上就医",
        "书上说咯血要立刻去医院，对吗",
        "讲座里说抽搐要按人中，对吗",
    ],
)
def test_quote_sources_filtered(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [18]
def test_rate_limit_key_ignores_untrusted_headers(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r87-rate-token-0123456789")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)

    def request_with(headers):
        return Request({
            "type": "http", "method": "POST", "path": "/api/chat",
            "headers": [(b"host", b"localhost"), *headers],
            "client": ("192.0.2.10", 51000),
            "server": ("localhost", 80), "scheme": "http",
            "query_string": b"",
        })

    plain = request_with([])
    with_header = request_with([(b"x-forwarded-for", b"1.2.3.4")])
    # 未受信地址头仍保留哨兵键（鉴权失败计数依赖其共享语义）
    assert api_server._client_ip_key(with_header) == "ip:untrusted-proxy"
    # 端点限流键退回可证明的 TCP 对端桶：带不带头都是同一份额度
    assert api_server._provable_client_key(plain) == "ip:192.0.2.10"
    assert api_server._provable_client_key(with_header) == "ip:192.0.2.10"
