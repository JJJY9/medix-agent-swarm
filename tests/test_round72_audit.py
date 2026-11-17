"""第七十二轮审查：预产期/时段定义问句/枚举阳性后缀/科普引用无主语、
家属提问框架、近期开始不吃、「停掉」、同秒删一编一补插预算、提醒 CSV
判重、共享复诊改期/迁移、预算来源哨兵、逾期总数、档案名校验全路径、
墓碑撤销、include_previous 与前端建会话/在途标记。"""
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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


def _freeze_instance(session_store, now=CREATED):
    session_store._now = lambda: now


# ---------------------------------------------------------------- 分诊
def test_due_date_and_full_term_bleeding():
    for text in (
        "预产期还有两周，阴道出血",
        "明天预产期，内裤上有血",
        "足月了，阴道出血",
    ):
        assert "孕产期出血" in _red_flags(text)["matches"], text
    # 往年预产期不算当前妊娠
    assert _red_flags("预产期是去年的，现在肚子痛")["level"] == "none"


def test_definition_suffix_with_urgent_help():
    for text in (
        "胸痛是什么病？怎么办？",
        "胸痛是什么病？严重吗？",
        "胸痛怎么办是什么病",
    ):
        assert "胸痛" in _red_flags(text)["matches"], text
    assert _red_flags("胸痛是什么病")["level"] == "none"


def test_enum_relief_and_half_hour_duration():
    for text in (
        "无发热、胸痛一直没缓解",
        "没有发烧、胸痛没有缓解",
        "无发热、胸痛未见缓解",
        "无发热、胸痛半个多小时",
        "无发热、胸痛持续半个多小时",
    ):
        assert "胸痛" in _red_flags(text)["matches"], text


def test_education_no_subject_before_quote():
    for text in (
        "这几天胸痛在网上查了说是心梗前兆",
        "昨晚咯血网上查了说是肺癌",
        "刚刚胸痛网上说可能是心梗",
    ):
        assert _red_flags(text)["level"] != "none", text
    assert _red_flags("我最近在网上看到胸痛要马上就医")["level"] == "none"


def test_triage_stopped_dropped_verb():
    for text in (
        "华法林停掉半年了，今天头撞到门框",
        "我把华法林停掉半年了，今天头撞到门框",
        "甲氨蝶呤停掉五年了，今天发烧39度",
    ):
        assert _red_flags(text)["level"] == "none", text
    for text in (
        "华法林停了半年了，今天头撞到门框",
        "华法林停掉半年了，能吃布洛芬吗",
    ):
        assert _red_flags(text)["level"] == "none", text


# ---------------------------------------------------------------- 用药
def test_medication_recent_started_not_eating():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "我昨天开始不吃华法林了，能吃布洛芬吗", {}
    )["level"] == "warning"
    # 裸「不吃」仍是已停药
    assert check_medication_safety(
        "华法林我不吃了，能吃布洛芬吗", {}
    )["level"] == "none"


def test_family_question_frame_not_patient_use():
    from core.medication_safety import check_medication_safety

    for text in (
        "我想问一下我爸吃华法林，我能吃布洛芬吗",
        "我想问一下我妈对青霉素过敏，我能吃头孢吗",
        "医生嘱咐我爸每天吃华法林，我能吃布洛芬吗",
        "我爸吃华法林我能吃布洛芬吗",
        "我爸吃华法林，我能吃布洛芬吗",
    ):
        assert check_medication_safety(
            text, {"age": "35岁"}
        )["level"] == "none", text
    # 患者本人/并列主语/患儿档案仍保留
    for text, context in (
        ("我和我爸都吃华法林，能吃布洛芬吗", {"age": "35岁"}),
        ("我儿子在吃华法林，他能吃布洛芬吗", {"age": "3岁"}),
        ("我儿子对阿司匹林过敏，他能吃布洛芬吗", {"age": "2岁"}),
        ("我妈建议我吃华法林，能吃布洛芬吗", {}),
    ):
        assert check_medication_safety(
            text, context
        )["level"] == "warning", text


