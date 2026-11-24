# -*- coding: utf-8 -*-
"""第八十五轮审查修复的回归测试。"""
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
from core.medication_text import (
    clause_mentions_drug_clause, normalize_brand_drug_names,
)
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
@pytest.mark.parametrize(
    "text",
    [
        "我昨天看视频胸痛",
        "最近刷视频胸痛",
        "这两天刷视频胸痛",
        "看着视频就胸痛",
        "刷着视频就胸痛",
        "我看视频胸痛已经两天了",
    ],
)
def test_media_word_without_connector_kept(text):
    result = _red(text)
    assert result["level"] == "emergency", (text, result)


def test_education_article_still_filtered():
    assert _red("我看了一篇关于胸痛的文章")["level"] == "none"


# ---------------------------------------------------------------- [5]
@pytest.mark.parametrize(
    "text",
    [
        "看了一个视频后胸痛",
        "看了一段视频后胸痛",
        "刷了一条短视频就胸痛",
        "看了一个短视频时胸痛",
        "刷了两条短视频后胸痛",
    ],
)
def test_media_quantifier_with_number(text):
    result = _red(text)
    assert result["level"] == "emergency", (text, result)


# ---------------------------------------------------------------- [2][4]
@pytest.mark.parametrize(
    "text",
    [
        "我在吃欣康，能同时用西地那非吗",
        "我一直在吃波立维，能吃奥美拉唑吗",
        "我在吃格华止，明天要做增强CT",
        "我在吃立普妥，能吃克拉霉素吗",
    ],
)
def test_brand_name_interaction_warning(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_brand_name_context_medication_warning():
    safety = check_medication_safety(
        "我平时用药", {"medications": "代文；螺内酯"}
    )
    assert safety["level"] == "warning", safety


def test_brand_name_normalization():
    assert normalize_brand_drug_names("波立维") == "氯吡格雷"
    assert normalize_brand_drug_names("可定期复查") == "可定期复查"
    assert normalize_brand_drug_names("代文献") == "代文献"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "middle",
    [
        "呋塞米吃了两年",
        "格列美脲吃了两年",
        "硝酸甘油吃了两年",
        "螺内酯吃了两年",
    ],
)
def test_group_generic_middle_clause_blocks_stop(middle):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{middle}，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], (middle, result)


# ---------------------------------------------------------------- [8][13]
@pytest.mark.parametrize(
    "text", ["可定期复查", "可定量评估", "可定位检查"],
)
def test_keding_not_drug_in_common_phrase(text):
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


def test_keding_brand_still_detected():
    assert clause_mentions_drug_clause("可定吃了两年")
    assert normalize_brand_drug_names("可定") == "瑞舒伐他汀"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "没有停经，月经规律，今天阴道出血",
        "未停经，阴道出血",
        "停经史：无，阴道出血",
        "我停经两个月了，没有怀孕，今天阴道出血",
        "去年月经推迟过，今年正常，今天阴道出血",
        "停经有两年了，昨天阴道出血",
    ],
)
def test_amenorrhea_guards(text):
    result = _red(text)
    assert "孕产期出血" not in result["matches"], (text, result)


def test_recent_amenorrhea_still_flags():
    result = _red("停经45天，阴道出血")
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "生的时候出了好多血",
        "我生的时候出血了",
        "生的时候出血很多，现在头晕",
    ],
)
def test_bare_sheng_time_intrapartum(text):
    result = _red(text)
    assert "孕产期出血" in result["matches"], (text, result)


def test_peanut_time_not_intrapartum():
    assert _red("花生的时候他把牙崩了")["level"] == "none"


