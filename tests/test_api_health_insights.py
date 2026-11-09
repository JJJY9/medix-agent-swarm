"""规则化健康提示 API 测试。"""
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


def _seed_critical(client):
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 190, "value2": 130,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 60, "unit": "U/L",
        "ref_low": 7, "ref_high": 40, "tested_at": f"{_date(0)}T09:00",
    })
    client.post("/api/care-items", json={
        "profile": "妈妈", "title": "糖化血红蛋白",
        "interval_days": 90, "next_due_at": _date(-2),
    })
    client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "流感疫苗",
        "vaccinated_at": _date(-60), "next_due": _date(-5),
    })
    client.post("/api/reminders", json={
        "profile": "妈妈", "title": "取降压药", "due_at": _date(-1),
    })
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "心内科复诊",
        "scheduled_at": f"{_date(-2)}T09:00",
    })


def test_insights_prioritize_critical_and_overdue(client_and_store):
    client, _store = client_and_store
    _seed_critical(client)

    response = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    )

    assert response.status_code == 200
    data = response.json()
    keys = [item["key"] for item in data["insights"]]
    assert data["insights"][0]["level"] == "danger"
    assert data["insights"][0]["key"] == "critical"
    assert "labs" in keys
    assert "care_overdue" in keys
    assert "vaccine_overdue" in keys
    assert "reminder_overdue" in keys
    assert "appointment_overdue" in keys
    assert data["counts"]["danger"] >= 1
    assert data["counts"]["warning"] >= 2
    levels = [item["level"] for item in data["insights"]]
    order = {"danger": 0, "warning": 1, "info": 2, "ok": 3}
    assert [order[level] for level in levels] == sorted(
        order[level] for level in levels
    )
    for item in data["insights"]:
        assert item["title"]
        assert item["section"]


def test_insights_metric_over_target(client_and_store):
    client, _store = client_and_store
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 80,
    })
    for hour in (8, 20):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp",
            "value1": 150, "value2": 95,
            "measured_at": f"{_date(0)}T{hour:02d}:00",
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    bp = next(
        item for item in data["insights"] if item["key"] == "metric_bp"
    )
    assert bp["level"] == "warning"
    assert "2 次超出目标" in bp["title"]
    assert "130/80" in bp["detail"]


def test_insights_followup_overdue(client_and_store):
    client, _store = client_and_store
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(-10),
        "diagnosis": "高血压复诊", "next_followup_date": _date(-3),
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    keys = [item["key"] for item in data["insights"]]
    assert "followup_overdue" in keys
    followup = next(
        item for item in data["insights"] if item["key"] == "followup_overdue"
    )
    assert followup["level"] == "warning"
    assert "高血压复诊" in followup["detail"]


def test_insights_no_data_and_all_clear(client_and_store):
    client, _store = client_and_store
    empty = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()
    assert [item["key"] for item in empty["insights"]] == ["no_data"]
    assert empty["insights"][0]["level"] == "info"

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 60,
        "measured_at": f"{_date(0)}T08:00",
    })
    clear = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    assert [item["key"] for item in clear["insights"]] == ["all_clear"]
    assert clear["insights"][0]["level"] == "ok"
    assert clear["counts"]["ok"] == 1


def test_insights_severe_symptom_and_consultation_risk(client_and_store):
    client, store = client_and_store
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "胸痛", "severity": 9,
        "recorded_at": f"{_date(0)}T08:00",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    keys = [item["key"] for item in data["insights"]]
    assert "symptoms_severe" in keys
    assert "all_clear" not in keys

    store.save_exchange("risk-session", "妈妈头晕", "**高危**，尽快就医。")
    store.set_session_profile("risk-session", "妈妈")
    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    keys = [item["key"] for item in data["insights"]]
    assert "consultation_risk" in keys


