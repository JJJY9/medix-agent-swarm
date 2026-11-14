"""第四十六轮审查：体温「度」、后脑勺语序、英文药名依从性、定义问句
与患者语境、导入标记/归属作用域与指纹 TEXT、复诊归属解析、代理混淆/
回环/套接字、档案导出累计、提醒改档、家庭提示成员与前端守卫。"""
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


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

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


# ---------------------------------------------------------------- 分诊/用药
def test_temperature_with_du_unit():
    from core.triage import detect_red_flags, patient_complaint_text

    assert detect_red_flags(
        patient_complaint_text("38.5度"), {"age": "2个月"}
    )["level"] == "emergency"
    assert detect_red_flags(
        patient_complaint_text("化疗后38.5度"), {"history": "化疗后"}
    )["level"] == "emergency"
    assert detect_red_flags(
        patient_complaint_text("38度5"), {"age": "2个月"}
    )["level"] == "emergency"
    # 非体温语境仍不误报
    assert detect_red_flags(
        patient_complaint_text("今天气温38度"), {"age": "2个月"}
    )["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("喝40度的白酒可以吗"), {}
    )["level"] == "none"


def test_head_trauma_with_dao_variants():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "摔倒磕到后脑勺",
        "摔倒撞到后脑勺",
        "摔倒碰到后脑勺",
    ):
        result = detect_red_flags(
            patient_complaint_text(text), {"medications": "华法林"}
        )
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_english_alias_adherence_and_restart():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "我没有按时吃 warfarin，现在牙疼能吃布洛芬吗", {}
    ).get("level") == "warning"
    assert check_medication_safety(
        "warfarin 停了半年，最近又吃上了，能吃布洛芬吗", {}
    ).get("level") == "warning"


def test_definition_questions_with_call_phrases():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "什么叫胸痛",
        "啥叫咯血",
        "请问什么叫咯血",
        "什么是持续性胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text

    # 已有患者症状语境时，后置定义问句仍按新症状保留
    assert detect_red_flags(
        patient_complaint_text("宝宝发烧，抽搐是什么病")
    )["level"] == "emergency"


def test_patient_context_not_from_time_or_negation():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "今天想问问，抽搐是什么病",
        "昨天看了科普，抽搐是什么病",
        "最近在查资料，咯血是什么病",
        "没有胸痛，抽搐是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_gdm_history_not_current_pregnancy():
    from core.triage import detect_red_flags

    assert detect_red_flags(
        "今天阴道出血", {"history": "妊娠期糖尿病"}
    )["level"] == "none"
    assert detect_red_flags(
        "今天阴道出血",
        {"history": "孕20周，妊娠期高血压（管理中）"},
    )["level"] == "emergency"


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created):
    return {
        "id": raw_id, "profile": profile, "name": name, "dose": dose,
        "frequency": "每日一次", "created_at": created,
        "updated_at": created,
    }


def test_import_marker_scope_keeps_local_notes(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        visit = target.add_visit_record("妈妈", visit_date="2026-10-01")
        appointment = target.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note=f"由就诊记录 #{visit['id']} 自动创建",
            owner_visit_id=visit["id"],
        )
        source.save_profile("妈妈", {"sex": "女"})
        source.add_visit_record(
            "妈妈", visit_date="2026-09-01", diagnosis="糖尿病",
        )
        backup = source.export_data()

        target.import_data(backup)

        refreshed = target.get_appointment(appointment["id"])
        assert f"#{visit['id']}" in str(refreshed["note"])
        assert int(refreshed["owner_visit_id"] or 0) == visit["id"]
    finally:
        target.close()
        source.close()


def test_import_marker_idempotent_for_no_link_appointment(tmp_path):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        appointment = source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note=f"由就诊记录 #{visit['id']} 自动创建",
        )
        backup = source.export_data()
        appointment_id = appointment["id"]
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        first_note = target.get_appointment(appointment_id)["note"]
        target.import_data(backup)
        second_note = target.get_appointment(appointment_id)["note"]
        assert first_note == second_note
        assert "由就诊记录 #" in first_note
    finally:
        target.close()


