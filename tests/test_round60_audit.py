"""第六十轮审查：化疗结束边界、孕期出血定义问句、孪生尾字段、提
醒补插计数按档案隔离、否定后缀入口、又吃了续用、同秒提醒多候选、
取消预约提醒复活、就诊改档案迁移、uvicorn 代理头 fail-closed、日均
指标口径、草稿归属持久化、风险谓词填充词、令牌限流按设备、停药漏服、
未指定档案文案、疫苗卡片。"""
import os
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
        "MEDIX_ALLOW_INSECURE_LAN", "MEDIX_RATE_LIMIT",
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
def test_chemo_ended_boundary_and_clause_strip():
    from core.triage import detect_red_flags, patient_complaint_text

    for history in (
        "3个月前开始化疗",
        "乳腺癌，3个月前开始化疗",
        "化疗结束才一周",
        "化疗结束后一周",
        "化疗结束后一个月",
    ):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert "免疫功能低下伴发热" in result["matches"], history
    for history in (
        "化疗结束5年了",
        "五年前化疗过",
        "化疗已经结束了",
        "化疗结束后5年",
    ):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert result["level"] == "none", history
    # 同分句的其它当前状态不能被结束短语一起吞掉
    for history, text, label in (
        ("化疗结束5年了每天吃华法林", "头撞到门框", "抗凝治疗期间头部外伤"),
        ("五年前化疗过怀孕了", "阴道出血", "孕产期出血"),
        (
            "五年前化疗过长期使用激素", "今天发烧38.5度",
            "免疫功能低下伴发热",
        ),
    ):
        result = detect_red_flags(text, {"history": history})
        assert label in result["matches"], history


def test_pregnancy_bleeding_definition_and_caesarean():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕期出血是什么病",
        "怀孕出血是什么病",
        "产后出血是什么病",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    for text in (
        "剖宫产术后10天，阴道出血",
        "剖腹产术后一周，阴道流血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    result = detect_red_flags(
        patient_complaint_text("阴道出血"), {"history": "剖宫产术后10天"}
    )
    assert "孕产期出血" in result["matches"]


def test_negated_suffix_reaches_fever_and_bleeding_entry_points():
    from core.triage import detect_red_flags, patient_complaint_text

    cases = (
        ("无咳嗽、发热3天", {"history": "正在化疗"}, "免疫功能低下伴发热"),
        ("无乏力、发热持续3天", {"age": "2个月"}, "3 个月以下婴儿发热"),
        (
            "无发热、腹痛、阴道出血2小时", {"history": "怀孕了"},
            "孕产期出血",
        ),
    )
    for text, ctx, label in cases:
        result = detect_red_flags(patient_complaint_text(text), ctx)
        assert label in result["matches"], text
    # 纯枚举否认仍是阴性
    result = detect_red_flags(
        patient_complaint_text("无咳嗽、发热"), {"history": "正在化疗"}
    )
    assert result["level"] == "none"


def test_restart_you_chi_le_forms():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "华法林停了一个月，今天又吃了，能吃布洛芬吗",
        "华法林停了一个月，今天又服用了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    result = detect_red_flags(
        patient_complaint_text("华法林停了半年，今天又吃了，头撞到门框"),
        {},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 吃饭不算续用
    result = detect_red_flags("头撞到门框", {"history": "华法林停了半年，今天又吃了饭"})
    assert result["level"] == "none"


