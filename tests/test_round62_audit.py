"""第六十二轮审查：患儿主诉化疗/抗凝语境、剖宫产术后出血、又吃了剂量
宾语、第三人称定义问句、药名后置既往/否定、导入孪生归属与改挂恢复、
就诊重复链接、CSV 提醒裸预约 ID、IPv6 全局预算闸门/共享桶、目标成员
窗口超标与重度单次血压、停用药物漏服成员卡、前端档案恢复快照与在途
文案对账。"""
import csv
import io
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
        "MEDIX_ALLOW_INSECURE_LAN", "MEDIX_RATE_LIMIT",
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


# ---------------------------------------------------------------- 分诊
def test_child_profile_complaint_chemo_and_anticoag():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text("我儿子正在化疗，今天发烧38度"),
        {"age": "5岁"},
    )
    assert "免疫功能低下伴发热" in result["matches"]
    # 未填年龄同样按患儿处理
    result = detect_red_flags(
        patient_complaint_text("我女儿正在化疗，今天发烧38.5度"), {}
    )
    assert "免疫功能低下伴发热" in result["matches"]
    result = detect_red_flags(
        patient_complaint_text("我儿子在吃华法林，昨天头撞到门框"),
        {"age": "3岁"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 成年档案仍排除家属分句
    result = detect_red_flags(
        patient_complaint_text("我儿子正在化疗，今天发烧38度"),
        {"age": "40岁"},
    )
    assert result["level"] == "none"


def test_caesarean_postop_bleeding_without_duration():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "剖宫产术后出血",
        "剖宫产术后，阴道出血",
        "剖宫产术后阴道出血",
        "剖宫产刚做完，出血了",
        "剖腹产术后，阴道流血",
        "产妇剖宫产术后流血不止",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text
    # 远期剖宫产史不算当前妊娠
    for text in (
        "剖宫产术后5年，阴道出血",
        "剖宫产5年了，阴道出血",
        "剖腹产术后多年",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert result["level"] == "none", text
    result = detect_red_flags(
        patient_complaint_text("阴道出血"), {"history": "剖宫产5年了"}
    )
    assert result["level"] == "none"


def test_restart_dose_quantifier_counts_as_same_medication():
    from core.triage import detect_red_flags, patient_complaint_text

    result = detect_red_flags(
        patient_complaint_text(
            "华法林停了半年，今天又吃了两片，头撞到门框"
        ),
        {},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 明确换用另一种药仍是换药：不能复活既往抗凝状态
    result = detect_red_flags(
        patient_complaint_text(
            "华法林停了半年，今天又吃了布洛芬，头撞到门框"
        ),
        {},
    )
    assert result["level"] == "none"


def test_third_person_definition_questions_keep_symptoms():
    from core.triage import detect_red_flags, patient_complaint_text

    for text, label in (
        ("他抽搐是什么病", "抽搐"),
        ("请解释一下他胸痛", "胸痛"),
        ("请解释一下她的呼吸困难", "呼吸困难"),
        ("她的呼吸困难是什么病", "呼吸困难"),
        ("他的胸痛是什么病", "胸痛"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    for text in (
        "胸痛是什么病", "抽搐是什么病", "他们抽搐是什么病",
        "其他胸痛是什么病", "他汀类药物胸痛是什么病",
    ):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


def test_medication_past_and_negated_after_drug_name():
    from core.medication_safety import check_medication_safety
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "阿司匹林以前吃过，现在吃布洛芬",
        "阿司匹林我没在吃，能吃布洛芬吗",
        "我没在吃阿司匹林，能吃布洛芬吗",
        "以前吃过阿司匹林，现在吃布洛芬",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text
    # 裸「吃过了」是今天已服药，不是停药
    assert (
        check_medication_safety("阿司匹林吃过了，现在吃布洛芬", {})["level"]
        == "warning"
    )
    for text in (
        "华法林我没在吃，昨天头撞到门框",
        "华法林以前吃过，昨天头撞到门框",
    ):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


# ---------------------------------------------------------------- 导入
def _reminder_rows(store):
    with store._lock:
        return [
            (int(row["id"]), row["profile"], row["title"], row["note"],
             int(row["done"]), int(row["advance_days"]))
            for row in store._conn.execute(
                "SELECT id, profile, title, note, done, advance_days"
                " FROM reminders ORDER BY id"
            ).fetchall()
        ]


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


def test_import_edit_then_reimport_is_idempotent(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"reminders": [
        {"id": 101, "profile": "妈妈", "title": "吃药", "note": "",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "爸爸", "title": "喝水", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 0, "done": 1,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 107, "profile": "妈妈", "title": "喝水", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 1,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = _reminder_rows(store)
    target = next(r for r in rows if r[1] == "妈妈" and r[2] == "喝水")
    # 前端 PATCH 保存表单：全字段不变也会刷新 updated_at
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET updated_at = '2026-11-15T12:00:00'"
            " WHERE id = ?",
            (target[0],),
        )
    assert store.import_data(backup).get("reminders") == 0
    assert len(_reminder_rows(store)) == 3


def test_moved_appointment_restored_without_stealing_twin(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"appointments": [
        {"id": 101, "profile": "妈妈", "title": "复诊0", "note": "n0",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "爸爸", "title": "复诊1", "note": "n1",
         "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = _appointment_rows(store)
    movable = next(r for r in rows if r[2] == "复诊0")
    doomed = next(r for r in rows if r[2] == "复诊1")
    store.update_appointment_partial(movable[0], profile="爸爸")
    store.delete_appointment(doomed[0])
    # 同秒孪生无法区分改挂与改名同化：按归属优先补回被删的复诊1，
    # 改挂行可能保留一条旧版本副本（宁重复勿丢记录）
    assert store.import_data(backup).get("appointments") == 2
    final = _appointment_rows(store)
    titles = sorted((r[1], r[2], r[3]) for r in final)
    assert ("爸爸", "复诊1", "n1") in titles
    assert len([row for row in titles if row[1] == "复诊0"]) == 2
    assert store.import_data(backup).get("appointments") == 0


def test_completed_reminder_not_reassigned_by_cross_tail(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"reminders": [
        {"id": 101, "profile": "妈妈", "title": "吃药", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "爸爸", "title": "复查", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 1,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = _reminder_rows(store)
    a_row = next(r for r in rows if r[1] == "妈妈")
    b_row = next(r for r in rows if r[1] == "爸爸")
    store.complete_reminder(a_row[0])
    store.delete_reminder(b_row[0])
    assert store.import_data(backup).get("reminders") == 1
    final = _reminder_rows(store)
    assert sorted((r[1], r[2], r[4]) for r in final) == [
        ("妈妈", "吃药", 1), ("爸爸", "复查", 1),
    ]
    assert store.import_data(backup).get("reminders") == 0


def test_moved_reminder_title_matching_sibling_not_orphaned(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"reminders": [
        {"id": 101, "profile": "妈妈", "title": "吃药", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "爸爸", "title": "吃药", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 107, "profile": "爸爸", "title": "喝水", "note": "n1",
         "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.import_data(backup)
    rows = _reminder_rows(store)
    x_row = next(r for r in rows if r[1] == "妈妈")
    z_row = next(r for r in rows if r[1] == "爸爸" and r[2] == "喝水")
    store.update_reminder(x_row[0], profile="爸爸")
    store.delete_reminder(z_row[0])
    assert store.import_data(backup).get("reminders") == 1
    final = _reminder_rows(store)
    assert sorted((r[1], r[2]) for r in final) == [
        ("爸爸", "吃药"), ("爸爸", "吃药"), ("爸爸", "喝水"),
    ]


def test_import_does_not_relink_duplicate_visit_appointment(store):
    store.save_profile("Bob", {})
    store.save_profile("Alice", {})
    visit = store.add_visit_record(
        "Bob", visit_date="2026-11-01", diagnosis="Fracture",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "Bob", "复诊：Fracture", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "复诊提醒", "2026-12-19", profile="Bob",
        appointment_id=appt["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment_id=appt["id"],
        reminder_id=reminder["id"],
    )
    backup = store.export_data()
    store.update_appointment_partial(appt["id"], profile="Alice")
    store.import_data(backup)
    store.update_appointment_partial(appt["id"], profile="Bob")
    store.import_data(backup)
    old_after = store.get_appointment(appt["id"])
    assert int(old_after.get("owner_visit_id") or 0) == 0
    assert "由就诊记录 #" not in str(old_after.get("note") or "")
    linked = [
        item for item in store.list_appointments("Bob", None)
        if item["status"] == "upcoming"
        and int(item.get("owner_visit_id") or 0) == visit["id"]
    ]
    assert len(linked) == 1


def test_csv_reminder_appointment_link_requires_hint(store):
    store.save_profile("Alice", {})
    local_appt = store.add_appointment(
        "Alice", "口腔科", scheduled_at="2026-12-01T09:00",
    )
    store.import_data({"reminders": [
        {"profile": "Alice", "title": "复诊：高血压",
         "note": "复诊 2026-12-13 心内科", "due_at": "2026-12-13T09:00",
         "advance_days": 1, "done": 0,
         "appointment_id": local_appt["id"]},
    ]})
    rows = store.list_reminders(False, "Alice")
    bad = next(r for r in rows if r["title"] == "复诊：高血压")
    assert int(bad["appointment_id"] or 0) == 0
    store.import_data({"reminders": [
        {"profile": "Alice", "title": "复诊：口腔科",
         "note": "复诊 2026-12-01", "due_at": "2026-11-30T09:00",
         "advance_days": 1, "done": 0,
         "appointment_id": local_appt["id"]},
    ]})
    rows = store.list_reminders(False, "Alice")
    good = next(r for r in rows if r["title"] == "复诊：口腔科")
    assert int(good["appointment_id"] or 0) == local_appt["id"]


# ---------------------------------------------------------------- 安全
@pytest.fixture
def auth_client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r62-secret-token")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(100, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(2, window=300),
    )
    session_store = SessionStore(str(tmp_path / "auth.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: session_store)
    try:
        yield TestClient, api_server, session_store
    finally:
        session_store.close()


def test_gate_branch_enforces_ipv6_global_budget(auth_client):
    TestClient, api_server, _store = auth_client
    codes = []
    for index in (1, 2, 3):
        proxy = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 57000 + index),
        )
        resp = proxy.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": f"2001:db8:{index}::9, 127.0.0.1",
            },
        )
        codes.append((resp.status_code, resp.json().get("code")))
    assert codes[0] == (401, "auth_required")
    assert codes[1] == (401, "auth_required")
    assert codes[2] == (429, "auth_rate_limited")
    proxy = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 57010),
    )
    ok = proxy.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": "r62-secret-token",
            "X-Forwarded-For": "2001:db8:9::9, 127.0.0.1",
        },
    )
    assert ok.status_code == 200


def test_shared_loopback_bucket_blocks_before_compare(
    auth_client, monkeypatch
):
    TestClient, api_server, _store = auth_client
    from core.ratelimit import SlidingWindowLimiter

    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(2, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_ipv6_global",
        SlidingWindowLimiter(100, window=300),
    )
    calls = {"n": 0}
    original = api_server.is_authorized

    def counting_authorized(request):
        calls["n"] += 1
        return original(request)

    monkeypatch.setattr(api_server, "is_authorized", counting_authorized)
    codes = []
    for index in (10, 11, 12, 13):
        proxy = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 57020 + index),
        )
        resp = proxy.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "bad",
                "X-Forwarded-For": f"203.0.113.{index}",
            },
        )
        codes.append((resp.status_code, resp.json().get("code")))
    assert codes[0][0] == 401 and codes[1][0] == 401
    assert codes[2][0] == 429
    # 共享桶已满后不再执行令牌比较
    assert calls["n"] == 2, calls["n"]
    proxy = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 57030),
    )
    ok = proxy.get(
        "/api/sessions",
        headers={
            "X-Medix-Token": "r62-secret-token",
            "X-Forwarded-For": "203.0.113.200",
        },
    )
    assert ok.status_code == 200


# ---------------------------------------------------------------- 报表
def test_family_attention_window_target_readings(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 140, "value2": 90,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 165, "value2": 100,
        "measured_at": f"{_date(-2)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
        "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    mom = next(p for p in data["profiles"] if p["name"] == "妈妈")
    assert mom["attention"]["level"] != "ok"
    assert "血压" in mom["attention"]["label"]
    assert "165" in mom["attention"]["detail"]

    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "glucose", "value1": 8.0,
    })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 9.2,
        "context": "fasting", "measured_at": f"{_date(-3)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 7.0,
        "context": "fasting", "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    grandma = next(p for p in data["profiles"] if p["name"] == "奶奶")
    assert "血糖" in grandma["attention"]["label"]


def test_profile_bp_single_warning_survives_target_alert(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 140, "value2": 90,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 175, "value2": 105,
        "measured_at": f"{_date(0)}T08:00",
    })
    insights = client.get(
        "/api/health/insights",
        params={"days": 30, "profile": "妈妈"},
    ).json()["insights"]
    # 第六十三轮起：已设个体化目标时不再输出通用阈值告警，
    # 重度峰值由目标告警升级为 warning（175/105 超目标且 ≥160/100）
    bp_single = [
        item for item in insights if item["key"] == "metric_bp_single"
    ]
    assert not bp_single
    target_alert = [
        item for item in insights if item["key"] == "metric_bp"
    ]
    assert target_alert and target_alert[0]["level"] == "warning"


def test_stopped_medication_skips_keep_member_card(client_and_store):
    client, store = client_and_store
    med = store.add_medication(
        "", "阿司匹林", dose="100mg", frequency="每日一次",
        started_at=_date(-10),
    )
    for day in range(1, 4):
        assert store.add_medication_log(
            med["id"], f"{_date(-day)}T08:00", "", skipped=True,
            skip_reason="forget",
        ) is not None
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET active = 0 WHERE id = ?",
            (med["id"],),
        )
    data = client.get("/api/family/overview", params={"days": 7}).json()
    blank = next(p for p in data["profiles"] if p["name"] == "")
    assert blank["attention"]["label"].startswith("近 7 天漏服")
    resp = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    assert any(
        "近 7 天未服 3 次" in cell for row in rows for cell in row
    )


# ---------------------------------------------------------------- 前端静态
def test_frontend_round62_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 恢复流程按服务端绑定更新档案：用选中序判断真实换档并刷新快照
    assert "const sendProfileSeq = profileSelectionSeq;" in source
    assert "let sendProfile = activeProfileName" in source
    assert (
        "sendProfile = (\n    els.profileSelect.value || activeProfileName"
        in source
    )
    assert "profileSelectionSeq !== sendProfileSeq" in source
    # 草稿被重新归属后结束「未确认接管」状态
    assert "detachedComposerText = '';" in source
    assert (
        "if (detachedComposerText && els.input.value === detachedComposerText)"
        in source
    )
    # 启动对账只清理来自在途记录的文本
    assert "let inflightRestoredText = '';" in source
    assert "inflightRestoredText = question;" in source
    # 第六十八轮起：允许在途主诉与后续草稿合并，按主诉本身判定落库
    assert "els.input.value.includes(inflightText)" in source
    assert "inflightQuestionText" in source
