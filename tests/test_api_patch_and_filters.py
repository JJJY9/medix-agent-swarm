"""PATCH 部分更新、日期边界、LIKE 转义与档案规范化的 API 测试。"""
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


def test_patch_metric_keeps_unspecified_fields(client):
    created = client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": "bp",
        "value1": 138,
        "value2": 86,
        "measured_at": "2026-01-02T08:00",
        "note": "晨起",
    })
    metric_id = created.json()["metric"]["id"]

    patched = client.patch(
        f"/api/metrics/{metric_id}", json={"value1": 140}
    )

    assert patched.status_code == 200
    metric = patched.json()["metric"]
    assert metric["value1"] == 140
    assert metric["value2"] == 86
    assert metric["profile"] == "妈妈"
    assert metric["measured_at"] == "2026-01-02T08:00"
    assert metric["note"] == "晨起"


def test_patch_symptom_keeps_recorded_at(client):
    created = client.post("/api/symptoms", json={
        "profile": "妈妈",
        "symptom": "头痛",
        "severity": 5,
        "recorded_at": "2026-01-02T09:00",
        "note": "偶发",
    })
    symptom_id = created.json()["symptom"]["id"]

    patched = client.patch(
        f"/api/symptoms/{symptom_id}", json={"severity": 7}
    )

    symptom = patched.json()["symptom"]
    assert symptom["severity"] == 7
    assert symptom["symptom"] == "头痛"
    assert symptom["recorded_at"] == "2026-01-02T09:00"
    assert symptom["note"] == "偶发"


def test_patch_medication_keeps_active_state(client):
    created = client.post("/api/medications", json={
        "profile": "妈妈",
        "name": "氨氯地平",
        "dose": "5 mg",
        "frequency": "每日一次",
        "active": False,
    })
    medication_id = created.json()["medication"]["id"]

    patched = client.patch(
        f"/api/medications/{medication_id}", json={"dose": "10 mg"}
    )

    medication = patched.json()["medication"]
    assert medication["dose"] == "10 mg"
    assert medication["name"] == "氨氯地平"
    assert medication["active"] is False


def test_midnight_metric_is_included_in_days_window(client):
    yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "metric": "weight",
        "value1": 70,
        "measured_at": f"{yesterday}T00:00",
    })

    listed = client.get("/api/metrics", params={"days": 2})

    values = [item["value1"] for item in listed.json()["metrics"]]
    assert 70 in values


def test_metric_search_escapes_like_wildcards(client):
    client.post("/api/metrics", json={
        "metric": "weight", "value1": 70, "note": "复查率 100% 达标",
    })
    client.post("/api/metrics", json={
        "metric": "weight", "value1": 71, "note": "普通记录",
    })

    percent = client.get("/api/metrics", params={"q": "%"})
    underscore = client.get("/api/metrics", params={"q": "_"})

    assert len(percent.json()["metrics"]) == 1
    assert percent.json()["metrics"][0]["note"].endswith("达标")
    assert underscore.json()["metrics"] == []


def test_weekly_report_normalizes_profile(client):
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/metrics", json={
        "profile": "妈妈",
        "metric": "weight",
        "value1": 62,
        "measured_at": f"{today}T08:00",
    })

    report = client.get(
        "/api/reports/weekly", params={"profile": " 妈妈 ", "days": 7}
    )

    assert report.status_code == 200
    assert report.json()["metrics"]["count"] == 1


def test_reminder_stats_normalizes_profile(client):
    today = datetime.now().strftime("%Y-%m-%d")
    client.post("/api/reminders", json={
        "title": "晨起测血压",
        "due_at": today,
        "profile": "妈妈",
    })

    stats = client.get(
        "/api/reminders/stats", params={"profile": " 妈妈 ", "days": 7}
    )

    assert stats.status_code == 200
    assert stats.json()["due_total"] == 1


def test_risk_filter_scans_past_first_page(client, monkeypatch):
    # 高危会话排在第 2000 条之后，验证扫描不再止步于前 2000 条
    store = memory.get_session_store()
    store.save_exchange("high-risk", "胸痛怎么办", "风险等级：高危，请立即就医。")
    with store._conn:
        store._conn.execute(
            "UPDATE sessions SET updated_at = '2020-01-01T00:00' "
            "WHERE id = 'high-risk'"
        )
    for index in range(2005):
        store.save_exchange(f"s{index}", f"问题 {index}", "普通答复")

    listed = client.get("/api/sessions", params={"risk": "high"})

    assert listed.status_code == 200
    ids = [item["id"] for item in listed.json()["sessions"]]
    assert "high-risk" in ids
