"""第六十三轮审查：药名后置续用/漏服窗口、剖宫产手术语序、科普引用
分句、患儿正则兜底、导入孪生/改期墓碑/复诊标记归属、家庭关注目标
等级与优先级、健康提示目标成员通用阈值与血压峰值展示、前端恢复档案
冲突与在途基线对账、历史会诊失败提示。"""
import csv
import io
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
def test_past_use_with_continuation_is_current_medication():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "华法林以前吃过，现在还在吃，今天撞到头",
        "华法林以前吃过，最近又吃上了，今天撞到头",
        "华法林以前吃过，每天吃一片，今天撞到头",
        "华法林以前吃过，一直服用，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    # 续用线索指向另一种药时不能复活既往药
    for text in (
        "华法林以前吃过，每天吃阿司匹林，今天撞到头",
        "以前吃华法林，现在每天吃阿司匹林，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text
    for text in (
        "华法林以前吃过，现在每天吃一片，能吃布洛芬吗",
        "华法林以前吃过，现在还在吃，能吃布洛芬吗",
    ):
        safety = check_medication_safety(text, {})
        assert safety["level"] == "warning", text
        assert any(
            "华法林" in str(alert.get("title") or "")
            for alert in safety.get("alerts") or []
        ), text
    # 宾语是另一药时不按本药续用告警
    safety = check_medication_safety(
        "华法林以前吃过，每天吃阿司匹林，能吃布洛芬吗", {}
    )
    assert not any(
        "华法林与非甾体抗炎药" in str(alert.get("title") or "")
        for alert in safety.get("alerts") or []
    )


def test_multi_day_missed_dose_still_anticoagulated():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我已经两天没吃华法林了，今天撞到头",
        "我三天没吃华法林，今天撞到头",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    assert check_medication_safety(
        "我已经两天没吃华法林了，能吃布洛芬吗", {}
    )["level"] == "warning"


def test_caesarean_surgery_phrasing_and_years():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "剖宫产手术之后，阴道出血",
        "剖宫产手术刚做完，出血了",
        "剖腹产手术以后，阴道流血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    for text in (
        "剖宫产术后已5年，阴道出血",
        "剖宫产手术之后5年，阴道出血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text


def test_education_reference_not_current_symptom():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我在网上看到胸痛要重视，我没有胸痛",
        "网上说胸痛是心梗，我有点头晕",
        "看到一篇科普说咯血要警惕",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text
    # 同一分句仍描述当前病程时保留
    result = detect_red_flags(
        patient_complaint_text("我在网上看到胸痛要重视，今天胸痛加重了"),
        {},
    )
    assert "胸痛" in result["matches"]


def test_child_profile_regex_and_history_pregnancy():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("我儿子头被门撞了"),
        {"age": "5岁", "medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    result = detect_red_flags(
        patient_complaint_text("阴道出血"),
        {"age": "16岁", "history": "我女儿怀孕12周"},
    )
    assert "孕产期出血" in result["matches"]
    # 成年档案仍排除家属病史
    result = detect_red_flags(
        patient_complaint_text("阴道出血"),
        {"age": "40岁", "history": "我女儿怀孕12周"},
    )
    assert result["level"] == "none"


# ---------------------------------------------------------------- 导入
def _appointment_rows(store):
    with store._lock:
        return [
            (int(row["id"]), row["profile"], row["title"], row["note"],
             int(row["owner_visit_id"]), row["status"])
            for row in store._conn.execute(
                "SELECT id, profile, title, note, owner_visit_id, status"
                " FROM appointments ORDER BY id"
            ).fetchall()
        ]


def test_moved_appointment_with_drift_restores_deleted_twin(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    # 占用 id 1-2 制造主键漂移
    store.add_appointment("妈妈", "口腔科0", scheduled_at="2026-12-01T09:00")
    store.add_appointment("妈妈", "口腔科1", scheduled_at="2026-12-01T10:00")
    backup = {"appointments": [
        {"id": 1, "profile": "妈妈", "title": "复诊", "note": "",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "title": "体检", "note": "",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_appointments(None, None)
    fu = next(r for r in rows if r["title"] == "复诊")
    tj = next(r for r in rows if r["title"] == "体检")
    store.update_appointment_partial(fu["id"], profile="爸爸")
    store.delete_appointment(tj["id"])
    # 被删的体检必须按原内容补回；改挂的复诊在旧档案可能留一条
    # 旧版本副本（同秒孪生无法区分归属，宁重复勿丢记录）
    assert store.import_data(backup).get("appointments") == 2
    titles = [(r[1], r[2]) for r in _appointment_rows(store)]
    assert ("爸爸", "体检") in titles, titles
    assert len([r for r in titles if r[1] == "复诊"]) == 2, titles
    assert store.import_data(backup).get("appointments") == 0


def test_symptom_recorded_at_edit_keeps_deleted_twin(store):
    store.save_profile("妈妈", {})
    backup = {"symptoms": [
        {"id": 1, "profile": "妈妈", "symptom": "头痛", "severity": 3,
         "note": "", "recorded_at": "2026-11-01T09:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "symptom": "头晕", "severity": 3,
         "note": "", "recorded_at": "2026-11-02T09:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_symptoms("妈妈", 100)
    headache = next(r for r in rows if r["symptom"] == "头痛")
    dizzy = next(r for r in rows if r["symptom"] == "头晕")
    store.update_symptom_partial(
        headache["id"], recorded_at="2026-11-02T09:00",
    )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE symptoms SET updated_at = '2026-11-15T10:00:05'"
            " WHERE id = ?",
            (headache["id"],),
        )
    store.delete_symptom(dizzy["id"])
    store.import_data(backup)
    symptoms = [
        (r["symptom"], r["recorded_at"])
        for r in store.list_symptoms("妈妈", 100)
    ]
    assert any(item[0] == "头晕" for item in symptoms), symptoms
    assert len([item for item in symptoms if item[0] == "头痛"]) == 1


def test_reminder_reschedule_reimport_with_drift(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    store.add_reminder("取快递", "2026-12-02T09:00", profile="爸爸")
    backup = {"reminders": [
        {"id": 1, "profile": "妈妈", "title": "吃药", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "title": "吃药", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = store.list_reminders(False)
    mom = next(
        r for r in rows
        if r["profile"] == "妈妈" and r["title"] == "吃药"
    )
    store.update_reminder(mom["id"], due_at="2026-12-05T09:00")
    store.import_data(backup)
    rows = store.list_reminders(False)
    mom_rows = [
        r for r in rows
        if r["profile"] == "妈妈" and r["title"] == "吃药"
    ]
    dad_rows = [
        r for r in rows
        if r["profile"] == "爸爸" and r["title"] == "吃药"
    ]
    assert len(mom_rows) == 1 and mom_rows[0]["due_at"].startswith(
        "2026-12-05"
    )
    assert len(dad_rows) == 1 and dad_rows[0]["due_at"].startswith(
        "2026-12-02"
    )
    assert store.import_data(backup).get("reminders") == 0


def test_deleted_rebuilt_reminder_stays_deleted(store, monkeypatch):
    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-15",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-15T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    r1 = store.add_reminder(
        "复诊提醒", "2026-12-14", profile="妈妈",
        appointment_id=appt["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment_id=appt["id"], reminder_id=r1["id"],
    )
    store.complete_reminder(r1["id"])
    clock["now"] = "2026-11-15T10:02:00"
    store.update_appointment_partial(
        appt["id"], scheduled_at="2026-12-20T09:00",
    )
    r2 = store.list_reminders(False, "妈妈")[0]
    clock["now"] = "2026-11-15T10:03:00"
    store.delete_reminder(r2["id"])
    clock["now"] = "2026-11-15T10:04:00"
    store.update_appointment_partial(
        appt["id"], scheduled_at="2026-12-30T09:00",
    )
    assert not store.list_reminders(False, "妈妈")
    updated_visit = store.get_visit_record(visit["id"])
    assert int(updated_visit["followup_reminder_id"] or 0) == 0


def test_import_duplicate_visit_keeps_original_marker(
    store, monkeypatch, tmp_path,
):
    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-14", diagnosis="高血压",
        next_followup_date="2026-12-05",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-05T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊提醒", "2026-12-04", profile="妈妈",
        appointment_id=appt["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment_id=appt["id"],
        reminder_id=reminder["id"],
    )
    backup1 = store.export_data()
    clock["now"] = "2026-11-15T10:00:20"
    store.update_visit_record_partial(visit["id"], diagnosis="高血压病2级")
    backup2 = store.export_data()

    from memory.session_store import SessionStore

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {})
        target.import_data(backup1)
        target.import_data(backup2)
        appt_row = target.get_appointment(appt["id"])
        assert int(appt_row.get("owner_visit_id") or 0) == visit["id"]
        assert (
            f"由就诊记录 #{visit['id']} 自动创建"
            in str(appt_row.get("note") or "")
        )
        visits = target.list_visit_records("妈妈", 100)
        assert len(visits) >= 2
        new_visit = next(v for v in visits if v["id"] != visit["id"])
        clock["now"] = "2026-11-15T10:01:00"
        target.update_visit_record_partial(
            new_visit["id"], next_followup_date="2026-12-25",
        )
        assert target.get_appointment(appt["id"]) is not None
        assert target.get_reminder(reminder["id"]) is not None
    finally:
        target.close()


# ---------------------------------------------------------------- 报表
def test_target_info_alert_does_not_mask_skipped_warning(client_and_store):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 140, "value2": 90,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 145, "value2": 95,
        "measured_at": f"{_date(-2)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 130, "value2": 85,
        "measured_at": f"{_date(0)}T08:00",
    })
    med = store.add_medication(
        "妈妈", "阿司匹林", dose="100mg", frequency="每日一次",
        started_at=_date(-10),
    )
    for day in range(1, 4):
        assert store.add_medication_log(
            med["id"], f"{_date(-day)}T08:00", "", skipped=True,
            skip_reason="forget",
        ) is not None
    data = client.get("/api/family/overview", params={"days": 7}).json()
    mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
    assert mom["attention"]["level"] == "warning"
    assert "漏服" in mom["attention"]["label"]


def test_target_glucose_multiple_over_is_warning(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "glucose", "value1": 8.0,
    })
    for day in range(2, 7):
        client.post("/api/metrics", json={
            "profile": "奶奶", "metric": "glucose", "value1": 15.0,
            "context": "fasting",
            "measured_at": f"{_date(-day)}T08:00",
        })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 7.5,
        "context": "fasting", "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    grandma = next(p for p in data["profiles"] if p["name"] == "奶奶")
    assert grandma["attention"]["level"] == "warning"
    assert "血糖 5 次超出目标" in grandma["attention"]["label"]


def test_target_member_within_target_keeps_peak_insight(client_and_store):
    # 家庭卡片的关注徽标对目标内读数保持「暂无关注」；健康提示面板
    # 仍保留按标准 160/100 的单次峰值提示（第四十九轮安全契约）：
    # 宽松目标不应让 165/100 这样的重度读数完全消失在提示里
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "李奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "李奶奶", "metric": "bp", "value1": 170, "value2": 110,
    })
    client.post("/api/metrics", json={
        "profile": "李奶奶", "metric": "bp", "value1": 165,
        "value2": 100, "measured_at": f"{_date(0)}T08:00",
    })
    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        p for p in overview["profiles"] if p["name"] == "李奶奶"
    )
    assert member["attention"]["level"] == "ok"
    for kwargs in ({"days": 30}, {"days": 30, "profile": "李奶奶"}):
        insights = client.get(
            "/api/health/insights", params=kwargs
        ).json()["insights"]
        bp_single = [
            item for item in insights
            if item["key"] == "metric_bp_single"
        ]
        assert bp_single and bp_single[0]["level"] == "warning", insights


def test_bp_peak_shows_systolic_and_diastolic_separately(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 150, "value2": 85,
        "measured_at": f"{_date(-1)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 130, "value2": 105,
        "measured_at": f"{_date(0)}T08:00",
    })
    for kwargs, need_member in (
        ({"days": 30}, True),
        ({"days": 30, "profile": "妈妈"}, False),
    ):
        insights = client.get(
            "/api/health/insights", params=kwargs
        ).json()["insights"]
        bp_single = [
            item for item in insights
            if item["key"] == "metric_bp_single"
        ]
        assert bp_single, kwargs
        text = bp_single[0]["title"] + bp_single[0]["detail"]
        assert "最高收缩压 150" in text, text
        assert "最高舒张压 105" in text, text
        if need_member:
            assert "妈妈" in bp_single[0]["title"]


# ---------------------------------------------------------------- 前端静态
def test_frontend_round63_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 恢复改写的档案与提交时不同：按草稿冲突中止
    assert "const submitProfile = sendProfile;" in source
    assert (
        "if (submitProfile && sendProfile && submitProfile !== sendProfile)"
        in source
    )
    assert "preserveDraftProfile(submitProfile, question);" in source
    # 在途记录携带发送前基线，启动对账要求问答 id 更新
    assert "baseline: baselineMessageId," in source
    assert "let inflightRestoredBaseline = 0;" in source
    assert "newestAssistantId > inflightBaseline" in source
    # 打开历史会诊失败提示不渲染进当前会话线程
    assert "const failedCurrent = id === (sessionId || '');" in source
    assert "打开会诊失败：" in source
