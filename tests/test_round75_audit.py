# -*- coding: utf-8 -*-
"""第七十五轮审查修复的回归测试。"""
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
@pytest.mark.parametrize(
    "text,term",
    [
        ("服用华法林期间不吃青菜，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("吃华法林不吃西兰花，今天摔了一跤磕到头", "抗凝治疗期间头部外伤"),
        ("吃华法林不吃纳豆，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("服用华法林期间不吃绿叶蔬菜，今天头撞到门框", "抗凝治疗期间头部外伤"),
    ],
)
def test_diet_object_keeps_drug_context(text, term):
    result = _red(text)
    assert result["level"] == "emergency"
    assert term in result["matches"]


def test_true_stop_still_drops_drug():
    assert _red("我不吃华法林，今天头撞到门框")["level"] == "none"
    assert _red("华法林我不吃了，今天头撞到门框")["level"] == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "顺产20天，今天阴道出血",
        "月子里，今天下面出血",
        "坐月子第10天，阴道流血",
        "自然分娩20天，阴道出血",
        "生完宝宝半个月，下面流血",
        "刚生完孩子，下面出血",
    ],
)
def test_recent_postpartum_bleeding(text):
    result = _red(text)
    assert result["level"] == "emergency"
    assert "孕产期出血" in result["matches"]


def test_past_postpartum_years_not_current():
    assert _red("顺产5年，今天阴道出血")["level"] == "none"


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我也有高血压，今天头撞到门框",
        "我爸吃华法林，我也在吃维生素C，今天头撞到门框",
        "我爸吃华法林，我也有一只猫，今天头撞到门框",
        "我爸吃华法林，我也在吃阿司匹林，今天头撞到门框",
    ],
)
def test_continuation_must_name_same_drug(text):
    assert _red(text)["level"] == "none"
    assert check_medication_safety(text, {"age": "35岁"})["level"] == "none"


def test_continuation_same_drug_still_kept():
    assert _red("我爸吃华法林，我也在吃，今天头撞到门框")["level"] == (
        "emergency"
    )
    result = check_medication_safety(
        "我爸吃华法林，我也在吃，能吃布洛芬吗", {"age": "35岁"}
    )
    assert result["level"] == "warning"


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "我在看关于胸痛的文章",
        "我看了一篇讲胸痛的文章",
        "我在看关于抽搐的视频",
        "我想找关于咯血的科普视频",
    ],
)
def test_article_about_symptom_is_not_complaint(text):
    assert _red(text)["level"] == "none"


def test_companion_symptom_before_quote_still_emergency():
    assert _red("胸痛伴气短网上查了说是心梗")["level"] == "emergency"


# ---------------------------------------------------------------- [6]
def test_english_cross_clause_recent_stop():
    for text in (
        "I used to take warfarin, stopped yesterday, can I take ibuprofen?",
        "I used to take warfarin, stopped taking it yesterday, "
        "can I take ibuprofen?",
    ):
        result = check_medication_safety(text, {"age": "35岁"})
        assert result["level"] == "warning", text


# ---------------------------------------------------------------- [15]
def test_continuation_rejects_unrelated_objects():
    assert check_medication_safety(
        "我爸吃华法林，我也在吃维生素C，能吃阿司匹林吗", {"age": "35岁"}
    )["level"] == "none"
    assert check_medication_safety(
        "我爸吃华法林，我也有一只猫，能吃布洛芬吗", {"age": "35岁"}
    )["level"] == "none"
    assert check_medication_safety(
        "我爸对青霉素过敏，我也对海鲜过敏，能吃阿莫西林吗", {"age": "35岁"}
    )["level"] == "none"


