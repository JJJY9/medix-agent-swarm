# -*- coding: utf-8 -*-
"""第八十二轮审查修复的回归测试。"""
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

import api_server
import memory
from core.medication_safety import check_medication_safety
from core.triage import (
    _active_medical_text, detect_red_flags, patient_complaint_text,
)
from memory.session_store import SessionStore

ROOT = Path(__file__).resolve().parent.parent
CREATED = "2026-06-01T08:00:00"


def _store(tmp_path):
    return SessionStore(str(tmp_path / "sessions.db"))


def _red(text, context=None):
    return detect_red_flags(patient_complaint_text(text), context or {})


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _rem_rows(store):
    return [
        (r["id"], r["profile"], r["title"], r["due_at"], r["note"])
        for r in store.list_reminders(True, None)
    ]


# ---------------------------------------------------------------- [1][3]
@pytest.mark.parametrize(
    "middle",
    [
        "奥美拉唑吃了两年",
        "左甲状腺素吃了两年",
        "恩替卡韦吃了两年",
        "氯雷他定吃了两年",
        "阿卡波糖吃了两年",
        "孟鲁司特吃了两年",
        "降压药吃了两年",
        "去年吃中药调理",
    ],
)
def test_other_drug_clause_blocks_stop_attribution(middle):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{middle}，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


def test_immunosuppressant_kept_with_intervening_drug():
    result = _red(
        "今天发烧39度",
        {"history": "甲氨蝶呤吃了两年，奥美拉唑吃了两年，停了一年多了"},
    )
    assert "免疫功能低下伴发热" in result["matches"], result
    result = _red(
        "今天头撞到门框",
        {"history": "华法林没吃完，奥美拉唑吃了两年，停了一年多了"},
    )
    assert "抗凝治疗期间头部外伤" in result["matches"], result


@pytest.mark.parametrize(
    "bare", ["胃不舒服", "用着不舒服", "胃不舒服打了点滴", "吃得少"]
)
def test_bare_verb_clause_still_attributed_to_current_drug(bare):
    result = _red(
        "今天头撞到门框",
        {"history": f"华法林吃了三年，{bare}，停了一年多了"},
    )
    assert result["level"] == "none", result


