"""就诊记录 API、时间线与备份测试。"""
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


def test_visit_can_link_to_appointment(client_and_store):
    client, _store = client_and_store
    appointment = client.post("/api/appointments", json={
        "profile": "妈妈",
        "title": "心内科复诊",
        "department": "心内科",
        "scheduled_at": f"{_date(0)}T09:00",
        "status": "completed",
    }).json()["appointment"]
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "appointment_id": appointment["id"],
        "visit_date": _date(0),
        "department": "心内科",
        "diagnosis": "高血压复诊",
    }).json()["visit"]

    assert visit["appointment_id"] == appointment["id"]
    listed = client.get("/api/visits").json()["visits"]
    assert listed[0]["appointment_id"] == appointment["id"]


def test_visits_api_roundtrip(client_and_store):
    client, _store = client_and_store
    created = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": _date(0),
        "department": "心内科",
        "doctor": "王医生",
        "facility": "市医院",
        "diagnosis": "原发性高血压 2 级",
        "advice": "低盐饮食，监测血压",
        "prescription": "氨氯地平 5 mg 每日一次",
        "next_followup_date": _date(30),
        "note": "带化验单",
    })
    assert created.status_code == 200
    item = created.json()["visit"]
    assert item["diagnosis"] == "原发性高血压 2 级"
    assert item["followup_days_until"] == 30
    assert item["followup_overdue"] is False

    listed = client.get(
        "/api/visits", params={"profile": "妈妈"}
    ).json()["visits"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/visits/{item['id']}", json={"diagnosis": "高血压 2 级"}
    )
    assert updated.status_code == 200
    assert updated.json()["visit"]["diagnosis"] == "高血压 2 级"

    deleted = client.delete(f"/api/visits/{item['id']}")
    missing = client.delete("/api/visits/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "visit.create" in actions
    assert "visit.update" in actions
    assert "visit.delete" in actions


def test_visit_validation(client_and_store):
    client, _store = client_and_store
    future = client.post("/api/visits", json={
        "visit_date": _date(2), "diagnosis": "复诊",
    })
    bad_date = client.post("/api/visits", json={
        "visit_date": "not-a-date", "diagnosis": "复诊",
    })
    bad_followup = client.post("/api/visits", json={
        "visit_date": _date(0),
        "diagnosis": "复诊",
        "next_followup_date": _date(-1),
    })

    assert future.status_code == 400
    assert bad_date.status_code == 400
    assert bad_followup.status_code == 400
    assert client.get("/api/visits").json()["visits"] == []


def test_visit_filters_and_search(client_and_store):
    client, _store = client_and_store
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(-2),
        "diagnosis": "高血压", "doctor": "王医生",
    })
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(-20),
        "diagnosis": "糖尿病", "advice": "控制饮食",
    })
    client.post("/api/visits", json={
        "profile": "爸爸", "visit_date": _date(-1),
        "diagnosis": "高血压",
    })

    recent = client.get(
        "/api/visits", params={"profile": "妈妈", "days": 7}
    ).json()["visits"]
    search = client.get(
        "/api/visits", params={"q": "糖尿病"}
    ).json()["visits"]

    assert [row["diagnosis"] for row in recent] == ["高血压"]
    assert [row["diagnosis"] for row in search] == ["糖尿病"]


def test_visits_export_csv(client_and_store):
    client, _store = client_and_store
    client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": _date(0),
        "diagnosis": "=高血压",
        "department": "心内科",
        "next_followup_date": _date(30),
    })

    export = client.get("/api/visits/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "就诊日期", "患者档案", "科室", "医生", "机构", "诊断",
        "医嘱", "处方", "下次复诊", "备注", "ID",
    ]
    assert rows[1][5] == "'=高血压"
    assert rows[1][8] == _date(30)


def test_visits_in_timeline_dossier_and_family(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/visits", json={
        "profile": "奶奶",
        "visit_date": _date(0),
        "department": "眼科",
        "doctor": "李医生",
        "diagnosis": "白内障术后复查",
        "advice": "继续滴眼药水",
        "next_followup_date": _date(14),
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "visit"}
    ).json()["events"]
    assert len(timeline) == 1
    assert timeline[0]["type"] == "visit"
    assert timeline[0]["title"] == "白内障术后复查"
    assert "眼科" in timeline[0]["value"]
    assert "下次复诊" in timeline[0]["value"]

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 近期就诊" in dossier.text
    assert "白内障术后复查" in dossier.text
    assert "继续滴眼药水" in dossier.text

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["visits"]["count"] == 1
    assert grandma["visits"]["latest"]["diagnosis"] == "白内障术后复查"

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-2] == "最近就诊"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "白内障术后复查" in row[-2]


def test_visits_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    item = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": _date(0),
        "diagnosis": "胃炎",
        "advice": "清淡饮食",
        "next_followup_date": _date(7),
    }).json()["visit"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["visit_records"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_visit_records()
    finally:
        target.close()

    assert result["visit_records"] == 1
    assert restored[0]["diagnosis"] == "胃炎"
    assert restored[0]["advice"] == "清淡饮食"
    assert restored[0]["next_followup_date"] == _date(7)

    store.rename_profile("妈妈", "母亲")
    moved = store.list_visit_records("母亲")
    assert [row["id"] for row in moved] == [item["id"]]
