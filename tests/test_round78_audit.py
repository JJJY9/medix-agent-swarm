# -*- coding: utf-8 -*-
"""第七十八轮审查修复的回归测试。"""
import asyncio
import logging
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.ratelimit import SlidingWindowLimiter
from core.security import _AccessLogQueryFilter
from core.triage import (
    _active_medical_text, detect_red_flags, patient_complaint_text,
)
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


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


def _seed_same_second(store, rows):
    """插入同秒记录并统一 created_at（用于同身份组孪生场景）。"""
    for profile, name, dose, freq, note, active in rows:
        store.add_medication(
            profile, name, dose, frequency=freq, note=note, active=active
        )
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE medications SET updated_at = ?", (CREATED,)
        )


# ---------------------------------------------------------------- [1][9]
def test_long_stop_only_suppresses_adjacent_drug_clause():
    active = _active_medical_text({"history": "怀孕39周，已经停药一年多了"})
    assert "怀孕39周" in active, active
    active = _active_medical_text({"history": "华法林吃了三年，停了一年多了"})
    assert "华法林" not in active, active
    active = _active_medical_text(
        {"history": "甲氨蝶呤吃了两年，化疗结束5年了"}
    )
    assert "甲氨蝶呤" not in active, active
    active = _active_medical_text({"history": "正在化疗，已经停药一年多了"})
    assert "化疗" in active, active
    active = _active_medical_text(
        {"history": "现在吃利伐沙班，已经停药一年多了"}
    )
    assert "利伐沙班" in active, active
    active = _active_medical_text({
        "history": "已经停药一年多了",
        "medications": [{"name": "华法林", "active": True}],
    })
    assert "华法林" in active, active


def test_pregnancy_bleeding_survives_bare_long_stop():
    result = _red("怀孕39周，已经停药一年多了，今天阴道出血")
    assert "孕产期出血" in result["matches"], result
    result = _red("华法林吃了三年，停了一年多了，今天头撞到门框")
    assert "抗凝治疗期间头部外伤" not in result["matches"], result
    result = _red("头撞到门框", {"history": "化疗结束5年了每天吃华法林"})
    assert "抗凝治疗期间头部外伤" in result["matches"], result


# ---------------------------------------------------------------- [2][10]
def test_recurrent_fever_is_positive():
    assert _red("再发高烧", {"age": "2个月"})["level"] == "emergency"
    assert "免疫功能低下伴发热" in _red(
        "反复再发高热", {"history": "正在化疗"}
    )["matches"]
    assert "3 个月以下婴儿发热" in _red(
        "孩子昨晚退烧，凌晨又再发高烧", {"age": "2个月"}
    )["matches"]
    assert _red("宝宝没有发烧，也没有再发高烧", {"age": "2个月"})[
        "level"
    ] == "none"
    assert _red(
        "化疗后没有发烧，也没有再发高烧", {"history": "正在化疗"}
    )["level"] == "none"


# ---------------------------------------------------------------- [11]
@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我们都吃，今天头撞到门框",
        "我爸吃华法林，我跟他也都在吃，今天头撞到门框",
        "我爸吃华法林，我和他一起在吃，今天头撞到门框",
    ],
)
def test_continuation_marker_variants(text):
    assert _red(text)["level"] == "emergency"


# ---------------------------------------------------------------- [12]
@pytest.mark.parametrize(
    "text",
    ["被球砸在头上", "手机砸在头上", "头在门框上撞了一下",
     "头上有包", "头砸在门框上"],
)
def test_head_trauma_word_orders(text):
    result = _red(text, {"history": "正在吃华法林"})
    assert "抗凝治疗期间头部外伤" in result["matches"], (text, result)


def test_pillow_swelling_is_not_head_trauma():
    result = _red("枕头上有个包", {"history": "正在吃华法林"})
    assert "抗凝治疗期间头部外伤" not in result["matches"], result


