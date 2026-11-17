"""第五十六轮审查：抗过敏药误判、无主语妊娠出血、既往用药续用、
未来停药/英文漏服/双重否定填充、孪生尾字段、指标计数兜底、跨成员
提醒映射、完成预约 keep_date、闸门单源与退避、fe80/NAT64、前端
在途档案/表单快照、家庭成员过滤。"""
import asyncio
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
CREATED = "2026-06-01T08:00:00"


@pytest.fixture
def store(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        yield session_store
    finally:
        session_store.close()


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    for key in (
        "MEDIX_ACCESS_TOKEN", "MEDIX_TRUST_PROXY_HEADERS",
        "MEDIX_PROXY_CLIENT_IP_HEADER", "MEDIX_TRUSTED_PROXY_ADDRS",
        "MEDIX_ALLOW_INSECURE_LAN",
    ):
        monkeypatch.delenv(key, raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


# ---------------------------------------------------------------- 分诊/用药
def test_antiallergy_medication_not_allergy_history():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("我在吃华法林和抗过敏药，今天撞到头"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = check_medication_safety(
        "我吃华法林和抗过敏药，能吃布洛芬吗", {}
    )
    assert result["level"] == "warning"
    result = check_medication_safety(
        "我吃阿司匹林和抗过敏药，能吃布洛芬吗", {}
    )
    titles = [item["title"] for item in result.get("alerts") or []]
    assert any("重复使用非甾体抗炎药" in title for title in titles)
    assert not any("过敏" in title for title in titles)


def test_pregnancy_definition_question_without_subject():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕早期，下面出血是什么病",
        "怀孕了，出血是什么病",
        "孕妇，下面有点出血是什么病",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text


def test_past_medication_restart_keeps_anticoagulant():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "以前吃过华法林，现在还在吃，今天撞到头",
        "十年前吃过华法林，现在还在吃，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_future_stop_variants_and_english_missed_dose():
    from core.medication_safety import check_medication_safety

    for text in (
        "我一个月后停用华法林，今天能吃布洛芬吗",
        "等这盒吃完就停用华法林，现在能吃布洛芬吗",
        "下周就不吃了华法林，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    assert check_medication_safety(
        "I didn't take warfarin today, can I take ibuprofen?", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "I stopped taking warfarin 3 weeks ago, can I take ibuprofen?", {}
    )["level"] != "warning"


def test_double_negation_with_fillers():
    from core.triage import detect_red_flags

    for text in (
        "不是没有过胸痛",
        "不是没出现过胸痛",
        "并非没有明显胸痛",
        "并不是说没有胸痛",
        "不是没有出现过呼吸困难",
    ):
        assert detect_red_flags(text, {})["level"] == "emergency", text


# ---------------------------------------------------------------- 导入
def test_twin_last_row_preserves_stopped_medication(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication("妈妈", "占位", "9g")
    backup = {"medications": [
        {"id": 5, "profile": "妈妈", "name": "阿司匹林", "dose": "100mg",
         "frequency": "每日一次", "note": "服用中", "active": True,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "name": "阿司匹林", "dose": "100mg",
         "frequency": "每日一次", "note": "已停药", "active": False,
         "stopped_at": "2026-05-01", "stop_reason": "医生建议",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    active_row = next(item for item in rows if item["active"])
    stopped_row = next(item for item in rows if not item["active"])
    store.update_medication(active_row["id"], note="本地改备注")
    store.delete_medication(stopped_row["id"])
    store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    assert len(rows) == 2
    stopped = [item for item in rows if not item["active"]]
    assert len(stopped) == 1
    assert stopped[0]["stopped_at"][:10] == "2026-05-01"
    assert stopped[0]["stop_reason"] == "医生建议"


def test_symptom_twin_tail_severity(store):
    store.save_profile("妈妈", {"sex": "女"})
    backup = {"symptoms": [
        {"id": 5, "profile": "妈妈", "symptom": "头痛", "severity": 3,
         "note": "轻度", "recorded_at": "2026-06-01T08:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "symptom": "头痛", "severity": 8,
         "note": "剧烈", "recorded_at": "2026-06-01T08:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_symptoms("妈妈", 100)
    severe = next(item for item in rows if item["severity"] == 8)
    store.delete_symptom(severe["id"])
    store.import_data(backup)
    rows = store.list_symptoms("妈妈", 100)
    assert sorted(item["severity"] for item in rows) == [3, 8]


def test_metric_twin_restore_after_drift(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric("妈妈", "weight", 50, measured_at="2026-05-01T08:00")
    backup = {"health_metrics": [
        {"id": 5, "profile": "妈妈", "metric": "bp", "value1": 120,
         "value2": 80, "measured_at": "2026-06-01T08:00", "note": "",
         "context": "", "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "metric": "bp", "value1": 120,
         "value2": 80, "measured_at": "2026-06-01T08:00", "note": "",
         "context": "", "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [item for item in store.list_metrics("妈妈", limit=100)
            if item["metric"] == "bp"]
    store.delete_metric(rows[0]["id"])
    result = store.import_data(backup)
    rows = [item for item in store.list_metrics("妈妈", limit=100)
            if item["metric"] == "bp"]
    assert result["metrics"] == 1
    assert len(rows) == 2


def test_medication_twin_full_tail_restore(store):
    store.save_profile("妈妈", {"sex": "女"})
    backup = {"medications": [
        {"id": 5, "profile": "妈妈", "name": "阿司匹林", "dose": "1g",
         "frequency": "每日一次", "note": "", "active": True,
         "stock": 10, "expiry_date": "2026-12-01",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "name": "阿司匹林", "dose": "1g",
         "frequency": "每日一次", "note": "", "active": True,
         "stock": 20, "expiry_date": "2027-06-01",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [item for item in store.list_medications(limit=10)
            if item["name"] == "阿司匹林"]
    ten = next(item for item in rows if int(item.get("stock") or 0) == 10)
    store.delete_medication(ten["id"])
    store.import_data(backup)
    rows = [item for item in store.list_medications(limit=10)
            if item["name"] == "阿司匹林"]
    stocks = sorted(int(item.get("stock") or 0) for item in rows)
    assert stocks == [10, 20]
    expiries = sorted(str(item.get("expiry_date") or "")[:10] for item in rows)
    assert expiries == ["2026-12-01", "2027-06-01"]


def test_reminder_cross_member_links_detached(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    backup = {
        "medications": [
            {"id": 5, "profile": "爸爸", "name": "华法林", "dose": "3mg",
             "frequency": "每日一次", "note": "", "active": True,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 6, "profile": "妈妈", "name": "氨氯地平", "dose": "5mg",
             "frequency": "每日一次", "note": "", "active": True,
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [
            {"id": 9, "profile": "妈妈", "title": "用药：华法林",
             "due_at": "2026-12-01", "note": "", "done": 0,
             "medication_id": 5, "created_at": CREATED,
             "updated_at": CREATED},
        ],
    }
    store.import_data(backup)
    reminder = next(
        item for item in store.list_reminders(True)
        if item["profile"] == "妈妈" and item["title"] == "用药：华法林"
    )
    assert int(reminder.get("medication_id") or 0) == 0


def test_reminder_cross_member_appointment_detached(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    backup = {
        "appointments": [
            {"id": 5, "profile": "爸爸", "title": "复诊", "department": "",
             "doctor": "", "location": "",
             "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
             "note": "", "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [
            {"id": 9, "profile": "妈妈", "title": "带病历本",
             "due_at": "2026-12-13", "note": "", "done": 0,
             "appointment_id": 5, "created_at": CREATED,
             "updated_at": CREATED},
        ],
    }
    store.import_data(backup)
    reminder = next(
        item for item in store.list_reminders(True)
        if item["profile"] == "妈妈" and item["title"] == "带病历本"
    )
    assert int(reminder.get("appointment_id") or 0) == 0


def test_add_reminder_rejects_foreign_or_cancelled_appointment(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    foreign = store.add_appointment(
        "爸爸", "复诊", scheduled_at="2026-12-14T09:00"
    )
    reminder = store.add_reminder(
        "带病历本", "2026-12-13", profile="妈妈",
        appointment_id=foreign["id"],
    )
    assert int(reminder.get("appointment_id") or 0) == 0
    own = store.add_appointment(
        "妈妈", "复诊2", scheduled_at="2026-12-15T09:00"
    )
    store.update_appointment_partial(own["id"], status="cancelled")
    reminder = store.add_reminder(
        "带病历本2", "2026-12-14", profile="妈妈",
        appointment_id=own["id"],
    )
    assert int(reminder.get("appointment_id") or 0) == 0


def test_appointment_profile_change_refreshes_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    time.sleep(1.05)
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, profile="爸爸")
    result = store.import_data(backup)
    assert result["reminders"] == 0


# ---------------------------------------------------------------- 复诊
def test_completed_appointment_keep_date(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    first_day = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
    moved_day = (datetime.now() + timedelta(days=13)).strftime("%Y-%m-%d")
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date=first_day,
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(
        appointment_id, scheduled_at=moved_day + "T09:00"
    )
    store.update_appointment_partial(appointment_id, status="completed")
    store.update_visit_record_partial(
        visit["id"], next_followup_date=moved_day
    )
    row = store.get_visit_record(visit["id"])
    assert int(row.get("followup_appointment_id") or 0) == appointment_id
    assert row["followup_scheduled"] is True


# ---------------------------------------------------------------- 代理
def test_gate_per_source_cap_serves_browser_concurrency():
    import api_server

    assert api_server._AUTH_COMPARE_GATE_MAX_PER_SOURCE >= 16
    api_server._auth_compare_gates.clear()

    async def run():
        key = "ip:r56-source"
        tasks = [
            asyncio.create_task(
                api_server._auth_compare_enter(key, source="A")
            )
            for _ in range(12)
        ]
        await asyncio.sleep(0.05)
        gate = api_server._auth_compare_gates[
            (id(asyncio.get_running_loop()), key)
        ]
        assert gate["sources"].get("A") == 12
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(run())


def test_fe80_aggregated_and_nat64_exact():
    import api_server

    assert api_server._bucket_address("fe80::1").endswith("/64")
    assert api_server._bucket_address("fe80::2") == (
        api_server._bucket_address("fe80::1")
    )
    nat64 = "64:ff9b::c0a8:101"
    assert api_server._bucket_address(nat64) == nat64


def test_blocked_bucket_known_token_not_delayed(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r56-secret")
    monkeypatch.delenv("MEDIX_ALLOW_INSECURE_LAN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    limiter = api_server.auth_failure_limiter_loopback
    key = "ip:127.0.0.1"
    with limiter._lock:
        limiter._hits[key] = deque(
            [time.monotonic()] * limiter.limit
        )
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("127.0.0.1", 59999),
        )
        started = time.monotonic()
        response = client.get(
            "/api/sessions", headers={"X-Medix-Token": "r56-secret"}
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert elapsed < 2.0
    finally:
        with limiter._lock:
            limiter._hits.pop(key, None)
        store.close()


# ---------------------------------------------------------------- 报表
def test_usage_only_deleted_profile_hidden(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.record_usage_event(
        "r56-usage", "妈妈", {"total_tokens": 10}, 1
    )
    assert client.delete("/api/profiles/妈妈").status_code == 200
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    assert all(item["name"] != "妈妈" for item in data["profiles"])


def test_inactive_unassigned_records_hidden(client_and_store):
    client, store = client_and_store
    store.add_appointment("", "牙科", scheduled_at="2026-12-01T09:00")
    appointment = store.list_appointments(None, None)[0]
    store.update_appointment_partial(appointment["id"], status="completed")
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    assert all(item["name"] != "" for item in data["profiles"])
    # 有实际可展示数据时仍显示「未指定」
    store.add_metric("", "weight", 60, measured_at="2026-06-01T08:00")
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    assert any(item["name"] == "" for item in data["profiles"])


def test_mem0_marker_written_and_preserves_cleanup(client_and_store):
    client, store = client_and_store
    import api_server

    api_server._memory_configured_marked = False
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "_mark_memory_configured(_memory)" in api_source

    class Configured:
        api_key_configured = True
        enabled = False

    class Disabled:
        api_key_configured = False
        enabled = False

    api_server._mark_memory_configured(Configured())
    assert store.get_app_state("mem0_configured") == "1"
    assert api_server._memory_cleanup_possible(Disabled()) is False


# ---------------------------------------------------------------- 前端
def test_frontend_round56_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 在途主诉档案校验回退到已保存档案
    restore = app_js[
        app_js.index("function restoreInflightQuestion()"):
        app_js.index("window.addEventListener('beforeunload'")
    ]
    assert "activeProfileName || readStoredProfile()" in restore
    # 换档失败回滚使用表单快照
    assert "const previousFormContext = readPatientContext();" in app_js
    assert app_js.count("applyPatientContext(previousFormContext);") == 2
    # 待重置换档提交时 INFLIGHT 记为未归属会话
    assert "sessionId: pendingSessionReset ? '' : (sessionId || '')," in app_js
