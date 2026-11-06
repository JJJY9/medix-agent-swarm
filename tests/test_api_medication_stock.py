"""用药库存、有效期与补货提醒的离线测试。"""
import csv
import io
import sqlite3
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


def test_medication_stock_roundtrip(client_and_store):
    client, _store = client_and_store
    expiry = _date(120)

    created = client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "氨氯地平",
        "dose": "5 mg",
        "frequency": "每日一次",
        "stock": 30,
        "stock_unit": "片",
        "low_stock_threshold": 7,
        "expiry_date": expiry,
    })
    assert created.status_code == 200
    item = created.json()["medication"]
    assert item["stock"] == 30
    assert item["stock_unit"] == "片"
    assert item["low_stock_threshold"] == 7
    assert item["expiry_date"] == expiry
    assert item["low_stock"] is False
    assert item["expired"] is False
    assert item["expiring_soon"] is False
    assert item["days_to_expiry"] == 120

    low = client.patch(
        f"/api/medications/{item['id']}", json={"stock": 5}
    ).json()["medication"]
    assert low["low_stock"] is True
    assert low["out_of_stock"] is False

    empty = client.patch(
        f"/api/medications/{item['id']}", json={"stock": 0}
    ).json()["medication"]
    assert empty["low_stock"] is True
    assert empty["out_of_stock"] is True

    expired = client.patch(
        f"/api/medications/{item['id']}", json={"expiry_date": _date(-1)}
    ).json()["medication"]
    assert expired["expired"] is True
    assert expired["days_to_expiry"] == -1

    listed = client.get("/api/medications").json()["medications"]
    assert listed[0]["stock"] == 0
    assert listed[0]["expired"] is True

    cleared = client.patch(
        f"/api/medications/{item['id']}", json={"stock": None}
    ).json()["medication"]
    assert cleared["stock"] is None
    assert cleared["low_stock"] is False


def test_medication_stock_validation(client_and_store):
    client, _store = client_and_store
    negative = client.post("/api/medications", json={
        "name": "氨氯地平", "stock": -1,
    })
    bad_expiry = client.post("/api/medications", json={
        "name": "氨氯地平", "expiry_date": "not-a-date",
    })
    bad_threshold = client.post("/api/medications", json={
        "name": "氨氯地平", "low_stock_threshold": -5,
    })

    assert negative.status_code == 422
    assert bad_threshold.status_code == 422
    assert bad_expiry.status_code == 400
    assert client.get("/api/medications").json()["medications"] == []


def test_medication_log_decrements_and_restores_stock(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/medications", json={
        "name": "二甲双胍", "stock": 10, "stock_unit": "片",
    }).json()["medication"]

    log = client.post(
        f"/api/medications/{item['id']}/logs", json={}
    ).json()["log"]
    assert log["stock_delta"] == -1
    assert log["stock_after"] == 9
    after = client.get("/api/medications").json()["medications"][0]
    assert after["stock"] == 9

    undo = client.delete(f"/api/medication-logs/{log['id']}")
    assert undo.status_code == 200
    restored = client.get("/api/medications").json()["medications"][0]
    assert restored["stock"] == 10


def test_medication_log_at_zero_stock_keeps_zero(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/medications", json={
        "name": "布洛芬", "stock": 0,
    }).json()["medication"]

    log = client.post(
        f"/api/medications/{item['id']}/logs", json={}
    ).json()["log"]
    assert log["stock_delta"] == 0
    assert log["stock_after"] == 0

    client.delete(f"/api/medication-logs/{log['id']}")
    after = client.get("/api/medications").json()["medications"][0]
    assert after["stock"] == 0


def test_manual_stock_reset_clears_log_deltas(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/medications", json={
        "name": "阿托伐他汀", "stock": 10,
    }).json()["medication"]
    log = client.post(
        f"/api/medications/{item['id']}/logs", json={}
    ).json()["log"]

    client.patch(f"/api/medications/{item['id']}", json={"stock": 20})
    client.delete(f"/api/medication-logs/{log['id']}")

    after = client.get("/api/medications").json()["medications"][0]
    assert after["stock"] == 20


