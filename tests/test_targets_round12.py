"""血压危急值边界与血糖目标情境的回归测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore, critical_metric_alerts


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _date(days=0):
    return (datetime.now() + timedelta(days=days)).strftime("%Y-%m-%d")


def test_isolated_low_diastolic_is_not_a_critical_bp():
    # 老年单纯收缩期高血压：收缩压高、舒张压低，不是“血压偏低”危急值
    assert critical_metric_alerts("bp", 150, 55) == []
    assert critical_metric_alerts("bp", 125, 55) == []
    # 收缩压 <90 仍然提示
    assert critical_metric_alerts("bp", 89, 80)
    # 高血压危象边界不变
    assert critical_metric_alerts("bp", 180, 80)
    assert critical_metric_alerts("bp", 120, 120)


def test_diabetes_template_does_not_create_context_free_glucose_target(
    client_and_store,
):
    client, _store = client_and_store
    condition = client.post("/api/conditions", json={
        "profile": "妈妈", "name": "2型糖尿病",
    }).json()["condition"]

    result = client.post(
        f"/api/conditions/{condition['id']}/apply-template"
    ).json()

    # 空腹 7.0 的目标会把正常餐后血糖判为超标，模板不再创建
    assert result["targets_created"] == []


def test_glucose_context_insight_runs_even_with_target(client_and_store):
    client, _store = client_and_store
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "glucose", "value1": 7.0,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "glucose", "value1": 7.5,
        "measured_at": f"{_date(0)}T07:00", "context": "fasting",
    })

    insights = client.get(
        "/api/health/insights", params={"profile": "妈妈"}
    ).json()["insights"]
    keys = [item["key"] for item in insights]

    # 目标超标提示与分情境（空腹 ≥7）提示同时存在
    assert "metric_glucose" in keys
    assert "metric_glucose_fasting" in keys
