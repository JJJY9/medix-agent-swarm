"""第四十二轮审查：导入同秒兄弟/第 2 步幂等/提醒跨档案、复诊清理与
completed 链接、代理 unknown 与共享桶、家庭血压分成员、陈旧每日累计、
旧版 Mem0 档案命名空间、深度研究网络搜索开关、分诊孕期/停药/定义问句、
用药既往「过」、前端深链/换档/草稿/待重置标记。"""
import asyncio
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
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


def _med(raw_id, profile, name, dose, created=""):
    item = {
        "profile": profile,
        "name": name,
        "dose": dose,
        "frequency": "每日一次",
    }
    if raw_id:
        item["id"] = raw_id
    if created:
        item["created_at"] = created
        item["updated_at"] = created
    return item


# ---------------------------------------------------------------- 导入
def test_import_same_second_sibling_does_not_swallow_edit(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        source.save_profile("爸爸", {})
        source.import_data({"medications": [
            _med(0, "妈妈", "药A", "1g"),
            _med(0, "爸爸", "药Y", "1g"),
            _med(0, "爸爸", "药Z", "1g"),
        ]})
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        target.import_data(backup)
        mom = next(
            item for item in target.list_medications(limit=20)
            if item["profile"] == "妈妈" and item["name"] == "药A"
        )
        assert target.delete_medication(mom["id"])
        dad_y = next(
            item for item in target.list_medications(limit=20)
            if item["name"] == "药Y"
        )
        time.sleep(1.05)
        assert target.update_medication(
            dad_y["id"], name="药A", dose="1g"
        )

        target.import_data(backup)

        rows = target.list_medications(limit=20)
        # 被删的妈妈药A必须补回；爸爸编辑后的药A保留
        assert any(
            item["profile"] == "妈妈" and item["name"] == "药A"
            for item in rows
        )
        assert any(
            item["profile"] == "爸爸" and item["name"] == "药A"
            for item in rows
        )
    finally:
        target.close()


def test_repeated_import_same_id_collision_is_idempotent(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.save_profile("爸爸", {})
        created = "2026-06-01T08:00:00"
        target.import_data({
            "medications": [_med(1, "爸爸", "药A", "1g", created)]
        })
        mom_backup = {
            "medications": [_med(1, "妈妈", "药A", "1g", created)]
        }

        assert target.import_data(mom_backup)["medications"] == 1
        # 第 2 步的跨档案守卫只跳过该候选，后续内容配对仍能认领
        # 上次导入的妈妈药A：重复导入必须幂等
        assert target.import_data(mom_backup)["medications"] == 0
        mom_rows = [
            item for item in target.list_medications(limit=20)
            if item["profile"] == "妈妈" and item["name"] == "药A"
        ]
        assert len(mom_rows) == 1
    finally:
        target.close()


def test_reminder_import_id_collision_across_profiles(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    for index in range(5):
        assert store.add_reminder(
            f"爸爸提醒{index}", "2026-12-01", "", "", "爸爸"
        )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = ? WHERE profile = '爸爸'",
            ("2026-06-01T08:00:00",),
        )

    result = store.import_data({"reminders": [{
        "id": 5,
        "profile": "妈妈",
        "title": "妈妈吃药",
        "due_at": "2026-12-01",
        "created_at": "2026-06-01T08:00:00",
    }]})

    assert result["reminders"] == 1
    mom = store.list_reminders(False, "妈妈")
    assert [item["title"] for item in mom] == ["妈妈吃药"]


def test_repeated_restore_keeps_preexisting_upcoming_appointment(store):
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
    store.import_data(backup)

    appointment = store.get_appointment(appointment_id)
    assert appointment is not None
    assert appointment["status"] == "upcoming"
    assert appointment["scheduled_at"][:10] == "2026-12-25"


def test_import_restores_completed_followup_link_in_fresh_store(tmp_path):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="2026-10-15",
        )
        first = _schedule_followup_sync(source, visit["id"])
        source.update_appointment_partial(
            first["appointment"]["id"], status="completed"
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)

        restored = target.list_visit_records(None, 0, "", 10)
        assert len(restored) == 1
        assert restored[0]["followup_appointment_id"] != 0
        assert restored[0]["followup_scheduled"] is True
    finally:
        target.close()


def test_import_rejected_link_does_not_claim_visit_ownership(store):
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
    new_visit_id = restored[0]["id"]
    assert restored[0]["followup_appointment_id"] == 0
    appointment = store.get_appointment(appointment_id)
    # 拒绝写链接时不能宣称归属，否则改期会静默覆盖就诊复诊日期
    assert int(appointment.get("owner_visit_id") or 0) != new_visit_id
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-30T09:00"
    )
    refreshed = store.get_visit_record(new_visit_id)
    assert refreshed["next_followup_date"][:10] == "2026-12-14"


