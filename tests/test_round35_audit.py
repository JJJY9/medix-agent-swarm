"""第三十五轮审查：triage 否定/科普回归、导入双胞胎、别名、代理头、预约取消。"""
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
def test_enumeration_negation_is_preserved():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "无发热、咳嗽、胸痛",
        "否认胸痛、呼吸困难",
        "今天没有胸痛、咯血，只是有点头晕",
    ):
        kept = patient_complaint_text(text)
        assert "、" in kept
        assert detect_red_flags(kept)["level"] == "none", text


def test_educational_markers_do_not_drop_real_complaints():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "胸痛表现为压榨性",
        "症状包括胸痛和呼吸困难",
        "现在主要表现为胸痛",
    ):
        kept = patient_complaint_text(text)
        assert kept, text
        assert detect_red_flags(kept)["level"] == "emergency", text

    # 纯定义问句仍然过滤
    assert patient_complaint_text("什么是咯血？") == ""
    assert patient_complaint_text("咯血是什么？") == ""
    assert patient_complaint_text("咯血是什么病") == ""


def test_pregnancy_and_reversed_head_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕妇阴道出血", "孕早期阴道出血", "孕12周阴道出血",
        "早孕出血", "产前出血",
    ):
        level = detect_red_flags(patient_complaint_text(text))["level"]
        assert level == "emergency", text
    assert detect_red_flags(
        patient_complaint_text("孕妇牙龈出血")
    )["level"] != "emergency"

    for text in ("吃华法林，摔了一跤，磕了头", "吃华法林，头上撞了个包"):
        level = detect_red_flags(
            patient_complaint_text(text), {"medications": "华法林"}
        )["level"]
        assert level == "emergency", text


def test_stopped_medication_not_flagged_as_current():
    from core.medication_safety import check_medication_safety

    for question in (
        "华法林已经停用两个月了，能吃布洛芬吗",
        "我以前吃阿司匹林，现在能吃布洛芬吗",
        "我已经停用两个月的华法林，能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") != "warning", question

    for question in (
        "华法林不能停药，能吃布洛芬吗",
        "华法林未停药，能吃布洛芬吗",
    ):
        result = check_medication_safety(question, {})
        assert result.get("level") == "warning", question


# ---------------------------------------------------------------- 导入双胞胎
def test_restore_deleted_twin_with_same_content(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "twins.db"))
    try:
        session_store.save_profile("妈妈", {})
        session_store.save_profile("爸爸", {})
        first = session_store.add_medication("妈妈", "维生素D", dose="400IU")
        second = session_store.add_medication("爸爸", "维生素D", dose="400IU")
        same_second = "2026-01-01T08:00:00"
        with session_store._lock, session_store._conn:
            session_store._conn.execute(
                "UPDATE medications SET created_at = ?"
                " WHERE id IN (?, ?)",
                (same_second, first["id"], second["id"]),
            )
        backup = session_store.export_data()
        assert session_store.delete_medication(first["id"])

        result = session_store.import_data(backup)

        assert result["medications"] == 1
        rows = session_store.list_medications(limit=10)
        assert sorted(item["profile"] for item in rows) == ["妈妈", "爸爸"]
    finally:
        session_store.close()


def test_repeated_import_is_idempotent(tmp_path):
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "idem.db"))
    try:
        session_store.save_profile("妈妈", {})
        session_store.add_medication("妈妈", "药A", dose="1g")
        backup = session_store.export_data()

        session_store.import_data(backup)
        before = len(session_store.list_medications(limit=10))
        session_store.import_data(backup)
        after = session_store.list_medications(limit=10)

        assert len(after) == before
    finally:
        session_store.close()


# ---------------------------------------------------------------- 别名
def test_alias_deleted_reused_name_does_not_follow_old_chain(store):
    store.save_profile("妈妈", {"sex": "女"})
    assert store.rename_profile("妈妈", "母亲")
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("s-old", "问题", "回答")
    store.set_session_profile("s-old", "妈妈")
    store.delete_profile("妈妈")

    # 名字被删除后又复用：不能沿用过往前任的 A→B 改名链
    assert store.resolve_profile_alias("妈妈") == "妈妈"


def test_alias_delete_recreate_then_rename_still_resolves(store):
    import time

    store.save_profile("妈妈", {"sex": "女"})
    store.delete_profile("妈妈")
    time.sleep(1.05)
    store.save_profile("妈妈", {"sex": "女"})
    time.sleep(1.05)
    assert store.rename_profile("妈妈", "母亲")

    assert store.resolve_profile_alias("妈妈") == "母亲"


