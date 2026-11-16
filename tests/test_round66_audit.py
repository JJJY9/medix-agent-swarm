"""第六十六轮审查：定义问句中文时长、头部外伤口语语序、停药后重启、
病史同句现孕与产后多年、远期停药覆盖「没吃完」、科普引述动作词、
否定程度词、遗留共享提醒链接、导入会话编号校验、安排复诊共享链接、
周报上期续聊会诊、停药打卡成员卡、下限目标最低值、未声明代理对端。"""
import json
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
    attachment_dir = tmp_path / "attachments"
    monkeypatch.setattr(api_server, "ATTACHMENT_DIR", attachment_dir)
    try:
        yield TestClient(api_server.app), session_store, attachment_dir
    finally:
        session_store.close()


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


# ---------------------------------------------------------------- 分诊
def test_definition_question_minutes_duration():
    for text in (
        "胸痛十分钟了是什么病",
        "胸痛一个小时了是什么病",
        "抽搐十分钟了是什么病",
        "胸痛十几分钟了是什么病",
        "胸痛10分钟了是什么病",
        # 频次/发作形态与「个多」连用同样是真实病程
        "抽搐好几次了是什么病",
        "胸痛一阵一阵的是什么病",
        "胸痛一阵子的是什么病",
        "胸痛时好时坏的是什么病",
        "胸痛一个多小时了是什么病",
    ):
        result = _red_flags(text)
        assert result["level"] != "none", text


