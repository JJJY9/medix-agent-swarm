"""第三十四轮审查：triage 回归、别名链、家庭统计、预约联动、代理与导入。"""
import asyncio
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


# ---------------------------------------------------------------- triage 回归
def test_patient_complaint_keeps_real_complaints():
    from core.triage import detect_red_flags, patient_complaint_text

    fever = patient_complaint_text("孩子发烧了，医生说是一种病毒感染")
    assert fever
    assert detect_red_flags(fever, {"age": "2个月"})["level"] == "emergency"

    chest = patient_complaint_text(
        "我爸胸口痛，医生说是心绞痛，典型症状是活动后胸痛"
    )
    assert "胸口痛" in chest
    assert detect_red_flags(chest)["level"] == "emergency"


def test_patient_complaint_keeps_symptoms_before_cause_question():
    from core.triage import detect_red_flags, patient_complaint_text

    breath = patient_complaint_text("胸痛、呼吸困难是什么原因")
    assert "胸痛" in breath and "呼吸困难" in breath
    assert detect_red_flags(breath)["level"] == "emergency"

    asthma = patient_complaint_text("没有胸痛，喘不上气是什么原因")
    assert "喘不上气" in asthma

    trauma = patient_complaint_text("正在吃华法林，摔到头是什么原因")
    assert "摔到头" in trauma
    assert detect_red_flags(
        trauma, {"medications": "华法林"}
    )["level"] == "emergency"


def test_patient_complaint_family_markers():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "老婆怀孕出血是什么原因",
        "3岁小孩高热不退是什么原因",
        "患者过敏性休克是什么原因",
        "老公胸口压榨样疼痛怎么回事",
    ):
        kept = patient_complaint_text(text)
        assert kept, text
        assert detect_red_flags(kept)["level"] == "emergency", text

    # 纯定义问句仍然不当作主诉
    assert patient_complaint_text("什么是咯血？") == ""
    assert patient_complaint_text("咯血是什么？") == ""


def test_validator_still_flags_real_complaint_with_definition_words():
    from constraints.validator import ConstraintValidator

    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "可能与肌肉拉伤有关，建议注意休息并观察。",
        user_input="我爸胸口痛，医生说是心绞痛，典型症状是活动后胸痛",
    )
    assert result["valid"] is False


# ---------------------------------------------------------------- 别名链
def test_alias_resolution_follows_old_chain_past_reused_name(store):
    import time

    store.save_profile("妈妈", {"sex": "女"})
    store.add_allergy("妈妈", "青霉素", kind="drug")
    assert store.rename_profile("妈妈", "母亲")
    # 时间戳精确到秒：确保「旧链改名」早于新患者创建
    time.sleep(1.05)
    assert store.rename_profile("母亲", "老妈")
    time.sleep(1.05)

    # 另一位患者复用了中间名「母亲」
    store.save_profile("母亲", {"sex": "女"})
    store.add_allergy("母亲", "阿司匹林", kind="drug")

    assert store.resolve_profile_alias("妈妈") == "老妈"

    allergies = {
        item["substance"] for item in store.list_allergies("母亲")
    }
    assert "青霉素" not in allergies
    assert "阿司匹林" in allergies


def test_alias_resolution_stops_at_reused_input_name(store):
    store.save_profile("妈妈", {"sex": "女"})
    assert store.rename_profile("妈妈", "母亲")
    store.save_profile("妈妈", {"sex": "女"})

    assert store.resolve_profile_alias("妈妈") == "妈妈"


# ---------------------------------------------------------------- 导入漂移 + 改挂
def test_import_drifted_moved_record_not_duplicated(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        source.save_profile("甲", {"sex": "男"})
        source.save_profile("乙", {"sex": "男"})
        first = source.add_medication("甲", "药A", dose="1g")
        source.add_medication("甲", "药B", dose="2g")
        same_second = "2026-01-01T08:00:00"
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = ? WHERE profile = ?",
                (same_second, "甲"),
            )
        backup = source.export_data()

        target.save_profile("甲", {"sex": "男"})
        target.save_profile("乙", {"sex": "男"})
        # 目标库已有另一条本地记录占用主键 1：导入后药A 主键漂移
        target.add_medication("甲", "本地药", dose="3g")
        target.import_data(backup)
        moved_row = next(
            item for item in target.list_medications(limit=100)
            if item["name"] == "药A"
        )
        assert target.update_medication(
            moved_row["id"], profile="乙"
        )

        before = len(target.list_medications(limit=100))
        target.import_data(backup)
        after = target.list_medications(limit=100)

        # 恢复旧备份不能把已改挂的行在原档案名下再插入一份
        assert len(after) == before
        assert sum(item["name"] == "药A" for item in after) == 1
    finally:
        source.close()
        target.close()


# ---------------------------------------------------------------- 家庭统计
def test_family_overview_includes_window_critical_metric(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "bp", 185, 120, measured_at=f"{yesterday}T08:00")
    store.add_metric("妈妈", "bp", 120, 80, measured_at=f"{today}T08:00")

    data = client.get("/api/family/overview", params={"days": 7}).json()

    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert row["critical_alerts"]["count"] >= 1
    assert row["attention"]["level"] == "danger"


def test_family_insights_include_consultation_risk(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    store.save_exchange("fam-risk-1", "头晕怎么办", "综合风险：高危")
    store.set_session_profile("fam-risk-1", "妈妈")
    store.save_exchange("fam-risk-2", "胸闷怎么办", "综合风险：高危")
    store.set_session_profile("fam-risk-2", "爸爸")

    insights = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]

    keys = {item["key"] for item in insights}
    assert "consultation_risk" in keys
    assert "no_data" not in keys


