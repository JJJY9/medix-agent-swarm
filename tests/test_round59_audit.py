"""第五十九轮审查：枚举否定时长、HCG 妊娠证据、化疗结束、寒暄语境、
意图停药不服用、英文双重否定、主键洞自认领、同秒提醒多候选/锚点、
预约备注覆盖、复诊级联、IPv6 全局预算、单药最差依从、家庭漏服、
风险跨级否定、档案名校验、过期复诊、前端草稿归属。"""
import asyncio
import time
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
def test_enum_negation_duration_keeps_chest_pain():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "无发热、胸痛已持续2小时",
        "无发热、咳嗽、胸痛持续2小时",
        "无发热、胸痛3天了",
        "无发热、胸痛5小时了",
        "没有发热、胸痛2小时了",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "胸痛" in result["matches"], text
    # 纯枚举否认仍是阴性
    result = detect_red_flags(
        patient_complaint_text("无发热、咳嗽、胸痛"), {}
    )
    assert result["level"] == "none"


def test_pregnancy_hcg_and_colloquial_evidence():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "HCG阳性，阴道出血",
        "hcg 阳性，阴道出血",
        "血HCG阳性，阴道出血",
        "尿HCG阳性，阴道出血",
        "β-HCG阳性，阴道出血",
        "验孕棒两道杠，阴道出血",
        "验孕棒一深一浅，阴道出血",
        "验孕试纸阳性，阴道出血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    result = detect_red_flags(
        patient_complaint_text("阴道出血"), {"history": "HCG阳性"}
    )
    assert "孕产期出血" in result["matches"]
    result = detect_red_flags(
        patient_complaint_text("自然流产史，阴道出血"), {}
    )
    assert "孕产期出血" not in result["matches"]


def test_chemo_ended_not_current_immunosuppression():
    from core.triage import detect_red_flags

    for history in (
        "化疗结束5年了",
        "化疗已经结束了",
        "化疗结束了",
        "五年前化疗过",
        "化疗完成2年了",
        "放疗结束3年",
        "化疗结束后5年",
    ):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert result["level"] == "none", history
    for history in ("还在化疗", "正在化疗", "刚结束化疗", "化疗期间"):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert "免疫功能低下伴发热" in result["matches"], history


