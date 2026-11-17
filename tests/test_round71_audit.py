"""第七十一轮审查：近期「开始不吃」/时段定义问句/科普引用回归、家属
与并列主语用药、括号停药归属、停掉近期停药、导入配对评分守卫、非法
行孪生、旧版共享复诊预约、认证共享键阈值、记录天数/周报环比、伪档案
校验、向量库权限、前端在途标记与会话/草稿隔离。"""
import os
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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


def _freeze_instance(session_store, now=CREATED):
    session_store._now = lambda: now


# ---------------------------------------------------------------- 分诊
def test_recent_started_not_eating_keeps_anticoagulation():
    for text in (
        "我昨天开始不吃华法林了，今天头撞到门框",
        "华法林昨天开始不吃了，今天头撞到门框",
        "华法林刚才不吃了，头撞到门框",
        "华法林今天开始不吃了，头撞到门框",
        "华法林前天开始不吃了，今天头撞到门框",
    ):
        assert "抗凝治疗期间头部外伤" in _red_flags(text)["matches"], text
    # 裸「不吃了」仍按已停药（既有契约）
    assert _red_flags("华法林我不吃了，头撞到门框")["level"] == "none"


def test_day_period_definition_questions_keep_red_flags():
    for text, label in (
        ("昨晚胸痛是什么病", "胸痛"),
        ("今早咯血是什么病", "咯血"),
        ("凌晨胸痛是什么病", "胸痛"),
        ("刚刚胸痛是什么病", "胸痛"),
        ("今晨呼吸困难是什么病", "呼吸困难"),
        ("傍晚胸痛是什么病", "胸痛"),
    ):
        assert label in _red_flags(text)["matches"], text
    # 纯术语仍是科普
    assert _red_flags("什么是胸痛")["level"] == "none"


def test_education_quote_keeps_patient_course_before_quote():
    for text in (
        "我这几天胸痛在网上查了说是心梗前兆",
        "我最近老是胸痛在网上查了说没事",
    ):
        assert "胸痛" in _red_flags(text)["matches"], text
    # 引用文章里的偶发描述仍是科普
    for text in (
        "我看了篇文章说偶尔胸痛没事",
        "我在网上看到偶见咯血不用紧张",
        "我最近在网上看到胸痛要马上就医",
    ):
        assert _red_flags(text)["level"] == "none", text


# ---------------------------------------------------------------- 用药
def test_family_filter_keeps_parallel_and_child_patient():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "我和我爸都吃华法林，能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "我儿子在吃华法林，他能吃布洛芬吗", {"age": "3岁"}
    )["level"] == "warning"
    assert check_medication_safety(
        "我儿子对阿司匹林过敏，他能吃布洛芬吗", {"age": "2岁"}
    )["level"] == "warning"
    # 家属本人用药/过敏仍排除
    assert check_medication_safety(
        "我爸吃华法林，我能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "我妈对青霉素过敏，我能吃头孢吗", {}
    )["level"] == "none"


