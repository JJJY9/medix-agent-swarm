"""第四十四轮审查：孕期黏膜并列、定义问句急症修饰、近期停药任意语序、
导入内容指纹、预约改档链接、超大归属标记、受信代理分桶、待重置刷新、
既往产科史过滤、提醒孪生/去重、改期重建提醒、风险窗口、每日趋势、
失败重试/预恢复、Mem0 分页截断、清理门禁、化验成员标注与 CSV 累计。"""
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
def test_pregnancy_bleeding_not_swallowed_by_gum_enumeration():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕8周，牙龈出血、阴道出血怎么办",
        "怀孕8周，牙龈出血及阴道出血",
        "孕8周，鼻出血和阴道出血",
        "怀孕8周，皮肤出血、阴道出血",
        "怀孕8周，牙龈出血、下体出血",
        "怀孕8周，牙龈出血、见红",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text


def test_definition_suffix_keeps_acs_modifiers():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "胸痛放射到左臂是什么病",
        "胸痛冒冷汗是什么病",
        "胸口压榨样疼痛是什么病",
        "呼吸困难躺不平是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 单一术语的纯定义问句仍按科普过滤
    assert detect_red_flags(
        patient_complaint_text("咯血是什么病")
    )["level"] == "none"


def test_ectopic_and_molar_bleeding_trigger_emergency():
    from core.triage import detect_red_flags

    for text in (
        "宫外孕，今天出血了",
        "葡萄胎清宫后，现在流血",
    ):
        assert detect_red_flags(text, {})["level"] == "emergency", text
    assert detect_red_flags(
        "今天出血", {"history": "宫外孕"}
    )["level"] == "emergency"


def test_past_obstetric_history_does_not_trigger():
    from core.triage import detect_red_flags

    for history in (
        "慢病：产后抑郁（管理中）",
        "慢病：妊娠期糖尿病（已控制）",
        "产后三年",
        "曾经怀孕，已流产",
        "去年孕早期出血",
    ):
        assert detect_red_flags(
            "今天阴道出血", {"history": history}
        )["level"] == "none", history


# ---------------------------------------------------------------- 用药
def test_recent_stop_any_word_order():
    from core.medication_safety import check_medication_safety

    for text in (
        "停了华法林三天了，现在牙疼能吃布洛芬吗",
        "停用华法林两天了，现在能吃布洛芬吗",
        "刚把华法林停了，现在能吃布洛芬吗",
        "华法林昨天停了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) == "warning", text

    # 两周以上按已停药处理（抗凝作用已基本消退）
    assert check_medication_safety(
        "已经停用华法林两周，能吃阿司匹林吗", {}
    ).get("level") != "warning"


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created):
    return {
        "id": raw_id, "profile": profile, "name": name, "dose": dose,
        "frequency": "每日一次", "created_at": created,
        "updated_at": created,
    }


def test_import_deleted_row_not_claimed_by_moved_other_row(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        created = "2026-06-01T08:00:00"
        backup = {"medications": [
            _med(5, "妈妈", "药A", "2g", created),
            _med(6, "爸爸", "药C", "1g", created),
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

        result = target.import_data(backup)

        rows = target.list_medications(limit=20)
        # 被删的妈妈药A必须补回，妈妈名下不能只剩被改挂的药C
        assert any(
            item["profile"] == "妈妈" and item["name"] == "药A"
            for item in rows
        ), rows
        assert result["medications"] >= 1
    finally:
        target.close()


def test_reminder_twin_drift_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        created = "2026-06-01T08:00:00"
        backup = {"reminders": [
            {
                "id": 3, "profile": "妈妈", "title": "提醒A",
                "due_at": "2026-12-01", "created_at": created,
                "updated_at": created,
            },
            {
                "id": 4, "profile": "妈妈", "title": "提醒B",
                "due_at": "2026-12-01", "created_at": created,
                "updated_at": created,
            },
        ]}
        target.import_data(backup)
        a = next(
            item for item in target.list_reminders(False, "妈妈")
            if item["title"] == "提醒A"
        )
        assert target.delete_reminder(a["id"])

        target.import_data(backup)

        titles = sorted(
            item["title"] for item in target.list_reminders(False, "妈妈")
        )
        assert titles == ["提醒A", "提醒B"]
    finally:
        target.close()


def test_import_keeps_distinct_reminders_same_appointment_day(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
    )
    assert store.add_reminder(
        "复诊：高血压", "2026-12-13", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    assert store.add_reminder(
        "带医保卡和既往检查单", "2026-12-13", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    backup = store.export_data()
    assert store.delete_reminder(
        next(
            item["id"] for item in store.list_reminders(False, "妈妈")
            if item["title"] == "带医保卡和既往检查单"
        )
    )

    store.import_data(backup)

    titles = {
        item["title"] for item in store.list_reminders(False, "妈妈")
    }
    assert titles == {"带医保卡和既往检查单", "复诊：高血压"}


# ---------------------------------------------------------------- 复诊
def test_appointment_profile_change_clears_foreign_visit_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(appointment_id, profile="爸爸")

    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_appointment_id"] == 0
    assert store.delete_visit_record(visit["id"])
    appointment = store.get_appointment(appointment_id)
    assert appointment is not None
    assert appointment["profile"] == "爸爸"


def test_schedule_followup_rejects_foreign_profile_reuse(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], profile="爸爸"
    )

    second = _schedule_followup_sync(store, visit["id"])

    assert second["appointment"]["profile"] == "妈妈"
    assert second["appointment"]["id"] != first["appointment"]["id"]


def test_huge_owner_marker_does_not_break_store(tmp_path):
    from memory.session_store import SessionStore

    path = str(tmp_path / "huge.db")
    first = SessionStore(path)
    try:
        first.save_profile("妈妈", {"sex": "女"})
        appointment = first.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note="由就诊记录 #999999999999999999999999 自动创建",
        )
        assert first.update_appointment_partial(
            appointment["id"], scheduled_at="2026-12-20T09:00"
        )
    finally:
        first.close()

    reopened = SessionStore(path)
    try:
        assert reopened.get_appointment(appointment["id"]) is not None
    finally:
        reopened.close()


def test_reschedule_rebuilds_completed_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    assert store.complete_reminder(first["reminder"]["id"])

    store.update_appointment_partial(
        first["appointment"]["id"], scheduled_at="2026-12-20T09:00"
    )

    pending = store.list_reminders(False, "妈妈")
    assert [item["due_at"] for item in pending] == ["2026-12-19"]
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_reminder_id"] == pending[0]["id"]


# ---------------------------------------------------------------- 代理
def test_trusted_proxy_buckets_by_forwarded_client(monkeypatch, tmp_path):
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
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(2, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("172.18.0.2", 50000),
    )
    try:
        for _ in range(3):
            client.get("/api/sessions", headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": "203.0.113.9",
            })
        victim = client.get("/api/sessions", headers={
            "X-Medix-Token": token,
            "X-Forwarded-For": "198.51.100.7",
        })
        # 攻击者只能锁自己的桶；另一位真实客户端的正确令牌仍可用
        assert victim.status_code == 200
        attacker = client.get("/api/sessions", headers={
            "X-Medix-Token": token,
            "X-Forwarded-For": "203.0.113.9",
        })
        assert attacker.status_code == 429
    finally:
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_risk_not_truncated_by_uncertain_answers(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    old_day = (datetime.now() - timedelta(days=20)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    store.save_exchange(
        "risk", "胸痛怎么办", "风险等级：高危，请立即就医。",
        profile="妈妈",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 'risk'",
            (old_day,),
        )
    for index in range(55):
        store.save_exchange(
            "uncertain", f"问题{index}",
            "风险等级：不确定。如有紧急情况请就医。",
            profile="妈妈",
        )

    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == "high"


def test_weekly_daily_metrics_hide_record_count_trend(
    client_and_store,
):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    today = datetime.now().date()
    for offset in range(1, 8):
        day = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "steps", "value1": 3000,
            "measured_at": f"{day}T08:00",
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "steps", "value1": 3000,
            "measured_at": f"{day}T18:00",
        })
        prev = (today - timedelta(days=offset + 7)).strftime("%Y-%m-%d")
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "steps", "value1": 6000,
            "measured_at": f"{prev}T08:00",
        })

    data = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
    ).json()
    steps = next(
        item for item in data["metrics"]["by_metric"]
        if item["metric"] == "steps"
    )
    assert steps.get("prev_avg_value1") is None


