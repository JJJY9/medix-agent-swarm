"""会话标签不得以原文进入审计日志。"""
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
    rows = client.get("/api/audit").json()["actions"]
    return " ".join(
        f"{row.get('action', '')} {row.get('target', '')} "
        f"{row.get('detail', '')}"
        for row in rows
    )


def test_session_tags_are_not_written_to_audit(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "最近心情很差", "建议就诊")

    response = client.patch(
        "/api/sessions/s1/tags", json={"tags": ["化疗", "抑郁"]}
    )
    assert response.status_code == 200
    assert response.json()["tags"] == ["化疗", "抑郁"]

    text = _audit_text(client)
    assert "化疗" not in text
    assert "抑郁" not in text
    actions = client.get("/api/audit").json()["actions"]
    tags_rows = [a for a in actions if a["action"] == "session.tags"]
    assert tags_rows and tags_rows[0]["detail"] == "count=2"


def test_replacing_tags_redacts_historical_tag_audit(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")

    client.patch("/api/sessions/s1/tags", json={"tags": ["化疗"]})
    client.patch("/api/sessions/s1/tags", json={"tags": ["随访"]})

    text = _audit_text(client)
    assert "化疗" not in text
    actions = client.get("/api/audit").json()["actions"]
    tags_rows = [a for a in actions if a["action"] == "session.tags"]
    assert any(row["detail"] == "tags=redacted" for row in tags_rows)
    assert any(row["detail"] == "count=1" for row in tags_rows)


def test_session_export_does_not_leak_tag_text(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")

    client.patch(
        "/api/sessions/s1/tags", json={"tags": ["HIV", "化疗"]}
    )
    exported = client.get("/api/sessions/export").json()
    audit_text = " ".join(
        f"{row.get('action', '')} {row.get('target', '')} "
        f"{row.get('detail', '')}"
        for row in exported.get("audit_log", [])
    )

    assert "HIV" not in audit_text
    assert "化疗" not in audit_text


def test_bulk_tag_audit_redacts_previous_rows(client_and_store):
    client, store = client_and_store
    store.save_exchange("s1", "问题", "回答")
    store.log_action("session.bulk_tags", "", "tags=HIV")

    response = client.patch(
        "/api/bulk/sessions/tags",
        json={"ids": ["s1"], "tags": ["化疗"]},
    )
    assert response.status_code == 200

    text = _audit_text(client)
    assert "HIV" not in text
    assert "化疗" not in text
    actions = client.get("/api/audit").json()["actions"]
    bulk_rows = [
        a for a in actions if a["action"] == "session.bulk_tags"
    ]
    assert any(row["detail"] == "tags=redacted" for row in bulk_rows)
    assert bulk_rows[0]["detail"].startswith("updated=1")
    assert "count=1" in bulk_rows[0]["detail"]