# ---------------------------------------------------------------- 预约取消
def test_cancel_appointment_unlinks_visit_followup(store):
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-11-10",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-11-10T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?"
            " WHERE id = ?",
            (appointment["id"], visit["id"]),
        )

    store.update_appointment(
        appointment["id"], "妈妈", "复诊：高血压",
        scheduled_at="2026-11-10T09:00", status="cancelled",
    )

    record = store.get_visit_record(visit["id"])
    assert record["followup_scheduled"] is False
    assert record["followup_appointment_id"] == 0


# ---------------------------------------------------------------- 代理头
def test_forwarded_header_rightmost_and_conflicts():
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    # 多元素 Forwarded：取最后一个元素的 for（最近代理追加）
    assert api_server._forwarded_client_address(
        FakeRequest({"forwarded": "for=127.0.0.1, for=203.0.113.9"})
    ) == "203.0.113.9"
    assert api_server._is_loopback_client(
        FakeRequest({"forwarded": "for=127.0.0.1, for=203.0.113.9"})
    ) is False
    # 客户端自带 for=127.0.0.1，代理在末尾追加真实地址：不能冒充本机
    assert api_server._is_loopback_client(
        FakeRequest({
            "forwarded": "for=127.0.0.1;proto=https, for=203.0.113.9",
        })
    ) is False
    # 多转发头互相矛盾：fail closed
    assert api_server._forwarded_client_address(FakeRequest({
        "x-forwarded-for": "127.0.0.1",
        "x-real-ip": "203.0.113.9",
    })) == ""
    # XFF 带端口/方括号/引号时归一化
    assert api_server._forwarded_client_address(
        FakeRequest({"x-forwarded-for": "127.0.0.1:40001"})
    ) == "127.0.0.1"
    assert api_server._forwarded_client_address(
        FakeRequest({"x-forwarded-for": "[::1]:443"})
    ) == "::1"


def test_client_ip_key_requires_trusted_loopback_peer(monkeypatch):
    import api_server

    class LoopbackClient:
        host = "127.0.0.1"

    class LanClient:
        host = "192.168.1.50"

    class FakeRequest:
        def __init__(self, headers, client):
            self.headers = headers
            self.client = client

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    # 直连 LAN 客户端伪造 XFF 不能更换限流桶
    assert api_server._client_ip_key(FakeRequest(
        {"x-forwarded-for": "10.0.0.9"}, LanClient()
    )) == "ip:192.168.1.50"
    # 本机代理后的用户按真实客户端地址分桶
    assert api_server._client_ip_key(FakeRequest(
        {"x-forwarded-for": "203.0.113.9"}, LoopbackClient()
    )) == "ip:203.0.113.9"


def test_forwarded_proto_only_counts_as_proxy_request(monkeypatch):
    import api_server

    class LoopbackClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = LoopbackClient()

    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    # 只带 X-Forwarded-Proto 的代理请求同样按远程处理（fail closed）
    assert api_server._is_loopback_client(
        FakeRequest({"x-forwarded-proto": "https"})
    ) is False


def test_same_origin_with_sec_fetch_site_allows_proxy_host_rewrite(
    client_and_store,
):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions", json={},
        headers={
            "Origin": "http://testserver:8080",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert response.status_code != 403


# ---------------------------------------------------------------- 每日累计与家庭风险
def test_daily_metric_insight_falls_back_to_latest_day(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 4000, measured_at=f"{yesterday}T08:00")
    store.add_metric("妈妈", "steps", 5000, measured_at=f"{yesterday}T18:00")

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()["insights"]

    texts = " ".join(
        f"{item.get('title')} {item.get('detail')}" for item in insights
    )
    # 昨天累计 9000 已达标：不能拿单条记录报“2 次低于目标”
    assert "低于目标" not in texts
    assert "复测" not in texts


def test_family_overview_risk_for_unassigned_records(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("unassigned-risk", "头晕怎么办", "综合风险：高危")
    store.add_metric("", "weight", 60)

    data = client.get("/api/family/overview", params={"days": 7}).json()

    row = next(
        item for item in data["profiles"]
        if item.get("display_name") == "未指定"
    )
    assert row["consultations"]["risk"] == "high"
    assert row["attention"]["level"] == "warning"


def test_pending_reset_persists_and_profile_switch_detaches():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 关闭标签页/重开 PWA 后仍能识别「下一轮换新会话」
    assert "pendingResetStorageKey(sessionId)" in app_js
    assert "safeStorageSet(pendingResetStorageKey(sessionId), '1')" in app_js
    assert "safeStorageGet(pendingResetStorageKey(sessionId))" in app_js
    # 恢复失败/线程为空时换档也要断开旧会话
    start = app_js.index("async function applySelectedProfile()")
    body = app_js[start:start + 2200]
    assert "await detachSessionForProfileChange()" in body
