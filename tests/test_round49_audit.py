"""第四十九轮审查：定义问句修饰白名单、家属宾语归属、婴儿/共享发热、
刷牙出血、英文否定停药与 quit 窗口、中文「没有停」、提醒漂移跨档案、
同秒孪生编辑、改期提醒复活、取消预约提醒挂接、回环别名限流、多级代理
预锁定、受信跳分桶、档案补充用药参与安全、血压峰值与目标、每日累计
统计、旧版记忆命名空间 gate 与前端深链/共享指针。"""
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
def test_definition_question_keeps_symptom_modifiers():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "胸痛越来越重是什么病",
        "活动后胸痛是什么病",
        "夜间胸痛是什么病",
        "胸痛加剧是什么病",
        "一侧肢体无力是什么病",
    ):
        kept = patient_complaint_text(text)
        assert detect_red_flags(kept)["level"] == "emergency", text
    # 单一术语的定义问句仍按科普过滤
    for text in ("胸痛是什么病", "咯血是什么病"):
        kept = patient_complaint_text(text)
        assert kept == "", text


def test_family_object_clause_keeps_patient_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我吃华法林，我扶我爸起来时头撞到门框",
        "我吃华法林，照顾我妈时摔到头",
        "我吃华法林，给我爸拿药时头撞到柜子",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    for text in (
        "我吃华法林，我爸今天头撞到门框",
        "我爸吃华法林，我今天头撞了一下",
        "我吃华法林，我给我爸打电话说他发烧",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text


def test_narrated_and_shared_fever_count_for_patient():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("我妈说宝宝发烧39度"), {"age": "2个月"}
    )
    assert "3 个月以下婴儿发热" in result["matches"]
    result = detect_red_flags(
        patient_complaint_text("我和我爸都发烧了"), {"history": "化疗"}
    )
    assert "免疫功能低下伴发热" in result["matches"]
    assert detect_red_flags(
        patient_complaint_text("我妈发烧了，我没事"), {"history": "化疗"}
    )["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("我爸发烧39度"), {"age": "2个月"}
    )["level"] == "none"


def test_brushing_bleeding_not_pregnancy_emergency():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("孕20周，刷牙时有点血丝"), {}
    )
    assert result["level"] == "none"
    result = detect_red_flags(
        patient_complaint_text("孕20周，阴道流了一点血"), {}
    )
    assert "孕产期出血" in result["matches"]


# ---------------------------------------------------------------- 用药
def test_english_negated_stop_and_quit_window():
    from core.medication_safety import check_medication_safety

    for text in (
        "I have not stopped warfarin. Can I take ibuprofen?",
        "I never stopped taking warfarin. Can I take ibuprofen?",
        "I am not off warfarin. Can I take ibuprofen?",
        "I quit warfarin two days ago. Can I take ibuprofen?",
        "我没有停华法林，牙疼能吃布洛芬吗",
        "不要停华法林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    for text in (
        "I stopped warfarin two years ago. Can I take ibuprofen?",
        "I quit warfarin two years ago. Can I take ibuprofen?",
        "华法林我不吃了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text


# ---------------------------------------------------------------- 导入
def test_deleted_reminder_not_claimed_by_other_member(store):
    from memory.session_store import SessionStore

    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.save_profile("爸爸", {"sex": "男"})
        source.add_reminder("提醒A", "2026-12-01", profile="妈妈")
        source.add_reminder("提醒B", "2026-12-01", profile="爸爸")
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE reminders SET created_at = '2026-06-01T08:00:00',"
                " updated_at = '2026-06-01T08:00:00'"
            )
        backup = source.export_data()
    finally:
        source.close()

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    placeholder = store.add_reminder("占位", "2026-11-01", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = '2026-01-01T00:00:00',"
            " updated_at = '2026-01-01T00:00:00' WHERE id = ?",
            (placeholder["id"],),
        )
    store.import_data(backup)
    mom = [
        item for item in store.list_reminders(True)
        if item["title"] == "提醒A"
    ][0]
    store.delete_reminder(mom["id"])
    result = store.import_data(backup)
    assert result.get("reminders") == 1
    rows = store.list_reminders(True)
    titles = sorted(item["title"] for item in rows)
    assert titles == sorted(["提醒A", "提醒B", "占位"])
    assert len([
        item for item in rows if item["title"] == "提醒B"
    ]) == 1


def test_same_second_edited_twins_not_rebuilt(store):
    store.save_profile("妈妈", {"sex": "女"})
    first = store.add_reminder("复查", "2026-12-01", profile="妈妈")
    second = store.add_reminder("复查", "2026-12-02", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    time.sleep(1.05)
    store.update_reminder(first["id"], due_at="2026-12-11")
    store.update_reminder(second["id"], due_at="2026-12-12")
    result = store.import_data(backup)
    assert result.get("reminders") == 0
    rows = store.list_reminders(True)
    assert sorted(item["due_at"] for item in rows) == [
        "2026-12-11", "2026-12-12",
    ]


def test_reschedule_does_not_resurrect_deleted_reminder(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-15T09:00"
    )
    # 从未创建提醒：改期不应新建
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-20T09:00"
    )
    assert store.list_reminders(False) == []
    # 有已完成提醒：改期重建待办
    reminder, created = store.ensure_reminder(
        "复诊：复诊", "2026-12-19", "", "", "妈妈",
        0, 0, 0, appointment["id"], False,
    )
    assert created
    store.complete_reminder(reminder["id"])
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-25T09:00"
    )
    pending = store.list_reminders(False)
    assert len(pending) == 1
    assert pending[0]["appointment_id"] == appointment["id"]