def test_family_insights_label_lab_members(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("孩子", {"sex": "男"})
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_lab_result(
        "妈妈", "血红蛋白", 90, "g/L", 120, 160,
        tested_at=f"{today}T08:00",
    )
    store.add_lab_result(
        "孩子", "空腹血糖", 9.1, "mmol/L", 3.9, 6.1,
        tested_at=f"{today}T09:00",
    )

    data = client.get("/api/health/insights", params={"days": 30}).json()
    lab = next(
        item for item in data["insights"] if item["key"] == "labs"
    )
    assert "妈妈：" in lab["detail"]
    assert "孩子：" in lab["detail"]


def test_family_csv_daily_metric_labels(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    today = datetime.now().date()
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "steps", "value1": 8000,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 3000,
        "measured_at": f"{today.isoformat()}T08:00",
    })
    store.add_reminder("逾期提醒", "2020-01-01", "", "", "妈妈")

    csv_response = client.get("/api/family/overview/export.csv")
    line = next(
        item for item in csv_response.text.splitlines()
        if item.startswith("妈妈")
    )
    assert "今日累计 3000" in line
    assert "低于目标" in line


# ---------------------------------------------------------------- 记忆安全
def test_memory_cleanup_gate_checks_session_scope(store):
    from core.session_context import session_memory_scope

    store.queue_memory_cleanup([("妈妈", "s1")])
    assert store.memory_scope_cleanup_pending(
        session_memory_scope("s1")
    ) is True


def test_mem0_truncated_iteration_is_error():
    from memory.long_term import LongTermMemory

    class FakeMem0:
        def get_all(self, user_id, page=1, page_size=100):
            return {
                "results": [
                    {
                        "id": "m1",
                        "memory": "摘要",
                        "metadata": {"session_id": "x"},
                    }
                ] * page_size
            }

        def delete(self, memory_id):
            return True

    memory = object.__new__(LongTermMemory)
    memory.mem0 = FakeMem0()
    memory.last_error = ""

    memory._delete_matching("scope", lambda metadata: True)

    assert memory.last_error


# ---------------------------------------------------------------- 前端
def test_frontend_round44_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 失败重试恢复期间切换会话：不发送旧问题
    retry_block = app_js[
        app_js.index("function makeFailureRetry"):
        app_js.index("function makeRetry")
        if "function makeRetry" in app_js
        else app_js.index("function makeFailureRetry") + 2500
    ]
    assert "const retrySession = sessionId;" in retry_block
    assert "sessionId !== retrySession" in retry_block

    # 深链预恢复期间再次提交不清空输入框
    submit_block = app_js[
        app_js.index("els.composer.addEventListener('submit'"):
        app_js.index("els.composer.addEventListener('submit'") + 500
    ]
    assert "if (sendInFlight)" in submit_block

    # 刷新时若待重置标记命中，共享会话指针保持失效
    assert "if (sessionId) safeStorageRemove('medix.session');" in app_js
