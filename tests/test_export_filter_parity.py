"""CSV 导出与列表筛选参数的一致性测试。"""
import csv
import io
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


def _date(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def _rows(response):
    return list(csv.reader(io.StringIO(
        response.content.decode("utf-8-sig")
    )))


def test_visit_export_honors_days_and_query(client):
    client.post("/api/visits", json={
        "visit_date": _date(0), "diagnosis": "高血压复诊",
    })
    client.post("/api/visits", json={
        "visit_date": _date(-60), "diagnosis": "感冒",
    })

    response = client.get(
        "/api/visits/export.csv",
        params={"days": 30, "q": "高血压"},
    )
    rows = _rows(response)

    assert response.status_code == 200
    assert len(rows) == 2
    assert "高血压" in rows[1][5]


def test_appointment_export_honors_status(client):
    client.post("/api/appointments", json={
        "title": "待就诊复诊", "scheduled_at": f"{_date(5)}T09:00",
    })
    cancelled = client.post("/api/appointments", json={
        "title": "已取消复诊", "scheduled_at": f"{_date(6)}T09:00",
    }).json()["appointment"]
    client.patch(
        f"/api/appointments/{cancelled['id']}",
        json={"status": "cancelled"},
    )

    response = client.get(
        "/api/appointments/export.csv", params={"status": "cancelled"}
    )
    rows = _rows(response)

    assert response.status_code == 200
    assert len(rows) == 2
    assert rows[1][6] == "已取消"


def test_condition_export_honors_active_only(client):
    active = client.post("/api/conditions", json={
        "name": "原发性高血压", "status": "active",
    }).json()["condition"]
    resolved = client.post("/api/conditions", json={
        "name": "急性咽炎", "status": "resolved",
    }).json()["condition"]

    response = client.get(
        "/api/conditions/export.csv", params={"active_only": "true"}
    )
    rows = _rows(response)

    assert response.status_code == 200
    assert len(rows) == 2
    assert active["name"] in rows[1][0]
    assert resolved["name"] not in rows[1][0]


def test_allergy_export_honors_kind_and_query(client):
    client.post("/api/allergies", json={
        "substance": "青霉素", "kind": "drug",
        "reaction": "皮疹", "severity": "severe",
    })
    client.post("/api/allergies", json={
        "substance": "花生", "kind": "food",
        "reaction": "瘙痒", "severity": "mild",
    })

    response = client.get(
        "/api/allergies/export.csv", params={"kind": "food"}
    )
    rows = _rows(response)

    assert response.status_code == 200
    assert len(rows) == 2
    assert "花生" in rows[1][0]


def test_care_export_honors_overdue(client):
    client.post("/api/care-items", json={
        "title": "眼底检查", "interval_days": 365,
        "next_due_at": _date(-2),
    })
    client.post("/api/care-items", json={
        "title": "血脂复查", "interval_days": 90,
        "next_due_at": _date(20),
    })

    response = client.get(
        "/api/care-items/export.csv", params={"overdue": "true"}
    )
    rows = _rows(response)

    assert response.status_code == 200
    assert len(rows) == 2
    assert "眼底检查" in rows[1][0]
