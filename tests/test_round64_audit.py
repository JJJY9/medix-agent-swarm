"""第六十四轮审查：科普引用误删真实主诉、从未停药/剂量换药、剖宫产
多年、兜底认领尾字段与组满幂等、就诊链接/日期同步/复诊复用、目标
提醒覆盖全部指标与峰值展示、未指定疫苗成员卡、附件档案过滤、前端
在途草稿档案与恢复清档拦截。"""
import asyncio
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
        "MEDIX_ALLOW_INSECURE_LAN", "MEDIX_RATE_LIMIT",
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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


# ---------------------------------------------------------------- 分诊
def test_education_reference_keeps_real_emergency():
    from core.triage import detect_red_flags, patient_complaint_text

    for text, label in (
        ("我在网上买的药吃了胸痛", "胸痛"),
        ("网上买的药吃完呼吸困难", "呼吸困难"),
        ("我跟着视频跳操后胸痛", "胸痛"),
        ("看了个视频后突然胸痛", "胸痛"),
        ("我照网上教程吃药后呼吸困难", "呼吸困难"),
        ("网上说胸痛要重视我现在胸痛", "胸痛"),
        ("我在网上看到胸痛要重视我胸痛得厉害", "胸痛"),
        ("我看到网上说胸痛要重视我刚胸痛了", "胸痛"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    # 纯科普引用仍过滤
    for text in (
        "我在网上看到胸痛要重视",
        "网上说胸痛是心梗，我有点头晕",
        "看到一篇科普说咯血要警惕",
    ):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


def test_never_stopped_medication_is_current():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("我从未间断服用华法林，今天头撞到门框"),
        {},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = detect_red_flags(
        "今天头撞到门框", {"history": "我从未停过华法林"}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    assert check_medication_safety(
        "我从未停过华法林，能吃布洛芬吗", {}
    )["level"] == "warning"


def test_dose_prefix_other_medication_not_revived():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text(
            "以前吃华法林，现在每天吃一片阿司匹林，今天头撞到门框"
        ),
        {},
    )
    assert result["level"] == "none"
    safety = check_medication_safety(
        "以前吃华法林，现在每天吃一片阿司匹林，能吃布洛芬吗", {}
    )
    assert not any(
        "华法林与非甾体抗炎药" in str(alert.get("title") or "")
        for alert in safety.get("alerts") or []
    )


def test_caesarean_many_years_phrasing():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "剖宫产手术后5年，阴道出血",
        "剖宫产手术后已5年，阴道出血",
        "剖宫产术后多年，阴道出血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text
    result = detect_red_flags(
        patient_complaint_text("阴道出血"),
        {"history": "剖宫产术后多年"},
    )
    assert result["level"] == "none"
    # 近期仍触发
    for text in ("剖宫产手术后，阴道出血", "剖宫产术后10天，阴道出血"):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text


def test_medication_negation_variants():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "华法林我没有在吃，能吃布洛芬吗",
        "阿司匹林我没有服用，能吃布洛芬吗",
        "我没吃过阿司匹林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text
    assert detect_red_flags(
        patient_complaint_text("华法林我没有在吃，昨天头撞到门框"), {}
    )["level"] == "none"


# ---------------------------------------------------------------- 导入
def test_fallback_tail_keeps_edited_sibling(store):
    store.save_profile("妈妈", {})
    backup = {"symptoms": [
        {"id": 1, "profile": "妈妈", "symptom": "头痛", "severity": 4,
         "note": "n2", "recorded_at": "2026-11-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "symptom": "头痛", "severity": 5,
         "note": "n1", "recorded_at": "2026-11-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_symptoms("妈妈", 100)
    first = next(r for r in rows if r["severity"] == 4)
    second = next(r for r in rows if r["severity"] == 5)
    store.delete_symptom(first["id"])
    store.update_symptom_partial(second["id"], recorded_at="2026-11-05T09:00")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE symptoms SET updated_at = '2026-11-15T10:00:05'"
            " WHERE id = ?",
            (second["id"],),
        )
    assert store.import_data(backup).get("symptoms") == 1
    pairs = sorted(
        (int(r["severity"]), r["recorded_at"])
        for r in store.list_symptoms("妈妈", 100)
    )
    assert (4, "2026-11-01T09:00") in pairs, pairs
    assert (5, "2026-11-05T09:00") in pairs, pairs


def test_group_full_fallback_reimport_idempotent(store):
    store.save_profile("妈妈", {})
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO symptoms(profile, symptom, severity, note,"
            " recorded_at, created_at, updated_at)"
            " VALUES ('妈妈','头痛',3,'','2026-11-02T09:00',?,?)",
            ("2026-06-01T08:00:01", "2026-11-15T09:00"),
        )
    backup = {"symptoms": [
        {"id": 2, "profile": "妈妈", "symptom": "头晕", "severity": 3,
         "note": "", "recorded_at": "2026-11-03T09:00",
         "created_at": "2026-06-01T08:00:01",
         "updated_at": "2026-06-01T08:00:01"},
        {"id": 3, "profile": "妈妈", "symptom": "头痛", "severity": 3,
         "note": "", "recorded_at": "2026-11-03T09:00",
         "created_at": "2026-06-01T08:00:01",
         "updated_at": "2026-06-01T08:00:01"},
    ]}
    first = store.import_data(backup)
    second = store.import_data(backup)
    assert first.get("symptoms") == 1
    assert second.get("symptoms") == 0
    with store._lock:
        count = store._conn.execute(
            "SELECT COUNT(*) AS n FROM symptoms"
        ).fetchone()["n"]
    assert count == 2


def test_import_keeps_appointment_referenced_by_visit_source(
    store, monkeypatch, tmp_path,
):
    clock = _freeze_clock(monkeypatch)
    from memory.session_store import SessionStore

    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {})
        visit = source.add_visit_record(
            "妈妈", visit_date="2026-11-01", diagnosis="高血压",
            next_followup_date="2026-12-14",
        )
        appt = source.add_appointment(
            "妈妈", "复诊：高血压", scheduled_at="2026-12-14T09:00",
            note=f"由就诊记录 #{visit['id']} 自动创建",
        )
        source.add_reminder(
            "复诊提醒", "2026-12-13", profile="妈妈",
            appointment_id=appt["id"],
        )
        source.set_visit_followup_links(
            visit["id"], appointment_id=appt["id"],
        )
        old_backup = source.export_data()
        source.add_visit_record(
            "妈妈", appointment_id=appt["id"],
            visit_date="2026-11-14", diagnosis="高血压复查",
        )
        new_backup = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(old_backup)
        rows = target.list_visit_records("妈妈", 100)
        visit_local = rows[0]
        appt_local = target.list_appointments("妈妈", None)[0]
        clock["now"] = "2026-11-15T10:00:10"
        target.update_visit_record_partial(
            visit_local["id"], next_followup_date="2026-12-20",
        )
        assert target.get_appointment(appt_local["id"]) is None
        clock["now"] = "2026-11-15T10:00:20"
        target.import_data(new_backup)
        appointments = target.list_appointments("妈妈", None)
        assert appointments, "导入后预约不应被清理"
        kept = appointments[0]
        assert target.list_reminders(False, "妈妈")
        visits = target.list_visit_records("妈妈", 100)
        sourced = [v for v in visits if int(v["appointment_id"] or 0)]
        assert sourced and int(sourced[0]["appointment_id"]) == kept["id"]
    finally:
        target.close()


def test_visit_links_and_reschedule_follow(store, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    clock["now"] = "2026-11-15T10:02:00"
    store.update_appointment_partial(
        appt["id"], scheduled_at="2026-12-25T09:00",
    )
    assert str(
        store.get_visit_record(visit["id"])["next_followup_date"]
    ).startswith("2026-12-25")
    # 已有其他就诊链接的预约不能再被共享
    assert store.set_visit_followup_links(
        visit["id"], appointment_id=appt["id"],
    ) is True
    visit_b = store.add_visit_record(
        "妈妈", visit_date="2026-11-02", diagnosis="乙",
        next_followup_date="2026-12-25",
    )
    assert store.set_visit_followup_links(
        visit_b["id"], appointment_id=appt["id"],
    ) is False


def test_drop_followup_keeps_other_owner_links(store, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {})
    visit_a = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="甲",
        next_followup_date="2026-12-08",
    )
    visit_b = store.add_visit_record(
        "妈妈", visit_date="2026-11-02", diagnosis="乙",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit_a['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊提醒", "2026-12-19", profile="妈妈",
        appointment_id=appt["id"],
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET owner_visit_id = ? WHERE id = ?",
            (visit_b["id"], appt["id"]),
        )
    store.set_visit_followup_links(
        visit_b["id"], appointment_id=appt["id"],
        reminder_id=reminder["id"],
    )
    clock["now"] = "2026-11-15T10:03:00"
    store.update_visit_record_partial(
        visit_a["id"], next_followup_date="2026-12-15",
    )
    b_row = store.get_visit_record(visit_b["id"])
    assert int(b_row["followup_appointment_id"] or 0) == appt["id"]
    assert int(b_row["followup_reminder_id"] or 0) == reminder["id"]
    assert store.get_appointment(appt["id"]) is not None


def test_schedule_followup_does_not_steal_other_visit_appointment(
    store, monkeypatch,
):
    _freeze_clock(monkeypatch)
    import api_server

    store.save_profile("妈妈", {})
    visit_a = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="甲",
        next_followup_date="2026-12-08",
    )
    visit_b = store.add_visit_record(
        "妈妈", visit_date="2026-11-02", diagnosis="乙",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit_a['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊提醒", "2026-12-19", profile="妈妈",
        appointment_id=appt["id"],
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE appointments SET owner_visit_id = ? WHERE id = ?",
            (visit_b["id"], appt["id"]),
        )
    store.set_visit_followup_links(
        visit_b["id"], appointment_id=appt["id"],
        reminder_id=reminder["id"],
    )
    result = api_server._schedule_followup_sync(store, visit_a["id"])
    kept = store.get_appointment(appt["id"])
    assert kept["scheduled_at"].startswith("2026-12-20")
    assert int(kept["owner_visit_id"] or 0) == visit_b["id"]
    a_row = store.get_visit_record(visit_a["id"])
    assert int(a_row["followup_appointment_id"] or 0) != appt["id"]
    assert (
        result.get("appointment") is None
        or int(result["appointment"]["id"]) != appt["id"]
    )


# ---------------------------------------------------------------- 报表
def test_family_attention_covers_lower_bound_targets(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "spo2", "value1": 95,
    })
    for offset, value in ((-3, 93), (-2, 92), (0, 96)):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "spo2", "value1": value,
            "measured_at": f"{_date(offset)}T08:00",
        })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
    assert mom["attention"]["level"] != "ok"
    assert "低于目标" in mom["attention"]["label"]


def test_unassigned_vaccination_keeps_member_card(client_and_store):
    client, store = client_and_store
    store.add_vaccination(
        "", "流感疫苗", dose_number=1,
        vaccinated_at="2026-09-16", next_due="2026-11-10",
    )
    data = client.get("/api/family/overview", params={"days": 7}).json()
    names = [p["name"] for p in data["profiles"]]
    assert "" in names, names
    resp = client.get("/api/family/overview/export.csv")
    assert "流感疫苗" in resp.content.decode("utf-8-sig")


def test_target_bp_detail_separates_peaks(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 140, "value2": 90,
    })
    for offset, v1, v2 in ((-2, 150, 85), (-1, 130, 105), (0, 120, 80)):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp", "value1": v1,
            "value2": v2, "measured_at": f"{_date(offset)}T08:00",
        })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
    assert "150/105" not in mom["attention"]["detail"]
    assert "最高收缩压 150" in mom["attention"]["detail"]
    assert "最高舒张压 105" in mom["attention"]["detail"]


