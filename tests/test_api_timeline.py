"""健康时间线 API 的离线测试。"""
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


def test_timeline_merges_and_filters(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    bp_time = (now - timedelta(minutes=3)).isoformat(timespec="minutes")
    symptom_time = (now - timedelta(minutes=2)).isoformat(timespec="minutes")
    log_time = (now - timedelta(minutes=1)).isoformat(timespec="minutes")
    older_time = (now - timedelta(days=10)).isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": bp_time,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 4, "recorded_at": symptom_time,
    })
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "氨氯地平", "dose": "5 mg",
    }).json()["medication"]
    client.post(
        f"/api/medications/{medication['id']}/logs",
        json={"taken_at": log_time, "note": "早餐后"},
    )
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "weight",
        "value1": 60, "measured_at": older_time,
    })
    client.post("/api/metrics", json={
        "profile": "父亲", "metric": "weight",
        "value1": 70, "measured_at": now.isoformat(timespec="minutes"),
    })

    timeline = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()
    events = timeline["events"]

    assert timeline["total"] == 4
    assert [event["type"] for event in events] == [
        "medication", "symptom", "metric", "metric"
    ]
    assert events[0]["title"] == "氨氯地平"
    assert events[0]["value"] == "5 mg · 打卡"
    assert events[0]["note"] == "早餐后"
    assert events[1]["value"] == "4/10"
    assert events[2]["title"] == "血压"
    assert events[2]["value"] == "130/85 mmHg"
    assert events[3]["value"] == "60 kg"

    recent = client.get(
        "/api/timeline", params={"profile": "妈妈", "days": 7}
    ).json()
    assert recent["total"] == 3
    assert all(
        event["title"] != "体重" or event["value"] != "60 kg"
        for event in recent["events"]
    )

    father = client.get(
        "/api/timeline", params={"profile": "父亲"}
    ).json()
    assert father["total"] == 1
    assert father["events"][0]["title"] == "体重"

    limited = client.get(
        "/api/timeline", params={"profile": "妈妈", "limit": 2}
    ).json()
    assert len(limited["events"]) == 2
    assert limited["total"] == 4

    family = client.get("/api/timeline").json()
    assert family["total"] == 5
    assert family["events"][0]["profile"] == "父亲"


def test_timeline_consultation_risk(client_and_store):
    client, store = client_and_store

    store.save_exchange(
        "risk-timeline", "头晕怎么办", "风险等级：中危，建议复测血压。"
    )

    events = client.get("/api/timeline").json()["events"]
    consultation = events[0]

    assert consultation["type"] == "consultation"
    assert consultation["risk"] == "medium"
    assert "中危" in consultation["value"]


def test_timeline_consultation_without_risk(client_and_store):
    client, store = client_and_store

    store.save_exchange("plain-session", "普通咨询", "建议多休息、多喝水。")

    events = client.get("/api/timeline").json()["events"]
    consultation = events[0]

    assert consultation["type"] == "consultation"
    assert consultation["risk"] == ""
    assert "中危" not in consultation["value"]


def test_timeline_csv_export(client_and_store):
    client, _store = client_and_store
    now = datetime.now().isoformat(timespec="minutes")

    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 130, "value2": 85, "measured_at": now,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "=SUM(A1:A2)",
        "severity": 4, "recorded_at": now,
    })

    export = client.get(
        "/api/timeline/export.csv", params={"profile": "妈妈"}
    )
    text = export.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))

    assert export.status_code == 200
    assert rows[0] == [
        "时间", "类型", "项目", "数值", "患者档案", "备注", "超标",
    ]
    assert {row[1] for row in rows[1:]} == {"指标", "症状"}
    assert any(row[2].startswith("'=SUM") for row in rows[1:])
    assert all(row[6] == "" for row in rows[1:])


def test_timeline_marks_over_target_metrics(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    high = (now - timedelta(minutes=2)).isoformat(timespec="minutes")
    normal = (now - timedelta(minutes=1)).isoformat(timespec="minutes")

    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": high,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 120, "value2": 80, "measured_at": normal,
    })

    events = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()["events"]

    assert events[0]["value"] == "120/80 mmHg"
    assert events[0]["alert"] is False
    assert events[1]["value"] == "140/95 mmHg"
    assert events[1]["alert"] is True

    exported = client.get(
        "/api/timeline/export.csv", params={"profile": "妈妈"}
    )
    rows = list(
        csv.reader(io.StringIO(exported.content.decode("utf-8-sig")))
    )
    alert_rows = [row for row in rows[1:] if row[6]]
    assert len(alert_rows) == 1
    assert alert_rows[0][3] == "140/95 mmHg"


def test_timeline_csv_export_follows_filters(client_and_store):
    client, _store = client_and_store
    now = datetime.now()
    metric_at = (now - timedelta(minutes=3)).isoformat(timespec="minutes")
    symptom_at = (now - timedelta(minutes=2)).isoformat(timespec="minutes")

    client.put("/api/metric-targets", json={
        "profile": "妈妈", "metric": "bp", "value1": 135, "value2": 85,
    })
    client.post("/api/metrics", json={
        "profile": "妈妈", "metric": "bp",
        "value1": 140, "value2": 95, "measured_at": metric_at,
    })
    client.post("/api/symptoms", json={
        "profile": "妈妈", "symptom": "头晕",
        "severity": 3, "recorded_at": symptom_at,
    })

    def read_rows(**params):
        response = client.get(
            "/api/timeline/export.csv",
            params={"profile": "妈妈", **params},
        )
        return list(
            csv.reader(io.StringIO(response.content.decode("utf-8-sig")))
        )

    symptom_rows = read_rows(type="symptom")
    assert [row[1] for row in symptom_rows[1:]] == ["症状"]

    alert_rows = read_rows(only_alert="true")
    assert len(alert_rows) == 2
    assert alert_rows[1][6] == "是"

    search_rows = read_rows(q="头晕")
    assert len(search_rows) == 2
    assert search_rows[1][1] == "症状"


def test_timeline_includes_consultations(client_and_store):
    client, store = client_and_store

    store.save_exchange("session-mom", "妈妈最近头晕怎么办", "建议监测血压。")
    store.set_session_profile("session-mom", "妈妈")
    store.save_exchange("session-empty", "没有档案的会诊", "已记录。")

    timeline = client.get("/api/timeline").json()
    consultation = [
        event for event in timeline["events"]
        if event["type"] == "consultation"
    ]
    assert len(consultation) == 2
    assert consultation[0]["id"].startswith("session-")
    assert consultation[0]["value"] == "会诊 · 2 条消息"

    mom = client.get(
        "/api/timeline", params={"profile": "妈妈"}
    ).json()
    assert [event["type"] for event in mom["events"]] == ["consultation"]
    assert mom["events"][0]["title"] == "妈妈最近头晕怎么办"
    assert mom["events"][0]["profile"] == "妈妈"

    unassigned = client.get(
        "/api/timeline", params={"profile": ""}
    ).json()
    assert [event["id"] for event in unassigned["events"]] == ["session-empty"]

    exported = client.get(
        "/api/timeline/export.csv", params={"profile": "妈妈"}
    )
    rows = list(csv.reader(io.StringIO(exported.content.decode("utf-8-sig"))))
    assert rows[1][1] == "会诊"
    assert rows[1][2] == "妈妈最近头晕怎么办"
