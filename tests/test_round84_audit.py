# -*- coding: utf-8 -*-
"""第八十四轮审查修复的回归测试。"""
import asyncio
import os
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.medication_text import clause_mentions_drug_clause
from core.ratelimit import SlidingWindowLimiter
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _rem_rows(store):
    return sorted(
        (r["id"], r["profile"], r["title"], r["due_at"], r["note"])
        for r in store.list_reminders(True, None)
    )


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "我看短视频时胸痛",
        "看视频就胸痛",
        "刷了会儿短视频后胸痛",
        "看视频看得胸痛",
        "看短视频的时候胸痛",
        "看短视频时抽搐",
    ],
)
def test_short_video_media_context_keeps_red_flag(text):
    result = _red(text)
    assert result["level"] == "emergency", (text, result)


@pytest.mark.parametrize(
    "text",
    ["刷视频时看到胸痛要马上就医", "看视频时看到胸痛急救科普"],
)
def test_media_quote_content_still_filtered(text):
    result = _red(text)
    assert result["level"] == "none", (text, result)


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "停经45天，阴道出血",
        "月经推迟两周，阴道出血",
        "两个月没来月经了，今天阴道出血",
        "停经45天伴下腹剧痛，阴道出血",
    ],
)
def test_recent_amenorrhea_pregnancy_bleeding(text):
    result = _red(text)
    assert "孕产期出血" in result["matches"], (text, result)


@pytest.mark.parametrize(
    "text",
    ["停经5年，阴道出血", "绝经后阴道出血", "停经多年，今天阴道出血"],
)
def test_menopause_not_pregnancy(text):
    result = _red(text)
    assert "孕产期出血" not in result["matches"], (text, result)


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "middle",
    [
        "波立维吃了两年",
        "优甲乐吃了两年",
        "替格瑞洛吃了两年",
        "立普妥吃了两年",
        "络活喜吃了两年",
    ],
)
def test_brand_drug_clause_blocks_stop_attribution(middle):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{middle}，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], (middle, result)


def test_brand_drug_clause_recognized_shared():
    assert clause_mentions_drug_clause("波立维吃了两年")


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text", ["其他定期检查", "其他定期复查", "其他定义都正常"],
)
def test_other_ding_phrase_not_a_drug(text):
    assert not clause_mentions_drug_clause(text)
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{text}，停了一年多了"},
    )
    assert result["level"] == "none", (text, result)
    safety = check_medication_safety(
        f"华法林没吃完，{text}，停了一年多了，我能吃布洛芬吗", {}
    )
    assert safety["level"] == "none", (text, safety)


# ---------------------------------------------------------------- [6]
def test_current_drug_form_suffix_not_other_drug():
    safety = check_medication_safety(
        "阿司匹林肠溶片吃了三年，停了一年多了，我能吃布洛芬吗", {}
    )
    assert safety["level"] == "none", safety
    safety = check_medication_safety(
        "布洛芬缓释胶囊吃了两年，停了半年了，能吃阿司匹林吗", {}
    )
    assert safety["level"] == "none", safety
    safety = check_medication_safety(
        "华法林没吃完，氯雷他定吃了两年，停了一年多了，我能吃布洛芬吗",
        {},
    )
    assert safety["level"] == "warning", safety


