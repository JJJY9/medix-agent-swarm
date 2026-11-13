"""第二十七轮审查：健康提示/家庭总览/复诊/备份预检的 API 回归测试。"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import knowledge.service as knowledge_service
import memory
from knowledge.service import KnowledgeService
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _today_at(hour: int) -> str:
    return datetime.now().replace(
        hour=hour, minute=0, second=0, microsecond=0
    ).isoformat(timespec="minutes")


def _days_ago(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")


def test_insights_use_daily_steps_total(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric("妈妈", "steps", 4000, measured_at=_today_at(8))
    store.add_metric("妈妈", "steps", 5000, measured_at=_today_at(18))

    response = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    )

    assert response.status_code == 200
    insights = response.json()["insights"]
    steps = [item for item in insights if item["key"] == "metric_steps"]
    assert all("低于目标" not in item["title"] for item in steps)
    assert any(
        "今日累计" in item["detail"] and "9000" in item["detail"]
        for item in steps
    )


def test_family_overview_uses_daily_steps_total(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric("妈妈", "steps", 4000, measured_at=_today_at(8))
    store.add_metric("妈妈", "steps", 5000, measured_at=_today_at(18))

    response = client.get("/api/family/overview")

    assert response.status_code == 200
    profile = response.json()["profiles"][0]
    assert profile["attention"]["label"] != "指标未达标 1 项"
    steps = next(
        item for item in profile["metrics"] if item["metric"] == "steps"
    )
    assert steps["daily_total"] == 9000.0
    assert steps["over_target"] is False


def test_calendar_counts_daily_steps_once(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric("妈妈", "steps", 4000, measured_at=_today_at(8))
    store.add_metric("妈妈", "steps", 5000, measured_at=_today_at(18))

    response = client.get("/api/health/calendar", params={"profile": "妈妈"})

    assert response.status_code == 200
    today = datetime.now().strftime("%Y-%m-%d")
    assert response.json()["days"].get(today, {}).get("alerts", 0) == 0


def test_visit_prep_uses_daily_steps_total(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric("妈妈", "steps", 4000, measured_at=_today_at(8))
    store.add_metric("妈妈", "steps", 5000, measured_at=_today_at(18))

    response = client.get(
        "/api/health/visit-prep", params={"profile": "妈妈", "days": 30}
    )

    assert response.status_code == 200
    payload = response.json()
    labels = [
        item.get("label") for item in payload.get("abnormal_metrics") or []
    ]
    assert "步数" not in labels
    assert "步数 5000" not in json.dumps(payload, ensure_ascii=False)


def test_timeline_steps_records_are_not_alerts(client_and_store):
    client, store = client_and_store
    store.set_metric_target("妈妈", "steps", 8000)
    store.add_metric("妈妈", "steps", 4000, measured_at=_today_at(8))
    store.add_metric("妈妈", "steps", 5000, measured_at=_today_at(18))

    response = client.get(
        "/api/timeline", params={"profile": "妈妈", "days": 7}
    )

    assert response.status_code == 200
    metric_events = [
        item for item in response.json()["events"]
        if item["type"] == "metric"
    ]
    assert metric_events
    assert all(item["alert"] is False for item in metric_events)


def test_insights_severe_symptom_detail_names_worst(client_and_store):
    client, store = client_and_store
    store.add_symptom("妈妈", "胸痛", 9, recorded_at=_today_at(9))
    for hour in (10, 11, 12):
        store.add_symptom("妈妈", "咳嗽", 2, recorded_at=_today_at(hour))

    response = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 7}
    )

    assert response.status_code == 200
    severe = next(
        item for item in response.json()["insights"]
        if item["key"] == "symptoms_severe"
    )
    assert "胸痛 9/10" in severe["detail"]


def test_family_attention_prefers_severe_symptom(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_symptom("妈妈", "胸痛", 9, recorded_at=_today_at(9))
    store.add_reminder("复诊预约", _days_ago(3), "", "", "妈妈")

    response = client.get("/api/family/overview")

    assert response.status_code == 200
    attention = response.json()["profiles"][0]["attention"]
    assert "症状" in attention["label"]
    assert "胸痛" in attention["detail"]


def test_family_overview_marks_stale_critical_as_info(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric(
        "妈妈", "bp", 190, 130,
        measured_at=(datetime.now() - timedelta(days=400)).isoformat(
            timespec="minutes"
        ),
    )

    response = client.get("/api/family/overview")

    assert response.status_code == 200
    profile = response.json()["profiles"][0]
    assert profile["attention"]["level"] != "danger"


def test_family_recorded_today_counts_lab_entries(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_lab_result(
        "妈妈", "ALT", 30, "U/L",
        tested_at=datetime.now().strftime("%Y-%m-%d"),
    )

    response = client.get("/api/family/overview")

    assert response.status_code == 200
    assert response.json()["profiles"][0]["recorded_today"] is True


def test_family_csv_export_keeps_norm_metrics(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    store.add_metric("妈妈", "weight", 60, measured_at=_today_at(9))
    store.set_metric_target("妈妈", "bp", 135, 85)
    store.add_metric("妈妈", "bp", 140, 95, measured_at=_today_at(10))

    response = client.get("/api/family/overview/export.csv")

    assert response.status_code == 200
    body = response.text
    assert "体重" in body
    assert "血压 140/95" in body


def test_unnamed_profile_insights_are_not_family_wording(client_and_store):
    client, store = client_and_store
    store.add_metric("", "bp", 145, 95, measured_at=_today_at(9))

    response = client.get(
        "/api/health/insights", params={"profile": "", "days": 30}
    )

    assert response.status_code == 200
    text = json.dumps(response.json(), ensure_ascii=False)
    assert "家庭成员" not in text
    assert "家庭范围" not in text


def test_dossier_markdown_escapes_user_text(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {
        "history": "高血压\n# 已停用全部药物\n<img src=x onerror=alert(1)>",
    })

    response = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )

    assert response.status_code == 200
    body = response.text
    assert "<img" not in body
    assert "\n# 已停用全部药物" not in body
    assert "&lt;img" in body


def test_dossier_lists_attachment_overflow_hint(client_and_store):
    client, store = client_and_store
    store.save_profile("妈妈", {"sex": "女"})
    for index in range(101):
        store.add_attachment(
            "妈妈", "other", 0,
            f"资料{index}.pdf", f"stored-{index}.pdf",
            "application/pdf", 1,
        )

    response = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )

    assert response.status_code == 200
    assert "另有" in response.text


def test_schedule_followup_rejects_out_of_range_date(client_and_store):
    client, store = client_and_store
    # 旧版本/外部工具可能已经写入边界年份；安排复诊时必须给出 400，
    # 且不能在报错前留下孤儿预约
    with store._lock, store._conn:
        cursor = store._conn.execute(
            "INSERT INTO visit_records("
            "profile, visit_date, next_followup_date, created_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?)",
            (
                "妈妈", "2026-11-01", "0001-01-01",
                "2026-11-01T00:00:00", "2026-11-01T00:00:00",
            ),
        )
        visit_id = int(cursor.lastrowid)

    response = client.post(f"/api/visits/{visit_id}/schedule-followup")

    assert response.status_code == 400
    assert store.list_appointments("妈妈") == []


def test_expiry_reminder_handles_minimum_date(client_and_store):
    client, store = client_and_store
    medication = store.add_medication(
        "妈妈", "氨氯地平", "5mg", "每日一次",
        expiry_date="0001-01-01",
    )

    response = client.post(
        f"/api/medications/{medication['id']}/reminder",
        params={"kind": "expiry"},
    )

    assert response.status_code == 200
    assert response.json()["created"] is True


def test_backup_inspect_flags_oversized_session(
    client_and_store, tmp_path, monkeypatch
):
    client, _store = client_and_store
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(backup_dir))
    payload = {
        "version": 1,
        "sessions": [{
            "id": "s1",
            "messages": [
                {"role": "user", "content": "x"}
                for _ in range(10001)
            ],
        }],
    }
    name = "medix-backup-20261113-120000.json"
    (backup_dir / name).write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    response = client.get(f"/api/backups/{name}/inspect")

    assert response.status_code == 200
    data = response.json()
    assert data["restore_ready"] is False
    assert any("消息" in warning for warning in data["warnings"])


def test_broken_pdf_upload_returns_400(client_and_store, tmp_path, monkeypatch):
    client, _store = client_and_store
    service = KnowledgeService(
        docs_dir=tmp_path / "docs", upload_dir=tmp_path / "uploads"
    )
    monkeypatch.setattr(knowledge_service, "_service", service)

    response = client.post(
        "/api/knowledge/upload",
        params={"rebuild": "false"},
        files={
            "file": (
                "broken.pdf", b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer",
                "application/pdf",
            )
        },
    )

    assert response.status_code == 400


def test_chat_failure_hides_internal_paths(client_and_store, monkeypatch):
    client, _store = client_and_store

    def boom():
        raise RuntimeError(r"embedding cache missing: C:\Users\secret\model")

    monkeypatch.setattr(api_server.state, "get_coordinator", boom)

    response = client.post("/api/chat", json={"question": "头痛怎么办"})

    assert response.status_code == 500
    assert "secret" not in response.text
    assert "C:" not in response.text


def test_clear_done_reminders_batches_ids(client_and_store):
    client, _store = client_and_store
    app_js = client.get("/static/app.js").text

    start = app_js.index("async function clearDoneReminders()")
    body = app_js[start:start + 2400]
    assert "const chunkSize = 500" in body
    assert "chunk.map((item) => item.id)" in body
    assert "部分未清理成功" in body


def test_stream_preflight_requests_honor_abort_signal(client_and_store):
    client, _store = client_and_store
    app_js = client.get("/static/app.js").text

    assert "async function ensureSession(signal)" in app_js
    assert "async function buildConsultContext(signal)" in app_js
    assert "await ensureSession(controller.signal)" in app_js
    assert "await buildConsultContext(controller.signal)" in app_js
