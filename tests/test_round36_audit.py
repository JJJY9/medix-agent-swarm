"""第三十六轮审查：病程定义问句、孕周边界、反语序头部外伤、
停药意图、导入配对缓存/跨档案漂移、复诊链接恢复、代理头 fail closed、
共享限流桶、家庭总览未指定档案与趋势。

所有断言都对应真实修复过的缺陷，防止后续回归。
"""
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


# ---------------------------------------------------------------- triage
def test_definition_question_with_course_marker_keeps_symptom():
    from core.triage import detect_red_flags, patient_complaint_text

    # 「两天了是什么病」是在描述病程，不是纯定义问句
    kept = patient_complaint_text("胸痛两天了是什么病")
    assert kept and "胸痛" in kept
    assert detect_red_flags(kept)["level"] == "emergency"

    # 中文数字病程同样保留；「是什么原因」只去掉疑问尾巴
    kept = patient_complaint_text("持续发烧三天是什么原因")
    assert kept and "发烧" in kept
    kept = patient_complaint_text("最近头晕是怎么回事")
    assert kept and "头晕" in kept

    # 纯定义问句仍然过滤，不能凭空制造急症主诉
    for text in ("咯血是什么病", "什么是胸痛？", "请解释一下呼吸困难"):
        assert patient_complaint_text(text) == "", text


def test_pregnancy_week_boundaries_and_negation():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in ("孕12周阴道出血", "怀孕20周，阴道出血"):
        level = detect_red_flags(patient_complaint_text(text))["level"]
        assert level == "emergency", text

    # 避孕/备孕/否认怀孕都不是妊娠状态；牙龈出血不算孕产期出血
    for text in (
        "避孕药吃完后阴道出血",
        "备孕12周了，阴道出血",
        "没有怀孕，现在阴道出血",
        "已经怀孕了，牙龈出血",
    ):
        level = detect_red_flags(patient_complaint_text(text))["level"]
        assert level != "emergency", text


def test_reversed_head_trauma_word_orders():
    from core.triage import detect_red_flags

    context = {"medications": "华法林"}
    for text in (
        "老人吃华法林，昨天撞了一下头",
        "吃华法林期间头上撞了个包",
        "华法林期间脑袋磕了一下",
    ):
        result = detect_red_flags(text, context)
        assert result["level"] == "emergency", text
        assert "抗凝治疗期间头部外伤" in result["matches"]

    # 单纯跌倒没有撞到头，不能按头部外伤处理
    assert detect_red_flags(
        "老人吃华法林，昨天摔了一跤", context
    )["level"] == "none"


def test_medication_stop_intent_and_continuous_use():
    from core.medication_safety import check_medication_safety

    for question in (
        "华法林吃了半年，现在准备停药，能同时吃布洛芬吗",
        "华法林可以停药吗？最近想吃布洛芬",
        "过去半年每天吃华法林，能吃布洛芬吗",
        "华法林不能停药，能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") == "warning", question

    for question in (
        "华法林已经停用两个月了，能吃布洛芬吗",
        "我以前吃华法林，现在能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") != "warning", question


# ---------------------------------------------------------------- 导入配对
def test_import_restores_two_deleted_same_second_reminders(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "reminders.db"))
    try:
        session_store.save_profile("妈妈", {})
        first = session_store.add_reminder(
            "吃药A", "2026-12-01", "", "", "妈妈"
        )
        second = session_store.add_reminder(
            "吃药B", "2026-12-02", "", "", "妈妈"
        )
        same_second = "2026-06-01T08:00:00"
        with session_store._lock, session_store._conn:
            session_store._conn.execute(
                "UPDATE reminders SET created_at = ? WHERE id IN (?, ?)",
                (same_second, first["id"], second["id"]),
            )
        backup = session_store.export_data()
        assert session_store.delete_reminder(first["id"])
        assert session_store.delete_reminder(second["id"])

        result = session_store.import_data(backup)

        # 同身份（档案 + 同秒创建）组里两条被删记录都要补回：
        # 空候选必须缓存，否则第二条会被本次插入的同秒记录“认领”而丢失
        assert result["reminders"] == 2
        assert len(session_store.list_reminders(False, "妈妈")) == 2

        again = session_store.import_data(backup)
        assert again["reminders"] == 0
        assert len(session_store.list_reminders(False, "妈妈")) == 2
    finally:
        session_store.close()