# ---------------------------------------------------------------- 导入
def test_same_second_delete_and_edit_inserts_only_missing(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_appointment(
            "妈妈", "复诊", scheduled_at="2026-12-01T09:00"
        )
        source.add_appointment(
            "妈妈", "复诊", scheduled_at="2026-12-02T09:00"
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        rows = {
            item["scheduled_at"]: item["id"]
            for item in target.list_appointments("妈妈", None)
        }
        target.delete_appointment(rows["2026-12-01T09:00"])
        target.update_appointment_partial(
            rows["2026-12-02T09:00"], scheduled_at="2026-12-09T09:00"
        )
        result = target.import_data(backup)
        assert result["appointments"] == 1
        final = sorted(
            item["scheduled_at"]
            for item in target.list_appointments("妈妈", None)
            if item["status"] == "upcoming"
        )
        assert final == ["2026-12-01T09:00", "2026-12-09T09:00"]
    finally:
        target.close()


def test_reminder_csv_dedupe_keeps_done_and_link_rows(store):
    store.save_profile("妈妈", {})
    first = store.import_data({"reminders": [
        {"title": "吃药", "due_at": "2026-12-02T09:00", "note": "n",
         "profile": "妈妈", "advance_days": 1, "done": 0},
        {"title": "吃药", "due_at": "2026-12-02T09:00", "note": "n",
         "profile": "妈妈", "advance_days": 1, "done": 1},
    ]})
    assert first["reminders"] == 2
    appt_a = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-01T09:00"
    )["id"]
    appt_b = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-02T09:00"
    )["id"]
    linked = store.import_data({"reminders": [
        {"title": "复诊提醒", "due_at": "2026-11-30T09:00",
         "profile": "妈妈", "appointment_id": appt_a},
        {"title": "复诊提醒", "due_at": "2026-11-30T09:00",
         "profile": "妈妈", "appointment_id": appt_b},
    ]})
    assert linked["reminders"] == 2


def test_shared_followup_reschedule_syncs_all_visits(store):
    store.save_profile("妈妈", {})
    visit_a = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="高血压",
        next_followup_date=_date(-2),
    )["id"]
    visit_b = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="糖尿病",
        next_followup_date=_date(-2),
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at=_date(-2) + "T09:00",
    )["id"]
    reminder = store.add_reminder(
        "复诊提醒", _date(-2), profile="妈妈",
        appointment_id=appointment,
    )["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?,"
            " followup_reminder_id = ? WHERE id IN (?, ?)",
            (appointment, reminder, visit_a, visit_b),
        )
    store.update_appointment_partial(
        appointment, scheduled_at=_date(10) + "T09:00"
    )
    rows = {
        item["id"]: item for item in store.list_visit_records("妈妈")
    }
    assert rows[visit_a]["next_followup_date"] == _date(10)
    assert rows[visit_b]["next_followup_date"] == _date(10)


def test_visit_profile_change_unlinks_shared_visits(store):
    store.save_profile("P1", {})
    store.save_profile("P2", {})
    visit_a = store.add_visit_record(
        "P1", visit_date=_date(-10), diagnosis="高血压",
        next_followup_date=_date(-2),
    )["id"]
    visit_b = store.add_visit_record(
        "P1", visit_date=_date(-10), diagnosis="糖尿病",
        next_followup_date=_date(-2),
    )["id"]
    appointment = store.add_appointment(
        "P1", "复诊：高血压", scheduled_at=_date(-2) + "T09:00",
        note=f"由就诊记录 #{visit_a} 自动创建",
        owner_visit_id=visit_a,
    )["id"]
    reminder = store.add_reminder(
        "复诊提醒", _date(-2), profile="P1",
        appointment_id=appointment,
    )["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?,"
            " followup_reminder_id = ? WHERE id = ?",
            (appointment, reminder, visit_b),
        )
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?,"
            " followup_reminder_id = ? WHERE id = ?",
            (appointment, reminder, visit_a),
        )
    store.update_visit_record_partial(visit_a, profile="P2")
    assert store.get_appointment(appointment)["profile"] == "P2"
    assert int(
        store.get_visit_record(visit_b)["followup_appointment_id"] or 0
    ) == 0


