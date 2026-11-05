"""健康指标 API 的离线测试。"""
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


def test_screener_score_api_and_csv(client):
    created = client.post(
        "/api/metrics",
        json={
            "metric": "phq9",
            "value1": 10,
            "measured_at": "2026-01-05T09:00",
            "note": "PHQ-9 筛查",
        },
    )
    listed = client.get("/api/metrics", params={"metric": "phq9"})
    exported = client.get(
        "/api/metrics/export.csv", params={"metric": "phq9"}
    )

    assert created.status_code == 200
    assert created.json()["metric"]["unit"] == "分"
    assert [item["metric"] for item in listed.json()["metrics"]] == ["phq9"]

    text = exported.content.decode("utf-8-sig")
    assert "PHQ-9 评分" in text
    assert "PHQ-9 筛查" in text


def test_metrics_api_roundtrip(client):
    created = client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 138,
            "value2": 86,
            "measured_at": "2026-01-02T08:00",
            "note": "晨起",
        },
    )
    weight = client.post(
        "/api/metrics",
        json={"profile": "妈妈", "metric": "weight", "value1": 62.5},
    )
    listed = client.get("/api/metrics")
    filtered = client.get("/api/metrics", params={"metric": "bp"})
    deleted = client.delete(f"/api/metrics/{created.json()['metric']['id']}")
    after_delete = client.get("/api/metrics")
    missing = client.delete("/api/metrics/999999")

    assert created.status_code == 200
    item = created.json()["metric"]
    assert item["unit"] == "mmHg"
    assert item["value1"] == 138
    assert weight.status_code == 200

    assert listed.status_code == 200
    assert len(listed.json()["metrics"]) == 2
    assert [row["metric"] for row in filtered.json()["metrics"]] == ["bp"]

    assert deleted.status_code == 200
    assert [row["metric"] for row in after_delete.json()["metrics"]] == ["weight"]
    assert missing.status_code == 404


def test_metrics_api_validation(client):
    missing_diastolic = client.post(
        "/api/metrics",
        json={"metric": "bp", "value1": 130},
    )
    reversed_bp = client.post(
        "/api/metrics",
        json={"metric": "bp", "value1": 80, "value2": 120},
    )
    unknown_metric = client.post(
        "/api/metrics",
        json={"metric": "unknown", "value1": 1},
    )
    invalid_time = client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 60,
            "measured_at": "not-a-date",
        },
    )
    invalid_list_filter = client.get(
        "/api/metrics", params={"metric": "unknown"}
    )

    assert missing_diastolic.status_code == 400
    assert reversed_bp.status_code == 400
    assert unknown_metric.status_code == 422
    assert invalid_time.status_code == 400
    assert invalid_list_filter.status_code == 400


def test_metrics_api_update(client):
    created = client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 138,
            "value2": 86,
            "measured_at": "2026-01-02T08:00",
        },
    )
    metric_id = created.json()["metric"]["id"]

    updated = client.patch(
        f"/api/metrics/{metric_id}",
        json={
            "profile": "妈妈",
            "metric": "weight",
            "value1": 62.5,
            "measured_at": "2026-01-03T07:30",
            "note": "复测",
        },
    )
    listed = client.get("/api/metrics", params={"metric": "weight"})
    missing = client.patch(
        "/api/metrics/999999",
        json={"metric": "weight", "value1": 60},
    )
    invalid = client.patch(
        f"/api/metrics/{metric_id}",
        json={"metric": "bp", "value1": 80, "value2": 120},
    )

    assert updated.status_code == 200
    assert updated.json()["metric"]["metric"] == "weight"
    assert updated.json()["metric"]["value1"] == 62.5
    assert len(listed.json()["metrics"]) == 1
    assert listed.json()["metrics"][0]["note"] == "复测"
    assert missing.status_code == 404
    assert invalid.status_code == 404


