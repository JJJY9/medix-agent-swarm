"""用药变更历史 API 与备份往返测试。"""
from datetime import datetime, timedelta

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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _create_medication(client):
    return client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "氨氯地平",
        "dose": "5 mg",
        "frequency": "每日一次",
    }).json()["medication"]


def test_create_records_history(client_and_store):
    client, _store = client_and_store
    medication = _create_medication(client)

    response = client.get(f"/api/medications/{medication['id']}/history")

    assert response.status_code == 200
    history = response.json()["history"]
    assert len(history) == 1
    assert history[0]["action"] == "create"
    assert "氨氯地平" in history[0]["detail"]
    assert "5 mg" in history[0]["detail"]
    assert history[0]["profile"] == "妈妈"


def test_dose_and_frequency_changes_are_recorded(client_and_store):
    client, _store = client_and_store
    medication = _create_medication(client)

    client.patch(
        f"/api/medications/{medication['id']}",
        json={"dose": "10 mg"},
    )
    client.patch(
        f"/api/medications/{medication['id']}",
        json={"frequency": "每日两次"},
    )

    history = client.get(
        f"/api/medications/{medication['id']}/history"
    ).json()["history"]
    assert [row["action"] for row in history] == [
        "update", "update", "create",
    ]
    assert "剂量 5 mg → 10 mg" in history[1]["detail"]
    assert "频次 每日一次 → 每日两次" in history[0]["detail"]


def test_stop_and_restart_are_recorded(client_and_store):
    client, _store = client_and_store
    medication = _create_medication(client)

    stopped = client.patch(
        f"/api/medications/{medication['id']}",
        json={"active": False, "stop_reason": "出现水肿"},
    )
    assert stopped.status_code == 200
    client.patch(
        f"/api/medications/{medication['id']}",
        json={"active": True, "started_at": _date(0)},
    )

    history = client.get(
        f"/api/medications/{medication['id']}/history"
    ).json()["history"]
    assert history[1]["action"] == "stop"
    assert "停药：出现水肿" in history[1]["detail"]
    assert history[0]["action"] == "start"
    assert "重新启用" in history[0]["detail"]


def test_unchanged_update_adds_no_history(client_and_store):
    client, _store = client_and_store
    medication = _create_medication(client)

    client.patch(
        f"/api/medications/{medication['id']}",
        json={"dose": "5 mg"},
    )

    history = client.get(
        f"/api/medications/{medication['id']}/history"
    ).json()["history"]
    assert len(history) == 1


def test_history_missing_medication(client_and_store):
    client, _store = client_and_store
    assert client.get("/api/medications/99999/history").status_code == 404


def test_delete_removes_history(client_and_store):
    client, store = client_and_store
    medication = _create_medication(client)
    client.delete(f"/api/medications/{medication['id']}")

    assert client.get(
        f"/api/medications/{medication['id']}/history"
    ).status_code == 404
    assert store.list_medication_history() == []


def test_history_backup_roundtrip(client_and_store, tmp_path):
    client, store = client_and_store
    medication = _create_medication(client)
    client.patch(
        f"/api/medications/{medication['id']}",
        json={"dose": "10 mg"},
    )

    data = store.export_data()
    assert len(data["medication_history"]) == 2

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        target.add_medication("", "占位药")
        result = target.import_data(data)
        restored_medications = [
            row for row in target.list_medications()
            if row["name"] == "氨氯地平"
        ]
        restored_id = restored_medications[0]["id"]
        restored_history = target.list_medication_history(restored_id)
    finally:
        target.close()

    assert result["medication_history"] == 2
    assert len(restored_medications) == 1
    assert restored_id != medication["id"]
    assert {row["medication_id"] for row in restored_history} == {
        restored_id
    }
    details = "；".join(row["detail"] for row in restored_history)
    assert "创建" in details
    assert "剂量 5 mg → 10 mg" in details


def test_old_backup_without_history_still_imports(
    client_and_store, tmp_path
):
    client, store = client_and_store
    _create_medication(client)
    data = store.export_data()
    data.pop("medication_history")
    data["checksum"] = None

    target = SessionStore(str(tmp_path / "old.db"))
    try:
        result = target.import_data(data)
    finally:
        target.close()

    assert result["medications"] == 1
    assert result.get("medication_history", 0) == 0
