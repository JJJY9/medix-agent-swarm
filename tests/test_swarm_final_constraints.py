"""Swarm 汇总答案的输出约束兜底测试。"""
from constraints.validator import ConstraintValidator
from swarm.swarm_coordinator import SwarmCoordinator
from validation.auto_fixer import AutoFixer


def _coordinator():
    coordinator = SwarmCoordinator.__new__(SwarmCoordinator)
    coordinator.validator = ConstraintValidator()
    coordinator.auto_fixer = AutoFixer()
    return coordinator


def test_swarm_final_answer_gets_disclaimer():
    coordinator = _coordinator()

    fixed = coordinator._enforce_final_constraints(
        "多学科会诊建议低盐饮食、规律运动。", "高血压怎么管理？"
    )

    assert "免责声明" in fixed
    assert coordinator.validator.validate_output(
        "swarm_lead", fixed
    )["valid"]


def test_swarm_final_answer_gets_emergency_warning():
    coordinator = _coordinator()

    fixed = coordinator._enforce_final_constraints(
        "可能与心脏问题有关，先休息观察。",
        "我父亲突发胸痛，出冷汗，怎么办？",
    )

    assert fixed.startswith("⚠️")
    assert "免责声明" in fixed
    assert coordinator.validator.validate_output(
        "swarm_lead", fixed, user_input="我父亲突发胸痛，出冷汗，怎么办？"
    )["valid"]


def test_swarm_final_answer_has_no_consultation_length_cap():
    coordinator = _coordinator()
    answer = (
        "汇总意见。" + "详细说明" * 500
        + "\n\n【免责声明】\n以上信息仅供参考。"
    )

    fixed = coordinator._enforce_final_constraints(answer, "复杂病例")

    assert fixed == answer
