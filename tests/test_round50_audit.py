"""第五十轮审查：孕N天/卫生巾出血、双重否定用药、请解释前缀、跨档案
漂移兄弟行排除、单跳预锁定声明、共享主语、英文词表、同源自认领、锚点
组存活、已完成复诊链接、提醒改档就诊链接、IPv6 分桶、陈旧危急值、
家庭总览打卡、疫苗改档同步、家庭补充用药化验、购药误判、重建提醒复活、
共享桶记账、空状态打卡、深链在途主诉、共享指针恢复。"""
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
    monkeypatch.delenv("MEDIX_PROXY_SINGLE_HOP", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


# ---------------------------------------------------------------- 分诊
def test_pregnancy_days_and_pad_bleeding():
    from core.triage import detect_red_flags, patient_complaint_text

    assert "孕产期出血" in detect_red_flags(
        patient_complaint_text("孕40天，阴道出血"), {}
    )["matches"]
    assert "孕产期出血" in detect_red_flags(
        patient_complaint_text("怀孕8周，卫生巾上有血"), {}
    )["matches"]
    result = detect_red_flags(
        "阴道出血", {"history": "孕40天"}
    )
    assert "孕产期出血" in result["matches"]


def test_definition_prefix_keeps_patient_context():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "请解释一下我为什么会胸痛",
        "请解释一下我小孩抽搐是什么病",
    ):
        kept = patient_complaint_text(text)
        assert detect_red_flags(kept)["level"] == "emergency", text
    for text in ("介绍一下呼吸困难", "介绍一下什么是咯血", "什么是咯血"):
        assert patient_complaint_text(text) == "", text


def test_shared_subject_clauses_and_purchase_not_attributed():
    from core.triage import detect_red_flags, patient_complaint_text

    assert "抗凝治疗期间头部外伤" in detect_red_flags(
        patient_complaint_text("我和我爸都吃华法林，今天头撞到门框"), {}
    )["matches"]
    assert "免疫功能低下伴发热" in detect_red_flags(
        patient_complaint_text("我和我爸都在化疗，今天发烧38.5度"), {}
    )["matches"]
    assert detect_red_flags(
        patient_complaint_text("我给我妈买了华法林，今天头撞到门框"), {}
    )["level"] == "none"


def test_english_drug_terms_trigger_context_flags():
    from core.triage import detect_red_flags

    assert "抗凝治疗期间头部外伤" in detect_red_flags(
        "头撞到门框", {"medications": "warfarin"}
    )["matches"]
    assert "免疫功能低下伴发热" in detect_red_flags(
        "今天发烧38.5度", {"history": "chemotherapy"}
    )["matches"]


# ---------------------------------------------------------------- 用药
def test_chinese_double_negation_keeps_medication():
    from core.medication_safety import check_medication_safety

    for text in (
        "我不得不吃华法林，能吃布洛芬吗",
        "我不能不吃华法林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    assert check_medication_safety(
        "我不吃华法林，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- 导入
def test_moved_drift_not_stolen_by_other_members_sibling(store):
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
    dad = [
        item for item in store.list_reminders(True)
        if item["title"] == "提醒B"
    ][0]
    store.delete_reminder(mom["id"])
    time.sleep(1.05)
    # 爸爸的提醒只做了普通编辑（改备注），不是改挂
    store.update_reminder(dad["id"], note="已确认")
    result = store.import_data(backup)
    assert result.get("reminders") == 1
    rows = store.list_reminders(True)
    assert len([
        item for item in rows if item["title"] == "提醒B"
    ]) == 1
    assert any(item["title"] == "提醒A" for item in rows)


def test_same_origin_edited_survivor_self_claimed(store):
    store.save_profile("妈妈", {"sex": "女"})
    first = store.add_reminder("A", "2026-12-01", profile="妈妈")
    second = store.add_reminder("B", "2026-12-02", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    store.delete_reminder(first["id"])
    time.sleep(1.05)
    store.update_reminder(second["id"], due_at="2026-12-12")
    result = store.import_data(backup)
    assert result.get("reminders") == 1
    rows = store.list_reminders(True)
    titles = sorted(item["title"] for item in rows)
    assert titles == ["A", "B"]
    b_row = [item for item in rows if item["title"] == "B"][0]
    assert b_row["due_at"] == "2026-12-12"


def test_anchor_group_all_alive_edited_not_rebuilt(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric(
        "妈妈", "bp", 120, 80, measured_at="2026-06-01T08:00"
    )
    store.add_metric(
        "妈妈", "bp", 130, 85, measured_at="2026-06-01T08:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    time.sleep(1.05)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET value1 = 140, value2 = 90,"
            " updated_at = ?",
            (datetime.now().isoformat(timespec="seconds"),),
        )
    result = store.import_data(backup)
    assert result.get("metrics") == 0
    rows = store.list_metrics("妈妈", "bp", 1000, 365)
    assert sorted(item["value1"] for item in rows) == [140, 140]


def test_completed_followup_link_restored_without_same_date(store):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    # 实际复诊日期与计划日期不同，预约已完成
    store.update_appointment_partial(
        appointment_id, status="completed",
        scheduled_at="2026-12-20T09:00",
    )
    backup = store.export_data()
    target = SessionStore(str(store.db_path) + ".dst")
    try:
        target.import_data(backup)
        restored = target.list_visit_records(None, 0, "", 10)[0]
        assert restored["followup_appointment_id"] != 0
        assert restored.get("followup_scheduled") is True
    finally:
        target.close()


def test_reminder_moved_after_reopen_detaches_visit_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    reminder_id = first["reminder"]["id"]
    # 预约完成后提醒被摘除链接（appointment_id=0），就诊链接仍在
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET appointment_id = 0 WHERE id = ?",
            (reminder_id,),
        )
    moved = store.update_reminder(reminder_id, profile="爸爸")
    assert moved["profile"] == "爸爸"
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_reminder_id"] == 0
    # 编辑就诊复诊日期不能删掉另一位成员的待办
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-25"
    )
    assert store.get_reminder(reminder_id) is not None


def test_vaccination_move_syncs_reminder_and_delete_cleans(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    vaccination = store.add_vaccination(
        "妈妈", "流感疫苗", 1, "2026-01-01", "2026-12-01"
    )
    reminder, created = store.ensure_reminder(
        "接种：流感疫苗（第1剂）", "2026-12-01", "疫苗接种提醒",
        "", "妈妈", 7, 0, vaccination["id"], 0, False,
    )
    assert created
    store.update_vaccination(
        vaccination["id"], "爸爸", "流感疫苗", 1,
        "2026-01-01", "2026-12-01", "",
    )
    mom_pending = store.list_reminders(False, "妈妈")
    dad_pending = store.list_reminders(False, "爸爸")
    assert mom_pending == [] or all(
        item["vaccination_id"] != vaccination["id"]
        for item in mom_pending
    )
    assert any(
        item["vaccination_id"] == vaccination["id"]
        for item in dad_pending
    )
    # 用户把提醒档案手动改回旧档案也不能留下孤儿
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET profile = '妈妈' WHERE id = ?",
            (reminder["id"],),
        )
    assert store.delete_vaccination(vaccination["id"]) is True
    assert store.get_reminder(reminder["id"]) is None


def test_rebuilt_reminder_deleted_stays_deleted(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-15T09:00"
    )
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-20T09:00"
    )
    reminder, created = store.ensure_reminder(
        "复诊：复诊", "2026-12-19", "", "", "妈妈",
        0, 0, 0, appointment["id"], False,
    )
    assert created
    store.complete_reminder(reminder["id"])
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-25T09:00"
    )
    rebuilt = store.list_reminders(False)
    assert len(rebuilt) == 1
    store.delete_reminder(rebuilt[0]["id"])
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-30T09:00"
    )
    assert store.list_reminders(False) == []


