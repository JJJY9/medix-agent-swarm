"""报表、家庭档案与 ICS 导出的安全回归测试。"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
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


def test_weekly_and_family_adherence_ignore_prn_logs(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    scheduled = client.post("/api/medications", json={
        "profile": "母亲", "name": "氨氯地平", "frequency": "每日一次",
    }).json()["medication"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ? WHERE id = ?",
            (
                (now - timedelta(days=6)).isoformat(timespec="seconds"),
                scheduled["id"],
            ),
        )
    prn = client.post("/api/medications", json={
        "profile": "母亲", "name": "布洛芬", "frequency": "必要时",
    }).json()["medication"]
    for _ in range(7):
        client.post(
            f"/api/medications/{prn['id']}/logs",
            json={"taken_at": now.isoformat(timespec="minutes")},
        )

    weekly = client.get(
        "/api/reports/weekly", params={"profile": "母亲", "days": 7}
    ).json()
    assert weekly["medications"]["adherence"] == {
        "expected": 7, "taken": 0, "rate": 0,
    }

    family = client.get("/api/family/overview").json()
    mom = next(item for item in family["profiles"] if item["name"] == "母亲")
    assert mom["medications"]["adherence"] == {
        "expected": 7, "taken": 0, "rate": 0,
    }


def test_family_dossier_includes_unassigned_session_and_reminder(
    client_and_store,
):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "妈妈", "context": {"age": "62"}})
    store.save_exchange("unassigned-session", "未指定咨询", "回答")
    client.post("/api/reminders", json={
        "title": "未指定提醒", "due_at": "2026-11-10",
    })

    response = client.get("/api/health/dossier-family.md", params={"days": 7})
    text = response.text

    assert response.status_code == 200
    assert "# 健康档案：未指定" in text
    assert "未指定提醒" in text
    assert "未指定咨询" in text


def test_family_dossier_accepts_only_unassigned_records(client_and_store):
    client, store = client_and_store
    store.save_exchange("only-unassigned", "只有未指定会诊", "回答")
    client.post("/api/reminders", json={
        "title": "只有未指定提醒", "due_at": "2026-11-10",
    })

    response = client.get("/api/health/dossier-family.md", params={"days": 7})

    assert response.status_code == 200
    assert "# 健康档案：未指定" in response.text


def test_family_dossier_applies_unassigned_metric_target(client_and_store):
    client, _store = client_and_store
    target = client.put("/api/metric-targets", json={
        "profile": "", "metric": "bp", "value1": 135, "value2": 85,
    })
    assert target.status_code == 200
    client.post("/api/metrics", json={
        "profile": "", "metric": "bp", "value1": 150, "value2": 95,
        "measured_at": datetime.now().isoformat(timespec="minutes"),
    })

    response = client.get("/api/health/dossier-family.md", params={"days": 7})
    text = response.text

    assert response.status_code == 200
    assert "# 健康档案：未指定" in text
    assert "≤ 135/85 mmHg" in text
    assert "超标" in text


def test_ics_preserves_timed_repeat_until(client_and_store):
    client, _store = client_and_store
    due = (datetime.now() + timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    until = due + timedelta(days=3)
    client.post("/api/reminders", json={
        "title": "按时复诊",
        "due_at": due.isoformat(timespec="minutes"),
        "repeat_days": 1,
        "repeat_until": until.isoformat(timespec="minutes"),
    })

    text = client.get("/api/reminders/export.ics").content.decode("utf-8")

    assert f"UNTIL={until.strftime('%Y%m%dT%H%M%S')}" in text
    assert "UNTIL=" in text


def test_ics_converts_timezone_bearing_repeat_until(client_and_store):
    client, _store = client_and_store
    due = (datetime.now() + timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    until_local = (due + timedelta(days=2)).replace(hour=10, minute=0)
    until_aware = until_local.astimezone(timezone.utc)
    expected = until_aware.astimezone().replace(tzinfo=None)
    client.post("/api/reminders", json={
        "title": "带时区截止",
        "due_at": due.isoformat(timespec="minutes"),
        "repeat_days": 1,
        "repeat_until": until_aware.isoformat(),
    })

    text = client.get("/api/reminders/export.ics").content.decode("utf-8")

    assert f"UNTIL={expected.strftime('%Y%m%dT%H%M%S')}" in text


def test_ics_omits_rrule_when_next_occurrence_passes_until(client_and_store):
    client, _store = client_and_store
    due = (datetime.now() + timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    until = (due + timedelta(days=1)).replace(hour=8, minute=0)
    client.post("/api/reminders", json={
        "title": "截止前一小时",
        "due_at": due.isoformat(timespec="minutes"),
        "repeat_days": 1,
        "repeat_until": until.isoformat(timespec="minutes"),
    })

    text = client.get("/api/reminders/export.ics").content.decode("utf-8")

    assert "RRULE:" not in text
    assert f"DTSTART:{due.strftime('%Y%m%dT%H%M%S')}" in text


def test_ics_omits_rrule_when_snoozed_past_until(client_and_store):
    client, _store = client_and_store
    due = (datetime.now() + timedelta(days=3)).replace(
        hour=9, minute=0, second=0, microsecond=0
    )
    until = (due + timedelta(days=1)).date().isoformat()
    reminder = client.post("/api/reminders", json={
        "title": "推迟后过期",
        "due_at": due.isoformat(timespec="minutes"),
        "repeat_days": 30,
        "repeat_until": until,
    }).json()["reminder"]
    snoozed = client.post(f"/api/reminders/{reminder['id']}/snooze?days=30")
    assert snoozed.status_code == 200

    text = client.get("/api/reminders/export.ics").content.decode("utf-8")

    assert "RRULE:" not in text
