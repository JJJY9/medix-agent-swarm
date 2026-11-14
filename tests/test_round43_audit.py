"""第四十三轮审查：枚举后置修饰、孕期简写/档案联动、近期停药与既往
「开始吃」、同源导入证据、预约备注陈旧标记、共享桶对端约束、家庭血压
分成员、续聊计入今日、草稿/待重置、档案删除的旧版记忆清理、提醒去重。"""
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


# ---------------------------------------------------------------- 分诊/用药
def test_enum_negation_post_modifier_is_positive():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "无发热、咳嗽、胸痛加重",
        "无发热、咳嗽、呼吸困难加重",
        "无发热、咳嗽、胸痛伴大汗",
        "无发热、咳嗽、胸痛放射到左臂",
        "无发热、咳嗽、胸痛明显加重",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "emergency", text

    # 纯枚举否认（无后置修饰）仍为阴性
    assert detect_red_flags(
        patient_complaint_text("无发热、咳嗽、胸痛")
    )["level"] == "none"


def test_pregnancy_terms_and_context_history():
    from core.triage import detect_red_flags

    for text in (
        "宫外孕，阴道出血",
        "孕吐厉害，今天见红",
        "产检正常，今天阴道出血",
        "葡萄胎术后，阴道流血",
    ):
        assert detect_red_flags(text, {})["level"] == "emergency", text

    # 孕期写在档案病史里、主诉只描述出血
    assert detect_red_flags(
        "今天阴道出血", {"history": "孕8周"}
    )["level"] == "emergency"
    # 家族史里的孕期不触发
    assert detect_red_flags(
        "今天阴道出血", {"history": "母亲怀孕中"}
    )["level"] == "none"


