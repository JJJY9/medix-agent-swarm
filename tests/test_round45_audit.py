"""第四十五轮审查：孕N+M周、依从性用药、跨分句患者语境、导入兜底/指纹、
复诊归属改期、无链接备注标记、跨档案导入链接、代理混淆标识与共享桶、
回环限流、风险窗口、化验成员、CSV 达标、提醒重开与前端草稿。"""
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


# ---------------------------------------------------------------- 分诊/用药
def test_pregnancy_week_plus_days_notation():
    from core.triage import detect_red_flags

    for text in (
        "孕36+2周，今天阴道出血",
        "孕 36+2 周，阴道流血",
        "孕12+3周，下体出血",
        "孕36+2周，见红",
    ):
        assert detect_red_flags(text, {})["level"] == "emergency", text
    assert detect_red_flags(
        "今天阴道出血", {"history": "孕36+2周"}
    )["level"] == "emergency"


def test_pregnancy_first_denial_is_negative():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕8周，无阴道出血",
        "孕8周，未见阴道出血",
        "孕8周，否认阴道流血",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_adherence_phrases_are_current_use():
    from core.medication_safety import check_medication_safety

    for text in (
        "我没有按时吃华法林，现在牙疼能吃布洛芬吗",
        "我未规律服用华法林，现在牙疼能吃布洛芬吗",
        "我最近没有按时吃华法林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) == "warning", text

    # 真正的未用药仍不告警
    assert check_medication_safety(
        "我没有吃华法林，能吃布洛芬吗", {}
    ).get("level") != "warning"


def test_patient_context_carries_to_definition_clause():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "宝宝发烧，抽搐是什么病",
        "孩子发烧了，抽搐是什么病",
        "孩子发烧了，咳血是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 孤立定义问句仍按科普过滤
    assert detect_red_flags(
        patient_complaint_text("抽搐是什么病")
    )["level"] == "none"


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created):
    return {
        "id": raw_id, "profile": profile, "name": name, "dose": dose,
        "frequency": "每日一次", "created_at": created,
        "updated_at": created,
    }