# ---------------------------------------------------------------- [3]
def test_same_origin_twin_stop_fields_restored(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "药A", "dose": "1片",
             "frequency": "每日一次", "started_at": "", "note": "已停药",
             "active": 0, "stopped_at": "2026-07-01",
             "stop_reason": "医生建议",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "药A", "dose": "1片",
             "frequency": "每日一次", "started_at": "", "note": "服用中",
             "active": 1, "stopped_at": "", "stop_reason": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        store.delete_medication(1)
        store.update_medication(
            2, note="EDIT", active=0,
            stopped_at="2026-11-01", stop_reason="医嘱",
        )
        store.import_data(backup)
        meds = store.list_medications(None, limit=100)
        assert len([m for m in meds if m["note"] == "已停药"]) == 1, meds
        assert len([m for m in meds if m["note"] == "EDIT"]) == 1, meds
        assert not [m for m in meds if m["note"] == "服用中"], meds
        before = store.list_medications(None, limit=100)
        assert store.import_data(backup)["medications"] == 0
        assert store.list_medications(None, limit=100) == before
    finally:
        store.close()


# ---------------------------------------------------------------- [9]
def test_no_id_backup_twin_restores_allergies(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        backup = {"allergies": [
            {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
             "noted_at": "2026-03-01", "reaction": "皮疹",
             "severity": "severe", "active": 1, "note": "",
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
             "noted_at": "2026-03-01", "reaction": "皮疹",
             "severity": "mild", "active": 1, "note": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_allergies(None, limit=100)
        severe = [r for r in rows if r["severity"] == "severe"]
        assert severe
        store.delete_allergy(severe[0]["id"])
        store.import_data(backup)
        rows = store.list_allergies(None, limit=100)
        assert len([r for r in rows if r["severity"] == "severe"]) == 1
        assert len([r for r in rows if r["severity"] == "mild"]) == 1
    finally:
        store.close()


# ---------------------------------------------------------------- [15]
def test_swapped_metric_profiles_reimport_idempotent(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        backup = {"health_metrics": [
            {"profile": "爸爸", "metric": "weight", "value1": 60,
             "measured_at": "2026-05-01T09:00", "context": "", "note": "M",
             "created_at": CREATED, "updated_at": CREATED},
            {"profile": "妈妈", "metric": "weight", "value1": 60,
             "measured_at": "2026-05-01T09:00", "context": "", "note": "M",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        ids = [m["id"] for m in store.list_metrics(None, limit=100)]
        store.update_metric(ids[0], profile="妈妈")
        store.update_metric(ids[1], profile="爸爸")
        assert store.import_data(backup)["metrics"] == 0
        assert store.import_data(backup)["metrics"] == 0
        assert len(store.list_metrics(None, limit=100)) == 2
    finally:
        store.close()


# ---------------------------------------------------------------- [16]
def test_completed_appointment_clears_shared_detached_reminder(
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
                "due_at": str(today + timedelta(days=4)),
                "appointment_id": 0, "done": 0,
                "created_at": CREATED, "updated_at": CREATED,
            }],
            "visit_records": [
                {"id": 1, "profile": "妈妈",
                 "visit_date": str(today - timedelta(days=10)),
                 "diagnosis": "高血压", "next_followup_date": day,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 1,
                 "created_at": CREATED, "updated_at": CREATED},
                {"id": 2, "profile": "妈妈",
                 "visit_date": str(today - timedelta(days=9)),
                 "diagnosis": "高血压", "next_followup_date": day,
                 "followup_appointment_id": 0,
                 "followup_reminder_id": 1,
                 "created_at": CREATED, "updated_at": CREATED},
            ],
        })
        client = TestClient(api_server.app)
        appointment = store.list_appointments(None, None)[0]
        resp = client.patch(
            f"/api/appointments/{int(appointment['id'])}",
            json={"status": "completed"},
        )
        assert resp.status_code == 200, resp.text
        assert store.list_reminders(True, "妈妈") == []
        for visit_id in (1, 2):
            visit = store.get_visit_record(visit_id)
            assert int(visit["followup_reminder_id"]) == 0, visit
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def _import_reminder_only_visit(store, day):
    store.import_data({
        "reminders": [{
            "id": 1, "profile": "妈妈", "title": "复诊：高血压",
            "note": f"复诊 {day}",
            "due_at": str(
                datetime.strptime(day, "%Y-%m-%d").date()
                - timedelta(days=1)
            ),
            "appointment_id": 0, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [{
            "id": 1, "profile": "妈妈", "diagnosis": "高血压",
            "visit_date": str(datetime.now().date() - timedelta(days=2)),
            "next_followup_date": day,
            "followup_appointment_id": 0, "followup_reminder_id": 1,
            "created_at": CREATED, "updated_at": CREATED,
        }],
    })


def test_schedule_merges_detached_reminder_with_appointment_own(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {})
        day = str(datetime.now().date() + timedelta(days=10))
        _import_reminder_only_visit(store, day)
        client = TestClient(api_server.app)
        appt = client.post("/api/appointments", json={
            "profile": "妈妈", "title": "复诊：高血压",
            "scheduled_at": day + "T09:00",
        }).json()["appointment"]
        client.post(f"/api/appointments/{appt['id']}/reminder", json={})
        resp = client.post("/api/visits/1/schedule-followup")
        assert resp.status_code == 200, resp.text
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        assert int(pending[0]["appointment_id"] or 0) == int(appt["id"])
        visit = store.get_visit_record(1)
        assert int(visit["followup_reminder_id"]) == int(pending[0]["id"])
    finally:
        store.close()


def test_schedule_with_tombstone_drops_detached_reminder(
    tmp_path, monkeypatch
):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {})
        day = str(datetime.now().date() + timedelta(days=10))
        _import_reminder_only_visit(store, day)
        client = TestClient(api_server.app)
        appt = client.post("/api/appointments", json={
            "profile": "妈妈", "title": "复诊：高血压",
            "scheduled_at": day + "T09:00",
        }).json()["appointment"]
        created = client.post(
            f"/api/appointments/{appt['id']}/reminder", json={}
        ).json()
        client.delete(f"/api/reminders/{created['reminder']['id']}")
        client.post("/api/visits/1/schedule-followup")
        assert store.list_reminders(False, "妈妈") == []
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_ipv6_global_budget_reached_across_five_prefixes(monkeypatch):
    token = "r85-ipv6-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
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

    def scope(ip):
        return {
            "type": "http", "method": "GET", "path": "/api/sessions",
            "headers": [(b"host", b"localhost"),
                        (b"x-medix-token", b"bad")],
            "client": (ip, 5000), "server": ("test", 80),
            "scheme": "http", "query_string": b"",
        }

    def run(scope_obj):
        async def call_next(_request):
            return Response(status_code=200)

        return asyncio.run(
            api_server.require_access_token(Request(scope_obj), call_next)
        )

    try:
        for net in range(1, 6):
            for _ in range(13):
                run(scope(f"2001:db8:{net}::9"))
        victim = run(scope("2400:cb00::99"))
        assert victim.status_code == 429, victim.status_code
        assert calls["n"] <= 70, calls["n"]
        global_hits = api_server.auth_failure_limiter_ipv6_global._hits.get(
            "ipv6", []
        )
        assert len(global_hits) == 60, len(global_hits)
    finally:
        api_server._known_token_proofs.clear()


# ---------------------------------------------------------------- [12][17]
def test_inflight_restored_text_scoped_to_session():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "let inflightRestoredOwnerSession = '';" in source
    assert "inflightRestoredOwnerSession = String(" in source
    start = source.index("if (expectedQuestion) {")
    end = source.index("let inflightReconciled", start)
    block = source[start:end]
    assert "inflightRestoredOwnerSession === expectedSession" in block
    # 第八十九轮起匹配与剥离使用同一段文本：会话归属仍由
    # restoredForSession 的 owner 校验守住（restoredForSession 只在
    # owner 匹配时取内存快照，否则回退 expectedQuestion）
    assert "inflightInputMatchesRestored(restoredForSession)" in block
    assert "matchedInflight" in block


def test_send_takeover_merges_pending_draft():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    count = source.count("const pendingDraft = String(els.input.value || '');")
    assert count >= 2, count
    assert source.count("`${question}\\n\\n${pendingDraft}`") >= 2