def test_import_reuses_drifted_edited_reminder(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        reminder = source.add_reminder(
            "复查血压", "2026-12-01", "", "", "妈妈"
        )
        same_second = "2026-06-01T08:00:00"
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE reminders SET created_at = ? WHERE id = ?",
                (same_second, reminder["id"]),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.add_reminder("别的提醒", "2026-12-05", "", "", "妈妈")
        survivor = target.add_reminder(
            "复查血压", "2026-12-01", "", "", "妈妈"
        )
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE reminders SET created_at = ? WHERE id = ?",
                (same_second, survivor["id"]),
            )
            target._conn.execute(
                "UPDATE reminders SET title = ?,"
                " updated_at = '2026-09-09T09:09:09' WHERE id = ?",
                ("复查血压（已改）", survivor["id"]),
            )

        result = target.import_data(backup)

        # 主键漂移 + 本地改过标题：按创建时间/到期日认出存活行，保留编辑
        assert result["reminders"] == 0
        titles = sorted(
            item["title"] for item in target.list_reminders(True, "妈妈")
        )
        assert titles == ["别的提醒", "复查血压（已改）"]
    finally:
        target.close()


def test_import_drift_cross_profile_candidate_needs_edit_evidence(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        medication = source.add_medication("妈妈", "药A", dose="1g")
        same_second = "2026-06-01T08:00:00"
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = ? WHERE id = ?",
                (same_second, medication["id"]),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        # 主键 1 先被占位记录占用，药A 落到 id=2：导入时主键必然漂移，
        # 只能走「创建时间 + 内容」的漂移候选分支
        target.add_medication("爸爸", "占位药", dose="9g")
        other = target.add_medication("爸爸", "药A", dose="1g")
        same_second = "2026-06-01T08:00:00"
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE medications SET created_at = ?,"
                " updated_at = ? WHERE id = ?",
                (same_second, same_second, other["id"]),
            )

        result = target.import_data(backup)

        # 另一位成员同秒同内容的未编辑记录不能被当成“已改挂”吞掉
        assert result["medications"] == 1
        mom_names = sorted(
            item["name"]
            for item in target.list_medications(limit=100)
            if item["profile"] == "妈妈"
        )
        assert mom_names == ["药A"]
        dad_names = sorted(
            item["name"]
            for item in target.list_medications(limit=100)
            if item["profile"] == "爸爸"
        )
        assert dad_names == ["占位药", "药A"]
    finally:
        target.close()


def test_import_drift_cross_profile_edited_row_not_duplicated(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        medication = source.add_medication("妈妈", "药A", dose="1g")
        same_second = "2026-06-01T08:00:00"
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = ? WHERE id = ?",
                (same_second, medication["id"]),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        target.add_medication("爸爸", "占位药", dose="9g")
        moved = target.add_medication("爸爸", "药A", dose="1g")
        same_second = "2026-06-01T08:00:00"
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE medications SET created_at = ?,"
                " updated_at = '2026-09-09T09:09:09' WHERE id = ?",
                (same_second, moved["id"]),
            )

        result = target.import_data(backup)

        # 本地确实改挂/编辑过的行仍要按漂移候选认出，不能复制旧备份
        assert result["medications"] == 0
        assert sum(
            item["name"] == "药A"
            for item in target.list_medications(limit=100)
        ) == 1
    finally:
        target.close()


def test_import_cleanup_unlinks_cancelled_appointment(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("爸爸", {})
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        visit = target.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-11-10",
        )
        appointment = target.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-11-10T09:00",
            note=f"由就诊记录 #{visit['id']} 自动创建",
        )
        reminder = target.add_reminder(
            "复诊：高血压", "2026-11-09", "", "", "妈妈",
            0, 0, "", 0, 0, appointment["id"],
        )
        target.set_visit_followup_links(
            visit["id"], appointment["id"], reminder["id"]
        )
        # 模拟历史版本留下的坏数据：预约已取消，提醒与复诊链接还在
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE appointments SET status = 'cancelled' WHERE id = ?",
                (appointment["id"],),
            )

        target.import_data(backup)

        record = target.get_visit_record(visit["id"])
        assert record["followup_appointment_id"] == 0
        assert record["followup_reminder_id"] == 0
        pending = target.list_reminders(False, "妈妈")
        assert pending == []
    finally:
        target.close()


