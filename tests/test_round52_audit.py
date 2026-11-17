"""第五十二轮审查：跨成员同秒孪生误认领、双重否定分诊、孪生删+编辑、
互斥比较闸门、换档等待竞态、家属照护发热、定义前缀孕产期、医嘱否定
停药、非妊娠部位出血、非指标表非法孪生、完成预约链接、兜底认领证据、
代理对端归一化、未来记录、无令牌代理暴露、提醒非法孪生、未指定未来
记录与附件导出快照。"""
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


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created, note=""):
    return {
        "id": raw_id, "profile": profile, "name": name, "dose": dose,
        "frequency": "每日一次", "note": note, "active": True,
        "created_at": created, "updated_at": created,
    }


def test_cross_member_twin_edit_not_claimed_as_moved(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    for index in range(3):
        store.add_medication("爸爸", f"旧药{index}", "1g")
    created = "2026-06-01T08:00:00"
    backup = {
        "medications": [
            _med(5, "妈妈", "阿司匹林", "100mg", created),
            _med(6, "爸爸", "阿司匹林", "100mg", created),
        ],
        "medication_logs": [{
            "id": 1, "medication_id": 5, "profile": "妈妈",
            "taken_at": "2026-06-01T09:00", "created_at": created,
        }],
    }
    store.import_data(backup)
    mom_row = next(
        item for item in store.list_medications(limit=100)
        if item["profile"] == "妈妈" and item["name"] == "阿司匹林"
    )
    dad_row = next(
        item for item in store.list_medications(limit=100)
        if item["profile"] == "爸爸" and item["name"] == "阿司匹林"
    )
    store.delete_medication(mom_row["id"])
    time.sleep(1.05)
    store.update_medication(dad_row["id"], note="edited")
    store.import_data(backup)
    rows = store.list_medications(limit=100)
    mom_rows = [
        item for item in rows
        if item["profile"] == "妈妈" and item["name"] == "阿司匹林"
    ]
    dad_rows = [
        item for item in rows
        if item["profile"] == "爸爸" and item["name"] == "阿司匹林"
    ]
    assert len(mom_rows) == 1
    assert len(dad_rows) == 1
    assert dad_rows[0]["note"] == "edited"
    logs = store.list_medication_logs(None, "妈妈", 100)
    assert logs
    assert all(log["profile"] == "妈妈" for log in logs)


def test_twin_delete_and_soft_edit_restores_two_rows(store):
    store.save_profile("妈妈", {"sex": "女"})
    first = store.add_medication("妈妈", "阿司匹林", "100mg")
    second = store.add_medication("妈妈", "阿司匹林", "100mg")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    store.delete_medication(first["id"])
    time.sleep(1.05)
    store.update_medication(second["id"], note="edited")
    result = store.import_data(backup)
    assert result.get("medications") == 1
    rows = store.list_medications(limit=100)
    assert len(rows) == 2
    notes = sorted(str(item.get("note") or "") for item in rows)
    assert notes == ["", "edited"]


def test_non_metric_invalid_twin_does_not_inflate_group(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication("妈妈", "阿司匹林", "5mg")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    time.sleep(1.05)
    store.update_medication(
        store.list_medications(limit=10)[0]["id"], dose="10mg"
    )
    polluted = copy.deepcopy(backup)
    polluted.pop("checksum", None)
    polluted["medications"].append({
        "id": 99, "profile": "妈妈", "name": "", "dose": "",
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-01T08:00:00",
    })
    result = store.import_data(polluted)
    assert result.get("medications") == 0
    assert result.get("invalid", 0) >= 1
    rows = store.list_medications(limit=10)
    assert [item["dose"] for item in rows] == ["10mg"]


def test_reminder_invalid_twin_does_not_duplicate(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_reminder("复查", "2026-12-01", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    time.sleep(1.05)
    store.update_reminder(
        store.list_reminders(True)[0]["id"], note="edited"
    )
    polluted = copy.deepcopy(backup)
    polluted.pop("checksum", None)
    polluted["reminders"].append({
        "id": 99, "profile": "妈妈", "title": "复查",
        "due_at": "not-a-date",
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-01T08:00:00",
    })
    result = store.import_data(polluted)
    assert result.get("reminders") == 0
    rows = store.list_reminders(True)
    assert len(rows) == 1
    assert rows[0]["note"] == "edited"


def test_fallback_claim_requires_edit_evidence(store):
    store.save_profile("妈妈", {"sex": "女"})
    unrelated = store.add_appointment(
        "妈妈", "牙科检查", scheduled_at="2026-12-31T09:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00', id = id"
        )
    backup = {"appointments": [{
        "id": unrelated["id"], "profile": "妈妈", "title": "复诊：高血压",
        "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-01T08:00:00",
    }]}
    store.import_data(backup)
    titles = sorted(item["title"] for item in store.list_appointments(None, None))
    assert titles == ["复诊：高血压", "牙科检查"]


def test_fallback_claim_rejects_unrelated_row_before_export(store):
    store.save_profile("妈妈", {"sex": "女"})
    unrelated = store.add_appointment(
        "妈妈", "牙科检查", scheduled_at="2026-12-31T09:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    # 备份导出时间晚于本地行最后修改：同主键同秒只是撞号，
    # 不能把本地无关记录当成备份行的旧版本
    backup = {
        "exported_at": "2026-11-01T00:00:00",
        "appointments": [{
            "id": unrelated["id"], "profile": "妈妈",
            "title": "复诊：高血压",
            "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
            "created_at": "2026-06-01T08:00:00",
            "updated_at": "2026-06-01T08:00:00",
        }],
    }
    store.import_data(backup)
    titles = sorted(item["title"] for item in store.list_appointments(None, None))
    assert titles == ["复诊：高血压", "牙科检查"]


def test_completed_appointment_clears_visit_reminder_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], status="completed"
    )
    linked = store.get_visit_record(visit["id"])
    assert int(linked.get("followup_reminder_id") or 0) == 0
    # 完成时待办提醒已删除；提醒主键可能被下一条提醒复用，
    # 就诊记录上残留的旧链接会让之后的就诊编辑误删无关提醒
    fresh = store.add_reminder("自备药", "2026-12-20", profile="妈妈")
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-21"
    )
    assert store.get_reminder(fresh["id"]) is not None


# ---------------------------------------------------------------- 分诊
def test_triage_double_negation_keeps_anticoagulant():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我不得不吃华法林，今天头撞到门框",
        "医生说我不能不吃华法林，今天头撞到门框",
        "我并非不吃华法林，今天头撞到门框",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    assert detect_red_flags(
        patient_complaint_text("我不吃华法林，今天头撞到门框"), {}
    )["level"] == "none"


def test_family_care_clause_keeps_patient_fever():
    from core.triage import detect_red_flags, patient_complaint_text

    assert "免疫功能低下伴发热" in detect_red_flags(
        patient_complaint_text("我照顾我爸时发烧38.5度"),
        {"history": "化疗"},
    )["matches"]
    assert detect_red_flags(
        patient_complaint_text("我陪我妈去化疗，今天发烧38.5度"), {}
    )["level"] == "none"


def test_definition_prefix_pregnancy_and_family():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("请解释一下阴道出血是什么病"),
        {"history": "怀孕20周"},
    )
    assert "孕产期出血" in result["matches"]
    assert detect_red_flags(
        patient_complaint_text("请解释一下我爸胸痛是什么原因"), {}
    )["level"] == "emergency"
    assert patient_complaint_text("介绍一下呼吸困难") == ""


def test_non_pregnancy_bleeding_sites_excluded():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕20周，大便出血",
        "怀孕20周，尿里有血",
        "怀孕20周，痰中带血",
    ):
        assert detect_red_flags(patient_complaint_text(text), {})[
            "level"
        ] == "none", text
    assert "孕产期出血" in detect_red_flags(
        patient_complaint_text("怀孕20周，阴道出血"), {}
    )["matches"]


