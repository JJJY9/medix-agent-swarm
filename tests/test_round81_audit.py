# -*- coding: utf-8 -*-
"""第八十一轮审查修复的回归测试。"""
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

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


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _seed_twins(store, rows):
    for profile, name, dose, note in rows:
        store.add_medication(profile, name, dose, frequency="每日1次",
                             note=note)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE medications SET updated_at = ?", (CREATED,)
        )


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "华法林没吃完，二甲双胍吃了两年，停了一年多了，今天头撞到门框",
        "华法林没吃完，我爸吃阿司匹林，停了一年多了，今天头撞到门框",
    ],
)
def test_unfinished_dose_does_not_cross_drug_clauses(text):
    assert "抗凝治疗期间头部外伤" in _red(text)["matches"], text
    result = _red(
        "今天头撞到门框",
        {"history": "华法林没吃完，二甲双胍吃了两年，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_unfinished_dose_plain_long_stop_still_applies():
    result = _red(
        "今天头撞到门框",
        {"history": "华法林没吃完，最近睡眠不好，胃口也差，已经停药半年了"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text", ["以前没有过的胸痛", "之前没有过的胸痛", "以前没有过的呼吸困难"]
)
def test_previously_absent_symptom_is_positive(text):
    assert _red(text)["level"] == "emergency", text


def test_plain_absent_symptom_stays_negative():
    assert _red("以前没有出现过胸痛")["level"] == "none"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "我每天吃华法林，我爸以前也吃华法林，两年前停了，我能吃布洛芬吗",
        "我每天吃华法林，我爸以前也吃华法林，两年前就停药了，"
        "我能吃布洛芬吗",
    ],
)
def test_family_time_first_stop_does_not_stop_patient(text):
    assert check_medication_safety(text, {})["level"] == "warning"


# ---------------------------------------------------------------- [4]
def test_drift_moved_twin_does_not_swallow_deleted(tmp_path):
    store = _store(tmp_path)
    for name in ("Dad", "Mom", "Gran"):
        store.save_profile(name, {"age": "60"})
    _seed_twins(store, [
        ("Dad", "阿司匹林", "1片", "noteB"),
        ("Mom", "阿司匹林", "1片", "noteA"),
    ])
    backup = store.export_data()
    store.close()

    store = _store(tmp_path)
    for name in ("Dad", "Mom", "Gran"):
        store.save_profile(name, {"age": "60"})
    store.add_medication("Mom", "LocalMed", "1片", frequency="每日1次")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    dad = next(r for r in rows if r["note"] == "noteB")
    mom = next(r for r in rows if r["note"] == "noteA")
    store.update_medication(dad["id"], profile="Gran")
    store.delete_medication(mom["id"])
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    restored = [
        r for r in rows if r["profile"] == "Mom" and r["note"] == "noteA"
    ]
    store.import_data(backup)
    rows2 = store.list_medications(None, limit=100)
    store.close()
    assert len(restored) == 1, rows
    assert sorted((r["profile"], r["note"]) for r in rows2) \
        == sorted((r["profile"], r["note"]) for r in rows)


# ---------------------------------------------------------------- [5]
def test_english_allergy_does_not_swallow_drug_names():
    for text in (
        "I am allergic to penicillin and I take warfarin, can I take ibuprofen?",
        "I'm allergic to penicillin and take warfarin. Can I take ibuprofen?",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "三年前流产过，今天阴道出血",
        "两年前胎停过一次，今天阴道出血",
        "人流后一年，阴道出血",
        "药流后两年，阴道出血",
    ],
)
def test_past_miscarriage_is_history(text):
    assert "孕产期出血" not in _red(text)["matches"], text


def test_recent_miscarriage_still_flags():
    assert "孕产期出血" in _red("药流后一周，阴道流血")["matches"]
    result = _red("今天阴道出血", {"history": "三年前流产过"})
    assert "孕产期出血" not in result["matches"], result


# ---------------------------------------------------------------- [7]
def test_soft_moved_same_name_twin_not_duplicated(tmp_path):
    store = _store(tmp_path)
    for name in ("Dad", "Mom", "Gran"):
        store.save_profile(name, {"age": "60"})
    _seed_twins(store, [
        ("Dad", "阿司匹林", "2片", ""),
        ("Mom", "阿司匹林", "1片", ""),
    ])
    backup = store.export_data()
    store.close()

    store = _store(tmp_path)
    for name in ("Dad", "Mom", "Gran"):
        store.save_profile(name, {"age": "60"})
    store.add_medication("Mom", "LocalMed", "1片", frequency="每日1次")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    dad = next(
        r for r in rows if r["profile"] == "Dad" and r["dose"] == "2片"
    )
    store.update_medication(dad["id"], profile="Gran")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    store.close()
    assert [
        r for r in rows if r["profile"] == "Dad" and r["dose"] == "2片"
    ] == [], rows


