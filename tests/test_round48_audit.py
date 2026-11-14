"""第四十八轮审查：症状语境建立、家属叙述框架归属、孕周简写、英文前置
停药与后置「不吃了」、改期降级清理复诊链接、同预约多提醒导入、改挂漂移
配对、跨成员内容指纹、误认领同秒记录、预约提醒并发锁、insecure-lan
开关、家庭重度症状去重、风险候选截断、代理预锁定粒度与前端草稿恢复。"""
import os
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


# ---------------------------------------------------------------- 分诊
def test_symptom_course_clause_establishes_patient_context():
    from core.triage import detect_red_flags, patient_complaint_text

    for text, expected in (
        ("发烧两天，抽搐是什么病", "抽搐"),
        ("腹痛两天，黑便是什么病", "消化道出血征象"),
        ("咳嗽三天，咯血是什么病", "咯血"),
    ):
        kept = patient_complaint_text(text)
        result = detect_red_flags(kept)
        assert result["level"] == "emergency", text
        assert expected in result["matches"], text
    # 铺垫/否定分句仍然不算患者语境
    for text in ("今天想问问，抽搐是什么病", "没有胸痛，抽搐是什么病"):
        kept = patient_complaint_text(text)
        assert detect_red_flags(kept)["level"] == "none", text