def test_medical_order_stop_negations_keep_current_use():
    from core.medication_safety import check_medication_safety

    for text in (
        "不擅自停用华法林，能吃布洛芬吗",
        "请勿停用华法林，能吃布洛芬吗",
        "切勿停用华法林，能吃布洛芬吗",
        "不得停用华法林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    assert check_medication_safety(
        "已经停用华法林，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- 代理/安全
def test_trusted_proxy_peer_forms_are_normalized(monkeypatch):
    import api_server

    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")
    assert api_server._is_trusted_proxy_peer("::ffff:172.18.0.2") is True
    assert api_server._is_trusted_proxy_peer("172.18.0.2:8080") is True
    monkeypatch.setenv(
        "MEDIX_TRUSTED_PROXY_ADDRS", "::ffff:172.18.0.2"
    )
    assert api_server._is_trusted_proxy_peer("172.18.0.2") is True


def test_compare_gate_serializes_with_lock():
    import asyncio

    import api_server

    api_server._auth_compare_gates.clear()

    async def run():
        times = []

        async def worker():
            gate = await api_server._auth_compare_enter("ip:round52")
            assert gate is not None
            times.append(asyncio.get_running_loop().time())
            api_server._auth_compare_exit(gate)

        await asyncio.gather(*(worker() for _ in range(2)))
        return sorted(times)

    times = asyncio.run(run())
    assert times[1] - times[0] >= 0.15


def test_compare_gate_releases_lock_when_cancelled():
    import asyncio

    import api_server

    api_server._auth_compare_gates.clear()

    async def run():
        first = await api_server._auth_compare_enter("ip:round52-cancel")
        api_server._auth_compare_exit(first)
        holder = asyncio.create_task(
            api_server._auth_compare_enter("ip:round52-cancel")
        )
        await asyncio.sleep(0.05)
        holder.cancel()
        try:
            await holder
        except asyncio.CancelledError:
            pass
        # 取消后锁必须已释放，否则后续请求会永久排队
        gate = await asyncio.wait_for(
            api_server._auth_compare_enter("ip:round52-cancel"),
            timeout=2.0,
        )
        api_server._auth_compare_exit(gate)

    asyncio.run(run())


def test_no_token_loopback_proxy_host_is_rejected(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("MEDIX_ALLOW_INSECURE_LAN", raising=False)
    monkeypatch.setenv("MEDIX_ALLOWED_HOSTS", "medix.local")
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        lan_via_proxy = TestClient(
            api_server.app,
            base_url="http://medix.local",
            client=("127.0.0.1", 50000),
        )
        blocked = lan_via_proxy.get("/api/sessions")
        assert blocked.status_code == 403
        allowed = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("127.0.0.1", 50000),
        ).get("/api/sessions")
        assert allowed.status_code == 200
    finally:
        store.close()


# ---------------------------------------------------------------- 报表
def test_future_records_excluded_from_activity_and_weekly(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    old_visit_day = (
        datetime.now() - timedelta(days=5)
    ).strftime("%Y-%m-%d")
    store.add_symptom("妈妈", "咳嗽", 3, recorded_at=f"{tomorrow}T08:00")
    store.add_visit_record(
        "妈妈", visit_date=tomorrow, diagnosis="未来就诊"
    )
    store.add_visit_record(
        "妈妈", visit_date=old_visit_day, diagnosis="既往就诊"
    )
    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert not str(row["last_activity"]).startswith(tomorrow)
    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 30}
    ).json()
    latest = weekly["visits"]["latest"]
    if latest is not None:
        assert latest["visit_date"] <= old_visit_day