# ---------------------------------------------------------------- [2]
def test_fallback_claim_does_not_swallow_moved_twin(tmp_path):
    store = _store(tmp_path)
    store.save_profile("妈妈", {})
    store.save_profile("爸爸", {})
    backup = {"reminders": [
        {"id": 1, "profile": "妈妈", "title": "甲", "note": "N1",
         "due_at": "2027-01-20T09:00", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 2, "profile": "爸爸", "title": "乙", "note": "N2",
         "due_at": "2027-02-15T09:00", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
        {"id": 4, "profile": "妈妈", "title": "丙", "note": "N3",
         "due_at": "2027-02-15T09:00", "done": 0,
         "created_at": CREATED, "updated_at": CREATED},
    ]}
    store.add_reminder("本地", "2027-03-01T09:00", profile="妈妈")
    store.import_data(backup)
    rows = _rem_rows(store)
    yi = next(r for r in rows if r[2] == "乙")
    bing = next(r for r in rows if r[2] == "丙")
    store.update_reminder(yi[0], profile="妈妈")
    store.delete_reminder(bing[0])
    store.import_data(backup)
    rows = _rem_rows(store)
    store.import_data(backup)
    rows2 = _rem_rows(store)
    store.close()
    assert len([r for r in rows if r[2] == "丙"]) == 1, rows
    assert len([r for r in rows if r[2] == "乙"]) == 1, rows
    assert sorted((r[1], r[2]) for r in rows2) \
        == sorted((r[1], r[2]) for r in rows)


# ---------------------------------------------------------------- [4]
@pytest.mark.parametrize(
    "text",
    [
        "我每天吃华法林，我爸以前也吃华法林，两年前已经停药了，"
        "我能吃布洛芬吗",
        "我每天吃华法林，我爸以前也吃华法林，两年前已停药，"
        "我能吃布洛芬吗",
        "我每天吃华法林，我爸以前也吃华法林，两年前早就停药了，"
        "我能吃布洛芬吗",
    ],
)
def test_family_time_already_stop(text):
    assert check_medication_safety(text, {})["level"] == "warning"


# ---------------------------------------------------------------- [5]
def test_medication_safety_intervening_drug_attribution():
    safety = check_medication_safety(
        "华法林没吃完，二甲双胍吃了两年，停了一年多了，我能吃布洛芬吗",
        {},
    )
    assert safety["level"] == "warning", safety
    safety = check_medication_safety(
        "华法林没吃完，最近睡眠不好，已经停药半年了，我能吃布洛芬吗",
        {},
    )
    assert safety["level"] == "none", safety


# ---------------------------------------------------------------- [6]
@pytest.mark.parametrize(
    "text",
    [
        "去年流产过，今天阴道出血",
        "前年胎停过，今天见红",
        "曾经自然流产过，今天下面出血",
    ],
)
def test_past_miscarriage_prefix_forms(text):
    assert "孕产期出血" not in _red(text)["matches"], text


# ---------------------------------------------------------------- [7]
@pytest.mark.parametrize(
    "text",
    [
        "胸痛是什么感觉",
        "心梗的胸痛是什么感觉",
        "咯血是什么感觉",
        "昏迷是什么感觉",
        "胸部压榨感是什么感觉",
        "胸痛是什么样的",
    ],
)
def test_sensation_definition_question_filtered(text):
    assert _red(text)["level"] == "none", text


def test_course_definition_question_kept():
    assert _red("胸痛三天了是什么病")["level"] == "emergency"


# ---------------------------------------------------------------- [8]
@pytest.mark.parametrize(
    "text",
    [
        "孩子2个月，体温不到38度",
        "孩子2个月，发烧不到38度",
        "孩子2个月，体温没有到38度",
        "孩子2个月，没有到38度",
    ],
)
def test_subthreshold_temperature(text):
    assert _red(text, {"age": "2个月"})["level"] == "none", text


def test_real_fever_still_flags():
    assert "3 个月以下婴儿发热" in _red(
        "孩子2个月，体温38.5度", {"age": "2个月"}
    )["matches"]
    assert "3 个月以下婴儿发热" in _red(
        "孩子2个月，发烧到39度", {"age": "2个月"}
    )["matches"]


# ---------------------------------------------------------------- [9]
def test_moved_and_rescheduled_reminder_not_duplicated(tmp_path):
    store = _store(tmp_path)
    for name in ("妈妈", "爸爸", "奶奶"):
        store.save_profile(name, {})
    a = store.add_reminder("吃药", "2027-01-20T09:00", note="N1",
                           profile="妈妈")
    b = store.add_reminder("复查", "2027-02-15T09:00", note="N2",
                           profile="爸爸")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE reminders SET created_at = ?", (CREATED,)
        )
        store._conn.execute(
            "UPDATE reminders SET updated_at = ?", (CREATED,)
        )
    backup = store.export_data()
    store.update_reminder(a["id"], profile="奶奶",
                          due_at="2027-02-15T10:00")
    store.delete_reminder(b["id"])
    store.import_data(backup)
    rows = _rem_rows(store)
    store.close()
    assert [
        r for r in rows
        if r[2] == "吃药" and str(r[3]).startswith("2027-01-20")
    ] == [], rows
    assert [
        r for r in rows
        if r[2] == "吃药" and str(r[3]).startswith("2027-02-15")
    ] and len([r for r in rows if r[2] == "吃药"]) == 1, rows


# ---------------------------------------------------------------- [10]
def test_completed_reminder_cleanup_does_not_opt_out(client_and_store):
    client, store = client_and_store
    today = datetime.now().date()
    visit = store.add_visit_record(
        "妈妈", visit_date=str(today - timedelta(days=2)),
        diagnosis="高血压",
        next_followup_date=str(today + timedelta(days=5)),
    )
    resp = client.post(f"/api/visits/{visit['id']}/schedule-followup")
    appt_id = resp.json()["appointment"]["id"]
    reminder_id = resp.json()["reminder"]["id"]
    client.post(f"/api/reminders/{reminder_id}/complete")
    assert client.post(
        "/api/reminders/bulk_delete", json={"ids": [reminder_id]}
    ).status_code == 200
    client.patch(f"/api/appointments/{appt_id}", json={
        "scheduled_at": str(today + timedelta(days=12)) + "T09:00",
    })
    pending = store.list_reminders(False, "妈妈")
    visit_row = store.get_visit_record(visit["id"])
    assert len(pending) == 1, pending
    assert int(visit_row.get("followup_reminder_id") or 0) \
        == pending[0]["id"]
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "cancelled"})
    client.patch(f"/api/appointments/{appt_id}",
                 json={"status": "upcoming"})
    assert len(store.list_reminders(False, "妈妈")) == 1
    # 主动删除待办提醒仍是 opt-out
    client.delete(f"/api/reminders/{store.list_reminders(False, '妈妈')[0]['id']}")
    client.patch(f"/api/appointments/{appt_id}", json={
        "scheduled_at": str(today + timedelta(days=20)) + "T09:00",
    })
    assert store.list_reminders(False, "妈妈") == []


