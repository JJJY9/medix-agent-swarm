"""症状日记 API 的离线测试。"""
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


def test_symptoms_api_roundtrip(client_and_store):
    client, _store = client_and_store

    created = client.post(
        "/api/symptoms",
        json={
            "profile": "母亲",
            "symptom": "头晕",
            "severity": 6,
            "recorded_at": "2026-11-04T08:00",
            "note": "起床后",
        },
    )
    listed = client.get("/api/symptoms", params={"profile": "母亲"})

    assert created.status_code == 200
    item = created.json()["symptom"]
    assert item["severity"] == 6
    assert item["note"] == "起床后"
    assert [row["id"] for row in listed.json()["symptoms"]] == [item["id"]]

    updated = client.patch(
        f"/api/symptoms/{item['id']}",
        json={
            "profile": "母亲",
            "symptom": "头晕",
            "severity": 4,
            "recorded_at": "2026-11-05T08:00",
            "note": "",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["symptom"]["severity"] == 4

    deleted = client.delete(f"/api/symptoms/{item['id']}")
    missing = client.delete("/api/symptoms/99999")
    audit = client.get("/api/audit")
    actions = [row["action"] for row in audit.json()["actions"]]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "symptom.create" in actions
    assert "symptom.update" in actions
    assert "symptom.delete" in actions


def test_symptoms_api_validation(client_and_store):
    client, _store = client_and_store

    invalid_date = client.post(
        "/api/symptoms",
        json={"symptom": "头晕", "severity": 6, "recorded_at": "bad"},
    )
    invalid_severity = client.post(
        "/api/symptoms", json={"symptom": "头晕", "severity": 11}
    )
    blank = client.post(
        "/api/symptoms", json={"symptom": "   ", "severity": 6}
    )

    assert invalid_date.status_code == 400
    assert invalid_severity.status_code == 422
    assert blank.status_code == 400


def test_symptoms_import_backup(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "symptoms": [
                {
                    "profile": "孩子",
                    "symptom": "咳嗽",
                    "severity": 3,
                    "recorded_at": "2026-11-04T20:00",
                }
            ]
        },
    )
    listed = client.get("/api/symptoms")

    assert response.status_code == 200
    assert response.json()["symptoms"] == 1
    assert listed.json()["symptoms"][0]["symptom"] == "咳嗽"


def test_symptoms_csv_export(client_and_store):
    client, _store = client_and_store

    client.post(
        "/api/symptoms",
        json={
            "profile": "母亲",
            "symptom": "头晕",
            "severity": 6,
            "recorded_at": "2026-11-04T08:00",
            "note": "起床后",
        },
    )
    client.post(
        "/api/symptoms",
        json={
            "profile": "孩子",
            "symptom": "咳嗽",
            "severity": 3,
            "recorded_at": "2026-11-03T20:00",
        },
    )

    response = client.get(
        "/api/symptoms/export.csv", params={"profile": "母亲"}
    )
    text = response.content.decode("utf-8-sig")

    assert response.status_code == 200
    assert text.splitlines()[0] == "记录时间,患者档案,症状,程度,备注"
    assert "头晕" in text
    assert "咳嗽" not in text


def test_symptoms_csv_import(client_and_store):
    client, _store = client_and_store

    lines = [
        "记录时间,患者档案,症状,程度,备注",
        "2026-11-04T08:00,母亲,头晕,6,起床后",
        "2026-11-04T08:00,母亲,头晕,6,起床后",
        "2026-11-03T20:00,孩子,咳嗽,3,",
        "2026-11-03T20:00,,咳嗽,11,",
        "2026-11-04T08:00,母亲,,6,",
        ",母亲,缺时间,5,备注",
    ]
    content = chr(0xFEFF) + "\n".join(lines)
    first = client.post(
        "/api/symptoms/import",
        files={"file": ("symptoms.csv", content.encode("utf-8"), "text/csv")},
    )
    listed = client.get("/api/symptoms")
    again = client.post(
        "/api/symptoms/import",
        files={"file": ("symptoms.csv", content.encode("utf-8"), "text/csv")},
    )
    empty = client.post(
        "/api/symptoms/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )

    assert first.status_code == 200
    assert first.json()["imported"] == 2
    assert first.json()["skipped"] == 1
    assert first.json()["invalid"] == 3
    titles = sorted(item["symptom"] for item in listed.json()["symptoms"])
    assert titles == ["咳嗽", "头晕"]
    assert again.status_code == 200
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 3
    assert empty.status_code == 400


def test_import_backup_accepts_targets_only(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/sessions/import",
        json={
            "metric_targets": [
                {"profile": "妈妈", "metric": "weight", "value1": 70}
            ]
        },
    )
    targets = client.get(
        "/api/metric-targets", params={"profile": "妈妈"}
    )

    assert response.status_code == 200
    assert response.json()["targets"] == 1
    assert targets.json()["targets"][0]["value1"] == 70