def test_schedule_followup_rolls_back_when_visit_missing(store, monkeypatch):
    from fastapi import HTTPException

    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    monkeypatch.setattr(
        store, "set_visit_followup_links", lambda *args, **kwargs: False
    )

    with pytest.raises(HTTPException):
        _schedule_followup_sync(store, visit["id"])

    assert store.list_appointments(None) == []
    assert store.list_reminders(False, None) == []


def test_visit_writes_share_followup_lock():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    update_block = api_source[
        api_source.index("async def update_visit("):
        api_source.index("def _schedule_followup_sync")
    ]
    delete_block = api_source[
        api_source.index("async def delete_visit("):
        api_source.index('@app.get("/api/visits/export.csv")')
    ]
    assert "_VISIT_FOLLOWUP_LOCK" in update_block
    assert "_VISIT_FOLLOWUP_LOCK" in delete_block


# ---------------------------------------------------------------- 分诊/用药
def test_pregnancy_modifier_negation_and_cross_comma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕8周，阴道不规则出血",
        "怀孕12周阴道无痛性出血",
        "孕早期阴道不规则流血",
        "孕8周，出血",
        "孕8周，有点出血",
        "怀孕两个月，有点出血",
        "产后两周，出血了",
        "产妇，出血了",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    for text in (
        "孕期鼻出血",
        "怀孕期间鼻出血",
        "怀孕8周，阴道没有出血",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_recent_stop_before_verb_and_cross_clause_restart():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "刚停用华法林，今天撞到头",
        "昨天停用华法林，今天撞到头",
        "今天刚停用华法林，头磕到桌角",
        "刚停了华法林，今天撞到头",
        "华法林停了半年，最近又吃上了，今天撞到头",
        "停用华法林半年，最近恢复服用，今天头撞到门框",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text

    result = detect_red_flags(
        patient_complaint_text("华法林已经停用两个月了，今天撞到头"), {}
    )
    assert "抗凝治疗期间头部外伤" not in result["matches"]


def test_definition_suffix_with_companion_symptoms():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "胸痛伴呼吸困难是什么病",
        "发热伴意识模糊是什么病",
        "咯血伴胸痛是什么病",
        "发烧合并抽搐是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text


def test_past_medication_with_aspect_marker():
    from core.medication_safety import check_medication_safety

    for text in (
        "以前吃过华法林，现在能吃布洛芬吗",
        "以前吃过阿司匹林，现在能吃布洛芬吗",
        "之前服用过阿司匹林，现在能吃布洛芬吗",
        "十年前吃过阿司匹林，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) != "warning", text

    assert check_medication_safety(
        "正在吃华法林，能吃布洛芬吗", {}
    ).get("level") == "warning"


# ---------------------------------------------------------------- 代理/限流
def test_unknown_forwarded_address_fails_closed(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    request = FakeRequest({
        "x-forwarded-for": "unknown, 127.0.0.1",
    })
    assert api_server._forwarded_client_address(request) == ""
    assert api_server._is_loopback_client(request) is False

    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
    forwarded = FakeRequest({
        "forwarded": "for=unknown, for=127.0.0.1",
    })
    assert api_server._forwarded_client_address(forwarded) == ""
    assert api_server._is_loopback_client(forwarded) is False
    # 只有 proto 的更早元素没有 for：仍可跳过取最右本机
    proto_only = FakeRequest({
        "forwarded": "proto=https, for=127.0.0.1",
    })
    assert api_server._forwarded_client_address(proto_only) == "127.0.0.1"


def test_non_loopback_proxy_uses_shared_bucket(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    # 非 loopback 反代必须显式声明对端地址，才被视为共享代理桶
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    # Docker 网桥/跨机反代：对端不是 loopback，所有客户端共用一个桶
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("172.18.0.2", 50000),
    )
    try:
        wrong = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad", "X-Forwarded-Proto": "https",
        })
        assert wrong.status_code == 401
        correct = client.get("/api/sessions", headers={
            "X-Medix-Token": token, "X-Forwarded-Proto": "https",
        })
        # 共享桶不能被预锁定：正确令牌必须仍可用
        assert correct.status_code == 200
        second = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad2", "X-Forwarded-Proto": "https",
        })
        assert second.status_code == 429
    finally:
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_family_bp_insights_are_per_member(client_and_store):
    client, store = client_and_store
    store.save_profile("爸爸", {})
    store.save_profile("妈妈", {})
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "bp", "value1": 165, "value2": 70,
        "measured_at": f"{today}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 150, "value2": 105,
        "measured_at": f"{today}T09:00",
    })

    data = client.get("/api/health/insights", params={"days": 30}).json()
    bp_titles = [
        item["title"] for item in data["insights"]
        if item["key"] == "metric_bp_single"
    ]
    assert any("爸爸" in title and "165/70" in title
               for title in bp_titles)
    assert any("妈妈" in title and "150/105" in title
               for title in bp_titles)


