"""第五十四轮审查：头外伤语序/漏服、产后N年上下文、科普检索、停药周、
跨档案改挂、提醒档案分组、孪生内容编辑、取消→完成回挂、stale 提醒脱钩、
IPv6 /64 预锁定、删除档案会话归属、月历风险、换档回滚、空深链待重置、
在途主诉档案、深链草稿、导入提醒校验/降级预约、批量删除、改名链。"""
import asyncio
import time
from datetime import datetime
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


# ---------------------------------------------------------------- 分诊
def test_head_trauma_more_phrases_and_no_false_positive():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我吃华法林，今天头外伤了",
        "我吃华法林，刚才被球砸到头",
        "我吃华法林，磕伤了头部",
        "我吃华法林，撞伤了头",
        "我吃华法林，砸到头部了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    for text in (
        "我在吃华法林，脚趾头被砸了一下",
        "我在吃华法林，手指头被门夹了",
        "我在吃华法林，鱼骨头被卡在喉咙里",
        "我在吃华法林，枕头被孩子弄湿了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text


def test_missed_dose_today_keeps_anticoagulant():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("华法林今天没吃，头磕到桌角"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    assert check_medication_safety(
        "我今天没吃华法林，牙疼能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "我没有吃华法林，牙疼能吃布洛芬吗", {}
    )["level"] == "none"


def test_postpartum_years_with_history_or_medications():
    from core.triage import detect_red_flags, patient_complaint_text

    for context in (
        {"history": "高血压5年"},
        {"medications": "华法林"},
        {"active_medications": ["阿司匹林"]},
    ):
        result = detect_red_flags(
            patient_complaint_text("产后2年，阴道出血"), context
        )
        assert result["level"] == "none", context


def test_online_research_prelude_not_patient_context():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在网上查了资料，胸痛是什么病",
        "今天在网上看到胸痛是什么病",
        "在网上看到胸痛是什么病的文章",
    ):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


def test_stopped_one_week_still_recent():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "停用华法林1周了，能吃阿司匹林吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "停用华法林一周了，能吃阿司匹林吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "已经停用华法林两周，能吃阿司匹林吗", {}
    )["level"] != "warning"
    assert check_medication_safety(
        "停用华法林3周了，能吃阿司匹林吗", {}
    )["level"] != "warning"
    assert check_medication_safety(
        "I stopped taking warfarin 1 week ago, can I take ibuprofen?", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "I stopped taking warfarin 3 weeks ago, can I take ibuprofen?", {}
    )["level"] != "warning"


