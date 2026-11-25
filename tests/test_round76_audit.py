# -*- coding: utf-8 -*-
"""第七十六轮审查修复的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
from core.triage import detect_red_flags, patient_complaint_text
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _red(text):
    return detect_red_flags(patient_complaint_text(text), {})


# ---------------------------------------------------------------- [1][7]
@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我最近也在吃，今天头撞到门框",
        "我爸吃华法林，我现在也在吃，今天头撞到门框",
        "我爸吃华法林，我也在吃了，今天头撞到门框",
        "我爸吃华法林，我也在吃药，今天头撞到门框",
        "我爸吃华法林，我也开始吃了，今天头撞到门框",
    ],
)
def test_continuation_with_adverb_and_generic_object(text):
    assert _red(text)["level"] == "emergency"
    assert check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗"), {"age": "35岁"}
    )["level"] == "warning"


@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我也没在吃，今天头撞到门框",
        "我爸吃华法林，我最近不吃，今天头撞到门框",
        "我爸吃华法林，我也没服用，今天头撞到门框",
    ],
)
def test_negated_continuation_is_not_patient_use(text):
    assert _red(text)["level"] == "none"
    assert check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗"), {"age": "35岁"}
    )["level"] == "none"


# ---------------------------------------------------------------- [2]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛难以忍受是什么病",
        "胸痛受不了是什么病",
        "抽搐翻白眼是什么病",
    ],
)
def test_definition_with_severe_description(text):
    assert _red(text)["level"] == "emergency"


# ---------------------------------------------------------------- [3]
@pytest.mark.parametrize(
    "text",
    [
        "吃华法林，摔了一跤头先着地",
        "吃华法林，摔倒时头先着地",
        "吃华法林，摔下去头先撞到桌角",
        "吃华法林，摔下去头先磕到桌角",
    ],
)
def test_head_first_trauma(text):
    result = _red(text)
    assert result["level"] == "emergency"
    assert "抗凝治疗期间头部外伤" in result["matches"]


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text,term",
    [
        ("服用华法林期间没吃青菜，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("服用华法林期间没有吃青菜，今天头撞到门框", "抗凝治疗期间头部外伤"),
        ("吃华法林不再吃菠菜，今天摔到头", "抗凝治疗期间头部外伤"),
        ("服用甲氨蝶呤期间不再吃辛辣，今天发烧了", "免疫功能低下伴发热"),
    ],
)
def test_diet_negation_variants(text, term):
    result = _red(text)
    assert result["level"] == "emergency"
    assert term in result["matches"]


# ---------------------------------------------------------------- [9]
def test_parallel_independent_negation():
    assert _red("不是胸痛也没有呼吸困难")["level"] == "none"
    assert _red("不是胸痛也没有咯血")["level"] == "none"
    assert _red("不是没有胸痛")["level"] == "emergency"


# ---------------------------------------------------------------- [4]
def test_reminder_import_keeps_distinct_pending_rows(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    appointment = store.add_appointment(
        "妈妈", "心内科复诊", scheduled_at="2026-12-01T09:00"
    )
    payload = {"reminders": [
        {"title": "就诊：心内科复诊", "due_at": "2026-12-01T09:00",
         "note": "带化验单", "profile": "妈妈",
         "appointment_id": appointment["id"], "repeat_days": 0},
        {"title": "就诊：心内科复诊", "due_at": "2026-12-01T09:00",
         "note": "带医保卡", "profile": "妈妈",
         "appointment_id": appointment["id"], "repeat_days": 2},
    ]}
    result = store.import_data(payload)
    rows = store.list_reminders(False, "妈妈")
    store.close()
    assert result.get("reminders") == 2, result
    assert len(rows) == 2, rows


# ---------------------------------------------------------------- [5]
def test_cross_member_edited_twin_restores_deleted(tmp_path):
    source = _store(tmp_path)
    source.save_profile("爸爸", {"age": "62"})
    first = source.add_reminder(
        "取药", "2026-12-01T09:00", note="m", profile="爸爸",
        advance_days=1,
    )
    source.add_reminder(
        "取药", "2026-12-01T09:00", note="m", profile="爸爸",
        advance_days=1,
    )
    source.add_reminder(
        "取药", "2026-12-01T09:00", note="m", profile="妈妈",
        advance_days=1,
    )
    source.add_reminder(
        "复诊", "2026-12-01T09:00", note="d", profile="爸爸",
        advance_days=0,
    )
    with source._lock, source._conn:
        source._conn.execute(
            "UPDATE reminders SET created_at = '2026-01-01T10:00:00'"
        )
    source.delete_reminder(first["id"])
    backup = source.export_data()
    source.close()

    target = _store(tmp_path)
    target.save_profile("爸爸", {"age": "62"})
    target.save_profile("妈妈", {"age": "60"})
    target.import_data(backup)
    rows = target.list_reminders(False)
    dad_drug = next(
        r for r in rows
        if r["profile"] == "爸爸" and r["title"] == "取药"
    )
    mom_drug = next(
        r for r in rows
        if r["profile"] == "妈妈" and r["title"] == "取药"
    )
    with target._lock, target._conn:
        target._conn.execute(
            "UPDATE reminders SET note = 'edited',"
            " updated_at = '2026-12-31T10:00:00' WHERE id = ?",
            (dad_drug["id"],),
        )
    target.delete_reminder(mom_drug["id"])
    target.import_data(backup)
    final = [
        (r["profile"], r["title"]) for r in target.list_reminders(False)
    ]
    store_ok = any(
        profile == "妈妈" and title == "取药" for profile, title in final
    )
    store_ok = store_ok and any(
        profile == "爸爸" and title == "取药" for profile, title in final
    )
    target.close()
    assert store_ok, final


# ---------------------------------------------------------------- [10]
def test_csv_import_restores_completed_reminder(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.add_reminder("复查血压", "2026-12-01T09:00", profile="妈妈")
    payload = {"reminders": [
        {"title": "复查血压", "due_at": "2026-12-01T09:00",
         "note": "", "profile": "妈妈", "done": 0},
        {"title": "复查血压", "due_at": "2026-12-01T09:00",
         "note": "", "profile": "妈妈", "done": 1},
    ]}
    result = store.import_data(payload)
    rows = store.list_reminders(True, "妈妈")
    done_rows = [r for r in rows if r["done"]]
    store.close()
    assert result.get("reminders") == 1, result
    assert len(done_rows) == 1, rows


# ---------------------------------------------------------------- [11]
def test_backup_does_not_import_rename_history(tmp_path):
    # 第七十九轮：备份自带的 profile_renames 不再写入本机改名历史，
    # 否则一份伪造备份就能把后续恢复的旧档案改挂到其它成员名下；
    # 备份数据本身仍按导出时的名称恢复
    source = SessionStore(str(tmp_path / "source.db"))
    source.save_profile("妈妈", {"age": "60"})
    assert source.rename_profile("妈妈", "母亲")
    backup = source.export_data()
    source.close()
    target = SessionStore(str(tmp_path / "target.db"))
    target.import_data(backup)
    profiles = {item["name"] for item in target.list_profiles()}
    resolved = target.resolve_profile_alias("妈妈")
    target.close()
    assert "母亲" in profiles, profiles
    assert resolved == "妈妈", resolved


# ---------------------------------------------------------------- [12]
def test_reminder_import_does_not_attach_by_title_substring(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    appointment = store.add_appointment(
        "妈妈", "复诊", scheduled_at="2026-05-01T09:00"
    )
    payload = {"reminders": [
        {"title": "复诊：高血压", "due_at": "2026-12-13T09:00",
         "note": "复诊 2026-12-14 心内科 王医生", "profile": "妈妈",
         "appointment_id": appointment["id"]},
    ]}
    store.import_data(payload)
    row = store.list_reminders(False, "妈妈")[0]
    store.close()
    assert int(row["appointment_id"] or 0) == 0, row


# ---------------------------------------------------------------- [13][22][23]
def _followup_setup(store, note="复诊 2026-11-28 心内科 王医生"):
    today = datetime.now()
    visit = store.add_visit_record(
        "妈妈",
        visit_date=(today - timedelta(days=10)).strftime("%Y-%m-%d"),
        diagnosis="高血压",
        next_followup_date=(today + timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压",
        scheduled_at=(today + timedelta(days=10)).strftime("%Y-%m-%d")
        + "T09:00",
    )
    reminder = store.add_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        note=note,
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], reminder["id"]
    )
    store.complete_reminder(reminder["id"])
    second, created = store.ensure_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created and second
    store.reopen_reminder(reminder["id"])
    return visit, appointment, reminder, second


def test_delete_appointment_removes_detached_visit_reminder(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    _visit, appointment, first, second = _followup_setup(store)
    store.delete_appointment(appointment["id"])
    pending = store.list_reminders(False, "妈妈")
    store.close()
    ids = {item["id"] for item in pending}
    assert first["id"] not in ids and second["id"] not in ids, pending


def test_reschedule_updates_detached_reminder_note(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    _visit, appointment, first, second = _followup_setup(store)
    new_day = (datetime.now() + timedelta(days=20)).strftime("%Y-%m-%d")
    store.update_appointment_partial(
        appointment["id"], scheduled_at=f"{new_day}T09:00"
    )
    row = store.get_reminder(second["id"])
    first_row = store.get_reminder(first["id"])
    store.close()
    # 备注里的复诊日期是预约日（提醒到期日仍提前一天，第八十轮修正
    # 与创建/安排路径同口径）；脱钩提醒并入预约待办
    # （第一百零二轮 [15]）
    assert first_row is None
    assert row["note"].startswith("复诊 " + str(new_day)), row
    assert str(row["due_at"])[:10] != new_day, row
    assert row["note"] != "复诊 2026-11-28 心内科 王医生", row


def test_rebuilt_reminder_clears_tombstone_for_restore(tmp_path):
    store = _store(tmp_path)
    tick = {"value": datetime.now()}

    def clock():
        return tick["value"].strftime("%Y-%m-%dT%H:%M:%S")

    store._now = clock
    store.save_profile("妈妈", {"age": "60"})
    today = tick["value"]
    visit = store.add_visit_record(
        "妈妈",
        visit_date=(today - timedelta(days=10)).strftime("%Y-%m-%d"),
        diagnosis="高血压",
        next_followup_date=(today + timedelta(days=10)).strftime("%Y-%m-%d"),
    )
    appointment = store.add_appointment(
        "妈妈", "复诊：高血压",
        scheduled_at=(today + timedelta(days=10)).strftime("%Y-%m-%d")
        + "T09:00",
    )
    first = store.add_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    store.set_visit_followup_links(
        visit["id"], appointment["id"], first["id"]
    )
    store.complete_reminder(first["id"])
    second, created = store.ensure_reminder(
        "复诊：高血压",
        (today + timedelta(days=9)).strftime("%Y-%m-%d"),
        profile="妈妈",
        appointment_id=appointment["id"],
    )
    assert created and second
    tick["value"] = today + timedelta(seconds=5)
    store.delete_reminder(first["id"])
    tick["value"] = today + timedelta(seconds=10)
    store.complete_reminder(second["id"])
    tick["value"] = today + timedelta(seconds=15)
    new_day = (today + timedelta(days=15)).strftime("%Y-%m-%d")
    store.update_appointment_partial(
        appointment["id"], scheduled_at=f"{new_day}T09:00"
    )
    rebuilt = [
        item for item in store.list_reminders(False, "妈妈")
        if int(item.get("appointment_id") or 0) == appointment["id"]
    ]
    assert rebuilt, "改期应重建提醒"
    store.update_appointment_partial(appointment["id"], status="cancelled")
    store.update_appointment_partial(appointment["id"], status="upcoming")
    restored = [
        item for item in store.list_reminders(False, "妈妈")
        if int(item.get("appointment_id") or 0) == appointment["id"]
    ]
    store.close()
    assert restored, "取消→恢复后提醒应能复活"


# ---------------------------------------------------------------- [6]
def test_untrusted_forwarded_shared_bucket_has_source_cap(
    monkeypatch, tmp_path
):
    token = "r76-untrusted-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.delenv("MEDIX_TRUST_PROXY_HEADERS", raising=False)
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    attacker = TestClient(
        api_server.app, base_url="http://localhost",
        client=("203.0.113.9", 56401),
    )
    try:
        codes = []
        for _ in range(12):
            codes.append(attacker.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": "1.2.3.4",
                },
            ).status_code)
        # 第七十七轮：未开启受信代理时伪造地址头不能跳过自身预锁定
        assert codes[:10] == [401] * 10, codes
        assert codes[10:] == [429, 429], codes
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 56402),
        )
        ok = victim.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "X-Forwarded-For": "5.6.7.8",
            },
        )
        assert ok.status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [15]
def test_loopback_single_hop_rotation_cannot_fill_shared_bucket(
    monkeypatch, tmp_path
):
    token = "r76-loopback-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.setenv("MEDIX_PROXY_CLIENT_IP_HEADER", "x-forwarded-for")
    monkeypatch.setenv("MEDIX_PROXY_SINGLE_HOP", "1")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "auth_failure_limiter",
        SlidingWindowLimiter(10, window=300),
    )
    monkeypatch.setattr(
        api_server, "auth_failure_limiter_loopback",
        SlidingWindowLimiter(100, window=300),
    )
    api_server._known_token_proofs.clear()
    attacker = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56403),
    )
    try:
        codes = []
        for index in range(12):
            codes.append(attacker.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": f"10.0.0.{index + 1}",
                },
            ).status_code)
        assert codes == [401] * 12, codes
        with api_server.auth_failure_limiter_loopback._lock:
            shared_hits = len(
                api_server.auth_failure_limiter_loopback._hits.get(
                    "ip:127.0.0.1", []
                )
            )
        assert shared_hits <= 10, shared_hits
        victim = TestClient(
            api_server.app, base_url="http://localhost",
            client=("127.0.0.1", 56404),
        )
        ok = victim.get(
            "/api/sessions",
            headers={
                "X-Medix-Token": token,
                "X-Forwarded-For": "192.168.1.20",
            },
        )
        assert ok.status_code == 200
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [16][21]
def test_resumed_medication_ignores_old_course_logs(tmp_path):
    store = _store(tmp_path)
    now = datetime.now()
    medication = store.add_medication(
        "妈妈", "二甲双胍", "500mg", frequency="每日两次",
        started_at=(now - timedelta(days=60)).strftime("%Y-%m-%d"),
    )
    store.add_medication_log(
        medication["id"],
        taken_at=(now - timedelta(days=5)).strftime("%Y-%m-%d") + "T08:00",
    )
    store.update_medication(medication["id"], active=False)
    store.update_medication(medication["id"], active=True)
    row = next(
        item for item in store.list_medications("妈妈")
        if item["id"] == medication["id"]
    )
    store.close()
    assert row["started_at"] == now.strftime("%Y-%m-%d"), row
    assert row["expected_week_doses"] == 2, row
    assert row["week_count"] == 0, row


# ---------------------------------------------------------------- [17][18][19][20]
def test_family_insight_limit_and_frontend_guards():
    api_source = (ROOT / "api_server.py").read_text(encoding="utf-8")
    assert "limit = 8 if not family_scope else max(8, 8 * 3)" in api_source
    app_source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "clearInflightForCurrentSession()" in app_source
    assert app_source.count("clearInflightForCurrentSession();") >= 2
    assert "Object.keys(readPatientContext()).length === 0" in app_source
    llm_source = (ROOT / "core" / "llm_client.py").read_text(
        encoding="utf-8"
    )
    assert "LLM call failed: %s status=%s" in llm_source
    assert 'f"模型调用失败：{message[:200]}"' not in llm_source


def test_classify_llm_error_hides_provider_body():
    from core.llm_client import LLMError, classify_llm_error

    class Boom(Exception):
        status_code = 422

    error = Boom("error: patient 张三 青霉素过敏 warfarin")
    result = classify_llm_error(error)
    assert isinstance(result, LLMError)
    assert "张三" not in result.user_message
    assert "青霉素" not in result.user_message
