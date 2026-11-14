"""第四十一轮审查：换药同句抗凝、孕期量词否定、枚举「或」、停药续用、
导入备份元数据（主键撞号/同秒孪生/跨档案证据）、新增就诊链接校验、
恢复已完成复诊、提醒复用/重开、风险聚合回退、陈旧指标超标、
导入无效计数、旧版 Mem0 命名空间清理、代理默认 fail-closed、
Forwarded 信任跳、Host 白名单、前端草稿归属与删除会话恢复。"""
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
    monkeypatch.delenv("MEDIX_ALLOWED_HOSTS", raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


# ---------------------------------------------------------------- triage
def test_switch_or_recent_stop_anticoagulant_with_head_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "停用华法林后改用利伐沙班，今天头撞到门框",
        "医生让我停用华法林但我还在吃，今天撞到头",
        "华法林停用两天，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text

    # 真正停用较久且无续用线索仍为阴性
    result = detect_red_flags(
        patient_complaint_text("华法林已经停用两个月了，今天撞到头"), {}
    )
    assert "抗凝治疗期间头部外伤" not in result["matches"]


def test_pregnancy_bleeding_quantifier_and_internal_negation():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕早期少量出血正常吗",
        "孕妇少量出血",
        "产前少量出血",
        "早孕少量流血",
        "孕早期有点出血",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    for text in (
        "怀孕8周，阴道没有出血",
        "怀孕8周，阴道无出血",
        "怀孕8周，阴道未见出血",
        "怀孕8周，阴道不出血",
        "怀孕8周，阴道没有流血",
        "怀孕8周，牙龈见红",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_enum_negation_supports_or_and_common_first_items():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "没有胸痛或呼吸困难",
        "无发热或胸痛",
        "否认胸痛或咯血",
        "没有喉咙痛、胸痛",
        "没有寒战、胸痛",
        "没有流鼻涕、胸痛",
        "没有嗜睡、胸痛",
        "没有怕冷、胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text

    # 词内否定/阳性修饰仍按阳性处理
    for text in (
        "我这两天全身无力、胸痛、冒冷汗",
        "没有力气、胸痛",
        "无诱因、突发胸痛",
        "没有胸痛或突发呼吸困难",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text


def test_medication_restart_cues_keep_current_use():
    from core.medication_safety import check_medication_safety

    for text in (
        "医生让我停用华法林，但我还在吃，能吃布洛芬吗",
        "华法林停了半年，最近又吃上了，能吃布洛芬吗",
        "华法林停药两周，现在重新服用，能吃布洛芬吗",
    ):
        result = check_medication_safety(text, {})
        assert result.get("level") == "warning", text

    # 明确停用且无续用线索仍不告警
    result = check_medication_safety(
        "已经停用华法林两周，能吃阿司匹林吗", {}
    )
    assert result.get("level") != "warning"


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created):
    return {
        "id": raw_id,
        "profile": profile,
        "name": name,
        "dose": dose,
        "frequency": "每日一次",
        "created_at": created,
        "updated_at": created,
    }


def test_import_id_hole_with_edited_survivor_no_duplicate(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        backup = {
            "medications": [
                _med(1, "妈妈", "药1", "1g", "2026-01-01T08:00:00"),
                _med(3, "妈妈", "药3", "1g", "2026-01-03T08:00:00"),
                _med(4, "妈妈", "药4", "1g", "2026-01-04T08:00:00"),
            ]
        }
        assert target.import_data(backup)["medications"] == 3
        survivor = next(
            item for item in target.list_medications(limit=10)
            if item["name"] == "药4"
        )
        assert target.update_medication(survivor["id"], dose="5g")

        result = target.import_data(backup)
        assert result["medications"] == 0
        rows = [
            item for item in target.list_medications(limit=10)
            if item["name"] == "药4"
        ]
        assert len(rows) == 1
        assert rows[0]["dose"] == "5g"
    finally:
        target.close()


def test_import_moved_record_with_other_survivor_not_excluded(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        target.add_medication("妈妈", "旧药", "1g", "每日一次")
        backup = {
            "medications": [
                _med(1, "妈妈", "药A", "1g", "2026-06-01T08:00:00"),
                _med(2, "爸爸", "药B", "2g", "2026-06-01T08:00:00"),
            ]
        }
        assert target.import_data(backup)["medications"] == 2
        moved = next(
            item for item in target.list_medications(limit=20)
            if item["name"] == "药A"
        )
        time.sleep(1.05)
        assert target.update_medication(moved["id"], profile="爸爸")

        result = target.import_data(backup)
        assert result["medications"] == 0
        rows = target.list_medications(limit=20)
        names = [
            (item["profile"], item["name"]) for item in rows
            if item["name"] == "药A"
        ]
        assert names == [("爸爸", "药A")]
    finally:
        target.close()


def test_import_raw_id_collision_without_edit_evidence_restores(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        created = "2026-06-01T08:00:00"
        target.import_data({
            "medications": [_med(1, "爸爸", "药A", "1g", created)]
        })
        backup = {
            "medications": [_med(1, "妈妈", "药A", "1g", created)]
        }

        result = target.import_data(backup)

        # 爸爸的未编辑行不能被当成「已改挂」吞掉：妈妈的记录必须补回
        assert result["medications"] == 1
        names = sorted(
            (item["profile"], item["name"])
            for item in target.list_medications(limit=10)
        )
        assert names == [("妈妈", "药A"), ("爸爸", "药A")]
    finally:
        target.close()


def test_import_appointments_cross_member_twin_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        created = "2026-06-01T08:00:00"
        backup = {
            "appointments": [
                {
                    "id": 1, "profile": "妈妈", "title": "复诊",
                    "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
                    "created_at": created, "updated_at": created,
                },
                {
                    "id": 2, "profile": "爸爸", "title": "体检",
                    "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
                    "created_at": created, "updated_at": created,
                },
            ]
        }
        target.import_data(backup)
        mom = next(
            item for item in target.list_appointments(None)
            if item["profile"] == "妈妈"
        )
        dad = next(
            item for item in target.list_appointments(None)
            if item["profile"] == "爸爸"
        )
        assert target.delete_appointment(mom["id"])
        time.sleep(1.05)
        assert target.update_appointment_partial(dad["id"], title="复诊")

        target.import_data(backup)

        rows = target.list_appointments(None)
        assert any(
            item["profile"] == "妈妈" and item["title"] == "复诊"
            for item in rows
        )
        # 爸爸改过的标题保留，不插入旧版本
        assert sorted(
            item["title"] for item in rows if item["profile"] == "爸爸"
        ) == ["复诊"]
    finally:
        target.close()


def test_import_invalid_counts_cover_dropped_rows(store):
    result = store.import_data({
        "symptoms": [{"profile": "妈妈", "symptom": "头痛", "severity": 3}],
        "metric_targets": [
            {"profile": "妈妈", "metric": "nope", "value1": 1},
        ],
        "profiles": [{"name": "爸爸", "context": "not-a-dict"}],
    })
    assert result["symptoms"] == 0
    assert result["targets"] == 0
    assert result["profiles"] == 0
    assert result["invalid"] >= 3


# ---------------------------------------------------------------- 复诊
def test_completed_followup_restore_rejects_duplicate(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_a = first["appointment"]
    store.update_appointment_partial(appointment_a["id"], status="completed")
    _schedule_followup_sync(store, visit["id"])

    with pytest.raises(ValueError):
        store.update_appointment_partial(
            appointment_a["id"], status="upcoming"
        )
    upcoming = store.list_appointments(None, "upcoming")
    assert len(upcoming) == 1


def test_completed_followup_restore_relinks_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment = first["appointment"]
    store.update_appointment_partial(appointment["id"], status="completed")
    assert store.list_reminders(False, "妈妈") == []

    store.update_appointment_partial(appointment["id"], status="upcoming")

    pending = store.list_reminders(False, "妈妈")
    assert len(pending) == 1
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_appointment_id"] == appointment["id"]
    assert refreshed["followup_reminder_id"] == pending[0]["id"]


def test_schedule_followup_recreates_completed_reminder(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    assert store.complete_reminder(first["reminder"]["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], scheduled_at="2026-12-20T09:00"
    )

    # 改期本身就会重建待办提醒（第四十四轮）：安排复诊接口只需复用
    pending_after_reschedule = store.list_reminders(False, "妈妈")
    assert [
        item["due_at"] for item in pending_after_reschedule
    ] == ["2026-12-19"]

    second = _schedule_followup_sync(store, visit["id"])

    assert second["reminder"]["due_at"] == "2026-12-19"
    pending = store.list_reminders(False, "妈妈")
    assert [item["due_at"] for item in pending] == ["2026-12-19"]


def test_delete_appointment_clears_done_reminder_and_visit_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    reminder_id = first["reminder"]["id"]
    assert store.complete_reminder(reminder_id)

    assert store.delete_appointment(first["appointment"]["id"])

    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_appointment_id"] == 0
    assert refreshed["followup_reminder_id"] == 0
    done_reminder = store.get_reminder(reminder_id)
    assert done_reminder["appointment_id"] == 0
    # 重新打开已完成提醒：不再指向已删除预约，也不会与新提醒重复
    assert store.reopen_reminder(reminder_id)
    again = store.get_reminder(reminder_id)
    assert again["done"] == 0
    assert again["appointment_id"] == 0


def test_import_new_visit_does_not_link_rescheduled_appointment(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-25T09:00"
    )
    store.delete_visit_record(visit["id"])
    store.update_appointment_partial(appointment_id, status="upcoming")

    store.import_data(backup)

    restored = store.list_visit_records(None, 0, "", 10)
    assert len(restored) == 1
    assert restored[0]["followup_appointment_id"] == 0
    assert restored[0]["followup_scheduled"] is False


# ---------------------------------------------------------------- 风险/报表
def test_family_risk_not_masked_by_plain_followup(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "s1", "胸痛怎么办", "风险等级：高危，请立即就医。",
        profile="妈妈",
    )
    store.save_exchange(
        "s1", "需要注意什么", "注意休息，如胸痛加重请紧急就医。",
        profile="妈妈",
    )

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == "high"

    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
    ).json()
    assert weekly["consultations"]["risk"] == "high"


def test_family_risk_survives_new_plain_session(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "old", "胸痛怎么办", "风险等级：高危，请立即就医。",
        profile="妈妈",
    )
    old_day = (datetime.now() - timedelta(days=2)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = 'old'",
            (old_day, old_day),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 'old'",
            (old_day,),
        )
    store.save_exchange("new", "流鼻涕怎么办", "多喝水，观察即可。",
                        profile="妈妈")

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == "high"


def test_stale_metric_not_marked_over_target(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    old_day = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "weight", "value1": 70,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 80,
        "measured_at": f"{old_day}T08:00",
    })

    overview = client.get("/api/family/overview").json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    weight = next(
        item for item in row["metrics"] if item["metric"] == "weight"
    )
    assert weight["stale"] is True
    assert weight["over_target"] is None

    csv_response = client.get("/api/family/overview/export.csv")
    mom_line = next(
        line for line in csv_response.text.splitlines()
        if line.startswith("妈妈")
    )
    assert "超标" not in mom_line


# ---------------------------------------------------------------- 代理/安全
def test_proxy_address_source_defaults_to_fail_closed(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    request = FakeRequest({"x-forwarded-for": "127.0.0.1"})
    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER", raising=False)
    assert api_server._forwarded_client_address(request) == ""
    assert api_server._has_forwarded_address_header(request) is False
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    assert api_server._forwarded_client_address(request) == "127.0.0.1"
    assert api_server._has_forwarded_address_header(request) is True
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "none")
    assert api_server._has_forwarded_address_header(request) is False
    unknown = FakeRequest({"x-forwarded-for": "unknown"})
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    # 声明了地址头但值不可解析：仍算「有地址头」，按 fail closed
    # 继续预锁定，而不是退回免限流的共享桶
    assert api_server._has_forwarded_address_header(unknown) is True


def test_forwarded_header_skips_loopback_trust_hop(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
    request = FakeRequest({
        "forwarded": "for=203.0.113.9, for=127.0.0.1",
    })
    assert api_server._forwarded_client_address(request) == "203.0.113.9"
    assert api_server._is_loopback_client(request) is False
    # 全部是本机信任跳时保留本机
    local_only = FakeRequest({"forwarded": "for=127.0.0.1"})
    assert api_server._forwarded_client_address(local_only) == "127.0.0.1"


def test_host_header_testserver_only_for_testclient(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    try:
        client = TestClient(
            api_server.app,
            base_url="http://testserver",
            client=("127.0.0.1", 50000),
        )
        response = client.get("/api/status")
        assert response.status_code == 421
        assert response.json()["code"] == "host_rejected"

        # Starlette TestClient 自身（对端 testclient）仍放行 testserver
        assert TestClient(api_server.app).get("/api/status").status_code == 200
    finally:
        session_store.close()


# ---------------------------------------------------------------- 长期记忆
def test_erase_long_term_cleans_legacy_namespaces(monkeypatch):
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
    # 旧版可读命名空间清理需显式开启（默认不发送明文姓名）
    monkeypatch.setenv("MEDIX_MEM0_LEGACY_CLEANUP", "1")
    assert api_server._erase_long_term_entries([("妈妈", "s1")])

    for legacy in ("medix_user", "medix_local", "medix_profile_妈妈"):
        assert ("summary", "s1", legacy) in calls
    assert ("summary", "s1", api_server._memory_scope("妈妈", "s1")) in calls


# ---------------------------------------------------------------- 前端
def test_session_deleted_restores_composer():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("} else if (err.sessionDeleted) {")
    end = app_js.index("} else {", start)
    block = app_js[start:end]
    assert "restoreComposer(question);" in block


def test_detach_marks_pending_reset_before_await():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js.index("async function detachSessionForProfileChange")
    end = app_js.index("function persistSessionId", start)
    block = app_js[start:end]
    pending_pos = block.index("setPendingSessionReset(true);")
    await_pos = block.index("await startNewSession();")
    assert pending_pos < await_pos


def test_draft_is_scoped_to_session():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "DRAFT_OWNER_KEY" in app_js
    assert "settleDraftForSession(activeSessionId);" in app_js
    load_start = app_js.index("function loadDraft()")
    load_end = app_js.index("/* ---------- 语音输入", load_start)
    assert "owner !== (sessionId || '')" in app_js[load_start:load_end]