def test_insights_no_target_single_high_reading(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 170, "value2": 100,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 100, "value2": 60,
        "measured_at": f"{_date(0)}T20:00",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    bp = next(
        item for item in data["insights"] if item["key"] == "metric_bp_single"
    )
    assert bp["level"] == "warning"
    assert "170/100" in bp["title"]
    assert "all_clear" not in [
        item["key"] for item in data["insights"]
    ]


def test_insights_glucose_context_thresholds(client_and_store):
    client, _store = client_and_store
    for hour in (13, 19):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 7.5,
            "measured_at": f"{_date(0)}T{hour:02d}:00",
            "context": "postprandial",
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    keys = [item["key"] for item in data["insights"]]
    assert "metric_glucose_postprandial" not in keys
    assert "metric_glucose_fasting" not in keys

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose", "value1": 8.0,
        "measured_at": f"{_date(0)}T07:00", "context": "fasting",
    })
    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    fasting = next(
        item for item in data["insights"]
        if item["key"] == "metric_glucose_fasting"
    )
    assert fasting["level"] == "info"


def test_insights_followup_overdue_respects_scheduled(client_and_store):
    client, _store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(-10),
        "diagnosis": "高血压复诊", "next_followup_date": _date(-3),
    }).json()["visit"]

    before = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    assert "followup_overdue" in [
        item["key"] for item in before["insights"]
    ]

    client.post(f"/api/visits/{visit['id']}/schedule-followup")
    after = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    assert "followup_overdue" not in [
        item["key"] for item in after["insights"]
    ]


def test_insights_no_data_counts_other_records(client_and_store):
    client, _store = client_and_store
    client.post("/api/appointments", json={
        "profile": "妈妈", "title": "心内科复诊",
        "scheduled_at": f"{_date(10)}T09:00",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    assert "no_data" not in [item["key"] for item in data["insights"]]


def test_insights_capped_at_eight(client_and_store):
    client, _store = client_and_store
    _seed_critical(client)
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "胸痛", "severity": 9,
        "recorded_at": f"{_date(0)}T08:00",
    })
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 130, "value2": 80,
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    assert len(data["insights"]) <= 8
    assert data["insights"][0]["level"] == "danger"


def test_insights_include_medication_safety(client_and_store):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿司匹林",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    safety = next(
        item for item in data["insights"]
        if item["key"] == "medication_safety"
    )
    assert safety["level"] == "warning"
    assert "安全提示" in safety["title"]
    assert safety["detail"]
    assert safety["section"] == "medication"

    family = client.get("/api/health/insights").json()
    family_safety = next(
        item for item in family["insights"]
        if item["key"] == "medication_safety"
    )
    assert "妈妈" in family_safety["detail"]

    dad = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()
    assert "medication_safety" not in [
        item["key"] for item in dad["insights"]
    ]


def test_insights_medication_safety_ignores_stopped_drugs(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "华法林",
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿司匹林", "active": False,
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    assert "medication_safety" not in [
        item["key"] for item in data["insights"]
    ]


def test_insights_medication_safety_covers_allergy(client_and_store):
    client, _store = client_and_store
    client.post("/api/allergies", json={
        "profile": "妈妈", "substance": "青霉素", "kind": "drug",
        "noted_at": _date(0),
    })
    client.post("/api/medications", json={
        "profile": "妈妈", "name": "阿莫西林",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    safety = next(
        item for item in data["insights"]
        if item["key"] == "medication_safety"
    )
    assert "青霉素" in safety["detail"]
    assert "阿莫西林" in safety["detail"]


def test_insights_medication_safety_lists_first_three(
    client_and_store,
):
    client, _store = client_and_store
    for name in ("华法林", "阿司匹林", "阿奇霉素", "胺碘酮", "左氧氟沙星"):
        client.post("/api/medications", json={
            "profile": "妈妈", "name": name,
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    safety = next(
        item for item in data["insights"]
        if item["key"] == "medication_safety"
    )
    assert "4 条安全提示" in safety["title"]
    assert safety["detail"].count("；") >= 1
    assert "华法林与氟喹诺酮类" not in safety["detail"]


def test_insights_days_clamped_and_profile_scoped(client_and_store):
    client, _store = client_and_store
    _seed_critical(client)

    small = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 0}
    ).json()
    huge = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 100000}
    ).json()
    other = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()

    assert small["days"] == 7
    assert huge["days"] == 90
    assert "critical" in [item["key"] for item in small["insights"]]
    assert [item["key"] for item in other["insights"]] == ["no_data"]
