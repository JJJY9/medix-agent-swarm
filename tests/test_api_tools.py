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


def test_phq9_api(client):
    response = client.post(
        "/api/tools/phq9",
        json={"answers": [0] * 7 + [5 // 3, 0]},
    )
    self_harm = client.post(
        "/api/tools/phq9",
        json={"answers": [0] * 8 + [2]},
    )
    invalid_length = client.post(
        "/api/tools/phq9",
        json={"answers": [0] * 8},
    )
    invalid_value = client.post(
        "/api/tools/phq9",
        json={"answers": [0] * 8 + [4]},
    )

    assert response.status_code == 200
    assert response.json()["score"] == 1
    assert response.json()["severity"] == "无或极轻"

    assert self_harm.status_code == 200
    assert self_harm.json()["urgent"] is True
    assert self_harm.json()["self_harm"] is True

    assert invalid_length.status_code == 422
    assert invalid_value.status_code == 400


def test_gad7_api(client):
    response = client.post(
        "/api/tools/gad7",
        json={"answers": [2] * 5 + [0] * 2},
    )
    invalid_length = client.post(
        "/api/tools/gad7",
        json={"answers": [0] * 6},
    )
    invalid_value = client.post(
        "/api/tools/gad7",
        json={"answers": [0] * 6 + [4]},
    )

    assert response.status_code == 200
    assert response.json()["score"] == 10
    assert response.json()["severity"] == "中度"
    assert invalid_length.status_code == 422
    assert invalid_value.status_code == 400


def test_has_bled_api(client):
    low = client.post(
        "/api/tools/has-bled",
        json={"age": 50},
    )
    high = client.post(
        "/api/tools/has-bled",
        json={
            "age": 80,
            "hypertension": True,
            "abnormal_renal": True,
            "abnormal_liver": True,
            "stroke_history": True,
            "bleeding_history": True,
            "labile_inr": True,
            "drugs": True,
            "alcohol": True,
        },
    )
    invalid_age = client.post(
        "/api/tools/has-bled",
        json={"age": 10},
    )

    assert low.status_code == 200
    assert low.json()["score"] == 0
    assert low.json()["risk"] == "低危"

    assert high.status_code == 200
    assert high.json()["score"] == 9
    assert high.json()["risk"] == "高危"

    assert invalid_age.status_code == 422
