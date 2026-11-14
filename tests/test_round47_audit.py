"""第四十七轮审查：孕期口语出血/中文孕周、头外伤「了/先/直接」语序、
口语亲属过滤、停药与英文否定、导入同秒兄弟与改挂编辑、预约标记
幂等/悬空清理、提醒改档解链、恢复改期去重、改期早于就诊、同预约
提醒去重、共享回环桶不再预锁定、家庭提示成员与未指定档案对比。"""
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


# ---------------------------------------------------------------- 分诊
def test_pregnancy_colloquial_bleeding_and_chinese_weeks():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕20周，阴道流了一点血",
        "孕20周，阴道有少量血",
        "孕20周，内裤上有点血",
        "孕20周，擦到血",
        "孕20周，有点血丝",
        "孕八周，阴道出血",
        "怀孕十二周，阴道出血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "emergency", text
        assert "孕产期出血" in result["matches"], text
    # 病史里的孕周也能触发（今天没重复孕周）
    result = detect_red_flags(
        patient_complaint_text("今天阴道流了一点血"), {"history": "孕20周"}
    )
    assert result["level"] == "emergency"
    # 血常规/血糖等非出血词不误报
    for text in ("今天血常规正常", "孕20周，血糖偏高"):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text


def test_head_trauma_lei_and_position_variants():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "昨晚摔了一跤，磕到了后脑勺",
        "后脑勺先着地",
        "后脑勺直接着地",
        "后脑勺重重着地",
        "后脑勺被磕了一下",
        "碰到了头",
    ):
        result = detect_red_flags(
            patient_complaint_text(text), {"medications": "华法林"}
        )
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_spoken_kin_filtered_both_directions():
    from core.triage import detect_red_flags, patient_complaint_text

    # 家人的抗凝药 + 患者本人外伤：不能拼接成患者急症
    result = detect_red_flags(
        patient_complaint_text("我爸吃华法林，我今天头撞了一下"), {}
    )
    assert result["level"] == "none"
    # 患者本人抗凝药 + 家人外伤：同样不能拼接
    result = detect_red_flags(
        patient_complaint_text("我吃华法林，我爸今天头撞到门框"), {}
    )
    assert result["level"] == "none"
    # 患者本人同时具备两个条件才是急症
    result = detect_red_flags(
        patient_complaint_text("我吃华法林，我今天摔了一跤磕到头"), {}
    )
    assert result["level"] == "emergency"


# ---------------------------------------------------------------- 用药
def test_stop_phrases_and_english_negation():
    from core.medication_safety import check_medication_safety

    for text in (
        "我停用了华法林，能吃布洛芬吗",
        "华法林我停了，能吃布洛芬吗",
        "华法林已停，能吃布洛芬吗",
        "我不吃华法林，能吃布洛芬吗",
        "我停用华法林两年了，能吃布洛芬吗",
        "warfarin was stopped 2 years ago. Can I take ibuprofen?",
        "I do not take warfarin anymore. Can I take ibuprofen?",
    ):
        result = check_medication_safety(text, {})
        assert result["level"] == "none", text


# ---------------------------------------------------------------- 导入
def test_same_second_siblings_stay_idempotent_with_id_drift(store):
    from memory.session_store import SessionStore

    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.add_reminder("测血压", "2026-12-01", profile="妈妈")
        source.add_reminder("吃降压药", "2026-12-01", profile="妈妈")
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE reminders SET created_at = '2026-06-01T08:00:00',"
                " updated_at = '2026-06-01T08:00:00'"
            )
        backup = source.export_data()
    finally:
        source.close()

    # 目标库先有一条提醒：本地 id = 备份 id + 1
    store.add_reminder("已有", "2026-11-01", profile="妈妈")
    first = store.import_data(backup)
    assert first.get("reminders") == 2
    titles = sorted(
        item["title"] for item in store.list_reminders(True)
    )
    assert titles == ["吃降压药", "已有", "测血压"]

    second = store.import_data(backup)
    assert second.get("reminders") == 0
    titles = sorted(
        item["title"] for item in store.list_reminders(True)
    )
    assert titles == ["吃降压药", "已有", "测血压"]

    victim = [
        item for item in store.list_reminders(False)
        if item["title"] == "吃降压药"
    ][0]
    store.delete_reminder(victim["id"])
    third = store.import_data(backup)
    assert third.get("reminders") == 1
    pending = sorted(
        item["title"] for item in store.list_reminders(False)
    )
    assert pending == ["吃降压药", "已有", "测血压"]


