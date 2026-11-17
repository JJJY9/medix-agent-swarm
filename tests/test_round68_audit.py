"""第六十八轮审查：婴儿/新生儿定义问句、结束治疗归属、指代词归药、
产科语境非妊娠部位出血、血红蛋白/血尿、远期停药跨药（中/英）、同秒
孪生内容编辑后的恢复、指标非法行组大小、化验/疫苗/就诊空时间、就诊
改档案残留归属、低频药依从提示、目标告警最低/最高读数、记忆作用域
加盐、提醒写接口与排程共用锁、前端合并草稿对账与换档序号。"""
import hashlib
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
        "MEDIX_PROXY_SINGLE_HOP", "MEDIX_ALLOW_INSECURE_LAN",
        "MEDIX_RATE_LIMIT",
    ):
        monkeypatch.delenv(key, raising=False)
    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient(api_server.app), session_store
    finally:
        session_store.close()


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


# ---------------------------------------------------------------- 分诊
def test_infant_definition_question_and_pure_terms():
    for text in (
        "新生儿抽搐是什么病",
        "婴儿抽搐是什么病",
        "儿童胸痛是什么病",
    ):
        result = _red_flags(text)
        assert result["level"] != "none", text
    fever = _red_flags("婴儿发烧是什么病", {"age": "2个月"})
    assert "3 个月以下婴儿发热" in fever["matches"]
    # 纯术语问句仍是科普，不弹急症横幅
    for text in ("什么是抽搐", "介绍一下抽搐", "什么是昏迷",
                 "什么是意识不清"):
        assert _red_flags(text)["level"] == "none", text
    # 带求助语义的仍是真实主诉
    assert "抽搐" in _red_flags("解释一下抽搐怎么办")["matches"]


def test_ended_treatment_must_match_context():
    context = {"medications": "华法林"}
    for text in (
        "华法林没吃完，阑尾手术结束三个月了，今天头撞到门框",
        "华法林没吃完，骨折手术完成半年了，今天头撞到门框",
        "华法林没吃完，牙科治疗结束两个月了，今天头撞到门框",
    ):
        result = _red_flags(text, context)
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    chemo = _red_flags(
        "甲氨蝶呤没吃完，化疗结束五年了，今天发烧38.9度", {}
    )
    assert chemo["level"] == "none"