def test_metrics_csv_import(client):
    content = "﻿" + (
        "测量时间,患者档案,指标,数值1,数值2,单位,备注\r\n"
        "2026-01-02T08:00,妈妈,血压,138,86,mmHg,晨起\r\n"
        "2026-01-03T07:30,妈妈,体重,62.5,,kg,复测\r\n"
        "2026-01-04T09:00,,未知,1,,,无效\r\n"
    )
    first = client.post(
        "/api/metrics/import",
        files={"file": ("metrics.csv", content.encode("utf-8"), "text/csv")},
    )
    listed = client.get("/api/metrics", params={"limit": 10})
    again = client.post(
        "/api/metrics/import",
        files={"file": ("metrics.csv", content.encode("utf-8"), "text/csv")},
    )
    empty = client.post(
        "/api/metrics/import",
        files={"file": ("empty.csv", b"", "text/csv")},
    )

    assert first.status_code == 200
    assert first.json()["imported"] == 2
    assert first.json()["invalid"] == 1
    assert len(listed.json()["metrics"]) == 2
    assert again.status_code == 200
    assert again.json()["imported"] == 0
    assert again.json()["skipped"] == 2
    assert empty.status_code == 400


def test_metrics_csv_export(client):
    client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 138,
            "value2": 86,
            "measured_at": "2026-01-02T08:00",
            "note": "晨起",
        },
    )
    client.post(
        "/api/metrics",
        json={"profile": "妈妈", "metric": "weight", "value1": 62.5},
    )

    response = client.get("/api/metrics/export.csv", params={"metric": "bp"})
    invalid = client.get(
        "/api/metrics/export.csv", params={"metric": "unknown"}
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/csv")
    text = response.content.decode("utf-8-sig")
    assert text.startswith("测量时间")
    assert "血压" in text
    assert "138" in text and "86" in text
    assert "62.5" not in text
    assert invalid.status_code == 400


def test_import_api_accepts_metrics_only(client):
    response = client.post(
        "/api/sessions/import",
        json={
            "health_metrics": [
                {
                    "profile": "孩子",
                    "metric": "temperature",
                    "value1": 38.2,
                    "measured_at": "2026-02-01T20:00",
                }
            ]
        },
    )
    listed = client.get("/api/metrics")

    assert response.status_code == 200
    assert response.json()["metrics"] == 1
    assert listed.json()["metrics"][0]["value1"] == 38.2


def test_metrics_api_days_filter(client):
    from datetime import datetime

    client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 60,
            "measured_at": "2020-01-01T08:00",
        },
    )
    client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 61,
            "measured_at": datetime.now().isoformat(timespec="minutes"),
        },
    )
    client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 62,
            "measured_at": "2099-01-01T08:00",
        },
    )

    all_items = client.get("/api/metrics")
    recent = client.get("/api/metrics", params={"days": 30})

    assert len(all_items.json()["metrics"]) == 3
    assert [item["value1"] for item in recent.json()["metrics"]] == [61]



def test_metrics_summary_api(client):
    from datetime import datetime, timedelta

    now = datetime.now()
    recent = [
        (now - timedelta(days=1)).isoformat(timespec="minutes"),
        (now - timedelta(days=2)).isoformat(timespec="minutes"),
    ]
    older = [
        (now - timedelta(days=9)).isoformat(timespec="minutes"),
        (now - timedelta(days=10)).isoformat(timespec="minutes"),
    ]
    for value, measured_at in ((70, recent[0]), (72, recent[1])):
        client.post(
            "/api/metrics",
            json={
                "profile": "妈妈",
                "metric": "weight",
                "value1": value,
                "measured_at": measured_at,
            },
        )
    for value, measured_at in ((60, older[0]), (62, older[1])):
        client.post(
            "/api/metrics",
            json={
                "profile": "妈妈",
                "metric": "weight",
                "value1": value,
                "measured_at": measured_at,
            },
        )

    summary = client.get(
        "/api/metrics/summary",
        params={"profile": "妈妈", "metric": "weight", "days": 7},
    )
    overall = client.get(
        "/api/metrics/summary", params={"profile": "妈妈", "metric": "weight"}
    )
    invalid = client.get(
        "/api/metrics/summary", params={"metric": "unknown"}
    )
    other_profile = client.get(
        "/api/metrics/summary", params={"profile": "孩子", "metric": "weight"}
    )

    assert summary.status_code == 200
    body = summary.json()
    assert body["count"] == 2
    assert body["value1"]["avg"] == 71.0
    assert body["value1"]["max"] == 72.0
    assert body["value1"]["min"] == 70.0
    assert body["value1"]["prev_avg"] == 61.0
    assert body["latest"]["value1"] == 70.0
    assert body["value2"] is None

    assert overall.json()["count"] == 4
    assert overall.json()["value1"]["prev_avg"] is None
    assert invalid.status_code == 400
    assert other_profile.json()["count"] == 0


