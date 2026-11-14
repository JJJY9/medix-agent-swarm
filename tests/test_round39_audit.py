"""第三十九轮审查：定义问句与「从未有过」急症漏报、近期停药意图、
导入同秒组亏空、复诊归属与改期同步、代理头拼接、家庭用药安全与
会诊窗口、风险最高值、深链持久化、记忆清理队列与附件目录。"""
import tempfile
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
def test_definition_suffix_keeps_acute_multisymptom_prefix():
    from core.triage import detect_red_flags, patient_complaint_text

    kept = patient_complaint_text("突发胸痛、呼吸困难是什么病")
    assert kept and "胸痛" in kept
    assert detect_red_flags(kept)["level"] == "emergency"

    fever = detect_red_flags(
        patient_complaint_text("发烧39度是什么病"), {"age": "2个月"}
    )
    assert "3 个月以下婴儿发热" in fever["matches"]

    trauma = detect_red_flags(
        patient_complaint_text("摔到头是什么病"),
        {"medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in trauma["matches"]

    # 单一术语的定义问句仍然过滤
    assert patient_complaint_text("咯血是什么病") == ""
    assert patient_complaint_text("胸痛是什么病") == ""


def test_never_before_superlative_is_positive():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "突然出现从未有过的胸痛",
        "前所未有的胸痛",
        "从未出现过的呼吸困难",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    assert detect_red_flags(
        patient_complaint_text("从未有过胸痛")
    )["level"] == "none"


def test_recent_past_medication_is_current():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "过去几天吃华法林，今天撞到头",
        "过去一周吃华法林，今天头撞到门上了",
        "之前几天吃华法林，今天头撞到门上",
        "以前吃阿司匹林现在吃华法林，今天撞到头了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text

    result = detect_red_flags(
        patient_complaint_text("以前吃华法林，今天头撞到门上"), {}
    )
    assert "抗凝治疗期间头部外伤" not in result["matches"]


def test_topical_steroid_chunk_does_not_hide_oral_steroid():
    from core.medication_safety import (
        check_medication_safety,
        find_drugs,
    )

    assert find_drugs(
        "氢化可的松乳膏和泼尼松，阿司匹林", "corticosteroids"
    ) == ["泼尼松"]
    result = check_medication_safety(
        "正在用氢化可的松乳膏和泼尼松，能吃阿司匹林吗", {}
    )
    assert result.get("level") == "warning"
    # 单纯外用激素仍不算全身用药
    assert find_drugs("氢化可的松乳膏", "corticosteroids") == []


# ---------------------------------------------------------------- 导入
def test_import_same_second_twin_does_not_copy_survivor(tmp_path):
    from memory.session_store import SessionStore

    store = SessionStore(str(tmp_path / "reminders.db"))
    try:
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
        # 同秒孪生删一改一：被删记录必须补回，本地编辑版保留；
        # 无法区分时允许保留旧副本，但不能丢失被删记录
        assert result["reminders"] >= 1
        assert "吃药A" in titles
        assert "吃药B（改）" in titles
    finally:
        store.close()


def test_import_same_minute_other_member_not_ambiguous():
    from memory.session_store import SessionStore

    import tempfile

    store = SessionStore(
        str(Path(tempfile.mkdtemp()) / "family.db")
    )
    try:
        store.save_profile("妈妈", {})
        store.save_profile("爸爸", {})
        mom = store.add_reminder("妈妈复查", "2026-12-01", "", "", "妈妈")
        dad = store.add_reminder("爸爸复查", "2026-12-02", "", "", "爸爸")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET created_at = ?, updated_at = ?"
                " WHERE id IN (?, ?)",
                (
                    "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                    mom["id"], dad["id"],
                ),
            )
        backup = store.export_data()
        assert store.update_reminder(
            mom["id"], title="妈妈复查（改）", due_at="2026-12-09"
        )

        result = store.import_data(backup)

        titles = [
            item["title"] for item in store.list_reminders(False)
        ]
        assert result["reminders"] == 0
        assert sorted(titles) == ["妈妈复查（改）", "爸爸复查"]
    finally:
        store.close()


def test_import_backup_keeps_updated_at_roundtrip(store):
    store.save_profile("妈妈", {})
    metric = store.add_metric(
        "妈妈", "weight", 63, measured_at="2026-11-01T08:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET updated_at = ? WHERE id = ?",
            ("2026-11-02T08:00:00", metric["id"]),
        )
    backup = store.export_data()
    exported = backup["health_metrics"][0]
    assert exported["updated_at"] == "2026-11-02T08:00:00"


# ---------------------------------------------------------------- 复诊
def test_followup_relink_requires_date_match(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-25", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    from api_server import _schedule_followup_sync

    first = _schedule_followup_sync(store, visit["id"])
    backup = store.export_data()
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-11-09"
    )

    store.import_data(backup)

    record = store.get_visit_record(visit["id"])
    # 恢复出的旧日期预约不能挂回已改期的就诊，也不能留下旧提醒
    assert record["followup_scheduled"] is False
    assert record["followup_appointment_id"] == 0
    assert first["appointment"]["id"] not in {
        item["appointment_id"]
        for item in store.list_reminders(False, "妈妈")
    }


def test_note_edit_then_reschedule_syncs_visit_date(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    from api_server import _schedule_followup_sync

    first = _schedule_followup_sync(store, visit["id"])
    appointment = first["appointment"]
    store.update_appointment_partial(
        appointment["id"], note="带医保卡 记得空腹"
    )

    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-20T14:00"
    )

    record = store.get_visit_record(visit["id"])
    assert record["next_followup_date"] == "2026-12-20"
    again = _schedule_followup_sync(store, visit["id"])
    assert again["appointment_created"] is False
    assert again["appointment"]["id"] == appointment["id"]


def test_restore_cancelled_appointment_rejects_duplicate(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    from api_server import _schedule_followup_sync

    first = _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], status="cancelled"
    )
    second = _schedule_followup_sync(store, visit["id"])
    assert second["appointment_created"] is True

    with pytest.raises(ValueError):
        store.update_appointment_partial(
            first["appointment"]["id"], status="upcoming"
        )

    upcoming = [
        item for item in store.list_appointments(limit=100)
        if item["status"] == "upcoming"
    ]
    assert len(upcoming) == 1