# ---------------------------------------------------------------- 导入
def test_moved_and_content_edited_with_id_drift(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    backup = {"medications": [{
        "id": 5, "profile": "妈妈", "name": "药A", "dose": "5mg",
        "frequency": "每日一次", "note": "", "active": True,
        "created_at": CREATED, "updated_at": CREATED,
    }]}
    store.import_data(backup)
    row = next(
        item for item in store.list_medications(limit=10)
        if item["name"] == "药A"
    )
    store.update_medication(row["id"], profile="爸爸", dose="10mg")
    result = store.import_data(backup)
    rows = store.list_medications(limit=10)
    assert result["medications"] == 0
    assert len(rows) == 1
    assert rows[0]["profile"] == "爸爸"
    assert rows[0]["dose"] == "10mg"


def test_cross_member_same_second_reminders_restored(store):
    store.save_profile("爸爸", {"sex": "男"})
    store.save_profile("妈妈", {"sex": "女"})
    for index in range(5):
        store.add_reminder(f"占位{index}", "2026-12-05", profile="爸爸")
    backup = {"reminders": [
        {"id": 5, "profile": "爸爸", "title": "爸爸复查",
         "due_at": "2026-12-02", "note": "", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 10, "profile": "妈妈", "title": "复查",
         "due_at": "2026-12-01", "note": "买药", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 11, "profile": "妈妈", "title": "复查",
         "due_at": "2026-12-01", "note": "复诊", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    mom_rows = [
        item for item in store.list_reminders(True)
        if item["profile"] == "妈妈"
    ]
    buy = next(item for item in mom_rows if item["note"] == "买药")
    store.delete_reminder(buy["id"])
    store.import_data(backup)
    mom_rows = [
        item for item in store.list_reminders(True)
        if item["profile"] == "妈妈"
    ]
    assert len([r for r in mom_rows if r["title"] == "复查"]) == 2
    assert len([r for r in mom_rows if r["note"] == "买药"]) == 1
    assert len([r for r in mom_rows if r["note"] == "复诊"]) == 1


def test_twin_delete_and_content_edit_keeps_two_rows(store):
    store.save_profile("妈妈", {"sex": "女"})
    for index in range(3):
        store.add_appointment(
            "妈妈", f"占位{index}", scheduled_at="2026-12-30T09:00"
        )
    backup = {"appointments": [
        {"id": 10, "profile": "妈妈", "title": "复诊", "department": "",
         "doctor": "", "location": "",
         "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
         "note": "", "created_at": CREATED, "updated_at": CREATED},
        {"id": 11, "profile": "妈妈", "title": "复诊", "department": "",
         "doctor": "", "location": "",
         "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
         "note": "", "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈", "upcoming")
        if item["title"] == "复诊"
    ]
    store.delete_appointment(rows[0]["id"])
    survivor = next(
        item for item in store.list_appointments("妈妈", "upcoming")
        if item["title"] == "复诊"
    )
    store.update_appointment_partial(
        survivor["id"], scheduled_at="2026-12-20T09:00"
    )
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈", "upcoming")
        if item["title"] == "复诊"
    ]
    assert len(rows) == 2
    assert sorted(item["scheduled_at"][:10] for item in rows) == [
        "2026-12-14", "2026-12-20"
    ]


def test_import_reminder_link_profile_matches_visit(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "爸爸", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    backup = store.export_data()
    store.update_reminder(first["reminder"]["id"], profile="妈妈")
    store.delete_appointment(first["appointment"]["id"])
    store.import_data(backup)
    row = store.get_visit_record(visit["id"])
    reminder_id = int(row.get("followup_reminder_id") or 0)
    if reminder_id:
        reminder = store.get_reminder(reminder_id)
        assert reminder is not None
        assert reminder["profile"] == "爸爸"


def test_downgraded_appointment_not_reowned(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    backup = store.export_data()
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-09-01T09:00"
    )
    store.import_data(backup)
    appointment = store.get_appointment(appointment_id)
    assert int(appointment.get("owner_visit_id") or 0) == 0
    assert "由就诊记录" not in str(appointment.get("note") or "")


# ---------------------------------------------------------------- 复诊
def test_cancelled_then_completed_relinks_visit(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        # 第六十六轮：排程锁内拒绝已过期的复诊日期，改用未来日期
        next_followup_date="2026-12-05",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(appointment_id, status="completed")
    row = store.get_visit_record(visit["id"])
    assert int(row.get("followup_appointment_id") or 0) == appointment_id


def test_import_stale_appointment_detaches_done_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    store.complete_reminder(first["reminder"]["id"])
    backup = store.export_data()
    store.delete_reminder(first["reminder"]["id"])
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-10"
    )
    store.import_data(backup)
    done = [
        item for item in store.list_reminders(True)
        if item.get("done") and item["title"] == "复诊：高血压"
    ]
    for item in done:
        assert int(item.get("appointment_id") or 0) == 0


# ---------------------------------------------------------------- 代理
def test_ipv6_aggregate_prelock_uses_gate(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "round54-secret")
    monkeypatch.delenv("MEDIX_ALLOW_INSECURE_LAN", raising=False)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        neighbor = TestClient(
            api_server.app,
            base_url="http://[2001:db8:5a::2]",
            client=("2001:db8:5a::b2", 50000),
        )
        # 先认证一次取得短期内存凭证：IPv6 全局预算锁定期间未持凭证
        # 的正确令牌同样 429，已认证会话才免比较直通
        assert neighbor.get(
            "/api/sessions",
            headers={"X-Medix-Token": "round54-secret"},
        ).status_code == 200
        attacker = TestClient(
            api_server.app,
            base_url="http://[2001:db8:5a::1]",
            client=("2001:db8:5a::a1", 50000),
        )
        for _ in range(10):
            attacker.get(
                "/api/sessions", headers={"X-Medix-Token": "wrong"}
            )
        response = neighbor.get(
            "/api/sessions",
            headers={"X-Medix-Token": "round54-secret"},
        )
        assert response.status_code == 200
    finally:
        store.close()
        with api_server.auth_failure_limiter._lock:
            api_server.auth_failure_limiter._hits.clear()
        with api_server.auth_failure_limiter_loopback._lock:
            api_server.auth_failure_limiter_loopback._hits.clear()


# ---------------------------------------------------------------- 报表
def test_deleted_profile_sessions_not_unassigned(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "r54-deleted", "头晕怎么办", "风险等级：高危。建议尽快就医。",
        profile="妈妈",
    )
    response = client.delete("/api/profiles/妈妈")
    assert response.status_code == 200
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    unassigned = [
        item for item in data["profiles"] if item["name"] == ""
    ]
    # 已删除档案的会诊不能被记成「未指定」成员的高危会诊
    for item in unassigned:
        assert int(item["consultations"]["count"] or 0) == 0
        assert not item["consultations"].get("risk")


def test_calendar_and_timeline_use_risk_answer(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "r54-risk", "头晕怎么办", "风险等级：高危。建议尽快就医。",
        profile="妈妈",
    )
    store.save_exchange(
        "r54-risk", "还要注意什么", "注意休息，多喝水。",
        profile="妈妈",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    calendar = client.get(
        "/api/health/calendar", params={"profile": "妈妈"}
    ).json()
    assert calendar["days"][today]["consultations"] >= 1
    assert calendar["days"][today]["alerts"] >= 1
    timeline = client.get(
        "/api/timeline",
        params={"profile": "妈妈", "days": 7, "only_risk": 1},
    ).json()
    assert timeline["total"] >= 1


# ---------------------------------------------------------------- 审计
def test_multi_rename_chain_scrubbed(client_and_store):
    client, store = client_and_store
    store.save_profile("母亲二号", {"sex": "女"})
    store.log_action("metric_target.set", "妈妈·steps", "8000")
    store.log_action("profile.rename", "妈妈", "母亲")
    store.log_action("profile.rename", "母亲", "母亲二号")
    response = client.delete("/api/profiles/母亲二号")
    assert response.status_code == 200
    audit = store.list_audit_log(limit=1000)
    blob = " ".join(
        f"{item.get('target') or ''} {item.get('detail') or ''}"
        for item in audit
    )
    assert "妈妈" not in blob
    assert "母亲" not in blob


def test_bulk_delete_invalid_ids_do_not_500(client_and_store):
    client, _store = client_and_store
    response = client.post(
        "/api/reminders/bulk_delete", json={"ids": [0]}
    )
    assert response.status_code == 200
    assert response.json()["deleted"] == 0
    response = client.post(
        "/api/reminders/bulk_delete", json={"ids": [-5, 0]}
    )
    assert response.status_code == 200


# ---------------------------------------------------------------- 前端
def test_frontend_round54_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 换档接管判定不再使用 startNewSession 自己递增的序号
    assert "openSessionSeq !== attemptSeq" not in app_js
    assert "openSessionSeq !== beforeSeq" not in app_js
    # 空深链恢复与 openSession 冲突都包含待重置换档
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("async function send(question, composerSubmit = false)")
    ]
    assert "|| pendingSessionReset" in restore
    open_body = app_js[
        app_js.index("async function openSession"):
        app_js.index("async function openSession") + 3000
    ]
    assert "|| pendingSessionReset" in open_body
    # 在途主诉记录档案并在恢复时校验
    # 第七十二轮起：INFLIGHT 记录与请求携带的档案快照一致
    assert app_js.count("profile: sendProfile || ''") == 2
    assert "profile !== currentProfile" in app_js
    # 深链接管文本不写当前会话草稿，pagehide 同步落盘同样生效
    assert "let detachedComposerText = '';" in app_js
    assert "function composerIsDetached()" in app_js
    flush = app_js[
        app_js.index("function flushDraftNow()"):
        app_js.index("window.addEventListener('pagehide', flushDraftNow);")
    ]
    assert "composerIsDetached()" in flush
    # 第七十六轮：清空输入框只丢弃属于当前会话的在途主诉
    assert "clearInflightForCurrentSession();" in flush
