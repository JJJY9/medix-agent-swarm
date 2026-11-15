"""第六十五轮审查：未吃完/漏服用药与抗凝语境、枚举否定转折/程度词、
科普引用同句口语主诉、孕期贫血与剖宫产多年、导入孪生恢复（提醒/预约/
用药）、就诊共享链接与改档案迁移、伪造转发地址失败桶隔离、未声明代理
对端共享桶、目标成员通用血糖提示、附件完整性全量孤儿、周报上期会诊
归属、前端换档竞态/首轮对账/成功后清草稿/化验卡片。"""
import json
import os
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
        "MEDIX_ALLOW_INSECURE_LAN", "MEDIX_RATE_LIMIT",
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
def test_unfinished_dose_keeps_anticoagulation():
    for text in (
        "华法林还没吃完，今天头撞到门框",
        "华法林没吃完，今天头撞到门框",
        "华法林没有吃完，今天头撞到门框",
    ):
        result = _red_flags(text)
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    fever = _red_flags("甲氨蝶呤没有吃完，今天发烧38.9度")
    assert "免疫功能低下伴发热" in fever["matches"]


def test_enum_negation_transition_and_degree_cues():
    for text in (
        "没有呕吐、只是胸痛",
        "无发热、胸痛剧烈",
        "没有发烧、只是胸痛和呼吸困难",
        "没发烧、就是胸痛得厉害",
    ):
        result = _red_flags(text)
        assert result["level"] != "none", text
        assert any(
            term in result["matches"] for term in ("胸痛", "呼吸困难")
        ), (text, result["matches"])


def test_education_reference_with_self_qualifier():
    for text in (
        "我在网上看到胸痛要警惕我有点胸痛",
        "网上说胸痛要重视我有一点胸痛",
        "看了视频说胸痛要当心我偶尔胸痛",
    ):
        result = _red_flags(text)
        assert "胸痛" in result["matches"], text
    reference_only = _red_flags("我在网上看到胸痛要重视")
    assert reference_only["level"] == "none"


def test_dose_missed_after_drug_name():
    for text in (
        "华法林今天没有吃，今天头撞到门框",
        "华法林昨天没有吃，今天头撞到门框",
    ):
        result = _red_flags(text)
        assert "抗凝治疗期间头部外伤" in result["matches"], text
    fever = _red_flags("甲氨蝶呤今天没有吃，今天发烧38.9度")
    assert "免疫功能低下伴发热" in fever["matches"]
    from core.medication_safety import check_medication_safety

    assert check_medication_safety(
        "华法林昨天没有吃，能吃布洛芬吗", {}
    )["level"] == "warning"


def test_pregnancy_anemia_is_not_bleeding():
    for text in (
        "孕20周，有点贫血",
        "怀孕8周，有贫血",
        "我怀孕8周，血红蛋白低，有点贫血",
    ):
        result = _red_flags(text)
        assert result["level"] == "none", text
    bleeding = _red_flags("怀孕8周，有少量血")
    assert "孕产期出血" in bleeding["matches"]


def test_caesarean_many_years_is_history():
    for text in (
        "剖宫产术后20多年，阴道出血",
        "剖宫产术后十多年，阴道出血",
        "剖宫产术后十几年，阴道出血",
        "剖宫产术后好多年，阴道出血",
    ):
        result = _red_flags(text)
        assert "孕产期出血" not in result["matches"], text
    recent = _red_flags("剖宫产手术后，阴道出血")
    assert "孕产期出血" in recent["matches"]
    days = _red_flags("剖宫产术后10天，阴道出血")
    assert "孕产期出血" in days["matches"]