def test_reminder_import_skips_cancelled_appointment_attach(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-15T09:00"
    )
    store.update_appointment_partial(appointment["id"], status="cancelled")
    backup = {"reminders": [{
        "id": 5, "profile": "妈妈", "title": "复诊：高血压",
        "due_at": "2026-12-14", "appointment_id": appointment["id"],
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-01T08:00:00",
    }]}
    result = store.import_data(backup)
    assert result.get("reminders") == 1
    rows = store.list_reminders(False)
    assert len(rows) == 1
    assert rows[0]["appointment_id"] == 0


# ---------------------------------------------------------------- 代理
def test_loopback_alias_cannot_rotate_failure_bucket(
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
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
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
        for alias in ("127.0.0.2", "127.0.0.3"):
            wrong = client.get("/api/sessions", headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": alias,
            })
            assert wrong.status_code == 401
        # 换一个回环别名不能获得新桶
        limited = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad",
            "X-Forwarded-For": "127.0.0.4",
        })
        assert limited.status_code == 429
        correct = client.get("/api/sessions", headers={
            "X-Medix-Token": token,
            "X-Forwarded-For": "127.0.0.5",
        })
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_multihop_appended_address_not_prelocked(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    single = FakeRequest({"x-forwarded-for": "203.0.113.9"})
    assert api_server._forwarded_appended_address(single) == "203.0.113.9"
    # 多元素时代理追加的最右项可能是上一跳代理（全家共享）
    multi = FakeRequest({"x-forwarded-for": "10.1.2.3, 172.18.0.1"})
    assert api_server._forwarded_appended_address(multi) == ""


def test_declared_proxy_hops_skipped_when_resolving(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.1")
    request = FakeRequest({"x-forwarded-for": "203.0.113.9, 172.18.0.1"})
    assert api_server._forwarded_client_address(request) == "203.0.113.9"
    assert api_server._client_ip_key(request) == "ip:203.0.113.9"


# ---------------------------------------------------------------- 报表/安全
def test_profile_context_medications_join_safety(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女", "medications": "华法林"})
    store.add_medication("妈妈", "布洛芬", "200mg")
    data = client.get("/api/family/overview", params={"days": 30}).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert (row.get("medication_lab_safety") or {}).get(
        "count", 0
    ) >= 1


def test_bp_peak_alert_not_suppressed_by_target(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    # 目标放宽到 200/120：读数在目标内（over=0），但按标准 160/100
    # 仍应给出单次峰值提示
    store.set_metric_target("妈妈", "bp", 200, 120)
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric(
        "妈妈", "bp", 175, 115, measured_at=f"{today}T08:00"
    )
    data = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()
    insights = {item["key"]: item for item in data["insights"]}
    assert "metric_bp_single" in insights
    assert "175/115" in insights["metric_bp_single"]["title"]


def test_daily_metric_stats_use_daily_totals(store):
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 4000, measured_at=f"{today}T08:00")
    store.add_metric("妈妈", "steps", 5000, measured_at=f"{today}T18:00")
    summary = store.metric_summary("妈妈", "steps", 7)
    assert summary["count"] == 2
    assert summary["value1"]["avg"] == 9000
    assert summary["value1"]["min"] == 9000
    assert summary["value1"]["max"] == 9000


def test_legacy_memory_scopes_disabled_by_default(monkeypatch):
    import api_server

    calls = []

    class FakeMemory:
        enabled = True

        def delete_scope(self, scope):
            calls.append(("scope", scope))

        def delete_session_summary(self, session_id, user_id=""):
            calls.append(("summary", session_id, user_id))

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: FakeMemory()
    )
    monkeypatch.delenv("MEDIX_MEM0_LEGACY_CLEANUP", raising=False)
    assert api_server._erase_long_term_entries([("妈妈", "s1")])
    plaintext = [
        call for call in calls
        if "medix_profile_妈妈" in str(call)
        or "medix_session_s1" in str(call)
    ]
    assert plaintext == []

    calls.clear()
    monkeypatch.setenv("MEDIX_MEM0_LEGACY_CLEANUP", "1")
    assert api_server._erase_long_term_entries([("妈妈", "s1")])
    plaintext = [
        call for call in calls
        if "medix_profile_妈妈" in str(call)
        or "medix_session_s1" in str(call)
    ]
    assert plaintext


# ---------------------------------------------------------------- 前端
def test_frontend_round49_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 深链导航时间只在新导航时写入，消费时清理
    assert "if (pendingDeepLinkSession !== initialSessionFromUrl) {" in app_js
    assert "safeSessionRemove(DEEP_LINK_AT_KEY);" in app_js
    # 待重置仍生效时不写回共享会话指针
    assert "safeSessionSet('medix.session.tab', id);" in app_js