def test_head_trauma_adverbs_and_swelling():
    context = {"medications": "华法林"}
    for text in (
        "我吃华法林，头不小心撞到墙",
        "我吃华法林，头一下子撞到门框",
        "我吃华法林，后脑勺上磕了个包",
        "我吃华法林，脑袋上磕了个包",
    ):
        result = _red_flags(text, context)
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_restart_after_stop_recognized():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林停了，现在又在吃，能吃布洛芬吗",
        "阿司匹林停了，现在又在吃，还能吃布洛芬吗",
        "华法林停了，现在还在吃，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    triage = _red_flags(
        "华法林停了，现在又在吃，今天头撞到门框",
        {"medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in triage["matches"]


def test_history_same_clause_pregnancy():
    for history in ("剖宫产术后3年现在怀孕8周", "产后两年又怀孕了"):
        result = _red_flags("阴道出血", {"history": history})
        assert "孕产期出血" in result["matches"], history


def test_long_stop_overrides_unfinished_dose():
    from core.medication_safety import check_medication_safety

    assert _red_flags(
        "华法林没吃完，已经停药半年了，今天头撞到门框", {}
    )["level"] == "none"
    assert _red_flags(
        "甲氨蝶呤没吃完，化疗结束五年了，今天发烧38.9度", {}
    )["level"] == "none"
    assert check_medication_safety(
        "阿司匹林没有吃完，已经停药一年了，能吃布洛芬吗", {}
    )["level"] == "none"
    # 停药分句离得较远（隔着多段生活描述）同样必须生效
    assert _red_flags(
        "华法林没吃完，最近睡眠不好，胃口也差，已经停药半年了，"
        "今天头撞到门框", {}
    )["level"] == "none"
    assert _red_flags(
        "甲氨蝶呤没吃完，身体一直不错，复查也都正常，化疗结束五年了，"
        "今天发烧38.9度", {}
    )["level"] == "none"
    assert check_medication_safety(
        "阿司匹林没吃完，最近睡眠一般，胃口也还可以，大小便正常，"
        "已经停药一年了，能吃布洛芬吗", {}
    )["level"] == "none"
    # 没有停药证据时「没吃完」仍是当前用药
    assert "抗凝治疗期间头部外伤" in _red_flags(
        "华法林还没吃完，今天头撞到门框", {}
    )["matches"]


def test_education_quote_actions_not_personal():
    for text in (
        "网上说吃完药后胸痛要马上就医",
        "文章讲运动后胸痛要警惕",
        "新闻里说吃完药后胸痛是心梗前兆",
        "视频教程里说吃完药呼吸困难要打120",
    ):
        assert _red_flags(text)["level"] == "none", text
    # 本人动作线索仍按真实主诉保留
    assert "胸痛" in _red_flags("网上买的药吃了胸痛")["matches"]


def test_history_postpartum_years_variants():
    for history in ("产后十多年", "顺产后十几年", "流产后多年", "产后好多年"):
        result = _red_flags("今天阴道出血", {"history": history})
        assert "孕产期出血" not in result["matches"], history


def test_negated_degree_modifiers():
    for text in (
        "无发热、胸痛不明显",
        "没有发烧、呼吸困难不明显",
        "无发热、胸痛不剧烈",
    ):
        assert _red_flags(text)["level"] == "none", text
    # 正例仍然保留
    assert "胸痛" in _red_flags("无发热、胸痛剧烈")["matches"]


# ---------------------------------------------------------------- 就诊
def test_shared_reminder_link_keeps_owner_visit_reminder(store):
    store.save_profile("妈妈", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01"
    )["id"]
    vb = store.add_visit_record(
        "妈妈", diagnosis="乙", visit_date="2026-11-02"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{va} 自动创建", owner_visit_id=vb,
    )["id"]
    reminder = store.add_reminder(
        "复诊：乙", "2026-12-20", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.set_visit_followup_links(vb, appointment, reminder)
    store._conn.execute(
        "UPDATE visit_records SET followup_appointment_id = ?,"
        " followup_reminder_id = ? WHERE id = ?",
        (appointment, reminder, va),
    )
    store._conn.commit()
    store.update_visit_record(
        va, "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-27",
    )
    # 非归属就诊改复诊日期不能删除归属就诊的待办提醒
    assert store._conn.execute(
        "SELECT id FROM reminders WHERE id = ?", (reminder,)
    ).fetchone() is not None
    assert store._conn.execute(
        "SELECT id FROM appointments WHERE id = ?", (appointment,)
    ).fetchone() is not None
    vb_row = store._conn.execute(
        "SELECT followup_reminder_id FROM visit_records WHERE id = ?",
        (vb,),
    ).fetchone()
    assert int(vb_row["followup_reminder_id"]) == reminder


def test_schedule_followup_with_legacy_shared_link(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-20",
    )["id"]
    vb = store.add_visit_record(
        "妈妈", diagnosis="乙", visit_date="2026-11-02"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{va} 自动创建", owner_visit_id=vb,
    )["id"]
    store.set_visit_followup_links(vb, appointment, 0)
    store._conn.execute(
        "UPDATE visit_records SET followup_appointment_id = ? WHERE id = ?",
        (appointment, va),
    )
    store._conn.commit()
    result = _schedule_followup_sync(store, va)
    assert result["appointment"]["id"] != appointment
    refreshed = store.get_visit_record(va)
    assert int(refreshed["followup_appointment_id"]) == int(
        result["appointment"]["id"]
    )
    # 归属就诊的预约不受影响
    assert store.get_appointment(appointment) is not None


# ---------------------------------------------------------------- 导入
def _visit(rid, profile, diagnosis, day="2026-05-01"):
    return {
        "id": rid, "profile": profile, "visit_date": day,
        "diagnosis": diagnosis, "created_at": CREATED,
        "updated_at": CREATED,
    }


def test_import_visit_cross_member_twin_restores_deleted(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    noise = "2026-01-01T00:00:00"
    with store._lock, store._conn:
        for profile, day, diagnosis in (
            ("妈妈", "2026-05-02", "噪声A"), ("爸爸", "2026-05-03", "噪声B"),
        ):
            store._conn.execute(
                "INSERT INTO visit_records(profile, visit_date, diagnosis,"
                " created_at, updated_at) VALUES (?,?,?,?,?)",
                (profile, day, diagnosis, noise, noise),
            )
    backup = {"visit_records": [
        _visit(1, "妈妈", "诊断0"),
        _visit(2, "爸爸", "诊断0"),
        _visit(3, "妈妈", "诊断1"),
    ]}
    assert store.import_data(backup).get("visit_records") == 3
    store._conn.execute(
        "UPDATE visit_records SET diagnosis = '诊断改',"
        " updated_at = '2026-11-16T10:00:00' WHERE id = 3"
    )
    store._conn.execute(
        "UPDATE visit_records SET profile = '妈妈',"
        " updated_at = '2026-11-16T10:00:01' WHERE id = 4"
    )
    store._conn.execute("DELETE FROM visit_records WHERE id = 5")
    store._conn.commit()
    store.import_data(backup)
    diagnoses = {
        row["diagnosis"] for row in store._conn.execute(
            "SELECT diagnosis FROM visit_records"
        ).fetchall()
    }
    # 被删记录（诊断1）必须补回；改名+改挂的存活行保留
    assert "诊断1" in diagnoses
    assert "诊断0" in diagnoses
    assert store.import_data(backup).get("visit_records") == 0


def test_import_symptom_content_edit_no_duplicate(store):
    store.save_profile("妈妈", {})
    noise = "2026-01-01T00:00:00"
    with store._lock, store._conn:
        for name in ("噪声A", "噪声B"):
            store._conn.execute(
                "INSERT INTO symptoms(profile, symptom, severity, note,"
                " recorded_at, created_at, updated_at)"
                " VALUES ('妈妈', ?, 1, '', '2026-01-01T00:00', ?, ?)",
                (name, noise, noise),
            )
    backup = {"symptoms": [
        {"id": 1, "profile": "妈妈", "symptom": "头痛", "severity": 3,
         "note": "n0", "recorded_at": "2026-05-01T00:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "妈妈", "symptom": "头痛", "severity": 3,
         "note": "n1", "recorded_at": "2026-05-01T00:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    assert store.import_data(backup).get("symptoms") == 2
    store.update_symptom_partial(3, recorded_at="2026-05-09T00:00")
    # 本地无缺行：编辑内容列后重复导入不能补插编辑前旧副本
    assert store.import_data(backup).get("symptoms") == 0
    rows = store.list_symptoms("妈妈", 100)
    assert len(rows) == 4


def test_schedule_followup_rejects_expired_date_inside_lock(store):
    from fastapi import HTTPException

    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-11-10",
    )
    try:
        _schedule_followup_sync(store, visit["id"])
    except HTTPException as exc:
        assert exc.status_code == 400
    else:
        raise AssertionError("过期复诊日期必须被拒绝")
    assert store.list_appointments(None) == []


def test_import_session_id_rejects_path_separators(store):
    result = store.import_data({"sessions": [
        {"id": "a/b", "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]},
        {"id": "a%2Fb", "messages": [
            {"role": "user", "content": "x"},
        ]},
        {"id": "ok-1", "messages": [
            {"role": "user", "content": "x"},
            {"role": "assistant", "content": "y"},
        ]},
    ]})
    assert result.get("imported") == 1
    assert result.get("invalid", 0) >= 2
    ids = [
        row["id"] for row in store._conn.execute(
            "SELECT id FROM sessions"
        ).fetchall()
    ]
    assert ids == ["ok-1"]


# ---------------------------------------------------------------- 报表
def test_weekly_prev_counts_continued_old_session(client_and_store):
    client, store, _attachments = client_and_store
    now = datetime.now()
    old_created = (now - timedelta(days=20)).strftime("%Y-%m-%dT09:00:00")
    prev_active = (now - timedelta(days=8)).strftime("%Y-%m-%dT09:00:00")
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile) VALUES ('s-old', '', ?, ?, '妈妈')",
            (old_created, prev_active),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata,"
            " created_at) VALUES ('s-old', 'user', '旧问题', '{}', ?)",
            (prev_active,),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata,"
            " created_at) VALUES ('s-old', 'assistant', '旧回答', '{}', ?)",
            (prev_active,),
        )
        today = now.strftime("%Y-%m-%dT09:00:00")
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile) VALUES ('s-new', '', ?, ?, '妈妈')",
            (today, today),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata,"
            " created_at) VALUES ('s-new', 'user', '新问题', '{}', ?)",
            (today,),
        )
        store._conn.execute(
            "INSERT INTO messages(session_id, role, content, metadata,"
            " created_at) VALUES ('s-new', 'assistant', '新回答', '{}', ?)",
            (today,),
        )
    data = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
    ).json()
    consultations = data["consultations"]
    assert consultations["count"] == 1, consultations
    # 创建更早、仅在上期续聊的会话计入上期，不能被算作凭空新增
    assert consultations["prev_count"] == 1, consultations


def test_family_overview_keeps_unassigned_stopped_med_log(
    client_and_store,
):
    client, store, _attachments = client_and_store
    medication = store.add_medication("", "阿司匹林", "100mg")
    today = datetime.now().strftime("%Y-%m-%d")
    store.add_medication_log(
        medication["id"], taken_at=f"{today}T08:00"
    )
    store.update_medication(medication["id"], active=False)
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    unassigned = next(
        (
            item for item in data["profiles"]
            if item.get("name") == ""
        ),
        None,
    )
    assert unassigned is not None
    assert int(unassigned["medications"]["taken_today"]) == 1


def test_lower_bound_target_detail_uses_minimum(client_and_store):
    client, _store, _attachments = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {"sex": "女"}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "sleep", "value1": 7,
    })
    for days, value in ((2, 4), (1, 6), (0, 7.5)):
        client.post("/api/metrics", json={
            "profile": "奶奶", "metric": "sleep", "value1": value,
            "measured_at": f"{_date(-days)}T07:00",
        })
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    grandma = next(
        item for item in data["profiles"] if item["name"] == "奶奶"
    )
    detail = grandma["attention"]["detail"]
    # 睡眠为下限目标：最差（最低）读数必须展示，不能被达标值掩盖
    assert "最低 4" in detail, detail