def test_family_relay_verbs_and_english():
    from core.medication_safety import check_medication_safety

    for text in (
        "我妈妈建议我吃华法林，能吃布洛芬吗",
        "我妈叮嘱我每天吃华法林，能吃布洛芬吗",
        "我爸嘱咐我吃华法林，能吃布洛芬吗",
        "My father told me to take warfarin, can I take ibuprofen?",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    assert check_medication_safety(
        "My father takes warfarin, can I take ibuprofen?", {}
    )["level"] == "none"


def test_stopped_verb_variants_recent_stop_window():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林昨天停掉，能吃布洛芬吗",
        "华法林停掉两天了，能吃布洛芬吗",
        "我昨天把华法林停掉了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


def test_parenthetical_stop_attribution_kept():
    from core.medication_safety import check_medication_safety

    assert "抗凝治疗期间头部外伤" in _red_flags(
        "华法林没吃完，阿司匹林（肠溶片）停了半年了，今天头撞到门框"
    )["matches"]
    assert check_medication_safety(
        "华法林没吃完，阿司匹林（肠溶片）停了半年，能吃布洛芬吗", {}
    )["level"] == "warning"
    assert check_medication_safety(
        "阿司匹林没有吃完，已经停药一年了，能吃布洛芬吗", {}
    )["level"] == "none"


# ---------------------------------------------------------------- 导入
def test_group_full_scoring_requires_edit_evidence(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_metric(
            "妈妈", "weight", 60,
            measured_at="2026-11-01T08:00", note="x",
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        for index in range(4):
            target.add_metric(
                "妈妈", "steps", 1000 + index,
                measured_at=f"2026-09-0{index + 1}T08:00",
            )
        with target._lock, target._conn:
            for value, note in ((65, "x"), (70, "z")):
                target._conn.execute(
                    "INSERT INTO health_metrics(profile, metric, value1,"
                    " value2, measured_at, note, context, created_at,"
                    " updated_at) VALUES (?, ?, ?, NULL, ?, ?, '', ?, ?)",
                    (
                        "妈妈", "weight", value, "2026-10-05T10:00",
                        note, CREATED, CREATED,
                    ),
                )
        assert target.import_data(backup)["metrics"] == 1
        rows = [
            item for item in target.list_metrics("妈妈")
            if item["metric"] == "weight"
        ]
        assert any(
            item["measured_at"] == "2026-11-01T08:00"
            and item["value1"] == 60.0
            for item in rows
        )
        assert target.import_data(backup)["metrics"] == 0
    finally:
        target.close()


def test_invalid_sibling_rows_not_in_twin_meta(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_symptom(
            "妈妈", "咳嗽", 3,
            recorded_at="2026-11-01T08:00", note="a",
        )
        source.add_symptom("妈妈", "咳嗽", 4, recorded_at="", note="b")
        backup = source.export_data()
    finally:
        source.close()
    backup.pop("checksum", None)
    for row in backup["symptoms"]:
        if row["severity"] == 4 and row["recorded_at"]:
            row["recorded_at"] = ""

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        for index in range(3):
            target.add_symptom(
                "妈妈", f"占位{index}", 1,
                recorded_at=f"2026-10-0{index + 1}T08:00",
            )
        first = target.import_data(backup)
        assert first["symptoms"] == 1
        assert first["invalid"] == 1
        legal = next(
            item for item in target.list_symptoms("妈妈")
            if item["symptom"] == "咳嗽"
        )
        target.update_symptom_partial(legal["id"], severity=4, note="b")
        assert target.import_data(backup)["symptoms"] == 0
        rows = [
            (item["severity"], item["note"])
            for item in target.list_symptoms("妈妈")
            if item["symptom"] == "咳嗽"
        ]
        assert (4, "b") in rows
    finally:
        target.close()


def test_legacy_shared_followup_appointment(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    day = _date(10)
    visit_a = store.add_visit_record(
        "妈妈", visit_date=_date(-5), diagnosis="高血压",
        next_followup_date=day,
    )["id"]
    visit_b = store.add_visit_record(
        "妈妈", visit_date=_date(-5), diagnosis="糖尿病",
        next_followup_date=day,
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at=day + "T09:00",
    )["id"]
    reminder = store.add_reminder(
        "复诊提醒", day, profile="妈妈",
        appointment_id=appointment,
    )["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?,"
            " followup_reminder_id = ? WHERE id IN (?, ?)",
            (appointment, reminder, visit_a, visit_b),
        )
    # 两条就诊都能完成安排复诊，不再误报 404
    first = _schedule_followup_sync(store, visit_a)
    assert first["appointment_created"] is False
    assert int(
        store.get_visit_record(visit_a)["followup_appointment_id"] or 0
    ) == appointment
    second = _schedule_followup_sync(store, visit_b)
    assert second["appointment"] is not None
    # 编辑另一条就诊的复诊日期不会删掉预约与提醒
    store.update_visit_record_partial(visit_b, next_followup_date=_date(20))
    assert store.get_appointment(appointment) is not None
    assert store.list_reminders(False, "妈妈")


# ---------------------------------------------------------------- 认证
def test_shared_spoof_bucket_uses_loopback_threshold(
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
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_PROXY_SINGLE_HOP", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 58010),
    )
    try:
        headers = {"X-Forwarded-For": "8.8.8.8"}
        for _ in range(10):
            assert client.get(
                "/api/sessions",
                headers={"X-Medix-Token": "wrong", **headers},
            ).status_code == 401
        correct = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token, **headers},
        )
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_proxy_proto_shared_key_uses_loopback_threshold(
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
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 58011),
    )
    try:
        headers = {"X-Forwarded-Proto": "https"}
        for _ in range(10):
            assert client.get(
                "/api/sessions",
                headers={"X-Medix-Token": "wrong", **headers},
            ).status_code == 401
        correct = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token, **headers},
        )
        assert correct.status_code == 200
    finally:
        session_store.close()


