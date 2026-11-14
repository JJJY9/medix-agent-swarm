"""第五十五轮审查：摔伤头外伤/家属传闻/未来停药/昨天漏服/否定填充、
同秒孪生顺序与缺失数、非内容尾字段、删除档案标记/导出、风险答复
可解析性、闸门单源限流、时间精度、前端对账与令牌处理。"""
import asyncio
import time
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
def test_head_trauma_fall_phrases():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在吃华法林，滑倒了，后脑勺摔到地上",
        "我在吃华法林，摔到后脑勺了",
        "我在吃华法林，头摔破了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    # 单纯跌倒（无头部撞击）仍是 none
    assert detect_red_flags(
        patient_complaint_text("我在吃华法林，摔了一跤"), {}
    )["level"] == "none"


def test_hearsay_family_emergency_kept():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "听说我爸今天胸痛得厉害",
        "我听说我爸今天胸痛得厉害",
        "听说父亲咯血了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "emergency", text
    assert patient_complaint_text("我听说咯血是肺癌的症状") == ""


def test_future_stop_is_still_current_use():
    from core.medication_safety import check_medication_safety

    for text in (
        "我明天停用华法林，今天能吃布洛芬吗",
        "医生让我下个月起停用华法林，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


def test_missed_dose_yesterday_still_anticoagulated():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("华法林昨天没吃，头磕到桌角"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    assert check_medication_safety(
        "我昨天没吃华法林，牙疼能吃布洛芬吗", {}
    )["level"] == "warning"


def test_negation_filler_zai():
    from core.triage import detect_red_flags

    for text in (
        "没有再出现胸痛",
        "近期没有再次胸痛",
        "不再呼吸困难了",
        "孕12周，阴道不再流血了",
    ):
        assert detect_red_flags(text, {})["level"] == "none", text
    assert detect_red_flags(
        "宝宝没再发烧，精神很好", {"age": "2个月"}
    )["level"] == "none"


def test_head_terms_do_not_match_toes_or_fingers():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在吃华法林，脚趾头受伤了",
        "我在吃华法林，手指头外伤了",
        "我在吃华法林，手指头砸伤了",
    ):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


# ---------------------------------------------------------------- 导入
def _appt(raw_id, status, note):
    return {
        "id": raw_id, "profile": "妈妈", "title": "复诊",
        "department": "", "doctor": "", "location": "",
        "scheduled_at": "2026-12-14T09:00", "status": status,
        "note": note, "created_at": CREATED, "updated_at": CREATED,
    }


def test_twin_order_preserves_deleted_later_row(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_appointment("妈妈", "占位", scheduled_at="2026-12-30T09:00")
    backup = {"appointments": [
        _appt(5, "upcoming", "待就诊备注"),
        _appt(6, "completed", "已完成备注"),
    ]}
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈")
        if item["title"] == "复诊"
    ]
    completed = next(
        item for item in rows if item["status"] == "completed"
    )
    store.delete_appointment(completed["id"])
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈")
        if item["title"] == "复诊"
    ]
    assert len(rows) == 2
    assert sorted(item["status"] for item in rows) == [
        "completed", "upcoming"
    ]
    assert {item["note"] for item in rows} == {
        "待就诊备注", "已完成备注"
    }


