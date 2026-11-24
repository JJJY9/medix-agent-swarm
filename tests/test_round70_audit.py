"""第七十轮审查：枚举后缀阳性/频次主诉/科普时间引用、家属用药过滤、
括号/逗号停药归属、同秒孪生恢复、空时间幽灵读数、CSV 判重列、共享
复诊链接归属、删除墓碑恢复链接、家庭逾期复诊/重度症状聚合、血压峰值
表述、SSRF 非全局地址与 DNS 固定、令牌预锁定 oracle、前端草稿清理。"""
import socket
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


def _freeze_instance(session_store, now=CREATED):
    session_store._now = lambda: now


# ---------------------------------------------------------------- 分诊
def test_enum_positive_suffix_modifiers():
    for text, label in (
        ("无发热、胸痛伴冷汗", "胸痛"),
        ("没有发烧、胸痛冒冷汗", "胸痛"),
        ("无发热、咯血约50毫升", "咯血"),
        ("无发热、咯血一口", "咯血"),
        ("无发热、胸痛伴冷汗、呼吸困难", "呼吸困难"),
    ):
        assert label in _red_flags(text)["matches"], text
    # 裸枚举仍整体否定
    assert _red_flags("无发热、咳嗽、胸痛")["level"] == "none"


def test_enum_occasional_and_or_connectors():
    for text in (
        "无发热、偶发胸痛",
        "无发热、偶尔胸痛",
        "无发热、胸痛偶发",
        "无发热、偶发咯血",
        "无发热或者有咯血",
        "无发热或有咯血",
    ):
        assert _red_flags(text)["level"] != "none", text


def test_negated_context_keeps_frequency_complaint():
    for text in (
        "今天没有胸痛，抽搐了两次是什么病",
        "我没有胸痛，抽搐了两次是什么病",
        "今天没有发烧，抽搐了两次是什么病",
        "今天没有胸痛，呼吸困难了两次是什么病",
    ):
        assert _red_flags(text)["level"] != "none", text
    # 无频次的纯定义问句仍不误报
    for text in (
        "今天没有胸痛，抽搐是什么病",
        "我没有胸痛，抽搐是什么病",
    ):
        assert _red_flags(text)["level"] == "none", text


def test_education_quote_with_time_words_not_current():
    for text in (
        "我最近在网上看到胸痛要马上就医",
        "我昨天看了一篇文章说咯血是肺癌",
        "我今天刷视频看到胸痛要马上就医",
        "我在网上看到胸痛要重视",
    ):
        assert _red_flags(text)["level"] == "none", text
    # 本人当前症状仍保留
    assert _red_flags("我今天胸痛")["level"] == "emergency"


def test_family_medication_not_attributed_to_patient():
    from core.medication_safety import check_medication_safety

    # 家属的远期停药不能把患者每天在服的华法林判为已停
    result = check_medication_safety(
        "我每天吃华法林，我爸以前也吃华法林，已经停药两年了，"
        "我能吃布洛芬吗",
        {},
    )
    assert result["level"] == "warning"
    assert any(
        "华法林" in alert["title"] for alert in result["alerts"]
    )
    # 家属本人的用药/过敏不算患者
    assert check_medication_safety(
        "我爸吃华法林，我能吃布洛芬吗", {}
    )["level"] == "none"
    assert check_medication_safety(
        "我妈对青霉素过敏，我能吃头孢吗", {}
    )["level"] == "none"
    # 家属转述让患者吃：保留患者本人用药
    assert check_medication_safety(
        "我爸让我吃华法林，我能吃布洛芬吗", {}
    )["level"] == "warning"


