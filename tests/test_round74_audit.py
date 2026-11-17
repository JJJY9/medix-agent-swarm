# -*- coding: utf-8 -*-
"""第七十四轮审查修复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
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


def _red(text):
    return detect_red_flags(patient_complaint_text(text), {})


# ---------------------------------------------------------------- [1]
def test_family_clause_with_patient_continuation_keeps_drug():
    assert _red("我爸吃华法林，我也在吃，今天头撞到门框")["level"] == (
        "emergency"
    )
    result = check_medication_safety(
        "我爸吃华法林，我也在吃，能吃布洛芬吗", {"age": "35岁"}
    )
    assert result["level"] == "warning"
    assert any("华法林" in alert["title"] for alert in result["alerts"])
    allergy = check_medication_safety(
        "我妈对青霉素过敏，我也过敏，能吃阿莫西林吗", {"age": "35岁"}
    )
    assert allergy["level"] == "warning"
    assert any("青霉素" in alert["title"] for alert in allergy["alerts"])


def test_family_only_clause_without_continuation_still_filtered():
    assert check_medication_safety(
        "我爸吃华法林，我能吃布洛芬吗", {"age": "35岁"}
    )["level"] == "none"
    assert check_medication_safety(
        "我爸吃华法林，我也在吃阿司匹林，能吃布洛芬吗", {"age": "35岁"}
    )["level"] == "warning"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "我胸口痛网上查了怎么办",
        "宝宝喘不上气网上查了怎么办",
        "孩子咯血看了文章",
        "小孩呼吸困难网上查了",
    ],
)
def test_compound_symptom_before_quote_is_patient_complaint(text):
    assert _red(text)["level"] == "emergency"


def test_education_lead_still_discarded():
    for text in (
        "我在网上看到胸痛要重视",
        "我最近在网上看到胸痛要马上就医",
        "我看了篇文章说偶尔胸痛没事",
        "网上说胸痛是心梗，我有点头晕",
    ):
        assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text,term",
    [
        ("服用华法林期间不吃辛辣，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("吃华法林不吃菠菜，今天摔了一跤磕到头", "抗凝治疗期间头部外伤"),
        ("吃利伐沙班期间不吃柚子，今天摔到头", "抗凝治疗期间头部外伤"),
        ("服用甲氨蝶呤期间不吃辛辣，今天发烧了", "免疫功能低下伴发热"),
    ],
)
def test_diet_restriction_keeps_drug_context(text, term):
    result = _red(text)
    assert result["level"] == "emergency"
    assert term in result["matches"]


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "孕妇阴道有点出血是什么病",
        "孕早期有点出血是什么病",
        "孕早期少量出血是什么病",
        "怀孕有点流血是什么病",
    ],
)
def test_pregnancy_bleeding_definition_with_quantifier(text):
    result = _red(text)
    assert result["level"] == "emergency"
    assert "孕产期出血" in result["matches"]


# ---------------------------------------------------------------- [5]
def _appointment_rows(store):
    return [
        (row["profile"], row["title"], row["scheduled_at"])
        for row in store.list_appointments(None, None)
    ]


def test_moved_and_edited_appointment_with_drift_restores_deleted(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("爸爸", {"age": "62"})
    store.add_appointment("妈妈", "口腔科0", scheduled_at="2026-12-01T09:00")
    store.add_appointment("妈妈", "口腔科1", scheduled_at="2026-12-01T10:00")
    backup = {"appointments": [
        {"id": 1, "profile": "妈妈", "title": "复诊", "note": "",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "title": "体检", "note": "",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_appointments(None, None)
    follow = next(r for r in rows if r["title"] == "复诊")
    checkup = next(r for r in rows if r["title"] == "体检")
    store.update_appointment_partial(follow["id"], profile="爸爸")
    store.delete_appointment(checkup["id"])
    store.update_appointment_partial(
        follow["id"], scheduled_at="2026-12-09T09:00"
    )
    store.import_data(backup)
    final = _appointment_rows(store)
    store.close()
    assert ("爸爸", "体检", "2026-12-02T09:00") in final, final
    assert any(
        profile == "爸爸" and title == "复诊"
        and at.startswith("2026-12-09")
        for profile, title, at in final
    ), final


def test_moved_and_edited_symptom_restores_deleted_cross_member(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("爸爸", {"age": "62"})
    backup = {"symptoms": [
        {"id": 1, "profile": "妈妈", "symptom": "咳嗽", "severity": 3,
         "note": "", "recorded_at": "2026-05-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "symptom": "发热", "severity": 4,
         "note": "", "recorded_at": "2026-05-02T09:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_symptoms(None, 100)
    cough = next(r for r in rows if r["symptom"] == "咳嗽")
    fever = next(r for r in rows if r["symptom"] == "发热")
    store.delete_symptom(cough["id"])
    store.update_symptom_partial(
        fever["id"], profile="妈妈", recorded_at="2026-05-29T09:00"
    )
    store.import_data(backup)
    final = [
        (row["profile"], row["symptom"], row["recorded_at"])
        for row in store.list_symptoms(None, 100)
    ]
    store.close()
    assert any(item[1] == "咳嗽" for item in final), final
    assert any(
        item[0] == "爸爸" and item[1] == "发热"
        and item[2].startswith("2026-05-02")
        for item in final
    ), final


# ---------------------------------------------------------------- [6][12]
def test_frontend_adopted_marker_survives_refresh():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "ADOPTED_SESSION_KEY" in source
    assert "safeSessionSet(ADOPTED_SESSION_KEY, sessionId)" in source
    marker = "const adoptedSession = ("
    assert marker in source
    region = source[source.index(marker):source.index(marker) + 700]
    assert "safeSessionGet(ADOPTED_SESSION_KEY) === sessionId" in region
    assert "!adoptedStoredProfile" in source


def test_frontend_reconcile_clears_inflight_guard():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert source.count("clearInflightPersisted();") >= 4


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛伴气短网上查了说是心梗",
        "胸痛伴左臂麻木网上查了说是心梗",
        "胸口痛网上查了说是心梗",
    ],
)
def test_red_flag_with_companion_symptom_before_quote(text):
    assert _red(text)["level"] == "emergency"


# ---------------------------------------------------------------- [8]
def test_reversed_parallel_and_english_family_markers():
    for text in (
        "我爸和我都对青霉素过敏，能吃阿莫西林吗",
        "My mom and I both take warfarin, can I take ibuprofen?",
        "My dad and I both take warfarin, can I take ibuprofen?",
    ):
        result = check_medication_safety(text, {"age": "35岁"})
        assert result["level"] == "warning", text
    # 家属之间并列不能算患者本人
    assert check_medication_safety(
        "我爸和我妈都吃华法林，我能吃布洛芬吗", {"age": "35岁"}
    )["level"] == "none"


# ---------------------------------------------------------------- [13]
def test_english_self_use_and_question_frame():
    for text in (
        "My father takes aspirin and I'm on warfarin, can I take ibuprofen?",
        "My father takes aspirin and I am on warfarin, can I take ibuprofen?",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    assert check_medication_safety(
        "My father takes warfarin can I take ibuprofen?", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [9]
def test_gate_source_uses_provable_peer_for_spoofed_headers(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r74-secret-token")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    sources = []

    async def fake_enter(key, source=""):
        sources.append(source)
        return None

    monkeypatch.setattr(api_server, "_auth_compare_enter", fake_enter)
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56201),
    )
    try:
        for index in (1, 2, 3):
            client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "bad",
                    "X-Forwarded-For": f"10.9.0.{index}, 127.0.0.1",
                },
            )
    finally:
        store.close()
    # 伪造的左项地址不能换来不同的单源槽位
    assert sources
    assert set(sources) == {api_server._peer_key("127.0.0.1")}, sources


# ---------------------------------------------------------------- [10]
def test_ipv6_global_budget_recheck_after_gate(monkeypatch):
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(1, window=300),
    )
    api_server.auth_failure_limiter_ipv6_global.check("ipv6")
    request = Request({
        "type": "http", "method": "GET", "path": "/api/sessions",
        "headers": [], "client": ("2001:db8::1", 1234),
        "server": ("testserver", 80), "scheme": "http",
        "query_string": b"",
    })
    assert api_server._auth_global_budget_retry(request) > 0
    ipv4_request = Request({
        "type": "http", "method": "GET", "path": "/api/sessions",
        "headers": [], "client": ("203.0.113.9", 1234),
        "server": ("testserver", 80), "scheme": "http",
        "query_string": b"",
    })
    assert api_server._auth_global_budget_retry(ipv4_request) == 0


# ---------------------------------------------------------------- [14]
def test_spoof_cap_keyed_by_provable_peer(monkeypatch, tmp_path):
    token = "r74-secret-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
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
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56202),
    )
    try:
        codes = []
        for index in range(1, 13):
            codes.append(client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": f"10.9.1.{index}, 127.0.0.1",
                },
            ).status_code)
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429, 429], codes
        ok = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "X-Forwarded-For": "10.9.2.9, 127.0.0.1",
            },
        )
        assert ok.status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [11]
def test_vaccine_next_dose_number_semantics(client_and_store):
    client, store = client_and_store
    store.save_profile("奶奶", {"age": "65"})
    today = datetime.now()
    vaccination = store.add_vaccination(
        "奶奶", "乙肝疫苗", dose_number=2,
        vaccinated_at=(today - timedelta(days=5)).strftime("%Y-%m-%d"),
        next_due=(today + timedelta(days=5)).strftime("%Y-%m-%d"),
    )
    reminder = client.post(
        f"/api/vaccinations/{vaccination['id']}/reminder"
    )
    assert reminder.status_code == 200
    titles = [
        item["title"]
        for item in store.list_reminders(False, "奶奶")
    ]
    assert any("第3剂" in title for title in titles), titles
    weekly = client.get("/api/reports/weekly", params={"days": 7}).json()
    upcoming = weekly["vaccinations"]["upcoming"]
    assert upcoming and upcoming[0]["next_dose_number"] == 3
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "奶奶"}
    ).json()
    vaccine = next(
        item for item in insights["insights"]
        if item["key"] == "vaccine_upcoming"
    )
    assert "第 3 剂" in vaccine["detail"]


# ---------------------------------------------------------------- [15]
def test_reschedule_after_reminder_delete_does_not_revive(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    today = datetime.now()
    visit = store.add_visit_record(
        "妈妈", visit_date=(today - timedelta(days=10)).strftime("%Y-%m-%d"),
        diagnosis="高血压",
        next_followup_date=(today + timedelta(days=30)).strftime("%Y-%m-%d"),
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压",
        scheduled_at=(today + timedelta(days=30)).strftime("%Y-%m-%d")
        + "T09:00",
    )
    first = store.add_reminder(
        "复诊提醒",
        (today + timedelta(days=29)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], first["id"]
    )
    assert store.complete_reminder(first["id"])
    second, created = store.ensure_reminder(
        "复诊：高血压",
        (today + timedelta(days=29)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created and second
    assert store.delete_reminder(second["id"])
    store.update_appointment_partial(
        appointment["id"],
        scheduled_at=(today + timedelta(days=35)).strftime("%Y-%m-%d")
        + "T09:00",
    )
    pending = [
        item for item in store.list_reminders(False, "妈妈")
        if int(item.get("appointment_id") or 0) == appointment["id"]
    ]
    store.close()
    assert pending == []


# ---------------------------------------------------------------- [16]
def test_care_done_counts_as_family_last_activity(client_and_store):
    client, store = client_and_store
    store.save_profile("爸爸", {"age": "62"})
    care = store.add_care_item("爸爸", "复查血糖", interval_days=30)
    assert store.mark_care_item_done(care["id"])
    overview = client.get("/api/family/overview", params={"days": 7}).json()
    dad = next(
        item for item in overview["profiles"] if item["name"] == "爸爸"
    )
    today = datetime.now().strftime("%Y-%m-%d")
    assert str(dad.get("last_activity") or "").startswith(today)
