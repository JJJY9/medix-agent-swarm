"""第三十八轮审查：枚举词内否定、停药意图/既往用药、免疫主诉、
跨秒导入配对、复诊改期同步、代理头重复字段/共享桶、家庭每日累计、
前端深链发送与已删除会话清理。"""
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
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


# ---------------------------------------------------------------- triage
def test_enum_negation_does_not_match_word_internal_negation():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我这两天全身无力、胸痛、冒冷汗",
        "我全身酸痛无力、呼吸困难",
        "没有力气、胸痛",
        "没有胃口、胸痛",
        "无诱因、胸痛",
        "未婚、胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 真正的枚举否定仍按阴性处理
    for text in (
        "无发热、咳嗽、胸痛",
        "否认胸痛、呼吸困难",
        "今天没有胸痛、咯血",
        "无恶心、呕吐、胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_stop_intent_and_past_medication_in_triage():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我打算停用华法林，今天头撞到门上了",
        "我想停用华法林了，今天头磕到了",
        "我准备停用华法林，今天撞到头",
        "过去半年每天吃华法林，今天撞到头",
        "华法林停用可以吗，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text

    for text in (
        "我以前吃华法林，今天头撞到门上",
        "我曾经服用华法林，今天头磕到了",
        "华法林已经停用两个月了，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" not in result["matches"], text


def test_immunosuppression_and_impossible_pregnancy_from_message():
    from core.triage import detect_red_flags, patient_complaint_text

    immune = detect_red_flags(
        patient_complaint_text("我正在化疗，今天发烧39度"), {}
    )
    assert "免疫功能低下伴发热" in immune["matches"]

    assert detect_red_flags(
        patient_complaint_text("不可能怀孕，但是阴道出血"), {}
    )["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("可能怀孕，阴道出血"), {}
    )["level"] == "emergency"


def test_medication_prefix_stop_intent_and_question():
    from core.medication_safety import check_medication_safety

    for question in (
        "我想停用华法林，能吃布洛芬吗？",
        "华法林停用可以吗？能吃布洛芬吗？",
        "华法林想停用，能吃布洛芬吗？",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") == "warning", question

    stopped = check_medication_safety(
        "华法林已经停用两个月了，能吃布洛芬吗", {}
    )
    assert stopped.get("level") != "warning"


# ---------------------------------------------------------------- 导入
def test_import_same_second_medication_dose_edit_not_duplicated(store):
    store.save_profile("妈妈", {})
    store.import_data({"medications": [
        {
            "profile": "妈妈", "name": "药A", "dose": "1g",
            "frequency": "每日一次",
        },
        {
            "profile": "妈妈", "name": "药B", "dose": "2g",
            "frequency": "每日一次",
        },
    ]})
    backup = store.export_data()
    target = next(
        item for item in store.list_medications(limit=10)
        if item["name"] == "药B"
    )
    assert store.update_medication(target["id"], dose="77mg")

    result = store.import_data(backup)

    # 同秒同身份的兄弟药只改了剂量：恢复旧备份不能把旧剂量再插一份
    assert result["medications"] == 0
    rows = sorted(
        (item["name"], item["dose"])
        for item in store.list_medications(limit=10)
    )
    assert rows == [("药A", "1g"), ("药B", "77mg")]


def test_import_same_second_reminder_twin_restores_deleted(store):
    store.save_profile("妈妈", {})
    first = store.add_reminder("吃药A", "2026-12-01", "", "", "妈妈")
    second = store.add_reminder("吃药B", "2026-12-02", "", "", "妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = ?, updated_at = ?"
            " WHERE id IN (?, ?)",
            (
                "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                first["id"], second["id"],
            ),
        )
    backup = store.export_data()
    assert store.delete_reminder(first["id"])
    assert store.update_reminder(
        second["id"], title="吃药B（改）", due_at="2026-12-09"
    )

    result = store.import_data(backup)

    titles = [
        item["title"] for item in store.list_reminders(False, "妈妈")
    ]
    # 被删的吃药A必须补回，本地编辑的吃药B（改）保留；同秒孪生
    # 无法区分时允许保留一份旧副本，但不能丢失被删记录
    assert result["reminders"] >= 1
    assert "吃药A" in titles
    assert "吃药B（改）" in titles


def test_import_metrics_shared_anchor_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        source.import_data({"health_metrics": [
            {
                "profile": "妈妈", "metric": "weight", "value1": 60,
                "measured_at": "2026-11-01T08:00",
            },
            {
                "profile": "妈妈", "metric": "weight", "value1": 61,
                "measured_at": "2026-11-01T08:00",
            },
        ]})
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        target.add_metric(
            "爸爸", "weight", 55, measured_at="2025-12-01T08:00"
        )
        target.import_data(backup)
        rows = target.list_metrics("妈妈", "weight", 10, 0)
        deleted = next(item for item in rows if item["value1"] == 60)
        survivor = next(item for item in rows if item["value1"] == 61)
        assert target.delete_metric(deleted["id"])
        assert target.update_metric(survivor["id"], value1=161)

        result = target.import_data(backup)

        values = sorted(
            float(item["value1"])
            for item in target.list_metrics("妈妈", "weight", 10, 0)
        )
        # 共享同一测量时间的同秒读数不可区分：优先补回被删的 60，
        # 保留本地编辑的 161
        assert result["metrics"] >= 1
        assert 60.0 in values
        assert 161.0 in values
    finally:
        target.close()


def test_import_cross_profile_symptom_requires_edit_evidence(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        symptom = source.add_symptom(
            "妈妈", "头痛", 2, recorded_at="2026-11-01T08:00"
        )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE symptoms SET created_at = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                    symptom["id"],
                ),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        target.add_symptom("爸爸", "咳嗽", 1, recorded_at="2026-01-01")
        other = target.add_symptom(
            "爸爸", "头痛", 2, recorded_at="2026-11-01T08:00"
        )
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE symptoms SET created_at = ?, updated_at = ?"
                " WHERE id = ?",
                (
                    "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                    other["id"],
                ),
            )

        result = target.import_data(backup)

        # 另一位成员同秒同内容的未编辑症状不能被当作改挂吞掉
        assert result["symptoms"] == 1
        assert sum(
            item["profile"] == "妈妈"
            for item in target.list_symptoms(None, 100, 0)
        ) == 1
    finally:
        target.close()


def test_import_duplicate_visit_relinks_followup(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-04",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-04T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-12-03", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    backup = store.export_data()
    assert store.delete_appointment(appointment["id"])

    result = store.import_data(backup)

    record = store.get_visit_record(visit["id"])
    assert result["appointments"] == 1
    assert record["followup_scheduled"] is True
    assert record["followup_appointment_id"] != 0
    restored = store.get_appointment(record["followup_appointment_id"])
    assert restored["status"] == "upcoming"


def test_import_duplicate_visit_rewrites_drifted_note(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-12-04",
        )
        source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-04T09:00",
            status="upcoming",
            note=f"由就诊记录 #{visit['id']} 自动创建",
        )
        appointment = next(
            item for item in source.list_appointments(limit=10)
            if item["title"] == "复诊：高血压"
        )
        source.set_visit_followup_links(
            visit["id"], appointment["id"], 0
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        unrelated = target.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="感冒",
        )
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE visit_records SET created_at = '2020-01-01T00:00:00'"
                " WHERE id = ?",
                (unrelated["id"],),
            )
        target.import_data(backup)
        restored = next(
            item for item in target.list_visit_records()
            if item["diagnosis"] == "高血压"
        )
        assert restored["id"] != unrelated["id"]
        appointment_id = restored["followup_appointment_id"]
        assert target.delete_appointment(appointment_id)

        target.import_data(backup)

        record = target.get_visit_record(restored["id"])
        appointment = target.get_appointment(
            record["followup_appointment_id"]
        )
        assert appointment is not None
        assert (
            f"由就诊记录 #{restored['id']} 自动创建"
            in appointment["note"]
        )
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_reschedule_auto_appointment_syncs_visit_date(store, client_and_store):
    _client, store = client_and_store
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    from api_server import _schedule_followup_sync

    first = _schedule_followup_sync(store, visit["id"])
    appointment = first["appointment"]
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-20T14:00"
    )

    record = store.get_visit_record(visit["id"])
    assert record["next_followup_date"] == "2026-12-20"

    again = _schedule_followup_sync(store, visit["id"])
    assert again["appointment_created"] is False
    assert again["appointment"]["id"] == appointment["id"]
    upcoming = [
        item for item in store.list_appointments(limit=100)
        if item["status"] == "upcoming"
    ]
    assert len(upcoming) == 1


