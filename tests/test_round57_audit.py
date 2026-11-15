"""第五十七轮审查：漏服自然语序、逗号定义问句、未来停药、过敏陈述、
否定用药史、续用主语、孪生尾字段归属、同秒同主键就诊、提醒改名、
按钮重建提醒、重复导入标记、闸门退避/直通/单源、回环桶键、家庭
关注口径、库存提示、空白成员卡。"""
import asyncio
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
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
        "MEDIX_ALLOW_INSECURE_LAN",
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
def test_missed_dose_natural_order_keeps_anticoagulant():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我今天没吃华法林，头磕到桌角",
        "我昨天没吃华法林，今天头磕到桌角",
        "我今早没吃华法林，头磕到桌角",
        "我前天没吃华法林，头磕到桌角",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    # 药名前置语序保持原契约
    result = detect_red_flags(
        patient_complaint_text("华法林今天没吃，头磕到桌角"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]


def test_comma_definition_question_keeps_second_red_flag():
    from core.triage import detect_red_flags, patient_complaint_text

    cases = {
        "手脚发麻，说话不清是什么病": "言语不清",
        "胸口闷痛，呼吸困难是什么病": "呼吸困难",
        "后背疼，呼吸困难是什么病": "呼吸困难",
        "浑身没劲，说话不清是什么病": "言语不清",
    }
    for text, label in cases.items():
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    # 纯科普铺垫不建立患者语境
    assert patient_complaint_text("我在网上查了资料，胸痛是什么病") == (
        "我在网上查了资料"
    )


def test_future_stop_natural_order_keeps_current_use():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "下周就不吃华法林了，今天头撞到门框",
        "华法林下周就不吃了，今天头撞到门框",
        "明天就不吃华法林了，今天头撞到门框",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    for text in (
        "下周就不吃华法林了，现在能吃布洛芬吗",
        "华法林下周就不吃了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


def test_allergy_statement_not_active_medication():
    from core.triage import detect_red_flags, patient_complaint_text

    for history in ("肝素过敏", "对华法林过敏", "华法林过敏"):
        result = detect_red_flags("今天撞到头", {"history": history})
        assert result["level"] == "none", history
    result = detect_red_flags(
        "今天发烧38.5度", {"history": "对甲氨蝶呤过敏"}
    )
    assert result["level"] == "none"
    # 「抗过敏药」和已有过敏史口径保持不变
    result = detect_red_flags(
        patient_complaint_text("我在吃华法林和抗过敏药，今天撞到头"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = detect_red_flags("今天撞到头", {"history": "有抗凝药过敏史"})
    assert result["level"] == "none"


def test_negated_medication_history_with_completed_aspect():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags

    for history in (
        "没用过华法林",
        "没服过华法林",
        "没吃过华法林",
        "未使用过华法林",
    ):
        result = detect_red_flags("头撞到门框", {"history": history})
        assert result["level"] == "none", history
    for history in (
        "没做过化疗",
        "未接受过化疗",
        "没有进行过放疗",
        "没得过艾滋病",
        "没有感染过HIV",
    ):
        result = detect_red_flags("今天发烧了", {"history": history})
        assert result["level"] == "none", history
    result = check_medication_safety(
        "我没用过华法林，现在能吃布洛芬吗", {}
    )
    assert result["level"] != "warning"
    # 当前用药的对照
    result = detect_red_flags("头撞到门框", {"history": "在吃华法林"})
    assert "抗凝治疗期间头部外伤" in result["matches"]


def test_past_medication_restart_scoped_to_patient_and_drug():
    from core.triage import detect_red_flags

    for history in (
        "以前吃华法林，我爸还在吃华法林",
        "以前吃华法林，我妈还在服用华法林",
        "以前吃华法林，现在还在吃阿司匹林",
    ):
        result = detect_red_flags("今天撞到头", {"history": history})
        assert result["level"] == "none", history
    # 无宾语的续用仍表示同一药物
    result = detect_red_flags(
        "今天撞到头", {"history": "以前吃华法林，现在还在吃"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 续用宾语仍是同一药名
    result = detect_red_flags(
        "今天撞到头", {"history": "以前吃华法林，现在还在吃华法林"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]


# ---------------------------------------------------------------- 导入
def _vaccination_twin_backup():
    return {"vaccinations": [
        {"id": 5, "profile": "妈妈", "vaccine_name": "流感疫苗",
         "dose_number": 2, "vaccinated_at": "2025-10-02",
         "next_due": "2027-10-01", "note": "",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "vaccine_name": "流感疫苗",
         "dose_number": 2, "vaccinated_at": "2025-10-02",
         "next_due": "2026-10-01", "note": "",
         "created_at": CREATED, "updated_at": CREATED},
    ]}


def test_twin_step1_claims_wrong_sibling_restores_deleted(store):
    store.save_profile("妈妈", {"sex": "女"})
    for index in range(4):
        store.add_vaccination(
            "妈妈", f"占位{index}", 1, vaccinated_at="2024-01-01",
        )
    backup = _vaccination_twin_backup()
    store.import_data(backup)
    due_map = {
        str(item.get("next_due") or "")[:10]: item["id"]
        for item in store.list_vaccinations("妈妈", limit=100)
        if item["vaccine_name"] == "流感疫苗"
    }
    assert due_map == {"2027-10-01": 5, "2026-10-01": 6}
    # 删除靠前（raw5）的那条后重导：被删记录必须补回，不能复制存活行
    assert store.delete_vaccination(due_map["2027-10-01"]) is True
    result = store.import_data(backup)
    assert result["vaccinations"] == 1
    rows = [
        item for item in store.list_vaccinations("妈妈", limit=100)
        if item["vaccine_name"] == "流感疫苗"
    ]
    dues = sorted(str(item.get("next_due") or "")[:10] for item in rows)
    assert dues == ["2026-10-01", "2027-10-01"]
    # 重复导入保持幂等
    again = store.import_data(backup)
    assert again["vaccinations"] == 0
    assert len(store.list_vaccinations("妈妈", limit=100)) == 6


def test_exported_edited_twin_restore(tmp_path, monkeypatch):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        source.add_appointment(
            "妈妈", "复诊", "心内科",
            scheduled_at="2026-12-14T09:00",
        )
        second = source.add_appointment(
            "妈妈", "复诊", "心内科",
            scheduled_at="2026-12-14T09:00",
        )
        source.update_appointment_partial(
            second["id"], status="completed", note="已完成备注",
        )
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.add_appointment(
            "妈妈", "牙科", scheduled_at="2026-11-01T09:00",
        )
        target.add_appointment(
            "妈妈", "眼科", scheduled_at="2026-11-02T09:00",
        )
        target.import_data(backup)
        twins = [
            item for item in target.list_appointments("妈妈", None)
            if item["title"] == "复诊"
        ]
        assert len(twins) == 2
        upcoming = next(
            item for item in twins if item["status"] == "upcoming"
        )
        assert target.delete_appointment(upcoming["id"]) is True
        clock["now"] = "2026-11-15T10:00:10"
        result = target.import_data(backup)
        assert result["appointments"] == 1
        twins = [
            item for item in target.list_appointments("妈妈", None)
            if item["title"] == "复诊"
        ]
        statuses = sorted(item["status"] for item in twins)
        assert statuses == ["completed", "upcoming"]
        completed = next(
            item for item in twins if item["status"] == "completed"
        )
        assert completed["note"] == "已完成备注"
        assert target.import_data(backup)["appointments"] == 0
    finally:
        target.close()


def test_same_second_visit_import_keeps_both(tmp_path, monkeypatch):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-05-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        appointment = source.add_appointment(
            "妈妈", "复诊", "心内科", scheduled_at="2026-12-14T09:00",
        )
        reminder = source.add_reminder(
            "复诊提醒", "2026-12-13T09:00", profile="妈妈",
        )
        source._conn.execute(
            "UPDATE appointments SET owner_visit_id = ?, note = ?"
            " WHERE id = ?",
            (
                visit["id"],
                f"由就诊记录 #{visit['id']} 自动创建",
                appointment["id"],
            ),
        )
        source._conn.execute(
            "UPDATE visit_records SET followup_appointment_id = ?,"
            " followup_reminder_id = ? WHERE id = ?",
            (appointment["id"], reminder["id"], visit["id"]),
        )
        source._conn.execute(
            "UPDATE reminders SET appointment_id = ? WHERE id = ?",
            (appointment["id"], reminder["id"]),
        )
        source._conn.commit()
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        placeholder = target.add_visit_record(
            "妈妈", visit_date="2026-04-01", diagnosis="占位",
        )
        clock["now"] = "2026-11-15T10:00:10"
        result = target.import_data(backup)
        assert result["visit_records"] == 1
        visits = target.list_visit_records("妈妈")
        assert len(visits) == 2
        imported = next(
            item for item in visits if item["diagnosis"] == "高血压"
        )
        kept = next(
            item for item in visits
            if item["id"] == placeholder["id"]
        )
        assert kept["diagnosis"] == "占位"
        assert int(imported["followup_appointment_id"] or 0) > 0
        appointments = target.list_appointments("妈妈", None)
        assert len(appointments) == 1
        assert int(appointments[0]["owner_visit_id"] or 0) == imported["id"]
        reminders = target.list_reminders(include_done=True)
        assert len(reminders) == 1
    finally:
        target.close()


def test_renamed_reminder_restore_no_duplicate(tmp_path, monkeypatch):
    from memory.session_store import SessionStore

    _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        placeholder = source.add_reminder(
            "占位", "2026-11-01T09:00", profile="妈妈",
        )
        source.delete_reminder(placeholder["id"])
        source.add_reminder("吃降压药", "2026-12-01T09:00", profile="妈妈")
        source.add_reminder("复查血压", "2026-12-02T09:00", profile="妈妈")
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        rows = target.list_reminders(include_done=True)
        first = next(item for item in rows if item["title"] == "吃降压药")
        target.update_reminder(first["id"], title="吃降压药（新）")
        result = target.import_data(backup)
        assert result["reminders"] == 0
        rows = target.list_reminders(include_done=True)
        titles = sorted(item["title"] for item in rows)
        assert titles == ["吃降压药（新）", "复查血压"]
    finally:
        target.close()


def test_twin_edited_survivor_attributed_to_sibling(store):
    store.save_profile("妈妈", {"sex": "女"})
    backup = {"medications": [
        {"id": 5, "profile": "妈妈", "name": "阿司匹林", "dose": "100mg",
         "frequency": "每日一次", "note": "服用中", "active": True,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 6, "profile": "妈妈", "name": "阿司匹林", "dose": "100mg",
         "frequency": "每日一次", "note": "已停药", "active": False,
         "stopped_at": "2026-05-01", "stop_reason": "医生建议",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    active_row = next(item for item in rows if item["active"])
    stopped_row = next(item for item in rows if not item["active"])
    # 被删除的是靠前（在用）记录，存活并编辑的是靠后（停药）记录
    store.update_medication(stopped_row["id"], note="本地改备注")
    store.delete_medication(active_row["id"])
    store.import_data(backup)
    rows = [
        item for item in store.list_medications(limit=10)
        if item["name"] == "阿司匹林"
    ]
    assert len(rows) == 2
    active = [item for item in rows if item["active"]]
    assert len(active) == 1
    assert active[0]["note"] == "服用中"
    stopped = [item for item in rows if not item["active"]]
    assert len(stopped) == 1
    assert stopped[0]["note"] == "本地改备注"


# ---------------------------------------------------------------- 复诊
def test_reschedule_rebuilds_reminder_after_button_recreate(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    linked = store.get_visit_record(visit["id"])
    reminder_id = int(linked.get("followup_reminder_id") or 0)
    assert reminder_id > 0
    # 用户删除提醒（写墓碑、清空就诊提醒链接）
    clock["now"] = "2026-11-15T10:00:05"
    assert store.delete_reminder(reminder_id) is True
    # 通过预约的『提醒』按钮重建，并完成它
    clock["now"] = "2026-11-15T10:00:10"
    rebuilt, _created = store.ensure_reminder(
        "复诊提醒", "2026-12-13T09:00", profile="妈妈",
        appointment_id=appointment_id,
    )
    clock["now"] = "2026-11-15T10:00:20"
    assert store.complete_reminder(rebuilt["id"]) is True
    # 改期：必须重建新的待办提醒，否则复诊前没有任何提醒
    clock["now"] = "2026-11-15T10:00:30"
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-20T09:00",
    )
    pending = [
        item for item in store.list_reminders(include_done=True)
        if int(item.get("appointment_id") or 0) == appointment_id
        and not item.get("done")
    ]
    assert len(pending) == 1
    assert str(pending[0]["due_at"])[:10] == "2026-12-19"


def test_reimport_does_not_restore_rejected_marker(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-20",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-14T09:00",
    )
    store.update_appointment_partial(appointment_id, status="completed")
    # 完成预约会把就诊复诊日同步到实际日期；用户之后又把复诊日
    # 改到别的日期，形成「已完成预约日期 ≠ 就诊复诊日」
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-20",
    )
    clock["now"] = "2026-11-15T10:00:10"
    backup = store.export_data()
    assert store.delete_appointment(appointment_id) is True
    clock["now"] = "2026-11-15T10:00:20"
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈", None)
        if item["status"] == "completed"
    ]
    assert len(rows) == 1
    assert rows[0]["note"] == ""
    assert int(rows[0]["owner_visit_id"] or 0) == 0
    clock["now"] = "2026-11-15T10:00:35"
    store.import_data(backup)
    rows = [
        item for item in store.list_appointments("妈妈", None)
        if item["status"] == "completed"
    ]
    assert len(rows) == 1
    assert rows[0]["note"] == ""
    assert int(rows[0]["owner_visit_id"] or 0) == 0
    linked = store.get_visit_record(visit["id"])
    assert int(linked.get("followup_appointment_id") or 0) == 0


# ---------------------------------------------------------------- 代理
def _prime_loopback_bucket(key: str):
    import api_server

    limiter = api_server.auth_failure_limiter_loopback
    with limiter._lock:
        limiter._hits[key] = deque(
            [time.monotonic()] * limiter.limit
        )
    return limiter, key


def test_loopback_bucket_key_matches_failure_counter(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r57-secret")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    recorded = []
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        if seconds >= 4:
            recorded.append(seconds)
            return
        await real_sleep(seconds, *args, **kwargs)

    monkeypatch.setattr(api_server.asyncio, "sleep", fake_sleep)
    limiter, key = _prime_loopback_bucket("ip:::/64")
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("::1", 55123),
        )
        response = client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        )
        assert response.status_code == 429
        assert recorded and recorded[0] >= 4
    finally:
        with limiter._lock:
            limiter._hits.pop(key, None)
        store.close()


def test_known_token_bypasses_blocked_gate(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r57-secret")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    real_sleep = asyncio.sleep

    async def fake_sleep(seconds, *args, **kwargs):
        if seconds >= 4:
            return
        await real_sleep(seconds, *args, **kwargs)

    monkeypatch.setattr(api_server.asyncio, "sleep", fake_sleep)
    limiter, key = _prime_loopback_bucket("ip:127.0.0.1")
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("127.0.0.1", 55999),
        )

        def _bad_call(_index):
            return client.get(
                "/api/sessions", headers={"X-Medix-Token": "bad"}
            ).status_code

        with ThreadPoolExecutor(max_workers=12) as pool:
            statuses = list(pool.map(_bad_call, range(40)))
        assert statuses and all(code == 429 for code in statuses)
        # 失败桶已满、闸门刚被错误令牌占用：正确令牌仍应快速通过
        started = time.monotonic()
        response = client.get(
            "/api/sessions", headers={"X-Medix-Token": "r57-secret"}
        )
        elapsed = time.monotonic() - started
        assert response.status_code == 200
        assert elapsed < 2.0
    finally:
        with limiter._lock:
            limiter._hits.pop(key, None)
        store.close()


def test_gate_source_uses_resolved_client(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r57-secret")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    captured = {}

    async def fake_enter(key, source=""):
        captured["key"] = key
        captured["source"] = source
        return None

    monkeypatch.setattr(api_server, "_auth_compare_enter", fake_enter)
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("127.0.0.1", 55222),
        )
        # 多级代理追加（最右为本机信任跳）：可解析出真实客户端地址
        response = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": "203.0.113.9, 127.0.0.1",
            },
        )
        assert response.status_code == 429
        assert captured["source"] == api_server._peer_key("203.0.113.9")
        assert captured["key"].startswith("ip:")
    finally:
        store.close()

    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER")
    store2 = SessionStore(str(tmp_path / "sessions2.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store2)
    captured.clear()
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("127.0.0.1", 55223),
        )
        response = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": "203.0.113.9, 127.0.0.1",
            },
        )
        assert response.status_code == 429
        # 默认不采信地址头（none）：无法证明客户端地址时不按代理
        # 对端设单源上限，攻击者占满自己的槽位不能 429 全家
        assert captured["source"] == ""
    finally:
        store2.close()


# ---------------------------------------------------------------- 报表
def test_family_attention_reports_overdue_followup_and_appointment(
    client_and_store,
):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    store.add_visit_record(
        "妈妈",
        visit_date=str(today - timedelta(days=30)),
        diagnosis="高血压复诊",
        next_followup_date=str(today - timedelta(days=10)),
    )
    store.save_profile("爸爸", {"sex": "男"})
    store.add_appointment(
        "爸爸",
        "心内科复诊",
        scheduled_at=f"{today - timedelta(days=3)}T09:00",
    )
    store.save_profile("奶奶", {"sex": "女"})
    store.add_metric(
        "奶奶", "glucose", 8.2,
        measured_at=f"{today}T08:00", context="fasting",
    )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    members = {item["name"]: item for item in data["profiles"]}
    assert members["妈妈"]["attention"]["level"] == "warning"
    assert "复诊已逾期" in members["妈妈"]["attention"]["label"]
    assert members["爸爸"]["attention"]["level"] == "info"
    assert "预约已过期" in members["爸爸"]["attention"]["label"]
    assert members["奶奶"]["attention"]["level"] == "warning"
    assert "血糖偏高" in members["奶奶"]["attention"]["label"]
    # 与健康提示同口径：复诊逾期必须是 warning
    insights = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()
    keys = {
        item["key"]: item["level"] for item in insights.get("insights") or []
    }
    assert keys.get("followup_overdue") == "warning"


def test_health_insights_reports_medication_stock(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    store.add_medication(
        "妈妈", "二甲双胍", "0.5g", "每日两次",
        stock=1, stock_unit="片", low_stock_threshold=5,
        expiry_date=str(today - timedelta(days=30)),
    )
    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    stock = next(
        item for item in insights["insights"]
        if item["key"] == "medication_stock"
    )
    assert stock["level"] == "warning"
    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert member["attention"]["label"] == "用药库存提醒"


def test_displayable_ignores_inactive_medication_logs(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("奶奶", {"sex": "女"})
    medication = store.add_medication("奶奶", "阿司匹林", "100mg")
    store.add_medication_log(medication["id"], taken_at=f"{today}T08:00")
    store.update_medication(medication["id"], active=False)
    assert client.delete("/api/profiles/奶奶").status_code == 200
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    assert all(item["name"] != "奶奶" for item in data["profiles"])


# ---------------------------------------------------------------- 前端
def test_frontend_round57_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    send = app_js[
        app_js.index("async function send(question)"):
        app_js.index("async function send(question)") + 4200
    ]
    # 首屏恢复未完成前必须等待：不能把问题写进上一会话
    assert "const initialSessionRestore = new Promise" in app_js
    assert "await initialSessionRestore" in send
    assert send.index("await initialSessionRestore") < (
        send.index("consumeDeepLink()")
    )
    assert "resolveInitialSessionRestore(Boolean(ok))" in app_js
    # 改名/删除档案同步在途主诉记录
    assert "updateInflightQuestionProfile(name, data.name);" in app_js
    assert "updateInflightQuestionProfile(name, '');" in app_js
    # 记录带档案而当前档案为空同样视为不一致
    assert "if (profile && profile !== currentProfile) {" in app_js