def test_post_op_context_keeps_definition_red_flags():
    from core.triage import detect_red_flags, patient_complaint_text

    for text, label in (
        ("刚做完手术，抽搐是什么病", "抽搐"),
        ("乳腺癌术后，说话不清是什么病", "言语不清"),
        ("刚做完手术，胸口痛是什么病", "胸痛"),
        ("化疗期间，抽搐是什么病", "抽搐"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    # 寒暄仍不建立语境
    assert detect_red_flags(
        patient_complaint_text("早上好，抽搐是什么病")
    )["level"] == "none"


# ---------------------------------------------------------------- 导入
def test_twin_group_tail_attribution_restores_all(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    _freeze_clock(monkeypatch)
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        backup = {"symptoms": [
            {"id": raw_id, "profile": "妈妈", "symptom": "头痛",
             "severity": severity, "note": note,
             "recorded_at": "2026-06-01T08:00",
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"}
            for raw_id, severity, note in (
                (101, 3, "a"), (104, 5, "b"), (107, 7, "c")
            )
        ]}
        target.import_data(backup)
        rows = target.list_symptoms("妈妈", 100)
        assert sorted(item["severity"] for item in rows) == [3, 5, 7]
        middle = next(item for item in rows if item["severity"] == 5)
        assert target.delete_symptom(middle["id"]) is True
        target.import_data(backup)
        rows = target.list_symptoms("妈妈", 100)
        assert sorted(item["severity"] for item in rows) == [3, 5, 7]
        assert target.import_data(backup)["symptoms"] == 0
    finally:
        target.close()


def test_reminder_insert_scope_per_profile(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        for name in ("张三", "李四"):
            source.save_profile(name, {"sex": "男"})
        source.add_reminder("吃药", "2026-06-05T08:00", profile="张三")
        source.add_reminder("吃药", "2026-06-05T08:00", profile="张三")
        source.add_reminder(
            "复诊", "2026-06-06T08:00", note="被删的", profile="李四",
        )
        source.add_reminder(
            "复诊", "2026-06-06T08:00", note="存活的", profile="李四",
        )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE reminders SET created_at = ?, updated_at = ?",
                ("2026-06-01T08:00:00", "2026-06-01T08:00:00"),
            )
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        for name in ("张三", "李四"):
            target.save_profile(name, {"sex": "男"})
        target.add_reminder("other", "2026-06-10T08:00", profile="张三")
        target.import_data(backup)
        rows = target.list_reminders(True)
        zhang_drugs = [
            item for item in rows
            if item["profile"] == "张三" and item["title"] == "吃药"
        ]
        li_gone = next(
            item for item in rows
            if item["profile"] == "李四" and item["note"] == "被删的"
        )
        target.delete_reminder(zhang_drugs[0]["id"])
        target.delete_reminder(li_gone["id"])
        for item in rows:
            if item["profile"] == "张三" and item["title"] == "吃药" \
                    and item["id"] != zhang_drugs[0]["id"]:
                target.update_reminder(item["id"], title="吃药2")
            if item["profile"] == "李四" and item["note"] == "存活的":
                target.update_reminder(item["id"], title="复诊改")
        clock["now"] = "2026-11-15T10:00:10"
        target.import_data(backup)
        rows = target.list_reminders(True)
        assert any(
            item["profile"] == "李四" and item["note"] == "被删的"
            for item in rows
        )
        assert any(
            item["profile"] == "张三" and item["title"] == "吃药"
            for item in rows
        )
        assert target.import_data(backup)["reminders"] == 0
    finally:
        target.close()


def test_renamed_reminder_in_same_soft_group_no_duplicate(
    monkeypatch, tmp_path,
):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        backup = {"reminders": [
            {"id": raw_id, "profile": "妈妈", "title": "复查",
             "due_at": "2026-12-01T09:00", "note": note, "done": 0,
             "created_at": "2026-06-01T08:00:00",
             "updated_at": "2026-06-01T08:00:00"}
            for raw_id, note in ((101, "n1"), (104, "n2"), (107, "n3"))
        ]}
        target.import_data(backup)
        rows = target.list_reminders(True)
        first = next(item for item in rows if item["note"] == "n1")
        clock["now"] = "2026-11-15T10:00:10"
        target.update_reminder(first["id"], title="复查改")
        clock["now"] = "2026-11-15T10:00:20"
        assert target.import_data(backup)["reminders"] == 0
        rows = target.list_reminders(True)
        assert sorted(item["note"] for item in rows) == ["n1", "n2", "n3"]
        assert target.import_data(backup)["reminders"] == 0
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_cancelled_followup_reminder_not_resurrected(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("Alice", {"sex": "女"})
    visit = store.add_visit_record(
        "Alice", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-15",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    clock["now"] = "2026-11-15T10:00:10"
    restored = store.import_data(backup)
    assert restored["reminders"] == 0
    assert store.list_reminders(False, "Alice") == []
    clock["now"] = "2026-11-15T10:00:20"
    second = _schedule_followup_sync(store, visit["id"])
    assert second["appointment_created"] is True
    pending = store.list_reminders(False, "Alice")
    assert len(pending) == 1
    assert int(pending[0].get("appointment_id") or 0) == int(
        second["appointment"]["id"]
    )


def test_visit_profile_change_migrates_owned_appointment(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-20",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    clock["now"] = "2026-11-15T10:00:05"
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_visit_record_partial(visit["id"], profile="爸爸")
    clock["now"] = "2026-11-15T10:00:10"
    store.update_appointment_partial(appointment_id, status="upcoming")
    reopened = store.get_appointment(appointment_id)
    # 归属就诊已改档案：恢复出的预约必须清除跨档案 owner/标记
    assert int(reopened.get("owner_visit_id") or 0) == 0
    assert "由就诊记录 #" not in str(reopened.get("note") or "")
    clock["now"] = "2026-11-15T10:00:20"
    second = _schedule_followup_sync(store, visit["id"])
    assert second["appointment_created"] is True
    upcoming_for_new = [
        item for item in store.list_appointments("爸爸", None)
        if item["status"] == "upcoming"
        and int(item.get("owner_visit_id") or 0) == visit["id"]
    ]
    assert len(upcoming_for_new) == 1


# ---------------------------------------------------------------- 安全/报表
def test_untrusted_forwarded_headers_fail_closed(monkeypatch, tmp_path):
    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r60-secret-token-123")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    monkeypatch.setattr(
        api_server, "_is_loopback_client", lambda request: False
    )
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("203.0.113.9", 57000),
        )
        first = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad-guess",
                "X-Real-IP": "1.1.1.1",
            },
        )
        assert first.status_code == 401
        # 换一个伪造地址不能换桶：未信任的转发头统一 fail-closed
        second = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad-guess",
                "X-Real-IP": "2.2.2.2",
            },
        )
        assert second.status_code == 429
    finally:
        store.close()
    source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "--no-proxy-headers" in source
    assert "MEDIX_SERVER_PROXY_HEADERS_DISABLED" in source


def test_daily_metric_summary_day_basis(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    for offset, values in ((1, (4000, 5000)), (0, (4000, 8000))):
        day = today - timedelta(days=offset)
        for value in values:
            store.add_metric(
                "妈妈", "steps", value, measured_at=f"{day}T08:00",
            )
    summary = client.get(
        "/api/metrics/summary", params={"metric": "steps", "profile": "妈妈"}
    ).json()
    assert summary["count"] == 4
    assert summary["day_count"] == 2
    assert summary["value1"]["min"] == 9000.0
    assert summary["value1"]["max"] == 12000.0
    assert float(summary["latest"]["value1"]) == 12000.0
    weekly = client.get(
        "/api/reports/weekly", params={"profile": "妈妈"}
    ).json()
    row = next(
        item for item in weekly["metrics"]["by_metric"]
        if item["metric"] == "steps"
    )
    assert row["count"] == 4
    assert row["day_count"] == 2
    assert row["avg_basis"] == "day"


def test_risk_predicate_filler_words():
    import api_server

    for text in (
        "目前未发现任何高危表现。",
        "未发现明显高危征象。",
        "未达到任何高危标准。",
    ):
        assert api_server.parse_risk_level(text) == "", text
    for text in (
        "不符合低危标准属于高危",
        "未达到极高危但属于高危",
    ):
        assert api_server.parse_risk_level(text) == "high", text


def test_token_rate_limit_per_client_address(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "family-token")
    monkeypatch.setenv("MEDIX_RATE_LIMIT", "2")
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    first_client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("192.168.1.10", 50000),
    )
    second_client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("192.168.1.11", 50000),
    )
    try:
        for _ in range(2):
            response = first_client.post(
                "/api/chat", json={},
                headers={"X-Medix-Token": "family-token"},
            )
            assert response.status_code == 422
        other = second_client.post(
            "/api/chat", json={},
            headers={"X-Medix-Token": "family-token"},
        )
        # 同一令牌的另一台设备有自己的额度，不被前一设备耗尽
        assert other.status_code == 422
    finally:
        store.close()


