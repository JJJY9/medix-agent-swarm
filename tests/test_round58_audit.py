"""第五十八轮审查：意图停药/验孕证据/计划停化疗/过敏分句剥离/漏服
动词、孪生内容编辑归属、风险谓词否定、首屏接管与草稿档案、复诊复用与
级联、闸门来源回收与取消释放、无目标血压、空白成员、库存去重。"""
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


def _freeze_clock(monkeypatch, start="2026-11-15T10:00:00"):
    from memory.session_store import SessionStore

    clock = {"now": start}
    monkeypatch.setattr(
        SessionStore, "_now", staticmethod(lambda: clock["now"])
    )
    return clock


# ---------------------------------------------------------------- 分诊/用药
def test_intent_stop_keeps_current_medication():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "准备不吃华法林了，头撞到门框",
        "打算不吃华法林了，头撞到门框",
        "准备不再吃华法林了，头撞到门框",
        "计划不服华法林了，头撞到门框",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    for text in (
        "准备不吃华法林了，现在能吃布洛芬吗",
        "打算不吃华法林了，现在能吃布洛芬吗",
        "准备不再吃华法林了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


def test_pregnancy_evidence_terms():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "验孕阳性，阴道出血",
        "验孕棒两条杠，阴道出血",
        "验孕棒阳性，下面出血",
        "自然流产，阴道出血",
        "流产了，阴道出血",
        "不全流产，阴道出血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    # 病史路径同样生效
    result = detect_red_flags(
        patient_complaint_text("阴道出血"), {"history": "验孕阳性"}
    )
    assert "孕产期出血" in result["matches"]
    # 既往流产史不是当前妊娠
    result = detect_red_flags(
        patient_complaint_text("自然流产史，阴道出血"), {}
    )
    assert "孕产期出血" not in result["matches"]


def test_planned_chemo_stop_still_immunosuppressed():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "明天不做化疗了，今天发烧38.5度",
        "下周不化疗了，今天发烧38.5度",
        "打算不再化疗了，今天发烧38.5度",
        "准备不做化疗了，今天发烧38.5度",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "免疫功能低下伴发热" in result["matches"], text


def test_allergy_phrase_stripped_not_whole_clause():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        "今天撞到头", {"history": "华法林每天一片对磺胺过敏"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = detect_red_flags(
        patient_complaint_text("我今天撞到头，我有华法林每天一片对磺胺过敏"),
        {},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = detect_red_flags(
        "阴道出血", {"history": "怀孕中期对青霉素过敏"}
    )
    assert "孕产期出血" in result["matches"]
    result = detect_red_flags(
        "今天发烧38.5度", {"history": "化疗期间对青霉素过敏"}
    )
    assert "免疫功能低下伴发热" in result["matches"]
    # 纯过敏陈述仍不算当前用药
    result = detect_red_flags("今天撞到头", {"history": "肝素过敏"})
    assert result["level"] == "none"
    result = detect_red_flags(
        patient_complaint_text("我在吃华法林和抗过敏药，今天撞到头"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]


def test_dose_missed_fu_yong_verbs():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我今天没有服用华法林，刚才头磕到桌角了",
        "我今天没服用华法林，刚才头磕到桌角了",
        "我昨天未服用华法林，今天头磕到桌角了",
        "我今天没口服华法林，刚才头磕到桌角了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    for text in (
        "我今天没有服用华法林，现在能吃布洛芬吗",
        "我今天没服华法林，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


def test_greeting_definition_questions_do_not_alert():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "你好，胸痛是什么病",
        "您好，抽搐是什么病",
        "谢谢，胸痛是什么病",
        "医生您好，抽搐是什么病",
        "好的，胸痛是什么病",
        "在吗，咯血是什么病",
        "咨询一下，抽搐是什么病",
        "我想问下，胸痛是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text
    # 真实症状分句仍建立患者语境
    for text, label in (
        ("手脚发麻，说话不清是什么病", "言语不清"),
        ("胸口闷痛，呼吸困难是什么病", "呼吸困难"),
        ("浑身没劲，说话不清是什么病", "言语不清"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    # 否定铺垫不建立语境
    assert detect_red_flags(
        patient_complaint_text("没有胸痛，抽搐是什么病")
    )["level"] == "none"


def test_english_drug_name_with_chinese_stop_patterns():
    from core.medication_safety import check_medication_safety

    for text in (
        "我今天没吃 warfarin，现在能吃布洛芬吗",
        "下周就不吃 warfarin 了，现在能吃布洛芬吗",
        "明天就不吃warfarin了，现在能吃布洛芬吗",
        "我一个月后停用 warfarin，今天能吃布洛芬吗",
        "准备不吃 warfarin 了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- 导入
def test_twin_delete_with_content_edit_no_duplicate(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.add_appointment(
            "妈妈", "复诊", "心内科", scheduled_at="2026-12-14T09:00",
        )
        second = source.add_appointment(
            "妈妈", "复诊", "心内科", scheduled_at="2026-12-14T09:00",
        )
        source.update_appointment_partial(
            second["id"], status="completed", note="已完成备注",
        )
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        twins = [
            item for item in target.list_appointments("妈妈", None)
            if item["title"] == "复诊"
        ]
        completed = next(
            item for item in twins if item["status"] == "completed"
        )
        assert target.delete_appointment(completed["id"]) is True
        upcoming = next(
            item for item in twins if item["status"] == "upcoming"
        )
        clock["now"] = "2026-11-15T10:00:10"
        # 存活行编辑内容列（scheduled_at）
        target.update_appointment_partial(
            upcoming["id"], scheduled_at="2026-12-20T09:00",
        )
        clock["now"] = "2026-11-15T10:00:20"
        result = target.import_data(backup)
        assert result["appointments"] == 1
        twins = [
            item for item in target.list_appointments("妈妈", None)
            if item["title"] == "复诊"
        ]
        assert len(twins) == 2
        statuses = sorted(item["status"] for item in twins)
        assert statuses == ["completed", "upcoming"]
        kept = next(
            item for item in twins if item["status"] == "upcoming"
        )
        assert kept["scheduled_at"][:10] == "2026-12-20"
        # 重复导入保持幂等
        assert target.import_data(backup)["appointments"] == 0
        assert len([
            item for item in target.list_appointments("妈妈", None)
            if item["title"] == "复诊"
        ]) == 2
    finally:
        target.close()


def test_twin_content_edit_with_pk_hole_idempotent(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        first = source.add_allergy("妈妈", "penicillin", reaction="rash")
        source.add_allergy("妈妈", "penicillin", reaction="shock")
        source.add_allergy("妈妈", "penicillin", reaction="hives")
        assert source.delete_allergy(first["id"]) is True
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        rows = target.list_allergies("妈妈", limit=20)
        assert sorted(item["reaction"] for item in rows) == [
            "hives", "shock"
        ]
        shock = next(item for item in rows if item["reaction"] == "shock")
        clock["now"] = "2026-11-15T10:00:10"
        # 编辑内容列（substance），主键与备份 raw id 错位
        target.update_allergy(shock["id"], substance="renamed")
        clock["now"] = "2026-11-15T10:00:20"
        result = target.import_data(backup)
        assert result["allergies"] == 0
        rows = target.list_allergies("妈妈", limit=20)
        assert len(rows) == 2
        assert sorted(item["reaction"] for item in rows) == [
            "hives", "shock"
        ]
        assert target.import_data(backup)["allergies"] == 0
        assert len(target.list_allergies("妈妈", limit=20)) == 2
    finally:
        target.close()


def test_reminder_rename_with_pk_hole_no_duplicate(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        rem0 = source.add_reminder(
            "rem0", "2026-12-01T09:00", profile="妈妈",
        )
        source.add_reminder("rem1", "2026-12-01T09:00", profile="妈妈")
        source.add_reminder("rem2", "2026-12-01T09:00", profile="妈妈")
        assert source.delete_reminder(rem0["id"]) is True
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        rows = target.list_reminders(include_done=True)
        assert sorted(item["title"] for item in rows) == ["rem1", "rem2"]
        rem1 = next(item for item in rows if item["title"] == "rem1")
        clock["now"] = "2026-11-15T10:00:10"
        target.update_reminder(rem1["id"], title="renamed")
        clock["now"] = "2026-11-15T10:00:20"
        result = target.import_data(backup)
        assert result["reminders"] == 0
        rows = target.list_reminders(include_done=True)
        assert sorted(item["title"] for item in rows) == [
            "rem2", "renamed"
        ]
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_schedule_followup_reuses_owned_appointment(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    clock["now"] = "2026-11-15T10:00:10"
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-25T09:00",
    )
    store.delete_visit_record(visit["id"])
    store.update_appointment_partial(appointment_id, status="upcoming")
    clock["now"] = "2026-11-15T10:00:30"
    store.import_data(backup)
    visits = store.list_visit_records("妈妈")
    assert len(visits) == 1
    imported_visit = visits[0]
    clock["now"] = "2026-11-15T10:00:40"
    result = _schedule_followup_sync(store, imported_visit["id"])
    assert result["appointment_created"] is False
    upcoming = [
        item for item in store.list_appointments("妈妈", None)
        if item["status"] == "upcoming"
        and int(item.get("owner_visit_id") or 0)
        == imported_visit["id"]
    ]
    assert len(upcoming) == 1
    assert upcoming[0]["scheduled_at"][:10] == "2026-12-14"
    pending = [
        item for item in store.list_reminders(False, "妈妈")
        if int(item.get("appointment_id") or 0)
        == int(upcoming[0]["id"])
    ]
    assert len(pending) == 1
    assert str(pending[0]["due_at"])[:10] == "2026-12-13"


def test_delete_visit_drops_owned_unlinked_appointment(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    clock["now"] = "2026-11-15T10:00:10"
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-25T09:00",
    )
    store.delete_visit_record(visit["id"])
    store.update_appointment_partial(appointment_id, status="upcoming")
    clock["now"] = "2026-11-15T10:00:30"
    store.import_data(backup)
    visits = store.list_visit_records("妈妈")
    assert len(visits) == 1
    imported_visit = visits[0]
    # 导入后预约 owner/备注指向就诊，但链接未写回
    assert int(imported_visit["followup_appointment_id"] or 0) == 0
    clock["now"] = "2026-11-15T10:00:40"
    assert store.delete_visit_record(imported_visit["id"]) is True
    remaining = [
        item for item in store.list_appointments("妈妈", None)
        if item["status"] == "upcoming"
    ]
    assert remaining == []
    pending = store.list_reminders(False, "妈妈")
    assert pending == []


# ---------------------------------------------------------------- 风险/报表
def test_risk_predicate_negation(client_and_store):
    import api_server

    for text in (
        "目前未达到高危标准，属于低危范围，先观察即可。",
        "未发现高危表现。",
        "暂未发现高危征象。",
        "不符合高危特征。",
        "不支持高危诊断。",
    ):
        assert api_server.parse_risk_level(text) != "high", text
    # 双重否定仍按高危
    assert api_server.parse_risk_level("不能排除高危。") == "high"

    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "s58", "最近头晕，帮我评估风险",
        "风险等级：低危。建议规律作息，必要时复诊。", profile="妈妈",
    )
    store.save_exchange(
        "s58", "那我这个是高危吗？",
        "目前未达到高危标准，属于低危范围，先观察即可。", profile="妈妈",
    )
    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 30}
    ).json()
    assert weekly["consultations"]["risk"] == "low"
    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    member = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert member["attention"]["label"] != "最近会诊提示高风险"
    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    keys = {
        item["key"] for item in insights.get("insights") or []
    }
    assert "consultation_risk" not in keys


def test_family_attention_no_target_bp(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric(
        "妈妈", "bp", 170, 105, measured_at=f"{today}T08:00",
    )
    store.save_profile("爸爸", {"sex": "男"})
    store.add_metric(
        "爸爸", "bp", 145, 92, measured_at=f"{today}T08:00",
    )
    store.save_profile("奶奶", {"sex": "女"})
    store.add_metric(
        "奶奶", "bp", 125, 80, measured_at=f"{today}T08:00",
    )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    members = {item["name"]: item for item in data["profiles"]}
    assert members["妈妈"]["attention"]["level"] == "warning"
    assert "血压偏高" in members["妈妈"]["attention"]["label"]
    assert members["爸爸"]["attention"]["level"] == "info"
    assert "血压偏高" in members["爸爸"]["attention"]["label"]
    assert members["奶奶"]["attention"]["level"] == "ok"


def test_overdue_appointment_keeps_unassigned_member(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.add_appointment(
        "", "心内科复诊",
        scheduled_at=f"{today - timedelta(days=2)}T09:00",
    )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in data["profiles"] if item["name"] == ""
    )
    assert int(member["appointments"]["overdue"]) == 1
    assert member["attention"]["label"] == "就诊预约已过期 1 个"


def test_stock_alert_count_deduped(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication(
        "妈妈", "胰岛素", "10U", "每日一次",
        stock=0, stock_unit="支", low_stock_threshold=2,
        expiry_date=str(today - timedelta(days=1)),
    )
    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    stock = next(
        item for item in insights["insights"]
        if item["key"] == "medication_stock"
    )
    assert stock["title"].startswith("1 种")


# ---------------------------------------------------------------- 代理
def test_gate_sources_reclaimed_and_cancel_single_release():
    import api_server

    api_server._auth_compare_gates.clear()

    async def run_many():
        key = "ip:r58-sources"
        for index in range(40):
            handle = await api_server._auth_compare_enter(
                key, source=f"S{index}"
            )
            api_server._auth_compare_exit(handle)
        gate = api_server._auth_compare_gates[
            (id(asyncio.get_running_loop()), key)
        ]
        assert not gate.get("sources")
        assert gate["waiting"] == 0

    asyncio.run(run_many())

    async def run_cancel():
        key = "ip:r58-cancel"
        handle = await api_server._auth_compare_enter(key, source="S")
        api_server._auth_compare_exit(handle)
        task_a = asyncio.create_task(
            api_server._auth_compare_enter(key, source="S")
        )
        await asyncio.sleep(0.05)
        task_b = asyncio.create_task(
            api_server._auth_compare_enter(key, source="S")
        )
        await asyncio.sleep(0.05)
        gate = api_server._auth_compare_gates[
            (id(asyncio.get_running_loop()), key)
        ]
        assert gate["sources"].get("S") == 2
        task_a.cancel()
        await asyncio.gather(task_a, return_exceptions=True)
        # 取消只释放一次：仍在途的 B 仍占用一个来源槽位
        assert gate["sources"].get("S") == 1
        task_b.cancel()
        await asyncio.gather(task_b, return_exceptions=True)
        assert not gate.get("sources")

    asyncio.run(run_cancel())


# ---------------------------------------------------------------- 安全/前端
def test_degraded_swarm_summary_skipped():
    source = (ROOT / "swarm" / "swarm_coordinator.py").read_text(
        encoding="utf-8"
    )
    assert (
        'if degraded or not str(final_answer or "").strip():'
        in source
    )
    assert "Degraded or empty answer, skip session summary" in source


def test_frontend_round58_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    send = app_js[
        app_js.index("async function send(question, composerSubmit = false)"):
        app_js.index("async function send(question, composerSubmit = false)") + 5200
    ]
    # 首屏恢复等待后的会话接管复核与恢复失败处理
    assert "const takeover = Boolean(" in send
    assert "会话已切换，刚才的主诉未提交" in send
    assert "initialRestoreOk = Boolean(await restoreCurrentSession());" in send
    assert "resolveInitialSessionRestore(Boolean(ok))" in app_js
    # 等待发送期间禁止新建会话
    start = app_js.index("async function startNewSession()")
    assert "if (busy || sendInFlight) {" in app_js[start:start + 400]
    # 草稿绑定档案
    assert "const DRAFT_PROFILE_KEY = 'medix.draft.profile';" in app_js
    assert "输入框内容属于档案" in send
    save = app_js[
        app_js.index("function saveDraft()"):
        app_js.index("function flushDraftNow()")
    ]
    assert "DRAFT_PROFILE_KEY" in save
    # 就诊保存/删除后刷新预约与提醒角标
    save_visit = app_js[
        app_js.index("async function saveVisit()"):
        app_js.index("async function scheduleVisitFollowup")
    ]
    assert "loadAppointments();" in save_visit
    assert "refreshReminderBadge();" in save_visit
    delete_visit = app_js[
        app_js.index("async function deleteVisit("):
        app_js.index("async function deleteVisit(") + 1200
    ]
    assert "loadAppointments();" in delete_visit
    assert "refreshReminderBadge();" in delete_visit
