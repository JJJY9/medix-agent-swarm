"""第五十三轮审查：定义问句病程语境、头外伤语序、产后N年、科普前缀、
跨档案内容同化认领、同秒孪生非内容字段、锚点规范化、复诊链接回填/
脱钩/完成提醒、比较闸门无竞争等待、Host 说明、家庭总览日累计/未来
就诊、用药提示来源计数、审计姓名残留、在途主诉与深链草稿。"""
import asyncio
import copy
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


# ---------------------------------------------------------------- 分诊
def test_course_only_clause_keeps_definition_question():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "最近，胸痛是什么病",
        "两天了，咯血是什么病",
        "最近，呼吸困难是什么病",
        "好几天了，胸痛是啥病",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "emergency", text
    # 科普铺垫仍不建立患者语境
    for text in (
        "今天想问问，抽搐是什么病",
        "昨天看了科普，抽搐是什么病",
        "最近在查资料，咯血是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_head_trauma_inserted_object_and_pack_phrases():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在吃华法林，今天头被门撞了",
        "我在吃华法林，额头磕了个包",
        "我在吃华法林，头顶撞了个包",
        "我在吃华法林，今天摔倒了，磕到了脑袋",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_postpartum_years_not_current_pregnancy():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in ("产后2年，阴道出血", "产后5年，出血"):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text
    assert "孕产期出血" in detect_red_flags(
        patient_complaint_text("怀孕20周，阴道出血"), {}
    )["matches"]


def test_educational_prefix_with_wo_xiang_liaojie():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我想了解一下什么是咯血",
        "我想了解什么是咯血",
        "我想知道什么是咯血",
    ):
        assert patient_complaint_text(text) == ""
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


def test_medical_order_stop_not_swallow_budebu():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "我不得不停用华法林了，现在能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "不得停用华法林，能吃布洛芬吗", {}
    )["level"] == "warning"


# ---------------------------------------------------------------- 导入
CREATED = "2026-06-01T08:00:00"


def _med(raw_id, profile, name, dose):
    return {
        "id": raw_id, "profile": profile, "name": name, "dose": dose,
        "frequency": "每日一次", "note": "", "active": True,
        "created_at": CREATED, "updated_at": CREATED,
    }


def test_cross_profile_content_assimilated_not_claimed(store):
    # 同秒孪生无法区分「改挂」与「本地改名同化」：按归属优先处理，
    # 被删记录必须按原内容补回；另一条记录可能因此保留一条旧版本
    # 副本（宁重复勿串档），重复导入保持幂等。
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    store.add_medication("爸爸", "本地药", "1mg")
    backup = {"medications": [
        _med(1, "妈妈", "阿司匹林", "100mg"),
        _med(2, "爸爸", "布洛芬", "200mg"),
    ]}
    store.import_data(backup)
    rows = store.list_medications(limit=100)
    mom = next(r for r in rows if r["profile"] == "妈妈")
    dad_ib = next(
        r for r in rows
        if r["profile"] == "爸爸" and r["name"] == "布洛芬"
    )
    store.delete_medication(mom["id"])
    store.update_medication(dad_ib["id"], name="阿司匹林", dose="100mg")
    assert store.import_data(backup)["medications"] == 2
    rows = store.list_medications(limit=100)
    # 被删的妈妈阿司匹林按原内容补回
    assert len([
        r for r in rows
        if r["profile"] == "妈妈" and r["name"] == "阿司匹林"
    ]) == 1
    # 爸爸改名后的阿司匹林保留
    assert len([
        r for r in rows
        if r["profile"] == "爸爸" and r["name"] == "阿司匹林"
    ]) == 1
    # 爸爸的布洛芬同样按原内容补回（数据完整优先）
    assert len([
        r for r in rows
        if r["profile"] == "爸爸" and r["name"] == "布洛芬"
    ]) == 1
    # 重复导入保持幂等
    assert store.import_data(backup)["medications"] == 0