def test_head_trauma_variants_with_anticoagulant():
    from core.triage import detect_red_flags

    for text in (
        "正在服用华法林，今天头部碰到桌角",
        "正在服用华法林，今天磕了后脑勺",
        "正在服用华法林，今天脑袋着地",
        "正在服用华法林，今天头顶磕到门框",
    ):
        result = detect_red_flags(text, {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_pregnancy_double_negation_modifier():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "怀孕8周，阴道未见明显出血",
        "孕8周，阴道未见明显流血",
        "怀孕8周，阴道未发现明显出血",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text


def test_past_medication_start_phrase_is_current():
    from core.medication_safety import check_medication_safety

    # 「N 年前开始吃/一直吃到现在」仍是当前用药
    for text in (
        "我两年前开始吃华法林，现在牙疼能吃布洛芬吗",
        "两年前开始吃华法林，一直吃到现在，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) == "warning", text

    # 完成体的既往用药仍不告警
    for text in (
        "以前吃过华法林，现在能吃布洛芬吗",
        "十年前吃过阿司匹林，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) != "warning", text


def test_recent_stop_in_medication_safety():
    from core.medication_safety import check_medication_safety

    for text in (
        "刚停用华法林，现在能吃布洛芬吗",
        "昨天停用华法林，现在能吃布洛芬吗",
        "华法林停药两天，现在能吃阿司匹林吗",
    ):
        assert check_medication_safety(text, {}).get(
            "level"
        ) == "warning", text


def test_triage_past_medication_with_duration():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "十年前吃过华法林，今天撞到头",
        "几年前服用过华法林，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" not in result["matches"], text


# ---------------------------------------------------------------- 导入
def _med(raw_id, profile, name, dose, created="", updated=""):
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
        item["updated_at"] = updated or created
    return item


def test_import_foreign_backup_restores_deleted_row(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("B", {})
        # 本机先有一条无关记录（id1，created_at 与备份不同）
        target.import_data({"medications": [
            _med(1, "A", "药P", "1g", "2026-06-02T09:00:00"),
        ]})
        created = "2026-06-01T08:00:00"
        target.import_data({"medications": [
            _med(2, "B", "药D", "1g", created),
            _med(3, "B", "药Q", "1g", created),
        ]})
        d = next(
            item for item in target.list_medications(limit=20)
            if item["name"] == "药D" and item["profile"] == "B"
        )
        assert target.delete_medication(d["id"])

        # 另一台设备删除药Q 后导出的备份只剩药D（原始主键 1）
        foreign_backup = {"medications": [
            _med(1, "B", "药D", "1g", created),
        ]}
        result = target.import_data(foreign_backup)
        assert result["medications"] == 1
        rows = target.list_medications(limit=20)
        assert any(
            item["profile"] == "B" and item["name"] == "药D"
            for item in rows
        )
        # 重复导入保持幂等
        assert target.import_data(foreign_backup)["medications"] == 0
    finally:
        target.close()


def test_import_sibling_skip_requires_same_backup_identity(tmp_path):
    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("A", {})
        target.save_profile("B", {})
        target.import_data({"care_items": [
            {
                "id": 1, "profile": "B", "title": "复查90天",
                "interval_days": 90,
                "created_at": "2026-06-02T09:00:00",
                "updated_at": "2026-06-02T09:00:00",
            },
            {
                "id": 2, "profile": "B", "title": "复查30天",
                "interval_days": 30,
                "created_at": "2026-06-01T08:00:00",
                "updated_at": "2026-06-01T08:00:00",
            },
        ]})
        created = "2026-06-01T08:00:00"
        edited = "2026-06-03T09:00:00"
        backup_x = {"care_items": [
            {
                "id": 1, "profile": "B", "title": "复查90天",
                "interval_days": 90, "created_at": created,
                "updated_at": edited,
            },
            {
                "id": 2, "profile": "A", "title": "复查90天",
                "interval_days": 90, "created_at": created,
                "updated_at": edited,
            },
            {
                "id": 3, "profile": "B", "title": "复查30天",
                "interval_days": 30, "created_at": created,
                "updated_at": edited,
            },
        ]}
        target.import_data(backup_x)

        # 未做任何修改，重复导入必须幂等，不能在 A 名下复制
        result = target.import_data(backup_x)
        assert result["care_items"] == 0
        a_rows = [
            item for item in target.list_care_items(None)
            if item["profile"] == "A"
        ]
        assert len(a_rows) == 1
    finally:
        target.close()


def test_appointment_stale_note_marker_is_remapped(store):
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
    appointment = store.get_appointment(appointment_id)
    # 备注标记改成新库就诊 id；归属未写回（旧 owner 仍指向已删就诊）
    assert f"#{new_visit_id}" in str(appointment.get("note") or "")
    assert int(appointment.get("owner_visit_id") or 0) != new_visit_id
    # 改期不会静默覆盖新就诊的复诊日期
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-30T09:00"
    )
    refreshed = store.get_visit_record(new_visit_id)
    assert refreshed["next_followup_date"][:10] == "2026-12-14"


def test_import_merges_restored_reminder_duplicate(store):
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
    store.update_appointment_partial(appointment_id, status="upcoming")
    pending_after_restore = store.list_reminders(False, "妈妈")
    assert len(pending_after_restore) == 1

    store.import_data(backup)

    pending = store.list_reminders(False, "妈妈")
    assert len(pending) == 1
    assert pending[0]["appointment_id"] == appointment_id


def test_visit_link_setter_rejects_cancelled_appointment(store):
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01",
        next_followup_date="2026-12-14",
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
    )
    store.update_appointment_partial(appointment["id"], status="cancelled")

    assert store.set_visit_followup_links(
        visit["id"], appointment["id"], 0
    ) is False
    record = store.get_visit_record(visit["id"])
    assert record["followup_appointment_id"] == 0


def test_appointment_endpoints_share_followup_lock():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    for name in ("async def update_appointment(", "async def delete_appointment("):
        start = api_source.index(name)
        end = api_source.index("\n@app.", start)
        assert "_VISIT_FOLLOWUP_LOCK" in api_source[start:end], name


# ---------------------------------------------------------------- 报表
def test_recorded_days_count_continued_sessions(store):
    store.save_profile("妈妈", {})
    old_day = (datetime.now() - timedelta(days=3)).strftime(
        "%Y-%m-%dT%H:%M:%S"
    )
    store.save_exchange("s1", "问题", "回答", profile="妈妈")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = 's1'",
            (old_day,),
        )
    store.save_exchange("s1", "继续问", "继续答", profile="妈妈")

    today = datetime.now().strftime("%Y-%m-%d")
    assert today in store.recorded_days("妈妈", 7)
    assert today in store.recorded_days_by_profile(7).get("妈妈", [])


def test_family_bp_monitoring_per_member(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 160, "value2": 100,
        "measured_at": f"{today}T07:00", "context": "morning",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "bp", "value1": 110, "value2": 70,
        "measured_at": f"{today}T22:00", "context": "evening",
    })

    data = client.get(
        "/api/health/bp-monitoring", params={"days": 1}
    ).json()

    members = {item["profile"]: item for item in data["members"]}
    assert members["妈妈"]["completed_slots"] == 1
    assert members["爸爸"]["completed_slots"] == 1
    # 跨成员合并的晨晚差没有意义：不能是 160-110=+50
    assert data["morning_evening_systolic_diff"] is None
    assert members["妈妈"]["morning_evening_systolic_diff"] is None
    assert members["爸爸"]["morning_evening_systolic_diff"] is None


def test_family_compare_daily_total_blank_without_today_records(
    client_and_store,
):
    client, store = client_and_store
    store.save_profile("妈妈", {})
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 4000,
        "measured_at": f"{yesterday}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "steps", "value1": 5000,
        "measured_at": f"{yesterday}T18:00",
    })

    data = client.get(
        "/api/family/compare", params={"metric": "steps", "days": 7}
    ).json()
    row = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert row["daily_total"] is None

    csv_response = client.get(
        "/api/family/compare/export.csv",
        params={"metric": "steps", "days": 7},
    )
    mom_line = next(
        line for line in csv_response.text.splitlines()
        if line.startswith("妈妈")
    )
    assert mom_line.endswith(",")