def test_edited_note_appointment_still_dropped(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    from api_server import _schedule_followup_sync

    result = _schedule_followup_sync(store, visit["id"])
    appointment = result["appointment"]
    store.update_appointment_partial(
        appointment["id"], note="带医保卡"
    )

    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-25"
    )

    # 备注被用户改写，但就诊自己的复诊链接仍是归属证据
    assert store.get_appointment(appointment["id"]) is None
    record = store.get_visit_record(visit["id"])
    assert record["followup_appointment_id"] == 0


# ---------------------------------------------------------------- 家庭提示
def test_family_insights_daily_steps_and_latest_bp(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_profile("妈妈", {"sex": "女"})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "steps", "value1": 8000,
    })
    for hour, value in (("08:00", 2000), ("12:00", 3000)):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "steps", "value1": value,
            "measured_at": f"{today}T{hour}",
        })

    data = client.get("/api/health/insights", params={"days": 30}).json()
    steps = [
        item for item in data["insights"] if item["key"] == "metric_steps"
    ]
    assert "all_clear" not in [
        item["key"] for item in data["insights"]
    ]
    assert steps and "5000" in steps[0]["title"]

    # 最近一次血压取最新读数，而不是最旧的超标读数
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 130, "value2": 80,
    })
    for offset, value1, value2 in ((10, 160, 100), (3, 150, 95), (0, 120, 78)):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp", "value1": value1,
            "value2": value2, "measured_at": f"{day}T08:00",
        })
    data = client.get("/api/health/insights", params={"days": 30}).json()
    bp = [item for item in data["insights"] if item["key"] == "metric_bp"]
    assert bp and "120/78" in bp[0]["detail"]


