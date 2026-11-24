# -*- coding: utf-8 -*-
"""第七十三轮审查修复的回归测试。"""
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent

CREATED = "2026-06-01T08:00:00"
LATER = "2026-11-17T10:00:00"


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
def test_past_anticoagulant_with_recent_stop_keeps_head_trauma_red_flag():
    result = _red("我之前吃华法林，昨天停药了，今天头撞到门框")
    assert result["level"] == "emergency"
    assert "抗凝治疗期间头部外伤" in result["matches"]
    medication = check_medication_safety(
        "我之前吃华法林，昨天停药了，能吃布洛芬吗", {"age": "35岁"}
    )
    assert medication["level"] == "warning"


# ---------------------------------------------------------------- [2][6]
@pytest.mark.parametrize(
    "text",
    [
        "我和我爸一起吃华法林，能吃布洛芬吗",
        "我和我爸都吃华法林，能吃布洛芬吗",
        "我和我爸均服用华法林，能吃布洛芬吗",
        "我、我爸都吃华法林，能吃布洛芬吗",
        "I'm on warfarin, can I take ibuprofen?",
        "My father and I both take warfarin, can I take ibuprofen?",
    ],
)
def test_parallel_subject_keeps_patient_warfarin(text):
    result = check_medication_safety(text, {"age": "35岁"})
    assert result["level"] == "warning"
    assert any("华法林" in alert["title"] for alert in result["alerts"])


@pytest.mark.parametrize(
    "text",
    [
        "我和我爸都对青霉素过敏，能吃阿莫西林吗",
        "我和我爸都青霉素过敏，能吃头孢吗",
    ],
)
def test_parallel_subject_keeps_patient_allergy(text):
    result = check_medication_safety(text, {"age": "35岁"})
    assert result["level"] == "warning"
    assert any("青霉素" in alert["title"] for alert in result["alerts"])


def test_family_only_subject_still_filtered():
    result = check_medication_safety(
        "我想问一下我爸吃华法林，我能吃布洛芬吗", {"age": "35岁"}
    )
    assert result["level"] == "none"


# ---------------------------------------------------------------- [3][16]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么病，要紧吗",
        "胸痛是什么病？怎么办啊",
        "胸痛是什么病？我该怎么办",
        "胸痛是什么病？怎么缓解",
        "胸痛是什么病？怎么治疗",
        "胸痛是什么病？危险吗",
        "胸痛是什么病？需要看医生吗",
        "什么是胸痛，怎么办",
        "咯血是什么病，要紧吗",
    ],
)
def test_definition_question_with_worry_keeps_red_flag(text):
    result = _red(text)
    assert result["level"] == "emergency"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text,term",
    [
        ("无发热、咳嗽伴胸痛及呼吸困难", "胸痛"),
        ("无发热、乏力伴胸痛", "胸痛"),
        ("无发热、咳嗽伴咯血", "咯血"),
        ("无发热、咳嗽伴咯血及呼吸困难", "咯血"),
    ],
)
def test_enumeration_with_bare_ban_keeps_positive_term(text, term):
    result = _red(text)
    assert result["level"] == "emergency"
    assert term in result["matches"]


# ---------------------------------------------------------------- [17]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛剧烈网上查了说是心梗",
        "胸痛伴冷汗网上查了说是心梗",
        "胸痛大汗网上查了说是心梗",
        "咯血网上查了说是肺癌",
    ],
)
def test_course_modifier_before_quote_is_patient_complaint(text):
    result = _red(text)
    assert result["level"] == "emergency"


# ---------------------------------------------------------------- [4]
def test_import_appointment_edit_keep_and_delete_restore(tmp_path):
    src = _store(tmp_path)
    src._now = lambda: CREATED
    src.save_profile("妈妈", {})
    src.add_appointment("妈妈", "复诊", scheduled_at="2026-12-01T09:00")
    src.add_appointment("妈妈", "复诊", scheduled_at="2026-12-02T09:00")
    backup = src.export_data()
    src.close()

    tgt = _store(tmp_path)
    tgt._now = lambda: "2026-11-17T10:00:00"
    tgt.save_profile("妈妈", {})
    tgt.import_data(backup)
    rows = {
        row["scheduled_at"]: row["id"]
        for row in tgt.list_appointments("妈妈", None)
    }
    tgt.delete_appointment(rows["2026-12-02T09:00"])
    tgt.update_appointment_partial(
        rows["2026-12-01T09:00"], scheduled_at="2026-12-09T09:00"
    )
    tgt.import_data(backup)
    final = sorted(
        row["scheduled_at"] for row in tgt.list_appointments("妈妈", None)
        if row["status"] == "upcoming"
    )
    tgt.close()
    # 保留本地编辑后的 12-09，并补回被删的 12-02；不能复活 12-01 旧副本
    assert final == ["2026-12-02T09:00", "2026-12-09T09:00"]