def test_long_stop_pronoun_points_to_current_drug():
    for text in (
        "华法林没吃完，这个药停了半年了，今天头撞到门框",
        "华法林没吃完，该药停了半年了，今天头撞到门框",
        "华法林没吃完，抗凝药停了半年了，今天头撞到门框",
    ):
        assert _red_flags(text)["level"] == "none", text
    other = _red_flags(
        "华法林没吃完，阿司匹林停药一年了，今天头撞到门框",
        {"medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in other["matches"]


def test_obstetric_context_non_pregnancy_sites():
    for text in (
        "孕妇大便出血",
        "产后20天，大便时出血",
        "产妇小便带血",
        "孕期痰中带血",
    ):
        assert _red_flags(text)["level"] == "none", text
    postpartum = _red_flags("产妇剖宫产术后流血")
    assert "孕产期出血" in postpartum["matches"]


def test_hemoglobin_and_hematuria_not_bleeding():
    for text in (
        "怀孕8周，有点血红蛋白低",
        "孕妇有点血尿",
        "怀孕8周，有点尿血",
    ):
        assert _red_flags(text)["level"] == "none", text
    assert "孕产期出血" in _red_flags("怀孕8周，有点血丝")["matches"]


def test_medication_long_stop_other_drug_and_english_off():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林没吃完，阿司匹林停药一年了，能吃布洛芬吗",
        "warfarin 没吃完，aspirin 停药一年了，能吃 ibuprofen 吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    for text in (
        "I have been off warfarin for two days, can I take ibuprofen?",
        "I have been off warfarin for a few days, can I take ibuprofen?",
        "I was off warfarin until yesterday, can I take ibuprofen?",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- 导入
def test_medication_twin_content_edit_restores_deleted(store):
    store.save_profile("妈妈", {})

    def med(rid, dose, active, note="", stopped="", reason=""):
        return {
            "id": rid, "profile": "妈妈", "name": "氨氯地平",
            "dose": dose, "frequency": "每日一次", "note": note,
            "active": active, "stopped_at": stopped,
            "stop_reason": reason, "created_at": CREATED,
            "updated_at": CREATED,
        }

    backup = {"medications": [med(1, "5mg", True), med(
        2, "10mg", False, "已停药", "2026-11-05", "副作用")]}
    store.import_data(backup)
    rows = [dict(r) for r in store._conn.execute(
        "SELECT id, dose, active FROM medications ORDER BY id")]
    stopped_id = next(r["id"] for r in rows if r["dose"] == "10mg")
    active_id = next(r["id"] for r in rows if r["active"])
    store.update_medication(stopped_id, dose="5mg")
    store.delete_medication(active_id)
    store.import_data(backup)
    rows = [dict(r) for r in store._conn.execute(
        "SELECT dose, active FROM medications ORDER BY id")]
    # 被删的在用记录必须恢复；编辑过的停药记录保留
    assert any(r["active"] and r["dose"] == "5mg" for r in rows)
    assert store.import_data(backup).get("medications") == 0


def test_symptom_twin_content_edit_restores_deleted(store):
    store.save_profile("妈妈", {})
    backup = {"symptoms": [
        {"id": 1, "profile": "妈妈", "symptom": "头痛", "severity": 5,
         "note": "A", "recorded_at": "2026-05-01T00:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "symptom": "头晕", "severity": 3,
         "note": "B", "recorded_at": "2026-05-01T00:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [dict(r) for r in store._conn.execute(
        "SELECT id, symptom FROM symptoms ORDER BY id")]
    dizzy = next(r["id"] for r in rows if r["symptom"] == "头晕")
    head = next(r["id"] for r in rows if r["symptom"] == "头痛")
    store.update_symptom_partial(dizzy, symptom="头痛")
    store.delete_symptom(head)
    store.import_data(backup)
    rows = [dict(r) for r in store._conn.execute(
        "SELECT symptom, severity FROM symptoms")]
    assert any(
        r["symptom"] == "头痛" and r["severity"] == 5 for r in rows
    )
    assert store.import_data(backup).get("symptoms") == 0


def test_metric_invalid_row_not_counted_in_group(store):
    store.save_profile("妈妈", {})
    backup = {"health_metrics": [
        {"id": 1, "profile": "妈妈", "metric": "weight", "value1": 60,
         "created_at": CREATED, "measured_at": "2026-11-01T08:00"},
        {"id": 2, "profile": "妈妈", "metric": "weight", "value1": 61,
         "created_at": CREATED, "measured_at": ""},
    ]}
    result = store.import_data(backup)
    assert result.get("metrics") == 1 and result.get("invalid") == 1
    row = store._conn.execute(
        "SELECT id FROM health_metrics ORDER BY id LIMIT 1"
    ).fetchone()
    store.update_metric(
        row["id"], value1=62, measured_at="2026-11-01T07:30"
    )
    # 空 measured_at 的非法行不能把合法行误判成缺行而补插旧副本
    assert store.import_data(backup).get("metrics") == 0
    count = store._conn.execute(
        "SELECT COUNT(*) AS n FROM health_metrics"
    ).fetchone()["n"]
    assert count == 1


def test_import_empty_times_rejected(store):
    store.save_profile("妈妈", {})
    result = store.import_data({
        "lab_results": [{"profile": "妈妈", "panel": "血常规",
                         "item": "血红蛋白", "value": 120, "unit": "g/L",
                         "tested_at": ""}],
        "vaccinations": [{"profile": "妈妈", "vaccine_name": "流感疫苗",
                          "dose_number": 1, "vaccinated_at": ""}],
        "visit_records": [{"profile": "妈妈", "diagnosis": "甲",
                           "visit_date": ""}],
    })
    assert result.get("invalid", 0) == 3
    for table in ("lab_results", "vaccinations", "visit_records"):
        count = store._conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}"
        ).fetchone()["n"]
        assert count == 0, table


def test_visit_profile_change_clears_cancelled_owner(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    visit = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-20",
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：甲", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit} 自动创建", owner_visit_id=visit,
    )["id"]
    store.update_appointment_partial(appointment, status="cancelled")
    store.update_visit_record(
        visit, "爸爸", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-20",
    )
    row = store._conn.execute(
        "SELECT profile, owner_visit_id, note FROM appointments"
        " WHERE id = ?", (appointment,),
    ).fetchone()
    assert row["profile"] == "妈妈"
    assert int(row["owner_visit_id"] or 0) == 0
    assert "由就诊记录 #" not in str(row["note"] or "")


# ---------------------------------------------------------------- 报表
def test_low_frequency_medication_insight(client_and_store):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    store.add_medication(
        "妈妈", "阿仑膦酸钠", "70mg", frequency="每周一次",
        started_at=_date(-10),
    )
    keys = [
        item["key"] for item in client.get(
            "/api/health/insights",
            params={"days": 30, "profile": "妈妈"},
        ).json()["insights"]
    ]
    family_keys = [
        item["key"] for item in client.get(
            "/api/health/insights", params={"days": 30}
        ).json()["insights"]
    ]
    assert "adherence" in keys and "all_clear" not in keys
    assert "adherence" in family_keys and "all_clear" not in family_keys


def test_target_alert_detail_uses_worst_reading(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "sleep", "value1": 7,
    })
    for offset, value in ((-2, 4), (-1, 6), (0, 7.5)):
        client.post("/api/metrics", json={
            "profile": "奶奶", "metric": "sleep", "value1": value,
            "measured_at": f"{_date(offset)}T07:00",
        })
    for params in ({"days": 7, "profile": "奶奶"}, {"days": 7}):
        detail = [
            item["detail"] for item in client.get(
                "/api/health/insights", params=params
            ).json()["insights"]
            if item["key"] == "metric_sleep"
        ]
        assert detail and "最低 4" in detail[0], params
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "bp", "value1": 140, "value2": 90,
    })
    for offset, sys_v, dia_v in ((-2, 160, 100), (0, 120, 80)):
        client.post("/api/metrics", json={
            "profile": "奶奶", "metric": "bp", "value1": sys_v,
            "value2": dia_v, "measured_at": f"{_date(offset)}T08:00",
        })
    detail = [
        item["detail"] for item in client.get(
            "/api/health/insights",
            params={"days": 7, "profile": "奶奶"},
        ).json()["insights"]
        if item["key"] == "metric_bp"
    ]
    assert detail and "160" in detail[0]