def test_metrics_summary_bp_series(client):
    client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 140,
            "value2": 90,
            "measured_at": "2026-01-02T08:00",
        },
    )
    client.post(
        "/api/metrics",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 120,
            "value2": 80,
            "measured_at": "2026-01-03T08:00",
        },
    )

    body = client.get(
        "/api/metrics/summary", params={"metric": "bp", "profile": "妈妈"}
    ).json()

    assert body["count"] == 2
    assert body["value1"]["avg"] == 130.0
    assert body["value2"]["avg"] == 85.0
    assert body["value1"]["max"] == 140.0
    assert body["value2"]["min"] == 80.0
    assert body["latest"]["value1"] == 120.0



def test_metrics_summary_target_adherence(client):
    for value1, value2, at in (
        (140, 90, "2026-01-02T08:00"),
        (120, 80, "2026-01-03T08:00"),
        (130, 85, "2026-01-04T08:00"),
    ):
        client.post(
            "/api/metrics",
            json={
                "profile": "妈妈",
                "metric": "bp",
                "value1": value1,
                "value2": value2,
                "measured_at": at,
            },
        )
    client.put(
        "/api/metric-targets",
        json={
            "profile": "妈妈",
            "metric": "bp",
            "value1": 125,
            "value2": 80,
        },
    )

    with_target = client.get(
        "/api/metrics/summary", params={"metric": "bp", "profile": "妈妈"}
    ).json()
    all_profiles = client.get(
        "/api/metrics/summary", params={"metric": "bp"}
    ).json()
    no_target = client.get(
        "/api/metrics/summary", params={"metric": "weight", "profile": "妈妈"}
    ).json()

    assert with_target["target"] == {"value1": 125.0, "value2": 80.0}
    assert with_target["within_target"] == 1
    assert all_profiles["target"] is None
    assert all_profiles["within_target"] is None
    assert no_target["target"] is None
    assert no_target["within_target"] is None


def test_extended_metric_types(client):
    for metric, value, unit in (
        ("heart_rate", 72, "次/分"),
        ("spo2", 97, "%"),
        ("waist", 82, "cm"),
    ):
        created = client.post(
            "/api/metrics",
            json={
                "metric": metric,
                "value1": value,
                "measured_at": "2026-11-01T08:00",
            },
        )
        assert created.status_code == 200
        assert created.json()["metric"]["unit"] == unit

    summary = client.get(
        "/api/metrics/summary", params={"metric": "spo2", "profile": ""}
    )
    exported = client.get(
        "/api/metrics/export.csv", params={"metric": "heart_rate"}
    )

    assert summary.status_code == 200
    assert summary.json()["count"] == 1
    assert "心率" in exported.content.decode("utf-8-sig")
    invalid = client.post(
        "/api/metrics", json={"metric": "heart_rate", "value1": 5}
    )
    assert invalid.status_code == 400


def test_metrics_api_offset(client):
    for value in (60, 61, 62):
        client.post(
            "/api/metrics",
            json={
                "metric": "weight",
                "value1": value,
                "measured_at": f"2026-01-0{value - 59}T08:00",
            },
        )

    first = client.get(
        "/api/metrics", params={"metric": "weight", "limit": 2}
    )
    second = client.get(
        "/api/metrics",
        params={"metric": "weight", "limit": 2, "offset": 2},
    )

    assert [item["value1"] for item in first.json()["metrics"]] == [62, 61]
    assert [item["value1"] for item in second.json()["metrics"]] == [60]