def test_medication_unfinished_and_missed_dose():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林没有吃完，能吃布洛芬吗",
        "阿司匹林没有吃完，还能吃布洛芬吗",
    ):
        result = check_medication_safety(text, {})
        assert result["level"] == "warning", text
    for text in (
        "华法林我没有在吃，能吃布洛芬吗",
        "我没吃过阿司匹林，能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "none", text


# ---------------------------------------------------------------- 导入
def _reminder(rid, note, profile="妈妈"):
    return {
        "id": rid, "profile": profile, "title": "复查",
        "due_at": "2026-12-01T09:00", "note": note,
        "advance_days": 0, "repeat_days": 0, "repeat_until": "",
        "done": 0, "created_at": CREATED, "updated_at": CREATED,
    }


def _appointment(rid, note, profile="妈妈"):
    return {
        "id": rid, "profile": profile, "title": "复诊",
        "department": "", "doctor": "", "location": "",
        "scheduled_at": "2026-12-02T09:00", "status": "upcoming",
        "note": note, "owner_visit_id": 0,
        "created_at": CREATED, "updated_at": CREATED,
    }


def test_twin_reminder_rename_and_note_edit_restores_deleted(store):
    store.save_profile("妈妈", {})
    backup = {"reminders": [_reminder(101, "空腹"), _reminder(104, "带报告")]}
    assert store.import_data(backup).get("reminders") == 2
    store.update_reminder(1, title="复查（已约）", note="空腹，提前一天")
    store.delete_reminder(2)
    assert store.import_data(backup).get("reminders") == 2
    notes = [
        row["note"] for row in store._conn.execute(
            "SELECT note FROM reminders ORDER BY id"
        ).fetchall()
    ]
    # 被删的「带报告」孪生必须补回；改名+改备注的存活行保持编辑后状态
    assert "带报告" in notes
    assert store.import_data(backup).get("reminders") == 0


def test_cross_profile_twin_move_delete_restores(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"appointments": [
        _appointment(1, "n1", "妈妈"), _appointment(2, "n2", "爸爸"),
    ]}
    assert store.import_data(backup).get("appointments") == 2
    store.update_appointment_partial(1, profile="爸爸", note="n1改")
    store.delete_appointment(2)
    assert store.import_data(backup).get("appointments") == 2
    rows = [
        dict(row) for row in store._conn.execute(
            "SELECT profile, note FROM appointments ORDER BY id"
        ).fetchall()
    ]
    # 被删的「爸爸/n2」必须恢复，改挂+改备注的存活行保留
    assert any(
        row["profile"] == "爸爸" and row["note"] == "n2" for row in rows
    )
    assert store.import_data(backup).get("appointments") == 0


def test_twin_soft_and_tail_edit_reimport_is_idempotent(store):
    store.save_profile("妈妈", {})
    backup = {"appointments": [
        _appointment(101, "n1"), _appointment(104, "n2"),
    ]}
    assert store.import_data(backup).get("appointments") == 2
    store.update_appointment_partial(1, title="复诊改", note="n1改")
    # 本地没有任何缺行：重复导入不能补插编辑前的旧副本
    assert store.import_data(backup).get("appointments") == 0
    rows = store._conn.execute(
        "SELECT id, title, note FROM appointments ORDER BY id"
    ).fetchall()
    assert len(rows) == 2
    assert rows[0]["title"] == "复诊改"
    assert store.import_data(backup).get("appointments") == 0


def test_twin_medication_and_allergy_reimport_idempotent(store):
    store.save_profile("妈妈", {})
    med_backup = {"medications": [
        {"id": 101, "profile": "妈妈", "name": "药A", "dose": "1片",
         "frequency": "每日一次", "note": "n1", "active": True,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "妈妈", "name": "药A", "dose": "1片",
         "frequency": "每日一次", "note": "n2", "active": True,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    assert store.import_data(med_backup).get("medications") == 2
    store.update_medication(1, name="药A改", note="n1改")
    assert store.import_data(med_backup).get("medications") == 0
    allergy_backup = {"allergies": [
        {"id": 101, "profile": "妈妈", "substance": "青霉素",
         "reaction": "皮疹", "severity": "moderate", "kind": "drug",
         "active": True, "noted_at": "2026-05-01", "note": "n1",
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 104, "profile": "妈妈", "substance": "青霉素",
         "reaction": "皮疹", "severity": "moderate", "kind": "drug",
         "active": True, "noted_at": "2026-05-01", "note": "n2",
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    assert store.import_data(allergy_backup).get("allergies") == 2
    first_allergy = store._conn.execute(
        "SELECT id FROM allergies ORDER BY id LIMIT 1"
    ).fetchone()["id"]
    store._conn.execute(
        "UPDATE allergies SET substance = '青霉素改', note = 'n1改',"
        " updated_at = '2026-11-15T10:00:00' WHERE id = ?",
        (first_allergy,),
    )
    store._conn.commit()
    assert store.import_data(allergy_backup).get("allergies") == 0


# ---------------------------------------------------------------- 就诊
def test_visit_profile_change_keeps_other_visit_appointment(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01"
    )["id"]
    vb = store.add_visit_record(
        "妈妈", diagnosis="乙", visit_date="2026-11-02"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-10T09:00",
        note=f"由就诊记录 #{va} 自动创建", owner_visit_id=vb,
    )["id"]
    reminder = store.add_reminder(
        "复诊：乙", "2026-12-10", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.set_visit_followup_links(va, appointment, reminder)
    store.update_visit_record(
        va, "爸爸", diagnosis="甲", visit_date="2026-11-01"
    )
    row = store._conn.execute(
        "SELECT profile FROM appointments WHERE id = ?", (appointment,)
    ).fetchone()
    reminder_row = store._conn.execute(
        "SELECT profile FROM reminders WHERE id = ?", (reminder,)
    ).fetchone()
    # owner 属于另一条就诊的预约与提醒不能随本就诊改档案迁移
    assert row["profile"] == "妈妈"
    assert reminder_row["profile"] == "妈妈"


def test_visit_profile_change_migrates_marker_only_appointment(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：甲", scheduled_at="2026-12-11T09:00",
        note=f"由就诊记录 #{va} 自动创建", owner_visit_id=0,
    )["id"]
    reminder = store.add_reminder(
        "复诊：甲", "2026-12-11", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.update_visit_record(
        va, "爸爸", diagnosis="甲", visit_date="2026-11-01"
    )
    row = store._conn.execute(
        "SELECT profile FROM appointments WHERE id = ?", (appointment,)
    ).fetchone()
    reminder_row = store._conn.execute(
        "SELECT profile FROM reminders WHERE id = ?", (reminder,)
    ).fetchone()
    # 导入恢复留下的「只有备注标记、owner=0」自动预约仍须跟随
    assert row["profile"] == "爸爸"
    assert reminder_row["profile"] == "爸爸"


def test_shared_legacy_visit_link_keeps_owner_appointment(store):
    store.save_profile("妈妈", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01"
    )["id"]
    vb = store.add_visit_record(
        "妈妈", diagnosis="乙", visit_date="2026-11-02"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：乙", scheduled_at="2026-12-12T09:00",
        note=f"由就诊记录 #{va} 自动创建", owner_visit_id=vb,
    )["id"]
    reminder = store.add_reminder(
        "复诊：乙", "2026-12-12", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.set_visit_followup_links(vb, appointment, reminder)
    store._conn.execute(
        "UPDATE visit_records SET followup_appointment_id = ? WHERE id = ?",
        (appointment, va),
    )
    store._conn.commit()
    store.update_visit_record(
        va, "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-25",
    )
    appointment_row = store._conn.execute(
        "SELECT id FROM appointments WHERE id = ?", (appointment,)
    ).fetchone()
    reminder_row = store._conn.execute(
        "SELECT id FROM reminders WHERE id = ?", (reminder,)
    ).fetchone()
    va_row = store._conn.execute(
        "SELECT followup_appointment_id FROM visit_records WHERE id = ?",
        (va,),
    ).fetchone()
    vb_row = store._conn.execute(
        "SELECT followup_appointment_id FROM visit_records WHERE id = ?",
        (vb,),
    ).fetchone()
    # 遗留共享链接：只能解除非 owner 就诊自己的链接
    assert appointment_row is not None
    assert reminder_row is not None
    assert int(va_row["followup_appointment_id"]) == 0
    assert int(vb_row["followup_appointment_id"]) == appointment


def test_drop_followup_clears_dangling_reminder_links(store):
    store.save_profile("妈妈", {})
    va = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01"
    )["id"]
    vb = store.add_visit_record(
        "妈妈", diagnosis="乙", visit_date="2026-11-02"
    )["id"]
    appointment = store.add_appointment(
        "妈妈", "复诊：甲", scheduled_at="2026-12-13T09:00",
        note=f"由就诊记录 #{va} 自动创建",
    )["id"]
    reminder = store.add_reminder(
        "复诊：甲", "2026-12-13", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.set_visit_followup_links(va, appointment, reminder)
    store._conn.execute(
        "UPDATE visit_records SET followup_reminder_id = ? WHERE id = ?",
        (reminder, vb),
    )
    store._conn.commit()
    store.update_visit_record(
        va, "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-26",
    )
    assert store._conn.execute(
        "SELECT id FROM appointments WHERE id = ?", (appointment,)
    ).fetchone() is None
    assert store._conn.execute(
        "SELECT id FROM reminders WHERE id = ?", (reminder,)
    ).fetchone() is None
    vb_row = store._conn.execute(
        "SELECT followup_reminder_id FROM visit_records WHERE id = ?",
        (vb,),
    ).fetchone()
    # 提醒已删：其它就诊指向它的复诊链接不能悬空
    assert int(vb_row["followup_reminder_id"]) == 0


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


def _bucket_keys():
    import api_server

    keys = set()
    for limiter in (
        api_server.auth_failure_limiter,
        api_server.auth_failure_limiter_loopback,
    ):
        with limiter._lock:
            keys.update(
                key for key, hits in limiter._hits.items() if hits
            )
    return keys


def test_spoofed_forwarded_failure_bucket_isolated(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r65-secret-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    session_store = SessionStore(str(tmp_path / "auth.db"))
    monkeypatch.setattr(
        memory, "get_session_store", lambda: session_store
    )
    _clear_auth_limiters()
    try:
        client = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 55123),
        )
        baseline = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "r65-secret-token-0123456789",
                "X-Forwarded-For": "192.168.1.50",
            },
        )
        assert baseline.status_code == 200
        for _ in range(10):
            response = client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": "192.168.1.50, 127.0.0.1",
                },
            )
            assert response.status_code == 401
        keys = _bucket_keys()
        # 伪造的左项地址不能进入真实地址桶
        assert not any("192.168.1.50" in key for key in keys), keys
        assert "spoof:ip:127.0.0.1" in keys
        victim = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "r65-secret-token-0123456789",
                "X-Forwarded-For": "192.168.1.50",
            },
        )
        assert victim.status_code == 200
    finally:
        _clear_auth_limiters()
        session_store.close()


def test_undeclared_proxy_peer_uses_shared_bucket(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    import api_server
    import memory
    from memory.session_store import SessionStore

    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "r65-secret-token-0123456789")
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    session_store = SessionStore(str(tmp_path / "peer.db"))
    monkeypatch.setattr(
        memory, "get_session_store", lambda: session_store
    )
    _clear_auth_limiters()
    try:
        client = TestClient(
            api_server.app, base_url="http://localhost",
            client=("172.18.0.2", 50001),
        )
        baseline = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "r65-secret-token-0123456789",
                "X-Forwarded-For": "10.1.2.3",
            },
        )
        assert baseline.status_code == 200
        for _ in range(10):
            response = client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": "10.1.2.3",
                },
            )
            assert response.status_code == 401
        keys = _bucket_keys()
        # 未声明的代理对端不能把整个对端地址硬预锁定
        assert "ip:172.18.0.2" not in keys, keys
        assert "ip:untrusted-proxy" in keys
        victim = client.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": "r65-secret-token-0123456789",
                "X-Forwarded-For": "10.9.9.9",
            },
        )
        assert victim.status_code == 200
    finally:
        _clear_auth_limiters()
        session_store.close()