def test_import_persists_owner_when_note_edited(tmp_path):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        first = _schedule_followup_sync(source, visit["id"])
        source.update_appointment_partial(
            first["appointment"]["id"],
            note="记得带病历和医保卡",
        )
        backup = source.export_data()
        appointment_id = first["appointment"]["id"]
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        imported_appointment = target.get_appointment(appointment_id)
        new_visit = target.list_visit_records(None, 0, "", 10)[0]
        assert int(imported_appointment["owner_visit_id"] or 0) == (
            new_visit["id"]
        )
        assert imported_appointment["note"] == "记得带病历和医保卡"
    finally:
        target.close()


def test_reminder_moved_profile_not_duplicated(tmp_path):
    from memory.session_store import SessionStore

    store = SessionStore(str(tmp_path / "store.db"))
    try:
        store.save_profile("妈妈", {"sex": "女"})
        store.save_profile("爸爸", {"sex": "男"})
        reminder = store.add_reminder(
            "妈妈复查", "2026-12-01", "", "", "妈妈"
        )
        backup = store.export_data()
        time.sleep(1.05)
        store.update_reminder(reminder["id"], profile="爸爸")

        result = store.import_data(backup)

        assert result["reminders"] == 0
        mom_rows = [
            item for item in store.list_reminders(False, "妈妈")
            if item["title"] == "妈妈复查"
        ]
        assert mom_rows == []
        dad_rows = [
            item for item in store.list_reminders(False, "爸爸")
            if item["title"] == "妈妈复查"
        ]
        assert len(dad_rows) == 1
    finally:
        store.close()


def test_fingerprint_keeps_text_columns(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.save_profile("爸爸", {"sex": "男"})
        created = "2026-06-01T08:00:00"
        backup = {"medications": [
            _med(5, "妈妈", "药A", "2.0", created),
            _med(6, "爸爸", "药C", "1.0", created),
        ]}
        target.import_data(backup)
        rows = target.list_medications(limit=20)
        mom_a = next(
            item for item in rows
            if item["profile"] == "妈妈" and item["name"] == "药A"
        )
        dad_c = next(
            item for item in rows
            if item["profile"] == "爸爸" and item["name"] == "药C"
        )
        assert target.delete_medication(mom_a["id"])
        time.sleep(1.05)
        assert target.update_medication(dad_c["id"], profile="妈妈")

        target.import_data(backup)

        rows = target.list_medications(limit=20)
        assert any(
            item["profile"] == "妈妈" and item["name"] == "药A"
            for item in rows
        ), rows
    finally:
        target.close()


def test_import_invalid_counts_non_dict_entries(store):
    result = store.import_data({
        "medications": ["bogus", 42, _med(1, "妈妈", "药A", "1g",
                                          "2026-06-01T08:00:00")],
        "profiles": [7, {"name": "妈妈", "context": {"sex": "女"}}],
        "symptoms": ["x"],
    })
    assert result["medications"] == 1
    assert result["profiles"] == 1
    assert result["invalid"] >= 4


# ---------------------------------------------------------------- 复诊
def test_restore_duplicate_rejected_after_dangling_owner_recovery(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.delete_visit_record(visit["id"])

    store.import_data(backup)

    restored = store.list_visit_records(None, 0, "", 10)[0]
    _schedule_followup_sync(store, restored["id"])
    with pytest.raises(ValueError):
        store.update_appointment_partial(appointment_id, status="upcoming")
    upcoming = store.list_appointments(None, "upcoming")
    assert len(upcoming) == 1


def test_reminder_profile_move_degrades_appointment(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    reminder_id = first["reminder"]["id"]
    store.update_reminder(reminder_id, profile="爸爸")

    moved = store.get_reminder(reminder_id)
    assert moved["appointment_id"] == 0
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_reminder_id"] == 0
    _schedule_followup_sync(store, visit["id"])
    mom_pending = store.list_reminders(False, "妈妈")
    dad_pending = store.list_reminders(False, "爸爸")
    assert len(mom_pending) == 1
    assert len(dad_pending) == 1


# ---------------------------------------------------------------- 代理
def test_loopback_forged_forwarded_still_rate_limited(
    client_and_store, monkeypatch
):
    import api_server
    from core.ratelimit import SlidingWindowLimiter

    client, _store = client_and_store
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "a-secure-random-token-123")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(2, window=300),
    )
    statuses = []
    for index in range(4):
        response = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad",
            "X-Forwarded-For": f"10.9.0.{index}",
        })
        statuses.append(response.status_code)
    # 伪造转发地址无法轮换共享回环桶：第 3 次起被锁
    assert statuses[:2] == [401, 401]
    assert statuses[-1] == 429


