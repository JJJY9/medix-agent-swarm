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


def test_child_pugh_api(client):
    a = client.post(
        "/api/tools/child-pugh",
        json={
            "bilirubin": 1,
            "albumin": 1,
            "inr": 1,
            "ascites": 1,
            "encephalopathy": 1,
        },
    )
    c = client.post(
        "/api/tools/child-pugh",
        json={
            "bilirubin": 3,
            "albumin": 3,
            "inr": 3,
            "ascites": 3,
            "encephalopathy": 3,
        },
    )
    invalid = client.post(
        "/api/tools/child-pugh",
        json={
            "bilirubin": 0,
            "albumin": 1,
            "inr": 1,
            "ascites": 1,
            "encephalopathy": 1,
        },
    )

    assert a.status_code == 200
    assert a.json()["score"] == 5
    assert a.json()["grade"] == "A"

    assert c.status_code == 200
    assert c.json()["score"] == 15
    assert c.json()["grade"] == "C"

    assert invalid.status_code == 422


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


def test_gcs_api(client):
    normal = client.post(
        "/api/tools/gcs",
        json={"eye": 4, "verbal": 5, "motor": 6},
    )
    severe = client.post(
        "/api/tools/gcs",
        json={"eye": 1, "verbal": 1, "motor": 1},
    )
    invalid = client.post(
        "/api/tools/gcs",
        json={"eye": 0, "verbal": 5, "motor": 6},
    )

    assert normal.status_code == 200
    assert normal.json()["score"] == 15
    assert normal.json()["severity"] == "轻度"

    assert severe.status_code == 200
    assert severe.json()["score"] == 3
    assert severe.json()["severity"] == "重度"

    assert invalid.status_code == 422


def test_wells_dvt_api(client):
    low = client.post("/api/tools/wells-dvt", json={})
    high = client.post(
        "/api/tools/wells-dvt",
        json={
            "active_cancer": True,
            "previous_dvt": True,
            "entire_leg_swelling": True,
        },
    )

    assert low.status_code == 200
    assert low.json()["score"] == 0
    assert low.json()["risk"] == "低概率"

    assert high.status_code == 200
    assert high.json()["score"] == 3
    assert high.json()["risk"] == "高概率"
    assert len(high.json()["criteria"]) == 3


def test_wells_pe_api(client):
    low = client.post("/api/tools/wells-pe", json={})
    high = client.post(
        "/api/tools/wells-pe",
        json={
            "clinical_signs_dvt": True,
            "pe_more_likely": True,
            "heart_rate_gt_100": True,
        },
    )

    assert low.status_code == 200
    assert low.json()["score"] == 0
    assert low.json()["risk"] == "低概率"

    assert high.status_code == 200
    assert high.json()["score"] == 7.5
    assert high.json()["risk"] == "高概率"
    assert len(high.json()["criteria"]) == 3


def test_interactions_api_reports_interactions(client):
    response = client.post(
        "/api/tools/interactions",
        json={"drugs": "华法林\n阿司匹林、克拉霉素"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["drugs"] == "华法林、阿司匹林、克拉霉素"
    assert data["level"] == "warning"
    assert data["alerts"]
    assert all("title" in alert and "detail" in alert for alert in data["alerts"])


def test_interactions_api_returns_empty_for_safe_list(client):
    response = client.post(
        "/api/tools/interactions", json={"drugs": "维生素C"}
    )

    assert response.status_code == 200
    assert response.json()["level"] == "none"
    assert response.json()["alerts"] == []


def test_interactions_api_checks_allergies(client):
    response = client.post(
        "/api/tools/interactions",
        json={"drugs": "阿莫西林", "allergies": "青霉素过敏"},
    )

    assert response.status_code == 200
    alerts = response.json()["alerts"]
    assert any("青霉素" in alert["title"] for alert in alerts)


def test_interactions_api_validates_input(client):
    blank = client.post("/api/tools/interactions", json={"drugs": "   "})
    missing = client.post("/api/tools/interactions", json={})
    too_long = client.post(
        "/api/tools/interactions", json={"drugs": "a" * 1001}
    )

    assert blank.status_code == 400
    assert missing.status_code == 422
    assert too_long.status_code == 422


def test_qsofa_api(client):
    low = client.post(
        "/api/tools/qsofa",
        json={"respiratory_rate": 18, "systolic_bp": 120},
    )
    high = client.post(
        "/api/tools/qsofa",
        json={
            "respiratory_rate": 24,
            "systolic_bp": 90,
            "altered_mentation": True,
        },
    )

    assert low.status_code == 200
    assert low.json()["score"] == 0
    assert low.json()["risk"] == "低风险"
    assert high.status_code == 200
    assert high.json()["score"] == 3
    assert high.json()["risk"] == "高风险"


def test_fib4_api(client):
    response = client.post(
        "/api/tools/fib-4",
        json={"age": 70, "ast": 100, "alt": 50, "platelets": 80},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["fib4"] == 12.37
    assert data["risk"] == "高风险"


def test_meld_api(client):
    response = client.post(
        "/api/tools/meld",
        json={"bilirubin": 3, "inr": 2, "creatinine": 2},
    )
    dialysis = client.post(
        "/api/tools/meld",
        json={"bilirubin": 1, "inr": 1, "creatinine": 1, "dialysis": True},
    )

    assert response.status_code == 200
    assert response.json()["meld"] == 25
    assert response.json()["risk"] == "高风险"
    assert dialysis.status_code == 200
    assert dialysis.json()["meld"] == 20


def test_new_scores_reject_invalid_values(client):
    qsofa = client.post(
        "/api/tools/qsofa",
        json={"respiratory_rate": 0, "systolic_bp": 120},
    )
    fib4 = client.post(
        "/api/tools/fib-4",
        json={"age": 17, "ast": 20, "alt": 20, "platelets": 200},
    )
    meld = client.post(
        "/api/tools/meld",
        json={"bilirubin": 0, "inr": 1, "creatinine": 1},
    )

    assert qsofa.status_code == 422
    assert fib4.status_code == 422
    assert meld.status_code == 422