def test_profile_delete_cleans_session_summaries(
    client_and_store, monkeypatch
):
    import api_server

    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.save_exchange("s1", "问题", "回答", profile="妈妈")
    captured = []

    def fake_erase(entries):
        captured.extend(entries)
        return True

    monkeypatch.setattr(
        api_server, "_erase_long_term_entries", fake_erase
    )
    monkeypatch.setattr(
        api_server, "record_audit",
        lambda *args, **kwargs: asyncio_sleep_noop(),
    )

    from urllib.parse import quote

    response = client.delete(f"/api/profiles/{quote('妈妈')}")

    assert response.status_code == 200
    assert ("妈妈", "") in captured
    assert ("妈妈", "s1") in captured


async def asyncio_sleep_noop():
    return None


# ---------------------------------------------------------------- 前端
def test_frontend_round43_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")

    # 换档被 busy/取消拒绝时撤销待重置标记
    profile_block = app_js[
        app_js.index("async function applySelectedProfile"):
        app_js.index("loadHomeGlance();", app_js.index(
            "async function applySelectedProfile"))
    ]
    assert "const hadPendingReset = pendingSessionReset;" in profile_block
    assert "setPendingSessionReset(hadPendingReset);" in profile_block

    # 草稿归属更新前先取消防抖定时器
    reassign = app_js[
        app_js.index("function reassignDraftToCurrentSession"):
        app_js.index("function settleDraftForSession")
    ]
    assert "clearTimeout(draftTimer);" in reassign
    ensure_block = app_js[
        app_js.index("async function ensureSession("):
        app_js.index("async function startNewSession")
    ]
    assert "reassignDraftToCurrentSession();" in ensure_block

    # 家庭血压按成员展示
    assert "bpMemberLines(data)" in app_js