# ---------------------------------------------------------------- [8]
def test_import_cross_member_moved_and_deleted_twin(tmp_path):
    src = _store(tmp_path)
    src._now = lambda: CREATED
    src.save_profile("妈妈", {})
    src.save_profile("爸爸", {})
    src.add_medication("妈妈", "氨氯地平", "5mg")
    src.add_medication("爸爸", "氨氯地平", "10mg")
    backup = src.export_data()
    src.close()

    tgt = _store(tmp_path)
    tgt._now = lambda: "2026-11-17T10:00:00"
    tgt.save_profile("妈妈", {})
    tgt.save_profile("爸爸", {})
    tgt.import_data(backup)
    mom = next(
        item for item in tgt.list_medications("妈妈")
        if item["name"] == "氨氯地平"
    )
    dad = next(
        item for item in tgt.list_medications("爸爸")
        if item["name"] == "氨氯地平"
    )
    tgt.delete_medication(mom["id"])
    tgt.update_medication(dad["id"], profile="妈妈", dose="7.5mg")
    tgt.import_data(backup)
    rows = sorted(
        (item["profile"], item["dose"])
        for item in tgt.list_medications(limit=200)
        if item["name"] == "氨氯地平"
    )
    tgt.close()
    # 改挂后的 7.5mg 留在妈妈名下，妈妈被删的 5mg 补回；
    # 爸爸的 10mg 旧副本不能复活
    assert rows == [("妈妈", "5mg"), ("妈妈", "7.5mg")]


# ---------------------------------------------------------------- [9]
def test_ensure_reminder_clears_deleted_tombstone(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-18T09:00"
    )
    reminder, created = store.ensure_reminder(
        "复诊提醒",
        "2026-12-17T09:00",
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created and reminder
    store.delete_reminder(reminder["id"])
    assert store.get_app_state(f"reminder_deleted_{appointment['id']}")
    again, created_again = store.ensure_reminder(
        "复诊提醒",
        "2026-12-17T09:00",
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created_again and again
    assert store.get_app_state(f"reminder_deleted_{appointment['id']}") == ""
    store.close()


# ---------------------------------------------------------------- [10]
def test_spoof_bucket_has_per_source_contribution_cap(monkeypatch, tmp_path):
    token = "r73-secret-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.delenv("MEDIX_PROXY_SINGLE_HOP", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(20, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    attacker = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56101),
    )
    victim = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56102),
    )
    spoofed = "192.168.1.50, 127.0.0.1"
    try:
        codes = []
        for _ in range(12):
            codes.append(attacker.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": spoofed,
                },
            ).status_code)
        # 单一来源最多贡献自身额度（10 次）到共享 spoof 桶，
        # 之后只按自身键限流，不再继续填桶
        assert codes[:10] == [401] * 10
        assert codes[10:] == [429, 429]
        ok = victim.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "X-Forwarded-For": "192.168.1.60, 127.0.0.1",
            },
        )
        assert ok.status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [11]
