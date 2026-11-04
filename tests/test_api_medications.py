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


def test_medication_logs_api_list_all(client_and_store):
    client, _store = client_and_store

    mother = client.post(
        "/api/medications", json={"profile": "母亲", "name": "药A"}
    ).json()["medication"]
    child = client.post(
        "/api/medications", json={"profile": "孩子", "name": "药B"}
    ).json()["medication"]
    client.post(
        f"/api/medications/{mother['id']}/logs",
        json={"taken_at": "2026-11-05T08:00"},
    )
    client.post(
        f"/api/medications/{child['id']}/logs",
        json={"taken_at": "2026-11-05T09:00"},
    )

    all_logs = client.get("/api/medication-logs")
    mother_logs = client.get(
        "/api/medication-logs", params={"profile": "母亲"}
    )
    limited = client.get("/api/medication-logs", params={"limit": 1})
    huge_days = client.get(
        "/api/medication-logs", params={"days": 4000000}
    )

    assert all_logs.status_code == 200
    assert [
        row["medication_name"] for row in all_logs.json()["logs"]
    ] == ["药B", "药A"]
    assert [
        row["medication_name"] for row in mother_logs.json()["logs"]
    ] == ["药A"]
    assert len(limited.json()["logs"]) == 1
    assert huge_days.status_code == 200


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
    huge_days = client.get(
        f"/api/medications/{item['id']}/logs", params={"days": 4000000}
    )
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
    assert huge_days.status_code == 200
    assert len(huge_days.json()["logs"]) == 1
    assert listed["today_count"] == 1
    assert listed["week_count"] == 1
    assert listed["last_taken_at"] == taken_at
    assert undone.status_code == 200
    assert missing_log.status_code == 404
    assert missing_delete.status_code == 404
    actions = [row["action"] for row in audit.json()["actions"]]
    assert "medication.log" in actions
    assert "medication.log_delete" in actions


def test_medication_logs_csv_export(client_and_store):
    client, _store = client_and_store
    from datetime import datetime, timedelta

    item = client.post(
        "/api/medications",
        json={"profile": "母亲", "name": "氨氯地平", "dose": "5 mg"},
    ).json()["medication"]
    recent = datetime.now().isoformat(timespec="minutes")
    old = (datetime.now() - timedelta(days=10)).isoformat(timespec="minutes")
    client.post(
        f"/api/medications/{item['id']}/logs",
        json={"taken_at": recent, "note": "早餐后"},
    )
    client.post(
        f"/api/medications/{item['id']}/logs",
        json={"taken_at": old, "note": "旧记录"},
    )

    export = client.get(
        "/api/medications/logs/export.csv", params={"profile": "母亲"}
    )
    recent_export = client.get(
        "/api/medications/logs/export.csv",
        params={"profile": "母亲", "days": 7},
    )

    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    text = export.content.decode("utf-8-sig")
    assert "药物名称" in text
    assert "氨氯地平" in text
    assert "5 mg" in text
    assert "早餐后" in text
    assert recent in text
    assert old in text

    recent_text = recent_export.content.decode("utf-8-sig")
    assert recent in recent_text
    assert old not in recent_text

    clamped = client.get(
        "/api/medications/logs/export.csv",
        params={"profile": "母亲", "days": 4000000},
    )
    assert clamped.status_code == 200
    assert recent in clamped.content.decode("utf-8-sig")


def test_medications_import_backup(client_and_store):
    client, _store = client_and_store
    from datetime import datetime

    taken_at = datetime.now().isoformat(timespec="minutes")
    response = client.post(
        "/api/sessions/import",
        json={
            "medications": [
                {
                    "id": 7,
                    "profile": "孩子",
                    "name": "布洛芬",
                    "dose": "100 mg",
                    "frequency": "必要时",
                    "active": True,
                }
            ],
            "medication_logs": [
                {
                    "medication_id": 7,
                    "profile": "孩子",
                    "medication_name": "布洛芬",
                    "medication_dose": "100 mg",
                    "taken_at": taken_at,
                    "note": "发热",
                }
            ],
        },
    )
    listed = client.get("/api/medications")

    assert response.status_code == 200
    assert response.json()["medications"] == 1
    assert response.json()["medication_logs"] == 1
    medication = listed.json()["medications"][0]
    assert medication["name"] == "布洛芬"
    assert medication["today_count"] == 1
    logs = client.get(
        f"/api/medications/{medication['id']}/logs"
    ).json()["logs"]
    assert len(logs) == 1
    assert logs[0]["note"] == "发热"
    assert logs[0]["medication_id"] == medication["id"]


def test_medications_csv_export_includes_counts(client_and_store):
    client, _store = client_and_store
    import csv
    import io
    from datetime import datetime

    medication = client.post(
        "/api/medications", json={
            "profile": "母亲", "name": "氨氯地平", "dose": "5 mg",
        },
    ).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": datetime.now().isoformat(timespec="minutes")},
    )

    export = client.get("/api/medications/export.csv")
    text = export.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    assert rows[0][-3:] == ["今日次数", "近7天次数", "最近服用"]
    assert rows[1][-3] == "1"
    assert rows[1][-2] == "1"
    assert rows[1][-1] != ""


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
    assert text.splitlines()[0] == (
        "药物名称,剂量,频次,患者档案,状态,备注,"
        "今日次数,近7天次数,最近服用"
    )
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
