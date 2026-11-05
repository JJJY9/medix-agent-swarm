"""每周健康小结 API 的离线测试。"""
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


def test_weekly_report_aggregates(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    recent = now.isoformat(timespec="minutes")
    older = (now - timedelta(days=10)).isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "bp",
        "value1": 120, "value2": 80, "measured_at": recent,
    })
    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "bp",
        "value1": 140, "value2": 90, "measured_at": recent,
    })
    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "weight",
        "value1": 60, "measured_at": recent,
    })
    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "weight",
        "value1": 80, "measured_at": older,
    })

    client.post("/api/symptoms", json={
        "profile": "母亲", "symptom": "头晕", "severity": 4,
        "recorded_at": recent,
    })
    client.post("/api/symptoms", json={
        "profile": "母亲", "symptom": "头晕", "severity": 6,
        "recorded_at": recent,
    })
    client.post("/api/symptoms", json={
        "profile": "母亲", "symptom": "乏力", "severity": 3,
        "recorded_at": recent,
    })
    client.post("/api/symptoms", json={
        "profile": "母亲", "symptom": "乏力", "severity": 2,
        "recorded_at": older,
    })

    medication = client.post("/api/medications", json={
        "profile": "母亲", "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": recent},
    )
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": older},
    )

    client.post("/api/reminders", json={
        "profile": "母亲", "title": "复查",
        "due_at": (now + timedelta(days=2)).strftime("%Y-%m-%d"),
    })
    client.post("/api/reminders", json={
        "profile": "母亲", "title": "逾期复查",
        "due_at": (now - timedelta(days=2)).strftime("%Y-%m-%d"),
    })

    report = client.get("/api/reports/weekly", params={"profile": "母亲"})
    data = report.json()

    assert report.status_code == 200
    assert data["days"] == 7
    assert data["start"] <= data["end"]
    assert data["metrics"]["count"] == 3
    by_metric = {row["metric"]: row for row in data["metrics"]["by_metric"]}
    assert by_metric["bp"]["count"] == 2
    assert by_metric["bp"]["avg_value1"] == 130.0
    assert by_metric["bp"]["avg_value2"] == 85.0
    assert by_metric["weight"]["count"] == 1
    assert by_metric["weight"]["avg_value1"] == 60.0
    assert by_metric["weight"]["prev_avg_value1"] == 80.0
    assert by_metric["bp"]["prev_avg_value1"] is None
    assert data["symptoms"]["count"] == 3
    assert data["symptoms"]["avg_severity"] == 4.3
    assert data["symptoms"]["max_severity"] == 6
    assert data["symptoms"]["top"][0] == {"symptom": "头晕", "count": 2}
    assert data["symptoms"]["prev_count"] == 1
    assert data["symptoms"]["prev_avg_severity"] == 2.0
    assert data["medications"]["active"] == 1
    assert data["medications"]["doses"] == 1
    assert data["medications"]["prev_doses"] == 1
    assert data["medications"]["by_medication"][0]["name"] == "氨氯地平"
    assert data["medications"]["by_medication"][0]["dose"] == "5 mg"
    assert data["reminders"]["pending"] == 2
    assert data["reminders"]["overdue"] == 1
    assert data["reminders"]["due_in_7_days"] == 1

    client.put("/api/metric-targets", json={
        "profile": "母亲", "metric": "bp",
        "value1": 130, "value2": 85,
    })
    client.put("/api/metric-targets", json={
        "profile": "母亲", "metric": "weight", "value1": 65,
    })
    targeted = client.get(
        "/api/reports/weekly", params={"profile": "母亲"}
    ).json()
    by_metric = {
        row["metric"]: row for row in targeted["metrics"]["by_metric"]
    }
    assert by_metric["bp"]["within_target"] == 1
    assert by_metric["bp"]["target"]["value1"] == 130
    assert by_metric["bp"]["target"]["value2"] == 85
    assert by_metric["weight"]["within_target"] == 1

    client.post("/api/metrics", json={
        "profile": "父亲", "metric": "weight",
        "value1": 70, "measured_at": recent,
    })
    family = client.get("/api/reports/weekly").json()
    assert family["metrics"]["count"] == 4
    family_weight = next(
        row for row in family["metrics"]["by_metric"]
        if row["metric"] == "weight"
    )
    assert family_weight["within_target"] is None
    scoped = client.get(
        "/api/reports/weekly", params={"profile": "母亲"}
    ).json()
    assert scoped["metrics"]["count"] == 3


def test_weekly_report_days_clamped(client_and_store):
    client, _store = client_and_store

    huge = client.get("/api/reports/weekly", params={"days": 100000})
    small = client.get("/api/reports/weekly", params={"days": 0})

    assert huge.status_code == 200
    assert huge.json()["days"] == 90
    assert small.status_code == 200
    assert small.json()["days"] == 7


def test_weekly_report_empty_profile_scopes_reminders(client_and_store):
    client, _store = client_and_store

    client.post("/api/reminders", json={
        "title": "未指定档案提醒", "due_at": "2026-11-06",
    })
    client.post("/api/reminders", json={
        "profile": "母亲", "title": "妈妈提醒", "due_at": "2026-11-06",
    })

    scoped = client.get(
        "/api/reports/weekly", params={"profile": ""}
    ).json()
    family = client.get("/api/reports/weekly").json()

    assert scoped["reminders"]["pending"] == 1
    assert family["reminders"]["pending"] == 2