def test_family_insights_only_consultations_are_not_no_data(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("fam-low", "头晕怎么办", "综合风险：低危")
    store.set_session_profile("fam-low", "妈妈")

    insights = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]

    assert "no_data" not in {item["key"] for item in insights}


# ---------------------------------------------------------------- 预约提醒
def test_completed_followup_appointment_survives_visit_changes(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-11-20",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", department="心内科", doctor="王医生",
        scheduled_at="2026-11-20T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder, _ = store.ensure_reminder(
        "复诊：高血压", "2026-11-19",
        "复诊 2026-11-20 心内科 王医生", "", "妈妈",
        0, 0, 0, appointment["id"],
    )
    assert store.complete_reminder(reminder["id"])
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET status = 'completed' WHERE id = ?",
            (appointment["id"],),
        )
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?"
            " WHERE id = ?",
            (appointment["id"], visit["id"]),
        )
    follow_visit = store.add_visit_record(
        "妈妈", appointment_id=appointment["id"],
        visit_date="2026-11-13", diagnosis="复诊",
    )

    assert store.delete_visit_record(visit["id"])

    # 已完成的复诊预约是就诊历史，不能随上一次就诊删除
    kept = store.get_appointment(appointment["id"])
    assert kept is not None and kept["status"] == "completed"
    assert store.get_reminder(reminder["id"]) is not None
    followed = store.get_visit_record(follow_visit["id"])
    assert followed["appointment_id"] == appointment["id"]


def test_restore_cancelled_appointment_recreates_reminder(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "牙科复诊", scheduled_at="2026-12-10T09:00",
    )
    reminder, _ = store.ensure_reminder(
        "就诊：牙科复诊", "2026-12-09",
        "2026-12-10 09:00", "", "妈妈", 0, 0, 0, appointment["id"],
    )
    store.update_appointment(
        appointment["id"], "妈妈", "牙科复诊",
        scheduled_at="2026-12-10T09:00", status="cancelled",
    )
    assert store.get_reminder(reminder["id"]) is None

    store.update_appointment(
        appointment["id"], "妈妈", "牙科复诊",
        scheduled_at="2026-12-10T09:00", status="upcoming",
    )

    pending = [
        item for item in store.list_reminders(False)
        if item.get("appointment_id") == appointment["id"]
    ]
    assert pending, "恢复待就诊后应重建提醒"


def test_followup_reminder_text_syncs_with_appointment(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", department="心内科", doctor="王医生",
        scheduled_at="2026-11-20T09:00",
    )
    reminder, _ = store.ensure_reminder(
        "复诊：高血压", "2026-11-19",
        "复诊 2026-11-20 心内科 王医生", "", "妈妈",
        0, 0, 0, appointment["id"],
    )

    store.update_appointment(
        appointment["id"], "妈妈", "复诊：心内科随访",
        department="心内科", doctor="王医生",
        scheduled_at="2026-11-28T09:00", status="upcoming",
    )

    updated = store.get_reminder(reminder["id"])
    assert updated["title"] == "复诊：心内科随访"
    assert "2026-11-28" in updated["note"]
    assert "2026-11-20" not in updated["note"]


# ---------------------------------------------------------------- 代理与同源
def test_trusted_proxy_headers_use_forwarded_address(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    assert api_server._is_loopback_client(
        FakeRequest({"x-forwarded-for": "203.0.113.9"})
    ) is False
    assert api_server._is_loopback_client(
        FakeRequest({"x-forwarded-for": "127.0.0.1"})
    ) is True
    # 限流键使用代理头里的真实客户端地址，避免全家共享一个计数桶
    assert api_server._client_ip_key(
        FakeRequest({"x-forwarded-for": "203.0.113.9"})
    ) == "ip:203.0.113.9"
    # 部署声明代理写 X-Real-IP 时同样按其真实地址分桶
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-real-ip")
    assert api_server._client_ip_key(
        FakeRequest({"x-real-ip": "198.51.100.7"})
    ) == "ip:198.51.100.7"


def test_cross_origin_port_is_rejected(client_and_store):
    client, _store = client_and_store

    blocked = client.post(
        "/api/sessions", json={},
        headers={"Origin": "http://testserver:9999"},
    )
    assert blocked.status_code == 403
    assert blocked.json()["code"] == "csrf_rejected"

    allowed = client.post(
        "/api/sessions", json={},
        headers={"Origin": "https://testserver"},
    )
    assert allowed.status_code != 403

# ---------------------------------------------------------------- 清理目标与索引
def test_cleanup_target_active_profile_uses_window(store):
    import api_server

    store.save_profile("A", {"sex": "女"})
    # 改名后改回原名：created_at 早于删除时间，但档案仍活跃，
    # 必须按时间窗清理而不是整段清空命名空间
    assert api_server._cleanup_target_recreated(
        store, "A", "", "9999-01-01T00:00:00"
    ) is True
    assert api_server._cleanup_target_recreated(
        store, "B", "", "9999-01-01T00:00:00"
    ) is False


def test_import_identity_indexes_exist(store):
    rows = store._conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'index'"
    ).fetchall()
    names = {row["name"] for row in rows}
    assert "idx_reminders_identity" in names
    assert "idx_lab_results_identity" in names