def test_greeting_variants_do_not_context_definition_questions():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "早上好，胸痛是什么病",
        "晚上好，抽搐是什么病",
        "医生，胸痛是什么病",
        "大夫，咯血是什么病",
        "嗯嗯，胸痛是什么病",
        "明白，抽搐是什么病",
        "收到，咯血是什么病",
        "辛苦了，胸痛是什么病",
        "哈喽，胸痛是什么病",
        "请教一下，胸痛是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text)
        )["level"] == "none", text
    for text, label in (
        ("手脚发麻，说话不清是什么病", "言语不清"),
        ("胸口闷痛，呼吸困难是什么病", "呼吸困难"),
        ("浑身没劲，说话不清是什么病", "言语不清"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text


def test_intent_stop_fu_yong_forms():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "准备不服用华法林了，现在能吃布洛芬吗",
        "打算不再服用华法林了，现在能吃布洛芬吗",
        "准备不再服用华法林了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    for text in (
        "准备不服用 warfarin 了，现在能吃布洛芬吗",
        "打算不再服用 warfarin 了，现在能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    result = detect_red_flags(
        patient_complaint_text("打算不再服用华法林了，头撞到门框"), {}
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]


def test_english_drug_double_negation():
    from core.medication_safety import check_medication_safety

    for text in (
        "我不得不吃 warfarin，现在能吃阿司匹林吗",
        "I can't stop taking warfarin, can I take aspirin?",
        "我不得不服用 warfarin，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- 导入
def test_metric_key_hole_self_claim_restores_deleted(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    clock = _freeze_clock(monkeypatch)
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.save_profile("妈妈", {"sex": "女"})
        for value, note in ((60, ""), (70, "A"), (80, "B")):
            source.add_metric(
                "妈妈", "weight", value,
                measured_at="2026-11-01T08:00", note=note,
            )
        with source._lock, source._conn:
            source._conn.execute(
                "UPDATE health_metrics SET created_at = ?,"
                " updated_at = ?",
                ("2026-11-15T10:00:00", "2026-11-15T10:00:00"),
            )
        rows = sorted(
            source.list_metrics("妈妈", limit=20),
            key=lambda row: row["value1"],
        )
        assert source.delete_metric(rows[0]["id"]) is True
        backup = source.export_data()
    finally:
        source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup)
        local = sorted(
            target.list_metrics("妈妈", limit=20),
            key=lambda row: row["value1"],
        )
        assert [row["value1"] for row in local] == [70.0, 80.0]
        assert target.delete_metric(local[0]["id"]) is True
        clock["now"] = "2026-11-15T10:02:00"
        assert target.update_metric(local[1]["id"], value1=75)
        clock["now"] = "2026-11-15T10:03:00"
        result = target.import_data(backup)
        values = sorted(
            row["value1"] for row in target.list_metrics("妈妈", limit=20)
        )
        # 被删的 70 必须补回；本地编辑的 75 保留；不再永久丢失
        assert 70.0 in values
        assert 75.0 in values
        assert result["metrics"] >= 1
        again = target.import_data(backup)
        assert again["metrics"] == 0
        after = sorted(
            row["value1"] for row in target.list_metrics("妈妈", limit=20)
        )
        assert after == values
    finally:
        target.close()


def test_reminder_multi_candidate_ignores_tail_only(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    _freeze_clock(monkeypatch)
    first = SessionStore(str(tmp_path / "first.db"))
    try:
        first.save_profile("妈妈", {"sex": "女"})
        first.add_reminder("甲", "2026-12-01T09:00", profile="妈妈")
        first.add_reminder("乙", "2026-12-01T09:00", profile="妈妈")
        with first._lock, first._conn:
            first._conn.execute(
                "UPDATE reminders SET created_at = ?, updated_at = ?",
                ("2026-11-15T10:00:00", "2026-11-15T10:00:00"),
            )
        backup_a = first.export_data()
    finally:
        first.close()
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data(backup_a)
        backup_b = {
            "exported_at": "2026-11-15T10:00:30",
            "reminders": [
                {"id": 1, "profile": "妈妈", "title": "丙",
                 "due_at": "2026-12-01T09:00", "note": "", "done": 0,
                 "advance_days": 0, "repeat_days": 0, "repeat_until": "",
                 "created_at": "2026-11-15T10:00:00",
                 "updated_at": "2026-11-15T10:00:00"},
            ],
        }
        result = target.import_data(backup_b)
        titles = sorted(
            item["title"] for item in target.list_reminders(True)
        )
        assert result["reminders"] == 1
        assert titles == ["丙", "乙", "甲"]
        assert target.import_data(backup_b)["reminders"] == 0
    finally:
        target.close()


def test_anchor_sole_claim_requires_soft_or_edit(monkeypatch, tmp_path):
    from memory.session_store import SessionStore

    _freeze_clock(monkeypatch)
    target = SessionStore(str(tmp_path / "target.db"))
    try:
        target.save_profile("妈妈", {"sex": "女"})
        target.import_data({"reminders": [
            {"id": 10, "profile": "妈妈", "title": "丙",
             "due_at": "2026-12-01T09:00", "note": "", "done": 0,
             "created_at": "2026-11-15T10:00:00",
             "updated_at": "2026-11-15T10:00:00"},
        ]})
        backup = {"reminders": [
            {"id": 1, "profile": "妈妈", "title": "甲",
             "due_at": "2026-12-01T09:00", "note": "", "done": 0,
             "created_at": "2026-11-15T10:00:00",
             "updated_at": "2026-11-15T10:00:00"},
            {"id": 2, "profile": "妈妈", "title": "乙",
             "due_at": "2026-12-01T09:00", "note": "", "done": 0,
             "created_at": "2026-11-15T10:00:00",
             "updated_at": "2026-11-15T10:00:00"},
        ]}
        result = target.import_data(backup)
        titles = sorted(
            item["title"] for item in target.list_reminders(True)
        )
        assert result["reminders"] == 2
        assert titles == ["丙", "乙", "甲"]
        # 重复导入幂等
        assert target.import_data(backup)["reminders"] == 0
        assert sorted(
            item["title"] for item in target.list_reminders(True)
        ) == ["丙", "乙", "甲"]
    finally:
        target.close()


def test_import_rejects_path_separator_profile_names(store):
    result = store.import_data({
        "profiles": [
            {"name": "a/b", "context": {"sex": "女"}},
            {"name": "..", "context": {"sex": "女"}},
            {"name": "正常", "context": {"sex": "女"}},
        ],
    })
    names = [item["name"] for item in store.list_profiles()]
    assert names == ["正常"]
    assert result["invalid"] >= 2


# ---------------------------------------------------------------- 复诊
def test_appointment_note_preserved_on_downgrade(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-10-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    updated = store.update_appointment_partial(
        appointment_id,
        scheduled_at="2026-09-15T09:00",
        note="医生说这次要带化验单",
    )
    assert updated is not None
    assert updated["note"] == "医生说这次要带化验单"
    assert int(updated["owner_visit_id"] or 0) == 0


def test_update_visit_followup_date_cascades_unlinked(store, monkeypatch):
    from api_server import _schedule_followup_sync

    clock = _freeze_clock(monkeypatch)
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-14",
    )
    first = _schedule_followup_sync(store, visit["id"])
    appointment_id = first["appointment"]["id"]
    clock["now"] = "2026-11-15T10:00:10"
    backup = store.export_data()
    store.update_appointment_partial(appointment_id, status="cancelled")
    store.update_appointment_partial(
        appointment_id, scheduled_at="2026-12-25T09:00",
    )
    store.delete_visit_record(visit["id"])
    store.update_appointment_partial(appointment_id, status="upcoming")
    clock["now"] = "2026-11-15T10:00:30"
    store.import_data(backup)
    visits = store.list_visit_records("妈妈")
    assert len(visits) == 1
    imported_visit = visits[0]
    assert int(imported_visit["followup_appointment_id"] or 0) == 0
    assert any(
        int(item.get("owner_visit_id") or 0) == imported_visit["id"]
        for item in store.list_appointments("妈妈", None)
    )
    clock["now"] = "2026-11-15T10:00:40"
    store.update_visit_record_partial(
        imported_visit["id"], next_followup_date="2027-01-10",
    )
    remaining = [
        item for item in store.list_appointments("妈妈", None)
        if item["status"] == "upcoming"
        and (
            int(item.get("owner_visit_id") or 0) == imported_visit["id"]
            or f"由就诊记录 #{imported_visit['id']} 自动创建"
            in str(item.get("note") or "")
        )
    ]
    assert remaining == []
    pending = store.list_reminders(False, "妈妈")
    assert pending == []


def test_schedule_followup_rejects_past_date(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    visit = store.add_visit_record(
        "妈妈", visit_date=str(today - timedelta(days=30)),
        diagnosis="高血压",
        next_followup_date=str(today - timedelta(days=5)),
    )
    response = client.post(
        f"/api/visits/{visit['id']}/schedule-followup"
    )
    assert response.status_code == 400
    assert "复诊日期已过期" in response.json()["detail"]
    assert store.list_appointments("妈妈", None) == []


# ---------------------------------------------------------------- 报表/安全
def test_bp_attention_uses_independent_peaks(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric(
        "妈妈", "bp", 145, 95, measured_at=f"{today}T08:00",
    )
    store.add_metric(
        "妈妈", "bp", 125, 101, measured_at=f"{today}T20:00",
    )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in data["profiles"] if item["name"] == "妈妈"
    )
    assert member["attention"]["level"] == "warning"
    assert "101" in member["attention"]["detail"]
    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    bp = next(
        item for item in insights["insights"]
        if item["key"] == "metric_bp_single"
    )
    assert bp["level"] == "warning"


def test_insights_worst_medication_not_diluted(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("爸爸", {"sex": "男"})
    frequent = store.add_medication(
        "爸爸", "阿卡波糖", "50mg", "每日三次",
        started_at=str(today - timedelta(days=9)),
    )
    never = store.add_medication(
        "爸爸", "阿托伐他汀", "20mg", "每日一次",
        started_at=str(today - timedelta(days=9)),
    )
    assert never is not None
    for index in range(27):
        day = today - timedelta(days=index // 3)
        store.add_medication_log(
            frequent["id"], taken_at=f"{day}T08:00"
        )
    insights = client.get(
        "/api/health/insights", params={"profile": "爸爸", "days": 10}
    ).json()
    adherence = [
        item for item in insights["insights"]
        if item["key"] == "adherence"
    ]
    assert adherence
    assert adherence[0]["level"] == "warning"
    assert "阿托伐他汀" in adherence[0]["title"]
    assert "0%" in adherence[0]["title"]


def test_family_attention_weekly_skips(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    store.save_profile("爷爷", {"sex": "男"})
    medication = store.add_medication(
        "爷爷", "二甲双胍", "0.5g", "每日三次",
        started_at=str(today - timedelta(days=30)),
    )
    for index in range(18):
        day = today - timedelta(days=index // 3)
        store.add_medication_log(
            medication["id"], taken_at=f"{day}T09:00"
        )
    for offset in (2, 3, 4):
        store.add_medication_log(
            medication["id"],
            taken_at=f"{today - timedelta(days=offset)}T12:00",
            skipped=True, skip_reason="forget",
        )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in data["profiles"] if item["name"] == "爷爷"
    )
    assert member["medications"]["skipped_week"] == 3
    assert member["attention"]["level"] == "warning"
    assert "漏服 3 次" in member["attention"]["label"]
    csv_text = client.get(
        "/api/family/overview/export.csv", params={"days": 7}
    ).text
    assert "近 7 天未服 3 次" in csv_text


def test_risk_predicate_negation_cross_level():
    import api_server

    for text in (
        "不符合低危标准属于高危",
        "未达到极高危但属于高危",
        "未发现低危表现属于高危",
        "不支持低危诊断应为高危",
    ):
        assert api_server.parse_risk_level(text) == "high", text
    for text in (
        "未发现高危表现。",
        "暂未发现高危征象。",
        "未达到高危标准。",
        "不符合高危特征。",
    ):
        assert api_server.parse_risk_level(text) == "", text
    assert api_server.parse_risk_level("不能排除高危。") == "high"


def test_profile_name_path_separators_rejected(client_and_store):
    client, _store = client_and_store
    for name in ("妈妈/外婆", "a\\\\b", "..", "."):
        response = client.post(
            "/api/profiles",
            json={"name": name, "context": {"sex": "女"}},
        )
        assert response.status_code == 422, name
    ok = client.post(
        "/api/profiles",
        json={"name": "正常", "context": {"sex": "女"}},
    )
    assert ok.status_code == 200
    rename = client.patch(
        "/api/profiles/正常", json={"new_name": "正常/错误"}
    )
    assert rename.status_code == 422


def test_ipv6_global_failure_budget(monkeypatch, tmp_path):
    import api_server
    import memory
    from fastapi.testclient import TestClient
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r59-secret")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    limiter = api_server.auth_failure_limiter_ipv6_global
    with limiter._lock:
        limiter._hits["ipv6"] = [time.monotonic()] * limiter.limit
    try:
        client = TestClient(
            api_server.app,
            base_url="http://localhost",
            client=("2001:db8:aaaa:1::1", 56000),
        )
        response = client.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        )
        assert response.status_code == 429
        payload = response.json()
        assert payload["code"] == "auth_rate_limited"
        # 全局预算满：不是按 /64 的「排队过多」
        assert "错误次数过多" in payload["detail"]
    finally:
        with limiter._lock:
            limiter._hits.pop("ipv6", None)
        store.close()


# ---------------------------------------------------------------- 前端
def test_frontend_round59_guards():
    app_js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    send_start = app_js.index("async function send(question)")
    send = app_js[
        send_start:app_js.index(
            "const controller = new AbortController();", send_start
        )
    ]
    # 发送时快照档案，abort/失败恢复主诉后守住草稿归属
    assert "const sendProfile = activeProfileName || readStoredProfile() || '';" in send
    assert app_js.count("preserveDraftProfile(sendProfile, question);") >= 3
    assert "function preserveDraftProfile(" in app_js
    # 输入时立即同步草稿档案（不等 200ms 防抖）
    input_handler = app_js[
        app_js.index("els.input.addEventListener('input'"):
        app_js.index("els.input.addEventListener('keydown'")
    ]
    assert "DRAFT_PROFILE_KEY" in input_handler
    # 重命名/删除档案同步草稿归属
    assert "updateDraftProfile(name, data.name);" in app_js
    assert "updateDraftProfile(name, '');" in app_js
    # 删除档案 404 后重新确认确实消失
    delete_block = app_js[
        app_js.index("async function deletePatientProfile()"):
        app_js.index("async function applySelectedProfile()")
    ]
    assert "档案未能删除" in delete_block
    # 档案缓存无原型污染
    assert "let profilesCache = Object.create(null);" in app_js