# ---------------------------------------------------------------- 记忆
def test_profile_memory_scope_is_salted(monkeypatch):
    from core import session_context

    monkeypatch.setenv("MEDIX_MEMORY_SCOPE_SALT", "unit-test-salt")
    monkeypatch.setattr(session_context, "_memory_scope_salt_cache", "")
    unsalted = hashlib.sha256("张三".encode("utf-8")).hexdigest()[:24]
    scope = session_context.profile_memory_scope("张三")
    other = session_context.profile_memory_scope("李四")
    assert scope.startswith("medix_profile_")
    assert "张三" not in scope
    assert scope != f"medix_profile_{unsalted}"
    assert scope != other
    assert session_context.profile_memory_scope("张三") == scope
    session_scope = session_context.session_memory_scope("custom-id")
    assert session_scope.startswith("medix_session_")
    assert "custom-id" not in session_scope


# ---------------------------------------------------------------- 静态
def test_reminder_endpoints_share_followup_lock():
    source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "def _with_visit_followup_lock(" in source
    markers = (
        "store.complete_reminder_with_repeat,",
        "store.reopen_reminder, reminder_id",
        "get_session_store().update_reminder,",
        "get_session_store().snooze_reminder,",
        "store.delete_reminder, reminder_id",
        "store.delete_reminders_bulk_with_ids,",
    )
    for marker in markers:
        index = source.index(marker)
        window = source[max(0, index - 200):index]
        assert "_with_visit_followup_lock" in window, marker


def test_frontend_round68_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function stripInflightQuestion(" in source
    # 第七十一轮起：落库判定与会话绑定，不再要求输入框仍包含主诉
    assert "notePersistedInflight(inflightQuestionText, sessionId)" in source
    assert "inflightQuestionText" in source
    # 第六十九轮起：无关草稿与在途主诉合并保留（不再只提示丢弃）
    assert "上次会诊未完成，主诉已合并到输入框" in source
    assert source.count("if (busy || sendInFlight) {") >= 4
    apply_block = source[
        source.index("async function applySelectedProfile"):
        source.index("function autoGrow")
    ]
    assert apply_block.count("if (busy || sendInFlight) {") >= 2
    seq_block = source[
        source.index("if (profileSelectionSeq !== sendProfileSeq) {"):
    ][:400]
    assert "restoreComposer(question);" in seq_block
    assert "preserveDraftProfile(sendProfile, question);" in seq_block
