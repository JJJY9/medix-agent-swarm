"""用药频次解析与近 7 天应服次数的离线测试。"""
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import (
    SessionStore,
    parse_frequency_doses_per_day,
)


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


@pytest.mark.parametrize("frequency,expected", [
    ("每日一次", 1.0),
    ("每日三次", 3.0),
    ("每天 2 次", 2.0),
    ("一日三次", 3.0),
    ("一天两次", 2.0),
    ("1日3次", 3.0),
    ("1日1次", 1.0),
    ("1次/日", 1.0),
    ("3次/天", 3.0),
    ("每12小时1次", 2.0),
    ("每8小时一次", 3.0),
    ("睡前", 1.0),
    ("qd", 1.0),
    ("tid", 3.0),
    ("隔日一次", 0.5),
    ("每 3 天一次", pytest.approx(1 / 3)),
    ("每周一次", pytest.approx(1 / 7)),
    ("每周一、三、五", pytest.approx(3 / 7)),
    ("每两周", pytest.approx(1 / 14)),
    ("每月一次", pytest.approx(1 / 30)),
    ("必要时服用", None),
    ("按需", None),
    ("饭后服用", None),
    ("", None),
    (None, None),
])
def test_parse_frequency_doses_per_day(frequency, expected):
    assert parse_frequency_doses_per_day(frequency) == expected


def test_medication_expected_week_doses(client_and_store):
    client, store = client_and_store
    now = datetime.now()

    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍", "frequency": "每日两次",
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
    for _ in range(2):
        client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"taken_at": taken_at},
        )

    data = client.get("/api/medications").json()["medications"][0]
    assert data["expected_week_doses"] == 14
    assert data["week_count"] == 2


def test_medication_expected_doses_counts_from_start_date(client_and_store):
    client, _store = client_and_store

    client.post("/api/medications", json={
        "name": "氨氯地平", "frequency": "每日三次",
    })
    client.post("/api/medications", json={
        "name": "维生素 D", "frequency": "必要时",
    })

    medications = client.get("/api/medications").json()["medications"]
    by_name = {item["name"]: item for item in medications}

    assert by_name["氨氯地平"]["expected_week_doses"] == 3
    assert by_name["维生素 D"]["expected_week_doses"] is None


def test_expected_doses_round_to_zero_for_long_interval(client_and_store):
    client, _store = client_and_store

    client.post("/api/medications", json={
        "profile": "妈妈", "name": "维生素 B12", "frequency": "每月一次",
    })

    medications = client.get(
        "/api/medications", params={"profile": "妈妈", "days": 7}
    ).json()["medications"]
    assert medications[0]["expected_range_doses"] == 0
    assert medications[0]["expected_week_doses"] == 0


def test_expected_doses_and_adherence_use_started_at(client_and_store):
    """补录开始用药日期后，应服次数与依从率分母按 started_at 计算。"""
    client, _store = client_and_store
    now = datetime.now()
    started = (now - timedelta(days=13)).strftime("%Y-%m-%d")
    medication = client.post("/api/medications", json={
        "profile": "妈妈", "name": "二甲双胍", "frequency": "每日两次",
        "started_at": started,
    }).json()["medication"]
    for day in range(6, 0, -1):
        base = (now - timedelta(days=day)).strftime("%Y-%m-%d")
        for hour in (8, 20):
            client.post(
                f"/api/medications/{medication['id']}/logs",
                json={"taken_at": f"{base}T{hour:02d}:00"},
            )

    data = client.get(
        "/api/medications", params={"profile": "妈妈", "days": 7}
    ).json()["medications"][0]
    assert data["expected_week_doses"] == 14
    assert data["expected_range_doses"] == 14

    report = client.get(
        "/api/reports/weekly", params={"profile": "妈妈", "days": 7}
    ).json()
    adherence = report["medications"]["adherence"]
    assert adherence["expected"] == 14
    assert adherence["taken"] == 12
    assert adherence["rate"] == 86