# ---------------------------------------------------------------- 认证
def _clear_auth_limiters():
    import api_server

    for limiter in (
        api_server.auth_failure_limiter,
        api_server.auth_failure_limiter_loopback,
        api_server.auth_failure_limiter_ipv6_global,
    ):
        with limiter._lock:
            limiter._hits.clear()


def test_trust_enabled_undeclared_peer_without_headers(
    monkeypatch, tmp_path
):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r66-secret-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.delenv("MEDIX_PROXY_CLIENT_IP_HEADER", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    session_store = SessionStore(str(tmp_path / "peer66.db"))
    monkeypatch.setattr(
        memory, "get_session_store", lambda: session_store
    )
    _clear_auth_limiters()
    try:
        client = TestClient(
            api_server.app, base_url="http://localhost",
            client=("172.18.0.2", 50101),
        )
        baseline = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "r66-secret-token-0123456789"},
        )
        assert baseline.status_code == 200
        for _ in range(10):
            response = client.get(
                "/api/sessions", headers={"X-Medix-Token": "guess"}
            )
            assert response.status_code == 401
        # 未声明代理对端不带地址头：正确令牌不能被共享键硬预锁定
        victim = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "r66-secret-token-0123456789"},
        )
        assert victim.status_code == 200
    finally:
        _clear_auth_limiters()
        session_store.close()


