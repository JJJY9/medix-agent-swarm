"""临床安全规则的回归测试：胸痛同义词、否定、就医建议与过敏标签。"""
import pytest

from constraints import ConstraintValidator
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags, has_visit_recommendation


@pytest.mark.parametrize("text", [
    "突然胸口痛，出冷汗",
    "胸口疼得厉害",
    "心口痛",
    "心口疼",
    "心前区疼痛",
    "胸部压榨感",
    "胸骨后压榨感",
])
def test_colloquial_chest_pain_triggers_emergency(text):
    result = detect_red_flags(text)
    assert result["level"] == "emergency"
    assert "胸痛" in result["matches"] or "胸部压榨感" in result["matches"]


@pytest.mark.parametrize("text", [
    "我没胸痛，就是有点咳嗽",
    "未见明显胸痛",
    "没有出现过胸痛",
])
def test_negated_colloquial_chest_pain_is_ignored(text):
    assert detect_red_flags(text)["level"] == "none"


def test_descriptive_hospital_mention_is_not_visit_advice():
    text = "您在医院做的检查未见明显异常，胸痛可能与焦虑有关，先观察。"
    assert has_visit_recommendation(text) is False

    assert has_visit_recommendation(
        "根据您之前的就诊记录，胸痛可能与焦虑有关，先观察。"
    ) is False

    # 同句中带有明确建议时仍应识别
    assert has_visit_recommendation(
        "检查结果提示需要进一步评估，建议尽快去医院。"
    ) is True


def test_descriptive_hospital_mention_does_not_suppress_emergency_warning():
    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "您在医院做的检查未见明显异常，胸痛可能与焦虑有关，先观察。",
        user_input="我胸痛两天了",
    )
    assert "高危症状未建议就医" in result["violations"]


def test_colloquial_chest_pain_triggers_validator_warning():
    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "可能是胃食管反流，建议清淡饮食。",
        user_input="突然胸口痛，出冷汗",
    )
    assert "高危症状未建议就医" in result["violations"]
    assert "diagnostic_agent" in validator.get_required_agents(
        "突然胸口痛，出冷汗"
    )


def test_negated_high_risk_symptoms_do_not_require_diagnostic_agent():
    validator = ConstraintValidator()

    assert "diagnostic_agent" not in validator.get_required_agents(
        "我没有呕血也没有黑便，只是胃疼"
    )
    assert "diagnostic_agent" not in validator.get_required_agents(
        "无抽搐，只是头晕"
    )
    assert "diagnostic_agent" not in validator.get_required_agents(
        "否认自杀想法"
    )


@pytest.mark.parametrize("question", [
    "过敏原：阿莫西林，能吃头孢吗",
    "过敏药物：阿莫西林，能吃头孢吗",
    "我过敏的药是阿莫西林，能吃头孢吗",
])
def test_label_style_allergy_is_recognized(question):
    result = check_medication_safety(question)
    titles = [alert["title"] for alert in result["alerts"]]
    assert any("青霉素类过敏史" in title for title in titles), titles


def test_label_style_allergy_is_removed_from_current_medications():
    result = check_medication_safety("过敏原：阿司匹林，能吃布洛芬吗")
    titles = [alert["title"] for alert in result["alerts"]]
    assert any("非甾体抗炎药过敏史" in title for title in titles), titles
    assert not any("重复使用非甾体抗炎药" in title for title in titles), titles


def test_negated_label_style_allergy_is_ignored():
    result = check_medication_safety("没有过敏药物：阿司匹林，能吃布洛芬吗")
    titles = [alert["title"] for alert in result["alerts"]]
    assert not any("过敏史" in title for title in titles), titles


def test_anti_allergy_medication_is_not_an_allergy_statement():
    result = check_medication_safety(
        "我在吃抗过敏药物氯雷他定、华法林，现在能吃阿司匹林吗"
    )
    titles = [alert["title"] for alert in result["alerts"]]
    assert any("华法林与非甾体抗炎药" in title for title in titles)
    assert not any("过敏史" in title for title in titles)


def test_allergy_test_mention_keeps_current_medications():
    result = check_medication_safety(
        "做过敏原检测前我现在吃华法林和布洛芬可以吗"
    )
    titles = [alert["title"] for alert in result["alerts"]]
    assert any("华法林与非甾体抗炎药" in title for title in titles)


def test_negative_allergy_test_result_is_not_history():
    result = check_medication_safety("过敏原：阿莫西林阴性，能吃头孢吗")
    titles = [alert["title"] for alert in result["alerts"]]
    assert not any("过敏史" in title for title in titles)


def test_positive_allergy_test_result_is_history():
    result = check_medication_safety("过敏原：阿莫西林阳性，能吃头孢吗")
    titles = [alert["title"] for alert in result["alerts"]]
    assert any("青霉素类过敏史" in title for title in titles), titles


@pytest.mark.parametrize("text", [
    "今天差点没昏倒",
    "差点没窒息",
    "不过胸痛越来越厉害了",
    "不过是胸痛而已",
])
def test_concessive_and_almost_phrases_still_trigger_emergency(text):
    assert detect_red_flags(text)["level"] == "emergency"


@pytest.mark.parametrize("text", [
    "如果胸痛加重，必要时就医。",
    "出现胸痛应就诊。",
    "应该看医生。",
    "尽早就诊。",
])
def test_common_visit_recommendations_are_recognized(text):
    assert has_visit_recommendation(text) is True
    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent", text, user_input="我胸痛两天了"
    )
    assert "高危症状未建议就医" not in result["violations"]


def test_negated_visit_recommendation_is_not_advice():
    assert has_visit_recommendation("不要去医院，先在家观察。") is False
    assert has_visit_recommendation("不应就诊。") is False
    assert has_visit_recommendation(
        "目前还没有去医院，可以先在家观察。"
    ) is False
    assert has_visit_recommendation("尚未去医院就诊。") is False
    # 否定词离得远时不能把真实建议一并否定
    assert has_visit_recommendation("没有明显诱因也应去医院检查。") is True

    validator = ConstraintValidator()
    result = validator.validate_output(
        "diagnostic_agent",
        "如果您目前没有去医院，可以先在家观察，注意休息。",
        user_input="我胸痛两天了",
    )
    assert "高危症状未建议就医" in result["violations"]
