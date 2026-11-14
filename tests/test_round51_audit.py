"""第五十一轮审查：枚举双重否定、孕产期出血定义问句/有一点血/口腔、
宾语剥离范围、家属患者、分诊停药词、酒精疑问、改名链目标、锚点自认领、
导入归属一致性、IPv6 映射/共享桶、闸门欠账、全部范围日累计、家庭最近
活动、深链编辑持久化与失败主诉恢复。"""
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


# ---------------------------------------------------------------- 分诊
def test_ruled_out_enumeration_is_positive():
    from core.triage import detect_red_flags

    for text in (
        "未排除发热和胸痛",
        "尚未排除发热和胸痛",
        "未除外发热和胸痛",
    ):
        result = detect_red_flags(text)
        assert result["level"] == "emergency", text
        assert "胸痛" in result["matches"], text
    result = detect_red_flags("未排除胸痛和呼吸困难")
    assert "呼吸困难" in result["matches"]
    assert detect_red_flags("没有发热和胸痛")["level"] == "none"
    assert detect_red_flags("医生排除了胸痛")["level"] == "none"


def test_pregnancy_bleeding_definition_question_kept():
    from core.triage import detect_red_flags, patient_complaint_text

    kept = patient_complaint_text("我怀孕20周，阴道出血是什么病")
    assert "孕产期出血" in detect_red_flags(kept)["matches"]
    result = detect_red_flags(
        "阴道出血是什么病", {"history": "怀孕20周"}
    )
    assert "孕产期出血" in result["matches"]


def test_small_amount_and_presence_bleeding():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "孕20周，今天有一点血",
        "孕20周，有一些血",
        "孕20周，有血",
    ):
        assert "孕产期出血" in detect_red_flags(
            patient_complaint_text(text), {}
        )["matches"], text
    assert detect_red_flags("孕20周，有血栓")["level"] == "none"


def test_gum_bleeding_main_path_and_family_care_scope():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in ("孕20周，刷牙出血", "孕20周，漱口流血"):
        assert detect_red_flags(patient_complaint_text(text), {})[
            "level"
        ] == "none", text
    for text in (
        "我陪我妈去化疗，今天发烧38.5度",
        "我照顾我妈吃华法林，今天头撞到门框",
    ):
        assert detect_red_flags(patient_complaint_text(text), {})[
            "level"
        ] == "none", text
    # 患者本人的外伤仍按抗凝+外伤处理
    assert "抗凝治疗期间头部外伤" in detect_red_flags(
        patient_complaint_text("我吃华法林，我扶我爸起来时头撞到门框"), {}
    )["matches"]


def test_family_patient_phrase_not_patient_history():
    from core.triage import detect_red_flags, patient_complaint_text

    assert detect_red_flags(
        patient_complaint_text("我爸是化疗患者，今天发烧38.5度"), {}
    )["level"] == "none"
    assert "免疫功能低下伴发热" in detect_red_flags(
        patient_complaint_text("我和我爸都在化疗，今天发烧38.5度"), {}
    )["matches"]


def test_triage_stop_words_synced():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我不吃华法林，今天头撞到门框",
        "华法林我不吃了，今天头撞到门框",
    ):
        assert detect_red_flags(patient_complaint_text(text), {})[
            "level"
        ] == "none", text
    assert "抗凝治疗期间头部外伤" in detect_red_flags(
        patient_complaint_text("我吃华法林，今天头撞到门框"), {}
    )["matches"]


# ---------------------------------------------------------------- 用药
def test_alcohol_question_is_not_negation():
    from core.medication_safety import check_medication_safety

    for text in (
        "吃头孢后能不能喝酒",
        "吃泰诺能不能喝酒",
        "打胰岛素能不能喝酒",
        "吃格列美脲能不能喝酒",
    ):
        result = check_medication_safety(text, {})
        assert result["level"] in ("warning", "info"), text
    assert check_medication_safety("吃头孢后不能喝酒", {})[
        "level"
    ] == "none"


# ---------------------------------------------------------------- 档案/导入
def test_rename_chain_does_not_cross_recreated_target(store):
    store.save_profile("甲", {"sex": "男"})
    store.rename_profile("甲", "乙")
    assert store.delete_profile("乙") is True
    # 旧名已不存在；改名目标在改名后被删除：不能继续解析到乙
    assert store.resolve_profile_alias("甲") == "甲"


def test_anchor_twin_delete_and_edit_self_claims(store):
    store.save_profile("妈妈", {"sex": "女"})
    first = store.add_metric(
        "妈妈", "bp", 120, 80, measured_at="2026-06-01T08:00"
    )
    second = store.add_metric(
        "妈妈", "bp", 130, 85, measured_at="2026-06-01T08:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    with store._lock, store._conn:
        store._conn.execute(
            "DELETE FROM health_metrics WHERE id = ?",
            (second["id"],),
        )
    time.sleep(1.05)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET value1 = 140, value2 = 90,"
            " updated_at = ? WHERE id = ?",
            (datetime.now().isoformat(timespec="seconds"), first["id"]),
        )
    result = store.import_data(backup)
    assert result.get("metrics") == 1
    rows = store.list_metrics("妈妈", "bp", 1000, 365)
    assert sorted(item["value1"] for item in rows) == [130, 140]


