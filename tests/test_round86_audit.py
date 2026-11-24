# -*- coding: utf-8 -*-
"""第八十六轮审查修复的回归测试。"""
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
@pytest.mark.parametrize("brand", ["拜瑞妥", "泰毕全", "艾乐妥", "里先安"])
def test_doac_brand_head_trauma(brand):
    result = _red("头撞到门框", {"medications": f"{brand} 20mg 每日一次"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (brand, result)
    inline = _red(f"我在吃{brand}，今天头撞到门框")
    assert "抗凝治疗期间头部外伤" in inline["matches"], (brand, inline)


def test_anticoagulant_generic_control():
    result = _red("头撞到门框", {"medications": "利伐沙班 20mg"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "medication",
    ["硫唑嘌呤 50mg 每日一次", "环磷酰胺", "来氟米特", "阿达木单抗"],
)
def test_immunosuppressant_drug_names(medication):
    result = _red("发烧39度", {"medications": medication})
    assert "免疫功能低下伴发热" in result["matches"], (medication, result)


def test_corticosteroid_history_immunosuppression():
    result = _red("发烧39度", {"history": "长期服用泼尼松"})
    assert "免疫功能低下伴发热" in result["matches"], result


# ---------------------------------------------------------------- [3]
def test_cross_member_twin_moved_to_sibling_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_medication("妈妈", "LocalMed", "1片")
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
        store.update_medication(mom["id"], dose="9")
        store.delete_medication(dad["id"])
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        notes = sorted(r["note"] for r in rows if r["name"] == "B")
        assert "b" in notes, rows
        assert len([r for r in rows if r["dose"] == "9"]) == 1, rows
        before = sorted(
            (r["profile"], r["name"], r["dose"], r["note"]) for r in rows
        )
        assert store.import_data(backup)["medications"] == 0
        assert sorted(
            (r["profile"], r["name"], r["dose"], r["note"])
            for r in store.list_medications(None, limit=100)
        ) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [4]
def test_no_id_twin_edited_survivor_restores_deleted(tmp_path):
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
        mild_id = next(r["id"] for r in rows if r["note"] == "N1")
        severe_id = next(r["id"] for r in rows if r["note"] == "N0")
        store.delete_allergy(mild_id)
        store.update_allergy(severe_id, severity="mild", note="EDITED")
        store.import_data(backup)
        pairs = [
            (r["severity"], r["note"])
            for r in store.list_allergies(None, limit=100)
        ]
        # 无 id 孪生无法唯一归属时以数据完整为准：被删的 (mild, N1)
        # 与用户编辑后的 (mild, EDITED) 都必须存在；允许额外保留一条
        # 编辑前旧副本（第八十七轮放宽）
        assert ("mild", "N1") in pairs, pairs
        assert ("mild", "EDITED") in pairs, pairs
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "产后18个月，阴道出血",
        "产后12个月，阴道出血",
        "剖宫产术后18个月，阴道出血",
        "产后24个月，阴道出血",
    ],
)
def test_postpartum_months_past(text):
    result = _red(text)
    assert "孕产期出血" not in result["matches"], (text, result)


def test_postpartum_months_recent_control():
    result = _red("产后十个月，阴道出血")
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [6]
def test_english_stop_and_chemo_history():
    result = _red("头撞到门框", {"history": "warfarin was stopped 2 years ago"})
    assert result["level"] == "none", result
    result = _red(
        "发烧39度", {"history": "finished chemotherapy 5 years ago"}
    )
    assert result["level"] == "none", result


def test_english_restart_keeps_anticoagulation():
    result = _red(
        "头撞到门框",
        {"history": "warfarin stopped 2 years ago but restarted last month"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [7]
def test_no_id_reminders_edit_and_delete_restores(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"reminders": [
            {"profile": "妈妈", "title": "复查",
             "due_at": "2027-01-01T09:00", "note": "N", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "title": "吃药",
             "due_at": "2027-01-01T09:00", "note": "N", "done": 0,
             "advance_days": 0, "repeat_days": 0, "repeat_until": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(True, None)
        follow = next(r for r in rows if r["title"] == "复查")
        medicine = next(r for r in rows if r["title"] == "吃药")
        store.update_reminder(
            follow["id"], due_at="2027-05-01T09:00"
        )
        store.delete_reminder(medicine["id"])
        store.import_data(backup)
        rows = store.list_reminders(True, None)
        titles = sorted(r["title"] for r in rows)
        assert titles == ["吃药", "复查"], rows
        assert len([r for r in rows if r["title"] == "复查"]) == 1, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_no_id_medication_move_reimport_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"medications": [
            {"profile": "妈妈", "name": "B", "dose": "3",
             "frequency": "qd", "note": "a", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "爸爸", "name": "B", "dose": "3",
             "frequency": "qd", "note": "b", "active": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        mom = next(r for r in rows if r["profile"] == "妈妈")
        store.update_medication(mom["id"], profile="爸爸")
        assert store.import_data(backup)["medications"] == 0
        assert store.import_data(backup)["medications"] == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_appointment_profile_change_keeps_detached_link(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        day = str(datetime.now().date() + timedelta(days=5))
        store.import_data({
            "appointments": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "status": "upcoming", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
                "due_at": str(datetime.now().date() + timedelta(days=4)),
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
        appt = store.list_appointments(None, None)[0]
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?"
                " WHERE id = 1",
                (int(appt["id"]),),
            )
        store.update_appointment_partial(int(appt["id"]), profile="爸爸")
        visit = store.get_visit_record(1)
        assert int(visit["followup_appointment_id"]) == 0, visit
        assert int(visit["followup_reminder_id"]) == 1, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_import_stale_reminder_not_reattached(tmp_path):
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
                "note": f"复诊 {day1}",
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


# ---------------------------------------------------------------- [11]
def test_malformed_forwarded_ipv6_counts_in_global_budget(monkeypatch):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r86-ipv6-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(60, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    monkeypatch.setattr(api_server, "_AUTH_COMPARE_GATE_SECONDS", 0.0)
    api_server._known_token_proofs.clear()
    calls = {"n": 0}
    original = api_server.is_authorized

    def counting(request):
        calls["n"] += 1
        return original(request)

    monkeypatch.setattr(api_server, "is_authorized", counting)

    def scope(net):
        return {
            "type": "http", "method": "GET", "path": "/api/sessions",
            "headers": [(b"host", b"localhost"),
                        (b"forwarded", (
                            b'for=203.0.113.99;x=", for=2001:db8:'
                            + str(net).encode() + b"::a"
                        )),
                        (b"x-medix-token", b"bad")],
            "client": ("127.0.0.1", 5000), "server": ("test", 80),
            "scheme": "http", "query_string": b"",
        }

    def run(scope_obj):
        async def call_next(_request):
            return Response(status_code=200)

        return asyncio.run(
            api_server.require_access_token(Request(scope_obj), call_next)
        )

    try:
        for net in range(1, 7):
            for _ in range(15):
                run(scope(net))
        victim = run(scope(7))
        assert victim.status_code == 429, victim.status_code
        assert calls["n"] <= 75, calls["n"]
        global_hits = api_server.auth_failure_limiter_ipv6_global._hits.get(
            "ipv6", []
        )
        assert len(global_hits) == 60, len(global_hits)
    finally:
        api_server._known_token_proofs.clear()


# ---------------------------------------------------------------- [12]
def test_family_attention_uses_latest_outside_window(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {"age": "62"})
        store.save_profile("爸爸", {"age": "60"})
        day = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
        store.add_metric("妈妈", "bp", 175, 108, measured_at=day + "T00:02")
        store.add_metric(
            "爸爸", "glucose", 13, measured_at=day + "T00:03",
            context="random",
        )
        client = TestClient(api_server.app)
        overview = client.get("/api/family/overview").json()
        mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
        dad = next(p for p in overview["profiles"] if p["name"] == "爸爸")
        assert (mom.get("attention") or {}).get("label") == "血压偏高", mom
        assert (dad.get("attention") or {}).get("label") == "血糖偏高", dad
    finally:
        store.close()


# ---------------------------------------------------------------- [13]
def test_restore_composer_returns_inserted_flag():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function restoreComposer(question)")
    end = source.index("function preserveDraftProfile", start)
    block = source[start:end]
    assert "return false;" in block
    assert "return true;" in block
    # 第九十二轮起条件分多行书写（并追加合并形态判定），按多行
    # 片段统计三个失败/停止分支
    assert source.count(
        "restoredNow" + chr(10)
        + "        || composerText === questionText"
    ) >= 3


# ---------------------------------------------------------------- [14]
def test_birth_time_not_intrapartum():
    assert _red("出生的时候早产，最近阴道出血")["level"] == "none"
    assert _red("产生的时候他很紧张，今天阴道出血")["level"] == "none"


# ---------------------------------------------------------------- [15]
def test_anding_common_phrase_not_drug_clause():
    result = _red(
        "今天头撞到门框",
        {"history": "华法林吃了三年，最近睡眠安定多了，停了一年多了"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [16]
def test_import_preserves_local_detached_link(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        day = str(datetime.now().date() + timedelta(days=6))
        store.import_data({
            "reminders": [{
                "id": 1, "profile": "妈妈", "title": "复诊：高血压",
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
                "note": "由就诊记录 #1 自动创建", "owner_visit_id": 0,
                "status": "upcoming", "scheduled_at": day + "T09:00",
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈",
                "visit_date": str(
                    datetime.now().date() - timedelta(days=2)
                ),
                "diagnosis": "高血压", "next_followup_date": day,
                "followup_appointment_id": 2, "followup_reminder_id": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
        })
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"]) == 1, visit
        assert len(store.list_reminders(False, "妈妈")) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [17]
def test_family_daily_target_ignores_out_of_window(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("爸爸", {"age": "60"})
        store.set_metric_target("爸爸", "steps", 8000)
        day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
        store.add_metric("爸爸", "steps", 5000, measured_at=day + "T10:00")
        client = TestClient(api_server.app)
        overview = client.get("/api/family/overview").json()
        dad = next(p for p in overview["profiles"] if p["name"] == "爸爸")
        attention = dad.get("attention") or {}
        assert attention.get("label") != "指标未达标 1 项", attention
        metrics = dad.get("metrics") or []
        for item in metrics:
            if item.get("metric") == "steps":
                assert item.get("daily_judged_day") != day, item
    finally:
        store.close()


# ---------------------------------------------------------------- [18][19][20]
def test_abort_reconcile_cancels_second_after_consumed():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function scheduleAbortReconcile")
    end = source.index("async function send", start)
    block = source[start:end]
    assert "inflightReconciledThisRestore" in block
    assert "clearTimeout(secondTimer);" in block


def test_owner_empty_inflight_not_reconciled():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "!inflightRestoredOwnerSession\n      ||" not in source


def test_takeover_keeps_draft_containing_question():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert source.count("pendingDraft.includes(question)") >= 2
    assert source.count("? pendingDraft") >= 2


# ---------------------------------------------------------------- [21]
def test_daiwen_common_words_not_normalized():
    assert normalize_brand_drug_names("古代文化") == "古代文化"
    assert normalize_brand_drug_names("现代文明") == "现代文明"
    safety = check_medication_safety(
        "我平时喜欢研究古代文化，最近膝盖痛能吃布洛芬吗", {}
    )
    assert safety["level"] == "none", safety
