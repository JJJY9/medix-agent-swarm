"""用药清单 API 的离线测试。"""
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


def test_medications_api_roundtrip(client_and_store):
    client, _store = client_and_store

    created = client.post(
        "/api/medications",
        json={
            "profile": "母亲",
            "name": "氨氯地平",
            "dose": "5 mg",
            "frequency": "每日一次",
            "note": "早餐后",
            "active": True,
        },
    )
    item = created.json()["medication"]
    listed = client.get("/api/medications", params={"profile": "母亲"})
    updated = client.patch(
        f"/api/medications/{item['id']}",
        json={
            "profile": "母亲",
            "name": "氨氯地平",
            "dose": "5 mg",
            "frequency": "每日一次",
            "note": "",
            "active": False,
        },
    )
    active_only = client.get(
        "/api/medications", params={"active_only": True}
    )
    deleted = client.delete(f"/api/medications/{item['id']}")
    missing = client.delete("/api/medications/99999")
    audit = client.get("/api/audit")

    assert created.status_code == 200
    assert item["active"] is True
    assert [row["id"] for row in listed.json()["medications"]] == [item["id"]]
    assert updated.status_code == 200
    assert updated.json()["medication"]["active"] is False
    assert active_only.json()["medications"] == []
    assert deleted.status_code == 200
    assert missing.status_code == 404
    actions = [row["action"] for row in audit.json()["actions"]]
    assert "medication.create" in actions
    assert "medication.update" in actions
    assert "medication.delete" in actions


def test_medications_api_validation(client_and_store):
    client, _store = client_and_store

    blank = client.post(
        "/api/medications", json={"name": " ", "active": True}
    )
    missing_name = client.post("/api/medications", json={"active": True})

    assert blank.status_code == 400
    assert missing_name.status_code == 422


def test_medication_logs_api_roundtrip(client_and_store):
    client, _store = client_and_store
    from datetime import datetime

    item = client.post(
        "/api/medications",
        json={"profile": "父亲", "name": "二甲双胍", "dose": "500 mg"},
    ).json()["medication"]
    taken_at = datetime.now().isoformat(timespec="minutes")

    logged = client.post(
        f"/api/medications/{item['id']}/logs",
        json={"taken_at": taken_at, "note": "早餐后"},
    )
    logs = client.get(f"/api/medications/{item['id']}/logs")
    listed = client.get("/api/medications").json()["medications"][0]
    undone = client.delete(
        f"/api/medication-logs/{logged.json()['log']['id']}"
    )
    missing_log = client.post("/api/medications/99999/logs", json={})
    missing_delete = client.delete("/api/medication-logs/99999")
    audit = client.get("/api/audit")

    assert logged.status_code == 200
    assert logged.json()["log"]["taken_at"] == taken_at
    assert logs.status_code == 200
    assert logs.json()["logs"][0]["medication_name"] == "二甲双胍"
    assert logs.json()["logs"][0]["note"] == "早餐后"
    assert listed["today_count"] == 1
    assert listed["last_taken_at"] == taken_at
    assert undone.status_code == 200
    assert missing_log.status_code == 404
    assert missing_delete.status_code == 404
    actions = [row["action"] for row in audit.json()["actions"]]
    assert "medication.log" in actions
    assert "medication.log_delete" in actions


def test_medications_import_backup(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "medications": [
                {
                    "profile": "孩子",
                    "name": "布洛芬",
                    "dose": "100 mg",
                    "frequency": "必要时",
                    "active": True,
                }
            ]
        },
    )
    listed = client.get("/api/medications")

    assert response.status_code == 200
    assert response.json()["medications"] == 1
    assert listed.json()["medications"][0]["name"] == "布洛芬"


def test_medications_csv_export_and_import(client_and_store):
    client, _store = client_and_store

    client.post(
        "/api/medications",
        json={
            "profile": "母亲",
            "name": "氨氯地平",
            "dose": "5 mg",
            "frequency": "每日一次",
            "note": '=HYPERLINK("http://example.com","open")',
        },
    )
    client.post(
        "/api/medications",
        json={
            "profile": "孩子",
            "name": "布洛芬",
            "dose": "100 mg",
            "frequency": "必要时",
        },
    )

    exported = client.get(
        "/api/medications/export.csv", params={"profile": "母亲"}
    )
    text = exported.content.decode("utf-8-sig")
    assert text.splitlines()[0] == "药物名称,剂量,频次,患者档案,状态,备注"
    assert "氨氯地平" in text
    assert "布洛芬" not in text
    assert "'=HYPERLINK" in text

    lines = [
        "药物名称,剂量,频次,患者档案,状态,备注",
        "二甲双胍,500 mg,每日两次,母亲,在用,餐后",
        "二甲双胍,500 mg,每日两次,母亲,在用,餐后",
        ",10 mg,每日一次,母亲,在用,",
    ]
    content = chr(0xFEFF) + "\n".join(lines)
    first = client.post(
        "/api/medications/import",
        files={"file": ("meds.csv", content.encode("utf-8"), "text/csv")},
    )
    again = client.post(
        "/api/medications/import",
        files={"file": ("meds.csv", content.encode("utf-8"), "text/csv")},
    )
    empty = client.post(
        "/api/medications/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )

    assert first.status_code == 200
    assert first.json()["imported"] == 1
    assert first.json()["skipped"] == 1
    assert first.json()["invalid"] == 1
    assert again.status_code == 200
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 2
    assert empty.status_code == 400
