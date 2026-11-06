"""用药开始/停药日期与原因的离线测试。"""
import csv
import io
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


def test_medication_dates_roundtrip(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "氨氯地平",
        "started_at": "2025-01-01",
        "stopped_at": "2025-06-01",
        "stop_reason": "医生调整方案",
    })
    assert created.status_code == 200
    item = created.json()["medication"]
    assert item["started_at"] == "2025-01-01"
    assert item["stopped_at"] == "2025-06-01"
    assert item["stop_reason"] == "医生调整方案"

    updated = client.patch(
        f"/api/medications/{item['id']}",
        json={"stopped_at": "", "stop_reason": ""},
    )
    assert updated.status_code == 200
    updated_item = updated.json()["medication"]
    assert updated_item["started_at"] == "2025-01-01"
    assert updated_item["stopped_at"] == ""
    assert updated_item["stop_reason"] == ""


def test_medication_dates_validation(client_and_store):
    client, _store = client_and_store
    bad_order = client.post("/api/medications", json={
        "name": "氨氯地平",
        "started_at": _date(0),
        "stopped_at": _date(-5),
    })
    future_start = client.post("/api/medications", json={
        "name": "氨氯地平",
        "started_at": _date(2),
    })
    bad_date = client.post("/api/medications", json={
        "name": "氨氯地平",
        "started_at": "not-a-date",
    })

    assert bad_order.status_code == 400
    assert future_start.status_code == 400
    assert bad_date.status_code == 400
    assert client.get("/api/medications").json()["medications"] == []


def test_medication_dates_csv_roundtrip(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "二甲双胍",
        "dose": "500 mg",
        "frequency": "每日两次",
        "started_at": "2025-02-01",
        "stopped_at": "2025-08-01",
        "stop_reason": "换用缓释片",
    })

    export = client.get("/api/medications/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-3:] == ["开始日期", "停药日期", "停药原因"]
    assert rows[1][-3:] == ["2025-02-01", "2025-08-01", "换用缓释片"]

    item = client.get("/api/medications").json()["medications"][0]
    client.delete(f"/api/medications/{item['id']}")
    imported = client.post(
        "/api/medications/import",
        files={"file": ("meds.csv", export.content, "text/csv")},
    )
    assert imported.status_code == 200
    assert imported.json()["imported"] == 1
    restored = client.get("/api/medications").json()["medications"][0]
    assert restored["started_at"] == "2025-02-01"
    assert restored["stopped_at"] == "2025-08-01"
    assert restored["stop_reason"] == "换用缓释片"


def test_medication_dates_in_dossier_and_backup(
    client_and_store, tmp_path
):
    client, store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    item = client.post("/api/medications", json={
        "profile": "奶奶",
        "name": "阿托伐他汀",
        "started_at": "2024-01-01",
        "stopped_at": "2024-12-01",
        "stop_reason": "肝功能异常",
    }).json()["medication"]

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "开始 2024-01-01" in dossier.text
    assert "停药 2024-12-01" in dossier.text
    assert "肝功能异常" in dossier.text

    data = store.export_data()
    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_medications()
    finally:
        target.close()

    assert result["medications"] == 1
    assert restored[0]["started_at"] == "2024-01-01"
    assert restored[0]["stopped_at"] == "2024-12-01"
    assert restored[0]["stop_reason"] == "肝功能异常"
    assert restored[0]["id"] == item["id"]
