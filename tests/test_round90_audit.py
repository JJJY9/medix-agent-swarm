# -*- coding: utf-8 -*-
"""第九十轮审查修复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"
OTHER = "2026-05-01T09:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    return TestClient(api_server.app), store


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _day(offset):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def _no_alert(result, word):
    return not any(
        word in (a.get("title") or "") for a in result.get("alerts") or []
    )


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "什么原因引起的胸痛怎么办",
        "什么原因引起的胸痛，需要去医院吗？",
        "胸痛见于什么病，持续两小时了",
        "我爸咯血见于什么病？",
        "母亲胸痛的原因是什么？",
    ],
)
def test_definition_question_with_context_kept(text):
    assert _red(text)["level"] == "emergency", text


@pytest.mark.parametrize(
    "text",
    [
        "什么病会引起胸痛？",
        "哪些疾病会导致咯血？",
        "呼吸困难见于什么病",
        "胸痛的原因有哪些",
        "咯血是什么病的症状？",
    ],
)
def test_pure_definition_question_filtered(text):
    assert _red(text)["level"] == "none", text


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "头部撞到门框。I have been off work for two months and I take"
        " warfarin daily.",
        "头部撞到门框。I have been off alcohol for two months and take"
        " warfarin.",
    ],
)
def test_been_off_non_drug_keeps_warfarin(text):
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_been_off_drug_is_long_stop():
    assert _red(
        "今天头撞到门框。I have been off warfarin for two years",
        {"age": "55岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [3][17]
def test_still_taking_my_warfarin_is_continuation():
    result = _red(
        "头部撞到门框。warfarin was stopped 2 years ago but I am still"
        " taking my warfarin.",
        {"age": "60岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


@pytest.mark.parametrize(
    "other_drug", ["methotrexate", "prednisone", "amlodipine"]
)
def test_still_taking_other_drug_not_warfarin(other_drug):
    result = _red(
        "今天头撞到门框。warfarin was stopped 2 years ago but I am still"
        f" taking {other_drug}.",
        {"age": "60岁"},
    )
    assert result["level"] == "none", result


def test_chemo_stopped_still_warfarin_not_immunosuppressed():
    result = _red(
        "今天发烧39度。chemotherapy was stopped 2 years ago but I am"
        " still taking warfarin.",
        {"age": "60岁"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "我以前吃华法林，现在改为一天一片，今天头撞到门框",
        "我以前吃华法林，现在一天一片，今天头撞到门框",
        "我以前吃华法林，现在每晚一片，今天头撞到门框",
        "我以前吃华法林，现在每日一次，每次一片，今天头撞到门框",
    ],
)
def test_dose_frequency_synonyms(text):
    result = _red(text, {"age": "40岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_dose_frequency_synonyms_medication_safety():
    result = check_medication_safety(
        "我以前吃华法林，现在改为一天一片，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [7]
def test_med_still_on_aspirin_not_warfarin():
    result = check_medication_safety(
        "warfarin was stopped 2 years ago but I am still on aspirin."
        " Can I take ibuprofen for pain?",
        {},
    )
    assert _no_alert(result, "华法林"), result


def test_med_still_taking_warfarin_warns():
    result = check_medication_safety(
        "warfarin was stopped 2 years ago but I am still taking warfarin."
        " Can I take ibuprofen?",
        {},
    )
    assert result["level"] == "warning", result


# ---------------------------------------------------------------- [12]
def test_trusted_proxy_without_headers_needs_source_cap(monkeypatch):
    from starlette.requests import Request

    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "192.0.2.77")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)

    def request_with(headers):
        return Request({
            "type": "http", "method": "GET", "path": "/api/sessions",
            "headers": [(b"host", b"localhost"), *headers],
            "client": ("192.0.2.77", 51000),
            "server": ("localhost", 80), "scheme": "http",
            "query_string": b"",
        })

    # 非本机对端（Docker/跨机已声明代理、开启信任但未声明对端）：
    # 无论是否带地址头都必须封顶（trust 开关不参与）
    assert api_server._needs_auth_source_cap(request_with([]), "192.0.2.77")
    assert api_server._needs_auth_source_cap(
        request_with([(b"x-forwarded-for", b"1.2.3.4")]), "192.0.2.77"
    )
    # 本机对端 + 未开启代理信任 + 无地址头：保持回环高阈值桶契约
    # （第四十五/四十六/六十五轮），单源上限不得让回环桶永不触发
    assert not api_server._needs_auth_source_cap(request_with([]), "127.0.0.1")
    assert not api_server._needs_auth_source_cap(
        request_with([]), "testclient"
    )
    # 开启信任代理且对端受信（含本机反代）：必须封顶（第七十五轮）
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    assert api_server._needs_auth_source_cap(request_with([]), "127.0.0.1")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)


# ---------------------------------------------------------------- [4]
def test_cross_member_twin_restore_keeps_owner(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.import_data({"medications": [{
            "id": 1, "profile": "爸爸", "name": "B", "dose": "100mg",
            "frequency": "qd", "note": "", "created_at": OTHER,
            "updated_at": OTHER,
        }]})
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "name": "A", "dose": "5mg",
             "frequency": "qd", "note": "",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        dad_a = next(m for m in rows if m["profile"] == "爸爸")
        store.delete_medication(int(dad_a["id"]))
        store.import_data(backup)
        rows = [
            (m["profile"], m["name"])
            for m in store.list_medications(None, limit=100)
        ]
        assert rows.count(("爸爸", "A")) == 1, rows
        assert rows.count(("妈妈", "A")) == 1, rows
        assert store.import_data(backup)["medications"] == 0
    finally:
        store.close()


# ---------------------------------------------------------------- [8]
def test_same_second_twin_dose_edit_no_old_copy(tmp_path):
    source = _store(tmp_path)
    try:
        source.save_profile("妈妈", {})
        source.add_medication(
            "妈妈", "维生素D", "1粒", frequency="qd", note="早饭后"
        )
        source.add_medication(
            "妈妈", "维生素D", "1粒", frequency="qd", note="晚饭后"
        )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = ?, updated_at = ?",
                (CREATED, CREATED),
            )
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.add_medication("妈妈", "钙片", "1片", frequency="qd")
        target.import_data(backup)
        rows = target.list_medications(None, limit=100)
        morning = next(m for m in rows if m["note"] == "早饭后")
        target.update_medication(int(morning["id"]), dose="2粒")
        target.import_data(backup)
        rows = [
            (m["name"], m["dose"], m["note"])
            for m in target.list_medications(None, limit=100)
        ]
        assert ("维生素D", "1粒", "早饭后") not in rows, rows
        assert ("维生素D", "2粒", "早饭后") in rows, rows
        assert ("维生素D", "1粒", "晚饭后") in rows, rows
    finally:
        target.close()


# ---------------------------------------------------------------- 共享提醒备份
def _shared_backup(v1_fa, v2_fa):
    twin_day = _day(6)
    return {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊A", "note": "",
             "owner_visit_id": 1, "status": "upcoming",
             "scheduled_at": twin_day + "T09:00",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "title": "复诊B", "note": "",
             "status": "upcoming", "scheduled_at": twin_day + "T10:00",
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [{
            "id": 701, "profile": "妈妈", "title": "复诊",
            "due_at": _day(5), "appointment_id": 0, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [
            {"id": 1, "profile": "妈妈", "visit_date": _day(-2),
             "diagnosis": "高血压", "next_followup_date": twin_day,
             "followup_appointment_id": v1_fa, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": _day(-2),
             "diagnosis": "高血压", "next_followup_date": twin_day,
             "followup_appointment_id": v2_fa, "followup_reminder_id": 701,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }


# ---------------------------------------------------------------- [9]
def test_delete_visit_keeps_shared_detached_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(1, 2))
        assert client.delete("/api/visits/2").status_code in (200, 204)
        pending = store.list_reminders(False, "妈妈")
        v1 = store.get_visit_record(1)
        assert len(pending) == 1, pending
        assert int(v1["followup_reminder_id"] or 0) == int(pending[0]["id"])
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_restore_does_not_adopt_shared_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(0, 2))
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        client.patch("/api/appointments/1", json={"status": "upcoming"})
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        assert any(
            int(x["id"]) == int(v2["followup_reminder_id"] or 0)
            for x in pending
        ), (pending, v2)
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        assert int(v2["followup_reminder_id"] or 0) != 0
        assert any(
            int(x["id"]) == int(v2["followup_reminder_id"] or 0)
            for x in pending
        ), (pending, v2)
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_completed_keeps_shared_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(0, 2))
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        client.patch("/api/appointments/1", json={"status": "completed"})
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        assert len(pending) == 1, pending
        assert int(v2["followup_reminder_id"] or 0) == int(pending[0]["id"])
    finally:
        store.close()


# ---------------------------------------------------------------- [14]
def test_detached_only_visit_counts_as_shared(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(1, 0))
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        assert len(pending) == 1, pending
        assert int(v2["followup_reminder_id"] or 0) == int(pending[0]["id"])
    finally:
        store.close()


# ---------------------------------------------------------------- [15][16]
def test_schedule_followup_merge_keeps_shared_reminder(
    tmp_path, monkeypatch
):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(1, 2))
        client.post("/api/appointments/1/reminder")
        assert client.post("/api/visits/1/schedule-followup").status_code == 200
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        alive = {int(x["id"]) for x in pending}
        assert 1 in alive, pending
        assert int(v2["followup_reminder_id"] or 0) == 1, (v2, pending)
    finally:
        store.close()


# ---------------------------------------------------------------- [5]
def test_open_session_rekeys_pending_reset():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    block_start = source.index("const pendingNewDraft = Boolean(")
    block = source[block_start:block_start + 2400]
    assert "safeSessionSet(PENDING_RESET_KEY, id);" in block
    assert "safeSessionSet(PENDING_RESET_AT_KEY" in block


# ---------------------------------------------------------------- [13]
def test_preserve_draft_profile_keeps_other_owner():
    source = (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")
    start = source.index("function preserveDraftProfile(profile, text) {")
    block = source[start:start + 900]
    assert "existingOwner" in block
    assert "existingOwner !== profile" in block