def test_family_narration_keeps_patient_anticoagulant_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我吃华法林，我妈说我今天头撞到门框",
        "我吃华法林，老婆说我头撞到墙了",
        "我吃华法林，家人看到我摔到头了",
        "我妈让我吃华法林，今天头撞到门框",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    result = detect_red_flags(
        patient_complaint_text("我妈说我今天摔到头了"),
        {"medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 家属本人作主语时仍不能拼到患者身上
    for text in (
        "我吃华法林，我爸今天头撞到门框",
        "我爸吃华法林，我今天头撞了一下",
        "我妈说她自己吃华法林",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text


def test_family_fever_not_attributed_to_patient():
    from core.triage import detect_red_flags, patient_complaint_text

    assert detect_red_flags(
        patient_complaint_text("我爸发烧39度"), {"history": "化疗"}
    )["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("我爸发烧39度"), {"age": "2个月"}
    )["level"] == "none"
    result = detect_red_flags(
        patient_complaint_text("我发烧39度"), {"history": "化疗"}
    )
    assert "免疫功能低下伴发热" in result["matches"]


def test_pregnancy_week_shorthand():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in ("孕12+周，阴道出血", "孕12-14周，阴道出血",
                 "孕20w，阴道出血"):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "emergency", text
        assert "孕产期出血" in result["matches"], text


# ---------------------------------------------------------------- 用药
def test_english_prefix_stop_and_chinese_completed_negation():
    from core.medication_safety import check_medication_safety

    for text in (
        "I stopped warfarin two years ago. Can I take ibuprofen?",
        "I stopped taking warfarin two years ago. Can I take ibuprofen?",
        "I am off warfarin. Can I take ibuprofen?",
        "华法林我不吃了，能吃布洛芬吗",
        "华法林不再吃了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text
    # 近期停药仍有抗凝作用；「不吃不行」仍是当前用药
    for text in (
        "I stopped warfarin 2 days ago. Can I take ibuprofen?",
        "华法林不吃不行，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] != "none", text


# ---------------------------------------------------------------- 复诊
def test_reschedule_before_visit_date_clears_followup_links(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-11", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-11-04T09:00"
    )
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["followup_appointment_id"] == 0
    assert refreshed["followup_reminder_id"] == 0
    assert refreshed.get("followup_scheduled") is False
    # 之后修改就诊复诊日期不能把已降级的独立预约删掉
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-20"
    )
    assert store.get_appointment(appointment_id) is not None


# ---------------------------------------------------------------- 导入
def test_import_keeps_multiple_same_title_reminders(store):
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-15T09:00"
    )
    first = store.add_reminder(
        "复诊：高血压", "2026-12-01", profile="妈妈",
        appointment_id=appointment["id"],
    )
    second = store.add_reminder(
        "复诊：高血压", "2026-12-10", profile="妈妈",
        appointment_id=appointment["id"],
    )
    backup = store.export_data()
    store.delete_reminder(first["id"])
    store.delete_reminder(second["id"])
    # 本地改期后重建了一条新日期的提醒
    store.add_reminder(
        "复诊：高血压", "2026-12-20", profile="妈妈",
        appointment_id=appointment["id"],
    )
    result = store.import_data(backup)
    assert result.get("reminders") == 2
    rows = [
        item for item in store.list_reminders(False)
        if item["title"] == "复诊：高血压"
    ]
    assert sorted(item["due_at"] for item in rows) == [
        "2026-12-01", "2026-12-10", "2026-12-20",
    ]
    # 重复导入保持幂等
    assert store.import_data(backup).get("reminders") == 0


def test_moved_reminder_with_id_drift_not_resurrected(store):
    from memory.session_store import SessionStore

    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.save_profile("爸爸", {"sex": "男"})
        source.add_reminder("妈妈复查", "2026-12-01", profile="妈妈")
        backup = source.export_data()
    finally:
        source.close()

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    placeholder = store.add_reminder(
        "占位", "2026-11-01", profile="妈妈"
    )
    # 目标库已有早于备份的行：导入后本地 id 与备份主键漂移
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = '2026-01-01T00:00:00',"
            " updated_at = '2026-01-01T00:00:00' WHERE id = ?",
            (placeholder["id"],),
        )
    store.import_data(backup)
    imported = [
        item for item in store.list_reminders(True)
        if item["title"] == "妈妈复查"
    ][0]
    time.sleep(1.05)
    store.update_reminder(imported["id"], profile="爸爸")
    result = store.import_data(backup)
    assert result.get("reminders") == 0
    rows = [
        item for item in store.list_reminders(True)
        if item["title"] == "妈妈复查"
    ]
    assert len(rows) == 1
    assert rows[0]["profile"] == "爸爸"


def test_cross_member_same_content_does_not_duplicate(store):
    from memory.session_store import SessionStore

    source = SessionStore(str(store.db_path) + ".src")
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.save_profile("爸爸", {"sex": "男"})
        source.add_medication("妈妈", "阿司匹林", "100mg")
        source.add_medication("爸爸", "阿司匹林", "100mg")
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE medications SET created_at = '2026-06-01T08:00:00',"
                " updated_at = '2026-06-01T08:00:00'"
            )
        backup = source.export_data()
    finally:
        source.close()

    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    store.import_data(backup)
    rows = store.list_medications(limit=20)
    assert len(rows) == 2
    repeated = store.import_data(backup)
    assert repeated.get("medications") == 0
    rows = store.list_medications(limit=20)
    assert len(rows) == 2


def test_same_second_unrelated_appointment_not_claimed(store):
    store.save_profile("妈妈", {"sex": "女"})
    target = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00"
    )
    backup = store.export_data()
    store.delete_appointment(target["id"])
    # 本地新建一条同秒、同档案但内容完全不同的预约
    other = store.add_appointment(
        "妈妈", "牙科检查", scheduled_at="2026-12-31T09:00"
    )
    with store._lock, store._conn:
        source_end = backup["appointments"][0]["created_at"]
        store._conn.execute(
            "UPDATE appointments SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (source_end, source_end, other["id"]),
        )
    store.import_data(backup)
    rows = store.list_appointments(None, None)
    titles = sorted(item["title"] for item in rows)
    assert titles == ["复诊：高血压", "牙科检查"]
    other_row = [
        item for item in rows if item["title"] == "牙科检查"
    ][0]
    assert other_row["owner_visit_id"] == 0
    restored = [
        item for item in rows if item["title"] == "复诊：高血压"
    ][0]
    assert restored["scheduled_at"] == "2026-12-14T09:00"


# ---------------------------------------------------------------- 预约提醒/配置
def test_appointment_reminder_requires_upcoming_and_locks(client_and_store):
    import inspect

    import api_server

    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-15T09:00"
    )
    store.update_appointment_partial(appointment["id"], status="cancelled")
    response = client.post(
        f"/api/appointments/{appointment['id']}/reminder"
    )
    assert response.status_code == 400
    assert store.list_reminders(False) == []
    source = inspect.getsource(api_server.create_appointment_reminder)
    assert "_VISIT_FOLLOWUP_LOCK" in source