def test_parenthetical_and_comma_stop_attribution():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林没吃完，阿司匹林（肠溶片）停了半年了，今天头撞到门框",
        "华法林没吃完，阿司匹林，停了半年了，今天头撞到门框",
    ):
        assert "抗凝治疗期间头部外伤" in _red_flags(text)["matches"], text
    for text in (
        "华法林没吃完，阿司匹林（肠溶片）停了半年，能吃布洛芬吗",
        "华法林没吃完，阿司匹林，停了半年，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    # 本药自己的停药仍生效
    for text in (
        "阿司匹林没有吃完，已经停药一年了，能吃布洛芬吗",
        "阿司匹林没吃完，最近睡眠一般，胃口也还可以，大小便正常，"
        "已经停药一年了，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text


# ---------------------------------------------------------------- 导入
def test_symptom_edit_and_delete_sibling_restores_deleted(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        kept = source.add_symptom(
            "妈妈", "咳嗽", 3,
            recorded_at="2026-11-01T09:00", note="a",
        )
        removed = source.add_symptom(
            "妈妈", "咳嗽", 2,
            recorded_at="2026-11-01T08:00", note="b",
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        by_time = {
            item["recorded_at"]: item["id"]
            for item in target.list_symptoms("妈妈")
        }
        target.update_symptom_partial(
            by_time["2026-11-01T09:00"],
            recorded_at="2026-11-01T07:30",
        )
        assert target.delete_symptom(
            by_time["2026-11-01T08:00"]
        ) is True
        target.import_data(backup)
        rows = [
            (item["recorded_at"], item["severity"], item["note"])
            for item in target.list_symptoms("妈妈")
        ]
        assert ("2026-11-01T07:30", 3, "a") in rows
        assert ("2026-11-01T08:00", 2, "b") in rows
        assert target.import_data(backup)["symptoms"] == 0
    finally:
        target.close()


def test_group_full_edited_rows_do_not_reinsert_old_values(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_metric(
            "妈妈", "weight", 60, measured_at="2026-11-01T08:00"
        )
        source.add_metric(
            "妈妈", "weight", 61, measured_at="2026-11-01T09:00"
        )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        by_time = {
            item["measured_at"]: item["id"]
            for item in target.list_metrics("妈妈")
        }
        target.update_metric(
            by_time["2026-11-01T08:00"],
            measured_at="2026-10-01T08:00",
        )
        target.update_metric(
            by_time["2026-11-01T09:00"],
            measured_at="2026-10-01T09:00",
        )
        target.import_data(backup)
        rows = sorted(
            (item["measured_at"], item["value1"])
            for item in target.list_metrics("妈妈")
        )
        assert rows == [
            ("2026-10-01T08:00", 60.0),
            ("2026-10-01T09:00", 61.0),
        ]
        assert target.import_data(backup)["metrics"] == 0
    finally:
        target.close()


def test_empty_time_rows_not_counted_as_twins(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        source.add_metric(
            "妈妈", "weight", 60, measured_at="2026-11-01T08:00"
        )
        source.add_metric(
            "妈妈", "weight", 61, measured_at="2026-11-01T09:00"
        )
        source.add_metric("妈妈", "weight", 60, measured_at="")
        backup = source.export_data()
    finally:
        source.close()
    backup.pop("checksum", None)
    # 手工制造非法空时间行（导出时会被规范化为当前时间）
    for row in backup["health_metrics"]:
        if row["measured_at"] not in (
            "2026-11-01T08:00", "2026-11-01T09:00",
        ):
            row["measured_at"] = ""
            break

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        first = target.import_data(backup)
        assert first["metrics"] == 2
        assert first["invalid"] == 1
        by_time = {
            item["measured_at"]: item["id"]
            for item in target.list_metrics("妈妈")
        }
        target.update_metric(
            by_time["2026-11-01T08:00"],
            measured_at="2026-11-01T07:30",
        )
        assert target.import_data(backup)["metrics"] == 0
        rows = sorted(
            item["measured_at"] for item in target.list_metrics("妈妈")
        )
        assert rows == ["2026-11-01T07:30", "2026-11-01T09:00"]
    finally:
        target.close()


def test_csv_import_keeps_semantically_distinct_rows(store):
    store.save_profile("妈妈", {})
    assert store.import_data({"medications": [
        {"profile": "妈妈", "name": "药A", "dose": "1片",
         "frequency": "每日", "note": "早饭后", "active": True,
         "started_at": "2026-01-01"},
        {"profile": "妈妈", "name": "药A", "dose": "1片",
         "frequency": "每日", "note": "晚饭后", "active": True,
         "started_at": "2026-01-01"},
    ]})["medications"] == 2
    assert store.import_data({"reminders": [
        {"profile": "妈妈", "title": "吃药", "due_at": "2026-12-01T09:00",
         "note": "", "advance_days": 1, "repeat_days": 0,
         "repeat_until": ""},
        {"profile": "妈妈", "title": "吃药", "due_at": "2026-12-01T09:00",
         "note": "", "advance_days": 3, "repeat_days": 0,
         "repeat_until": ""},
    ]})["reminders"] == 2
    assert store.import_data({"allergies": [
        {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
         "reaction": "皮疹", "severity": "severe", "active": True,
         "noted_at": "2026-01-01", "note": "儿童期"},
        {"profile": "妈妈", "substance": "青霉素", "kind": "drug",
         "reaction": "皮疹", "severity": "severe", "active": True,
         "noted_at": "2026-01-01", "note": "近期复查确认"},
    ]})["allergies"] == 2


def test_shared_visit_link_restores_owner(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    _freeze_instance(source)
    try:
        source.save_profile("妈妈", {})
        owner_visit = source.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2099-12-14",
        )["id"]
        other_visit = source.add_visit_record(
            "妈妈", visit_date="2026-11-02", diagnosis="糖尿病",
            next_followup_date="2099-12-14",
        )["id"]
        appointment = source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2099-12-14T09:00",
            note=f"由就诊记录 #{owner_visit} 自动创建",
            owner_visit_id=owner_visit,
        )["id"]
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = ?"
                " WHERE id IN (?, ?)",
                (appointment, owner_visit, other_visit),
            )
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    _freeze_instance(target, "2026-11-17T10:00:00")
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        appointments = [
            item for item in target.list_appointments("妈妈", None)
            if item["title"].startswith("复诊")
        ]
        assert len(appointments) == 1
        appointment = appointments[0]
        assert int(appointment["owner_visit_id"] or 0) != 0
        linked = [
            item for item in target.list_visit_records("妈妈")
            if int(item["followup_appointment_id"] or 0)
            == appointment["id"]
        ]
        assert len(linked) == 1
        assert linked[0]["diagnosis"] == "高血压"
    finally:
        target.close()


def test_legacy_shared_link_schedule_followup(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    day = _date(10)
    owner_visit = store.add_visit_record(
        "妈妈", visit_date=_date(-5), diagnosis="高血压",
        next_followup_date=day,
    )["id"]
    other_visit = store.add_visit_record(
        "妈妈", visit_date=_date(-4), diagnosis="糖尿病",
        next_followup_date=day,
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at=day + "T09:00",
        note=f"由就诊记录 #{owner_visit} 自动创建",
        owner_visit_id=owner_visit,
    )["id"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?"
            " WHERE id = ?",
            (appointment, other_visit),
        )
    result = _schedule_followup_sync(store, owner_visit)
    assert result["appointment_created"] is False
    assert int(
        store.get_visit_record(owner_visit)["followup_appointment_id"] or 0
    ) == appointment
    assert int(
        store.get_visit_record(other_visit)["followup_appointment_id"] or 0
    ) == 0


def test_deleted_reminder_tombstone_still_links_visit(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date=_date(-5), diagnosis="甲",
        next_followup_date=_date(10),
    )["id"]
    scheduled = _schedule_followup_sync(store, visit)
    appointment = scheduled["appointment"]["id"]
    reminder = scheduled["reminder"]["id"]
    assert store.delete_reminder(reminder) is True
    store.update_appointment_partial(appointment, status="cancelled")
    store.update_appointment_partial(appointment, status="upcoming")
    assert store.list_reminders(False, "妈妈") == []
    refreshed = store.get_visit_record(visit)
    assert int(refreshed["followup_appointment_id"] or 0) == appointment
    assert int(refreshed["followup_reminder_id"] or 0) == 0
    assert refreshed["followup_scheduled"] is True


# ---------------------------------------------------------------- 报表
def test_family_overdue_followups_list_all_members(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    store.add_visit_record(
        "妈妈", visit_date=_date(-20), diagnosis="高血压",
        next_followup_date=_date(-7),
    )
    store.add_visit_record(
        "爸爸", visit_date=_date(-18), diagnosis="糖尿病",
        next_followup_date=_date(-5),
    )
    data = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()
    overdue = [
        item for item in data["insights"]
        if item["key"] == "followup_overdue"
    ]
    assert overdue
    assert "2 项复诊已逾期" in overdue[0]["title"]
    assert "妈妈" in overdue[0]["detail"]
    assert "爸爸" in overdue[0]["detail"]


def test_family_severe_symptoms_cover_each_member(client_and_store):
    client, store = client_and_store
    store.save_profile("P1", {})
    store.save_profile("P2", {})
    store.add_symptom(
        "P1", "胸痛", 8,
        recorded_at=(datetime.now() - timedelta(days=6)).isoformat(),
    )
    store.add_symptom(
        "P1", "头晕", 8,
        recorded_at=(datetime.now() - timedelta(days=5)).isoformat(),
    )
    store.add_symptom(
        "P2", "呼吸困难", 7,
        recorded_at=(datetime.now() - timedelta(days=4)).isoformat(),
    )
    store.add_symptom(
        "", "咳嗽", 7,
        recorded_at=(datetime.now() - timedelta(days=3)).isoformat(),
    )
    data = client.get(
        "/api/health/insights", params={"days": 7}
    ).json()
    severe = [
        item for item in data["insights"]
        if item["key"] == "symptoms_severe"
    ]
    assert severe
    detail = severe[0]["detail"]
    assert "P1" in detail and "P2" in detail
    assert "咳嗽" in detail


def test_bp_single_info_does_not_claim_merged_reading(client_and_store):
    client, store = client_and_store
    store.save_profile("奶奶", {})
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("奶奶", "bp", 145, 80, measured_at=f"{today}T08:00")
    store.add_metric("奶奶", "bp", 130, 95, measured_at=f"{today}T09:00")
    data = client.get(
        "/api/health/insights",
        params={"days": 7, "profile": "奶奶"},
    ).json()
    single = [
        item for item in data["insights"]
        if item["key"] == "metric_bp_single"
    ]
    assert single
    text = single[0]["title"] + single[0]["detail"]
    assert "收缩压 145" in text
    assert "舒张压 95" in text
    # 不能把两次记录拼成一条并不存在的 145/95 读数
    assert "145/95" not in text
    assert "单次最高读数" not in text
    assert "可能来自不同测量" in text


# ---------------------------------------------------------------- 安全
def test_fetch_target_rejects_non_global_and_pins(monkeypatch):
    import research.web_search as web_search

    def fake_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (
            "100.100.100.200", 0,
        ))]

    monkeypatch.setattr(web_search.socket, "getaddrinfo", fake_getaddrinfo)
    with pytest.raises(web_search.UnsafeFetchTarget):
        web_search._validate_fetch_target(
            "http://metadata.example/latest/meta-data/"
        )
    with pytest.raises(web_search.UnsafeFetchTarget):
        web_search._pin_fetch_target("http://metadata.example/")

    def public_getaddrinfo(host, port, *args, **kwargs):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (
            "93.184.216.34", 0,
        ))]

    monkeypatch.setattr(
        web_search.socket, "getaddrinfo", public_getaddrinfo
    )
    pinned, headers, extensions = web_search._pin_fetch_target(
        "https://example.com/a?b=1"
    )
    assert pinned == "https://93.184.216.34/a?b=1"
    assert headers["Host"] == "example.com"
    assert extensions["sni_hostname"] == "example.com"


# ---------------------------------------------------------------- 前端
def test_frontend_draft_cleanup_and_inflight_reconcile():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "inflightPersistedText" in source
    # send() 成功清理按包含式移除本轮主诉，保留后续草稿
    finally_block = source[
        source.index("if (sendPersisted) {"):
    ][:1200]
    assert "stripInflightQuestion(" in finally_block
    assert "leftover === String(question || '').trim()" not in source
    # 对账确认落库后无条件清在途记录（输入框被改写也要清）
    first_expected = source.index("if (expectedQuestion) {")
    reconcile = source[
        source.index("if (expectedQuestion) {", first_expected + 1):
    ][:1400]
    assert reconcile.index(
        "safeSessionRemove(INFLIGHT_QUESTION_KEY)"
    ) < reconcile.index("if (remaining !== null)")