def test_same_content_twin_keeps_non_content_fields(store):
    store.save_profile("妈妈", {})
    for index in range(3):
        store.add_allergy("妈妈", f"占位{index}", reaction="x")
    backup = {"allergies": [
        {"id": 1, "profile": "妈妈", "substance": "青霉素",
         "reaction": "过敏性休克", "severity": "severe", "kind": "drug",
         "active": True, "noted_at": "2025-01-01", "note": "",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "substance": "青霉素",
         "reaction": "皮疹", "severity": "mild", "kind": "drug",
         "active": True, "noted_at": "2025-01-01", "note": "",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_allergies("妈妈")
    severe = next(r for r in rows if r.get("severity") == "severe")
    store.delete_allergy(severe["id"])
    store.import_data(backup)
    pen = [
        r for r in store.list_allergies("妈妈")
        if r["substance"] == "青霉素"
    ]
    assert len([
        r for r in pen
        if r["severity"] == "severe" and r["reaction"] == "过敏性休克"
    ]) == 1
    assert len([
        r for r in pen
        if r["severity"] == "mild" and r["reaction"] == "皮疹"
    ]) == 1


def test_anchor_group_uses_canonical_time(store):
    store.save_profile("妈妈", {})
    store.add_metric("妈妈", "weight", 50, measured_at="2026-10-01T08:00")
    backup = {"health_metrics": [
        {"id": 1, "profile": "妈妈", "metric": "weight", "value1": 60,
         "value2": None, "measured_at": "2026-11-01T08:00:00",
         "note": "", "context": "", "created_at": CREATED,
         "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "metric": "weight", "value1": 61,
         "value2": None, "measured_at": "2026-11-01T08:00:00",
         "note": "", "context": "", "created_at": CREATED,
         "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_metrics("妈妈", limit=100)
    v60 = next(r for r in rows if float(r["value1"]) == 60)
    store.delete_metric(v60["id"])
    rows = store.list_metrics("妈妈", limit=100)
    v61 = next(r for r in rows if float(r["value1"]) == 61)
    store.update_metric(v61["id"], value1=161)
    store.import_data(backup)
    values = sorted(
        float(r["value1"]) for r in store.list_metrics("妈妈", limit=100)
        if str(r.get("measured_at") or "").startswith("2026-11-01")
    )
    assert values == [60.0, 61.0, 161.0]


def test_reminder_invalid_advance_days_not_in_group(store):
    store.save_profile("妈妈", {})
    store.add_reminder("复查", "2026-12-01", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = ?, updated_at = ?",
            (CREATED, CREATED),
        )
    backup = store.export_data()
    store.add_reminder("占位", "2026-12-02", profile="爸爸")
    store.import_data(backup)
    rows = store.list_reminders(True)
    target = next(r for r in rows if r["title"] == "复查")
    time.sleep(1.05)
    store.update_reminder(target["id"], due_at="2026-12-08")
    polluted = copy.deepcopy(backup)
    polluted.pop("checksum", None)
    polluted["reminders"].append({
        "id": 99, "profile": "妈妈", "title": "复查",
        "due_at": "2026-12-01", "advance_days": "abc",
        "created_at": CREATED, "updated_at": CREATED,
    })
    result = store.import_data(polluted)
    assert result.get("reminders") == 0
    titles = [r["title"] for r in store.list_reminders(True)]
    assert titles.count("复查") == 1


# ---------------------------------------------------------------- 复诊联动
def test_restore_backup_does_not_relink_rescheduled_visit(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], status="completed"
    )
    backup = store.export_data()
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-10-20"
    )
    store.import_data(backup)
    row = store.get_visit_record(visit["id"])
    assert int(row.get("followup_appointment_id") or 0) == 0


def test_reopened_detached_reminder_deleted_on_reschedule(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    reminder_id = first["reminder"]["id"]
    extra, created = store.ensure_reminder(
        "就诊：复诊：高血压", "2026-12-13", profile="妈妈",
        appointment_id=appointment_id, match_due=False,
    )
    assert created and extra is not None
    store.complete_reminder(reminder_id)
    assert store.reopen_reminder(reminder_id)
    assert int(
        store.get_reminder(reminder_id).get("appointment_id") or 0
    ) == 0
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-10"
    )
    assert store.get_reminder(reminder_id) is None
    assert store.get_reminder(extra["id"]) is None


def test_done_reminder_detached_when_appointment_dropped(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    reminder_id = first["reminder"]["id"]
    store.complete_reminder(reminder_id)
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-10"
    )
    assert store.get_appointment(appointment_id) is None
    reminder = store.get_reminder(reminder_id)
    assert reminder is not None
    assert int(reminder.get("appointment_id") or 0) == 0


# ---------------------------------------------------------------- 代理
def test_forwarded_rightmost_mapped_trusted_hop(monkeypatch):
    import api_server

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv(
        "MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded"
    )
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")

    class FakeRequest:
        headers = {
            "forwarded": (
                "for=203.0.113.9, for=::ffff:172.18.0.2"
            )
        }
        client = type("C", (), {"host": "172.18.0.2"})()

    assert api_server._forwarded_client_address(
        FakeRequest()
    ) == "203.0.113.9"


def test_compare_gate_no_wait_when_idle():
    import api_server

    api_server._auth_compare_gates.clear()

    async def run():
        gate = await api_server._auth_compare_enter("ip:r53-idle")
        api_server._auth_compare_exit(gate)
        time.sleep(0.3)
        started = time.monotonic()
        gate = await api_server._auth_compare_enter("ip:r53-idle")
        elapsed = time.monotonic() - started
        api_server._auth_compare_exit(gate)
        return elapsed

    assert asyncio.run(run()) < 0.1


def test_compare_gate_serializes_without_parallel_compares():
    import api_server

    api_server._auth_compare_gates.clear()

    async def run():
        state = {"active": 0, "peak": 0}

        async def worker():
            gate = await api_server._auth_compare_enter("ip:r53-peak")
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            await asyncio.sleep(0.02)
            state["active"] -= 1
            api_server._auth_compare_exit(gate)

        await asyncio.gather(*(worker() for _ in range(4)))
        return state["peak"]

    assert asyncio.run(run()) == 1


def test_host_check_documented_as_not_a_credential():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "不是认证边界" in api_source
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "无法区分" in readme
    assert "必须设置 `MEDIX_ACCESS_TOKEN`" in readme


# ---------------------------------------------------------------- 报表/审计
def test_family_overview_daily_total_without_target(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    day = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.add_metric("妈妈", "water", 1200, measured_at=f"{day}T08:00")
    store.add_metric("妈妈", "water", 800, measured_at=f"{day}T20:00")
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    water = next(
        item for item in row["metrics"] if item["metric"] == "water"
    )
    assert water["daily_judged_total"] == 2000
    assert water["daily_judged_day"] == day


def test_family_overview_and_visit_prep_skip_future_visits(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    past = (datetime.now() - timedelta(days=5)).strftime("%Y-%m-%d")
    store.add_visit_record("妈妈", visit_date=tomorrow, diagnosis="未来就诊")
    store.add_visit_record("妈妈", visit_date=past, diagnosis="既往就诊")
    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["visits"]["latest"]["diagnosis"] == "既往就诊"
    prep = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈"}
    )
    if prep.status_code == 200:
        last = prep.json().get("last_visit") or {}
        assert last.get("diagnosis") != "未来就诊"


def test_health_insights_source_counts_use_all_alerts():
    import api_server

    safety = {
        "level": "warning",
        "count": 6,
        "alerts": [
            {"title": f"提示{i}", "source": "base" if i < 3 else "lab"}
            for i in range(5)
        ],
        "by_source": {"base": 3, "lab": 2, "condition": 1},
    }
    insights = api_server._build_health_insights(
        {"days": 7, "profile": "妈妈"}, safety, family_scope=False
    )
    item = next(i for i in insights if i["key"] == "medication_safety")
    assert "6 条" in item["title"]
    assert "化验 2 条" in item["detail"]
    assert "慢病用药 1 条" in item["detail"]


def test_delete_profile_redacts_metric_and_rename_audit(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("母亲", {"sex": "女"})
    store.log_action("metric_target.set", "妈妈·steps", "8000")
    store.log_action("profile.rename", "妈妈", "母亲")
    response = client.delete("/api/profiles/母亲")
    assert response.status_code == 200
    audit = store.list_audit_log(limit=1000)
    blob = " ".join(
        f"{item.get('target') or ''} {item.get('detail') or ''}"
        for item in audit
    )
    assert "妈妈·steps" not in blob
    assert "母亲" not in blob


def test_bulk_delete_reminder_redacts_audit_titles(client_and_store):
    client, store = client_and_store
    reminder = store.add_reminder(
        "复查肿瘤标志物", "2026-12-01", profile="妈妈"
    )
    store.complete_reminder(reminder["id"])
    store.log_action(
        "reminder.create", f"{reminder['id']}·复查肿瘤标志物"
    )
    store.log_action(
        "reminder.update", f"{reminder['id']}·复查肿瘤标志物"
    )
    response = client.post(
        "/api/reminders/bulk_delete", json={"ids": [reminder["id"]]}
    )
    assert response.status_code == 200
    audit = store.list_audit_log(limit=1000)
    targets = " ".join(str(item.get("target") or "") for item in audit)
    assert "复查肿瘤标志物" not in targets


# ---------------------------------------------------------------- 前端
def test_frontend_round53_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 空会话换档：await 前先应用档案，失败回滚，被接管保留
    start = app_js.index("async function applySelectedProfile() {")
    end = app_js.index("\n}\n", start) + 3
    body = app_js[start:end]
    assert "const ok = await detachSessionForProfileChange();" in body
    assert body.index("setActiveProfile(name);") < body.index(
        "const ok = await detachSessionForProfileChange();"
    )
    assert "if (!ok) {" in body
    assert "const takenOver = Boolean(" in body
    # 在途主诉：过期丢弃、无会话不放回、清空输入时移除
    restore = app_js[
        app_js.index("function restoreInflightQuestion()"):
        app_js.index("window.addEventListener('beforeunload'")
    ]
    assert "6 * 60 * 60 * 1000" in restore
    assert "if (owner && !sessionId) return;" in restore
    assert "safeSessionRemove(INFLIGHT_QUESTION_KEY);" in app_js
    assert "if (!busy && !sendInFlight) {" in app_js
    # 深链被接管：不写成当前会话草稿
    send_start = app_js.index("async function send(question, composerSubmit = false)")
    send = app_js[
        send_start:app_js.index(
            "const controller = new AbortController();", send_start
        )
    ]
    assert "els.input.value = question;" in send
    assert "会诊已切换，刚才的主诉未提交" in send