# ---------------------------------------------------------------- [8]
def test_huge_backup_id_does_not_materialize_range(tmp_path):
    store = _store(tmp_path)
    store.save_profile("Mom", {"age": "60"})
    huge = 10 ** 9
    backup = {"medications": [
        {"id": 1, "profile": "Mom", "name": "A", "dose": "1片",
         "frequency": "每日1次", "note": "", "active": 1,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": huge, "profile": "Mom", "name": "H", "dose": "1片",
         "frequency": "每日1次", "note": "", "active": 1,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    h_row = next(r for r in rows if r["name"] == "H")
    a_row = next(r for r in rows if r["name"] == "A")
    store.delete_medication(h_row["id"])
    store.update_medication(a_row["id"], dose="2片")
    started = time.monotonic()
    store.import_data(backup)
    elapsed = time.monotonic() - started
    rows = store.list_medications(None, limit=100)
    store.close()
    assert elapsed < 10
    assert any(r["name"] == "H" for r in rows)


def _freeze_clock(store):
    tick = {"value": datetime.now() - timedelta(days=1)}

    def clock():
        tick["value"] += timedelta(seconds=1)
        return tick["value"].strftime("%Y-%m-%dT%H:%M:%S")

    store._now = clock


# ---------------------------------------------------------------- [9]
def test_imported_completed_appointment_keeps_owner(client_and_store):
    client, store = client_and_store
    _freeze_clock(store)
    today = datetime.now().date()
    day = str(today + timedelta(days=7))
    later = str(today + timedelta(days=17))
    visit = store.add_visit_record(
        "妈妈", visit_date=str(today - timedelta(days=5)),
        diagnosis="高血压", next_followup_date=day,
    )
    resp = client.post(f"/api/visits/{visit['id']}/schedule-followup")
    appt_id = resp.json()["appointment"]["id"]
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "completed"})
    backup = store.export_data()
    client.patch(f"/api/visits/{visit['id']}",
                 json={"next_followup_date": later})
    client.delete(f"/api/appointments/{appt_id}")
    store.import_data(backup)
    restored = next(
        a for a in store.list_appointments("妈妈", None, False, 100, 0)
        if a["status"] == "completed"
    )
    client.patch(f"/api/visits/{visit['id']}",
                 json={"next_followup_date": day})
    final = store.get_visit_record(visit["id"])
    assert int(restored["owner_visit_id"] or 0) == visit["id"], restored
    assert int(final.get("followup_appointment_id") or 0) == restored["id"]


# ---------------------------------------------------------------- [10]
def test_imported_cancelled_appointment_keeps_owner(client_and_store):
    client, store = client_and_store
    _freeze_clock(store)
    today = datetime.now().date()
    day = str(today + timedelta(days=7))
    later = str(today + timedelta(days=17))
    visit = store.add_visit_record(
        "妈妈", visit_date=str(today - timedelta(days=5)),
        diagnosis="高血压", next_followup_date=day,
    )
    resp = client.post(f"/api/visits/{visit['id']}/schedule-followup")
    appt_id = resp.json()["appointment"]["id"]
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "cancelled"})
    backup = store.export_data()
    client.patch(f"/api/visits/{visit['id']}",
                 json={"next_followup_date": later})
    client.delete(f"/api/appointments/{appt_id}")
    store.import_data(backup)
    restored = next(
        a for a in store.list_appointments("妈妈", None, False, 100, 0)
        if a["status"] == "cancelled"
    )
    status = client.patch(f"/api/appointments/{appt_id}",
                          json={"status": "upcoming"}).status_code
    upcoming = store.list_appointments("妈妈", "upcoming")
    assert int(restored["owner_visit_id"] or 0) == visit["id"], restored
    assert status >= 400 and upcoming == [], (status, upcoming)