def test_unassigned_future_record_not_shown(client_and_store):
    client, store = client_and_store
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    store.add_metric("", "weight", 60, measured_at=f"{tomorrow}T08:00")
    assert store.has_unassigned_records(7) is False
    data = client.get("/api/family/overview", params={"days": 7}).json()
    assert all(item["name"] != "" for item in data["profiles"])


# ---------------------------------------------------------------- 前端/导出
def test_frontend_round52_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    switch = app_js[
        app_js.index("const hasTurns = ("):
        app_js.index("} else if (sessionId) {", app_js.index("const hasTurns = ("))
    ]
    assert switch.index("applyPatientContext(readProfiles()[name].context || {});") < switch.index("const created = await startNewSession();")
    assert "const takenOver = Boolean(" in switch
    assert "setActiveProfile(previous || '');" in switch
    detach = app_js[
        app_js.index("async function detachSessionForProfileChange"):
        app_js.index("function persistSessionId")
    ]
    assert "const beforeSession = sessionId;" in detach
    assert "if (!takenOver) {" in detach
    send_start = app_js.index("async function send(question, composerSubmit = false)")
    send = app_js[
        send_start:app_js.index(
            "const controller = new AbortController();", send_start
        )
    ]
    assert "els.empty && !els.thread.querySelector('.turn')" in send
    # 第六十八轮：合并草稿时按主诉移除，不再要求输入框与主诉全等
    assert "const remaining = stripInflightQuestion(" in app_js
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "snapshot=True" in api_source
    assert "100000 if snapshot else 1000" in api_source
