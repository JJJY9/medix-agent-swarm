"""导出筛选与家庭档案导出的回归测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app)
    finally:
        store.close()


def test_reminder_export_respects_search_overdue_and_date(client):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    client.post("/api/reminders", json={
        "title": "复查血压", "due_at": yesterday,
    })
    client.post("/api/reminders", json={
        "title": "配药", "due_at": tomorrow,
    })

    searched = client.get(
        "/api/reminders/export.csv", params={"q": "血压"}
    ).content.decode("utf-8-sig")
    overdue = client.get(
        "/api/reminders/export.csv", params={"overdue": "true"}
    ).content.decode("utf-8-sig")
    dated = client.get(
        "/api/reminders/export.csv", params={"date": tomorrow}
    ).content.decode("utf-8-sig")

    assert "复查血压" in searched
    assert "配药" not in searched
    assert "复查血压" in overdue
    assert "配药" not in overdue
    assert "配药" in dated
    assert "复查血压" not in dated


def test_medication_export_respects_status_and_search(client):
    client.post("/api/medications", json={
        "name": "氨氯地平", "active": True,
    })
    client.post("/api/medications", json={
        "name": "布洛芬", "active": False,
    })

    inactive = client.get(
        "/api/medications/export.csv", params={"status": "inactive"}
    ).content.decode("utf-8-sig")
    searched = client.get(
        "/api/medications/export.csv", params={"q": "氨氯"}
    ).content.decode("utf-8-sig")

    assert "布洛芬" in inactive
    assert "氨氯地平" not in inactive
    assert "氨氯地平" in searched
    assert "布洛芬" not in searched


def test_family_dossier_includes_unassigned_records(client):
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "metric": "weight",
        "value1": 70,
        "measured_at": f"{today}T08:00",
    })

    response = client.get("/api/health/dossier-family.md")

    assert response.status_code == 200
    text = response.text
    assert "健康档案：未指定" in text
    assert "体重" in text


def test_single_dossier_still_requires_profile(client):
    response = client.get("/api/health/dossier.md")

    assert response.status_code == 400
