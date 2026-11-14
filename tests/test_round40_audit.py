"""第四十轮审查：孕期量词出血、频率病程、枚举否定填充词、英文药名
停药、同秒孪生双向配对、跨成员漂移、复诊归属/改期/档案迁移、
代理地址头来源、风险窗口、改名不活跃、审计标签擦除、疫苗连续天数、
深链草稿时序。"""
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


# ---------------------------------------------------------------- triage
def test_pregnancy_bleeding_with_quantifiers():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕8周，阴道少量出血",
        "孕8周，阴道少量流血",
        "怀孕了，阴道有点出血",
        "怀孕8周，阴道大量流血",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 牙龈出血/明确否认不算孕产期出血
    assert detect_red_flags(
        patient_complaint_text("怀孕8周，牙龈出血")
    )["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("没有阴道出血，怀孕8周")
    )["level"] == "none"


def test_definition_keeps_frequency_course_adverbs():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "经常胸痛是什么病",
        "一直胸痛是什么病",
        "长期胸痛是什么病",
        "胸痛很久了是什么病",
        "总是胸痛是什么病",
    ):
        kept = patient_complaint_text(text)
        assert kept, text
        assert detect_red_flags(kept)["level"] == "emergency", text

    assert patient_complaint_text("咯血是什么病") == ""


def test_enumeration_negation_fillers_and_long_lists():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "没有出现胸痛和呼吸困难",
        "没有出现发热、咳嗽、胸痛",
        "未出现胸痛、呼吸困难",
        "无明显胸痛、呼吸困难",
        "无发热、咳嗽、咳痰、咽痛、胸痛",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text

    # 阳性修饰词仍救回
    assert detect_red_flags(
        patient_complaint_text("无诱因、突发胸痛")
    )["level"] == "emergency"


def test_english_medication_stop_intent_and_chinese_suffix():
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "我打算停用 warfarin，现在牙痛能吃布洛芬吗", {}
    ).get("level") == "warning"
    assert check_medication_safety(
        "warfarin 已经停用两个月了，现在牙痛能吃布洛芬吗", {}
    ).get("level") != "warning"


def test_recent_medication_is_current_for_interactions():
    from core.medication_safety import check_medication_safety

    for question in (
        "过去几天吃华法林，今天能吃布洛芬吗",
        "过去一周吃华法林，今天能吃布洛芬吗",
        "之前几天吃华法林，今天能吃布洛芬吗",
    ):
        assert check_medication_safety(
            question, {}
        ).get("level") == "warning", question


# ---------------------------------------------------------------- 导入
def test_import_both_edited_same_second_no_duplicates(tmp_path):
    from memory.session_store import SessionStore

    store = SessionStore(str(tmp_path / "reminders.db"))
    try:
        store.save_profile("妈妈", {})
        first = store.add_reminder("吃药A", "2026-12-01", "", "", "妈妈")
        second = store.add_reminder("吃药B", "2026-12-02", "", "", "妈妈")
        with store._lock, store._conn:
            store._conn.execute(
                "UPDATE reminders SET created_at = ?, updated_at = ?"
                " WHERE id IN (?, ?)",
                (
                    "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                    first["id"], second["id"],
                ),
            )
        backup = store.export_data()
        assert store.update_reminder(
            first["id"], title="吃药A（改）", due_at="2026-12-09"
        )
        assert store.update_reminder(
            second["id"], title="吃药B（改）", due_at="2026-12-10"
        )

        result = store.import_data(backup)

        titles = sorted(
            item["title"] for item in store.list_reminders(False, "妈妈")
        )
        # 两条都只是被编辑：按主键认领自身，不插入任何旧版本
        assert result["reminders"] == 0
        assert titles == ["吃药A（改）", "吃药B（改）"]
    finally:
        store.close()