def test_ipv6_aggregate_not_hard_prelocked(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "round71-secret")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(60, window=300),
    )
    try:
        attacker = TestClient(
            api_server.app, base_url="http://[2001:db8:5a::1]",
            client=("2001:db8:5a::a1", 58012),
        )
        for _ in range(10):
            attacker.get(
                "/api/sessions", headers={"X-Medix-Token": "wrong"}
            )
        neighbor = TestClient(
            api_server.app, base_url="http://[2001:db8:5a::2]",
            client=("2001:db8:5a::b2", 58013),
        )
        assert neighbor.get(
            "/api/sessions",
            headers={"X-Medix-Token": "round71-secret"},
        ).status_code == 200
    finally:
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_recorded_days_expand_session_messages(store):
    store.save_profile("妈妈", {})
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile, tags) VALUES (?,?,?,?,?,?)",
            ("s-multi", "跨天", "2026-11-15T09:00:00",
             "2026-11-16T09:00:00", "妈妈", "[]"),
        )
        for mid, role, content, created in (
            (1, "user", "问题一", "2026-11-15T09:00:00"),
            (2, "assistant", "回答一", "2026-11-15T09:01:00"),
            (3, "user", "问题二", "2026-11-16T09:00:00"),
            (4, "assistant", "回答二", "2026-11-16T09:01:00"),
        ):
            store._conn.execute(
                "INSERT INTO messages(session_id, id, role, content,"
                " metadata, created_at) VALUES (?,?,?,?,?,?)",
                ("s-multi", mid, role, content, "{}", created),
            )
    days = store.recorded_days("妈妈", 30)
    assert "2026-11-15" in days
    assert "2026-11-16" in days
    by_profile = store.recorded_days_by_profile(30)
    assert "2026-11-15" in by_profile.get("妈妈", [])


def test_weekly_previous_counts_cross_week_session(
    monkeypatch, store
):
    import asyncio

    import api_server
    import memory

    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    store.save_profile("妈妈", {})
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile, tags) VALUES (?,?,?,?,?,?)",
            ("s-old", "长期会话", "2026-10-28T09:00:00",
             "2026-11-16T09:00:00", "妈妈", "[]"),
        )
        for mid, role, content, created in (
            (1, "user", "上期问题", "2026-11-07T09:00:00"),
            (2, "assistant", "上期回答", "2026-11-07T09:01:00"),
            (3, "user", "本期问题", "2026-11-16T09:00:00"),
            (4, "assistant", "本期回答", "2026-11-16T09:01:00"),
        ):
            store._conn.execute(
                "INSERT INTO messages(session_id, id, role, content,"
                " metadata, created_at) VALUES (?,?,?,?,?,?)",
                ("s-old", mid, role, content, "{}", created),
            )
    report = asyncio.run(
        api_server._weekly_report_impl("妈妈", 7, 100, True)
    )
    consultations = report.get("consultations") or {}
    assert consultations.get("prev_count") == 1


def test_profile_name_rejected_on_records_and_import(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    session_store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    try:
        client = TestClient(api_server.app)
        response = client.post("/api/symptoms", json={
            "profile": "妈妈/爸爸", "symptom": "咳嗽", "severity": 3,
        })
        assert response.status_code == 400
        assert session_store.list_symptoms("妈妈/爸爸") == []
        imported = session_store.import_data({"symptoms": [{
            "profile": "妈妈/爸爸", "symptom": "咳嗽", "severity": 3,
            "recorded_at": "2026-11-01T08:00", "note": "",
            "created_at": "2026-06-01T08:00:00",
        }]})
        assert imported["symptoms"] == 0
        assert imported["invalid"] == 1
        names = [
            item["name"] for item in session_store.list_profiles()
        ]
        assert "妈妈/爸爸" not in names
    finally:
        session_store.close()


# ---------------------------------------------------------------- 隐私
def test_milvus_index_permissions_tightened(tmp_path):
    # 离线 CI 不安装 sentence_transformers：不导入知识库模块，按源码
    # 断言收紧调用，并直接验证 restrict_permissions 的落盘效果
    source = (
        ROOT / "knowledge" / "milvus_kb.py"
    ).read_text(encoding="utf-8")
    assert "restrict_permissions(Path(self.db_path).parent, 0o700)" in source
    assert "_restrict_index_permissions()" in source
    assert 'glob(base.name + "*")' in source
    from core.fs_permissions import restrict_permissions

    db_path = tmp_path / "milvus_lite.db"
    db_path.write_bytes(b"x")
    restrict_permissions(db_path, 0o600)
    if os.name != "nt":
        assert (db_path.stat().st_mode & 0o777) == 0o600


# ---------------------------------------------------------------- 前端
def test_frontend_inflight_marker_and_send_snapshot():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 在途落库标记绑定会话、按提交来源消费、对账后一次性生效
    assert "inflightPersistedSession" in source
    assert "notePersistedInflight(inflightQuestionText, sessionId)" in source
    assert "composerSubmit && inflightPersistedText" in source
    assert "startsWith(persistedText + '\\n')" in source
    assert "clearInflightPersisted();" in source
    # 成功清理只处理失败分支恢复时的输入快照，不按任意子串改写新草稿
    finally_block = source[
        source.index("if (sendPersisted) {"):
    ][:1400]
    assert "lastRestoredComposerText" in finally_block
    assert "els.input.value === lastRestoredComposerText" in finally_block
    # 新建会诊在途时提交先等待建会话完成
    assert "creatingSessionPromise" in source
    assert "await creatingSessionPromise" in source
    assert "send(q, true);" in source
