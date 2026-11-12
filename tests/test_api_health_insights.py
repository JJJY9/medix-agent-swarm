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


def test_sleep_lower_bound_insight_wording(client_and_store):
    client, _store = client_and_store
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "sleep", "value1": 7,
    })
    for value in (5, 5.5):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "sleep", "value1": value,
            "measured_at": f"{_date(0)}T08:00",
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    item = next(
        row for row in data["insights"]
        if row["key"] == "metric_sleep"
    )
    assert "低于目标" in item["title"]
    assert "超出目标" not in item["title"]
    assert "目标≥7" in item["detail"].replace(" ", "")
    assert "低于目标" in item["action"]


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


def test_insights_weight_trend(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 80,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 75,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 80,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 78,
        "measured_at": f"{_date(0)}T08:00",
    })

    mom = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    weight = next(
        item for item in mom["insights"]
        if item["key"] == "metric_weight_change"
    )
    assert weight["level"] == "warning"
    assert "体重近" in weight["title"] and "下降" in weight["title"]

    dad = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()
    small = next(
        item for item in dad["insights"]
        if item["key"] == "metric_weight_change"
    )
    assert small["level"] == "info"

    client.post("/api/metrics", json={
        "profile": "孩子", "metric": "weight", "value1": 80,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "孩子", "metric": "weight", "value1": 81.6,
        "measured_at": f"{_date(0)}T08:00",
    })
    boundary = client.get(
        "/api/health/insights", params={"profile": "孩子"}
    ).json()
    boundary_trend = next(
        item for item in boundary["insights"]
        if item["key"] == "metric_weight_change"
    )
    assert boundary_trend["level"] == "info"
    assert "增加" in boundary_trend["title"]

    client.post("/api/metrics", json={
        "profile": "爷爷", "metric": "weight", "value1": 80,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爷爷", "metric": "weight", "value1": 79,
        "measured_at": f"{_date(0)}T08:00",
    })
    stable = client.get(
        "/api/health/insights", params={"profile": "爷爷"}
    ).json()
    assert "metric_weight_change" not in [
        item["key"] for item in stable["insights"]
    ]


def test_insights_weight_trend_runs_with_target(client_and_store):
    client, _store = client_and_store
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "weight", "value1": 70,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 65,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 75,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.put("/api/metric-targets", json={
        "profile": "爸爸", "metric": "weight", "value1": 70,
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 65,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 60,
        "measured_at": f"{_date(0)}T08:00",
    })

    mom = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    mom_keys = [item["key"] for item in mom["insights"]]
    assert "metric_weight" in mom_keys
    assert "metric_weight_change" in mom_keys
    mom_trend = next(
        item for item in mom["insights"]
        if item["key"] == "metric_weight_change"
    )
    assert mom_trend["level"] == "warning"

    dad = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()
    dad_keys = [item["key"] for item in dad["insights"]]
    assert "metric_weight_change" in dad_keys
    assert "metric_weight" not in dad_keys


def test_insights_trends_are_profile_scoped(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight", "value1": 80,
        "measured_at": f"{_date(-10)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "weight", "value1": 60,
        "measured_at": f"{_date(0)}T08:00",
    })
    for offset in (-2, -1):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp",
            "value1": 150, "value2": 95,
            "measured_at": f"{_date(offset)}T07:00",
            "context": "morning",
        })
        client.post("/api/metrics", json={
            "profile": "爸爸", "metric": "bp",
            "value1": 115, "value2": 75,
            "measured_at": f"{_date(offset)}T21:00",
            "context": "evening",
        })

    family = client.get("/api/health/insights").json()

    keys = [item["key"] for item in family["insights"]]
    assert "metric_weight_change" not in keys
    assert "metric_bp_morning_evening" not in keys


def test_insights_bp_morning_evening_difference(client_and_store):
    client, _store = client_and_store
    for offset in (-2, -1):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp",
            "value1": 150, "value2": 95,
            "measured_at": f"{_date(offset)}T07:00",
            "context": "morning",
        })
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "bp",
            "value1": 135, "value2": 85,
            "measured_at": f"{_date(offset)}T21:00",
            "context": "evening",
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()

    bp = next(
        item for item in data["insights"]
        if item["key"] == "metric_bp_morning_evening"
    )
    assert bp["level"] == "info"
    assert "高 15 mmHg" in bp["title"]

    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "bp",
        "value1": 150, "value2": 95,
        "measured_at": f"{_date(0)}T07:00", "context": "morning",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "bp",
        "value1": 120, "value2": 80,
        "measured_at": f"{_date(0)}T21:00", "context": "evening",
    })
    single = client.get(
        "/api/health/insights", params={"profile": "爸爸"}
    ).json()
    assert "metric_bp_morning_evening" not in [
        item["key"] for item in single["insights"]
    ]


