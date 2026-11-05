"""目标判定与依从率计算的单元测试。"""
from api_server import parse_risk_level
from memory.session_store import adherence_rate, metric_over_target


def test_metric_over_target_bp_compares_diastolic():
    target = {"value1": 135, "value2": 85}

    assert metric_over_target("bp", 140, 95, target) is True
    assert metric_over_target("bp", 130, 90, target) is True
    assert metric_over_target("bp", 130, 80, target) is False


def test_metric_over_target_lower_and_upper_bounds():
    assert metric_over_target("weight", 61, None, {"value1": 60}) is True
    assert metric_over_target("weight", 59, None, {"value1": 60}) is False
    assert metric_over_target(
        "spo2", 94, None, {"value1": 95, "value2": None}
    ) is True
    assert metric_over_target(
        "spo2", 96, None, {"value1": 95, "value2": None}
    ) is False


def test_adherence_rate_rounds_half_up_and_caps():
    assert adherence_rate(5, 8) == 63
    assert adherence_rate(8, 8) == 100
    assert adherence_rate(10, 8) == 100
    assert adherence_rate(0, 8) == 0
    assert adherence_rate(5, 0) is None
    assert adherence_rate(None, None) is None


def test_parse_risk_level_tagged_and_keywords():
    assert parse_risk_level("综合风险：中危") == "medium"
    assert parse_risk_level("风险等级：紧急") == "emergency"
    assert parse_risk_level("**高危**，建议尽快就诊。") == "high"
    assert parse_risk_level("低风险，可先观察。") == "low"
    assert parse_risk_level("高危人群需要定期筛查") == ""
    assert parse_risk_level("") == ""
    assert parse_risk_level(None) == ""


def test_parse_risk_level_accepts_bare_labels():
    assert parse_risk_level("风险等级：高") == "high"
    assert parse_risk_level("风险等级：中，建议尽快就医") == "medium"
    assert parse_risk_level("风险等级：低") == "low"
    assert parse_risk_level("风险等级：高血压") == ""


def test_parse_risk_level_negation_and_formatting():
    assert parse_risk_level(
        "您目前属于低危，注意控制高危因素即可"
    ) == "low"
    assert parse_risk_level("不属于高危，建议观察") == ""
    assert parse_risk_level("风险等级：**紧急**，请立即就医") == "emergency"
    assert parse_risk_level("风险等级：中等") == "medium"
    assert parse_risk_level("风险程度为高危") == "high"
    assert parse_risk_level("高风险因素包括吸烟") == ""
