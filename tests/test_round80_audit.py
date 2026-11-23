# -*- coding: utf-8 -*-
"""第八十轮审查修复的回归测试。"""
import csv
import io
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
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


# ---------------------------------------------------------------- [1]
@pytest.mark.parametrize(
    "text",
    [
        "化疗结束后第5天，发烧39度",
        "化疗结束后第一周，发烧39度",
        "化疗结束后5-14天，发烧39度",
        "化疗结束后第五天，发烧39度",
        "化疗结束后10来天，发烧39度",
    ],
)
def test_recent_chemo_windows(text):
    assert "免疫功能低下伴发热" in _red(text)["matches"], text
    active = _active_medical_text({"history": "化疗结束后第5天"})
    assert "化疗" in active, active


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "孕8个多月，见红",
        "孕八个半月，阴道出血",
        "刚生产完，阴道出血",
        "刚分娩完，阴道流血",
        "生下宝宝十天了，阴道出血",
        "药流后一周，阴道流血",
        "刚流产，阴道出血",
    ],
)
def test_obstetric_bleeding_evidence(text):
    assert "孕产期出血" in _red(text)["matches"], text


def test_obstetric_history_variants():
    assert "孕产期出血" in _red(
        "今天阴道出血", {"history": "刚生产完"}
    )["matches"]
    assert "孕产期出血" in _red(
        "今天阴道出血", {"history": "怀二胎8个月"}
    )["matches"]


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "从来没出现过的胸痛",
        "没出现过的胸痛",
        "之前没出现过的胸痛",
        "好多年没出现的胸痛又来了",
        "多年未见的胸痛",
    ],
)
def test_novel_chest_pain_is_positive(text):
    assert _red(text)["level"] == "emergency", text


# ---------------------------------------------------------------- [4]
def test_english_allergy_statements():
    assert check_medication_safety(
        "I am allergic to penicillin, can I take amoxicillin?", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "I'm allergic to aspirin, can I take ibuprofen?", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "I am not allergic to penicillin, can I take amoxicillin?", {}
    )["level"] == "none"
    assert check_medication_safety(
        "No known allergies, can I take ibuprofen?", {}
    )["level"] == "none"


# ---------------------------------------------------------------- [5]
def test_moved_sibling_twin_does_not_swallow_deleted(tmp_path):
    store = _store(tmp_path)
    for name in ("爸爸", "妈妈", "奶奶"):
        store.save_profile(name, {"age": "60"})
    store.add_medication("爸爸", "阿司匹林", "1片", frequency="每日1次",
                         note="爸爸备注")
    store.add_medication("妈妈", "阿司匹林", "1片", frequency="每日1次",
                         note="妈妈备注")
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
    for name in ("爸爸", "妈妈", "奶奶"):
        store.save_profile(name, {"age": "60"})
    store.add_medication("妈妈", "本地药", "1片", frequency="每日1次")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    dad = next(r for r in rows if r["note"] == "爸爸备注")
    mom = next(r for r in rows if r["note"] == "妈妈备注")
    store.update_medication(dad["id"], profile="奶奶")
    store.delete_medication(mom["id"])
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    restored = [
        r for r in rows
        if r["profile"] == "妈妈" and r["note"] == "妈妈备注"
    ]
    store.import_data(backup)
    rows2 = store.list_medications(None, limit=100)
    store.close()
    assert len(restored) == 1, rows
    assert sorted((r["profile"], r["note"]) for r in rows2) \
        == sorted((r["profile"], r["note"]) for r in rows), (rows, rows2)


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "华法林吃了三年，我爸吃阿司匹林，停了一年多了，今天头撞到门框",
        "华法林吃了三年，二甲双胍吃了两年，停了一年多了，今天头撞到门框",
        "我吃利伐沙班，我爸以前吃华法林，停了一年多了，今天头撞到门框",
    ],
)
def test_detached_stop_does_not_cross_drug_clauses(text):
    assert "抗凝治疗期间头部外伤" in _red(text)["matches"], text
    active = _active_medical_text(
        {"history": "华法林吃了三年，二甲双胍吃了两年，停了一年多了"}
    )
    assert "华法林" in active and "二甲双胍" not in active, active


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "华法林没吃完，化疗结束5年了，今天头撞到门框",
        "服用华法林期间没吃完，化疗结束5年了，今天头撞到门框",
        "利伐沙班没吃完，放疗结束多年了，今天头撞到门框",
    ],
)
def test_unfinished_dose_keeps_unrelated_anticoagulant(text):
    assert "抗凝治疗期间头部外伤" in _red(text)["matches"], text
    active = _active_medical_text(
        {"history": "甲氨蝶呤没吃完，化疗结束5年了"}
    )
    assert "甲氨蝶呤" not in active, active


