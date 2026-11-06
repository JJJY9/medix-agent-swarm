"""疫苗接种记录 API 与存储的离线测试。"""
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


def _future_days(days):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_vaccinations_api_roundtrip(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    due = _future_days(28)

    created = client.post("/api/vaccinations", json={
        "profile": "妈妈",
        "vaccine_name": "流感疫苗",
        "dose_number": 1,
        "vaccinated_at": today,
        "next_due": due,
        "note": "左臂",
    })
    assert created.status_code == 200
    item = created.json()["vaccination"]
    assert item["dose_number"] == 1
    assert item["next_due"] == due

    listed = client.get("/api/vaccinations", params={"profile": "妈妈"})
    assert [row["id"] for row in listed.json()["vaccinations"]] == [item["id"]]

    updated = client.patch(
        f"/api/vaccinations/{item['id']}",
        json={"dose_number": 2, "note": "第二剂（加强）"},
    )
    assert updated.status_code == 200
    updated_item = updated.json()["vaccination"]
    assert updated_item["dose_number"] == 2
    assert updated_item["vaccine_name"] == "流感疫苗"
    assert updated_item["note"] == "第二剂（加强）"

    deleted = client.delete(f"/api/vaccinations/{item['id']}")
    missing = client.delete("/api/vaccinations/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "vaccination.create" in actions
    assert "vaccination.update" in actions
    assert "vaccination.delete" in actions


def test_vaccination_validation(client_and_store):
    client, _store = client_and_store
    today = datetime.now()

    blank = client.post("/api/vaccinations", json={"vaccine_name": "   "})
    bad_dose = client.post("/api/vaccinations", json={
        "vaccine_name": "流感疫苗", "dose_number": 0,
    })
    too_many = client.post("/api/vaccinations", json={
        "vaccine_name": "流感疫苗", "dose_number": 21,
    })
    bad_date = client.post("/api/vaccinations", json={
        "vaccine_name": "流感疫苗", "vaccinated_at": "not-a-date",
    })
    future = client.post("/api/vaccinations", json={
        "vaccine_name": "流感疫苗",
        "vaccinated_at": _future_days(3),
    })
    due_before = client.post("/api/vaccinations", json={
        "vaccine_name": "流感疫苗",
        "vaccinated_at": today.strftime("%Y-%m-%d"),
        "next_due": (today - timedelta(days=1)).strftime("%Y-%m-%d"),
    })

    assert blank.status_code == 400
    assert bad_dose.status_code == 422
    assert too_many.status_code == 422
    assert bad_date.status_code == 400
    assert future.status_code == 400
    assert due_before.status_code == 400
    assert client.get("/api/vaccinations").json()["vaccinations"] == []


def test_vaccination_reminder_is_idempotent(client_and_store):
    client, _store = client_and_store
    due = _future_days(14)

    item = client.post("/api/vaccinations", json={
        "profile": "爸爸",
        "vaccine_name": "乙肝疫苗",
        "dose_number": 2,
        "next_due": due,
    }).json()["vaccination"]

    missing_due = client.post("/api/vaccinations", json={
        "vaccine_name": "无下一剂"
    }).json()["vaccination"]
    no_due = client.post(
        f"/api/vaccinations/{missing_due['id']}/reminder"
    )
    first = client.post(f"/api/vaccinations/{item['id']}/reminder")
    second = client.post(f"/api/vaccinations/{item['id']}/reminder")

    assert no_due.status_code == 400
    assert first.status_code == 200
    assert first.json()["created"] is True
    reminder = first.json()["reminder"]
    assert reminder["vaccination_id"] == item["id"]
    assert reminder["profile"] == "爸爸"
    assert reminder["due_at"] == due
    assert reminder["advance_days"] == 7
    assert "乙肝疫苗" in reminder["title"]
    assert second.status_code == 200
    assert second.json()["created"] is False

    reminders = client.get(
        "/api/reminders", params={"profile": "爸爸"}
    ).json()["reminders"]
    assert [row["id"] for row in reminders] == [reminder["id"]]


def test_vaccinations_export_csv(client_and_store):
    client, _store = client_and_store
    client.post("/api/vaccinations", json={
        "profile": "妈妈",
        "vaccine_name": "=流感疫苗",
        "dose_number": 1,
        "vaccinated_at": "2026-10-01",
        "next_due": "2026-11-01",
        "note": "备注",
    })

    export = client.get("/api/vaccinations/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "接种日期", "患者档案", "疫苗名称", "剂次", "下一剂", "备注", "ID",
    ]
    assert rows[1][0] == "2026-10-01"
    assert rows[1][2] == "'=流感疫苗"
    assert rows[1][3] == "1"


def test_vaccinations_api_backup_roundtrip(client_and_store):
    client, _store = client_and_store
    due = _future_days(21)
    item = client.post("/api/vaccinations", json={
        "profile": "妈妈",
        "vaccine_name": "流感疫苗",
        "dose_number": 1,
        "next_due": due,
    }).json()["vaccination"]

    export = client.get("/api/sessions/export")
    assert export.status_code == 200
    data = export.json()
    assert data["vaccinations"][0]["id"] == item["id"]
    assert data["vaccinations"][0]["next_due"] == due

    imported = client.post("/api/sessions/import", json=data)
    assert imported.status_code == 200
    assert imported.json()["vaccinations"] == 0


def test_vaccinations_in_timeline_and_dossier(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/profiles", json={"name": "妈妈", "context": {}})
    client.post("/api/vaccinations", json={
        "profile": "妈妈",
        "vaccine_name": "流感疫苗",
        "dose_number": 1,
        "vaccinated_at": today,
        "next_due": today,
    })

    timeline = client.get(
        "/api/timeline",
        params={"profile": "妈妈", "type": "vaccination"},
    ).json()
    events = timeline["events"]
    assert len(events) == 1
    assert events[0]["type"] == "vaccination"
    assert events[0]["title"] == "流感疫苗"
    assert "第 1 剂" in events[0]["value"]
    assert events[0]["alert"] is True

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "妈妈"}
    )
    assert dossier.status_code == 200
    assert "## 疫苗接种" in dossier.text
    assert "流感疫苗" in dossier.text


def test_vaccinations_in_family_overview(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    due = _future_days(30)
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/vaccinations", json={
        "profile": "奶奶",
        "vaccine_name": "肺炎疫苗",
        "dose_number": 1,
        "vaccinated_at": today,
        "next_due": due,
    })

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["vaccinations"]["count"] == 1
    assert grandma["vaccinations"]["overdue"] == 0
    assert grandma["vaccinations"]["next_due"] == due
    assert grandma["vaccinations"]["latest"]["vaccine_name"] == "肺炎疫苗"
    assert grandma["recorded_today"] is True

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-7] == "疫苗接种"
    grandma_row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "肺炎疫苗" in grandma_row[-7]
    assert due in grandma_row[-7]


def test_vaccination_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    due = _future_days(10)
    item = client.post("/api/vaccinations", json={
        "profile": "妈妈",
        "vaccine_name": "水痘疫苗",
        "dose_number": 2,
        "next_due": due,
    }).json()["vaccination"]
    reminder = client.post(
        f"/api/vaccinations/{item['id']}/reminder"
    ).json()["reminder"]

    data = store.export_data()
    assert any(
        row["id"] == item["id"] for row in data["vaccinations"]
    )

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_vaccinations()
        reminders = target.list_reminders(True)
    finally:
        target.close()

    assert result["vaccinations"] == 1
    assert len(restored) == 1
    assert restored[0]["vaccine_name"] == "水痘疫苗"
    assert restored[0]["next_due"] == due
    assert reminders[0]["vaccination_id"] == restored[0]["id"]

    store.rename_profile("妈妈", "母亲")
    moved = store.list_vaccinations("母亲")
    assert [row["id"] for row in moved] == [item["id"]]