# ---------------------------------------------------------------- 家庭/报表
def test_family_overview_includes_drug_interaction(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.add_medication("妈妈", "华法林", dose="3mg")
    store.add_medication("妈妈", "阿司匹林", dose="100mg")

    data = client.get("/api/family/overview").json()

    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    safety = row["medication_lab_safety"]
    assert safety["count"] >= 1
    assert any("华法林" in alert["title"] for alert in safety["alerts"])
    assert row["attention"]["level"] != "ok"


def test_continued_old_session_counts_as_recent(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "old-session", "胸痛怎么办",
        "风险等级：高危，请立即就医。", profile="妈妈",
    )
    old_day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (f"{old_day}T08:00:00", f"{old_day}T08:00:00", "old-session"),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (f"{old_day}T08:00:00", "old-session"),
        )
    # 今天继续旧会话：只更新 updated_at 与消息时间
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_exchange(
        "old-session", "今天又胸痛了",
        "风险等级：高危，请立即就医。", profile="妈妈",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            (f"{old_day}T08:00:00", "old-session"),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?"
            " AND created_at >= ?",
            (f"{today}T09:00:00", "old-session", f"{today}T00:00:00"),
        )

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["count"] == 1
    assert row["attention"]["level"] != "ok"

    insights = client.get("/api/health/insights", params={"days": 30}).json()
    assert any(
        item["key"] == "consultation_risk" for item in insights["insights"]
    )


def test_risk_keeps_highest_answer_after_thanks(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "risk-session", "胸痛怎么办",
        "风险等级：高危，请立即就医。", profile="妈妈",
    )
    store.save_exchange(
        "risk-session", "好的谢谢", "不客气，注意休息。", profile="妈妈",
    )

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == "high"

    sessions = client.get("/api/sessions", params={"days": 7}).json()
    target = next(
        item for item in sessions["sessions"]
        if item["id"] == "risk-session"
    )
    assert target["risk"] == "high"