# ---------------------------------------------------------------- 代理
def test_single_hop_env_restores_prelock_for_multi_head(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    request = FakeRequest({
        "x-forwarded-for": "10.1.2.3, 172.18.0.1",
    })
    assert api_server._forwarded_appended_address(request) == ""
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    assert api_server._forwarded_appended_address(request) == "172.18.0.1"


def test_ipv6_bucket_groups_by_64():
    import api_server

    assert api_server._bucket_address(
        "2001:db8:1:2::1"
    ) == "2001:db8:1:2::/64"
    assert api_server._bucket_address("203.0.113.9") == "203.0.113.9"


def test_shared_bucket_counts_after_per_client_limiter():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    failure = api_source.index("if not is_authorized(request):")
    segment = api_source[failure:failure + 4000]
    assert segment.index("limiter.check(limiter_key)") < segment.index(
        "auth_failure_limiter_loopback.check("
    )


# ---------------------------------------------------------------- 报表
def test_weekly_critical_skips_stale_metric(client_and_store):
    import asyncio

    import api_server

    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    old_day = (datetime.now() - timedelta(days=300)).strftime(
        "%Y-%m-%d"
    )
    store.add_metric(
        "妈妈", "bp", 190, 120, measured_at=f"{old_day}T08:00"
    )
    report = asyncio.run(
        api_server._weekly_report_impl("妈妈", 200, 400, False)
    )
    assert report["critical_alerts"]["count"] == 0


def test_family_overview_counts_inactive_medication_logs(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    medication = store.add_medication("妈妈", "二甲双胍", "500mg")
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_medication_log(medication["id"], taken_at=f"{today}T08:00")
    store.update_medication(medication["id"], active=False)
    data = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert row["medications"]["taken_today"] >= 1
    assert row["medications"]["last_taken_at"]


def test_family_context_meds_enable_condition_safety(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女", "medications": "布洛芬"})
    store.add_condition("妈妈", "胃溃疡")
    data = client.get("/api/health/insights", params={"days": 30}).json()
    med_insights = [
        item for item in data["insights"]
        if item.get("key") == "medication_safety"
    ]
    assert med_insights
    assert "溃疡" in med_insights[0]["detail"]
    assert "布洛芬" in med_insights[0]["detail"]


def test_health_insights_empty_state_counts_medication_logs(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    medication = store.add_medication("妈妈", "二甲双胍", "500mg")
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_medication_log(medication["id"], taken_at=f"{today}T08:00")
    store.update_medication(medication["id"], active=False)
    data = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()
    keys = [item["key"] for item in data["insights"]]
    assert "no_data" not in keys


# ---------------------------------------------------------------- 前端/汇总
def test_frontend_round50_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 在途主诉先合并草稿再清理；提交入口极早持久化；离开提示覆盖预恢复
    restore = app_js[
        app_js.index("function restoreInflightQuestion()"):
        app_js.index("window.addEventListener('beforeunload'")
    ]
    assert restore.index("restoreComposer(question);") < restore.index(
        "safeSessionRemove(INFLIGHT_QUESTION_KEY);"
    )
    assert "if (!busy && !sendInFlight) return;" in app_js
    assert "极早持久化已提交主诉" in app_js
    # 撤销待重置恢复共享指针
    assert "persistSessionId(sessionId);" in app_js[
        app_js.index("setPendingSessionReset(hadPendingReset);"):
        app_js.index("setPendingSessionReset(hadPendingReset);") + 300
    ]
    coordinator = (ROOT / "swarm" / "swarm_coordinator.py").read_text(
        encoding="utf-8"
    )
    assert "canonical_profile" in coordinator
