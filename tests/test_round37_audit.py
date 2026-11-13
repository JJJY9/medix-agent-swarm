"""第三十七轮审查：抗凝文本识别、A-not-A 否定、科普变体、停药时间、
导入回归（软字段/无 updated_at 表/主键空洞/已完成复诊链接）、复诊改期、
家庭提示按成员目标、风险窗口、CSV 风险、代理 append 语义、共享限流桶、
记忆清理日志脱敏、uvicorn 代理头与前端深链/竞态。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from loguru import logger

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


# ---------------------------------------------------------------- triage
def test_anticoagulant_in_message_triggers_head_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在吃华法林，今天撞到头了",
        "服用利伐沙班期间头部被撞",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "emergency", text
        assert "抗凝治疗期间头部外伤" in result["matches"]

    # 否定/停药/家族史仍然不能触发
    for text in (
        "没有吃华法林，今天撞到头了",
        "华法林已经停用两个月了，今天撞到头了",
        "我父亲在吃华法林，今天撞到头了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" not in result["matches"], text


def test_uncertain_and_double_negation_are_positive():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "不知道是不是胸痛，就是胸口闷得慌",
        "不确定是不是胸痛",
        "有没有胸痛",
        "是不是胸痛",
        "不是没有胸痛，疼得厉害",
        "并非没有胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 普通否定与枚举否定不受影响
    for text in ("没有胸痛", "没有胸痛，没有呼吸困难", "无发热、咳嗽、胸痛"):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_educational_definition_variants_are_filtered():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我想知道什么是咯血",
        "请问什么是咯血",
        "我听说咯血是肺癌的症状",
        "咯血是什么病呢",
        "什么是咯血？",
        "咯血是什么病",
    ):
        assert patient_complaint_text(text) == "", text

    # 带病程的提问仍是真实主诉；「请问胸痛怎么办」保留症状
    assert "胸痛" in patient_complaint_text("我想知道胸痛两天了是什么病")
    assert patient_complaint_text("请问胸痛怎么办")
    assert detect_red_flags(
        patient_complaint_text("请问胸痛怎么办")
    )["level"] == "emergency"


def test_stopped_use_time_expression_variants():
    from core.medication_safety import check_medication_safety

    for question in (
        "华法林半年前停了，能吃布洛芬吗",
        "华法林前几天停了，能吃布洛芬吗",
        "华法林两个月前就停了，能吃布洛芬吗",
        "华法林几个月前停了，能吃布洛芬吗",
        "华法林一年多前停了，能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") != "warning", question

    for question in (
        "华法林吃了半年，现在准备停药，能同时吃布洛芬吗",
        "华法林不能停药，能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") == "warning", question


# ---------------------------------------------------------------- 导入回归
def test_import_cross_second_rename_not_duplicated(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        medication = source.add_medication("妈妈", "氨氯地平", dose="5mg")
        backup = source.export_data()
        assert source.update_medication(
            medication["id"], name="苯磺酸氨氯地平"
        )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET updated_at = '2026-09-09T09:09:09'"
                " WHERE id = ?",
                (medication["id"],),
            )

        result = source.import_data(backup)

        # 软字段被本地改名（跨秒）后恢复旧备份：认领唯一存活行，
        # 不再插入旧名称版本
        assert result["medications"] == 0
        names = sorted(
            item["name"] for item in source.list_medications(limit=100)
        )
        assert names == ["苯磺酸氨氯地平"]
    finally:
        source.close()


def test_import_metrics_moved_after_id_drift_not_duplicated(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        source.save_profile("爸爸", {})
        metric = source.add_metric(
            "妈妈", "weight", 63, measured_at="2026-10-01T08:00"
        )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE health_metrics SET created_at = ? WHERE id = ?",
                ("2026-06-01T08:00:00", metric["id"]),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        # 占住主键 1，使备份读数导入后主键漂移
        target.add_metric(
            "爸爸", "weight", 55, measured_at="2025-12-01T08:00"
        )
        assert target.import_data(backup)["metrics"] == 1
        moved = next(
            item for item in target.list_metrics("妈妈", "weight", 100, 0)
            if float(item["value1"]) == 63
        )
        assert target.update_metric(moved["id"], profile="爸爸")

        result = target.import_data(backup)

        # health_metrics 没有 updated_at 列：合法改挂仍要按漂移候选认领
        assert result["metrics"] == 0
        assert sum(
            float(item["value1"]) == 63
            for item in target.list_metrics(None, None, 100, 0)
        ) == 1
    finally:
        target.close()


def test_import_completed_followup_link_preserved(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-10-15",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-10-15T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-10-14", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    store.update_appointment_partial(appointment["id"], status="completed")

    store.import_data(store.export_data())

    record = store.get_visit_record(visit["id"])
    # 已完成预约是就诊历史：导入不能清链接，否则已按时复诊重新报逾期
    assert record["followup_scheduled"] is True
    assert record["followup_appointment_id"] == appointment["id"]


def test_import_cancelled_followup_link_still_cleaned(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-10-15",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-10-15T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-10-14", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
            (appointment["id"],),
        )

    store.import_data({"medications": []})

    record = store.get_visit_record(visit["id"])
    assert record["followup_appointment_id"] == 0
    assert store.list_reminders(False, "妈妈") == []


def test_import_metrics_key_hole_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        for value in (60, 61, 62):
            source.add_metric(
                "妈妈", "weight", value,
                measured_at=f"2026-07-0{value - 59}T08:00",
            )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE health_metrics SET created_at = ?",
                ("2026-06-01T08:00:00",),
            )
        rows = source.list_metrics("妈妈", "weight", 10, 0)
        first = min(item["id"] for item in rows)
        assert source.delete_metric(first)
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        rows = target.list_metrics("妈妈", "weight", 10, 0)
        deleted = next(item for item in rows if float(item["value1"]) == 61)
        survivor = next(item for item in rows if float(item["value1"]) == 62)
        assert target.delete_metric(deleted["id"])
        assert target.update_metric(survivor["id"], value1=162)

        result = target.import_data(backup)

        # 被删的 61 要补回；被编辑的存活行保留 162，不能被旧值 62 复制
        assert result["metrics"] == 1
        values = sorted(
            float(item["value1"])
            for item in target.list_metrics("妈妈", "weight", 10, 0)
        )
        assert values == [61.0, 162.0]
    finally:
        target.close()


def test_import_medication_key_hole_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        placeholder = source.add_medication("妈妈", "占位药", dose="9g")
        source.add_medication("妈妈", "阿司匹林", dose="100mg")
        source.add_medication("妈妈", "阿司匹林", dose="50mg")
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = ?"
                " WHERE name = '阿司匹林'",
                ("2026-06-01T08:00:00",),
            )
        assert source.delete_medication(placeholder["id"])
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        rows = {
            item["dose"]: item for item in target.list_medications(limit=100)
        }
        assert target.delete_medication(rows["100mg"]["id"])
        assert target.update_medication(rows["50mg"]["id"], dose="75mg")

        result = target.import_data(backup)

        # 同秒同名不同剂量、主键有空洞：被删的 100mg 必须补回，
        # 本地编辑的 75mg 保留；最后的备份行认领存活行，不再复制旧 50mg
        doses = {
            item["dose"] for item in target.list_medications(limit=100)
        }
        assert result["medications"] == 1
        assert doses == {"100mg", "75mg"}
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_same_date_reschedule_keeps_appointment(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-04",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-04T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-12-03", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    store.update_appointment_partial(
        appointment["id"], scheduled_at="2026-12-09T14:30",
        doctor="李医生",
    )

    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-09"
    )

    record = store.get_visit_record(visit["id"])
    kept = store.get_appointment(appointment["id"])
    # 用户先手工改期、再把就诊复诊日期改成同一天：预约与手工修改保留
    assert kept is not None
    assert kept["scheduled_at"] == "2026-12-09T14:30"
    assert kept["doctor"] == "李医生"
    assert record["followup_scheduled"] is True
    assert record["followup_appointment_id"] == appointment["id"]


def test_completed_appointment_not_reused_by_schedule_followup(store):
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

    again = _schedule_followup_sync(store, visit["id"])

    # 已完成的预约不能被复用并挂上新的待办提醒
    assert again["appointment_created"] is True
    assert again["appointment"]["id"] != first["appointment"]["id"]
    assert again["appointment"]["status"] == "upcoming"
    upcoming = [
        item for item in store.list_appointments(limit=100)
        if item["status"] == "upcoming"
    ]
    assert len(upcoming) == 1
    assert all(
        item["appointment_id"] != first["appointment"]["id"]
        for item in store.list_reminders(False, "妈妈")
    )


def test_restore_cancelled_appointment_after_date_change(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-11-19",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment = first["appointment"]
    store.update_appointment_partial(appointment["id"], status="cancelled")
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-09"
    )

    store.update_appointment_partial(appointment["id"], status="upcoming")

    record = store.get_visit_record(visit["id"])
    # 取消期间改过复诊日期：恢复旧日期的预约不能把链接挂回去
    assert record["followup_scheduled"] is False
    assert record["followup_appointment_id"] == 0

    again = _schedule_followup_sync(store, visit["id"])
    assert again["appointment_created"] is True
    assert again["appointment"]["scheduled_at"][:10] == "2026-12-09"


# ---------------------------------------------------------------- 家庭提示
def test_family_insights_per_member_targets_and_glucose(client_and_store):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_profile("妈妈", {"sex": "女"})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "glucose", "value1": 7,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose", "value1": 12.0,
        "context": "fasting", "measured_at": f"{today}T08:00",
    })

    data = client.get("/api/health/insights", params={"days": 30}).json()
    keys = [item["key"] for item in data["insights"]]

    # 家庭范围不能因为“跨成员不可合并”就整体给出 all_clear
    assert "all_clear" not in keys
    assert "metric_glucose" in keys
    assert "metric_glucose_fasting" in keys
    assert all(
        "妈妈" in item["title"] for item in data["insights"]
        if item["key"] in ("metric_glucose", "metric_glucose_fasting")
    )


def test_family_insights_risk_respects_window(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "recent-risk", "最近头晕",
        "综合风险等级：高危，建议尽快就医。", profile="妈妈",
    )

    recent = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()
    assert any(
        item["key"] == "consultation_risk" for item in recent["insights"]
    )

    old_day = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (f"{old_day}T08:00:00", f"{old_day}T08:00:00", "recent-risk"),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (f"{old_day}T08:00:00", "recent-risk"),
        )

    stale = client.get("/api/health/insights", params={"days": 30}).json()
    assert not any(
        item["key"] == "consultation_risk" for item in stale["insights"]
    )


def test_family_csv_risk_label_requires_window_consultation(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange(
        "old-risk", "最近头晕",
        "综合风险等级：高危，建议尽快就医。", profile="妈妈",
    )
    old_day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (f"{old_day}T08:00:00", f"{old_day}T08:00:00", "old-risk"),
        )

    response = client.get(
        "/api/family/overview/export.csv", params={"days": 7}
    )

    line = next(
        row for row in response.text.splitlines() if row.startswith("妈妈")
    )
    assert "近 7 天 0 次" in line
    assert "高危" not in line


# ---------------------------------------------------------------- 代理/限流
def _fake_request(headers):
    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, header_map):
            self.headers = header_map
            self.client = FakeClient()

    return FakeRequest(headers)


def test_forwarded_for_append_proxy_keeps_rightmost():
    import api_server

    # append 型代理在末尾追加真实地址：客户端自带的更左项不能覆盖
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "198.51.100.7, 203.0.113.9",
    })) == "203.0.113.9"
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "198.51.100.7, 203.0.113.9, 127.0.0.1",
    })) == "203.0.113.9"
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "203.0.113.9, unknown",
    })) == ""


def test_shared_bucket_with_address_header_fails_closed(
    client_and_store, monkeypatch
):
    from core.ratelimit import SlidingWindowLimiter

    import api_server

    client, _store = client_and_store
    token = "a-secure-random-token-123"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    monkeypatch.setattr(
        api_server, "_is_loopback_client", lambda request: False
    )

    wrong = client.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": "bad-guess",
            "X-Real-IP": "198.51.100.10",
        },
    )
    assert wrong.status_code == 401

    # 未信任代理头时计数键仍是共享的对端地址：正确令牌也被预锁定
    # （fail closed 到限流，而不是免限流）
    other = client.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": token,
            "X-Real-IP": "198.51.100.20",
        },
    )
    assert other.status_code == 429

    # 显式信任代理头后按转发的真实地址分桶，另一个来源不受影响
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    blocked = client.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": "bad-guess",
            "X-Real-IP": "198.51.100.30",
        },
    )
    assert blocked.status_code == 401
    distinct = client.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": token,
            "X-Real-IP": "198.51.100.40",
        },
    )
    assert distinct.status_code == 200


def test_uvicorn_proxy_headers_disabled():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "proxy_headers=False" in api_source
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "--no-proxy-headers" in readme
    assert "FORWARDED_ALLOW_IPS" in readme


def test_cleanup_log_hashes_profile(monkeypatch):
    import api_server
    import memory

    class FakeStore:
        def list_memory_cleanup(self, limit):
            return [{
                "id": 1, "profile": "张三", "session_id": "s1",
                "created_at": "2026-01-01T00:00:00",
            }]

        def complete_memory_cleanup(self, entries):
            return None

        def fail_memory_cleanup(self, row_id, error):
            return None

    monkeypatch.setattr(
        memory, "get_session_store", lambda: FakeStore()
    )
    monkeypatch.setattr(
        api_server, "_cleanup_target_recreated",
        lambda *args: True,
    )
    monkeypatch.setattr(
        api_server, "_erase_long_term_before", lambda *args: True
    )
    messages = []
    sink = logger.add(
        lambda message: messages.append(str(message)), level="INFO"
    )
    try:
        api_server._process_memory_cleanup_queue()
    finally:
        logger.remove(sink)

    joined = "\n".join(messages)
    assert "记忆清理目标已重建" in joined
    assert "张三" not in joined
    assert "profile_hash=" in joined
    assert "session_length=2" in joined


# ---------------------------------------------------------------- 前端
def test_app_deep_link_clears_stale_pending_reset():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    assert "if (deepLinkedSession && sessionId) {" in app_js
    assert "pendingSessionReset = false;" in app_js
    assert "safeStorageRemove(pendingResetStorageKey(sessionId));" in app_js
    # 深链等同显式打开历史会话，残留的待重置标记必须失效
    block = app_js[
        app_js.index("if (deepLinkedSession && sessionId) {"):
        app_js.index("if (deepLinkedSession && sessionId) {") + 500
    ]
    assert "safeStorageRemove(pendingResetStorageKey(sessionId));" in block


def test_app_deep_link_consumed_after_success():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("function scheduleAbortReconcile")
    ]

    assert "const useDeepLinkContext = deepLinkedSession && !contextUserEdited;" in restore
    assert "const deepLinkDraftConflict = deepLinkedSession && contextUserEdited;" in restore
    assert "renderStoredMessages(" in restore
    # 首次 GET 失败不能消费深链标记，成功渲染后才消费
    assert restore.index("renderStoredMessages(") < restore.index(
        "if (deepLinkedSession) consumeDeepLink();"
    )
    assert "contextUserEdited = true;" in app_js
    # 未消费的深链持久化到 sessionStorage，刷新后仍按深链处理
    assert "DEEP_LINK_PENDING_KEY" in app_js
    assert "pendingDeepLink === initialSessionFromUrl" in app_js


def test_app_restore_race_guarded_by_open_session_seq():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("function scheduleAbortReconcile")
    ]

    assert "const restoreOpenSeq = openSessionSeq;" in restore
    assert "if (restoreOpenSeq !== openSessionSeq) return false;" in restore