# ---------------------------------------------------------------- [11]
def test_declared_proxy_not_hard_prelocked_without_trust(
    monkeypatch, tmp_path
):
    token = "r81-proxy-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")
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
    attacker = TestClient(
        api_server.app, base_url="http://localhost",
        client=("172.18.0.2", 58001),
    )
    try:
        codes = [
            attacker.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "bad",
                    "X-Forwarded-For": f"198.51.100.{index}",
                },
            ).status_code
            for index in range(12)
        ]
        assert codes == [401] * 12, codes
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("172.18.0.2", 58002),
        )
        ok = victim.get(
            "/api/sessions",
            headers={"X-Medix-Token": token,
                     "X-Forwarded-For": "198.51.100.200"},
        )
        assert ok.status_code == 200, ok.status_code
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [12]
def test_info_medication_attention_not_above_warnings(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.add_medication("妈妈", "头孢克肟", "0.1g", frequency="每日2次")
    store.add_medication("妈妈", "鸿茅药酒", "50ml", frequency="每日1次")
    store.save_exchange(
        "s-high", "胸痛怎么办",
        "风险等级：高危，请立即就医", profile="妈妈",
    )
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    assert (mom.get("attention") or {}).get("level") == "warning"
    store.delete_session("s-high")
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    attention = mom.get("attention") or {}
    assert attention.get("level") == "info"
    assert "用药安全" in str(attention.get("label") or "")


# ---------------------------------------------------------------- [13]
def test_frontend_reconciliation_requires_restored_snapshot():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function inflightInputMatchesRestored(restoredText)" in source
    assert source.count("inflightInputMatchesRestored(") >= 3
    start = source.index("function inflightInputMatchesRestored")
    end = source.index("function stripInflightQuestion", start)
    block = source[start:end]
    assert "current.startsWith(restored + '\\n')" in block


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "华法林吃了三年，胃不舒服，停了一年多了，今天头撞到门框",
        "华法林吃了三年，用着不舒服，停了一年多了，今天头撞到门框",
        "华法林吃了三年，胃不舒服打了点滴，停了一年多了，今天头撞到门框",
    ],
)
def test_bare_verb_clause_does_not_block_stop_scan(text):
    assert _red(text)["level"] == "none", text


def test_medication_category_clause_blocks_stop_scan():
    # 第八十二轮：「去年吃中药调理/降压药」是另一条用药分句，
    # 停药更可能指它，患者抗凝药必须保留
    for text in (
        "华法林吃了三年，去年吃中药调理，停了一年多了，今天头撞到门框",
        "华法林吃了三年，去年吃降压药，停了一年多了，今天头撞到门框",
    ):
        assert "抗凝治疗期间头部外伤" in _red(text)["matches"], text


# ---------------------------------------------------------------- [15]
@pytest.mark.parametrize(
    "text",
    [
        "在生产线工作，手被划伤出血",
        "我是生产线的工人，手被机器划伤流血",
        "我老公在生产线上班，手被划伤流血",
    ],
)
def test_production_line_is_not_postpartum(text):
    assert "孕产期出血" not in _red(text)["matches"], text
    assert "孕产期出血" in _red("刚生产完，阴道出血")["matches"]


# ---------------------------------------------------------------- [16]
@pytest.mark.parametrize(
    "text",
    [
        "Allergy to penicillin, can I take amoxicillin?",
        "I'm penicillin allergic, can I take amoxicillin?",
        "Penicillin allergy, can I take amoxicillin?",
    ],
)
def test_english_allergy_forms(text):
    assert check_medication_safety(text, {})["level"] == "warning", text


def test_english_allergy_negation():
    assert check_medication_safety(
        "I am not allergic to penicillin, can I take amoxicillin?", {}
    )["level"] == "none"
    assert check_medication_safety(
        "No known allergies, can I take ibuprofen?", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [17]
def test_individual_bp_target_suppresses_generic_warning(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.set_metric_target("妈妈", "bp", 160, 100)
    today = datetime.now()
    store.add_metric(
        "妈妈", "bp", 160, 95,
        measured_at=(today - timedelta(days=1)).strftime("%Y-%m-%dT08:00"),
    )
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()["insights"]
    assert [
        item for item in insights if item.get("key") == "metric_bp_single"
    ] == []
    family = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]
    assert [
        item for item in family if item.get("key") == "metric_bp_single"
    ] == []
    store.add_metric(
        "妈妈", "bp", 170, 110,
        measured_at=(today - timedelta(days=2)).strftime("%Y-%m-%dT08:00"),
    )
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()["insights"]
    assert any(
        item.get("key") == "metric_bp" and item.get("level") == "warning"
        for item in insights
    )


# ---------------------------------------------------------------- [18]
def test_frontend_backup_slot_cleared_by_session():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function clearOtherSessionInflight")
    end = source.index("function removeInflightRecord", start)
    block = source[start:end]
    assert "sameSession" in block
    assert "wantedQuestion.startsWith(recordQuestion)" in block


# ---------------------------------------------------------------- [19]
def test_frontend_creating_session_inflight_owner_empty():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "sessionId: (pendingSessionReset || creatingSessionPromise)" \
        in source


# ---------------------------------------------------------------- [20]
def test_export_omits_rename_history(tmp_path):
    store = _store(tmp_path)
    store.save_profile("张秀英", {"age": "60"})
    assert store.rename_profile("张秀英", "妈妈")
    backup = store.export_data()
    store.close()
    assert "profile_renames" not in backup
    assert "张秀英" not in str(backup)
