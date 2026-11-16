"""第六十七轮审查：定义引导前缀急症保留、胸部疼痛同义写法、停药后续用
变体、远期停药须指向本药、否认变化不等于否认症状、头部外伤被动/包块、
科普引述时长频次、导入撞号内容核对与锚点预算、就诊改档案 owner 校验、
单跳代理的本机判定与分桶、低频药最差依从率、CSV 口径、深链恢复档案归属、
本地反代声明、指标空测量时间、复诊提醒墓碑/仅改时间、停药打卡 CSV、换档
成功后首页刷新。"""
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
        "MEDIX_PROXY_SINGLE_HOP", "MEDIX_ALLOW_INSECURE_LAN",
        "MEDIX_RATE_LIMIT",
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


def _red_flags(text, context=None):
    from core.triage import detect_red_flags, patient_complaint_text

    return detect_red_flags(patient_complaint_text(text), context or {})


# ---------------------------------------------------------------- 分诊
def test_definition_prefix_keeps_acute_complaints():
    for text in (
        "请解释一下突发胸痛是什么病",
        "解释一下抽搐怎么办",
        "介绍一下抽搐怎么办",
        "解释一下抽搐吐白沫是什么病",
        "解释一下突然胸痛是什么原因",
    ):
        result = _red_flags(text)
        assert result["level"] != "none", text
    # 纯术语问句仍是科普
    assert _red_flags("介绍一下呼吸困难")["level"] == "none"
    assert _red_flags("什么是持续性胸痛")["level"] == "none"


def test_chest_pain_synonyms_detected():
    for text in (
        "胸部疼痛，冒冷汗，持续20分钟",
        "胸骨后疼痛，持续不缓解",
        "胸部疼痛两天了是什么病",
    ):
        result = _red_flags(text)
        assert "胸痛" in result["matches"], text


def test_restart_after_stop_more_variants():
    from core.medication_safety import check_medication_safety

    for text in (
        "华法林停了，现在接着吃，能吃布洛芬吗",
        "阿司匹林停了，恢复用药，还能吃布洛芬吗",
    ):
        assert check_medication_safety(text, {})["level"] == "warning", text
    triage = _red_flags(
        "华法林停了，现在接着吃，今天头撞到门框",
        {"medications": "华法林"},
    )
    assert "抗凝治疗期间头部外伤" in triage["matches"]


def test_long_stop_must_name_current_drug():
    context = {"medications": "华法林"}
    # 另一种药的远期停药不能抹掉本药语境
    result = _red_flags(
        "华法林没吃完，阿司匹林停药一年了，今天头撞到门框", context
    )
    assert "抗凝治疗期间头部外伤" in result["matches"]
    fever = _red_flags(
        "甲氨蝶呤没吃完，二甲双胍停药一年了，今天发烧38.9度", {}
    )
    assert "免疫功能低下伴发热" in fever["matches"]


def test_negated_change_keeps_symptom():
    for text in ("胸痛没加重", "胸痛未加重", "胸痛不恶化"):
        assert "胸痛" in _red_flags(text)["matches"], text
    assert _red_flags("无发热、胸痛不明显")["level"] == "none"
    assert "胸痛" in _red_flags("无发热、胸痛剧烈")["matches"]


def test_head_trauma_passive_and_swelling():
    context = {"medications": "华法林"}
    for text in (
        "我吃华法林，头部受到撞击",
        "我吃华法林，头皮磕破了",
        "我吃华法林，头被门夹了",
    ):
        result = _red_flags(text, context)
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_education_quote_course_markers_not_personal():
    for text in (
        "网上说胸痛持续十分钟了要马上就医",
        "文章讲抽搐好几回了要就医",
    ):
        assert _red_flags(text)["level"] == "none", text
    assert "胸痛" in _red_flags("胸痛持续十分钟了是什么病")["matches"]


# ---------------------------------------------------------------- 就诊
def test_visit_profile_change_keeps_other_owner_linked(store):
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
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
        va, "爸爸", diagnosis="甲", visit_date="2026-11-01"
    )
    # 遗留共享链接的预约/提醒属于 vb，不能随 va 改档案迁移
    appointment_row = store._conn.execute(
        "SELECT profile FROM appointments WHERE id = ?", (appointment,)
    ).fetchone()
    reminder_row = store._conn.execute(
        "SELECT profile FROM reminders WHERE id = ?", (reminder,)
    ).fetchone()
    assert appointment_row["profile"] == "妈妈"
    assert reminder_row["profile"] == "妈妈"


def test_schedule_followup_respects_deleted_reminder_tombstone(store):
    from api_server import _schedule_followup_sync

    store.save_profile("妈妈", {})
    visit = store.add_visit_record(
        "妈妈", diagnosis="甲", visit_date="2026-11-01",
        next_followup_date="2026-12-20",
    )["id"]
    first = _schedule_followup_sync(store, visit)
    reminder_id = first["reminder"]["id"]
    assert store.delete_reminder(reminder_id)
    # 再次安排复诊不能绕过删除墓碑复活提醒
    again = _schedule_followup_sync(store, visit)
    assert again.get("reminder") is None
    assert not [
        item for item in store.list_reminders(False, "妈妈")
        if int(item.get("appointment_id") or 0)
        == int(again["appointment"]["id"])
    ]