# ---------------------------------------------------------------- [3]
def test_step1_soft_veto_does_not_duplicate_twin(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "B", "note": "N1",
             "due_at": "2027-01-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "A", "note": "N2",
             "due_at": "2027-02-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "妈妈", "title": "A", "note": "N2",
             "due_at": "2027-02-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = _rem_rows(store)
        mom_a = [r for r in rows if r[1] == "妈妈" and r[2] == "A"]
        store.update_reminder(mom_a[0][0], title="B")
        before = _rem_rows(store)
        assert store.import_data(backup)["reminders"] == 0
        assert _rem_rows(store) == before
    finally:
        store.close()


def test_step1_soft_veto_restores_deleted_content(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "note": "X",
             "due_at": "2027-01-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药", "note": "Y",
             "due_at": "2027-01-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = _rem_rows(store)
        x_row = next(r for r in rows if r[4] == "X")
        y_row = next(r for r in rows if r[4] == "Y")
        store.update_reminder(x_row[0], note="Z")
        store.delete_reminder(y_row[0])
        store.import_data(backup)
        notes = sorted(r[4] for r in _rem_rows(store))
        assert notes == ["Y", "Z"], notes
        before = _rem_rows(store)
        assert store.import_data(backup)["reminders"] == 0
        assert _rem_rows(store) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_non_last_tail_tie_keeps_own_survivor(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "吃药", "note": "N1",
             "due_at": "2027-01-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药", "note": "N2",
             "due_at": "2027-01-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = _rem_rows(store)
        n1 = next(r for r in rows if r[4] == "N1")
        n2 = next(r for r in rows if r[4] == "N2")
        store.delete_reminder(n1[0])
        store.update_reminder(n2[0], note="Z")
        store.import_data(backup)
        notes = sorted(r[4] for r in _rem_rows(store))
        assert notes == ["N1", "Z"], notes
        before = _rem_rows(store)
        assert store.import_data(backup)["reminders"] == 0
        assert _rem_rows(store) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_overdue_auto_followup_restore_is_atomic(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {})
        today = datetime.now().date()
        past_day = str(today - timedelta(days=3))
        store.import_data({
            "appointments": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "cancelled", "scheduled_at": past_day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": f"复诊 {past_day}",
                "due_at": str(today - timedelta(days=4)),
                "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(today - timedelta(days=10)),
                "diagnosis": "高血压", "next_followup_date": past_day,
                "followup_appointment_id": 0, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        client = TestClient(api_server.app)
        appointment = store.list_appointments(None, None)[0]
        before_reminder = store.list_reminders(False, "妈妈")[0]
        resp = client.patch(
            f"/api/appointments/{int(appointment['id'])}",
            json={"status": "upcoming"},
        )
        assert resp.status_code == 400, resp.text
        after = store.list_appointments(None, None)[0]
        assert after["status"] == "cancelled", after
        reminder = store.list_reminders(False, "妈妈")[0]
        assert int(reminder["appointment_id"]) == 0, reminder
        assert str(reminder["due_at"]) == str(
            before_reminder["due_at"]), reminder
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"]) == 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_completed_cancelled_appointment_drops_orphan_reminder(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {})
        today = datetime.now().date()
        day = str(today + timedelta(days=5))
        store.import_data({
            "appointments": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "cancelled", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": f"复诊 {day}",
                "due_at": str(today + timedelta(days=4)),
                "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(today - timedelta(days=2)),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        client = TestClient(api_server.app)
        appointment = store.list_appointments(None, None)[0]
        resp = client.patch(
            f"/api/appointments/{int(appointment['id'])}",
            json={"status": "completed"},
        )
        assert resp.status_code == 200, resp.text
        assert store.list_reminders(False, "妈妈") == []
        client.delete("/api/visits/1")
        assert store.list_reminders(True, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
def test_visit_reattach_requires_same_profile(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("张三", {})
        store.save_profile("李四", {})
        today = datetime.now().date()
        day = str(today - timedelta(days=1))
        store.import_data({
            "appointments": [{
                "id": 2, "profile": "李四", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "completed", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "张三",
                "visit_date": str(today - timedelta(days=20)),
                "diagnosis": "高血压",
                "next_followup_date": str(today - timedelta(days=10)),
                "followup_appointment_id": 0, "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        store.update_visit_record_partial(
            1, next_followup_date=str(today - timedelta(days=12))
        )
        store.update_visit_record_partial(1, next_followup_date=day)
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"]) == 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def _forwarded_scope(value, client=("127.0.0.1", 5000)):
    return {
        "type": "http", "method": "GET", "path": "/api/sessions",
        "headers": [(b"host", b"localhost"), (b"forwarded", value)],
        "client": client, "server": ("test", 80), "scheme": "http",
        "query_string": b"",
    }


@pytest.mark.parametrize(
    "value,expected",
    [
        (b'\", for=203.0.113.9', "203.0.113.9"),
        (b'for=203.0.113.99;x=\", For=198.51.100.10',
         "198.51.100.10"),
        (b'for=203.0.113.99;x=\", for=unknown', ""),
        (b'for=203.0.113.99;x=\", for=_hidden', ""),
        (b'for=203.0.113.99;x=\", for=unix:', ""),
    ],
)
def test_forwarded_appended_only_last_element(value, expected, monkeypatch):
    for key, val in (
        ("MEDIX_TRUST_PROXY_HEADERS", "1"),
        ("MEDIX_PROXY_SINGLE_HOP", "1"),
        ("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded"),
    ):
        monkeypatch.setenv(key, val)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    request = Request(_forwarded_scope(value))
    assert api_server._forwarded_appended_address(request) == expected


def _auth_scope(headers, client):
    return {
        "type": "http", "method": "GET", "path": "/api/sessions",
        "headers": [(b"host", b"localhost"), *headers],
        "client": client, "server": ("test", 80), "scheme": "http",
        "query_string": b"",
    }


def _run_auth(scope):
    async def call_next(_request):
        return Response(status_code=200)

    return asyncio.run(
        api_server.require_access_token(Request(scope), call_next)
    )


def test_uppercase_proxy_element_still_prelocks(monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r84-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    calls = {"n": 0}
    original = api_server.is_authorized

    def counting(request):
        calls["n"] += 1
        return original(request)

    monkeypatch.setattr(api_server, "is_authorized", counting)
    try:
        scopes = [
            _auth_scope(
                [(b"forwarded",
                  b'for=203.0.113.99;x=\", For=198.51.100.10'),
                 (b"x-medix-token", b"bad")],
                ("127.0.0.1", 59000),
            )
            for _ in range(15)
        ]
        codes = [_run_auth(scope).status_code for scope in scopes]
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429] * 5, codes
        assert calls["n"] == 10, calls
    finally:
        api_server._known_token_proofs.clear()


# ---------------------------------------------------------------- [11]
def test_settle_draft_preserves_adopted_session_draft():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function settleDraftForSession")
    end = source.index("function restoreComposer", start)
    block = source[start:end]
    assert "safeSessionGet(ADOPTED_SESSION_KEY) === target" in block
    assert "safeSessionSet(DRAFT_OWNER_KEY, target);" in block