# ---------------------------------------------------------------- [3]
def test_shared_content_soft_claim_restores_deleted_sibling(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    backup = {"appointments": [
        {"id": 1, "profile": "爸爸", "title": "复诊", "department": "",
         "note": "", "scheduled_at": "2026-12-01T09:00",
         "status": "upcoming", "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "title": "复诊", "department": "内科",
         "note": "备注", "scheduled_at": "2026-12-02T09:00",
         "status": "upcoming", "created_at": CREATED, "updated_at": CREATED},
        {"id": 3, "profile": "爸爸", "title": "复诊", "department": "内科",
         "note": "", "scheduled_at": "2026-12-02T09:00",
         "status": "upcoming", "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_appointments(None, None)
    first = next(r for r in rows if r["scheduled_at"].startswith("2026-12-01"))
    third = next(
        r for r in rows
        if r["scheduled_at"].startswith("2026-12-02")
        and not r["note"]
    )
    store.update_appointment_partial(first["id"], department="外科")
    store.delete_appointment(third["id"])
    store.import_data(backup)
    final = [
        (r["note"], r["scheduled_at"]) for r in store.list_appointments(
            None, None
        )
    ]
    again = store.import_data(backup)
    store.close()
    assert any(
        note == "" and at.startswith("2026-12-02") for note, at in final
    ), final
    assert again.get("appointments") == 0


# ---------------------------------------------------------------- [9]
def test_group_full_tail_preference_does_not_duplicate(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    active = store.add_care_item("妈妈", "复查血压", interval_days=30)
    paused = store.add_care_item("妈妈", "复查血压", interval_days=30)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE care_items SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE care_items SET active = 0 WHERE id = ?", (paused["id"],)
        )
    backup = store.export_data()
    store.update_care_item_partial(active["id"], active=False)
    store.import_data(backup)
    items = store.list_care_items(None, False, False, 100)
    store.close()
    assert len(items) == 2, items
    assert not any(item["active"] for item in items), items


def test_group_full_completed_reminder_stays_done(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    first = store.add_reminder("提醒B", "2026-12-02T09:00", profile="妈妈")
    second = store.add_reminder("提醒B", "2026-12-02T09:00", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute("UPDATE reminders SET created_at = ?", (CREATED,))
    store.complete_reminder(first["id"])
    backup = store.export_data()
    store.complete_reminder(second["id"])
    store.import_data(backup)
    rows = store.list_reminders(True, "妈妈")
    pending = [item for item in rows if not item["done"]]
    store.close()
    assert len(rows) == 2, rows
    assert pending == [], rows


# ---------------------------------------------------------------- [10]
def test_duplicate_sibling_import_stays_idempotent(tmp_path):
    store = _store(tmp_path)
    store.save_profile("爸爸", {"age": "62"})
    existing = store.add_metric(
        "爸爸", "weight", 60.0, measured_at="2026-05-02T08:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET created_at = '2026-05-05T08:00'"
            " WHERE id = ?",
            (existing["id"],),
        )
    rows = []
    for index in range(1, 8):
        rows.append({
            "id": index,
            "profile": "爸爸",
            "metric": "weight",
            "value1": 70.0 if index not in (3, 6) else 75.0,
            "value2": None,
            "measured_at": (
                "2026-05-01T08:00" if index in (3, 6)
                else f"2026-05-0{index}T09:00"
            ),
            "note": "",
            "context": "unspecified",
            "created_at": CREATED,
            "updated_at": CREATED,
        })
    backup = {"health_metrics": rows}
    store.import_data(backup)
    count_after_first = len(store.list_metrics("爸爸", "weight", 100, 0))
    second = store.import_data(backup)
    count_after_second = len(store.list_metrics("爸爸", "weight", 100, 0))
    third = store.import_data(backup)
    store.close()
    assert count_after_first == count_after_second, (
        count_after_first, count_after_second
    )
    assert second.get("metrics") == 0
    assert third.get("metrics") == 0


# ---------------------------------------------------------------- [11]
def _followup_setup(store):
    today = datetime.now()
    visit = store.add_visit_record(
        "妈妈",
        visit_date=(today - timedelta(days=10)).strftime("%Y-%m-%d"),
        diagnosis="高血压",
        next_followup_date=(today + timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压",
        scheduled_at=(today + timedelta(days=10)).strftime("%Y-%m-%d")
        + "T09:00",
    )
    reminder = store.add_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    store.complete_reminder(reminder["id"])
    second, created = store.ensure_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created and second
    store.reopen_reminder(reminder["id"])
    return visit, appointment, reminder, second


def test_reschedule_updates_detached_visit_reminder(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    visit, appointment, first, second = _followup_setup(store)
    new_day = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
    store.update_appointment_partial(
        appointment["id"], scheduled_at=f"{new_day}T09:00"
    )
    first_row = store.get_reminder(first["id"])
    second_row = store.get_reminder(second["id"])
    store.close()
    assert first_row["appointment_id"] == 0
    # 脱钩提醒随改期同步到与挂预约提醒相同的日期
    assert first_row["due_at"] == second_row["due_at"], (
        first_row, second_row
    )
    assert first_row["due_at"][:10] < new_day


def test_cancel_removes_detached_visit_reminder(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    visit, appointment, first, second = _followup_setup(store)
    store.update_appointment_partial(appointment["id"], status="cancelled")
    pending = store.list_reminders(False, "妈妈")
    store.close()
    assert pending == [], pending


# ---------------------------------------------------------------- [13]
def test_resumed_medication_counts_from_resume_date(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    medication = store.add_medication(
        "妈妈", "二甲双胍", "500mg", frequency="每日两次",
        active=False,
        started_at=(now - timedelta(days=60)).strftime("%Y-%m-%d"),
        stopped_at=(now - timedelta(days=30)).strftime("%Y-%m-%d"),
    )
    assert medication
    assert store.update_medication(medication["id"], active=True)
    row = next(
        item for item in store.list_medications("妈妈")
        if item["id"] == medication["id"]
    )
    store.close()
    assert row["started_at"] == now.strftime("%Y-%m-%d"), row
    assert row["expected_week_doses"] == 2, row


# ---------------------------------------------------------------- [17]
def test_vaccination_update_syncs_next_dose_reminder_title(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    vaccination = store.add_vaccination(
        "妈妈", "乙肝疫苗", dose_number=2,
        vaccinated_at="2026-05-01",
        next_due="2026-12-01",
    )
    store.ensure_reminder(
        "接种：乙肝疫苗（第3剂）", "2026-11-24",
        profile="妈妈", vaccination_id=vaccination["id"],
    )
    store.update_vaccination_partial(
        vaccination["id"], vaccine_name="乙肝疫苗（加强）"
    )
    renamed = store.list_reminders(False, "妈妈")[0]["title"]
    store.update_vaccination_partial(vaccination["id"], dose_number=3)
    dose_changed = store.list_reminders(False, "妈妈")[0]["title"]
    store.close()
    assert "乙肝疫苗（加强）" in renamed, renamed
    assert "第4剂" in dose_changed, dose_changed


# ---------------------------------------------------------------- [18]
def test_backup_keeps_detached_visit_reminder_link(tmp_path):
    source = _store(tmp_path)
    source.save_profile("妈妈", {"age": "60"})
    visit, appointment, first, second = _followup_setup(source)
    backup = source.export_data()
    source.close()
    target = _store(tmp_path)
    target.import_data(backup)
    restored = target.get_visit_record(visit["id"])
    reminder_id = int(restored.get("followup_reminder_id") or 0)
    reminder = target.get_reminder(reminder_id) if reminder_id else None
    target.close()
    assert reminder is not None, restored
    assert int(reminder["appointment_id"] or 0) == 0
    assert not reminder["done"]


# ---------------------------------------------------------------- [19]
def test_family_target_bp_peak_uses_all_readings(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 140, "value2": 90,
    })
    current = datetime.now().isoformat(timespec="minutes")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 125, "value2": 95,
        "measured_at": current,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 138, "value2": 85,
        "measured_at": current,
    })
    overview = client.get("/api/family/overview").json()
    mom = next(item for item in overview["profiles"] if item["name"] == "妈妈")
    detail = mom["attention"]["detail"]
    assert "最高收缩压 138" in detail, detail
    assert "最高舒张压 95" in detail, detail


# ---------------------------------------------------------------- [20]
def test_scheduled_followup_not_suggested_again(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    today = datetime.now()
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": (today - timedelta(days=3)).strftime("%Y-%m-%d"),
        "diagnosis": "高血压",
        "next_followup_date": (
            today + timedelta(days=5)
        ).strftime("%Y-%m-%d"),
    })
    assert visit.status_code == 200
    visit_id = visit.json()["visit"]["id"]
    scheduled = client.post(f"/api/visits/{visit_id}/schedule-followup")
    assert scheduled.status_code == 200
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()
    keys = {item["key"] for item in insights["insights"]}
    assert "followup_upcoming" not in keys, keys


# ---------------------------------------------------------------- [4][12]
def test_forwarded_parse_failure_has_source_cap(monkeypatch, tmp_path):
    token = "r75-secret-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
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
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56301),
    )
    try:
        codes = []
        for _ in range(12):
            codes.append(client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    'Forwarded': 'for="',
                },
            ).status_code)
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429, 429], codes
        ok = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "Forwarded": "for=203.0.113.9",
            },
        )
        assert ok.status_code == 200
        local = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 56302),
        )
        assert local.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        ).status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


def test_shared_bucket_has_source_cap(monkeypatch, tmp_path):
    token = "r75-shared-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "none")
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
    api_server._known_token_proofs.clear()
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56303),
    )
    try:
        codes = []
        for _ in range(12):
            codes.append(client.get(
                "/api/sessions",
                headers={"X-Medix-Token": "guess"},
            ).status_code)
        # 单源额度用尽后不再向共享桶累计：错误令牌仍 401，
        # 但共享桶不会被填满
        assert codes == [401] * 12, codes
        with api_server.auth_failure_limiter_loopback._lock:
            shared_hits = len(
                api_server.auth_failure_limiter_loopback._hits.get(
                    "ip:127.0.0.1", []
                )
            )
        assert shared_hits <= 10, shared_hits
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 56304),
        )
        assert victim.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        ).status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [5][14]
def test_frontend_keeps_inflight_guard_for_queued_submit():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert source.count("if (!sendInFlight) clearInflightPersisted();") >= 2
    assert "sendPersisted = Number(finalPayload.assistant_message_id) > 0" in (
        source
    )