def test_stale_daily_metric_not_marked_over_target(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    old_day = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "steps", "value1": 8000,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 3000,
        "measured_at": f"{old_day}T08:00",
    })

    overview = client.get("/api/family/overview").json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    steps = next(
        item for item in row["metrics"] if item["metric"] == "steps"
    )
    assert steps["stale"] is True
    assert steps["over_target"] is None

    csv_response = client.get("/api/family/overview/export.csv")
    mom_line = next(
        line for line in csv_response.text.splitlines()
        if line.startswith("妈妈")
    )
    assert "低于目标" not in mom_line


# ---------------------------------------------------------------- 长期记忆
def test_profile_delete_cleans_legacy_memory_scope(monkeypatch):
    import api_server

    calls = []

    class FakeMemory:
        enabled = True

        def delete_scope(self, scope):
            calls.append(("scope", scope))

        def delete_scope_before(self, scope, cutoff):
            calls.append(("scope_before", scope, cutoff))

        def delete_session_summary(self, session_id, user_id=""):
            calls.append(("summary", session_id, user_id))

    monkeypatch.setattr(
        api_server, "_get_long_term_memory", lambda: FakeMemory()
    )

    assert api_server._erase_long_term_entries([("妈妈", "")])
    assert ("scope", "medix_profile_妈妈") in calls

    assert api_server._erase_long_term_before("妈妈", "", "2026-01-01")
    assert (
        "scope_before", "medix_profile_妈妈", "2026-01-01"
    ) in calls


# ---------------------------------------------------------------- 深度研究
def test_web_search_can_be_disabled_and_fallback_is_deidentified(monkeypatch):
    from research.deep_research_workflow import DeepResearchWorkflow

    monkeypatch.setenv("MEDIX_DISABLE_WEB_SEARCH", "1")
    workflow = DeepResearchWorkflow(
        llm_client=object(), use_web_search=True, use_knowledge_base=False
    )
    assert workflow.use_web_search is False
    assert workflow.web_search is None
    assert workflow._fallback_queries("张三 65岁 华法林 牙龈出血") == [
        "张三 65岁 华法林 牙龈出血"
    ]

    workflow.use_web_search = True
    # 网络搜索开启时规划失败不能把原始问题交给第三方搜索
    assert workflow._fallback_queries("张三 65岁 华法林 牙龈出血") == [
        "最新临床指南"
    ]


# ---------------------------------------------------------------- 前端
def test_frontend_round42_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 深链待恢复标记在导航丢掉 ?session= 后仍按深链处理
    assert "pendingDeepLinkSession === sessionId" in app_js
    assert "deepLinkedSession = true;" in app_js

    # 换档等待前先标记待重置
    profile_block = app_js[
        app_js.index("async function applySelectedProfile"):
        app_js.index("loadHomeGlance();", app_js.index(
            "async function applySelectedProfile"))
    ]
    assert profile_block.index("setPendingSessionReset(true);") < (
        profile_block.index("await startNewSession();")
    )

    # 草稿归属在调用时捕获，新建会话后重新归属
    save_block = app_js[
        app_js.index("function saveDraft()"):
        app_js.index("function reassignDraftToCurrentSession")
    ]
    assert "const ownerSnapshot = sessionId || '';" in save_block
    assert "safeSessionSet(DRAFT_OWNER_KEY, ownerSnapshot);" in save_block
    start_block = app_js[
        app_js.index("async function startNewSession()"):
        app_js.index("async function", app_js.index(
            "async function startNewSession()") + 10)
    ]
    assert "reassignDraftToCurrentSession();" in start_block

    # 待重置标记不再写入跨标签页共享的 localStorage：只保留本标签页
    # sessionStorage 标记，并失效共享会话指针避免冷启动恢复旧档案
    assert "safeStorageSet(pendingResetStorageKey(sessionId), '1')" not in app_js
    assert "safeStorageRemove('medix.session')" in app_js
