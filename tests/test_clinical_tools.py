"""临床计算工具的单元测试。"""
from core.clinical_tools import (
    calculate_bmi,
    calculate_chads_vasc,
    calculate_egfr,
)


def test_bmi_categories_and_healthy_range():
    result = calculate_bmi(170, 65)
    assert result["bmi"] == 22.5
    assert result["category"] == "正常"
    assert result["healthy_weight_min"] == 53.5
    assert result["healthy_weight_max"] == 69.1

    assert calculate_bmi(170, 50)["category"] == "偏瘦"
    assert calculate_bmi(170, 70)["category"] == "超重"
    assert calculate_bmi(170, 82)["category"] == "肥胖"


def test_bmi_boundaries():
    # BMI 18.5 / 24 / 28 的分界
    assert calculate_bmi(100, 18.5)["category"] == "正常"
    assert calculate_bmi(100, 24.0)["category"] == "超重"
    assert calculate_bmi(100, 28.0)["category"] == "肥胖"


def test_egfr_male_reference_value():
    result = calculate_egfr(60, "male", 1.0)

    assert 84 <= result["egfr"] <= 88
    assert result["stage"] == "G2"
    assert result["creatinine_mg_dl"] == 1.0


def test_egfr_female_reference_value():
    result = calculate_egfr(60, "female", 0.8)

    assert 80 <= result["egfr"] <= 88
    assert result["stage"] == "G2"


def test_egfr_accepts_umol_per_litre():
    mg_dl = calculate_egfr(60, "male", 1.0)
    umol_l = calculate_egfr(60, "male", 88.4, unit="μmol/L")

    assert umol_l["egfr"] == mg_dl["egfr"]
    assert umol_l["creatinine_mg_dl"] == 1.0


def test_egfr_stage_boundaries():
    # 用高肌酐值与低肌酐值覆盖不同分期，验证不抛异常且分期单调
    values = [
        calculate_egfr(40, "male", creatinine)["stage"]
        for creatinine in (0.6, 1.0, 1.5, 2.5, 5.0, 12.0)
    ]

    assert values[0] in ("G1", "G2")
    assert values[-1] == "G5"


def test_chads_vasc_empty_score():
    assert calculate_chads_vasc(50, "male")["score"] == 0
    assert calculate_chads_vasc(50, "male")["risk"] == "低危"
    assert calculate_chads_vasc(50, "female")["score"] == 1
    assert calculate_chads_vasc(50, "female")["risk"] == "低危"


def test_chads_vasc_full_score():
    result = calculate_chads_vasc(
        80,
        "female",
        chf=True,
        hypertension=True,
        diabetes=True,
        stroke_tia=True,
        vascular_disease=True,
    )

    assert result["score"] == 9
    assert result["risk"] == "高危"


def test_chads_vasc_age_weighting():
    assert calculate_chads_vasc(65, "male")["score"] == 1
    assert calculate_chads_vasc(75, "male")["score"] == 2
    assert calculate_chads_vasc(66, "male", hypertension=True)["risk"] == "高危"