# ---------------------------------------------------------------- 前端静态
def test_frontend_round66_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 再次会诊保留提交时档案，回车由提交前检查拦截
    assert "questionProfile = ''" in source
    assert "preserveDraftProfile(questionProfile, question);" in source
    assert "sendProfile\n    );" in source
    # 已渲染会话不因一次补恢复失败被静默解绑
    assert "let renderedSessionId = '';" in source
    assert "renderedSessionId !== sessionId" in source
    assert "renderedSessionId = activeSessionId || '';" in source
    # 打开历史会话时在响应返回后重新核对深链草稿冲突
    assert "const draftConflictNow = deepLinkOpen && Boolean(" in source
    assert "const draftConflict = deepLinkDraftConflict || draftConflictNow;" in source
    # 流式回答渲染后同样标记会话已展示，避免补恢复失败解绑刚用过的会话
    assert "renderedSessionId = answerSession;" in source
    # 依从率用单药最差口径
    assert "medications.worst_rate !== null" in source
    assert "依从率 ${adherenceRate}%" in source
    # 停药药物的当日打卡/当周漏服同样渲染
    assert "`用药 今日 ${Number(medications.taken_today)} 次`" in source
    assert "` · 近 7 天未服 ${Number(medications.skipped_week)} 次`" in source
