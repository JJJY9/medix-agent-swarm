# -*- coding: utf-8 -*-
"""第八十三轮审查修复的回归测试。"""
import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request
from starlette.responses import Response

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
from core.triage import (
    _active_medical_text, detect_red_flags, patient_complaint_text,
)
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


# ---------------------------------------------------------------- [1][8]
@pytest.mark.parametrize(
    "text",
    ["发烧不到一天", "发烧不到48小时", "发烧不到两天"],
)
def test_infant_duration_fever_still_flags(text):
    result = _red(f"孩子2个月，{text}", {"age": "2个月"})
    assert "3 个月以下婴儿发热" in result["matches"], result


def test_chemo_duration_fever_still_flags():
    result = _red("化疗期间发烧不到一天", {"history": "正在化疗"})
    assert "免疫功能低下伴发热" in result["matches"], result


@pytest.mark.parametrize(
    "text",
    ["发烧没到39度", "体温不到39度", "发烧没到40度", "发烧不到38.5度"],
)
def test_high_fever_bound_not_negative(text):
    result = _red(f"孩子2个月，{text}", {"age": "2个月"})
    assert "3 个月以下婴儿发热" in result["matches"], result


@pytest.mark.parametrize(
    "text",
    [
        "体温不到38度", "发烧不到38度", "体温没有到38度", "没有到38度",
    ],
)
def test_sub_38_bound_still_negative(text):
    result = _red(f"孩子2个月，{text}", {"age": "2个月"})
    assert result["level"] == "none", result


def test_positive_fever_readings_still_flag():
    for text in ("发烧38.9度", "体温39.2度"):
        result = _red(f"孩子2个月，{text}", {"age": "2个月"})
        assert "3 个月以下婴儿发热" in result["matches"], result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "我看视频时胸痛", "昨天看视频时胸痛", "看视频时喘不上气",
        "我看视频时抽搐了", "看了个视频后胸痛", "看新闻时胸痛",
    ],
)
def test_media_time_symptom_keeps_red_flag(text):
    result = _red(text)
    assert result["level"] == "emergency", (text, result)


@pytest.mark.parametrize(
    "text",
    [
        "我在网上看到胸痛要重视",
        "网上说胸痛持续十分钟要就医",
        "看了一篇关于胸痛的文章",
    ],
)
def test_education_context_still_filtered(text):
    result = _red(text)
    assert result["level"] == "none", (text, result)


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "history",
    [
        "以前吃华法林，现在还吃",
        "以前吃华法林，一直没停过",
        "以前吃华法林，一直吃到现在",
        "以前吃华法林，现在还吃着呢",
    ],
)
def test_continued_warfarin_counts_as_active(history):
    result = _red("今天头撞到门框", {"history": history})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_continued_warfarin_medication_safety():
    safety = check_medication_safety(
        "我以前吃华法林，现在还吃，能吃布洛芬吗", {}
    )
    assert safety["level"] == "warning", safety
    active = _active_medical_text({"history": "以前吃华法林，一直吃到现在"})
    assert "华法林" in active, active


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "middle",
    ["氯雷他定吃了两年", "去年吃中药调理", "降压药吃了两年"],
)
def test_medication_safety_intervening_other_drug(middle):
    safety = check_medication_safety(
        f"华法林没吃完，{middle}，停了一年多了，我能吃布洛芬吗", {}
    )
    assert safety["level"] == "warning", (middle, safety)


# ---------------------------------------------------------------- [9]
@pytest.mark.parametrize(
    "text",
    [
        "分娩的时候出血很多，现在头晕",
        "剖宫产时出血很多，现在头晕",
        "生孩子的时候出了好多血，现在头晕",
        "分娩时出血很多，现在头晕",
    ],
)
def test_intrapartum_bleeding_flags(text):
    result = _red(text)
    assert "孕产期出血" in result["matches"], result


def test_past_intrapartum_not_current_pregnancy():
    result = _red("今天阴道出血", {"history": "3年前分娩时大出血"})
    assert "孕产期出血" not in result["matches"], result


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize("bare", ["吃药不规律", "去年打了疫苗", "吃了两年"])
def test_non_drug_clause_keeps_stop_attribution(bare):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{bare}，停了一年多了"},
    )
    assert result["level"] == "none", (bare, result)


@pytest.mark.parametrize(
    "middle",
    [
        "奥美拉唑吃了两年", "氯雷他定吃了两年", "左甲状腺素吃了两年",
        "降压药吃了两年", "去年吃中药调理",
    ],
)
def test_named_other_drug_still_blocks(middle):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{middle}，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], (middle, result)


# ---------------------------------------------------------------- [4]
def test_reminder_twin_move_and_delete_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "乙", "note": "",
             "due_at": "2027-02-15T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "丙", "note": "",
             "due_at": "2027-02-15T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.add_reminder("本地", "2027-03-01T09:00", profile="妈妈")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET created_at = ?"
                " WHERE title = '本地'",
                ("2026-01-02T08:00:00",),
            )
        store.import_data(backup)
        rows = _rem_rows(store)
        yi = next(r for r in rows if r[2] == "乙")
        bing = next(r for r in rows if r[2] == "丙")
        store.update_reminder(yi[0], profile="妈妈")
        store.delete_reminder(bing[0])
        store.import_data(backup)
        rows = _rem_rows(store)
        assert len([r for r in rows if r[2] == "丙"]) == 1, rows
        assert len([r for r in rows if r[2] == "乙"]) == 1, rows
        before = _rem_rows(store)
        store.import_data(backup)
        assert _rem_rows(store) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
