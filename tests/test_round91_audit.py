# -*- coding: utf-8 -*-
"""第九十一轮审查修复的回归测试。"""
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


# ---------------------------------------------------------------- [1][2]
@pytest.mark.parametrize(
    "text,context,label",
    [
        (
            "今天头部撞到门框。chemotherapy was stopped 2 years ago but"
            " I am still taking warfarin.",
            {"age": "40岁"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "发烧39度。warfarin was stopped 2 years ago but I am still"
            " taking methotrexate.",
            {"age": "40岁"},
            "免疫功能低下伴发热",
        ),
        (
            "今天头撞到门框。aspirin was stopped 2 years ago but I am"
            " still taking warfarin.",
            {"age": "55岁"},
            "抗凝治疗期间头部外伤",
        ),
        (
            "今天发烧39度。warfarin was stopped 2 years ago but I am"
            " still taking prednisone.",
            {"age": "60岁"},
            "免疫功能低下伴发热",
        ),
    ],
)
def test_stop_clause_keeps_other_active_drug(text, context, label):
    assert label in _red(text, context)["matches"], text


def test_stop_clause_does_not_revive_stopped_drug():
    result = _red(
        "今天头撞到门框。warfarin was stopped 2 years ago but I am"
        " still taking methotrexate.",
        {"age": "60岁"},
    )
    assert "抗凝治疗期间头部外伤" not in result["matches"], result
    result = _red(
        "今天发烧39度。chemotherapy was stopped 2 years ago but I am"
        " still taking warfarin.",
        {"age": "60岁"},
    )
    assert "免疫功能低下伴发热" not in result["matches"], result


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "今天头部撞到门框。I have been off my warfarin for two years.",
        "今天头部撞到门框。I have been off the warfarin for two years",
        "今天头部撞到门框。have been off of my warfarin for 2 years",
        "今天头部撞到门框。I have been off warfarin for two years",
    ],
)
def test_been_off_with_determiner(text):
    assert _red(text, {"age": "40岁"})["level"] == "none", text


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "什么病会引起胸痛？最近在准备考试",
        "胸痛见于什么病？这几天在减肥",
        "咯血见于什么病？最近工作压力大",
        "胸痛见于什么病？今天门诊人很多",
    ],
)
def test_definition_question_unrelated_follow_clause(text):
    assert _red(text)["level"] == "none", text


