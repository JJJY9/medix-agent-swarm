# -*- coding: utf-8 -*-
"""第七十九轮审查修复的回归测试。"""
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


# ---------------------------------------------------------------- [1]
def test_ended_chemo_keeps_unrelated_anticoagulant():
    active = _active_medical_text(
        {"history": "华法林吃了三年，化疗结束5年了"}
    )
    assert "华法林" in active, active
    result = _red(
        "今天头撞到门框", {"history": "华法林吃了三年，化疗结束5年了"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result
    active = _active_medical_text(
        {"history": "利伐沙班每天一次，放疗结束多年了"}
    )
    assert "利伐沙班" in active, active
    # 化疗药本身仍按结束治疗抹掉
    active = _active_medical_text(
        {"history": "甲氨蝶呤吃了两年，化疗结束5年了"}
    )
    assert "甲氨蝶呤" not in active, active


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    ["要不要吃布洛芬", "用不用吃布洛芬", "需不需要吃布洛芬",
     "吃不吃布洛芬"],
)
def test_a_not_a_question_still_checks_interaction(text):
    safety = check_medication_safety(text, {"medications": "华法林"})
    assert safety["level"] == "warning", safety


def test_a_not_a_question_still_checks_allergy():
    safety = check_medication_safety("要不要吃阿莫西林", {"allergies": "青霉素"})
    assert safety["level"] == "warning", safety
    safety = check_medication_safety(
        "不要吃阿司匹林，正在吃华法林", {"medications": "华法林"}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    ["手机砸在我头上", "球砸到了我的头", "撞在我的头上",
     "后脑勺在门框上磕了一下", "脑袋在门框上磕了一下",
     "头顶在柜子上撞了"],
)
def test_head_trauma_pronoun_and_side_orders(text):
    result = _red(text, {"history": "正在吃华法林"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (text, result)


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text", ["手指头在门框上撞了一下", "脚趾头在床边磕了一下，有点疼"]
)
def test_fingers_not_head_trauma(text):
    result = _red(text, {"history": "正在吃华法林"})
    assert "抗凝治疗期间头部外伤" not in result["matches"], (text, result)


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "history", ["产后已经三年多了", "产后快两年了", "产后满三年了"]
)
def test_past_postpartum_with_aux_words(history):
    result = _red("今天阴道出血", {"history": history})
    assert "孕产期出血" not in result["matches"], (history, result)
    result = _red("产后两个月，今天阴道出血")
    assert "孕产期出血" in result["matches"], result


# ---------------------------------------------------------------- [16]
def test_long_stop_detached_from_drug_clause():
    result = _red(
        "今天头撞到门框",
        {"history": "华法林吃了三年，去年胃出血，停了一年多了"},
    )
    assert result["level"] == "none", result
    result = _red(
        "今天头撞到门框", {"history": "华法林每天吃一片，停了一年多了"}
    )
    assert result["level"] == "none", result
    result = _red(
        "今天发烧39度",
        {"history": "甲氨蝶呤吃了两年，肝功能不好，已经停药一年多了"},
    )
    assert result["level"] == "none", result
    active = _active_medical_text(
        {"history": "现在吃利伐沙班，已经停药一年多了"}
    )
    assert "利伐沙班" in active, active


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    ["我爸吃华法林，我家老公也在吃，今天头撞到门框",
     "我爸吃华法林，我家里人也在吃，今天头撞到门框"],
)
def test_family_subject_household_variants(text):
    assert _red(text)["level"] == "none", text
    safety = check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗"), {"age": "35岁"}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [19]