def test_appointment_time_only_change_keeps_reminder(store):
    store.save_profile("妈妈", {})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-12-20T09:00",
    )["id"]
    reminder = store.add_reminder(
        "复诊", "2026-12-19", profile="妈妈",
        appointment_id=appointment,
    )["id"]
    store.complete_reminder(reminder)
    store.update_appointment_partial(
        appointment, scheduled_at="2026-12-20T15:30"
    )
    # 仅时间变化：不按新时间重建提醒、不复活已完成提醒
    pending = store.list_reminders(False, "妈妈")
    assert not [
        item for item in pending
        if int(item.get("appointment_id") or 0) == appointment
    ]
    row = store._conn.execute(
        "SELECT due_at, done FROM reminders WHERE id = ?", (reminder,)
    ).fetchone()
    assert row is not None and int(row["done"]) == 1


# ---------------------------------------------------------------- 导入
def test_import_metric_requires_measured_at(store):
    store.save_profile("妈妈", {})
    result = store.import_data({"health_metrics": [
        {"profile": "妈妈", "metric": "weight", "value1": 60,
         "created_at": CREATED, "measured_at": ""},
        {"profile": "妈妈", "metric": "weight", "value1": 61,
         "created_at": CREATED, "measured_at": "2026-11-01T08:00"},
    ]})
    assert result.get("invalid", 0) >= 1
    assert result.get("metrics") == 1


# ---------------------------------------------------------------- 认证
def test_proxy_header_loopback_requires_single_hop(monkeypatch):
    import api_server

    class FakeClient:
        host = "127.0.0.1"

    class FakeRequest:
        def __init__(self, headers):
            self.headers = headers
            self.client = FakeClient()

    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.delenv("MEDIX_PROXY_SINGLE_HOP", raising=False)
    forwarded_loopback = FakeRequest({"x-forwarded-for": "127.0.0.1"})
    # 透传代理可把客户端自带的 127.0.0.1 原样转发：未声明单跳不算本机
    assert api_server._is_loopback_client(forwarded_loopback) is False
    request = FakeRequest({"x-forwarded-for": "203.0.113.9"})
    assert api_server._client_ip_key(request) == "ip:127.0.0.1"
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    assert api_server._is_loopback_client(forwarded_loopback) is True
    assert api_server._client_ip_key(request) == "ip:203.0.113.9"


# ---------------------------------------------------------------- 报表
def test_low_frequency_medication_counts_in_worst_rate(client_and_store):
    client, store, = client_and_store
    client.post("/api/profiles", json={"name": "档W", "context": {"sex": "女"}})
    daily = store.add_medication(
        "档W", "二甲双胍", "0.5g", frequency="每日一次",
        started_at=_date(-7),
    )
    for offset in range(7):
        store.add_medication_log(
            daily["id"], taken_at=f"{_date(-offset)}T08:00"
        )
    store.add_medication(
        "档W", "阿仑膦酸钠", "70mg", frequency="每周一次",
        started_at=_date(-7),
    )
    data = client.get(
        "/api/family/overview", params={"days": 7}
    ).json()
    member = next(
        item for item in data["profiles"] if item["name"] == "档W"
    )
    # 每周一次、完全没打卡的药同样计入单药最差依从率
    assert member["medications"]["worst_rate"] == 0
    export = client.get(
        "/api/family/overview/export.csv", params={"days": 7}
    )
    rows = list(csv.DictReader(io.StringIO(
        export.content.decode("utf-8-sig")
    )))
    row = next(item for item in rows if item["成员"] == "档W")
    assert row["近7天依从率"] == "0%"


def test_family_csv_shows_stopped_med_today_dose(client_and_store):
    client, store = client_and_store
    medication = store.add_medication("", "阿司匹林", "100mg")
    store.add_medication_log(
        medication["id"], taken_at=f"{_date(0)}T08:00"
    )
    store.update_medication(medication["id"], active=False)
    export = client.get(
        "/api/family/overview/export.csv", params={"days": 7}
    )
    rows = list(csv.DictReader(io.StringIO(
        export.content.decode("utf-8-sig")
    )))
    row = next(item for item in rows if item["成员"] in ("", "未指定"))
    assert "今日 1 次" in row["用药"]


# ---------------------------------------------------------------- 前端静态
def test_frontend_round67_guards():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    # 合并草稿后仍保留档案归属（不再要求精确相等）
    assert "els.input.value.includes(String(text || '').trim())" in source
    # 深链恢复失败放回主诉时同样保留提交档案
    assert source.count("preserveDraftProfile(sendProfile, question);") >= 4
    # 空白会话换档成功不再被「接管」误判（成功会换 sessionId）
    assert "busy || (!ok && sessionId !== attemptSession)" in source