def test_insights_bp_morning_evening_boundaries(client_and_store):
    client, _store = client_and_store
    for offset in (-2, -1):
        client.post("/api/metrics", json={
            "profile": "爷爷", "metric": "bp",
            "value1": 150, "value2": 95,
            "measured_at": f"{_date(offset)}T07:00",
            "context": "morning",
        })
        client.post("/api/metrics", json={
            "profile": "爷爷", "metric": "bp",
            "value1": 130, "value2": 80,
            "measured_at": f"{_date(offset)}T21:00",
            "context": "evening",
        })
    for offset in (-2, -1):
        client.post("/api/metrics", json={
            "profile": "孩子", "metric": "bp",
            "value1": 145, "value2": 90,
            "measured_at": f"{_date(offset)}T07:00",
            "context": "morning",
        })
        client.post("/api/metrics", json={
            "profile": "孩子", "metric": "bp",
            "value1": 140, "value2": 85,
            "measured_at": f"{_date(offset)}T21:00",
            "context": "evening",
        })
    for offset in (-2, -1):
        client.post("/api/metrics", json={
            "profile": "奶奶", "metric": "bp",
            "value1": 150, "value2": 95,
            "measured_at": f"{_date(offset)}T07:00",
            "context": "morning",
        })
    client.post("/api/metrics", json={
        "profile": "奶奶", "metric": "bp",
        "value1": 120, "value2": 80,
        "measured_at": f"{_date(0)}T21:00", "context": "evening",
    })

    grandpa = client.get(
        "/api/health/insights", params={"profile": "爷爷"}
    ).json()
    bp = next(
        item for item in grandpa["insights"]
        if item["key"] == "metric_bp_morning_evening"
    )
    assert bp["level"] == "warning"
    assert "高 20 mmHg" in bp["title"]

    child = client.get(
        "/api/health/insights", params={"profile": "孩子"}
    ).json()
    assert "metric_bp_morning_evening" not in [
        item["key"] for item in child["insights"]
    ]

    grandma = client.get(
        "/api/health/insights", params={"profile": "奶奶"}
    ).json()
    assert "metric_bp_morning_evening" not in [
        item["key"] for item in grandma["insights"]
    ]


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


def test_insights_glucose_merges_fasting_and_unspecified(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose", "value1": 6.9,
        "measured_at": f"{_date(0)}T07:00", "context": "fasting",
    })
    for hour in (12, 15, 18, 20, 22):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "glucose", "value1": 9.5,
            "measured_at": f"{_date(0)}T{hour:02d}:00",
        })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()
    fasting = next(
        item for item in data["insights"]
        if item["key"] == "metric_glucose_fasting"
    )

    # 空腹 1 次 + 未分情境 5 次，按次数加权后平均应约为 9.07
    assert "共 6 次" in fasting["detail"]
    assert "9.06" in fasting["title"] or "9.07" in fasting["title"]


def test_insights_followup_overdue_beyond_visit_window(client_and_store):
    client, _store = client_and_store
    client.post("/api/visits", json={
        "profile": "妈妈", "visit_date": _date(-120),
        "diagnosis": "高血压复诊", "next_followup_date": _date(-30),
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    keys = [item["key"] for item in data["insights"]]

    assert "followup_overdue" in keys


def test_insights_completed_vaccine_series_not_overdue(client_and_store):
    client, _store = client_and_store
    client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "乙肝疫苗", "dose_number": 1,
        "vaccinated_at": _date(-90), "next_due": _date(-30),
    })
    client.post("/api/vaccinations", json={
        "profile": "妈妈", "vaccine_name": "乙肝疫苗", "dose_number": 2,
        "vaccinated_at": _date(-20),
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    keys = [item["key"] for item in data["insights"]]

    assert "vaccine_overdue" not in keys


def test_family_insights_label_members_and_avoid_family_average(
    client_and_store,
):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 190, "value2": 130,
        "measured_at": f"{_date(0)}T08:00",
    })
    client.post("/api/metrics", json={
        "profile": "爸爸", "metric": "bp", "value1": 185, "value2": 125,
        "measured_at": f"{_date(0)}T09:00",
    })

    data = client.get("/api/health/insights", params={"days": 30}).json()
    insights = {item["key"]: item for item in data["insights"]}

    critical = insights["critical"]
    assert "妈妈：" in critical["detail"]
    assert "爸爸：" in critical["detail"]

    bp = insights["metric_bp_single"]
    assert "家庭成员" in bp["title"]
    assert "平均" not in bp["detail"]


def test_single_profile_bp_insight_still_uses_average(client_and_store):
    client, _store = client_and_store
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp", "value1": 150, "value2": 95,
        "measured_at": f"{_date(0)}T08:00",
    })

    data = client.get(
        "/api/health/insights", params={"profile": "妈妈", "days": 30}
    ).json()
    bp = next(
        item for item in data["insights"]
        if item["key"] == "metric_bp_single"
    )

    assert "家庭成员" not in bp["title"]
    assert "平均" in bp["detail"]


def test_family_insights_batches_safety_queries(
    client_and_store, monkeypatch,
):
    client, store = client_and_store
    for name in ("妈妈", "爸爸"):
        client.post("/api/medications", json={
            "profile": name, "name": "氨氯地平", "dose": "5 mg",
        })

    counts = {}
    for method in (
        "list_profiles", "list_medications", "list_allergies",
        "list_lab_results", "list_conditions",
    ):
        original = getattr(store, method)

        def make(method_name, func):
            def wrapped(*args, **kwargs):
                counts[method_name] = counts.get(method_name, 0) + 1
                return func(*args, **kwargs)
            return wrapped

        monkeypatch.setattr(store, method, make(method, original))

    client.get("/api/health/insights", params={"days": 30})

    # 全员数据只加载一次；用药清单另有一次来自周报聚合
    assert counts.get("list_allergies") == 1
    assert counts.get("list_profiles") == 1
    assert counts.get("list_medications", 0) <= 2