def test_reactivated_medication_clears_stop_date_and_counts_doses(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    started = (now - timedelta(days=60)).strftime("%Y-%m-%d")
    stopped = (now - timedelta(days=30)).strftime("%Y-%m-%d")
    medication = store.add_medication(
        "妈妈", "二甲双胍", "500mg", frequency="每日两次",
        active=False, started_at=started, stopped_at=stopped,
    )
    assert medication
    reactivated = store.update_medication(
        medication["id"], active=True,
        started_at=started, stopped_at=stopped,
    )
    assert reactivated is not None
    row = next(
        item for item in store.list_medications("妈妈")
        if item["id"] == medication["id"]
    )
    store.close()
    assert row["stopped_at"] == ""
    assert row["stop_reason"] == ""
    assert (row["expected_week_doses"] or 0) > 0


# ---------------------------------------------------------------- [5][12][13]
def test_frontend_adopted_session_profile_conflict():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "adoptedSharedSession" in source
    marker = "adoptedProfileConflict"
    assert marker in source
    region = source[source.index(marker):source.index(marker) + 900]
    assert "setPendingSessionReset(true)" in region


def test_frontend_preserve_draft_profile_writes_synchronously():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function preserveDraftProfile")
    body = source[start:start + 900]
    sync_index = body.index("draftProfileMemory = profile")
    timer_index = body.index("setTimeout")
    assert sync_index < timer_index


def test_frontend_abort_restores_composer_snapshot():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    marker = "announceChat('已停止生成')"
    start = source.index(marker)
    body = source[start:start + 1200]
    assert (
        "restoredNow || composerText === questionText"
    ) in body
    assert "lastRestoredComposerQuestion = lastRestoredComposerText" in body


# ---------------------------------------------------------------- [14]
def test_rename_profile_rejects_unprintable_name(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    with pytest.raises(ValueError):
        store.rename_profile("妈妈", "妈‍妈")
    assert store.list_profiles()[0]["name"] == "妈妈"
    store.close()


def test_api_rename_and_create_reject_unprintable_name(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    created = client.post(
        "/api/profiles", json={"name": "奶​奶", "context": {}}
    )
    assert created.status_code == 422
    renamed = client.patch(
        "/api/profiles/妈妈", json={"new_name": "妈‍妈"}
    )
    assert renamed.status_code == 422


# ---------------------------------------------------------------- [15]
def test_import_rejects_unprintable_profile_name(tmp_path):
    store = _store(tmp_path)
    result = store.import_data({
        "profiles": [
            {"name": "妈妈​", "context": {"age": "60"}},
            {"name": "爸爸", "context": {"age": "62"}},
        ],
    })
    names = [item["name"] for item in store.list_profiles()]
    store.close()
    assert "爸爸" in names
    assert not any("妈妈" in name and name != "妈妈" for name in names)
    assert result.get("invalid", 0) >= 1


# ---------------------------------------------------------------- [18]
def test_reminder_csv_reimport_after_complete_does_not_duplicate(tmp_path):
    store = _store(tmp_path)
    payload = {
        "reminders": [{
            "id": 1,
            "title": "吃药",
            "due_at": "2026-12-02T09:00",
            "note": "n",
            "profile": "妈妈",
            "advance_days": 1,
            "done": 0,
        }],
    }
    first = store.import_data(payload)
    assert first.get("reminders") == 1
    reminder = store.list_reminders(include_done=True)[0]
    assert store.complete_reminder(reminder["id"])
    second = store.import_data(payload)
    rows = store.list_reminders(include_done=True)
    store.close()
    assert second.get("reminders") == 0
    assert len(rows) == 1


# ---------------------------------------------------------------- [19]
def test_family_csv_next_dose_names_its_vaccine(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/vaccinations", json={
        "profile": "奶奶",
        "vaccine_name": "流感疫苗",
        "dose_number": 1,
        "vaccinated_at": (now - timedelta(days=60)).strftime("%Y-%m-%d"),
        "next_due": (now + timedelta(days=10)).strftime("%Y-%m-%d"),
    })
    client.post("/api/vaccinations", json={
        "profile": "奶奶",
        "vaccine_name": "乙肝疫苗",
        "dose_number": 2,
        "vaccinated_at": (now - timedelta(days=1)).strftime("%Y-%m-%d"),
        "next_due": (now + timedelta(days=180)).strftime("%Y-%m-%d"),
    })
    export = client.get("/api/family/overview/export.csv")
    text = export.content.decode("utf-8-sig")
    row = next(
        line for line in text.splitlines() if line.startswith("奶奶")
    )
    # 最近接种是乙肝，但下一剂属于流感疫苗：必须带上疫苗名
    assert "下一剂 流感疫苗" in row


# ---------------------------------------------------------------- [20]
def test_care_item_done_counts_as_recorded_today(client_and_store):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "爸爸", "context": {}})
    created = client.post("/api/care-items", json={
        "profile": "爸爸", "title": "复查血糖", "interval_days": 30,
    })
    care = created.json()["care_item"]
    assert client.post(
        f"/api/care-items/{care['id']}/done", json={}
    ).status_code == 200
    today = datetime.now().strftime("%Y-%m-%d")
    assert today in store.recorded_days("爸爸", 3650)
    overview = client.get("/api/family/overview").json()
    dad = next(
        item for item in overview["profiles"] if item["name"] == "爸爸"
    )
    assert dad["recorded_today"] is True


# ---------------------------------------------------------------- [21]
def test_metric_target_and_usage_reject_fake_profile_names(tmp_path):
    store = _store(tmp_path)
    assert store.set_metric_target("../evil", "bp", 140, 90) is None
    store.record_usage_event(
        "s1", "妈妈​", {"prompt_tokens": 10}, 1
    )
    names = store.list_used_profile_names()
    store.close()
    assert "../evil" not in names
    assert not any("​" in name for name in names)


def test_api_rejects_fake_profile_names(client_and_store):
    client, _store = client_and_store
    target = client.put("/api/metric-targets", json={
        "profile": "../evil", "metric": "bp", "value1": 140, "value2": 90,
    })
    assert target.status_code == 422
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        api_server.ChatRequest(
            question="胸痛", patient_profile="妈妈​"
        )