def test_import_moved_and_edited_records_not_resurrected(store):
    reminder = store.add_reminder("妈妈复查", "2026-12-01", profile="妈妈")
    backup = store.export_data()
    time.sleep(1.05)
    store.update_reminder(reminder["id"], profile="爸爸",
                          due_at="2026-12-09")
    result = store.import_data(backup)
    assert result.get("reminders") == 0
    rows = store.list_reminders(True)
    assert [(item["profile"], item["due_at"]) for item in rows] == [
        ("爸爸", "2026-12-09")
    ]

    medication = store.add_medication("妈妈", "氨氯地平", "5mg")
    backup = store.export_data()
    time.sleep(1.05)
    store.update_medication(
        medication["id"], profile="爸爸", dose="10mg"
    )
    result = store.import_data(backup)
    assert result.get("medications") == 0
    meds = store.list_medications(limit=20)
    assert [(item["profile"], item["dose"]) for item in meds] == [
        ("爸爸", "10mg")
    ]

    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00"
    )
    backup = store.export_data()
    time.sleep(1.05)
    store.update_appointment_partial(
        appointment["id"], profile="爸爸",
        scheduled_at="2026-12-20T09:00",
    )
    result = store.import_data(backup)
    assert result.get("appointments") == 0
    appts = store.list_appointments(None, None)
    assert [(item["profile"], item["scheduled_at"]) for item in appts] == [
        ("爸爸", "2026-12-20T09:00")
    ]


def test_appointment_marker_import_is_idempotent(store):
    from memory.session_store import SessionStore

    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        source.add_visit_record(
            "妈妈", visit_date="2026-09-01", diagnosis="糖尿病",
            next_followup_date="2026-12-14",
        )
        source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note="由就诊记录 #1 自动创建",
        )
        backup = source.export_data()
    finally:
        source.close()

    # 目标库先有就诊：主键漂移后 #1 会映射到本地 #2
    store.save_profile("妈妈", {"sex": "女"})
    store.add_visit_record(
        "妈妈", visit_date="2026-01-01", diagnosis="高血脂"
    )
    store.import_data(backup)
    notes = [
        item["note"] for item in store.list_appointments(None, None)
    ]
    assert notes == ["由就诊记录 #2 自动创建"]

    # 重复导入同一备份：本地标记不能被再当成备份主键二次映射
    store.import_data(backup)
    notes = [
        item["note"] for item in store.list_appointments(None, None)
    ]
    assert notes == ["由就诊记录 #2 自动创建"]


def test_delete_visit_strips_marker_and_import_clears_dangling(store):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.delete_visit_record(visit["id"])
    appointment = store.get_appointment(appointment_id)
    assert "由就诊记录" not in (appointment.get("note") or "")
    assert appointment.get("owner_visit_id") == 0

    # 导入的预约带着未映射的陈旧标记：新插入行必须清掉
    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note="由就诊记录 #1 自动创建",
        )
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(store.db_path) + ".dst")
    try:
        target.import_data(backup)
        rows = target.list_appointments(None, None)
        assert [item["note"] for item in rows] == [""]
        assert [item["owner_visit_id"] for item in rows] == [0]
    finally:
        target.close()


