"""指标导入单位换算与提醒跨档案判重回归。"""
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


def test_metric_import_converts_common_units(client_and_store):
    client, _store = client_and_store
    csv_text = (
        "测量时间,患者档案,指标,数值1,数值2,单位,备注\n"
        "2026-11-01T08:00,妈妈,体重,154.3,,lb,磅\n"
        "2026-11-01T08:00,妈妈,腰围,31.5,,in,英寸\n"
        "2026-11-01T08:00,妈妈,体温,98.6,,F,华氏\n"
        "2026-11-01T08:00,妈妈,血糖,126,,mg/dL,毫克\n"
        "2026-11-01T08:00,妈妈,血压,16,10.7,kPa,千帕\n"
        "2026-11-01T08:00,妈妈,体重,70,,,\n"
        "2026-11-01T08:00,妈妈,体重,11,,stone,未知单位\n"
    )

    response = client.post(
        "/api/metrics/import",
        files={"file": ("metrics.csv", csv_text.encode("utf-8"), "text/csv")},
    )

    assert response.status_code == 200
    result = response.json()
    assert result["imported"] == 6
    assert result["invalid"] == 1

    metrics = client.get("/api/metrics").json()["metrics"]
    by_metric = {}
    for item in metrics:
        by_metric.setdefault(item["metric"], []).append(item)

    weight = min(
        by_metric["weight"], key=lambda item: abs(item["value1"] - 70)
    )
    assert abs(weight["value1"] - 70.0) < 0.1
    assert by_metric["waist"][0]["value1"] == pytest.approx(80.01, abs=0.01)
    assert by_metric["temperature"][0]["value1"] == pytest.approx(37.0, abs=0.1)
    assert by_metric["glucose"][0]["value1"] == pytest.approx(6.99, abs=0.02)
    assert by_metric["bp"][0]["value1"] == pytest.approx(120.01, abs=0.1)
    assert by_metric["bp"][0]["value2"] == pytest.approx(80.26, abs=0.1)
    assert not any(
        item["metric"] == "weight" and item["value1"] == 11
        for item in metrics
    )


def test_reminder_import_same_id_different_profile_is_kept(client_and_store):
    client, store = client_and_store
    with store._lock, store._conn:
        store._conn.execute(
            """
            INSERT INTO reminders(
                id, title, note, due_at, session_id, profile, advance_days,
                repeat_days, repeat_until, medication_id, done,
                created_at, updated_at
            ) VALUES (7, '复查', '', '2026-11-10', '', '妈妈', 0, 0, '', 0, 0,
                      '2026-11-01T09:00:00', '2026-11-01T09:00:00')
            """
        )

    response = client.post(
        "/api/sessions/import",
        json={
            "reminders": [{
                "id": 7,
                "title": "复查",
                "due_at": "2026-11-12",
                "profile": "爸爸",
                "created_at": "2026-11-02T09:00:00",
            }],
        },
    )

    assert response.status_code == 200
    reminders = client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]
    profiles = {item["profile"] for item in reminders if item["title"] == "复查"}
    assert profiles == {"妈妈", "爸爸"}


def test_reminder_import_same_id_same_profile_is_still_skipped(
    client_and_store,
):
    client, store = client_and_store
    with store._lock, store._conn:
        store._conn.execute(
            """
            INSERT INTO reminders(
                id, title, note, due_at, session_id, profile, advance_days,
                repeat_days, repeat_until, medication_id, done,
                created_at, updated_at
            ) VALUES (8, '复诊', '', '2026-11-10', '', '妈妈', 0, 0, '', 0, 0,
                      '2026-11-01T09:00:00', '2026-11-01T09:00:00')
            """
        )

    response = client.post(
        "/api/sessions/import",
        json={
            "reminders": [{
                "id": 8,
                "title": "复诊",
                "due_at": "2026-11-12",
                "profile": "妈妈",
                "created_at": "2026-11-02T09:00:00",
            }],
        },
    )

    assert response.status_code == 200
    reminders = client.get(
        "/api/reminders", params={"include_done": True}
    ).json()["reminders"]
    assert len([
        item for item in reminders
        if item["title"] == "复诊" and item["profile"] == "妈妈"
    ]) == 1