# ---------------------------------------------------------------- [19]
@pytest.mark.parametrize(
    "text",
    [
        "我爸吃华法林，我岳母也吃，今天头撞到门框",
        "我爸吃华法林，我老伴也吃，今天头撞到门框",
        "我爸吃华法林，我家孩子也吃，今天头撞到门框",
        "我爸吃华法林，我公公也吃，今天头撞到门框",
    ],
)
def test_family_subject_guard_covers_all_markers(text):
    assert _red(text)["level"] == "none", text
    safety = check_medication_safety(
        text.replace("今天头撞到门框", "能吃布洛芬吗"), {"age": "35岁"}
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [20]
def test_postpartum_recent_uses_explicit_years():
    assert "孕产期出血" not in _red("生完孩子5年了，今天阴道出血")[
        "matches"
    ]
    assert "孕产期出血" not in _red("顺产9年了，今天出血")["matches"]
    assert "孕产期出血" in _red("生完孩子两个月，今天阴道出血")[
        "matches"
    ]


# ---------------------------------------------------------------- [3]
def test_moved_row_not_counted_in_local_group_size(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("爸爸", {"age": "62"})
    meds = []
    for profile, name, dose, freq in (
        ("妈妈", "氨氯地平", "10mg", "每日1次"),
        ("妈妈", "氨氯地平", "10mg", "每日2次"),
        ("爸爸", "阿司匹林", "10mg", "每日1次"),
        ("妈妈", "二甲双胍", "10mg", "每日1次"),
    ):
        meds.append(store.add_medication(
            profile, name, dose, frequency=freq
        ))
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE medications SET updated_at = ?", (CREATED,)
        )
    backup = store.export_data()
    # 合并恢复：目标库先有一条无关用药，本地 id 整体漂移一位
    store.add_medication("妈妈", "钙片", "1片", frequency="每日1次")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    target = next(
        r for r in rows
        if r["profile"] == "妈妈" and r["frequency"] == "每日2次"
    )
    store.delete_medication(target["id"])
    for row in rows:
        if row["name"] == "阿司匹林":
            store.update_medication(row["id"], profile="妈妈")
        if row["name"] == "二甲双胍":
            store.update_medication(row["id"], name="格华止")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    store.close()
    daily2 = [
        r for r in rows
        if r["profile"] == "妈妈" and r["name"] == "氨氯地平"
        and r["frequency"] == "每日2次"
    ]
    assert len(daily2) == 1, rows
    assert len([r for r in rows if r["name"] == "格华止"]) == 1, rows
    assert all(r["profile"] != "妈妈" for r in rows
               if r["name"] == "二甲双胍"), rows


# ---------------------------------------------------------------- [4][5]
def test_shared_followup_reminder_survives_later_reminder_rows(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    backup = {
        "appointments": [
            {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
             "note": "", "owner_visit_id": 0, "status": "upcoming",
             "scheduled_at": "2027-01-20T09:00",
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "reminders": [
            {"id": 1, "profile": "妈妈", "title": "复诊：高血压",
             "note": "", "due_at": "2027-01-19T09:00",
             "appointment_id": 1, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
            # 原始 id 更大的无关提醒排在共享复诊提醒之后处理
            {"id": 9, "profile": "妈妈", "title": "无关提醒",
             "note": "", "due_at": "2027-02-01T09:00",
             "appointment_id": 0, "done": 0,
             "created_at": CREATED, "updated_at": CREATED},
        ],
        "visit_records": [
            {"id": 1, "profile": "妈妈", "visit_date": "2026-11-01",
             "diagnosis": "高血压",
             "next_followup_date": "2027-03-01",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
            {"id": 2, "profile": "妈妈", "visit_date": "2026-11-02",
             "diagnosis": "糖尿病",
             "next_followup_date": "2027-01-20",
             "followup_appointment_id": 1, "followup_reminder_id": 1,
             "created_at": CREATED, "updated_at": CREATED},
        ],
    }
    store.import_data(backup)
    reminders = store.list_reminders(True, "妈妈")
    visits = store.list_visit_records("妈妈")
    store.close()
    followup = [r for r in reminders if r["title"] == "复诊：高血压"]
    assert len(followup) == 1, reminders
    surviving = next(v for v in visits if v["diagnosis"] == "糖尿病")
    assert int(surviving.get("followup_reminder_id") or 0) == followup[0]["id"]


# ---------------------------------------------------------------- [8]
def test_forged_future_rename_rejected(tmp_path):
    store = _store(tmp_path)
    store.save_profile("爸爸", {"age": "62"})
    forged = {
        "exported_at": "0000-01-01T00:00:00",
        "profile_renames": [
            {"old_name": "妈妈", "new_name": "父亲",
             "renamed_at": "9999-01-01T00:00:00"},
        ],
        "sessions": [{"id": 1, "profile": "妈妈", "title": "t",
                      "created_at": CREATED, "updated_at": CREATED}],
        "allergies": [{"id": 1, "profile": "妈妈", "substance": "青霉素",
                       "created_at": CREATED, "updated_at": CREATED}],
    }
    store.import_data(forged)
    try:
        assert store.resolve_profile_alias("妈妈") == "妈妈"
        assert all(
            item["profile"] == "妈妈"
            for item in store.list_allergies(None)
        )
    finally:
        store.close()


def test_payload_renames_do_not_remap_current_import(tmp_path):
    store = _store(tmp_path)
    forged = {
        "exported_at": "2026-01-01T00:00:00",
        "profile_renames": [
            {"old_name": "妈妈", "new_name": "父亲",
             "renamed_at": "2025-01-01T00:00:00"},
        ],
        "sessions": [{"id": 1, "profile": "妈妈", "title": "t",
                      "created_at": CREATED, "updated_at": CREATED}],
        "allergies": [{"id": 1, "profile": "妈妈", "substance": "青霉素",
                       "created_at": CREATED, "updated_at": CREATED}],
    }
    store.import_data(forged)
    allergies = store.list_allergies(None)
    store.close()
    assert all(item["profile"] == "妈妈" for item in allergies), allergies


# ---------------------------------------------------------------- [13]
def test_cross_profile_twin_step1_claim_guard(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("爸爸", {"age": "62"})
    filler = store.add_medication("妈妈", "填充药", "1片", frequency="每日1次")
    _seed_same_second(store, [
        ("妈妈", "氨氯地平", "5mg", "每日1次", "妈妈服用", True),
        ("爸爸", "氨氯地平", "5mg", "每日1次", "爸爸服用", False),
    ])
    store.delete_medication(filler["id"])
    backup = store.export_data()  # 主键 2,3
    store.import_data(backup)     # 新机恢复为本地 id 1,2
    rows = store.list_medications(None, limit=100)
    mom = next(r for r in rows if r["profile"] == "妈妈")
    dad = next(r for r in rows if r["profile"] == "爸爸")
    store.delete_medication(mom["id"])
    store.update_medication(dad["id"], profile="妈妈")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    store.close()
    mom_active = [
        r for r in rows
        if r["profile"] == "妈妈" and r["note"] == "妈妈服用"
    ]
    assert len(mom_active) == 1, rows
    assert not [r for r in rows if r["profile"] == "爸爸"], rows


# ---------------------------------------------------------------- [14]
def test_inserted_rows_consume_missing_budget(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {"age": "60"})
    filler = store.add_medication("妈妈", "填充药", "1片", frequency="每日1次")
    _seed_same_second(store, [
        ("妈妈", "二甲双胍", "500mg", "每日2次", "", True),
        ("妈妈", "氨氯地平", "5mg", "每日1次", "", True),
    ])
    store.delete_medication(filler["id"])   # 备份主键 2,3
    backup = store.export_data()
    store.import_data(backup)               # 本地 id 1,2
    rows = store.list_medications(None, limit=100)
    metformin = next(r for r in rows if r["name"] == "二甲双胍")
    amlodipine = next(r for r in rows if r["name"] == "氨氯地平")
    store.delete_medication(metformin["id"])
    store.update_medication(amlodipine["id"], name="络活喜")
    store.import_data(backup)
    rows = store.list_medications(None, limit=100)
    store.close()
    names = sorted(r["name"] for r in rows)
    assert "二甲双胍" in names, names
    assert "氨氯地平" not in names and "络活喜" in names, names


# ---------------------------------------------------------------- [6]
def test_loopback_proxy_shared_bucket_not_hard_prelocked(
    monkeypatch, tmp_path
):
    token = "r78-loopback-token-0123456789"
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
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56501),
    )
    try:
        codes = [
            client.get(
                "/api/sessions",
                headers={
                    "X-Medix-Token": "guess",
                    "X-Forwarded-For": f"10.9.9.{index}",
                },
            ).status_code
            for index in range(12)
        ]
        assert codes == [401] * 12, codes
        # 同机反代后的正确令牌（新设备/凭证过期）不能被硬预锁定
        correct = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token,
                     "X-Forwarded-For": "10.9.9.50"},
        )
        assert correct.status_code == 200, correct.status_code
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [7]
def test_family_overview_allergy_join_matches_insights(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60", "allergies": "无药物过敏"})
    store.add_allergy("妈妈", "青霉素")
    store.add_medication("妈妈", "阿莫西林", "0.5g", frequency="每日3次")
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    safety = mom.get("medication_lab_safety") or {}
    assert int(safety.get("count") or 0) >= 1, mom.get("attention")
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()["insights"]
    assert any(
        "青霉素" in str(item.get("detail", ""))
        or "青霉素" in str(item.get("title", ""))
        for item in insights
    ), insights


# ---------------------------------------------------------------- [15]
def test_unprovable_gate_source_uses_total_queue(
    monkeypatch, tmp_path
):
    token = "r78-gate-token-0123456789"
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", token)
    monkeypatch.setenv("MEDIX_TRUST_PROXY_HEADERS", "1")
    monkeypatch.delenv("MEDIX_TRUSTED_PROXY_ADDRS", raising=False)
    store = _store(tmp_path)
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "_forwarded_client_address",
        lambda request: "198.51.100.5",
    )
    monkeypatch.setattr(
        api_server, "_forwarded_appended_address",
        lambda request: "",
    )
    api_server._known_token_proofs.clear()
    seen = []
    original = api_server._auth_compare_enter

    async def spy(key, source=""):
        seen.append((key, source))
        return await original(key, source=source)

    monkeypatch.setattr(api_server, "_auth_compare_enter", spy)
    client = TestClient(
        api_server.app, base_url="http://localhost",
        client=("127.0.0.1", 56502),
    )
    try:
        client.get(
            "/api/sessions",
            headers={"X-Medix-Token": "guess",
                     "X-Forwarded-For": "203.0.113.7"},
        )
        assert seen and seen[0][1] == "", seen
        correct = client.get(
            "/api/sessions",
            headers={"X-Medix-Token": token,
                     "X-Forwarded-For": "203.0.113.7"},
        )
        assert correct.status_code == 200, correct.status_code
    finally:
        api_server._known_token_proofs.clear()
        store.close()


# ---------------------------------------------------------------- [16]
def test_random_and_bedtime_glucose_not_silent(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    today = datetime.now()
    store.add_metric(
        "妈妈", "glucose", 15.0, context="random",
        measured_at=(today - timedelta(days=2)).strftime("%Y-%m-%dT08:00"),
    )
    store.add_metric(
        "妈妈", "glucose", 12.8, context="bedtime",
        measured_at=(today - timedelta(days=1)).strftime("%Y-%m-%dT22:00"),
    )
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    attention = mom.get("attention") or {}
    attention_text = " ".join(str(value) for value in attention.values())
    assert "血糖" in attention_text, attention
    insights = client.get(
        "/api/health/insights", params={"days": 30, "profile": "妈妈"}
    ).json()["insights"]
    keys = [item.get("key") for item in insights]
    assert "metric_glucose_random" in keys, keys
    assert "all_clear" not in keys, keys
    report = asyncio.run(api_server._weekly_report_impl(None, 30, 400, False))
    kinds = [
        alert.get("kind")
        for alert in report.get("member_glucose_alerts") or []
    ]
    assert "random" in kinds, kinds


# ---------------------------------------------------------------- [17]
def test_inflight_restore_honors_baseline():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function restoreInflightForOpenSession")
    end = source.index("function restoreInflightQuestion", start)
    block = source[start:end]
    assert "const baseline = Number((data && data.baseline) || 0);" in block
    assert "if (owner && owner === targetId) {" in block
    assert "> baseline" in block


# ---------------------------------------------------------------- [18]
def test_access_log_filter_redacts_sensitive_path_without_query():
    filt = _AccessLogQueryFilter()
    record = logging.LogRecord(
        "gunicorn.access", logging.INFO, __file__, 1, "%(r)s", {
            "r": "GET /api/profiles/妈妈 HTTP/1.1",
            "s": "200",
        }, None,
    )
    filt.filter(record)
    assert record.args["r"] == "GET /api/profiles/<redacted> HTTP/1.1"
    query_record = logging.LogRecord(
        "gunicorn.access", logging.INFO, __file__, 1, "%(r)s", {
            "r": "GET /api/health/search?q=抑郁症 HTTP/1.1",
            "s": "200",
        }, None,
    )
    filt.filter(query_record)
    assert query_record.args["r"] == "GET /api/health/search?<redacted>"


# ---------------------------------------------------------------- [21]
def test_non_context_controls_do_not_mark_context_cleared():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("const onContextPanelEdit")
    end = source.index("els.contextPanel.addEventListener('input'", start)
    block = source[start:end]
    assert "const isContextField =" in block
    assert "isContextField\n    && Object.keys(readPatientContext()).length" \
        in block
