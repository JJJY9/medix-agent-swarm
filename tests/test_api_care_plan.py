"""复查计划 API、顺延与备份测试。"""
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


def test_care_items_api_roundtrip(client_and_store):
    client, _store = client_and_store

    created = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "糖化血红蛋白",
        "interval_days": 90,
        "last_done_at": _date(-80),
        "note": "空腹",
    })
    assert created.status_code == 200
    item = created.json()["care_item"]
    assert item["next_due_at"] == _date(10)
    assert item["days_until"] == 10
    assert item["overdue"] is False
    assert item["active"] is True

    listed = client.get(
        "/api/care-items", params={"profile": "妈妈"}
    ).json()["care_items"]
    assert [row["id"] for row in listed] == [item["id"]]

    updated = client.patch(
        f"/api/care-items/{item['id']}", json={"interval_days": 120}
    )
    assert updated.status_code == 200
    assert updated.json()["care_item"]["interval_days"] == 120

    deleted = client.delete(f"/api/care-items/{item['id']}")
    missing = client.delete("/api/care-items/99999")
    actions = [
        row["action"] for row in client.get("/api/audit").json()["actions"]
    ]

    assert deleted.status_code == 200
    assert missing.status_code == 404
    assert "care.create" in actions
    assert "care.update" in actions
    assert "care.delete" in actions


def test_care_item_validation(client_and_store):
    client, _store = client_and_store
    blank = client.post("/api/care-items", json={
        "title": "   ", "interval_days": 30,
    })
    bad_interval = client.post("/api/care-items", json={
        "title": "复查", "interval_days": 0,
    })
    bad_date = client.post("/api/care-items", json={
        "title": "复查", "interval_days": 30,
        "next_due_at": "not-a-date",
    })
    due_before = client.post("/api/care-items", json={
        "title": "复查", "interval_days": 30,
        "last_done_at": _date(0), "next_due_at": _date(-1),
    })

    assert blank.status_code == 400
    assert bad_interval.status_code == 422
    assert bad_date.status_code == 400
    assert due_before.status_code == 400
    assert client.get("/api/care-items").json()["care_items"] == []


def test_care_done_reschedules_and_overdue_filter(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "眼底检查",
        "interval_days": 365,
        "last_done_at": _date(-400),
    }).json()["care_item"]

    overdue = client.get(
        "/api/care-items", params={"overdue": True}
    ).json()["care_items"]
    assert [row["id"] for row in overdue] == [item["id"]]

    done = client.post(
        f"/api/care-items/{item['id']}/done", json={}
    )
    assert done.status_code == 200
    updated = done.json()["care_item"]
    assert updated["last_done_at"] == _date(0)
    assert updated["next_due_at"] == _date(365)
    assert updated["overdue"] is False

    overdue_after = client.get(
        "/api/care-items", params={"overdue": True}
    ).json()["care_items"]
    assert overdue_after == []


def test_care_item_reminder_is_idempotent(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "血脂复查",
        "interval_days": 30,
        "next_due_at": _date(10),
    }).json()["care_item"]

    first = client.post(f"/api/care-items/{item['id']}/reminder")
    second = client.post(f"/api/care-items/{item['id']}/reminder")

    assert first.status_code == 200
    assert first.json()["created"] is True
    reminder = first.json()["reminder"]
    assert reminder["title"] == "复查：血脂复查"
    assert reminder["due_at"] == _date(3)
    assert second.json()["created"] is False


def test_care_items_export_csv(client_and_store):
    client, _store = client_and_store
    client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "=复查",
        "interval_days": 30,
        "next_due_at": _date(5),
    })

    export = client.get("/api/care-items/export.csv")
    assert export.status_code == 200
    assert export.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0] == [
        "复查项目", "患者档案", "间隔天数", "上次完成", "下次复查",
        "状态", "备注", "ID",
    ]
    assert rows[1][0] == "'=复查"
    assert rows[1][2] == "30"
    assert rows[1][5] == "启用"


def test_care_items_in_timeline_dossier_and_family(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={"name": "奶奶", "context": {}})
    client.post("/api/care-items", json={
        "profile": "奶奶",
        "title": "肾功能复查",
        "interval_days": 180,
        "next_due_at": _date(3),
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "奶奶", "type": "care"}
    ).json()["events"]
    assert len(timeline) == 1
    assert timeline[0]["type"] == "care"
    assert timeline[0]["title"] == "肾功能复查"
    assert "3 天后" in timeline[0]["value"]
    assert timeline[0]["alert"] is True

    dossier = client.get(
        "/api/health/dossier.md", params={"profile": "奶奶"}
    )
    assert "## 复查计划" in dossier.text
    assert "肾功能复查" in dossier.text

    overview = client.get("/api/family/overview").json()
    grandma = {
        item["name"]: item for item in overview["profiles"]
    }["奶奶"]
    assert grandma["care_plan"]["due_soon"] == 1
    assert grandma["care_plan"]["next"]["title"] == "肾功能复查"

    export = client.get("/api/family/overview/export.csv")
    rows = list(csv.reader(
        io.StringIO(export.content.decode("utf-8-sig"))
    ))
    assert rows[0][-4] == "复查计划"
    row = next(row for row in rows[1:] if row[0] == "奶奶")
    assert "肾功能复查" in row[-4]


def test_care_items_backup_roundtrip_and_profile_rename(
    client_and_store, tmp_path
):
    client, store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "乳腺超声",
        "interval_days": 365,
        "last_done_at": _date(-100),
        "note": "年度体检",
    }).json()["care_item"]

    data = store.export_data()
    assert any(row["id"] == item["id"] for row in data["care_items"])

    target = SessionStore(str(tmp_path / "restored.db"))
    try:
        result = target.import_data(data)
        restored = target.list_care_items()
    finally:
        target.close()

    assert result["care_items"] == 1
    assert restored[0]["title"] == "乳腺超声"
    assert restored[0]["interval_days"] == 365
    assert restored[0]["next_due_at"] == item["next_due_at"]
    assert restored[0]["note"] == "年度体检"

    store.rename_profile("妈妈", "母亲")
    moved = store.list_care_items("母亲")
    assert [row["id"] for row in moved] == [item["id"]]


def test_care_done_completes_previous_reminder(client_and_store):
    client, _store = client_and_store
    item = client.post("/api/care-items", json={
        "profile": "妈妈",
        "title": "眼底检查",
        "interval_days": 365,
        "next_due_at": _date(10),
    }).json()["care_item"]
    old = client.post(
        f"/api/care-items/{item['id']}/reminder"
    ).json()["reminder"]
    assert old["due_at"] == _date(3)

    done = client.post(f"/api/care-items/{item['id']}/done", json={})
    assert done.status_code == 200
    new = done.json()["reminder"]
    assert new["id"] != old["id"]

    reminders = client.get(
        "/api/reminders",
        params={"include_done": True, "profile": "妈妈"},
    ).json()["reminders"]
    by_id = {row["id"]: row for row in reminders}
    assert by_id[old["id"]]["done"] is True
    assert by_id[new["id"]]["done"] is False
    assert [row["id"] for row in reminders if not row["done"]] == [
        new["id"]
    ]