def test_medication_stock_reminders(client_and_store):
    client, _store = client_and_store
    expiry = _date(40)
    item = client.post("/api/medications", json={
        "profile": "爸爸",
        "name": "氯吡格雷",
        "stock": 3,
        "low_stock_threshold": 7,
        "expiry_date": expiry,
    }).json()["medication"]

    stock_reminder = client.post(
        f"/api/medications/{item['id']}/reminder",
        params={"kind": "stock"},
    )
    assert stock_reminder.status_code == 200
    assert stock_reminder.json()["created"] is True
    reminder = stock_reminder.json()["reminder"]
    assert reminder["medication_id"] == item["id"]
    assert reminder["title"] == "补货：氯吡格雷"
    assert reminder["due_at"] == _date(0)

    again = client.post(
        f"/api/medications/{item['id']}/reminder",
        params={"kind": "stock"},
    )
    assert again.json()["created"] is False

    expiry_reminder = client.post(
        f"/api/medications/{item['id']}/reminder",
        params={"kind": "expiry"},
    ).json()["reminder"]
    assert expiry_reminder["title"] == "药物到期：氯吡格雷"
    assert expiry_reminder["due_at"] == _date(10)

    no_stock = client.post("/api/medications", json={
        "name": "维生素 D",
    }).json()["medication"]
    assert client.post(
        f"/api/medications/{no_stock['id']}/reminder",
        params={"kind": "stock"},
    ).status_code == 400
    assert client.post(
        f"/api/medications/{no_stock['id']}/reminder",
        params={"kind": "expiry"},
    ).status_code == 400


def test_medication_stock_csv_roundtrip(client_and_store):
    client, _store = client_and_store
    expiry = _date(60)
    client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "氨氯地平",
        "dose": "5 mg",
        "frequency": "每日一次",
        "stock": 12,
        "stock_unit": "片",
        "low_stock_threshold": 5,
        "expiry_date": expiry,
    })
    export = client.get("/api/medications/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][11:] == [
        "剩余库存", "库存单位", "低库存阈值", "有效期", "库存与效期提示",
    ]
    assert rows[1][11] == "12"
    assert rows[1][12] == "片"
    assert rows[1][13] == "5"
    assert rows[1][14] == expiry
    assert rows[1][15] == ""

    medication = client.get("/api/medications").json()["medications"][0]
    client.delete(f"/api/medications/{medication['id']}")
    imported = client.post(
        "/api/medications/import",
        files={"file": ("meds.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    restored = client.get("/api/medications").json()["medications"][0]
    assert restored["stock"] == 12
    assert restored["stock_unit"] == "片"
    assert restored["low_stock_threshold"] == 5
    assert restored["expiry_date"] == expiry


def test_medication_stock_in_family_overview_and_dossier(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/medications", json={
        "profile": "奶奶",
        "name": "阿司匹林",
        "stock": 2,
        "low_stock_threshold": 5,
        "expiry_date": _date(5),
    })

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["medications"]["low_stock"] == 1
    assert grandma["medications"]["expiring"] == 1
    assert grandma["medications"]["expired"] == 0
    assert "阿司匹林" in grandma["medications"]["stock_alerts"]

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-4] == "用药库存"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "库存偏低 1 种" in row[-4]
    assert "即将过期 1 种" in row[-4]

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "库存 2" in dossier.text
    assert "即将过期" in dossier.text


def test_medication_stock_backup_roundtrip(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        item = source.add_medication(
            "妈妈", "氨氯地平", stock=10, stock_unit="片",
            low_stock_threshold=3, expiry_date=_date(90),
        )
        log = source.add_medication_log(item["id"])
        data = source.export_data()
    finally:
        source.close()

    assert data["medications"][0]["stock"] == 9
    assert data["medication_logs"][0]["stock_delta"] == -1

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        result = target.import_data(data)
        medications = target.list_medications()
        logs = target.list_medication_logs()
        target.delete_medication_log(logs[0]["id"])
        after_undo = target.list_medications()[0]
    finally:
        target.close()

    assert result["medications"] == 1
    assert medications[0]["stock"] == 9
    assert medications[0]["stock_unit"] == "片"
    assert medications[0]["low_stock_threshold"] == 3
    assert medications[0]["expiry_date"] == _date(90)
    assert after_undo["stock"] == 10


def test_old_medications_table_is_migrated(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(str(path))
    try:
        conn.execute(
            """
            CREATE TABLE medications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile TEXT NOT NULL DEFAULT '',
                name TEXT NOT NULL,
                dose TEXT NOT NULL DEFAULT '',
                frequency TEXT NOT NULL DEFAULT '',
                note TEXT NOT NULL DEFAULT '',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO medications(
                profile, name, dose, frequency, note, active,
                created_at, updated_at
            ) VALUES ('妈妈', '旧药', '', '', '', 1,
                      '2026-01-01T00:00', '2026-01-01T00:00')
            """
        )
        conn.commit()
    finally:
        conn.close()

    store = SessionStore(str(path))
    try:
        columns = {
            row["name"]
            for row in store._conn.execute(
                "PRAGMA table_info(medications)"
            )
        }
        items = store.list_medications()
    finally:
        store.close()

    assert {"stock", "stock_unit", "low_stock_threshold", "expiry_date"} <= columns
    assert items[0]["name"] == "旧药"
    assert items[0]["stock"] is None