def test_rename_in_identity_group_stays_idempotent(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication("妈妈", "占位", "9g")
    backup = {"medications": [
        {"id": raw_id, "profile": "妈妈", "name": name, "dose": "1g",
         "frequency": "每日一次", "note": "", "active": True,
         "created_at": CREATED, "updated_at": CREATED}
        for raw_id, name in ((1, "药A"), (2, "药B"))
    ]}
    store.import_data(backup)
    row_b = next(
        item for item in store.list_medications(limit=10)
        if item["name"] == "药B"
    )
    store.update_medication(row_b["id"], name="药C")
    result = store.import_data(backup)
    rows = store.list_medications(limit=10)
    assert result["medications"] == 0
    assert not [item for item in rows if item["name"] == "药B"]
    assert {"药A", "药C"} <= {item["name"] for item in rows}


def test_three_identical_delete_one_restores_one(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication("妈妈", "占位", "9g")
    backup = {"medications": [
        {"id": raw_id, "profile": "妈妈", "name": "阿司匹林",
         "dose": "100mg", "frequency": "每日一次", "note": "",
         "active": True, "created_at": CREATED, "updated_at": CREATED}
        for raw_id in (5, 6, 7)
    ]}
    store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    store.delete_medication(rows[0]["id"])
    result = store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    assert result["medications"] == 1
    assert len(rows) == 3


def test_deleted_profile_tag_survives_export_and_edit(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("r55-tags", "问题", "回答", profile="妈妈")
    store.set_tags("r55-tags", ["a", "b", "c", "d", "e"])
    store.delete_profile("妈妈")
    session = store.list_sessions(10)[0]
    assert "__deleted_profile__" in session["tags"]
    # 用户重设标签不能清掉内部标记
    store.set_tags("r55-tags", ["x"])
    session = store.list_sessions(10)[0]
    assert "__deleted_profile__" in session["tags"]
    backup = store.export_data()
    exported = next(
        item for item in backup["sessions"] if item["id"] == "r55-tags"
    )
    assert "__deleted_profile__" in exported["tags"]
    other = __import__(
        "memory.session_store", fromlist=["SessionStore"]
    ).SessionStore(":memory:")
    try:
        other.import_data(backup)
        restored = next(
            item for item in other.list_sessions(10)
            if item["id"] == "r55-tags"
        )
        assert "__deleted_profile__" in restored["tags"]
    finally:
        other.close()


def test_rebinding_clears_deleted_profile_tag(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("r55-rebind", "问题", "回答", profile="妈妈")
    store.delete_profile("妈妈")
    store.save_exchange("r55-rebind", "新问题", "新回答", profile="爸爸")
    session = next(
        item for item in store.list_sessions(10)
        if item["id"] == "r55-rebind"
    )
    assert session["profile"] == "爸爸"
    assert "__deleted_profile__" not in session["tags"]


# ---------------------------------------------------------------- 复诊
def test_cancelled_backup_owner_not_restored_on_new_visit(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-20T09:00"
    )
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-10"
    )
    backup = store.export_data()
    store.delete_visit_record(visit["id"])
    store.update_appointment_partial(appointment_id, status="upcoming")
    store.import_data(backup)
    appointment = store.get_appointment(appointment_id)
    assert int(appointment.get("owner_visit_id") or 0) == 0
    assert "由就诊记录" not in str(appointment.get("note") or "")
    restored_visit = store.list_visit_records(None, 0, "", 10)[0]
    store.update_appointment_partial(
        appointment_id, scheduled_at="2027-02-01T09:00"
    )
    refreshed = store.get_visit_record(restored_visit["id"])
    assert refreshed["next_followup_date"][:10] == "2027-01-10"


def test_empty_visit_date_restore_then_schedule_reuses(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_visit_record_partial(visit["id"], next_followup_date="")
    # 恢复时就诊日期为空：保持独立预约（不保留自动归属）
    store.update_appointment_partial(appointment_id, status="upcoming")
    row = store.get_visit_record(visit["id"])
    assert int(row.get("followup_appointment_id") or 0) == 0
    appointment = store.get_appointment(appointment_id)
    assert int(appointment.get("owner_visit_id") or 0) == 0
    # 补回同一天的复诊日期再安排：应接管这条无主预约，不生成重复
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-14"
    )
    _schedule_followup_sync(store, visit["id"])
    rows = [
        item for item in store.list_appointments("妈妈", "upcoming")
        if item["title"] == "复诊：高血压"
        and str(item["scheduled_at"] or "")[:10] == "2026-12-14"
    ]
    assert len(rows) == 1
    reminders = [
        item for item in store.list_reminders(False, "妈妈")
        if item["title"] == "复诊：高血压"
    ]
    assert len(reminders) == 1


def test_minute_precision_resave_does_not_rearm_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-15",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    reminder_id = first["reminder"]["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET scheduled_at = '2026-12-15T09:00:00'"
            " WHERE id = ?",
            (appointment_id,),
        )
    store.complete_reminder(reminder_id)
    store.update_appointment_partial(
        appointment_id, title="复诊：改名",
        scheduled_at="2026-12-15T09:00",
    )
    pending = [
        item for item in store.list_reminders(True)
        if item.get("appointment_id") == appointment_id
        and not item.get("done")
    ]
    assert pending == []


# ---------------------------------------------------------------- 代理
def test_gate_per_source_slot_cap():
    import api_server

    api_server._auth_compare_gates.clear()

    async def run():
        key = "ip:r55-source"
        cap = api_server._AUTH_COMPARE_GATE_MAX_PER_SOURCE
        assert cap >= 16
        tasks = [
            asyncio.create_task(
                api_server._auth_compare_enter(key, source="A")
            )
            for _ in range(cap)
        ]
        await asyncio.sleep(0.05)
        overflow = await api_server._auth_compare_enter(key, source="A")
        assert overflow is None
        b_task = asyncio.create_task(
            api_server._auth_compare_enter(key, source="B")
        )
        await asyncio.sleep(0.05)
        gate = api_server._auth_compare_gates[
            (id(asyncio.get_running_loop()), key)
        ]
        assert gate["sources"].get("B") == 1
        for task in tasks + [b_task]:
            task.cancel()
        await asyncio.gather(*tasks, b_task, return_exceptions=True)

    asyncio.run(run())


# ---------------------------------------------------------------- 报表
def test_deleted_profile_no_empty_unassigned_card(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "r55-del", "头晕怎么办", "风险等级：高危。建议尽快就医。",
        profile="妈妈",
    )
    assert client.delete("/api/profiles/妈妈").status_code == 200
    assert store.has_unassigned_records(7) is False
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    for item in data["profiles"]:
        if item["name"] == "":
            assert int(item["consultations"]["count"] or 0) == 0
            assert not item["consultations"].get("risk")
    dossier = client.get(
        "/api/health/dossier-family.md", params={"days": 30}
    )
    if dossier.status_code == 200:
        text = dossier.text
        assert "头晕怎么办" not in text


def test_risk_answer_skips_uncertain_template(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "r55-risk", "头晕怎么办", "风险等级：高危。建议尽快就医。",
        profile="妈妈",
    )
    store.save_exchange(
        "r55-risk", "还要注意什么",
        "风险等级：不确定。如有紧急情况请就医。",
        profile="妈妈",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    calendar = client.get(
        "/api/health/calendar", params={"profile": "妈妈"}
    ).json()
    assert calendar["days"][today]["alerts"] >= 1
    timeline = client.get(
        "/api/timeline",
        params={"profile": "妈妈", "days": 7, "only_risk": 1},
    ).json()
    assert timeline["total"] >= 1


# ---------------------------------------------------------------- 前端
def test_frontend_round55_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # baseline=0 不做「同文本旧问答已落库」判定
    assert "if (!baselineMessageId) {" in app_js
    # 深链预恢复窗口内拒绝切换会话
    open_body = app_js[
        app_js.index("async function openSession"):
        app_js.index("async function openSession") + 600
    ]
    assert "if (sendInFlight) {" in open_body
    # 接管放回的文本不被 settleDraftForSession 清掉
    settle = app_js[
        app_js.index("function settleDraftForSession"):
        app_js.index("function settleDraftForSession") + 1200
    ]
    assert "detachedComposerText && els.input.value === detachedComposerText" in settle
    # 令牌只在 401 时清除
    assert "verify.status === 401" in app_js
    assert "verify.status !== 401" in app_js