def test_import_rewrites_followup_note_on_visit_id_drift(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-11-20",
        )
        appointment = source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-11-20T09:00",
            status="upcoming",
            note=f"由就诊记录 #{visit['id']} 自动创建",
        )
        reminder = source.add_reminder(
            "复诊：高血压", "2026-11-19", "", "", "妈妈",
            0, 0, "", 0, 0, appointment["id"],
        )
        source.set_visit_followup_links(
            visit["id"], appointment["id"], reminder["id"]
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        unrelated = target.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="感冒",
        )
        with target._lock, target._conn:
            target._conn.execute(
                "UPDATE visit_records SET created_at = '2020-01-01T00:00:00'"
                " WHERE id = ?",
                (unrelated["id"],),
            )

        result = target.import_data(backup)

        assert result["visit_records"] == 1
        restored = next(
            item for item in target.list_visit_records()
            if item["diagnosis"] == "高血压"
        )
        # 主键被无关就诊占用，导入后 id 漂移：自动预约的归属标记
        # 必须改写为新 id，否则改期/删除会漏掉这条预约
        assert restored["id"] != visit["id"]
        appointment = target.get_appointment(
            restored["followup_appointment_id"]
        )
        assert appointment is not None
        assert (
            f"由就诊记录 #{restored['id']} 自动创建"
            in appointment["note"]
        )

        target.update_visit_record_partial(
            restored["id"], next_followup_date="2026-12-20"
        )
        assert target.get_appointment(appointment["id"]) is None
        record = target.get_visit_record(restored["id"])
        assert record["followup_appointment_id"] == 0
    finally:
        target.close()


# ---------------------------------------------------------------- 预约恢复
def test_cancel_then_restore_appointment_relinks_visit(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-11-10",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-11-10T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊：高血压", "2026-11-09", "", "", "妈妈",
        0, 0, "", 0, 0, appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )

    store.update_appointment_partial(appointment["id"], status="cancelled")
    cancelled = store.get_visit_record(visit["id"])
    assert cancelled["followup_scheduled"] is False
    assert cancelled["followup_appointment_id"] == 0

    store.update_appointment_partial(appointment["id"], status="upcoming")

    restored = store.get_visit_record(visit["id"])
    assert restored["followup_scheduled"] is True
    assert restored["followup_appointment_id"] == appointment["id"]
    pending = [
        item for item in store.list_reminders(False, "妈妈")
        if item.get("appointment_id") == appointment["id"]
    ]
    assert len(pending) == 1
    assert restored["followup_reminder_id"] == pending[0]["id"]


def test_schedule_followup_ownership_not_shared_between_visits(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    first_visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-11-20",
    )
    second_visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-02", diagnosis="高血压",
        next_followup_date="2026-11-20",
    )

    first = _schedule_followup_sync(store, first_visit["id"])
    second = _schedule_followup_sync(store, second_visit["id"])

    # 同诊断同日期的另一条就诊不能复用别人的自动预约：否则取消/
    # 改期其中一条会连带删掉另一条的预约
    assert second["appointment_created"] is True
    assert first["appointment"]["id"] != second["appointment"]["id"]
    upcoming = [
        item for item in store.list_appointments(limit=100)
        if item["status"] == "upcoming"
    ]
    assert len(upcoming) == 2

    retry = _schedule_followup_sync(store, first_visit["id"])
    assert retry["appointment_created"] is False
    assert retry["appointment"]["id"] == first["appointment"]["id"]


# ---------------------------------------------------------------- 家庭总览/趋势
def test_family_overview_unassigned_labs_join_medication_safety(
    client_and_store,
):
    client, store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    store.save_profile("妈妈", {"sex": "女"})
    store.add_lab_result(
        "", "血钾", 5.8, unit="mmol/L", panel="电解质",
        tested_at=f"{today}T08:00",
    )
    store.add_medication("", "培哚普利", dose="4mg")
    store.add_condition("", "哮喘")
    store.add_medication("", "普萘洛尔", dose="10mg")

    data = client.get("/api/family/overview").json()

    row = next(
        item for item in data["profiles"] if item["name"] == ""
    )
    # 未指定档案的化验与慢病也要参与用药安全核对（与 insights 一致）
    safety = row["medication_lab_safety"]
    assert safety["count"] >= 2
    assert any("血钾" in alert["title"] for alert in safety["alerts"])
    assert safety["condition_count"] >= 1
    assert any("哮喘" in alert["title"] for alert in safety["alerts"])
    # 未指定档案的当天记录要计入 recorded_today/连续天数
    assert row["recorded_today"] is True
    assert row["streak"] >= 1