def test_weekly_report_uses_calendar_window(client_and_store):
    client, _store = client_and_store
    from datetime import time

    now = datetime.now()
    in_window = (now - timedelta(days=6)).isoformat(timespec="minutes")
    old_date = (now - timedelta(days=7)).date()
    out_window = datetime.combine(
        old_date, time(23, 59)
    ).isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "metric": "weight", "value1": 60, "measured_at": in_window,
    })
    client.post("/api/metrics", json={
        "metric": "weight", "value1": 70, "measured_at": out_window,
    })

    report = client.get("/api/reports/weekly", params={"days": 7}).json()

    assert report["metrics"]["count"] == 1
    assert report["metrics"]["by_metric"][0]["avg_value1"] == 60.0


def test_weekly_report_lower_bound_target(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "spo2", "value1": 95,
        "measured_at": now,
    })
    client.post("/api/metrics", json={
        "profile": "母亲", "metric": "spo2", "value1": 90,
        "measured_at": now,
    })
    client.put("/api/metric-targets", json={
        "profile": "母亲", "metric": "spo2", "value1": 95,
    })

    report = client.get(
        "/api/reports/weekly", params={"profile": "母亲"}
    ).json()
    row = next(
        item for item in report["metrics"]["by_metric"]
        if item["metric"] == "spo2"
    )

    assert row["within_target"] == 1
    assert row["target"]["value1"] == 95


def test_weekly_report_logs_calendar_window(client_and_store):
    client, _store = client_and_store
    from datetime import time

    now = datetime.now()
    medication = client.post(
        "/api/medications", json={"name": "二甲双胍"}
    ).json()["medication"]
    in_window = (now - timedelta(days=6)).isoformat(timespec="minutes")
    old_date = (now - timedelta(days=7)).date()
    out_window = datetime.combine(
        old_date, time(23, 59)
    ).isoformat(timespec="minutes")

    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": in_window},
    )
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": out_window},
    )

    report = client.get("/api/reports/weekly").json()
    assert report["medications"]["doses"] == 1


def test_weekly_report_counts_consultations(client_and_store):
    client, store = client_and_store
    now = datetime.now()
    previous = (now - timedelta(days=10)).isoformat(timespec="seconds")

    store.save_exchange("weekly-recent", "最近头晕怎么办", "建议测血压。")
    store.set_session_profile("weekly-recent", "母亲")
    store.save_exchange("weekly-previous", "孩子的疫苗怎么打", "按接种程序。")
    store.set_session_profile("weekly-previous", "母亲")
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE sessions SET created_at = ? WHERE id = ?",
            (previous, "weekly-previous"),
        )

    report = client.get(
        "/api/reports/weekly", params={"profile": "母亲"}
    ).json()
    assert report["consultations"] == {"count": 1, "prev_count": 1}

    other = client.get(
        "/api/reports/weekly", params={"profile": "父亲"}
    ).json()
    assert other["consultations"] == {"count": 0, "prev_count": 0}

    family = client.get("/api/reports/weekly").json()
    assert family["consultations"] == {"count": 1, "prev_count": 1}


def test_weekly_report_medication_adherence(client_and_store):
    client, store = client_and_store
    now = datetime.now()

    medication = client.post("/api/medications", json={
        "profile": "母亲", "name": "二甲双胍", "frequency": "每日两次",
    }).json()["medication"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ? WHERE id = ?",
            (
                (now - timedelta(days=6)).isoformat(timespec="seconds"),
                medication["id"],
            ),
        )

    taken_at = now.isoformat(timespec="minutes")
    for _ in range(8):
        client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"taken_at": taken_at},
        )

    report = client.get("/api/reports/weekly").json()
    medications = report["medications"]

    assert medications["adherence"] == {
        "expected": 14, "taken": 8, "rate": 57,
    }
    row = medications["by_medication"][0]
    assert row["expected"] == 14
    assert row["rate"] == 57


def test_weekly_report_adherence_rounds_half_up(client_and_store):
    client, store = client_and_store
    now = datetime.now()

    medication = client.post("/api/medications", json={
        "profile": "母亲", "name": "氨氯地平", "frequency": "每日两次",
    }).json()["medication"]
    with store._lock, store._conn:
        store._conn.execute(
            "UPDATE medications SET created_at = ? WHERE id = ?",
            (
                (now - timedelta(days=3)).isoformat(timespec="seconds"),
                medication["id"],
            ),
        )

    taken_at = now.isoformat(timespec="minutes")
    for _ in range(5):
        client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"taken_at": taken_at},
        )

    report = client.get("/api/reports/weekly").json()
    assert report["medications"]["adherence"] == {
        "expected": 8, "taken": 5, "rate": 63,
    }


def test_weekly_report_lists_active_medication_without_logs(client_and_store):
    client, _store = client_and_store

    client.post("/api/medications", json={
        "name": "维生素 D", "frequency": "每日一次",
    })

    report = client.get("/api/reports/weekly").json()
    medications = report["medications"]
    row = medications["by_medication"][0]

    assert row["name"] == "维生素 D"
    assert row["doses"] == 0
    assert row["expected"] == 1
    assert medications["adherence"] == {
        "expected": 1, "taken": 0, "rate": 0,
    }