def test_invalid_profile_rejected_across_write_paths(store, tmp_path):
    assert store.save_profile("..", {}) is None
    assert store.save_profile("妈妈/爸爸", {}) is None
    assert store.set_session_profile("s-bind", "..") is False
    assert store.add_attachment(
        "..", "other", 1, "a.txt", "a.txt", "text/plain", 10
    ) is None
    assert store.import_data({"reminders": [{
        "title": "吃药", "due_at": "2026-12-01T09:00",
        "profile": "妈妈/爸爸", "done": 0,
    }]})["invalid"] == 1
    assert store.import_data({"sessions": [{
        "id": "bad1", "title": "x", "profile": "..",
        "created_at": CREATED, "updated_at": CREATED,
    }], "messages": []})["invalid"] == 1
    assert store.import_data({"metric_targets": [{
        "profile": "..", "metric": "bp", "value1": 140, "value2": 90,
    }]})["invalid"] == 1
    assert store.import_data({"usage_events": [{
        "session_id": "x", "profile": "..", "prompt_tokens": 1,
        "completion_tokens": 1, "total_tokens": 2, "llm_calls": 1,
        "created_at": CREATED,
    }]})["invalid"] == 1


def test_reminder_recreated_clears_delete_tombstone(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date=_date(-10), diagnosis="甲",
        next_followup_date=_date(10),
    )["id"]
    first = _schedule_followup_sync(store, visit)
    appointment = first["appointment"]["id"]
    assert store.delete_reminder(first["reminder"]["id"]) is True
    store.add_reminder(
        "复诊提醒", _date(9), profile="妈妈",
        appointment_id=appointment,
    )
    store.update_appointment_partial(appointment, status="cancelled")
    store.update_appointment_partial(appointment, status="upcoming")
    pending = [
        item for item in store.list_reminders(False, "妈妈")
        if int(item["appointment_id"] or 0) == appointment
    ]
    assert len(pending) == 1
    assert int(
        store.get_visit_record(visit)["followup_reminder_id"] or 0
    ) > 0


# ---------------------------------------------------------------- 认证
def test_budget_source_sentinel_falls_back_to_peer(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r72-secret")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(3, window=300),
    )
    client = TestClient(
        api_server.app, base_url="http://[2001:db8:5a::1]",
        client=("2001:db8:5a::a1", 59000),
    )
    try:
        for _ in range(3):
            assert client.get(
                "/api/sessions", headers={"X-Medix-Token": "bad"}
            ).status_code == 401
        assert client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        ).status_code == 429
        # 加一个客户端自带的地址头不能绕过全局预算
        assert client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": "198.51.100.9",
            },
        ).status_code == 429
    finally:
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_overdue_followup_count_not_truncated(monkeypatch, store):
    import asyncio

    import api_server
    import memory

    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    for index in range(6):
        name = f"P{index}"
        store.save_profile(name, {})
        store.add_visit_record(
            name, visit_date=_date(-20), diagnosis="慢病",
            next_followup_date=_date(-10 + index),
        )
    report = asyncio.run(
        api_server._weekly_report_impl(None, 30, 400, True)
    )
    insights = api_server._build_health_insights(report, None)
    item = next(
        row for row in insights if row["key"] == "followup_overdue"
    )
    assert "6 项复诊已逾期" in item["title"]
    assert "等 6 项" in item["detail"]


def test_include_previous_false_hides_prev_consultations(
    monkeypatch, store
):
    import asyncio

    import api_server
    import memory

    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    store.save_profile("妈妈", {})
    prev_day = (datetime.now() - timedelta(days=10)).strftime(
        "%Y-%m-%dT09:00:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile, tags) VALUES (?,?,?,?,?,?)",
            ("s-prev", "旧会话", prev_day, prev_day, "妈妈", "[]"),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, id, role, content,"
            " metadata, created_at) VALUES (?,?,?,?,?,?)",
            ("s-prev", 1, "user", "问题", "{}", prev_day),
        )
    report = asyncio.run(
        api_server._weekly_report_impl("妈妈", 7, 100, False)
    )
    assert report["consultations"]["prev_count"] == 0


# ---------------------------------------------------------------- 前端
def test_frontend_creating_session_and_inflight_profile():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 建会话在途时提交保持 sendInFlight（阻止重复提交/刷新丢主诉）
    block = source[
        source.index("if (creatingSessionPromise) {"):
    ][:400]
    assert "sendInFlight = true;" in block
    assert "await creatingSessionPromise" in block
    # INFLIGHT 写入使用发送档案快照
    assert source.count("profile: sendProfile || ''") == 2
    # 待重置换档的首次提问不能被旧会话去重标记吞掉
    assert (
        "composerSubmit && inflightPersistedText && !pendingSessionReset"
        in source
    )