# ---------------------------------------------------------------- [8]
def test_remap_respects_snapshot_export_time(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "70"})
    store.add_allergy("妈妈", "青霉素")
    assert store.rename_profile("妈妈", "婆婆")
    future = (datetime.now() + timedelta(seconds=5)).isoformat(
        timespec="seconds"
    )
    store.import_data({
        "exported_at": future,
        "profiles": [{"name": "妈妈", "created_at": "2026-08-01T08:00:00",
                      "context": {"age": "35"}}],
        "allergies": [{"id": 1, "profile": "妈妈", "substance": "花生",
                       "created_at": CREATED, "updated_at": CREATED}],
    })
    profiles = {p["name"] for p in store.list_profiles()}
    allergies = sorted(
        (a["profile"], a["substance"]) for a in store.list_allergies(None)
    )
    store.close()
    assert {"妈妈", "婆婆"} <= profiles, profiles
    assert ("妈妈", "花生") in allergies, allergies
    assert ("婆婆", "青霉素") in allergies, allergies


def test_remap_still_follows_post_export_rename(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "70"})
    assert store.rename_profile("妈妈", "婆婆")
    old_export = (datetime.now() - timedelta(hours=1)).isoformat(
        timespec="seconds"
    )
    store.import_data({
        "exported_at": old_export,
        "allergies": [{"id": 1, "profile": "妈妈", "substance": "花生",
                       "created_at": CREATED, "updated_at": CREATED}],
    })
    allergies = store.list_allergies(None)
    store.close()
    assert all(a["profile"] == "婆婆" for a in allergies), allergies


# ---------------------------------------------------------------- [9]
def test_appointment_date_fix_restores_completed_link(
    client_and_store,
):
    client, store = client_and_store
    today = datetime.now().date()
    resp = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": str(today - timedelta(days=5)),
        "diagnosis": "高血压",
        "next_followup_date": str(today),
    })
    visit_id = resp.json()["visit"]["id"]
    resp = client.post(f"/api/visits/{visit_id}/schedule-followup")
    appt_id = resp.json()["appointment"]["id"]
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "completed"})
    client.patch(f"/api/visits/{visit_id}", json={
        "next_followup_date": str(today - timedelta(days=1)),
    })
    after_move = store.get_visit_record(visit_id)
    client.patch(f"/api/appointments/{appt_id}", json={
        "scheduled_at": str(today - timedelta(days=1)) + "T09:00",
    })
    after_fix = store.get_visit_record(visit_id)
    assert int(after_move.get("followup_appointment_id") or 0) == 0
    assert int(after_fix.get("followup_appointment_id") or 0) == appt_id
    assert bool(after_fix.get("followup_scheduled"))


# ---------------------------------------------------------------- [10]
def test_info_medication_alert_attention_level(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.add_medication("妈妈", "头孢克肟", "0.1g", frequency="每日2次")
    store.add_medication("妈妈", "鸿茅药酒", "50ml", frequency="每日1次")
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    assert (mom.get("attention") or {}).get("level") == "info"
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()["insights"]
    safety = next(
        i for i in insights if i.get("key") == "medication_safety"
    )
    assert safety.get("level") == "info"


# ---------------------------------------------------------------- [11][22][23]
def test_frontend_inflight_backup_slot_is_multi_record():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function readOtherInflightRecords()" in source
    assert "function writeOtherInflightRecords(records)" in source
    assert "function clearOtherSessionInflight(" in source
    assert "clearOtherSessionInflight(sessionId, question);" in source
    assert "function removeInflightRecord(record)" in source
    # baseline=0 首轮对账扫描任意同文本用户消息（不只看最后一条）
    start = source.index("restoreInflightForOpenSession")
    end = source.index("function restoreInflightQuestion", start)
    block = source[start:end]
    assert "list.some(" in block
    # 改名/删除档案同步备份槽
    start = source.index("function updateInflightQuestionProfile")
    end = source.index("function freshSessionMarkKey", start)
    profile_block = source[start:end]
    assert "readOtherInflightRecords()" in profile_block
    assert "writeOtherInflightRecords(next)" in profile_block


# ---------------------------------------------------------------- [12]
def test_deleted_profile_rename_history_scrubbed(tmp_path):
    store = _store(tmp_path)
    store.save_profile("张三", {"age": "60"})
    assert store.rename_profile("张三", "妈妈")
    assert store.delete_profile("妈妈")
    backup = store.export_data()
    resolved = store.resolve_profile_alias("张三")
    store.close()
    # 第八十一轮起导出不再携带 profile_renames（旧姓名为明文死数据）
    assert not backup.get("profile_renames"), backup.get("profile_renames")
    assert resolved == "张三", resolved


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "我家里人吃华法林，今天头撞到门框",
        "我家孩子吃华法林，今天头撞到门框",
        "我家宝宝吃华法林，今天头撞到门框",
        "我家老人吃华法林，今天头撞到门框",
    ],
)
def test_family_subject_medication_not_patient(text):
    assert _red(text, {"age": "35岁"})["level"] == "none", text
    safety = check_medication_safety(
        text.replace("今天头撞到门框", "我能吃布洛芬吗"), {"age": "35岁"}
    )
    assert safety["level"] == "none", safety
    if any(term in text for term in ("孩子", "宝宝", "娃")):
        # 患儿档案里这些称谓指患者本人
        assert _red(text, {"age": "3岁"})["level"] == "emergency", text