# ---------------------------------------------------------------- [11]
def test_proxy_header_detection_is_linear():
    headers = [(b"x-forwarded-a", b"") for _ in range(900)]
    scope = {
        "type": "http", "method": "GET", "path": "/api/sessions",
        "headers": headers, "client": ("203.0.113.9", 1),
        "server": ("test", 80), "scheme": "http",
        "query_string": b"",
    }
    request = Request(scope)
    started = time.monotonic()
    result = api_server._has_proxy_headers(request)
    elapsed = time.monotonic() - started
    assert elapsed < 0.05, elapsed
    assert result is False


# ---------------------------------------------------------------- [12]
def test_frontend_draft_owner_sticky_while_editing():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    assert "const stillEditingOwnDraft = Boolean(" in source
    start = source.index("const stillEditingOwnDraft")
    end = source.index("if (!stillEditingOwnDraft)", start)
    block = source[start:end]
    assert "els.input.value.includes(previousDraft)" in block


# ---------------------------------------------------------------- [13]
@pytest.mark.parametrize(
    "text",
    [
        "分娩时阴道出血，现在头晕",
        "生产时阴道出血，现在头晕",
        "分娩时出血很多，现在头晕",
    ],
)
def test_intrapartum_bleeding(text):
    assert "孕产期出血" in _red(text)["matches"], text


def test_production_line_still_not_postpartum():
    assert "孕产期出血" not in _red(
        "在生产线工作，手被划伤出血"
    )["matches"]


# ---------------------------------------------------------------- [14]
def test_daily_total_precision_consistent(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.set_metric_target("妈妈", "steps", 8000)
    day = datetime.now().strftime("%Y-%m-%d")
    store.add_metric("妈妈", "steps", 4000.0, measured_at=day + "T00:01")
    store.add_metric("妈妈", "steps", 3999.96, measured_at=day + "T00:02")
    overview = client.get("/api/family/overview").json()
    mom = next(p for p in overview["profiles"] if p["name"] == "妈妈")
    assert (mom.get("attention") or {}).get("level") in ("ok", "info")
    for params in ({"days": 30, "profile": "妈妈"}, {"days": 30}):
        insights = client.get(
            "/api/health/insights", params=params
        ).json()["insights"]
        assert [
            item for item in insights
            if item.get("key") == "metric_steps"
            and item.get("level") == "warning"
        ] == []


# ---------------------------------------------------------------- [15]
def test_frontend_backup_slot_cross_session_prefix_guard():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("function clearOtherSessionInflight")
    end = source.index("function removeInflightRecord", start)
    block = source[start:end]
    assert "itemOwner !== String(sessionId)" in block
    assert "其它会话停止未落库的主诉是唯一副本" in block


# ---------------------------------------------------------------- [16]
def test_frontend_dedupe_branch_restores_leftover():
    source = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    start = source.index("if (composerSubmit && inflightPersistedText")
    end = source.index("const remainder = stripInflightQuestion", start)
    block = source[start:end]
    assert "const leftover = stripInflightQuestion(current, persistedText);" \
        in block
    assert "已跳过刚完成的重复问题，其余内容已放回输入框" in block


# ---------------------------------------------------------------- [17]
def test_attachment_counts_respect_profile(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"age": "60"})
    store.save_profile("孩子", {"age": "5"})
    child_visit = store.add_visit_record(
        "孩子", visit_date=str(datetime.now().date()), diagnosis="感冒",
    )
    store.add_attachment(
        "孩子", "visit", child_visit["id"], "报告.pdf", "a.pdf",
        "application/pdf", 10, "",
    )
    mom_counts = client.get(
        "/api/attachments/counts", params={"profile": "妈妈"}
    ).json()["counts"]
    all_counts = client.get("/api/attachments/counts").json()["counts"]
    assert mom_counts["visit"] == {}, mom_counts
    assert str(child_visit["id"]) in all_counts["visit"], all_counts