def test_socket_peer_none_is_local_and_shared():
    import api_server

    class FakeRequest:
        def __init__(self):
            self.headers = {}
            self.client = None

    request = FakeRequest()
    assert api_server._is_loopback_client(request) is True
    assert api_server._client_ip_key(request) == "ip:unknown"


def test_forwarded_allow_ips_startup_warning():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "FORWARDED_ALLOW_IPS" in api_source
    assert "--no-proxy-headers" in api_source


# ---------------------------------------------------------------- 报表
def test_dossier_daily_total_uses_records_own_day(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    for hour in ("08:00", "18:00"):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "steps", "value1": 3000,
            "measured_at": f"{yesterday}T{hour}",
        })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "water", "value1": 1800,
        "measured_at": f"{yesterday}T20:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "water", "value1": 300,
        "measured_at": f"{today}T09:00",
    })

    text = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    ).text
    water_line = next(
        line for line in text.splitlines() if "饮水量" in line
    )
    assert "当日累计 300" in water_line
    assert "1800" not in water_line


def test_family_insights_label_unassigned_adherence(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    started = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d")
    store.add_medication(
        "", "二甲双胍", "1片", "每日两次", started_at=started
    )

    data = client.get("/api/health/insights", params={"days": 7}).json()
    adherence = next(
        item for item in data["insights"] if item["key"] == "adherence"
    )
    assert "未指定档案" in adherence["title"]


def test_family_vaccine_overdue_labels_member(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_vaccination(
        "妈妈", "流感疫苗", 1,
        vaccinated_at="2026-01-01", next_due="2026-02-01",
    )

    data = client.get("/api/health/insights", params={"days": 30}).json()
    vaccine = next(
        item for item in data["insights"] if item["key"] == "vaccine_overdue"
    )
    assert "妈妈：" in vaccine["detail"]


# ---------------------------------------------------------------- 前端
def test_frontend_round46_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 再次会诊/失败重试在待重置时不得自动重发
    regenerate = app_js[
        app_js.index("makeAction('再次会诊'"):
        app_js.index("makeAction('再次会诊'") + 400
    ]
    assert "pendingSessionReset" in regenerate
    retry = app_js[
        app_js.index("function makeFailureRetry"):
        app_js.index("function renderError")
    ]
    assert "pendingSessionReset" in retry

    # 待重置期间草稿不属于旧会话
    assert "pendingSessionReset ? '' : (sessionId || '')" in app_js

    # 未指定档案不显示「已删除」
    assert "if (!saved && profile.name)" in app_js

    # 重开当前会话不用旧快照覆盖未发送的补充信息
    # （第四十七轮起：未消费的深链同 ID 打开仍按深链回填快照）
    assert "const sameSession = id === sessionId;" in app_js
    assert "restoreContext: deepLinkOpen || !sameSession" in app_js
