"""第三轮临床安全审计回归：锂盐利尿剂、过敏阴性、红旗子串与就医建议。"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from constraints import ConstraintValidator
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, has_visit_recommendation

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def test_lithium_and_thiazide_interaction_is_flagged():
    for text in (
        "碳酸锂和氢氯噻嗪能一起吃吗",
        "碳酸锂和吲达帕胺",
    ):
        result = check_medication_safety(text)
        titles = [alert["title"] for alert in result["alerts"]]
        assert any("噻嗪" in title for title in titles), (text, titles)


def test_negative_allergy_result_is_not_an_allergy():
    result = check_medication_safety(
        "能吃阿莫西林吗", {"allergies": "过敏原：阿莫西林阴性"}
    )

    titles = [alert["title"] for alert in result["alerts"]]
    assert not any("过敏史" in title for title in titles)


def test_positive_allergy_result_still_alerts():
    result = check_medication_safety(
        "能吃阿莫西林吗", {"allergies": "过敏原：阿莫西林阳性"}
    )

    titles = [alert["title"] for alert in result["alerts"]]
    assert any("青霉素类过敏史" in title for title in titles)


@pytest.mark.parametrize("text", [
    "尿频尿急，无尿痛",
    "如何减少尿蛋白",
    "没有尿痛，只是尿频",
])
def test_urine_related_substrings_do_not_trigger_emergency(text):
    result = detect_red_flags(text)
    assert "无尿/少尿" not in result["matches"], result["matches"]


@pytest.mark.parametrize("text", [
    "24 小时无尿",
    "尿量明显减少",
    "今天尿量很少，接近少尿",
])
def test_real_oliguria_still_triggers_emergency(text):
    result = detect_red_flags(text)
    assert "无尿/少尿" in result["matches"]


@pytest.mark.parametrize("text", [
    "可以不去医院，先在家观察。",
    "没必要去医院。",
    "用不着去医院。",
    "目前不去医院，先观察。",
])
def test_advice_against_hospital_is_not_visit_advice(text):
    assert has_visit_recommendation(text) is False


def test_descriptive_sentence_starting_with_hospital_is_not_advice():
    text = "医院检查结果未见明显异常，胸痛可能与焦虑有关，先观察。"
    assert has_visit_recommendation(text) is False

    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent", text, user_input="我胸痛两天了"
    )
    assert "高危症状未建议就医" in result["violations"]


def test_advice_against_hospital_triggers_constraint_warning():
    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "可以不去医院，先在家观察。",
        user_input="我胸痛两天了",
    )
    assert "高危症状未建议就医" in result["violations"]


def test_standalone_emergency_sentence_is_still_advice():
    assert has_visit_recommendation("不建议自行处理。急诊。") is True


@pytest.mark.parametrize("context,text", [
    ({"history": "父亲正在化疗"}, "发烧了"),
    ({"history": "化疗已结束"}, "发烧了"),
    ({"history": "化疗后"}, "有点乏力"),
    ({"medications": "未使用抗凝药"}, "摔倒撞到头了"),
    ({"history": "有抗凝药过敏史"}, "摔倒撞到头了"),
])
def test_family_history_and_inactive_conditions_do_not_raise_emergency(
    context, text
):
    result = detect_red_flags(text, context)
    assert result["level"] == "none", result["matches"]


def test_active_chemotherapy_and_anticoagulation_still_raise_emergency():
    fever = detect_red_flags("发烧了", {"history": "化疗后"})
    assert "免疫功能低下伴发热" in fever["matches"]

    trauma = detect_red_flags(
        "摔倒撞到头了", {"active_medications": "华法林 3 mg 每日一次"}
    )
    assert "抗凝治疗期间头部外伤" in trauma["matches"]


def test_head_trauma_requires_actual_head_contact():
    context = {"medications": "华法林"}

    missed = detect_red_flags("摔了一跤，头磕到地上", context)
    assert "抗凝治疗期间头部外伤" in missed["matches"]

    no_head = detect_red_flags("跌倒了，但没碰到头", context)
    assert "抗凝治疗期间头部外伤" not in no_head["matches"]


def _load_risk_skill():
    path = PROJECT_ROOT / ".claude" / "skills" / "assess-risk" / "script" / "risk.py"
    spec = importlib.util.spec_from_file_location("medix_assess_risk", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_assess_risk_skill_ignores_negated_symptoms(monkeypatch):
    module = _load_risk_skill()

    class EmptyKB:
        def search(self, **kwargs):
            return []

    monkeypatch.setattr(module, "get_knowledge_base", lambda: EmptyKB())

    negated = asyncio.run(module.assess_risk("没有胸痛，只是有点咳嗽"))
    assert negated["risk_level"] == "low"

    severe = asyncio.run(module.assess_risk("症状不严重，也没有加重"))
    assert severe["risk_level"] == "low"

    positive = asyncio.run(module.assess_risk("胸痛持续加重"))
    assert positive["risk_level"] in ("high", "emergency")