def test_note_tie_non_last_row_restores_deleted(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"id": i, "profile": p, "title": "吃药", "note": f"N{i - 1}",
             "due_at": "2027-02-15T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED}
            for i, p in (
                (1, "妈妈"), (2, "爸爸"), (3, "爸爸"), (4, "妈妈"),
            )
        ]}
        store.add_reminder("本地", "2027-04-01T09:00", profile="妈妈")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET created_at = ?"
                " WHERE title = '本地'",
                ("2026-01-02T08:00:00",),
            )
        store.import_data(backup)
        rows = _rem_rows(store)
        ids = {r[4]: r[0] for r in rows if r[4]}
        store.delete_reminder(ids["N0"])
        store.delete_reminder(ids["N2"])
        store.update_reminder(ids["N1"], due_at="2027-04-01T09:00")
        store.update_reminder(ids["N3"], note="editedN3")
        store.import_data(backup)
        rows = _rem_rows(store)
        assert any(r[4] == "N0" for r in rows), rows
        assert any(r[3] == "2027-04-01T09:00" for r in rows), rows
        assert any(r[4] == "editedN3" for r in rows), rows
        before = _rem_rows(store)
        store.import_data(backup)
        assert _rem_rows(store) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_repeated_import_idempotent_after_rename_move_delete(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"reminders": [
            {"id": 1, "profile": "爸爸", "title": "吃药", "note": "N0",
             "due_at": "2027-03-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "吃药", "note": "N1",
             "due_at": "2027-02-15T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "title": "吃药", "note": "N2",
             "due_at": "2027-03-01T09:00", "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.add_reminder("本地", "2027-04-01T09:00", profile="妈妈")
        store.import_data(backup)
        rows = _rem_rows(store)
        ids = {r[4]: r[0] for r in rows if r[4]}
        store.update_reminder(ids["N0"], title="R吃药")
        store.update_reminder(ids["N1"], profile="爸爸")
        store.delete_reminder(ids["N2"])
        store.import_data(backup)
        after2 = _rem_rows(store)
        store.import_data(backup)
        assert _rem_rows(store) == after2
        store.import_data(backup)
        assert _rem_rows(store) == after2
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_restore_upcoming_adopts_orphan_reminder(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {})
        today = datetime.now().date()
        day = str(today + timedelta(days=10))
        store.import_data({
            "appointments": [{
                "id": 2, "profile": "妈妈", "title": "复诊：高血压",
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "cancelled", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "note": f"复诊 {day} 心内科 王医生",
                "due_at": str(today + timedelta(days=9)),
                "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(today - timedelta(days=5)),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 0, "followup_reminder_id": 1,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        client = TestClient(api_server.app)
        appointment = store.list_appointments(None, None)[0]
        resp = client.patch(
            f"/api/appointments/{int(appointment['id'])}",
            json={"status": "upcoming"},
        )
        assert resp.status_code == 200, resp.text
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1 and int(pending[0]["id"]) == 1, pending
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"]) == 1, visit
        client.delete("/api/visits/1")
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [6][11]
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


def _auth_env(monkeypatch, header):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r83-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", header)
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
    return calls


def test_uds_appended_address_prelock(monkeypatch):
    calls = _auth_env(monkeypatch, "x-forwarded-for")
    try:
        scopes = [
            _auth_scope(
                [(b"x-forwarded-for", b"198.51.100.9"),
                 (b"x-medix-token", b"bad")],
                None,
            )
            for _ in range(15)
        ]
        codes = [_run_auth(scope).status_code for scope in scopes]
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429] * 5, codes
        assert calls["n"] == 10, calls
        correct = _auth_scope(
            [(b"x-forwarded-for", b"198.51.100.9"),
             (b"x-medix-token", b"r83-token-0123456789")],
            None,
        )
        assert _run_auth(correct).status_code == 429
    finally:
        api_server._known_token_proofs.clear()


def test_malformed_forwarded_keeps_prelock(monkeypatch):
    calls = _auth_env(monkeypatch, "forwarded")
    try:
        scopes = [
            _auth_scope(
                [(b"forwarded", b'\", for=203.0.113.9'),
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


# ---------------------------------------------------------------- [17]
def test_family_attention_keeps_bp_warning(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {"age": "60"})
        store.set_metric_target("妈妈", "steps", 8000)
        day = datetime.now().strftime("%Y-%m-%d")
        store.add_metric("妈妈", "steps", 3000, measured_at=day + "T00:01")
        store.add_metric("妈妈", "bp", 165, 102, measured_at=day + "T00:02")
        client = TestClient(api_server.app)
        overview = client.get("/api/family/overview").json()
        mom = next(
            p for p in overview["profiles"] if p["name"] == "妈妈"
        )
        attention = mom.get("attention") or {}
        assert attention.get("label") == "血压偏高", attention
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
def test_dedupe_remainder_sets_restore_snapshot():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("question = remainder;")
    end = source.index("autoGrow();", start)
    block = source[start:end]
    assert "lastRestoredComposerText = remainder;" in block


# ---------------------------------------------------------------- [13][14]
def test_draft_owner_uses_similarity_for_edits():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function draftTextSimilarity(" in source
    start = source.index("const stillEditingOwnDraft")
    end = source.index("if (!stillEditingOwnDraft)", start)
    block = source[start:end]
    assert "draftTextSimilarity(previousDraft, els.input.value)" in block
    assert ">= 0.5" in block


# ---------------------------------------------------------------- [18]
def test_load_draft_keeps_owner_empty():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function loadDraft()")
    end = source.index("function ", start + 10)
    block = source[start:end]
    assert "owner !== ''" in block