def test_reminder_move_clears_medication_and_vaccination_links(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    medication = store.add_medication("妈妈", "二甲双胍", "500mg")
    reminder = store.add_reminder(
        "吃二甲双胍", "2026-12-01", profile="妈妈",
        medication_id=medication["id"],
    )
    moved = store.update_reminder(reminder["id"], profile="爸爸")
    assert moved["medication_id"] == 0
    assert store.delete_medication(medication["id"]) is True
    assert store.get_reminder(reminder["id"]) is not None

    vaccination = store.add_vaccination(
        "妈妈", "流感疫苗", 1, "2026-01-01", "2026-12-01"
    )
    reminder = store.add_reminder(
        "打流感疫苗", "2026-12-01", profile="妈妈",
        vaccination_id=vaccination["id"],
    )
    moved = store.update_reminder(reminder["id"], profile="爸爸")
    assert moved["vaccination_id"] == 0
    assert store.delete_vaccination(vaccination["id"]) is True
    assert store.get_reminder(reminder["id"]) is not None


def test_restore_with_new_date_still_dedupes(store):
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
    # 恢复旧预约并改到新日期也不能绕过重复复诊守卫
    with pytest.raises(ValueError):
        store.update_appointment_partial(
            appointment_id, status="upcoming",
            scheduled_at="2026-12-25T09:00",
        )
    upcoming = store.list_appointments(None, "upcoming")
    assert len(upcoming) == 1


def test_reschedule_before_visit_date_degrades_appointment(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-11", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    # 改到早于就诊日的日期：不能把 next_followup_date 写坏
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-11-04T09:00"
    )
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["next_followup_date"] == "2026-12-14"
    appointment = store.get_appointment(appointment_id)
    assert appointment["owner_visit_id"] == 0
    assert "由就诊记录" not in (appointment.get("note") or "")
    # 就诊记录仍能正常编辑（自身校验不会被违反）
    assert store.update_visit_record_partial(
        visit["id"], diagnosis="高血压 2 级"
    ) is not None


def test_reminder_import_dedupes_same_appointment_title(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-15T09:00"
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-12-14", profile="妈妈",
        appointment_id=appointment["id"],
    )
    backup = store.export_data()
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2027-01-13T09:00"
    )
    store.delete_reminder(reminder["id"])
    store.add_reminder(
        "复诊：高血压", "2027-01-12", profile="妈妈",
        appointment_id=appointment["id"],
    )
    result = store.import_data(backup)
    assert result.get("reminders") == 0
    pending = [
        item for item in store.list_reminders(False)
        if item["title"] == "复诊：高血压"
    ]
    assert len(pending) == 1
    assert pending[0]["due_at"] == "2027-01-12"


