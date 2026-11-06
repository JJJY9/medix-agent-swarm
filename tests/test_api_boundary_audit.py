"""接口边界与审计补齐的回归测试。"""
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


def test_metric_create_and_update_are_audited(client):
    created = client.post(
        "/api/metrics", json={"metric": "weight", "value1": 70}
    )
    metric_id = created.json()["metric"]["id"]
    client.patch(f"/api/metrics/{metric_id}", json={"value1": 71})

    audit = client.get("/api/audit", params={"limit": 10}).json()["actions"]
    actions = [item["action"] for item in audit]

    assert "metric.create" in actions
    assert "metric.update" in actions


def test_profile_save_is_audited(client):
    client.post(
        "/api/profiles",
        json={"name": "张三", "context": {"age": 40}},
    )

    audit = client.get("/api/audit", params={"limit": 10}).json()["actions"]

    assert any(
        item["action"] == "profile.save"
        and item["target"].startswith("profile:")
        for item in audit
    )
    assert all("张三" not in item["target"] for item in audit)


def test_snooze_rejects_completed_reminder(client):
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-11-10"},
    ).json()["reminder"]
    client.post(f"/api/reminders/{created['id']}/complete", json={})

    response = client.post(
        f"/api/reminders/{created['id']}/snooze", params={"days": 7}
    )

    assert response.status_code == 404
    reminders = client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]
    item = next(r for r in reminders if r["id"] == created["id"])
    assert item["due_at"] == "2026-11-10"
    assert item["done"] is True


def test_ics_uses_valid_status_for_completed(client):
    created = client.post(
        "/api/reminders",
        json={"title": "复查", "due_at": "2026-11-10"},
    ).json()["reminder"]
    client.post(f"/api/reminders/{created['id']}/complete", json={})

    ics = client.get(
        "/api/reminders/export.ics", params={"include_done": True}
    ).text

    assert "STATUS:COMPLETED" not in ics
    assert "STATUS:CONFIRMED" in ics
    assert "TRANSP:TRANSPARENT" in ics


def test_csv_import_rejects_oversized_upload(client):
    big = b"a" * (5 * 1024 * 1024 + 10)

    response = client.post(
        "/api/metrics/import",
        files={"file": ("big.csv", big, "text/csv")},
    )

    assert response.status_code == 413


class FakeLLMClient:
    async def chat_with_usage_retry(self, messages, **kwargs):
        return "OK", {}


def test_diagnose_llm_is_rate_limited(client, monkeypatch):
    api_server.diagnose_limiter.clear()
    monkeypatch.setattr(
        "core.llm_client.LLMClient", lambda: FakeLLMClient()
    )

    statuses = [
        client.post("/api/diagnose/llm").status_code
        for _ in range(6)
    ]

    assert statuses[:5] == [200] * 5
    assert statuses[5] == 429
    api_server.diagnose_limiter.clear()