# ---------------------------------------------------------------- 报表
def test_glucose_target_member_keeps_generic_hint_quiet(
    client_and_store
):
    client, _store, _attachments = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.put("/api/metric-targets", json={
        "profile": "奶奶", "metric": "glucose", "value1": 8.0,
    })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 7.5,
        "context": "fasting", "measured_at": f"{_date(0)}T08:00",
    })
    family = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]
    assert not [
        item for item in family if item["key"] == "metric_glucose_fasting"
    ]
    single = client.get(
        "/api/health/insights",
        params={"days": 30, "profile": "奶奶"},
    ).json()["insights"]
    assert not [
        item for item in single if item["key"] == "metric_glucose_fasting"
    ]
    overview = client.get(
        "/api/family/overview", params={"days": 30}
    ).json()
    grandma = next(
        item for item in overview["profiles"] if item["name"] == "奶奶"
    )
    assert grandma["attention"]["level"] == "ok"
    # 有超标读数时保留目标告警与通用提示并存
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "glucose", "value1": 9.2,
        "context": "fasting", "measured_at": f"{_date(0)}T09:00",
    })
    family = client.get(
        "/api/health/insights", params={"days": 30}
    ).json()["insights"]
    assert [
        item for item in family if item["key"] == "metric_glucose_fasting"
    ]
    single = client.get(
        "/api/health/insights",
        params={"days": 30, "profile": "奶奶"},
    ).json()["insights"]
    assert [
        item for item in single if item["key"] == "metric_glucose"
    ]
    assert [
        item for item in single if item["key"] == "metric_glucose_fasting"
    ]


