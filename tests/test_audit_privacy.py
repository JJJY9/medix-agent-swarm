"""审计日志隐私回归：健康记录与档案文本不进入 target/detail。"""
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


def _audit_text(client):
    data = client.get("/api/audit", params={"limit": 500}).json()
    return " ".join(
        f"{item.get('target', '')} {item.get('detail', '')}"
        for item in data["actions"]
    )


def test_new_health_audits_do_not_include_patient_text(client_and_store):
    client, _store = client_and_store
    symptom = client.post(
        "/api/symptoms",
        json={
            "profile": "妈妈", "symptom": "偏头痛", "severity": 5,
            "recorded_at": "2026-11-01T09:00:00",
        },
    ).json()["symptom"]
    medication = client.post(
        "/api/medications",
        json={"profile": "妈妈", "name": "氨氯地平"},
    ).json()["medication"]
    reminder = client.post(
        "/api/reminders",
        json={
            "profile": "妈妈", "title": "复查血压", "due_at": "2026-12-01",
        },
    ).json()["reminder"]
    assert client.post(
        "/api/profiles", json={"name": "妈妈", "context": {"age": 60}}
    ).status_code == 200

    text = _audit_text(client)

    assert "偏头痛" not in text
    assert "氨氯地平" not in text
    assert "复查血压" not in text
    assert "妈妈" not in text
    # id 仍可用于关联
    assert str(symptom["id"]) in text
    assert str(medication["id"]) in text
    assert str(reminder["id"]) in text


def test_deleting_symptom_scrubs_old_audit_target(client_and_store):
    client, store = client_and_store
    symptom = client.post(
        "/api/symptoms",
        json={
            "profile": "妈妈", "symptom": "偏头痛", "severity": 5,
            "recorded_at": "2026-11-01T09:00:00",
        },
    ).json()["symptom"]
    # 模拟旧版本写入的 target
    store.log_action(
        "symptom.update", f"{symptom['id']}·偏头痛", ""
    )

    assert client.delete(f"/api/symptoms/{symptom['id']}").status_code == 200

    assert "偏头痛" not in _audit_text(client)


def test_deleting_profile_scrubs_old_name(client_and_store):
    client, store = client_and_store
    assert client.post(
        "/api/profiles", json={"name": "妈妈", "context": {"age": 60}}
    ).status_code == 200
    store.log_action("profile.save", "妈妈", "")
    store.log_action("profile.rename", "妈妈", "母亲")

    assert client.delete("/api/profiles/妈妈").status_code == 200

    text = _audit_text(client)
    assert "妈妈" not in text
    assert "母亲" not in text
    assert "profile:" in text


def test_backup_export_does_not_contain_scrubbed_patient_text(
    client_and_store,
):
    client, store = client_and_store
    symptom = client.post(
        "/api/symptoms",
        json={
            "profile": "妈妈", "symptom": "偏头痛", "severity": 5,
            "recorded_at": "2026-11-01T09:00:00",
        },
    ).json()["symptom"]
    store.log_action("symptom.delete", f"{symptom['id']}·偏头痛", "")
    client.delete(f"/api/symptoms/{symptom['id']}")

    payload = store.export_data()
    text = str(payload)
    assert "偏头痛" not in text