def test_import_delete_and_edit_same_second_does_not_lose_deleted(store):
    store.save_profile("妈妈", {})
    first = store.add_reminder("吃药A", "2026-12-01", "", "", "妈妈")
    second = store.add_reminder("吃药B", "2026-12-02", "", "", "妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = ?, updated_at = ?"
            " WHERE id IN (?, ?)",
            (
                "2026-06-01T08:00:00", "2026-06-01T08:00:00",
                first["id"], second["id"],
            ),
        )
    backup = store.export_data()
    # 删除主键较大的 B、编辑主键较小的 A（反向顺序）
    assert store.delete_reminder(second["id"])
    assert store.update_reminder(
        first["id"], title="吃药A（改）", due_at="2026-12-09"
    )

    store.import_data(backup)

    records = store.list_reminders(False, "妈妈")
    titles = [item["title"] for item in records]
    assert "吃药A（改）" in titles
    assert "吃药B" in titles


def test_import_cross_profile_does_not_swallow_member(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        source.save_profile("爸爸", {})
        source.import_data({"medications": [
            {
                "profile": "妈妈", "name": "药A", "dose": "1g",
                "frequency": "每日一次",
            },
            {
                "profile": "爸爸", "name": "药B", "dose": "2g",
                "frequency": "每日一次",
            },
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
            item for item in target.list_medications(limit=10)
            if item["profile"] == "妈妈"
        )
        dad = next(
            item for item in target.list_medications(limit=10)
            if item["profile"] == "爸爸"
        )
        assert target.delete_medication(mom["id"])
        assert target.update_medication(
            dad["id"], name="药A", dose="1g"
        )

        target.import_data(backup)

        rows = target.list_medications(limit=100)
        # 妈妈被删的药A必须补回；爸爸的记录不能被吞掉
        assert any(
            item["profile"] == "妈妈" and item["name"] == "药A"
            for item in rows
        )
        assert any(item["profile"] == "爸爸" for item in rows)
    finally:
        target.close()


def test_import_restores_deleted_and_keeps_edited_same_second(tmp_path):
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        source.import_data({"health_metrics": [
            {
                "profile": "妈妈", "metric": "weight", "value1": 60,
                "measured_at": "2026-11-01T08:00",
            },
            {
                "profile": "妈妈", "metric": "weight", "value1": 61,
                "measured_at": "2026-11-02T08:00",
            },
        ]})
        backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup)
        rows = target.list_metrics("妈妈", "weight", 10, 0)
        deleted = next(item for item in rows if item["value1"] == 60)
        survivor = next(item for item in rows if item["value1"] == 61)
        assert target.delete_metric(deleted["id"])
        assert target.update_metric(survivor["id"], value1=161)

        target.import_data(backup)

        values = {
            float(item["value1"])
            for item in target.list_metrics("妈妈", "weight", 10, 0)
        }
        assert 60.0 in values
        assert 161.0 in values
    finally:
        target.close()


# ---------------------------------------------------------------- 复诊
def test_restore_cancelled_appointment_rejects_changed_date(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-11-19",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment = first["appointment"]
    store.update_appointment_partial(appointment["id"], status="cancelled")
    store.update_visit_record_partial(
        visit["id"], next_followup_date="2026-12-09"
    )

    with pytest.raises(ValueError):
        store.update_appointment_partial(
            appointment["id"], status="upcoming"
        )
    assert store.get_appointment(appointment["id"])["status"] == "cancelled"
    assert store.list_reminders(False, "妈妈") == []


def test_visit_profile_move_moves_followup_records(store):
    from api_server import _schedule_followup_sync

    store.save_profile("爸爸", {})
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "爸爸", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    result = _schedule_followup_sync(store, visit["id"])
    appointment = result["appointment"]
    reminder = result["reminder"]

    store.update_visit_record_partial(visit["id"], profile="妈妈")

    assert store.get_appointment(appointment["id"])["profile"] == "妈妈"
    record = store.get_visit_record(visit["id"])
    assert record["profile"] == "妈妈"
    reminders = store.list_reminders(False, "妈妈")
    assert any(item["id"] == reminder["id"] for item in reminders)
    assert store.list_reminders(False, "爸爸") == []


# ---------------------------------------------------------------- 报表/风险
def test_stale_high_risk_after_thanks_not_current(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "old-risk", "胸痛怎么办",
        "风险等级：高危，请立即就医。", profile="妈妈",
    )
    old_day = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (f"{old_day}T08:00:00", f"{old_day}T08:00:00", "old-risk"),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (f"{old_day}T08:00:00", "old-risk"),
        )
    store.save_exchange(
        "old-risk", "今天有点流鼻涕",
        "多喝水，注意休息。", profile="妈妈",
    )
    today = datetime.now().strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?"
            " AND created_at >= ?",
            (f"{today}T09:00:00", "old-risk", f"{today}T00:00:00"),
        )

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["risk"] == ""
    assert row["attention"]["level"] == "ok"

    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()
    assert not any(
        item["key"] == "consultation_risk" for item in insights["insights"]
    )


def test_rename_session_does_not_refresh_activity(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_exchange(
        "old-session", "胸痛怎么办",
        "风险等级：高危，请立即就医。", profile="妈妈",
    )
    old_day = (datetime.now() - timedelta(days=40)).strftime("%Y-%m-%d")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ?, updated_at = ?"
            " WHERE id = ?",
            (f"{old_day}T08:00:00", f"{old_day}T08:00:00", "old-session"),
        )
        store._conn.execute(
            "UPDATE messages SET created_at = ? WHERE session_id = ?",
            (f"{old_day}T08:00:00", "old-session"),
        )

    response = client.patch(
        "/api/sessions/old-session",
        json={"title": "妈妈-头晕随访"},
    )
    assert response.status_code == 200

    overview = client.get("/api/family/overview", params={"days": 7}).json()
    row = next(
        item for item in overview["profiles"] if item["name"] == "妈妈"
    )
    assert row["consultations"]["count"] == 0
    assert row["attention"]["level"] == "ok"


def test_vaccination_counts_toward_streak(client_and_store):
    client, store = client_and_store
    store.save_profile("爷爷", {})
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_vaccination(
        "爷爷", "流感疫苗", vaccinated_at=f"{today}T09:00"
    )

    streak = client.get(
        "/api/health/streak", params={"profile": "爷爷"}
    ).json()
    assert streak["recorded_today"] is True
    assert streak["current"] >= 1


def test_delete_session_redacts_old_tag_audit(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO audit_log(action, target, detail, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                "session.tags", "s1", "化疗、抑郁",
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    client.delete("/api/sessions/s1")

    audit = client.get("/api/audit").json()
    details = " ".join(
        str(item.get("detail") or "") for item in audit.get("actions") or []
    )
    assert "化疗" not in details
    assert "抑郁" not in details


# ---------------------------------------------------------------- 代理
def test_proxy_address_header_allowlist(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    request = FakeRequest({
        "x-forwarded-proto": "https",
        "x-forwarded-for": "127.0.0.1",
    })
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "none")
    assert api_server._forwarded_client_address(request) == ""
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-real-ip")
    assert api_server._forwarded_client_address(request) == ""
    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER")
    assert api_server._forwarded_client_address(request) == "127.0.0.1"


# ---------------------------------------------------------------- 前端
def test_context_edit_excludes_non_context_fields():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    block = app_js[
        app_js.index("const onContextPanelEdit"):
        app_js.index("els.contextPanel.addEventListener('input'")
    ]
    assert "id !== 'profile-select'" in block
    assert "id !== 'ctx-single'" in block


def test_send_early_exit_restores_composer():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "function restoreComposer(question)" in app_js
    send = app_js[
        app_js.index("async function send(question)"):
        app_js.index("async function send(question)") + 3000
    ]
    assert send.count("restoreComposer(question);") >= 2


def test_deep_link_flags_read_after_fetch():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    restore = app_js[
        app_js.index("async function restoreCurrentSession"):
        app_js.index("function scheduleAbortReconcile")
    ]
    fetch_at = restore.index("await fetch(")
    flag_at = restore.index(
        "const useDeepLinkContext = deepLinkedSession && !contextUserEdited;"
    )
    assert flag_at > fetch_at