def test_metrics_summary_spo2_lower_bound_target(client):
    for value, at in ((97, "2026-01-02T08:00"), (89, "2026-01-03T08:00")):
        client.post(
            "/api/metrics",
            json={
                "profile": "妈妈",
                "metric": "spo2",
                "value1": value,
                "measured_at": at,
            },
        )
    client.put(
        "/api/metric-targets",
        json={"profile": "妈妈", "metric": "spo2", "value1": 95},
    )

    body = client.get(
        "/api/metrics/summary", params={"metric": "spo2", "profile": "妈妈"}
    ).json()

    assert body["target"] == {"value1": 95.0, "value2": None}
    assert body["within_target"] == 1


def test_screener_metrics_roundtrip(client):
    for metric, value in (
        ("cat", 32), ("ess", 16), ("auditc", 8), ("mmrc", 3),
    ):
        created = client.post(
            "/api/metrics",
            json={
                "metric": metric,
                "value1": value,
                "measured_at": "2026-01-05T09:00",
            },
        )
        assert created.status_code == 200
        assert created.json()["metric"]["unit"] in ("分", "级")

    exported = client.get(
        "/api/metrics/export.csv", params={"metric": "cat"}
    )
    assert "CAT 评分" in exported.content.decode("utf-8-sig")
    summary = client.get(
        "/api/metrics/summary", params={"metric": "ess", "profile": ""}
    )
    assert summary.json()["count"] == 1


def test_latest_metrics_api(client):
    client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 60,
            "measured_at": "2026-01-01T08:00",
        },
    )
    client.post(
        "/api/metrics",
        json={
            "metric": "weight",
            "value1": 62,
            "measured_at": "2026-01-02T08:00",
        },
    )
    client.post(
        "/api/metrics",
        json={
            "metric": "spo2",
            "value1": 97,
            "measured_at": "2026-01-01T08:00",
        },
    )

    response = client.get("/api/metrics/latest")

    assert response.status_code == 200
    latest = {item["metric"]: item["value1"] for item in response.json()["metrics"]}
    assert latest == {"weight": 62, "spo2": 97}


def test_metrics_days_use_calendar_day_window(client):
    from datetime import datetime, timedelta

    now = datetime.now()
    window_start = (now - timedelta(days=6)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    inside = window_start + timedelta(minutes=1)
    outside = window_start - timedelta(minutes=1)

    for value, measured_at in ((70, inside), (60, outside)):
        client.post(
            "/api/metrics",
            json={
                "metric": "weight",
                "value1": value,
                "measured_at": measured_at.isoformat(timespec="minutes"),
            },
        )

    items = client.get(
        "/api/metrics", params={"days": 7}
    ).json()["metrics"]
    assert [item["value1"] for item in items] == [70]

    summary = client.get(
        "/api/metrics/summary",
        params={"metric": "weight", "days": 7},
    ).json()
    assert summary["count"] == 1


def test_latest_metrics_days_and_future_bound(client):
    from datetime import datetime, timedelta

    now = datetime.now()
    recent = (now - timedelta(days=1)).isoformat(timespec="minutes")
    old = (now - timedelta(days=20)).isoformat(timespec="minutes")

    for value, measured_at in (
        (61, recent), (60, old), (99, "2099-01-01T08:00"),
    ):
        client.post(
            "/api/metrics",
            json={
                "metric": "weight",
                "value1": value,
                "measured_at": measured_at,
            },
        )

    all_latest = client.get("/api/metrics/latest").json()["metrics"]
    assert [item["value1"] for item in all_latest] == [61]

    week = client.get(
        "/api/metrics/latest", params={"days": 7}
    ).json()["metrics"]
    assert [item["value1"] for item in week] == [61]

    today = client.get(
        "/api/metrics/latest", params={"days": 1}
    ).json()["metrics"]
    assert today == []
