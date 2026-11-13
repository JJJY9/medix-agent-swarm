"""家庭指标对比 API 的离线测试。"""
import csv
import io
from datetime import datetime

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


def test_family_compare_metrics(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/profiles", json={
        "name": "妈妈", "context": {"age": "62"},
    })
    client.post("/api/profiles", json={
        "name": "孩子", "context": {"age": "7"},
    })
    client.post("/api/profiles", json={
        "name": "爸爸", "context": {"age": "65"},
    })
    for value in (60, 62):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": value, "measured_at": now,
        })
    client.post("/api/metrics", json={
        "profile": "孩子", "metric": "weight",
        "value1": 30, "measured_at": now,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 84, "measured_at": now,
    })
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "weight", "value1": 60,
    })

    data = client.get(
        "/api/family/compare", params={"metric": "weight", "days": 30}
    ).json()

    assert data["metric"] == "weight"
    assert data["label"] == "体重"
    assert data["unit"] == "kg"
    assert [row["name"] for row in data["profiles"]] == [
        "妈妈", "孩子", "爸爸",
    ]
    mom = data["profiles"][0]
    assert mom["count"] == 2
    assert mom["avg_value1"] == 61.0
    assert mom["avg_value2"] is None
    assert mom["min_value1"] == 60.0
    assert mom["max_value1"] == 62.0
    assert mom["latest"]["value1"] == 62
    assert mom["within_target"] == 1
    assert mom["target"]["value1"] == 60.0
    assert mom["target_lower_bound"] is False
    child = data["profiles"][1]
    assert child["count"] == 1
    assert child["avg_value1"] == 30.0
    assert child["min_value1"] == 30.0
    assert child["max_value1"] == 30.0
    dad = data["profiles"][2]
    assert dad["count"] == 0
    assert dad["avg_value1"] is None
    assert dad["latest"] is None

    bp = client.get(
        "/api/family/compare", params={"metric": "bp", "days": 30}
    ).json()
    mom_bp = next(row for row in bp["profiles"] if row["name"] == "妈妈")
    assert mom_bp["avg_value1"] == 130.0
    assert mom_bp["avg_value2"] == 84.0


def test_family_compare_reports_lower_bound_target(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "spo2",
        "value1": 96, "measured_at": now,
    })
    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "spo2", "value1": 95,
    })

    data = client.get(
        "/api/family/compare", params={"metric": "spo2", "days": 30}
    ).json()
    mom = next(row for row in data["profiles"] if row["name"] == "妈妈")
    assert mom["within_target"] == 1
    assert mom["target_lower_bound"] is True


def test_family_compare_validates_metric(client_and_store):
    client, _store = client_and_store

    response = client.get(
        "/api/family/compare", params={"metric": "unknown"}
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "不支持的指标类型"


def test_family_compare_empty_store(client_and_store):
    client, _store = client_and_store

    data = client.get("/api/family/compare").json()

    assert data["profiles"] == []
    assert data["metric"] == "weight"


def test_family_compare_export_csv(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")
    for value in (60, 62):
        client.post("/api/metrics", json={
            "profile": "妈妈", "metric": "weight",
            "value1": value, "measured_at": now,
        })
    client.post("/api/metrics", json={
        "profile": "孩子", "metric": "weight",
        "value1": 30, "measured_at": now,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 84, "measured_at": now,
    })

    response = client.get(
        "/api/family/compare/export.csv",
        params={"metric": "weight", "days": 30},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(
        response.content.decode("utf-8-sig")
    )))
    assert rows[0] == [
        "档案", "指标", "单位", "天数", "平均", "最低", "最高",
        "次数", "达标次数", "最近日期", "最近值", "今日累计",
    ]
    by_name = {row[0]: row for row in rows[1:]}
    assert by_name["妈妈"][4] == "61"
    assert by_name["妈妈"][5] == "60"
    assert by_name["妈妈"][6] == "62"
    assert by_name["妈妈"][7] == "2"
    assert by_name["孩子"][4] == "30"

    bp = client.get(
        "/api/family/compare/export.csv",
        params={"metric": "bp", "days": 30},
    )
    bp_rows = list(csv.reader(io.StringIO(
        bp.content.decode("utf-8-sig")
    )))
    mom_bp = next(row for row in bp_rows[1:] if row[0] == "妈妈")
    assert mom_bp[4] == "130/84"