def test_stale_metric_does_not_warn_in_family_overview(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "weight", "value1": 70,
    })
    old = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 80,
        "measured_at": f"{old}T08:00",
    })

    data = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert row["attention"]["level"] == "ok"


# ---------------------------------------------------------------- 代理
def test_forwarded_unclosed_quote_fails_closed(monkeypatch):
    import api_server

    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, value):
            self.headers = {"forwarded": value}
            self.client = FakeClient()

    assert api_server._forwarded_client_address(FakeRequest(
        'for=127.0.0.1;x=", for=203.0.113.9;proto=https'
    )) == ""
    assert api_server._forwarded_client_address(FakeRequest(
        'for="203.0.113.9";proto=https'
    )) == "203.0.113.9"


def test_extra_address_header_does_not_skip_preblock(
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
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    try:
        # 客户端额外自带一个非受信地址头：仍以声明的 XFF 最右项
        # （代理追加写入）为准，不能借此跳过预锁定
        contradicting = {
            "X-Forwarded-For": "203.0.113.9",
            "X-Real-IP": "198.51.100.7",
        }
        first = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "bad", **contradicting},
        )
        assert first.status_code == 401
        correct = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "a-secure-random-token-123",
                **contradicting,
            },
        )
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
    monkeypatch.setenv(
        "MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for"
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    try:
        spoofed = client.get("/api/sessions", headers=[
            ("X-Forwarded-For", "127.0.0.1"),
            ("X-Forwarded-For", "203.0.113.9"),
        ])
        assert spoofed.status_code == 403
    finally:
        session_store.close()


# ---------------------------------------------------------------- 记忆/附件
def test_memory_cleanup_keeps_queue_when_previously_configured(
    monkeypatch, tmp_path
):
    import api_server
    import memory
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    session_store.set_app_state("mem0_configured", "1")
    try:
        class FakeMemory:
            enabled = False
            api_key_configured = False

        assert api_server._memory_cleanup_possible(FakeMemory()) is False

        session_store.set_app_state("mem0_configured", "")
        assert api_server._memory_cleanup_possible(FakeMemory()) is True
    finally:
        session_store.close()


def test_default_attachment_dir_survives_owner_marker_loss(tmp_path):
    from core.storage_paths import attachment_dir_for_db

    parent = tmp_path
    default_db = parent / "sessions.db"
    default_db.touch()
    dedicated = parent / "sessions.db-attachments"
    dedicated.mkdir()
    legacy = parent / "attachments"
    legacy.mkdir()
    # 标记被自定义库占用后该库文件消失（过期标记）
    (legacy / ".medix-attachments-owner").write_text(
        "family.db", encoding="utf-8"
    )

    assert attachment_dir_for_db(default_db) == dedicated


# ---------------------------------------------------------------- 前端
def test_deep_link_pending_persisted_across_reload():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "DEEP_LINK_PENDING_KEY" in app_js
    assert "pendingDeepLinkSession === initialSessionFromUrl" in app_js
    assert "function consumeDeepLink()" in app_js
    assert "safeSessionRemove(DEEP_LINK_PENDING_KEY);" in app_js


def test_send_has_pre_restore_lock_and_session_guard():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    send = app_js[
        app_js.index("async function send(question)"):
        app_js.index("async function send(question)") + 3000
    ]
    assert "let sendInFlight = false;" in app_js
    assert "if (busy || sendInFlight)" in send
    assert "const sendSession = sessionId;" in send
    assert "const sendSeq = openSessionSeq;" in send
    assert "sessionId !== sendSession" in send
    assert "openSessionSeq !== sendSeq" in send


def test_empty_deep_link_respects_user_edits():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    block = app_js[
        app_js.index("const messages = data.messages || [];"):
        app_js.index("const newestAssistantId = messages.reduce")
    ]
    # 深链编辑标记刷新后仍生效（第五十一轮持久化）
    assert "safeSessionGet(DEEP_LINK_EDITED_KEY) === sessionId" in block
    assert "setPendingSessionReset(true);" in block
    assert "applyPatientContext({});" in block
