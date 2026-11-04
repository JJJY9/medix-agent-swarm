"""临床计算工具的单元测试。"""
import pytest

from core.clinical_tools import (
    calculate_bmi,
    calculate_chads_vasc,
    calculate_child_pugh,
    calculate_curb65,
    calculate_egfr,
    calculate_gad7,
    calculate_gcs,
    calculate_has_bled,
    calculate_phq9,
    calculate_wells_dvt,
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


def test_curb65_low_risk_and_boundaries():
    result = calculate_curb65(
        age=40,
        confusion=False,
        urea=7,
        respiratory_rate=29,
        systolic_bp=100,
        diastolic_bp=70,
    )

    assert result["score"] == 0
    assert result["risk"] == "低危"
    assert result["criteria"] == []


def test_curb65_moderate_risk():
    result = calculate_curb65(70, False, 8, 20, 120, 80)

    assert result["score"] == 2
    assert result["risk"] == "中危"
    assert "年龄 ≥ 65 岁" in result["criteria"]
    assert "尿素氮 > 7 mmol/L" in result["criteria"]


def test_curb65_all_criteria_high_risk():
    result = calculate_curb65(80, True, 10, 32, 85, 55)

    assert result["score"] == 5
    assert result["risk"] == "高危"
    assert len(result["criteria"]) == 5
    assert "低血压（收缩压 < 90 或舒张压 ≤ 60 mmHg）" in result["criteria"]


def test_child_pugh_thresholds():
    assert calculate_child_pugh(1, 1, 1, 1, 1)["grade"] == "A"
    assert calculate_child_pugh(2, 1, 1, 1, 1)["grade"] == "A"
    assert calculate_child_pugh(2, 2, 1, 1, 1)["grade"] == "B"
    assert calculate_child_pugh(2, 2, 2, 2, 1)["grade"] == "B"
    assert calculate_child_pugh(2, 2, 2, 2, 2)["grade"] == "C"
    assert calculate_child_pugh(3, 3, 3, 3, 3)["score"] == 15
    assert calculate_child_pugh(3, 3, 3, 3, 3)["grade"] == "C"


def test_child_pugh_validation():
    with pytest.raises(ValueError):
        calculate_child_pugh(0, 1, 1, 1, 1)
    with pytest.raises(ValueError):
        calculate_child_pugh(1, 4, 1, 1, 1)
    with pytest.raises(ValueError):
        calculate_child_pugh(1, 1, 1, 1, True)


def test_phq9_severity_thresholds():
    assert calculate_phq9([0] * 9)["severity"] == "无或极轻"
    assert calculate_phq9([1] * 4 + [0] * 5)["severity"] == "无或极轻"
    assert calculate_phq9([1] * 5 + [0] * 4)["severity"] == "轻度"
    assert calculate_phq9([2] * 5 + [0] * 4)["severity"] == "中度"
    assert calculate_phq9([3] * 5 + [0] * 4)["severity"] == "中重度"
    assert calculate_phq9([3] * 7 + [0] * 2)["severity"] == "重度"


def test_phq9_self_harm_flag():
    result = calculate_phq9([0] * 8 + [1])

    assert result["score"] == 1
    assert result["self_harm"] is True
    assert result["urgent"] is True
    assert "自伤" in result["note"]


def test_phq9_validation():
    with pytest.raises(ValueError):
        calculate_phq9([0] * 8)
    with pytest.raises(ValueError):
        calculate_phq9([0] * 8 + [4])
    with pytest.raises(ValueError):
        calculate_phq9([0] * 8 + [True])


def test_gad7_severity_thresholds():
    assert calculate_gad7([0] * 7)["severity"] == "无或极轻"
    assert calculate_gad7([1] * 4 + [0] * 3)["severity"] == "无或极轻"
    assert calculate_gad7([1] * 5 + [0] * 2)["severity"] == "轻度"
    assert calculate_gad7([2] * 5 + [0] * 2)["severity"] == "中度"
    assert calculate_gad7([3] * 5 + [0] * 2)["severity"] == "重度"


def test_gad7_validation():
    with pytest.raises(ValueError):
        calculate_gad7([0] * 6)
    with pytest.raises(ValueError):
        calculate_gad7([0] * 6 + [4])
    with pytest.raises(ValueError):
        calculate_gad7([0] * 6 + [True])


def test_has_bled_score_and_thresholds():
    low = calculate_has_bled(50)
    assert low["score"] == 0
    assert low["risk"] == "低危"

    assert calculate_has_bled(65)["score"] == 0
    assert calculate_has_bled(66)["score"] == 1

    moderate = calculate_has_bled(
        80, hypertension=True, bleeding_history=True
    )
    assert moderate["score"] == 3
    assert moderate["risk"] == "高危"

    all_positive = calculate_has_bled(
        80,
        hypertension=True,
        abnormal_renal=True,
        abnormal_liver=True,
        stroke_history=True,
        bleeding_history=True,
        labile_inr=True,
        drugs=True,
        alcohol=True,
    )
    assert all_positive["score"] == 9
    assert all_positive["risk"] == "高危"
    assert len(all_positive["criteria"]) == 9


def test_has_bled_boundary_low_risk():
    result = calculate_has_bled(
        80, hypertension=True, bleeding_history=True
    )
    # 年龄 + 高血压 + 出血史 = 3，仍为高危
    assert result["risk"] == "高危"

    two_points = calculate_has_bled(80, hypertension=True)
    assert two_points["score"] == 2
    assert two_points["risk"] == "低危"


def test_gcs_severity_thresholds():
    assert calculate_gcs(4, 5, 6)["score"] == 15
    assert calculate_gcs(4, 5, 6)["severity"] == "轻度"
    assert calculate_gcs(3, 4, 5)["score"] == 12
    assert calculate_gcs(3, 4, 5)["severity"] == "中度"
    assert calculate_gcs(2, 3, 4)["score"] == 9
    assert calculate_gcs(2, 3, 4)["severity"] == "中度"
    assert calculate_gcs(1, 1, 1)["score"] == 3
    assert calculate_gcs(1, 1, 1)["severity"] == "重度"
    assert calculate_gcs(2, 2, 4)["score"] == 8
    assert calculate_gcs(2, 2, 4)["severity"] == "重度"


def test_gcs_validation():
    with pytest.raises(ValueError):
        calculate_gcs(0, 5, 6)
    with pytest.raises(ValueError):
        calculate_gcs(4, 6, 6)
    with pytest.raises(ValueError):
        calculate_gcs(4, 5, 7)
    with pytest.raises(ValueError):
        calculate_gcs(True, 5, 6)


def test_wells_dvt_probability_thresholds():
    assert calculate_wells_dvt()["score"] == 0
    assert calculate_wells_dvt()["risk"] == "低概率"

    one = calculate_wells_dvt(active_cancer=True)
    assert one["score"] == 1
    assert one["risk"] == "中概率"

    two = calculate_wells_dvt(active_cancer=True, previous_dvt=True)
    assert two["score"] == 2
    assert two["risk"] == "中概率"

    three = calculate_wells_dvt(
        active_cancer=True, previous_dvt=True, entire_leg_swelling=True
    )
    assert three["score"] == 3
    assert three["risk"] == "高概率"

    alternative = calculate_wells_dvt(alternative_diagnosis=True)
    assert alternative["score"] == -2
    assert alternative["risk"] == "低概率"


def test_wells_dvt_all_criteria():
    result = calculate_wells_dvt(
        active_cancer=True,
        paralysis_or_immobilization=True,
        bedridden_or_surgery=True,
        deep_vein_tenderness=True,
        entire_leg_swelling=True,
        calf_swelling=True,
        pitting_edema=True,
        collateral_veins=True,
        previous_dvt=True,
        alternative_diagnosis=True,
    )

    assert result["score"] == 7
    assert result["risk"] == "高概率"
    assert len(result["criteria"]) == 10