def test_rejected_followup_link_clears_owner_and_marker(store):
    store.save_profile("妈妈", {"sex": "女"})
    created = "2026-06-01T08:00:00"
    backup = {
        "visit_records": [{
            "id": 1, "profile": "妈妈", "visit_date": "2026-10-01",
            "diagnosis": "高血压", "next_followup_date": "2026-12-25",
            "followup_appointment_id": 1, "followup_reminder_id": 0,
            "created_at": created, "updated_at": created,
        }],
        "appointments": [{
            "id": 1, "profile": "妈妈", "title": "复诊：高血压",
            "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
            "owner_visit_id": 1, "note": "由就诊记录 #1 自动创建",
            "created_at": created, "updated_at": created,
        }],
    }
    store.import_data(backup)
    appointments = store.list_appointments(None, None)
    assert len(appointments) == 1
    assert appointments[0]["owner_visit_id"] == 0
    assert "由就诊记录" not in appointments[0]["note"]


def test_shared_followup_appointment_not_relinked(store):
    store.save_profile("妈妈", {"sex": "女"})
    created = "2026-06-01T08:00:00"
    backup = {
        "visit_records": [
            {
                "id": 1, "profile": "妈妈", "visit_date": "2026-10-01",
                "diagnosis": "高血压", "next_followup_date": "2026-12-14",
                "followup_appointment_id": 1, "followup_reminder_id": 0,
                "created_at": created, "updated_at": created,
            },
            {
                "id": 2, "profile": "妈妈", "visit_date": "2026-10-02",
                "diagnosis": "糖尿病", "next_followup_date": "2026-12-14",
                "followup_appointment_id": 1, "followup_reminder_id": 0,
                "created_at": "2026-06-02T08:00:00",
                "updated_at": "2026-06-02T08:00:00",
            },
        ],
        "appointments": [{
            "id": 1, "profile": "妈妈", "title": "复诊",
            "scheduled_at": "2026-12-14T09:00", "status": "upcoming",
            "owner_visit_id": 1, "note": "由就诊记录 #1 自动创建",
            "created_at": created, "updated_at": created,
        }],
    }
    store.import_data(backup)
    visits = store.list_visit_records(None, 0, "", 10)
    linked = [
        item for item in visits if item["followup_appointment_id"]
    ]
    assert len(linked) == 1


def test_invalid_twin_rows_do_not_inflate_group_sizes(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric(
        "妈妈", "bp", 120, 80, measured_at="2026-06-01T08:00"
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET created_at = '2026-06-01T08:00:00',"
            " updated_at = '2026-06-01T08:00:00'"
        )
    backup = store.export_data()
    time.sleep(1.05)
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE health_metrics SET value1 = 140, value2 = 90,"
            " updated_at = ?",
            (datetime.now().isoformat(timespec="seconds"),),
        )
    polluted = copy.deepcopy(backup)
    # 手动改备份内容后旧校验和失效：导入前移除（校验和由导出侧自洽）
    polluted.pop("checksum", None)
    polluted["health_metrics"].append({
        "id": 999, "profile": "妈妈", "metric": "bp",
        "value1": 999, "value2": 999,
        "measured_at": "2026-06-01T08:00",
        "created_at": "2026-06-01T08:00:00",
        "updated_at": "2026-06-01T08:00:00",
    })
    result = store.import_data(polluted)
    assert result.get("metrics") == 0
    assert result.get("invalid", 0) >= 1
    rows = store.list_metrics("妈妈", "bp", 1000, 365)
    assert [item["value1"] for item in rows] == [140]


def test_metric_summary_all_range_uses_daily_totals(store):
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 4000, measured_at=f"{today}T08:00")
    store.add_metric("妈妈", "steps", 5000, measured_at=f"{today}T18:00")
    summary = store.metric_summary("妈妈", "steps", 0)
    assert summary["value1"]["avg"] == 9000
    assert summary["value1"]["max"] == 9000


# ---------------------------------------------------------------- 代理
def test_ipv4_mapped_ipv6_keeps_separate_bucket():
    import api_server

    assert api_server._bucket_address(
        "::ffff:192.0.2.1"
    ) == "192.0.2.1"
    assert api_server._bucket_address("2001:db8::1") == "2001:db8::/64"


def test_ipv6_proxy_peer_uses_shared_bucket(monkeypatch, tmp_path):
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
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(1, window=300),
    )
    client = TestClient(
        api_server.app,
        base_url="http://localhost",
        client=("::1", 50000),
    )
    try:
        first = client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        )
        assert first.status_code == 401
        correct = client.get(
            "/api/sessions", headers={"X-Medix-Token": token}
        )
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_compare_gate_debt_is_bounded():
    import api_server

    api_server._auth_compare_gates.clear()
    waits = [
        api_server._auth_compare_wait_seconds("ip:test")
        for _ in range(30)
    ]
    assert all(wait <= 0.41 for wait in waits)
    api_server._auth_compare_gates.clear()


# ---------------------------------------------------------------- 报表
def test_family_last_activity_uses_out_of_window_records(
    client_and_store
):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    old_day = (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")
    store.add_symptom("妈妈", "咳嗽", 3, recorded_at=f"{old_day}T08:00")
    data = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert str(row["last_activity"]).startswith(old_day)


# ---------------------------------------------------------------- 前端
def test_frontend_round51_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "DEEP_LINK_EDITED_KEY" in app_js
    assert "safeSessionSet(DEEP_LINK_EDITED_KEY, sessionId);" in app_js
    assert "safeSessionGet(DEEP_LINK_EDITED_KEY) === sessionId" in app_js
    send_src = app_js[
        app_js.index("async function send(question)"):
        app_js.index("async function send(question)") + 1200
    ]
    assert "safeSessionRemove(INFLIGHT_QUESTION_KEY);" in send_src
    assert "if (sendPersisted) {" in app_js
    assert "// 失败轮次可能从未落库：把主诉放回输入框与草稿" in app_js
    assert "if (els.input.value.includes(question)) {" in app_js