# ---------------------------------------------------------------- [14]
@pytest.mark.parametrize(
    "text",
    [
        "顺产满三年了，今天阴道出血",
        "剖宫产术后已满三年，今天阴道出血",
        "产后已满三年了，今天阴道出血",
        "生完孩子满三年了，今天阴道出血",
    ],
)
def test_past_obstetric_with_full_aux(text):
    assert "孕产期出血" not in _red(text)["matches"], text


# ---------------------------------------------------------------- [15]
def test_reminder_fallbacks_need_clues(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    vaccination = store.add_vaccination(
        "妈妈", "乙肝疫苗", vaccinated_at="2026-01-01",
        next_due="2027-03-01",
    )
    store.import_data({"reminders": [{
        "id": 77, "profile": "妈妈", "title": "接种：流感疫苗 第2剂",
        "due_at": "2026-12-01T09:00", "vaccination_id": vaccination["id"],
        "done": 0, "created_at": CREATED, "updated_at": CREATED,
    }]})
    wrong = next(
        r for r in store.list_reminders(False, "妈妈")
        if r["title"].startswith("接种：流感")
    )
    assert int(wrong["vaccination_id"] or 0) == 0
    store.import_data({"reminders": [{
        "id": 78, "profile": "妈妈", "title": "接种：乙肝疫苗 第3剂",
        "due_at": "2027-02-28T09:00", "vaccination_id": vaccination["id"],
        "done": 0, "created_at": CREATED, "updated_at": CREATED,
    }]})
    right = next(
        r for r in store.list_reminders(False, "妈妈")
        if r["title"].startswith("接种：乙肝")
    )
    store.close()
    assert int(right["vaccination_id"] or 0) == vaccination["id"]


# ---------------------------------------------------------------- [16]
def test_reminder_created_at_whitespace_idempotent(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    backup = {"reminders": [{
        "id": 1, "profile": "妈妈", "title": "复诊",
        "due_at": "2027-01-20T09:00", "note": "",
        "advance_days": 0, "repeat_days": 0, "repeat_until": "", "done": 0,
        "created_at": " 2026-06-01T08:00:00 ",
        "updated_at": " 2026-06-01T08:00:00 ",
    }]}
    for _ in range(3):
        store.import_data(backup)
    rows = store.list_reminders(True, "妈妈")
    store.close()
    assert len(rows) == 1, rows


# ---------------------------------------------------------------- [17]
def test_repeat_schedule_after_completion(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    resp = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": str(today - timedelta(days=1)),
        "diagnosis": "高血压",
        "next_followup_date": str(today + timedelta(days=2)),
    })
    visit_id = resp.json()["visit"]["id"]
    first = client.post(f"/api/visits/{visit_id}/schedule-followup")
    appt_id = first.json()["appointment"]["id"]
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "completed"})
    second = client.post(f"/api/visits/{visit_id}/schedule-followup")
    body = second.json()
    appointments = store.list_appointments("妈妈", None, False, 100, 0)
    visit = store.get_visit_record(visit_id)
    assert second.status_code == 200
    assert body.get("appointment_created") is False
    assert int(body["appointment"]["id"]) == appt_id
    assert len(appointments) == 1, appointments
    assert int(visit.get("followup_appointment_id") or 0) == appt_id