def test_stopped_medication_keeps_weekly_skip(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("爸爸", {"sex": "男"})
    medication = store.add_medication(
        "爸爸", "阿托伐他汀", "20mg", "每日一次",
        started_at=str(today - timedelta(days=30)),
    )
    for offset in (2, 3, 4):
        store.add_medication_log(
            medication["id"],
            taken_at=f"{today - timedelta(days=offset)}T12:00",
            skipped=True, skip_reason="forget",
        )
    store.update_medication(medication["id"], active=False)
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in data["profiles"] if item["name"] == "爸爸"
    )
    assert member["medications"]["skipped_week"] == 3
    assert member["attention"]["level"] == "warning"
    csv_text = client.get(
        "/api/family/overview/export.csv", params={"days": 7}
    ).text
    assert "近 7 天未服 3 次" in csv_text


def test_unassigned_insights_skip_detail_not_family(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    medication = store.add_medication(
        "", "阿司匹林", "100mg", "每日一次",
    )
    store.add_medication_log(
        medication["id"], taken_at=f"{today - timedelta(days=1)}T09:00",
        skipped=True, skip_reason="forget",
    )
    insights = client.get(
        "/api/health/insights", params={"profile": "", "days": 30}
    ).json()
    skips = next(
        item for item in insights["insights"]
        if item["key"] == "medication_skips"
    )
    assert "涉及" not in skips["detail"]
    # 家庭视图仍标注成员
    family = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()
    family_skips = next(
        item for item in family["insights"]
        if item["key"] == "medication_skips"
    )
    assert "涉及" in family_skips["detail"]


# ---------------------------------------------------------------- 前端
def test_frontend_round60_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 草稿档案归属用编辑时快照持久化，翻页/防抖不改写
    assert "let draftProfileMemory = null;" in app_js
    assert "function draftProfileSnapshot()" in app_js
    flush = app_js[
        app_js.index("function flushDraftNow()"):
        app_js.index("window.addEventListener('pagehide'")
    ]
    assert "draftProfileSnapshot()" in flush
    assert "activeProfileName || readStoredProfile()" not in flush
    save = app_js[
        app_js.index("function saveDraft()"):
        app_js.index("function flushDraftNow()")
    ]
    assert "draftProfileSnapshot()" in save
    # 删除档案写哨兵值，不直接移除归属
    assert "const DELETED_DRAFT_PROFILE = '已删除档案';" in app_js
    assert "const replacement = newName || DELETED_DRAFT_PROFILE;" in app_js
    # 家庭卡片渲染疫苗（第六十一轮改为历史累计口径 + 最近接种日期）
    assert "疫苗 累计" in app_js
    assert "latestVaccineDate" in app_js
    # 日均口径
    assert "row.avg_basis === 'day'" in app_js
    assert "最近一日合计" in app_js
