"""提醒重复窗口、同名复查清理与打卡联动的回归测试。"""
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_repeat_until_before_due_is_rejected(client_and_store):
    client, _store = client_and_store
    response = client.post("/api/reminders", json={
        "title": "每月复查",
        "due_at": _date(10),
        "repeat_days": 7,
        "repeat_until": _date(3),
    })

    assert response.status_code == 400
    assert "重复截止" in response.json()["detail"]


def test_repeat_until_same_day_is_allowed(client_and_store):
    client, _store = client_and_store
    response = client.post("/api/reminders", json={
        "title": "当天复查",
        "due_at": _date(10),
        "repeat_days": 7,
        "repeat_until": _date(10),
    })

    assert response.status_code == 200


def test_patch_due_after_repeat_until_is_rejected(client_and_store):
    client, _store = client_and_store
    reminder = client.post("/api/reminders", json={
        "title": "复查",
        "due_at": _date(3),
        "repeat_days": 7,
        "repeat_until": _date(20),
    }).json()["reminder"]

    response = client.patch(
        f"/api/reminders/{reminder['id']}", json={"due_at": _date(30)}
    )

    assert response.status_code == 400


def test_same_title_care_plans_do_not_complete_each_other(
    client_and_store,
):
    client, _store = client_and_store
    first = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(10),
    }).json()["care_item"]
    second = client.post("/api/care-items", json={
        "profile": "妈妈", "title": "眼底检查",
        "interval_days": 365, "next_due_at": _date(20),
    }).json()["care_item"]

    first_reminder = client.post(
        f"/api/care-items/{first['id']}/reminder"
    ).json()["reminder"]
    second_reminder = client.post(
        f"/api/care-items/{second['id']}/reminder"
    ).json()["reminder"]

    pending = {
        row["id"]: row
        for row in client.get("/api/reminders").json()["reminders"]
    }
    assert first_reminder["id"] in pending
    assert second_reminder["id"] in pending

    client.post(f"/api/care-items/{first['id']}/done", json={})
    pending = {
        row["id"]: row
        for row in client.get("/api/reminders").json()["reminders"]
    }
    # 完成第一条计划不能顺带完成第二条计划的提醒
    assert second_reminder["id"] in pending

    # 删除第一条计划也不能删掉同名计划的提醒
    client.delete(f"/api/care-items/{first['id']}")
    pending = {
        row["id"]: row
        for row in client.get("/api/reminders").json()["reminders"]
    }
    assert second_reminder["id"] in pending


def test_reminder_edit_keeps_time_part():
    assert "reminderEditOriginal" in SCRIPT
    assert ".slice(0, 10) === dueAt" in SCRIPT
    assert ".slice(0, 10) === untilInput" in SCRIPT


def test_medication_checkin_completes_overdue_and_today():
    start = SCRIPT.index("async function completeDueMedicationReminder")
    body = SCRIPT[start:start + 2600]
    # 不再限制只能逾期 1 天，并在后端顺延到今天时继续完成
    assert "days <= 0" in body
    assert "attempt < 10" in body
    assert "todayLocalDate()" in body