def test_metric_fallback_does_not_claim_moved_other_row(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.save_profile("爸爸", {"sex": "男"})
        created = "2026-06-01T08:00:00"
        backup = {"health_metrics": [
            {
                "id": 1, "profile": "妈妈", "metric": "weight",
                "value1": 60, "measured_at": "2026-06-01T08:00",
                "created_at": created, "updated_at": created,
            },
            {
                "id": 2, "profile": "爸爸", "metric": "weight",
                "value1": 70, "measured_at": "2026-06-02T08:00",
                "created_at": created, "updated_at": created,
            },
        ]}
        target.import_data(backup)
        rows = target.list_metrics(None, "weight", 10, 0)
        mom = next(
            item for item in rows if item["profile"] == "妈妈"
        )
        dad = next(
            item for item in rows if item["profile"] == "爸爸"
        )
        assert target.delete_metric(mom["id"])
        time.sleep(1.05)
        assert target.update_metric(dad["id"], profile="妈妈")

        target.import_data(backup)

        values = {
            float(item["value1"])
            for item in target.list_metrics(None, "weight", 10, 0)
        }
        # 被删的 60 必须补回
        assert 60.0 in values
    finally:
        target.close()


def test_lab_null_reference_fingerprint_matches(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.save_profile("爸爸", {"sex": "男"})
        created = "2026-06-01T08:00:00"
        backup = {"lab_results": [
            {
                "id": 1, "profile": "妈妈", "item": "白细胞",
                "value": 6.0, "unit": "10^9/L",
                "tested_at": "2026-06-01T08:00",
                "created_at": created, "updated_at": created,
            },
            {
                "id": 2, "profile": "爸爸", "item": "红细胞",
                "value": 4.5, "unit": "10^12/L",
                "tested_at": "2026-06-02T08:00",
                "created_at": created, "updated_at": created,
            },
        ]}
        target.import_data(backup)
        rows = target.list_lab_results(None, None, None, 0, 100, 0)
        mom = next(
            item for item in rows if item["profile"] == "妈妈"
        )
        dad = next(
            item for item in rows if item["profile"] == "爸爸"
        )
        assert target.delete_lab_result(mom["id"])
        time.sleep(1.05)
        assert target.update_lab_result_partial(dad["id"], profile="妈妈")

        target.import_data(backup)

        rows = target.list_lab_results(None, None, None, 0, 100, 0)
        assert any(
            item["profile"] == "妈妈" and item["item"] == "白细胞"
            for item in rows
        ), rows
    finally:
        target.close()


def test_edited_content_equal_to_other_member_not_other_owned(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.save_profile("爸爸", {"sex": "男"})
        backup = {"medications": [
            _med(1, "妈妈", "氨氯地平", "5mg", "2026-06-01T08:00:00"),
            _med(2, "爸爸", "氨氯地平", "10mg", "2026-07-15T08:00:00"),
        ]}
        target.import_data(backup)
        mom = next(
            item for item in target.list_medications(limit=20)
            if item["profile"] == "妈妈"
        )
        target.update_medication(mom["id"], dose="10mg")

        target.import_data(backup)

        mom_rows = [
            item for item in target.list_medications(limit=20)
            if item["profile"] == "妈妈" and item["name"] == "氨氯地平"
        ]
        assert len(mom_rows) == 1
        assert mom_rows[0]["dose"] == "10mg"
    finally:
        target.close()


def test_import_remaps_note_without_followup_link(store, tmp_path):
    from api_server import _schedule_followup_sync
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-10-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        first = _schedule_followup_sync(source, visit["id"])
        source.update_appointment_partial(
            first["appointment"]["id"], status="cancelled"
        )
        source.update_visit_record_partial(
            visit["id"], next_followup_date=""
        )
        source.update_appointment_partial(
            first["appointment"]["id"], status="upcoming"
        )
        backup = source.export_data()
        appointment_id = first["appointment"]["id"]
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        unrelated = target.add_visit_record(
            "妈妈", visit_date="2026-09-01", diagnosis="体检",
            next_followup_date="2027-03-03",
        )
        target.import_data(backup)
        imported_appointment = target.list_appointments(None)[0]
        note = str(imported_appointment.get("note") or "")
        assert f"#{unrelated['id']}" not in note
        # 改期导入的预约不能覆盖目标库同号无关就诊的复诊日期
        target.update_appointment_partial(
            imported_appointment["id"],
            scheduled_at="2027-02-02T09:00",
        )
        refreshed = target.get_visit_record(unrelated["id"])
        assert refreshed["next_followup_date"][:10] == "2027-03-03"
    finally:
        target.close()


def test_import_cross_profile_link_rejected(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.save_profile("爸爸", {"sex": "男"})
        created = "2026-06-01T08:00:00"
        backup = {
            "appointments": [{
                "id": 1, "profile": "爸爸", "title": "复诊：高血压",
                "scheduled_at": "2026-12-14T09:00",
                "status": "upcoming",
                "created_at": created, "updated_at": created,
            }],
            "visit_records": [{
                "id": 1, "profile": "妈妈", "visit_date": "2026-10-01",
                "diagnosis": "高血压", "next_followup_date": "2026-12-14",
                "followup_appointment_id": 1,
                "created_at": created, "updated_at": created,
            }],
        }
        target.import_data(backup)
        visit = target.list_visit_records(None, 0, "", 10)[0]
        assert visit["followup_appointment_id"] == 0
        appointment = target.list_appointments(None)[0]
        assert target.delete_visit_record(visit["id"])
        assert target.get_appointment(appointment["id"]) is not None
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_reschedule_ignores_foreign_visit_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], status="cancelled"
    )
    store.update_visit_record_partial(visit["id"], next_followup_date="")
    store.update_appointment_partial(
        first["appointment"]["id"], status="upcoming"
    )
    refreshed = store.get_visit_record(visit["id"])
    # 链接写回未命中：不能保留自动归属
    assert refreshed["followup_appointment_id"] == 0
    appointment = store.get_appointment(first["appointment"]["id"])
    assert int(appointment.get("owner_visit_id") or 0) == 0

    store.update_visit_record_partial(
        visit["id"], next_followup_date="2027-01-10"
    )
    _schedule_followup_sync(store, visit["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], scheduled_at="2027-01-20T09:00"
    )
    refreshed = store.get_visit_record(visit["id"])
    assert refreshed["next_followup_date"][:10] == "2027-01-10"


def test_reopen_beside_rebuilt_reminder_degrades_to_standalone(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    assert store.complete_reminder(first["reminder"]["id"])
    store.update_appointment_partial(
        first["appointment"]["id"], scheduled_at="2026-12-20T09:00"
    )
    assert store.reopen_reminder(first["reminder"]["id"])

    reopened = store.get_reminder(first["reminder"]["id"])
    assert reopened["appointment_id"] == 0
    pending = [
        item for item in store.list_reminders(False, "妈妈")
        if item["appointment_id"]
    ]
    assert len(pending) == 1
    assert pending[0]["due_at"] == "2026-12-19"


# ---------------------------------------------------------------- 代理
def test_obfuscated_forwarded_ids_fail_closed(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "forwarded")
    for value in ("for=_hidden", 'for="[unknown]"',
                  "for=unknown:443"):
        request = FakeRequest({"forwarded": value})
        assert api_server._forwarded_client_address(request) == "", value
    request = FakeRequest({"forwarded": "for=_hidden"})
    assert api_server._client_ip_key(request) == "ip:127.0.0.1"


def test_trusted_proxy_without_headers_uses_shared_bucket(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    token = "a-secure-random-token-123"
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUSTED_PROXY_ADDRS", "172.18.0.2")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(2, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("172.18.0.2", 50000),
    )
    try:
        for _ in range(3):
            client.get("/api/sessions", headers={
                "X-Medix-Token": "bad",
            })
        correct = client.get("/api/sessions", headers={
            "X-Medix-Token": token,
        })
        # 无地址头的受信代理请求共用键：不能被预锁定
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_loopback_wrong_tokens_are_rate_limited(client_and_store, monkeypatch):
    import api_server
    from core.ratelimit import SlidingWindowLimiter

    client, _store = client_and_store
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "a-secure-random-token-123")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(2, window=300),
    )
    for _ in range(2):
        response = client.get("/api/sessions", headers={
            "X-Medix-Token": "bad",
        })
        assert response.status_code == 401
    limited = client.get("/api/sessions", headers={
        "X-Medix-Token": "bad",
    })
    assert limited.status_code == 429


# ---------------------------------------------------------------- 报表
def test_risk_window_beyond_100_noise_answers(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    old_day = (datetime.now() - timedelta(days=20)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    store.save_exchange(
        "risk", "胸痛怎么办", "风险等级：高危，请立即就医。",
        profile="妈妈",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = 'risk'",
            (old_day,),
        )
    for index in range(120):
        store.save_exchange(
            "noise", f"问题{index}",
            "如有紧急情况请及时就医。",
            profile="妈妈",
        )

    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == "high"


def test_unassigned_lab_label_in_family_insights(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_lab_result(
        "", "血钾", 6.8, "mmol/L", 3.5, 5.3,
        tested_at=f"{today}T08:00",
    )
    store.add_lab_result(
        "妈妈", "血糖", 2.5, "mmol/L", 3.9, 6.1,
        tested_at=f"{today}T09:00",
    )

    data = client.get("/api/health/insights", params={"days": 30}).json()
    critical = next(
        item for item in data["insights"] if item["key"] == "lab_critical"
    )
    assert "未指定档案：" in critical["detail"]
    assert "妈妈：" in critical["detail"]


def test_family_csv_met_target_not_marked(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    today = datetime.now().strftime("%Y-%m-%d")
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "weight", "value1": 70,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 65,
        "measured_at": f"{today}T08:00",
    })

    csv_response = client.get("/api/family/overview/export.csv")
    line = next(
        item for item in csv_response.text.splitlines()
        if item.startswith("妈妈")
    )
    assert "65kg" in line
    assert "超标" not in line
    assert "低于目标" not in line


def test_family_medication_alert_count_uses_total(client_and_store):
    client, store = client_and_store
    for name in ("妈妈", "爸爸", "孩子"):
        store.save_profile(name, {"sex": "女"})
        for drug in ("华法林", "阿司匹林", "布洛芬"):
            store.add_medication(name, drug, "1片", "每日一次")

    data = client.get("/api/health/insights", params={"days": 30}).json()
    med = next(
        item for item in data["insights"]
        if item["key"] == "medication_safety"
    )
    # 3 人 × 2 条告警：展示只截前 5 条，但标题必须报总数
    assert "6 条安全提示" in med["title"]


# ---------------------------------------------------------------- 前端
def test_frontend_round45_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 恢复输入时已有文本要追加保留
    restore = app_js[
        app_js.index("function restoreComposer"):
        app_js.index("function loadDraft")
    ]
    assert "current.includes(text)" in restore
    assert "`${text}" in restore

    # 常用主诉按钮受发送锁保护
    starter = app_js[
        app_js.index("els.starters.addEventListener('click'"):
        app_js.index("els.starterAdd.addEventListener")
    ]
    assert "if (sendInFlight)" in starter

    # 草稿在页面隐藏/刷新时同步落盘
    assert "flushDraftNow" in app_js
    assert "window.addEventListener('pagehide', flushDraftNow);" in app_js

    # 家庭卡片按判定日累计展示每日累计指标
    assert "function familyMetricValue" in app_js
    assert "daily_judged_total" in app_js