def test_attachment_orphans_computed_from_full_registry(client_and_store):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)

    def _write(name, old=False):
        path = directory / name
        path.write_bytes(b"%PDF-1.4 test")
        if old:
            stamp = time.time() - 3600
            os.utime(path, (stamp, stamp))

    store.add_attachment(
        "妈妈", "visit", 0, "妈妈报告.pdf", "mom-1.pdf",
        "application/pdf", 13,
    )
    store.add_attachment(
        "爸爸", "visit", 0, "爸爸报告.pdf", "dad-1.pdf",
        "application/pdf", 13,
    )
    _write("mom-1.pdf")
    _write("dad-1.pdf")
    data = client.get(
        "/api/attachments/integrity", params={"profile": "妈妈"}
    ).json()
    # 其他成员已登记的文件不是孤儿，也不能泄露文件名
    assert data["orphan_count"] == 0, data
    assert data["db_count"] == 1
    assert data["db_total_count"] == 2
    assert data["missing_count"] == 0
    _write("orphan.pdf", old=True)
    data = client.get(
        "/api/attachments/integrity", params={"profile": "妈妈"}
    ).json()
    assert data["orphan_count"] == 1
    assert [item["name"] for item in data["orphans"]] == ["orphan.pdf"]
    response = client.post(
        "/api/attachments/cleanup",
        json={"confirm": True, "expected_orphans": data["orphan_count"]},
    )
    assert response.status_code == 200, response.text
    assert response.json().get("removed") == 1