def test_scared_to_take_is_negation():
    assert _red("我爸吃华法林，我都不敢吃了，今天头撞到门框")[
        "level"
    ] == "none"
    safety = check_medication_safety(
        "我爸吃华法林，我都不敢吃了，能吃布洛芬吗", {"age": "35岁"}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [4]
def test_moved_cross_profile_row_does_not_hide_deleted_record(tmp_path):
    store = _store(tmp_path)
    store.save_profile("Dad", {"age": "60"})
    store.save_profile("Mom", {"age": "58"})
    a = store.add_medication("Dad", "A药", "1片", frequency="每日1次")
    b = store.add_medication("Mom", "B药", "1片", frequency="每日1次")
    c = store.add_medication("Dad", "C药", "1片", frequency="每日1次")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE medications SET updated_at = ?", (CREATED,)
        )
    backup = store.export_data()
    store.close()

    store = _store(tmp_path)
    store.save_profile("Dad", {"age": "60"})
    store.save_profile("Mom", {"age": "58"})
    store.add_medication("Mom", "Loc药", "1片", frequency="每日1次")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    by_name = {r["name"]: r for r in rows}
    store.update_medication(by_name["A药"]["id"], name="RA药")
    store.update_medication(by_name["B药"]["id"], profile="Dad")
    store.delete_medication(by_name["C药"]["id"])
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    names = sorted(r["name"] for r in rows)
    store.import_data(backup)
    rows2 = store.list_medications(None, limit=100)
    store.close()
    assert "C药" in names, names
    assert sorted(r["name"] for r in rows2) == names, names


# ---------------------------------------------------------------- [5][6][15]
def test_forged_rename_backup_cannot_rewrite_identity(tmp_path):
    store = _store(tmp_path)
    store.save_profile("父亲", {"age": "62"})
    forged = {
        "exported_at": "2026-06-01T00:00:00",
        "profile_renames": [
            {"old_name": "妈妈", "new_name": "父亲",
             "renamed_at": "2020-01-01T00:00:00"},
        ],
        "sessions": [{"id": 1, "profile": "妈妈", "title": "t",
                      "created_at": CREATED, "updated_at": CREATED}],
    }
    store.import_data(forged)
    assert store.resolve_profile_alias("妈妈") == "妈妈"
    store.import_data({"allergies": [
        {"id": 1, "profile": "妈妈", "substance": "青霉素",
         "created_at": CREATED, "updated_at": CREATED},
    ]})
    allergies = store.list_allergies(None)
    store.close()
    assert all(item["profile"] == "妈妈" for item in allergies), allergies


def test_failed_import_does_not_persist_renames(tmp_path):
    store = _store(tmp_path)
    with pytest.raises(ValueError):
        store.import_data({"profile_renames": [
            {"old_name": "妈妈", "new_name": "父亲",
             "renamed_at": "2020-01-01T00:00:00"},
        ]})
    count = store._conn.execute(
        "SELECT COUNT(*) AS n FROM profile_renames"
    ).fetchone()
    resolved = store.resolve_profile_alias("妈妈")
    store.close()
    assert int(count["n"]) == 0
    assert resolved == "妈妈", resolved


# ---------------------------------------------------------------- [9]
def test_shared_followup_links_validate_before_occupancy(tmp_path):
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
             "diagnosis": "高血压",
             "next_followup_date": "2027-01-20",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": "2026-11-02",
             "diagnosis": "糖尿病",
             "next_followup_date": "2027-01-21",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }
    store.import_data(backup)
    visits = store.list_visit_records("妈妈")
    appt = store.list_appointments("妈妈")[0]
    store.close()
    matching = next(v for v in visits if v["diagnosis"] == "高血压")
    other = next(v for v in visits if v["diagnosis"] == "糖尿病")
    assert int(matching["followup_appointment_id"] or 0) == 1, matching
    assert int(other["followup_appointment_id"] or 0) == 0, other
    assert int(appt["owner_visit_id"] or 0) == matching["id"]