def test_insights_unnamed_profile_allows_trends(client_and_store):
    client, store = client_and_store
    today = datetime.now()
    store.add_metric(
        "", "weight", 80,
        measured_at=(today - timedelta(days=10)).strftime(
            "%Y-%m-%dT08:00"
        ),
    )
    store.add_metric(
        "", "weight", 70,
        measured_at=today.strftime("%Y-%m-%dT08:00"),
    )

    unnamed = client.get(
        "/api/health/insights", params={"profile": ""}
    ).json()
    unnamed_keys = [item["key"] for item in unnamed["insights"]]
    assert "metric_weight_change" in unnamed_keys

    # 家庭范围仍然不显示跨成员趋势
    family = client.get("/api/health/insights").json()
    family_keys = [item["key"] for item in family["insights"]]
    assert "metric_weight_change" not in family_keys


# ---------------------------------------------------------------- 代理头/限流
def _fake_request(headers):
    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, header_map):
            self.headers = header_map
            self.client = FakeClient()

    return FakeRequest(headers)


def test_forwarded_headers_fail_closed_on_unparsable_last_hop():
    import api_server

    # Forwarded 只看最右元素：最右元素存在但没有可解析的 for
    # （或为 unknown）时必须视为失败，不能回退到客户端自带元素
    assert api_server._forwarded_client_address(_fake_request({
        "forwarded": "for=203.0.113.9, proto=https",
    })) == ""
    assert api_server._forwarded_client_address(_fake_request({
        "forwarded": "for=203.0.113.9, for=unknown",
    })) == ""

    # XFF 最右项无法解析同样 fail closed
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "203.0.113.9, unknown",
    })) == ""

    # 多级本机代理：从右向左跳过 loopback 信任跳
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "198.51.100.7, 203.0.113.9, 127.0.0.1",
    })) == "203.0.113.9"
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "203.0.113.9, [::1]",
    })) == "203.0.113.9"
    # 全是本机地址时保留本机
    assert api_server._forwarded_client_address(_fake_request({
        "x-forwarded-for": "127.0.0.1",
    })) == "127.0.0.1"


def test_shared_bucket_skips_auth_preblock(client_and_store, monkeypatch):
    from core.ratelimit import SlidingWindowLimiter

    import api_server

    client, _store = client_and_store
    token = "a-secure-random-token-123"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    monkeypatch.setattr(
        api_server, "_is_loopback_client", lambda request: False
    )

    wrong = client.get(
        "/api/sessions", headers={"X-Medix-Token": "bad-guess"}
    )
    assert wrong.status_code == 401

    # 代理只加了 X-Forwarded-Proto、没有真实客户端地址：所有用户共用
    # 一个计数桶，不能因为其中一人的错误尝试把全家锁死
    proxied = client.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": token,
            "X-Forwarded-Proto": "https",
        },
    )
    assert proxied.status_code == 200

    # 直连来源仍受限流保护，正确定义也不会绕过已锁定状态
    plain = client.get(
        "/api/sessions", headers={"X-Medix-Token": token}
    )
    assert plain.status_code == 429
    assert plain.json()["code"] == "auth_rate_limited"


# ---------------------------------------------------------------- 前端状态
def test_app_pending_reset_cleared_before_session_switch():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = app_js[
        app_js.index("async function startNewSession"):
        app_js.index("async function restoreCurrentSession")
    ]

    assert "setPendingSessionReset(false)" in start
    assert "sessionId = data.session_id" in start
    assert "safeStorageRemove(pendingResetStorageKey(sessionId))" in start
    # 必须先按旧会话清理待重置标记，再切换到新会话；否则旧会话的
    # medix.pendingReset.<id> 会永久残留
    assert start.index("setPendingSessionReset(false)") < start.index(
        "sessionId = data.session_id"
    )
    assert start.index("sessionId = data.session_id") < start.index(
        "safeStorageRemove(pendingResetStorageKey(sessionId))"
    )
    assert "deepLinkedSession = false;" in start


def test_app_deep_link_context_consumed_once():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("function scheduleAbortReconcile")
    ]

    # 深链上下文只在成功渲染后消费：首次失败保留，重连重试仍能回填；
    # 用户已编辑补充信息时跳过快照，避免覆盖草稿（round37 调整）
    assert "const useDeepLinkContext = deepLinkedSession && !contextUserEdited;" in restore
    assert "const deepLinkDraftConflict = deepLinkedSession && contextUserEdited;" in restore
    assert "if (deepLinkedSession) consumeDeepLink();" in restore
    assert "restoreContext: useDeepLinkContext ? true : false" in restore
    # 深链语义不能在每次对账恢复时重复生效
    assert "restoreContext: deepLinkedSession" not in restore