# ---------------------------------------------------------------- [18]
def test_schedule_adopts_orphan_reminder(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    today = datetime.now().date()
    day = str(today + timedelta(days=2))
    store.import_data({
        "reminders": [{
            "id": 1, "profile": "妈妈", "title": "复诊：高血压",
            "note": f"复诊 {day} 心内科", "due_at": day + "T09:00",
            "appointment_id": 0, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        }],
        "visit_records": [{
            "id": 1, "profile": "妈妈", "visit_date": str(today),
            "diagnosis": "高血压", "next_followup_date": day,
            "followup_appointment_id": 0, "followup_reminder_id": 1,
            "created_at": CREATED, "updated_at": CREATED,
        }],
    })
    visit = store.list_visit_records("妈妈")[0]
    body = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    ).json()
    appt_id = body["appointment"]["id"]
    pending = store.list_reminders(False, "妈妈")
    assert len(pending) == 1, pending
    assert int(pending[0]["id"]) == 1
    assert int(pending[0]["appointment_id"] or 0) == appt_id
    client.delete(f"/api/visits/{visit['id']}")
    assert store.list_reminders(False, "妈妈") == []


# ---------------------------------------------------------------- [19]
def test_reschedule_note_uses_appointment_day(tmp_path):
    store = _store(tmp_path)
    today = datetime.now().date()
    day = str(today + timedelta(days=2))
    new_day = str(today + timedelta(days=12))
    visit = store.add_visit_record(
        "妈妈", visit_date=str(today),
        diagnosis="高血压", next_followup_date=day,
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at=day + "T09:00",
        owner_visit_id=visit["id"],
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", str(today + timedelta(days=1)),
        note=f"复诊 {day} 心内科", profile="妈妈",
        appointment_id=appt["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment_id=appt["id"],
        reminder_id=reminder["id"],
    )
    store.complete_reminder(reminder["id"])
    store.add_reminder(
        "复诊：高血压", str(today + timedelta(days=1)),
        note=f"复诊 {day}", profile="妈妈",
        appointment_id=appt["id"],
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET done = 0, appointment_id = 0"
            " WHERE id = ?",
            (reminder["id"],),
        )
    store.update_appointment(
        appt["id"], "妈妈", "复诊：高血压", "", "", "",
        new_day + "T09:00", "upcoming",
        f"由就诊记录 #{visit['id']} 自动创建",
    )
    refreshed = store.get_reminder(reminder["id"])
    store.close()
    assert new_day in str(refreshed.get("note") or ""), refreshed
    assert str(refreshed.get("due_at") or "")[:10] == str(
        today + timedelta(days=11)
    ), refreshed


# ---------------------------------------------------------------- [20][21]
def test_active_only_today_count_and_csv_lab_cell(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.add_medication("妈妈", "氨氯地平", "5mg", frequency="每日1次")
    stopped = store.add_medication(
        "妈妈", "布洛芬", "0.3g", frequency="每日3次", active=False
    )
    store.add_medication_log(
        stopped["id"],
        taken_at=datetime.now().strftime("%Y-%m-%d") + "T08:00",
    )
    store.add_medication("妈妈", "头孢克肟", "0.1g", frequency="每日2次")
    store.add_medication("妈妈", "鸿茅药酒", "50ml", frequency="每日1次")
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    meds = mom.get("medications") or {}
    assert int(meds.get("taken_today_active") or 0) == 0
    assert int(meds.get("taken_today") or 0) >= 1
    text = client.get("/api/family/overview/export.csv").content.decode(
        "utf-8-sig"
    )
    rows = list(csv.reader(io.StringIO(text)))
    header = rows[0]
    row = next(r for r in rows[1:] if r and r[0] == "妈妈")
    assert "今日 0 次" in row[header.index("用药")]
    assert row[header.index("化验异常")] == "用药安全需核对 1 条"


# ---------------------------------------------------------------- [24]
def test_single_delete_redacts_bulk_tag_audit(client_and_store):
    client, store = client_and_store
    store.ensure_session("s-old", "测试", "妈妈")
    store.log_action(
        "session.bulk_tags", "",
        "updated=2 missing=0 count=2 tags=化疗、抑郁",
    )
    assert client.delete("/api/sessions/s-old").status_code == 200
    leaked = [
        entry for entry in store.list_audit_log(limit=1000)
        if entry.get("action") == "session.bulk_tags"
        and "化疗" in str(entry.get("detail") or "")
    ]
    assert leaked == [], leaked
