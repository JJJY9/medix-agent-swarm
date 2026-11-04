"""临床计算工具 API 的离线测试。"""
import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_bmi_api(client):
    response = client.post(
        "/api/tools/bmi",
        json={"height_cm": 170, "weight_kg": 65},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["bmi"] == 22.5
    assert data["category"] == "正常"
    assert data["healthy_weight_min"] == 53.5


def test_bmi_api_rejects_invalid_values(client):
    assert client.post(
        "/api/tools/bmi", json={"height_cm": 0, "weight_kg": 65}
    ).status_code == 422
    assert client.post(
        "/api/tools/bmi", json={"height_cm": 170, "weight_kg": 0}
    ).status_code == 422
    assert client.post("/api/tools/bmi", json={}).status_code == 422


def test_egfr_api_accepts_both_units(client):
    mg_dl = client.post(
        "/api/tools/egfr",
        json={
            "age": 60,
            "sex": "male",
            "creatinine": 1.0,
            "unit": "mg/dL",
        },
    )
    umol_l = client.post(
        "/api/tools/egfr",
        json={
            "age": 60,
            "sex": "male",
            "creatinine": 88.4,
            "unit": "umol/L",
        },
    )

    assert mg_dl.status_code == 200
    assert mg_dl.json()["egfr"] == umol_l.json()["egfr"]
    assert mg_dl.json()["stage"] == "G2"
    assert mg_dl.json()["creatinine_mg_dl"] == 1.0


def test_egfr_api_validates_adult_and_sex(client):
    assert client.post(
        "/api/tools/egfr",
        json={"age": 10, "sex": "male", "creatinine": 1.0},
    ).status_code == 422
    assert client.post(
        "/api/tools/egfr",
        json={"age": 60, "sex": "other", "creatinine": 1.0},
    ).status_code == 422
    assert client.post(
        "/api/tools/egfr",
        json={"age": 60, "sex": "male", "creatinine": 0},
    ).status_code == 422


def test_chads_vasc_api(client):
    response = client.post(
        "/api/tools/chads-vasc",
        json={
            "age": 80,
            "sex": "female",
            "chf": True,
            "hypertension": True,
            "diabetes": True,
            "stroke_tia": True,
            "vascular_disease": True,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 9
    assert data["risk"] == "高危"
    assert "出血风险" in data["note"]


def test_chads_vasc_api_defaults(client):
    response = client.post(
        "/api/tools/chads-vasc",
        json={"age": 50, "sex": "male"},
    )

    assert response.status_code == 200
    assert response.json()["score"] == 0
    assert response.json()["risk"] == "低危"


def test_curb65_api(client):
    response = client.post(
        "/api/tools/curb65",
        json={
            "age": 70,
            "confusion": False,
            "urea": 8,
            "respiratory_rate": 20,
            "systolic_bp": 120,
            "diastolic_bp": 80,
        },
    )

    assert response.status_code == 200
    data = response.json()
    assert data["score"] == 2
    assert data["risk"] == "中危"
    assert "年龄 ≥ 65 岁" in data["criteria"]


def test_curb65_api_validates_ranges(client):
    below_adult = client.post(
        "/api/tools/curb65",
        json={
            "age": 10,
            "urea": 5,
            "respiratory_rate": 18,
            "systolic_bp": 120,
            "diastolic_bp": 80,
        },
    )
    negative_urea = client.post(
        "/api/tools/curb65",
        json={
            "age": 50,
            "urea": -1,
            "respiratory_rate": 18,
            "systolic_bp": 120,
            "diastolic_bp": 80,
        },
    )

    assert below_adult.status_code == 422
    assert negative_urea.status_code == 422