def test_allow_insecure_lan_flag_sets_request_env(monkeypatch, tmp_path):
    import sys
    import types

    import api_server

    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    old_flag = os.environ.pop("MEDIX_ALLOW_INSECURE_LAN", None)
    calls = []
    fake_uvicorn = types.SimpleNamespace(
        run=lambda *args, **kwargs: calls.append((args, kwargs))
    )
    monkeypatch.setitem(sys.modules, "uvicorn", fake_uvicorn)
    monkeypatch.setattr(
        sys, "argv",
        ["api_server.py", "--allow-insecure-lan", "--host", "127.0.0.1"],
    )
    try:
        api_server.main()
        assert os.environ.get("MEDIX_ALLOW_INSECURE_LAN") == "1"
        assert calls
    finally:
        os.environ.pop("MEDIX_ALLOW_INSECURE_LAN", None)
        if old_flag is not None:
            os.environ["MEDIX_ALLOW_INSECURE_LAN"] = old_flag


# ---------------------------------------------------------------- 报表/风险
def test_family_severe_symptoms_keep_each_member(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    store.add_symptom("妈妈", "胸痛", 9)
    store.add_symptom("爸爸", "胸痛", 8)
    data = client.get("/api/reports/weekly", params={"days": 30}).json()
    severe = data["symptoms"]["severe_items"]
    profiles = {item.get("profile") for item in severe
                if item["symptom"] == "胸痛"}
    assert profiles == {"妈妈", "爸爸"}


def test_risk_candidates_keep_parseable_answers(store):
    from api_server import _latest_parseable_risk

    store.save_profile("妈妈", {"sex": "女"})
    old_day = (datetime.now() - timedelta(days=5)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    store.save_exchange(
        "risk-old", "胸痛怎么办", "风险等级：高危，请立即就医。",
        profile="妈妈",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (old_day, "risk-old"),
        )
    for index in range(210):
        store.save_exchange(
            f"risk-noise-{index}", f"问题{index}",
            "如有紧急情况请及时就医。", profile="妈妈",
        )
    rows = store.recent_risk_answers(old_day[:10], per_profile=200)
    result = _latest_parseable_risk(rows, old_day[:10], "")
    assert "妈妈" in result
    assert result["妈妈"]["answered_at"] == old_day[:10]


# ---------------------------------------------------------------- 代理
def test_appended_forwarded_address_requires_proxy_written_value(
    monkeypatch,
):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    single = FakeRequest({"x-forwarded-for": "203.0.113.9"})
    assert api_server._forwarded_appended_address(single) == "203.0.113.9"
    multihop = FakeRequest({"x-forwarded-for": "203.0.113.9, 127.0.0.1"})
    assert api_server._forwarded_appended_address(multihop) == ""
    unknown = FakeRequest({"x-forwarded-for": "unknown, 127.0.0.1"})
    assert api_server._forwarded_appended_address(unknown) == ""


def test_shared_bucket_correct_token_still_allowed(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(2, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("127.0.0.1", 50000),
    )
    try:
        for _ in range(2):
            wrong = client.get(
                "/api/sessions", headers={"X-Medix-Token": "bad"}
            )
            assert wrong.status_code == 401
        limited = client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad-again"}
        )
        assert limited.status_code == 429
        correct = client.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        )
        assert correct.status_code == 200
    finally:
        session_store.close()


# ---------------------------------------------------------------- 前端
def test_frontend_round48_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 待重置期间重开当前会话保留现场；深链草稿冲突保留用户编辑
    assert "const keepPendingReset = sameSession && pendingSessionReset" in app_js
    assert "const deepLinkDraftConflict = deepLinkOpen && Boolean(" in app_js
    assert "preserveComposer" in app_js
    # 待重置与深链的时间戳裁决
    assert "PENDING_RESET_AT_KEY" in app_js
    assert "DEEP_LINK_AT_KEY" in app_js
    assert "const userEditedAfterDeepLink = Boolean(" in app_js
    # 未完成主诉在刷新后恢复
    assert "INFLIGHT_QUESTION_KEY" in app_js
    assert "restoreInflightQuestion();" in app_js
    assert "function restoreInflightQuestion()" in app_js
    assert "window.addEventListener('beforeunload'" in app_js
