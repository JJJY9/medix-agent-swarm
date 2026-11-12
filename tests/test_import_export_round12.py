"""导入/导出闭环：化验分组、提醒恢复与关联、导出可恢复性。"""
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


def test_lab_import_keeps_same_value_different_panel(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    records = [
        {
            "profile": "妈妈", "panel": "生化全套", "item": "血糖",
            "value": 5.6, "unit": "mmol/L",
            "tested_at": "2026-11-01T08:00", "note": "",
        },
        {
            "profile": "妈妈", "panel": "空腹血糖", "item": "血糖",
            "value": 5.6, "unit": "mmol/L",
            "tested_at": "2026-11-01T08:00", "note": "",
        },
    ]
    try:
        first = store.import_data({"lab_results": records})
        rows = store.list_lab_results("妈妈")
        second = store.import_data({"lab_results": records})
    finally:
        store.close()

    # 同一时刻不同分组的化验必须各自保留，重复导入仍幂等
    assert first["lab_results"] == 2
    assert len(rows) == 2
    assert second["lab_results"] == 0


def test_reminder_restore_after_deleting_same_batch(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    payload = {
        "reminders": [
            {
                "title": "每月复查", "due_at": f"2026-12-{day:02d}",
                "profile": "妈妈", "note": "同批",
                "created_at": "2026-11-01T08:00:00",
            }
            for day in (1, 8, 15)
        ],
    }
    try:
        assert store.import_data(payload)["reminders"] == 3
        rows = store.list_reminders(True, "妈妈")
        # 删除同一批创建的两条提醒
        store.delete_reminder(rows[0]["id"])
        store.delete_reminder(rows[1]["id"])
        restored = store.import_data(payload)
        final = store.list_reminders(True, "妈妈")
        again = store.import_data(payload)
    finally:
        store.close()

    # 删除的同批提醒应能恢复，重复导入仍然幂等
    assert restored["reminders"] == 2
    assert len(final) == 3
    assert again["reminders"] == 0


def test_reminder_csv_keeps_vaccination_and_appointment_links(
    client_and_store,
):
    client, _store = client_and_store
    vaccination = client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "乙肝疫苗",
        "vaccinated_at": _date(-10), "next_due": _date(10),
    }).json()["vaccination"]
    appointment = client.post("/api/appointments", json={
        "profile": "妈妈", "title": "复诊",
        "scheduled_at": f"{_date(5)}T09:00",
    }).json()["appointment"]
    reminder = client.post("/api/reminders", json={
        "title": "复查提醒",
        "due_at": _date(3),
        "profile": "妈妈",
        "vaccination_id": vaccination["id"],
        "appointment_id": appointment["id"],
    }).json()["reminder"]

    exported = client.get("/api/reminders/export.csv")
    client.delete(f"/api/reminders/{reminder['id']}")
    imported = client.post(
        "/api/reminders/import",
        files={"file": ("reminders.csv", exported.content, "text/csv")},
    )

    assert imported.status_code == 200
    rows = client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]
    restored = next(
        row for row in rows if row["title"] == "复查提醒"
    )
    assert restored["vaccination_id"] == vaccination["id"]
    assert restored["appointment_id"] == appointment["id"]


def test_export_rejects_unrestorable_payload(tmp_path, monkeypatch):
    import memory.session_store as store_module

    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        store.save_exchange("s1", "问", "答")
        store.save_exchange("s1", "再问", "再答")
        monkeypatch.setattr(
            store_module, "MAX_BACKUP_SESSION_MESSAGES", 1
        )
        with pytest.raises(ValueError):
            store.export_data()
    finally:
        store.close()