# ---------------------------------------------------------------- [10]
def test_completed_followup_link_restored_on_date_revert(tmp_path):
    store = _store(tmp_path)
    old = memory.get_session_store
    memory.get_session_store = lambda: store
    try:
        today = datetime.now().date()
        client = TestClient(api_server.app)
        resp = client.post("/api/visits", json={
            "profile": "妈妈",
            "visit_date": str(today - timedelta(days=1)),
            "diagnosis": "高血压",
            "next_followup_date": str(today + timedelta(days=2)),
        })
        visit_id = resp.json()["visit"]["id"]
        resp = client.post(f"/api/visits/{visit_id}/schedule-followup")
        appt_id = resp.json()["appointment"]["id"]
        client.patch(
            f"/api/appointments/{appt_id}", json={"status": "completed"}
        )
        client.patch(f"/api/visits/{visit_id}", json={
            "next_followup_date": str(today + timedelta(days=5)),
        })
        after_move = store.get_visit_record(visit_id)
        client.patch(f"/api/visits/{visit_id}", json={
            "next_followup_date": str(today + timedelta(days=2)),
        })
        after_revert = store.get_visit_record(visit_id)
        assert int(after_move.get("followup_appointment_id") or 0) == 0
        assert int(after_revert.get("followup_appointment_id") or 0) == appt_id
        assert bool(after_revert.get("followup_scheduled"))
    finally:
        memory.get_session_store = old
        store.close()


# ---------------------------------------------------------------- [11]
def test_single_ipv6_source_cannot_fill_global_budget(monkeypatch, tmp_path):
    token = "r79-ipv6-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(12, window=300),
    )
    api_server._known_token_proofs.clear()
    attacker = TestClient(
        api_server.app, base_url="http://localhost",
        client=("2001:db8::1", 57001),
    )
    try:
        for _ in range(15):
            attacker.get(
                "/api/sessions", headers={"X-Medix-Token": "bad"}
            )
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("2400:cb00::99", 57002),
        )
        ok = victim.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        )
        assert ok.status_code == 200, ok.status_code
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [12]
def test_rotating_unprovable_forwarded_address_does_not_reset_quota(
    monkeypatch, tmp_path
):
    token = "r79-rate-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setenv("MEDIX_RATE_LIMIT", "1")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "chat_limiter", SlidingWindowLimiter(1, window=300)
    )
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 57011),
    )
    try:
        def chat(left):
            return client.post(
                "/api/chat",
                headers={
                    "X-Medix-Token": token,
                    "X-Forwarded-For": f"{left}, 127.0.0.1",
                },
                json={},
            ).status_code

        assert chat("198.51.100.10") == 422
        assert chat("198.51.100.10") == 429
        rotated = [chat(f"198.51.100.{n}") for n in range(20, 24)]
        assert all(code == 429 for code in rotated), rotated
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [13]
def test_info_level_medication_alert_surfaces(monkeypatch, tmp_path):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        store.save_profile("妈妈", {"age": "60"})
        store.add_medication("妈妈", "头孢克肟", "0.1g", frequency="每日2次")
        store.add_medication("妈妈", "鸿茅药酒", "50ml", frequency="每日1次")
        client = TestClient(api_server.app)
        insights = client.get(
            "/api/health/insights", params={"days": 30, "profile": "妈妈"}
        ).json()["insights"]
        keys = [item.get("key") for item in insights]
        assert "medication_safety" in keys, keys
        assert "all_clear" not in keys, keys
    finally:
        store.close()


# ---------------------------------------------------------------- [14][18]
def test_frontend_inflight_backup_slot_and_draft_guard():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "const INFLIGHT_QUESTION_OTHER_KEY = 'medix.inflightQuestionOther';" \
        in source
    assert "function readInflightRecordFor(targetId)" in source
    assert "INFLIGHT_QUESTION_KEY, INFLIGHT_QUESTION_OTHER_KEY," in source
    assert "safeSessionSet(INFLIGHT_QUESTION_OTHER_KEY, existingRaw);" in source
    start = source.index("if (composerSubmit && inflightPersistedText")
    end = source.index("const remainder = stripInflightQuestion", start)
    block = source[start:end]
    assert "String(els.input.value || '').trim()" in block