# ---------------------------------------------------------------- 代理
def test_shared_loopback_bucket_never_blocks_correct_token(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(2, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    try:
        for _ in range(2):
            wrong = client.get(
                "/api/sessions", headers={"X-Medix-Token": "bad"}
            )
            assert wrong.status_code == 401
        # 共享桶已满：错误令牌 429，但正确令牌不能被预锁定
        limited = client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad-again"}
        )
        assert limited.status_code == 429
        correct = client.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        )
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_forwarded_address_cannot_prelock_victim(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    try:
        # 多级本机代理：最后一跳在末尾追加 127.0.0.1，左侧地址来自
        # 客户端，可被伪造成受害者地址；这种情况不能预锁定
        spoofed = [
            ("X-Forwarded-For", "192.168.1.50"),
            ("X-Forwarded-For", "127.0.0.1"),
        ]
        for _ in range(10):
            client.get("/api/sessions", headers=[
                ("X-Medix-Token", "bad"),
                *spoofed,
            ])
        # 受害者携带正确令牌：不能因为别人塞满的桶被 429
        victim = client.get("/api/sessions", headers=[
            ("X-Medix-Token", token),
            *spoofed,
        ])
        assert victim.status_code == 200
        # 但同一伪造地址继续猜令牌仍会被限流
        attacker = client.get("/api/sessions", headers=[
            ("X-Medix-Token", "bad-more"),
            *spoofed,
        ])
        assert attacker.status_code == 429
    finally:
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_family_insights_label_members(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    store.add_symptom("妈妈", "胸痛", 9)
    store.add_symptom("爸爸", "呼吸困难", 8)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    visit = store.add_visit_record(
        "爸爸", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date=yesterday,
    )
    assert visit is not None

    data = client.get("/api/health/insights", params={"days": 30}).json()
    insights = {item["key"]: item for item in data["insights"]}
    severe = insights["symptoms_severe"]
    assert "妈妈：胸痛 9/10" in severe["detail"]
    assert "爸爸：呼吸困难 8/10" in severe["detail"]
    followup = insights["followup_overdue"]
    assert "爸爸：" in followup["detail"]

    weekly = client.get("/api/reports/weekly", params={"days": 30}).json()
    severe_profiles = {
        item.get("profile") for item in weekly["symptoms"]["severe_items"]
    }
    assert severe_profiles == {"妈妈", "爸爸"}
    overdue = weekly["visits"]["overdue_followups"]
    assert overdue and overdue[0].get("profile") == "爸爸"


def test_family_vaccine_upcoming_labels_member(client_and_store):
    client, store = client_and_store
    store.save_profile("孩子", {"sex": "男"})
    due = (datetime.now() + timedelta(days=6)).strftime("%Y-%m-%d")
    store.add_vaccination(
        "孩子", "流感疫苗", 2, "2026-01-01", due
    )
    data = client.get("/api/health/insights", params={"days": 30}).json()
    insights = {item["key"]: item for item in data["insights"]}
    assert "孩子" in insights["vaccine_upcoming"]["title"]
    weekly = client.get("/api/reports/weekly", params={"days": 30}).json()
    upcoming = weekly["vaccinations"]["upcoming"]
    assert upcoming and upcoming[0].get("profile") == "孩子"


def test_family_compare_includes_unassigned_records(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    start = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    store.add_metric("", "steps", 6000, measured_at=f"{start}T08:00")
    store.add_metric("", "steps", 7000, measured_at=f"{today}T08:00")

    data = client.get(
        "/api/family/compare", params={"metric": "steps", "days": 7}
    ).json()
    rows = data["profiles"]
    assert len(rows) == 1
    assert rows[0]["name"] == ""
    assert rows[0]["count"] == 2

    csv = client.get(
        "/api/family/compare/export.csv",
        params={"metric": "steps", "days": 7},
    ).text
    assert "未指定档案" in csv


def test_daily_metric_summary_has_no_per_record_prev_avg(store):
    today = datetime.now()
    old = (today - timedelta(days=10)).strftime("%Y-%m-%dT08:00")
    yesterday = (today - timedelta(days=1)).strftime("%Y-%m-%dT08:00")
    store.add_metric("妈妈", "steps", 6000, measured_at=old)
    store.add_metric("妈妈", "steps", 8000, measured_at=yesterday)
    summary = store.metric_summary("妈妈", "steps", 7)
    assert summary["count"] == 1
    assert summary["value1"]["prev_avg"] is None


# ---------------------------------------------------------------- 前端
def test_frontend_deep_link_open_session_keeps_snapshot():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "const deepLinkOpen = deepLinkedSession && sameSession;" in app_js
    assert "restoreContext: deepLinkOpen || !sameSession" in app_js
    assert "if (deepLinkOpen) {" in app_js
    assert "if (!deepLinkOpen) consumeDeepLink();" in app_js


def test_frontend_pending_draft_survives_reload_and_reconcile():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "owner === '' && pendingSessionReset" in app_js
    assert "const pendingNewSession = owner === '' && (" in app_js
    assert "name.textContent = row.name || '未指定档案';" in app_js
