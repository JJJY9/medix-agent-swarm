"""约束验证、自动修复与 CLI 边界的回归测试（全部离线）。"""
import asyncio
import importlib.util
from pathlib import Path

import pytest

from constraints import ConstraintValidator
from core.triage import has_visit_recommendation
from validation import AutoFixer

DIAGNOSE_PATH = (
    Path(__file__).resolve().parent.parent / "scripts" / "diagnose.py"
)


@pytest.fixture(scope="module")
def validator():
    return ConstraintValidator()


@pytest.fixture(scope="module")
def diagnose_module():
    spec = importlib.util.spec_from_file_location("medix_diagnose", DIAGNOSE_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_every_yaml_forbidden_diagnosis_action_is_enforced(validator):
    for agent_id in ("consultation_agent", "diagnostic_agent", "research_agent"):
        result = validator.validate_output(agent_id, "根据描述，您患有高血压。")
        assert any("明确诊断" in item for item in result["violations"]), agent_id


def test_negated_diagnosis_phrase_is_not_flagged(validator):
    for text in (
        "仅凭症状不能诊断为高血压，需要测量血压。",
        "无法根据这些信息确诊为高血压。",
        "目前尚未确诊为肺炎。",
        "目前没有确诊为肺炎。",
    ):
        result = validator.validate_output("consultation_agent", text)
        assert not any("明确诊断" in item for item in result["violations"]), text


def test_every_yaml_forbidden_prescription_action_is_enforced(validator):
    samples = {
        "consultation_agent": "建议服用硝苯地平 20mg，每日一次。",
        "diagnostic_agent": "推荐具体药物：阿司匹林 100mg，每日一次。",
        "research_agent": "推荐治疗方案：每天一片阿司匹林。",
    }
    for agent_id, text in samples.items():
        result = validator.validate_output(agent_id, text)
        assert any("药物处方" in item for item in result["violations"]), agent_id


def test_prescription_detection_accepts_tablet_and_liquid_units(validator):
    for text in (
        "建议口服二甲双胍，每次 1 片。",
        "可以服用布洛芬混悬液 5ml。",
        "每日三次，每次两粒。",
        "可服用布洛芬 200mg 缓解疼痛。",
        "布洛芬 200mg，每日三次。",
        "用法：每次 0.5g，一日三次。",
        "建议使用胰岛素 10 单位，每日两次。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert any("药物处方" in item for item in result["violations"]), text


def test_overdose_description_is_not_prescription(validator):
    for text in (
        "误服药物超过 20 片，请立即就医。",
        "过量服药 20 片需要立即就医。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert not any("药物处方" in item for item in result["violations"]), text


def test_general_lifestyle_advice_is_not_prescription(validator):
    result = validator.validate_output(
        "diagnostic_agent",
        "建议多吃蔬菜，每天步行 30 分钟，保持规律作息。",
    )
    assert not any("药物处方" in item for item in result["violations"])


def test_food_slice_is_not_prescription(validator):
    result = validator.validate_output(
        "diagnostic_agent", "每天吃一片全麦面包，多吃蔬菜。"
    )
    assert not any("药物处方" in item for item in result["violations"])


def test_high_risk_check_ignores_non_emergency_mentions(validator):
    result = validator.validate_output(
        "diagnostic_agent", "心悸多与焦虑有关，建议心内科门诊就诊。"
    )
    assert "高危症状未建议就医" not in result["violations"]


def test_high_risk_check_detects_negated_visit_advice(validator):
    for text in (
        "胸痛但不建议您去医院，先在家观察。",
        "胸痛但不建议您现在立刻去医院，先在家观察。",
        "胸痛，不推荐去医院，可在家观察。",
        "胸痛是否需要去医院？先观察。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert "高危症状未建议就医" in result["violations"], text


def test_high_risk_check_accepts_positive_visit_advice(validator):
    for text in (
        "胸痛可能提示急症，请立即就医。",
        "突发胸痛，请及时就诊。",
        "胸痛持续不缓解，建议尽快看医生。",
        "胸痛伴大汗，请立即拨打 120。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert "高危症状未建议就医" not in result["violations"], text
    assert has_visit_recommendation("不建议自行用药，请立即就医。") is True
    assert has_visit_recommendation("不建议您去医院。") is False
    assert has_visit_recommendation("不建议自行处理。急诊。") is True
    assert has_visit_recommendation("血压 120/80，建议休息。") is False
    assert has_visit_recommendation("胸痛是否需要去医院？先观察。") is False


def test_educational_red_flag_text_is_not_treated_as_patient_symptom(validator):
    for text in (
        "糖尿病视网膜病变可导致失明，建议控制血糖并定期复查。",
        "服用铁剂后出现黑便属常见现象，无需停药。",
        "热性惊厥多发生于 6 个月至 5 岁儿童。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert "高危症状未建议就医" not in result["violations"], text


def test_user_input_distinguishes_patient_context(validator):
    benign_question = "糖尿病视网膜病变有哪些表现？"
    output = "糖尿病视网膜病变可导致失明，建议控制血糖并定期复查。"
    result = validator.validate_output(
        "diagnostic_agent", output, user_input=benign_question
    )
    assert "高危症状未建议就医" not in result["violations"]

    emergency_question = "我父亲突发胸痛，出冷汗，怎么办？"
    result = validator.validate_output(
        "diagnostic_agent", "可能与心脏问题有关，先休息观察。",
        user_input=emergency_question,
    )
    assert "高危症状未建议就医" in result["violations"]


def test_auto_fixer_disclaimer_matches_validator_condition(validator):
    text = "这是解释。免责：以上内容不构成建议。"
    result = validator.validate_output("consultation_agent", text)
    assert "缺少免责声明" in result["violations"]

    fixed = AutoFixer().fix_output(text, result["auto_fixable"])

    assert "免责声明" in fixed
    assert "缺少免责声明" not in validator.validate_output(
        "consultation_agent", fixed
    )["violations"]


def test_auto_fixer_high_risk_warning_respects_context():
    fixer = AutoFixer()

    benign = "心悸多与焦虑有关，建议心内科门诊就诊。"
    assert fixer.fix_output(benign, ["add_emergency_warning"]) == benign

    educational = "糖尿病视网膜病变可导致失明，建议控制血糖并定期复查。"
    assert fixer.fix_output(
        educational, ["add_emergency_warning"]
    ) == educational

    negated = "胸痛但不建议您现在立刻去医院。"
    assert fixer.fix_output(
        negated, ["add_emergency_warning"]
    ).startswith("⚠️")

    advised = "突发胸痛，请立即就医。"
    assert fixer.fix_output(advised, ["add_emergency_warning"]) == advised

    patient_context = fixer.fix_output(
        "可能与心脏问题有关，先休息观察。",
        ["add_emergency_warning"],
        user_input="我父亲突发胸痛，出冷汗。",
    )
    assert patient_context.startswith("⚠️")


def test_interactive_mode_exits_cleanly_on_eof(monkeypatch, capsys):
    import main as cli

    def raise_eof(prompt=""):
        raise EOFError

    monkeypatch.setattr("builtins.input", raise_eof)

    asyncio.run(cli.interactive_mode())

    assert "输入已结束" in capsys.readouterr().out


def _diagnose_report(query_ok):
    return {
        "os": "test",
        "python": "3.12",
        "python_ok": True,
        "project_root": "D:/repo",
        "packages": [],
        "missing_required": [],
        "llm_config": {"api_key_set": True, "model_name": "m", "base_url": "http://x"},
        "knowledge": {
            "documents": 1,
            "uploads": 0,
            "vector_store_files": [],
            "query_ok": query_ok,
            "top_score": 0.9 if query_ok else None,
        },
    }


def test_diagnose_exit_code_fails_when_kb_query_fails(
    monkeypatch, capsys, diagnose_module
):
    monkeypatch.setattr(
        diagnose_module, "collect_report", lambda run_kb: _diagnose_report(False)
    )
    monkeypatch.setattr("sys.argv", ["diagnose.py", "--kb", "--json"])

    assert diagnose_module.main() == 1
    output = capsys.readouterr().out
    assert '"query_ok": false' in output


def test_diagnose_conclusion_mentions_kb_failure(diagnose_module, capsys):
    diagnose_module.print_report(_diagnose_report(False))

    output = capsys.readouterr().out
    assert "存在问题" in output
