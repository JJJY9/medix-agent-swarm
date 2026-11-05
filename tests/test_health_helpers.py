"""目标判定与依从率计算的单元测试。"""
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