def test_weekly_previous_consultations_use_created_at(client_and_store):
    client, store, _attachments = client_and_store
    now = datetime.now()
    old_day = (now - timedelta(days=10)).strftime("%Y-%m-%dT09:00:00")
    today = now.strftime("%Y-%m-%dT09:00:00")
    with store._lock, store._conn:
        store._conn.execute(
            "INSERT INTO sessions(id, title, created_at, updated_at,"
            " profile) VALUES ('s-old', '', ?, ?, '妈妈')",
            (old_day, today),
        )
        for role, content in (
            ("user", "十天前的问题"), ("assistant", "十天前的回答"),
            ("user", "今天继续追问"), ("assistant", "今天的回答"),
        ):
            store._conn.execute(
                "INSERT INTO messages(session_id, role, content,"
                " metadata, created_at) VALUES ('s-old', ?, ?, '{}', ?)",
                (role, content, today),
            )
    data = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
    ).json()
    consultations = data["consultations"]
    # 续聊旧会话不能把上一周期的会诊事后抹掉
    assert consultations["prev_count"] == 1, consultations
    assert consultations["count"] == 1


# ---------------------------------------------------------------- 前端静态
def test_frontend_round65_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 换档竞态：旧调用 await 后不得回滚覆盖更新的选择
    assert source.count("const myProfileSeq = ++profileSelectionSeq;") >= 2
    assert source.count(
        "if (profileSelectionSeq !== myProfileSeq) {"
    ) >= 2
    # 本页新建会话标记允许 baseline=0 的首轮对账
    assert source.count("markFreshSession(sessionId);") >= 2
    assert "isFreshPageSession(expectedSession)" in source
    assert "(inflightBaseline > 0 || freshSession)" in source
    # 继续/重试成功后清除仍等于本次主诉的输入与草稿
    assert "leftover === String(question || '').trim()" in source
    # 只有正常化验记录的成员卡片与复制文本
    assert "`化验 近 ${days} 天 ${labs.count} 条`" in source
    assert "parts.push(`化验 ${labs.count} 条`);" in source
    assert "parts.push(`疫苗 累计 ${Number(vaccination.count) || 0} 剂`);" in source
    assert "需关注 ${attention.label}" in source