def test_definition_question_with_duration_kept():
    assert _red("胸痛见于什么病，持续两小时了")["level"] == "emergency"
    assert _red("什么原因引起的胸痛，需要去医院吗？")["level"] == "emergency"


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "今天头撞到门框。I stopped drinking alcohol two years ago and"
        " take warfarin daily.",
        "今天头撞到门框。I quit smoking two years ago and take warfarin"
        " daily.",
    ],
)
def test_stopped_habit_keeps_active_drug(text):
    result = _red(text, {"age": "55岁"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_stopped_drug_still_stop():
    assert _red(
        "今天头撞到门框。warfarin was stopped 2 years ago", {"age": "55岁"}
    )["level"] == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "tail",
    [
        "and I take warfarin daily.",
        "and I take warfarin",
        "and I am on warfarin.",
    ],
)
def test_resume_same_drug_without_still(tail):
    result = _red(
        "今天头撞到门框。warfarin was stopped 2 years ago " + tail,
        {"age": "55岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], (tail, result)


def test_resume_other_drug_not_continuation():
    assert _red(
        "今天头撞到门框。warfarin was stopped 2 years ago and I take"
        " amlodipine daily.",
        {"age": "55岁"},
    )["level"] == "none"


# ---------------------------------------------------------------- [8]
def test_med_still_on_aspirin_after_comma():
    result = check_medication_safety(
        "warfarin was stopped 2 years ago, but I am still on aspirin."
        " Can I take ibuprofen?",
        {},
    )
    assert _no_alert(result, "华法林"), result


# ---------------------------------------------------------------- [9]
def test_id_collision_does_not_hide_local_twin(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        store.add_medication("妈妈", "Decoy", "1", frequency="qd")
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n1",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 3, "profile": "爸爸", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n3",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 4, "profile": "爸爸", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n4",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        n1 = next(m for m in rows if m["note"] == "n1")
        store.update_medication(int(n1["id"]), dose="9mg")
        store.import_data(backup)
        rows = [
            (m["profile"], m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
            if m["name"] == "B"
        ]
        assert ("妈妈", "2mg", "n1") not in rows, rows
        assert ("妈妈", "9mg", "n1") in rows, rows
        assert len([r for r in rows if r[0] == "妈妈"]) == 2, rows
        assert len([r for r in rows if r[0] == "爸爸"]) == 2, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [10]
def test_dose_edit_with_deleted_twin_restores_without_copy(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.add_medication("妈妈", "Decoy", "1", frequency="qd")
        backup = {"medications": [
            {"id": 1, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n2",
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "name": "B", "dose": "2mg",
             "frequency": "qd", "note": "n3",
             "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_medications(None, limit=100)
        n2 = next(m for m in rows if m["note"] == "n2")
        n3 = next(m for m in rows if m["note"] == "n3")
        store.update_medication(int(n2["id"]), dose="9mg")
        store.delete_medication(int(n3["id"]))
        assert store.import_data(backup)["medications"] == 1
        rows = [
            (m["dose"], m["note"])
            for m in store.list_medications(None, limit=100)
            if m["name"] == "B"
        ]
        assert ("2mg", "n2") not in rows, rows
        assert ("9mg", "n2") in rows, rows
        assert ("2mg", "n3") in rows, rows
    finally:
        store.close()


# ---------------------------------------------------------------- [11]
def test_cross_member_orphan_not_claimed_before_owner(tmp_path):
    store = _store(tmp_path)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        for index in range(4):
            store.add_reminder(f"seed{index}", _day(3 + index), profile="妈妈")
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊",
             "due_at": "2026-07-10", "note": "n1", "appointment_id": 0,
             "done": 0, "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "爸爸", "title": "体检",
             "due_at": "2026-07-17", "note": "n2", "appointment_id": 0,
             "done": 0, "created_at": CREATED, "updated_at": CREATED},
        ]}
        store.import_data(backup)
        rows = store.list_reminders(False)
        mom = next(r for r in rows if r["title"] == "复诊")
        dad = next(r for r in rows if r["title"] == "体检")
        store.update_reminder(int(mom["id"]), due_at="2027-01-05")
        store.update_reminder(int(dad["id"]), due_at="2027-01-08")
        assert store.import_data(backup)["reminders"] == 0
        rows = [
            (r["profile"], r["title"], r["due_at"])
            for r in store.list_reminders(False)
        ]
        dad_rows = [r for r in rows if r[1] == "体检"]
        assert len(dad_rows) == 1, dad_rows
        assert any(r[2] == "2027-01-08" for r in dad_rows), dad_rows
        assert any(
            r[2] == "2027-01-05" for r in rows if r[1] == "复诊"
        ), rows
    finally:
        store.close()


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


# ---------------------------------------------------------------- [5]
def test_tombstone_restore_keeps_shared_reminder(tmp_path, monkeypatch):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        client.post("/api/sessions/import", json=_shared_backup(0, 2))
        client.post("/api/appointments/1/reminder")
        pending = store.list_reminders(False, "妈妈")
        own = [x for x in pending if int(x["appointment_id"] or 0) == 1]
        assert own, pending
        client.delete(f"/api/reminders/{int(own[0]['id'])}")
        client.patch("/api/appointments/1", json={"status": "cancelled"})
        client.patch("/api/appointments/1", json={"status": "upcoming"})
        pending = store.list_reminders(False, "妈妈")
        v2 = store.get_visit_record(2)
        assert len(pending) == 1, pending
        assert int(v2["followup_reminder_id"] or 0) == int(pending[0]["id"])
    finally:
        store.close()


# ---------------------------------------------------------------- [12]
def test_visit_profile_change_keeps_shared_reminder(
    tmp_path, monkeypatch
):
    client, store = _client(tmp_path, monkeypatch)
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        client.post("/api/sessions/import", json=_shared_backup(1, 2))
        resp = client.patch("/api/visits/1", json={"profile": "爸爸"})
        assert resp.status_code == 200, resp.text
        pending = store.list_reminders(False, "妈妈")
        assert len(pending) == 1, pending
        v2 = store.get_visit_record(2)
        assert int(v2["followup_reminder_id"] or 0) == int(pending[0]["id"])
        v1 = store.get_visit_record(1)
        assert int(v1["followup_reminder_id"] or 0) == 0, v1
    finally:
        store.close()


# ---------------------------------------------------------------- [13][14][15]
def _web_source():
    return (
        ROOT / "web" / "app.js"
    ).read_text(encoding="utf-8").replace("\r\n", "\n")


def test_expected_question_reconcile_cancels_second_pass():
    source = _web_source()
    start = source.index("let inflightReconciled = false;")
    end = source.index("inflightReconciledThisRestore = inflightReconciled;")
    block = source[start:end]
    assert "inflightReconciled = true;" in block
    assert block.index("if (expectedQuestion)") < block.index(
        "inflightReconciled = true;"
    )
    # 只有一处声明
    assert source.count("let inflightReconciled = false;") == 1


def test_load_draft_clears_stale_profile_owner():
    source = _web_source()
    start = source.index("function loadDraft() {")
    block = source[start:start + 1600]
    assert "safeSessionRemove(DRAFT_PROFILE_KEY);" in block
    assert "draftProfileMemory = null;" in block


def test_deleted_session_restore_respects_open_sequence():
    source = _web_source()
    start = source.index("if (res.status === 404) {")
    block = source[start:start + 900]
    assert "restoreOpenSeq === openSessionSeq" in block
