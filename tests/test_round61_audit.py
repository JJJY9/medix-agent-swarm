"""第六十一轮审查：患儿家属称谓、近期化疗语序、定义问句多症状、头部
起包、剖宫产时效、又吃了量词、导入孪生归属/改挂、就诊改档案迁移、NAT64
全局预算、CSV 依从率、家庭关注目标成员、审计哈希加盐、前端换档/恢复
防护。"""
import hashlib
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
def test_child_profile_family_terms_belong_to_patient():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "我儿子今天发烧38度",
        "我女儿今天发烧38度",
    ):
        result = detect_red_flags(
            patient_complaint_text(text), {"age": "2个月"}
        )
        assert "3 个月以下婴儿发热" in result["matches"], text
        result = detect_red_flags(
            patient_complaint_text(text), {"history": "化疗中"}
        )
        assert "免疫功能低下伴发热" in result["matches"], text
        # 档案未填年龄：同样按患者本人处理（宁严勿漏）
        result = detect_red_flags(
            patient_complaint_text(text), {"history": "正在化疗"}
        )
        assert "免疫功能低下伴发热" in result["matches"], text
    # 儿科档案下的头部外伤同样归属患者
    result = detect_red_flags(
        patient_complaint_text("我儿子今天头撞到桌角"),
        {"age": "2个月", "medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    # 明确成年档案：家属分句仍然排除
    result = detect_red_flags(
        patient_complaint_text("我爸发烧39度"), {"age": "40岁"}
    )
    assert result["level"] == "none"
    assert detect_red_flags(
        patient_complaint_text("我爸发烧39度"), {"age": "2个月"}
    )["level"] == "none"


def test_chemo_recent_end_before_verb_and_past_forms():
    from core.triage import detect_red_flags

    for history in (
        "上周化疗结束", "昨天化疗结束", "两周前化疗结束",
        "刚刚化疗结束", "最近化疗结束",
    ):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert "免疫功能低下伴发热" in result["matches"], history
    for history in (
        "五年前做过化疗", "五年前结束化疗", "五年前做完化疗",
        "五年前停止化疗", "5年前接受过化疗", "五年前进行过化疗",
        "五年前化疗过",
    ):
        result = detect_red_flags("今天发烧38.5度", {"history": history})
        assert result["level"] == "none", history


def test_definition_multi_symptom_and_body_side():
    from core.triage import detect_red_flags, patient_complaint_text

    for text, label in (
        ("发热胸痛是什么病", "胸痛"),
        ("咳嗽胸痛是什么病", "胸痛"),
        ("抽搐吐白沫是什么病", "抽搐"),
        ("呼吸困难口唇发紫是什么病", "呼吸困难"),
        ("左侧胸痛是什么病", "胸痛"),
        ("胸闷胸痛是什么病", "胸痛"),
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert label in result["matches"], text
    # 单一术语解释问句仍被过滤
    for text in ("胸痛是什么病", "咯血是什么病", "抽搐是什么病"):
        assert detect_red_flags(
            patient_complaint_text(text), {}
        )["level"] == "none", text


def test_head_swelling_counts_as_head_trauma():
    from core.triage import detect_red_flags, patient_complaint_text

    for text in (
        "摔了一跤，头上起了个大包",
        "摔了一跤，头皮血肿",
        "摔了一跤，后脑勺鼓了个包",
        "摔了一跤，头上出血了",
    ):
        result = detect_red_flags(
            patient_complaint_text(text), {"medications": "华法林"}
        )
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    # 非头部部位不误报
    result = detect_red_flags(
        patient_complaint_text("摔了一跤，脚趾头肿了个包"),
        {"medications": "华法林"},
    )
    assert result["level"] == "none"


def test_caesarean_only_recent_counts_as_pregnancy():
    from core.triage import detect_red_flags, patient_complaint_text

    for history in ("剖宫产5年了", "三年前剖宫产", "剖腹产十年了"):
        result = detect_red_flags(
            patient_complaint_text("阴道出血"), {"history": history}
        )
        assert result["level"] == "none", history
    for text in (
        "剖宫产术后10天，阴道出血",
        "剖腹产术后一周，阴道流血",
    ):
        result = detect_red_flags(patient_complaint_text(text), {})
        assert "孕产期出血" in result["matches"], text


def test_restart_you_chi_le_quantifiers():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林停了一个月，今天又吃了点东西，能吃布洛芬吗",
        "华法林停了一个月，今天又吃了些水果，能吃布洛芬吗",
        "华法林停了一个月，今天又吃了两口饭，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text
    for text in (
        "华法林停了一个月，今天又吃了，能吃布洛芬吗",
        "华法林停了一个月，今天又吃了两片，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text


# ---------------------------------------------------------------- 导入
def _reminder_backup():
    return {"reminders": [
        {
            "id": 101, "profile": "妈妈", "title": "吃药", "note": "n1",
            "due_at": "2026-12-02T09:00", "advance_days": 1, "done": 0,
            "created_at": CREATED, "updated_at": CREATED,
        },
        {
            "id": 104, "profile": "爸爸", "title": "复查", "note": "n1",
            "due_at": "2026-12-02T09:00", "advance_days": 0, "done": 1,
            "created_at": CREATED, "updated_at": CREATED,
        },
    ]}


def _seed_reminder_twins(store):
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO reminders(title, note, due_at, session_id, profile,"
            " advance_days, repeat_days, repeat_until, medication_id,"
            " vaccination_id, appointment_id, done, created_at, updated_at)"
            " VALUES ('吃药','n1','2026-12-02T09:00','','妈妈',1,0,'',"
            "0,0,0,0,?,?)",
            (CREATED, CREATED),
        )
        store._conn.execute(
            "INSERT INTO reminders(title, note, due_at, session_id, profile,"
            " advance_days, repeat_days, repeat_until, medication_id,"
            " vaccination_id, appointment_id, done, created_at, updated_at)"
            " VALUES ('复查','n1','2026-12-02T09:00','','爸爸',0,0,'',"
            "0,0,0,1,?,?)",
            (CREATED, CREATED),
        )


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


def test_moved_twin_reminder_not_stolen_on_restore(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    _seed_reminder_twins(store)
    store.update_reminder(1, profile="爸爸")
    store.delete_reminder(2)

    first = store.import_data(_reminder_backup())
    assert first.get("reminders") == 1
    rows = _reminder_rows(store)
    assert [(p, t) for _i, p, t, *_ in rows] == [
        ("爸爸", "吃药"), ("爸爸", "复查"),
    ]
    assert rows[1][3] == "n1" and rows[1][4] == 1
    # 重复导入保持幂等
    second = store.import_data(_reminder_backup())
    assert second.get("reminders") == 0
    assert _reminder_rows(store) == rows


def test_repeat_import_keeps_twin_rows_idempotent(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    _seed_reminder_twins(store)
    store.delete_reminder(1)
    store.update_reminder(2, profile="爸爸", note="n2")

    first = store.import_data(_reminder_backup())
    assert first.get("reminders") == 1
    second = store.import_data(_reminder_backup())
    assert second.get("reminders") == 0
    rows = _reminder_rows(store)
    assert sorted((p, t, n) for _i, p, t, n, *_ in rows) == [
        ("妈妈", "吃药", "n1"), ("爸爸", "复查", "n2"),
    ]


def test_moved_symptom_restored_without_duplicate(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    with store._lock, store._conn:
        for severity in (3, 5):
            store._conn.execute(
                "INSERT INTO symptoms(profile, symptom, severity, note,"
                " recorded_at, created_at, updated_at)"
                " VALUES ('妈妈', '咳嗽', ?, 'n1', '2026-06-03T08:00', ?, ?)",
                (severity, CREATED, CREATED),
            )
    store.update_symptom_partial(1, profile="爸爸")
    backup = {"symptoms": [
        {"id": 101, "profile": "妈妈", "symptom": "咳嗽", "severity": 3,
         "note": "n1", "recorded_at": "2026-06-03T08:00",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "妈妈", "symptom": "咳嗽", "severity": 5,
         "note": "n1", "recorded_at": "2026-06-03T08:00",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    result = store.import_data(backup)
    assert result.get("symptoms") == 0
    with store._lock:
        rows = [
            (row["profile"], int(row["severity"]))
            for row in store._conn.execute(
                "SELECT profile, severity FROM symptoms ORDER BY id"
            ).fetchall()
        ]
    assert sorted(rows) == [("妈妈", 5), ("爸爸", 3)]


# ---------------------------------------------------------------- 就诊联动
def test_visit_profile_move_scoped_to_old_profile(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("李四", {"sex": "男"})
    store.save_profile("王五", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "妈妈提醒", "2026-12-19", profile="妈妈",
        appointment_id=appt["id"],
    )
    # 预约先改到李四：owner 清 0 且备注标记被剥离
    store.update_appointment_partial(appt["id"], profile="李四")
    moved = store.get_appointment(appt["id"])
    assert moved["profile"] == "李四"
    assert "由就诊记录 #" not in str(moved.get("note") or "")
    # 就诊再改到王五：不能把李四的预约再搬走
    store.update_visit_record_partial(visit["id"], profile="王五")
    assert store.get_appointment(appt["id"])["profile"] == "李四"
    # 删除王五的就诊不能级联删除李四的预约与提醒
    store.delete_visit_record(visit["id"])
    assert store.get_appointment(appt["id"]) is not None
    assert store.get_reminder(reminder["id"]) is not None


def test_marker_only_followup_reminder_follows_profile(store):
    store.save_profile("妈妈", {"sex": "女"})
    store.save_profile("爸爸", {"sex": "男"})
    visit = store.add_visit_record(
        "妈妈", visit_date="2026-11-01", diagnosis="高血压",
        next_followup_date="2026-12-20",
    )
    appt = store.add_appointment(
        "妈妈", "复诊：高血压", scheduled_at="2026-12-20T09:00",
        note=f"由就诊记录 #{visit['id']} 自动创建",
    )
    reminder = store.add_reminder(
        "妈妈提醒", "2026-12-19", profile="妈妈",
        appointment_id=appt["id"],
    )
    store.update_visit_record_partial(visit["id"], profile="爸爸")
    assert store.get_appointment(appt["id"])["profile"] == "爸爸"
    assert store.get_reminder(reminder["id"])["profile"] == "爸爸"


# ---------------------------------------------------------------- 安全/报表
def test_nat64_source_counted_in_ipv6_global_budget(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from core.ratelimit import SlidingWindowLimiter
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r61-secret-token-123")
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
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
    try:
        first = TestClient(
            api_server.app, base_url="http://localhost",
            client=("64:ff9b::1.2.3.4", 57000),
        )
        assert first.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        ).status_code == 401
        assert first.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        ).status_code == 401
        # 换一个 NAT64 映射地址不能绕过全局失败预算
        rotated = TestClient(
            api_server.app, base_url="http://localhost",
            client=("64:ff9b::1.2.3.5", 57001),
        )
        limited = rotated.get(
            "/api/sessions", headers={"X-Medix-Token": "bad"}
        )
        assert limited.status_code == 429
        assert limited.json().get("code") == "auth_rate_limited"
        # 正确令牌仍然直通
        assert rotated.get(
            "/api/sessions",
            headers={"X-Medix-Token": "r61-secret-token-123"},
        ).status_code == 200
    finally:
        store.close()
    source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "_is_ipv6_source_key" in source


def test_ipv6_source_key_helper():
    import api_server

    assert api_server._is_ipv6_source_key("ip:64:ff9b::1.2.3.4")
    assert api_server._is_ipv6_source_key("ip:2001:db8::/64")
    assert not api_server._is_ipv6_source_key("ip:1.2.3.4")
    assert not api_server._is_ipv6_source_key("ip:untrusted-proxy")
    assert not api_server._is_ipv6_source_key("")


def test_family_csv_adherence_for_active_medication(client_and_store):
    import csv
    import io

    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    med = client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍", "dose": "0.5g",
        "frequency": "每日两次", "started_at": _date(-13),
    }).json()["medication"]
    for day in range(1, 7):
        client.post(f"/api/medications/{med['id']}/logs", json={
            "taken_at": f"{_date(-day)}T08:00",
        })
    overview = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    adherence = mom["medications"].get("adherence")
    assert adherence and adherence["rate"] < 100

    resp = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(io.StringIO(resp.content.decode("utf-8-sig"))))
    header = rows[0]
    cell = rows[1][header.index("近7天依从率")]
    assert cell == f"{adherence['rate']}%"


def test_family_attention_skips_targeted_glucose_and_bp(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "glucose", "value1": 8.0,
    })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 7.5,
        "context": "fasting", "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    grandma = next(p for p in data["profiles"] if p["name"] == "奶奶")
    assert "血糖" not in grandma["attention"]["label"]

    client.put("/api/metric-targets", json={
        "profile": "爷爷", "metric": "bp", "value1": 150, "value2": 95,
    })
    client.post("/api/metrics", json={
        "profile": "爷爷", "metric": "bp", "value1": 150, "value2": 95,
        "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    grandpa = next(p for p in data["profiles"] if p["name"] == "爷爷")
    assert "血压" not in grandpa["attention"]["label"]

    # 未设目标成员仍按通用阈值提示
    client.post("/api/profiles", json={"name": "外婆", "context": {}})
    client.post("/api/metrics", json={
        "profile": "外婆", "metric": "glucose", "value1": 8.2,
        "context": "fasting", "measured_at": f"{_date(0)}T08:00",
    })
    data = client.get("/api/family/overview", params={"days": 7}).json()
    granny = next(p for p in data["profiles"] if p["name"] == "外婆")
    assert granny["attention"]["label"] == "血糖偏高"


def test_audit_profile_key_salted(monkeypatch, tmp_path):
    import api_server
    import memory
    from memory.session_store import SessionStore

    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(api_server.state, "audit_profile_salt", "", raising=False)
    try:
        key = api_server._profile_audit_key("妈妈")
        assert key.startswith("profile:")
        unsalted = "profile:" + hashlib.sha256(
            "妈妈".encode("utf-8")
        ).hexdigest()[:16]
        assert key != unsalted
        assert api_server._profile_audit_key("妈妈") == key
        assert store.get_app_state("audit_profile_salt")
        # 进程重启（清空缓存）后同安装摘要稳定
        api_server.state.audit_profile_salt = ""
        assert api_server._profile_audit_key("妈妈") == key
    finally:
        store.close()


# ---------------------------------------------------------------- 前端静态
def test_frontend_round61_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 请求体用发送时刻的档案快照 + await 后复核
    assert "patient_profile: sendProfile" in source
    assert "context: consultContext" in source
    assert "err.profileChanged" in source
    assert "档案已切换，本轮会诊未提交" in source
    # 换档草稿快照
    assert "noteComposerSwitchSnapshot()" in source
    assert "composerKeepsSwitchOwner()" in source
    assert "switchDraftSnapshot" in source
    # 启动恢复对账：已落库则清空输入框
    assert "inflightReconciled" in source
    assert "上次会诊已完成，答复已载入" in source
    # 清理收尾的 busy 复核
    assert "purged = Array.isArray(info.messages)" in source
    assert (
        "purged && sessionId === current && !busy && !sendInFlight"
        in source
    )
    # 疫苗 chip 累计口径
    assert "疫苗 累计" in source
    assert "疫苗 近 ${days} 天" not in source
    # 2000 字截断 + 422 detail 数组格式化
    assert "const limit = 2000" in source
    assert "detail.filter" in source or "Array.isArray(detail)" in source