def test_attachment_integrity_profile_filter(client_and_store):
    client, store = client_and_store
    store.add_attachment(
        "妈妈", "other", 0, "妈妈报告.pdf", "mom-missing.pdf",
        "application/pdf", 100,
    )
    store.add_attachment(
        "爸爸", "other", 0, "爸爸报告.pdf", "dad-missing.pdf",
        "application/pdf", 100,
    )
    data = client.get(
        "/api/attachments/integrity", params={"profile": "妈妈"}
    ).json()
    names = [item["original_name"] for item in data.get("missing") or []]
    assert "妈妈报告.pdf" in names
    assert "爸爸报告.pdf" not in names
    everything = client.get("/api/attachments/integrity").json()
    all_names = [
        item["original_name"] for item in everything.get("missing") or []
    ]
    assert "爸爸报告.pdf" in all_names


# ---------------------------------------------------------------- 前端静态
def test_frontend_round64_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 刷新恢复的在途主诉保留原档案归属
    assert "draftProfileMemory = profile;" in source
    assert "safeSessionSet(DRAFT_PROFILE_KEY, profile);" in source
    # 恢复把档案清空时同样拦截（submitProfile 非空即比较）
    assert "if (submitProfile && submitProfile !== sendProfile)" in source
    # 附件完整性检查带档案过滤
    assert "/api/attachments/integrity?profile=" in source