def test_family_overview_attention_uses_daily_total(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_profile("妈妈", {"sex": "女"})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "steps", "value1": 8000,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 2000,
        "measured_at": f"{today}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 3000,
        "measured_at": f"{today}T12:00",
    })

    data = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert "5000" in row["attention"]["detail"]
    assert "3000" not in row["attention"]["detail"]


# ---------------------------------------------------------------- 代理/限流
def test_remote_client_cannot_spoof_proxy_header_to_skip_preblock(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "a-secure-random-token-123")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    client = TestClient(api_server.app, client=("203.0.113.9", 50000))
    try:
        first = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad",
            "X-Forwarded-Proto": "https",
        })
        second = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad2",
            "X-Forwarded-Proto": "https",
        })
        correct = client.get("/api/sessions", headers={
            "X-Medix-Token": "a-secure-random-token-123",
            "X-Forwarded-Proto": "https",
        })
        # 直连远程客户端伪造代理头不能跳过预锁定
        assert first.status_code == 401
        assert second.status_code == 429
        assert correct.status_code == 429
    finally:
        session_store.close()


def test_duplicate_xff_fields_use_last_appended(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    client = TestClient(api_server.app, client=("127.0.0.1", 50000))
    try:
        spoofed = client.get("/api/sessions", headers=[
            ("X-Forwarded-For", "127.0.0.1"),
            ("X-Forwarded-For", "203.0.113.9"),
        ])
        empty_first = client.get("/api/sessions", headers=[
            ("X-Forwarded-For", ""),
            ("X-Forwarded-For", "203.0.113.9"),
        ])
        # 应用不解析 uvicorn 头时，重复 XFF 字段也必须取最后追加的真实地址
        assert spoofed.status_code == 403
        assert empty_first.status_code == 403
    finally:
        session_store.close()


def test_rate_limit_key_uses_forwarded_address(monkeypatch):
    import api_server

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, address):
            self.headers = {"x-forwarded-for": address}
            self.client = FakeClient()

    first = api_server._rate_limit_key(FakeRequest("192.168.1.10"))
    second = api_server._rate_limit_key(FakeRequest("192.168.1.11"))
    assert first == "ip:192.168.1.10"
    assert second == "ip:192.168.1.11"


# ---------------------------------------------------------------- 前端
def test_clear_deleted_session_clears_thread():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    block = app_js[
        app_js.index("function clearDeletedSession"):
        app_js.index("async function deleteHistory")
    ]
    assert "els.thread.replaceChildren(els.empty);" in block
    assert "els.empty.hidden = false;" in block
    assert "resetRail();" in block


def test_appointment_writes_refresh_visits():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    for name in ("saveAppointment", "updateAppointmentStatus",
                 "deleteAppointment"):
        start = app_js.index(f"async function {name}(")
        end = app_js.index("\n}", start)
        block = app_js[start:end]
        assert "loadVisits" in block, name


def test_send_waits_for_deep_link_restore():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    send = app_js[
        app_js.index("async function send(question)"):
        app_js.index("async function send(question)") + 6000
    ]
    assert "if (deepLinkedSession) {" in send
    assert "await restoreCurrentSession();" in send
    assert "会诊记录尚未打开" in send


def test_explicit_context_clear_blocks_snapshot_restore():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "CONTEXT_CLEARED_KEY" in app_js
    assert "function markContextCleared()" in app_js
    assert "safeSessionGet(CONTEXT_CLEARED_KEY) !== '1'" in app_js
    clear_block = app_js[
        app_js.index("els.contextClear.addEventListener"):
        app_js.index("els.profileSelect.addEventListener")
    ]
    assert "markContextCleared();" in clear_block
    delete_block = app_js[
        app_js.index("async function deletePatientProfile"):
        app_js.index("async function applySelectedProfile")
    ]
    assert "markContextCleared();" in delete_block
    assert "detachSessionForProfileChange();" in delete_block
