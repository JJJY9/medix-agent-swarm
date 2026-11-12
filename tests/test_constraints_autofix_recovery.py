"""约束自动修复的截断/免责声明回归测试。"""
import asyncio
from types import SimpleNamespace

from loguru import logger

from constraints.validator import ConstraintValidator
from core.agent_loop import AgentLoop
from validation.auto_fixer import AutoFixer

DISCLAIMER = "\n\n【免责声明】\n以上信息仅供参考，不能替代专业医生的诊断和治疗。"


async def _post_process_noop(result, final_answer):
    return result


def _validator():
    return ConstraintValidator()


def test_truncation_keeps_existing_disclaimer_in_one_pass():
    validator = _validator()
    output = "建议内容" + "很长的说明" * 400 + DISCLAIMER
    result = validator.validate_output("consultation_agent", output)

    assert any("回答过长" in item for item in result["violations"])
    assert "truncate_response:2000" in result["auto_fixable"]

    fixed = AutoFixer().fix_output(output, result["auto_fixable"])

    assert "免责声明" in fixed
    assert len(fixed) <= 2000
    assert validator.validate_output("consultation_agent", fixed)["valid"]


def test_truncation_preserves_custom_disclaimer_text():
    validator = _validator()
    custom = "本回答依据 2026 版高血压指南整理，具体用药请遵从主诊医生安排。"
    output = (
        "建议内容" + "很长的说明" * 400
        + "\n\n【免责声明】\n" + custom
    )
    result = validator.validate_output("consultation_agent", output)
    assert "truncate_response:2000" in result["auto_fixable"]

    fixed = AutoFixer().fix_output(output, result["auto_fixable"])

    assert custom in fixed
    assert len(fixed) <= 2000
    assert len(fixed) >= 1900  # 保留声明后不应再额外多截 200 字
    assert validator.validate_output("consultation_agent", fixed)["valid"]


def test_disclaimer_added_after_truncation_stays_within_limit():
    validator = _validator()
    fixer = AutoFixer()
    output = "建议内容" + "很长的说明" * 397
    first = validator.validate_output("consultation_agent", output)
    assert "add_disclaimer" in first["auto_fixable"]

    fixed = fixer.fix_output(output, first["auto_fixable"])
    # 补声明后可能略微超过上限，第二轮应保留声明并把长度压回上限内
    recheck = validator.validate_output("consultation_agent", fixed)
    assert "truncate_response:2000" in recheck["auto_fixable"]

    fixed = fixer.fix_output(fixed, recheck["auto_fixable"])

    assert "免责声明" in fixed
    assert len(fixed) <= 2000
    assert validator.validate_output("consultation_agent", fixed)["valid"]


def test_emergency_warning_survives_truncation():
    validator = _validator()
    output = "可能与心脏问题有关，先休息观察。" + "补充说明" * 500

    fixed = AutoFixer().fix_output(
        output,
        ["truncate_response:2000", "add_emergency_warning"],
        user_input="我父亲突发胸痛，出冷汗。",
    )

    assert fixed.startswith("⚠️")
    assert len(fixed) <= 2000
    assert validator.validate_output(
        "diagnostic_agent", fixed, user_input="我父亲突发胸痛，出冷汗。"
    )["valid"]


def test_emergency_warning_added_before_disclaimer():
    fixed = AutoFixer().fix_output(
        "可能与心脏问题有关，先休息观察。",
        ["add_disclaimer", "add_emergency_warning"],
        user_input="我父亲突发胸痛，出冷汗。",
    )

    assert fixed.startswith("⚠️")
    assert "免责声明" in fixed


def test_existing_medication_and_interaction_notes_are_not_prescriptions():
    validator = _validator()
    for text in (
        "您目前正在服用氨氯地平 5mg，请继续遵医嘱。",
        "药物相互作用提示：华法林 5mg 与阿司匹林 100mg 合用会增加出血风险。",
        "药物相互作用提示：华法林 5mg，与阿司匹林 100mg 合用会增加出血风险。",
        "与华法林合用时，布洛芬 200mg 会增加出血风险。",
        "华法林 5mg 与阿司匹林 100mg 合用需监测出血风险。",
        "氨氯地平的常用剂量为 5mg，每日一次，具体请遵医嘱。",
        "请不要自行服用布洛芬 200mg。",
        "建议患者不要服用布洛芬 200mg。",
        "应避免服用阿司匹林 100mg。",
        "请勿服用布洛芬 200mg。",
        "不可服用阿司匹林 100mg。",
        "不宜服用布洛芬 200mg。",
        "不应服用阿司匹林 100mg。",
        "别服用布洛芬 200mg。",
        "不建议服用布洛芬 200mg。",
        "不推荐服用布洛芬 200mg。",
        "无需服用布洛芬 200mg。",
        "不必服用阿司匹林 100mg。",
        "不能服用布洛芬 200mg。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert not any("药物处方" in item for item in result["violations"]), text


def test_affirmative_prescription_with_context_words_is_still_flagged():
    validator = _validator()
    for text in (
        "建议服用布洛芬 200mg，并注意药物相互作用。",
        "推荐长期服用阿司匹林 100mg。",
        "可以服用二甲双胍 500mg，每日两次。",
        "可以合用阿司匹林 100mg。",
        # 安全语境不能跨分句豁免真实的换药/加量建议
        "不建议自行服用阿司匹林 100mg，但可以改用氯吡格雷 75mg。",
        "不要自行服用布洛芬 200mg，如果疼痛不缓解可以加量到 400mg。",
        # 推荐词与用药词之间隔着多药列表时仍应拦截
        "建议将阿司匹林 100mg 与氯吡格雷 75mg 联合使用。",
        "建议布洛芬 200mg 与对乙酰氨基酚 500mg 同时服用。",
        "推荐阿司匹林 100mg 与氯吡格雷 75mg 联用。",
        # 换药、长期服用、换用等措辞也是具体用药建议
        "目前使用的降糖药建议改为二甲双胍 500mg，每日两次。",
        "需要长期服用阿司匹林 100mg 预防血栓。",
        "您目前服用的药物可以换成布洛芬 200mg。",
        # “适合用于”里的“合用”不是相互作用语境
        "布洛芬 200mg 适合用于缓解轻中度疼痛。",
        # 相邻分句的现有用药措辞不能豁免换药/加药建议
        "您正在服用华法林 5mg，建议换用利伐沙班 20mg。",
        "华法林与布洛芬合用会增加出血风险，建议改用对乙酰氨基酚 500mg。",
        "您正在服用华法林 5mg，布洛芬 200mg 每日三次止痛。",
        "您目前服用的药物建议换用利伐沙班 20mg。",
        # 没有风险提示的裸“联合使用”是具体联合用药方案
        "联合使用阿司匹林 100mg 和氯吡格雷 75mg。",
        "联合使用阿司匹林 100mg，再使用氯吡格雷 75mg。",
        "同时使用布洛芬 200mg 和对乙酰氨基酚 500mg，每日三次。",
        "联合用药：阿司匹林 100mg 和氯吡格雷 75mg。",
        # 祈使式推荐同样属于具体用药建议
        "必须长期服用阿司匹林 100mg。",
        "务必长期服用阿司匹林 100mg。",
        "一定要坚持长期服用阿司匹林 100mg。",
        "需长期服用阿司匹林 100mg。",
        # 推荐语气位于上一分句时仍支配当前分句的用药
        "建议在医生指导下，长期服用阿司匹林 100mg。",
        "建议经评估后，长期服用阿司匹林 100mg 预防血栓。",
    ):
        result = validator.validate_output("diagnostic_agent", text)
        assert any("药物处方" in item for item in result["violations"]), text


def test_agent_loop_second_pass_returns_constraint_valid_answer():
    loop = AgentLoop()
    loop.validator = ConstraintValidator()
    loop.auto_fixer = AutoFixer()
    agent = SimpleNamespace(
        agent_id="consultation_agent",
        post_process_result=_post_process_noop,
    )
    state = loop.state_manager.create_state(
        "test-task", "consultation_agent", {}, 3
    )
    # 补上免责声明后会略微超过 2000 字，必须由第二轮修复压回上限内
    output = "建议内容" + "很长的说明" * 397

    messages = []
    sink_id = logger.add(
        lambda message: messages.append(str(message)), level="INFO"
    )
    try:
        result = asyncio.run(loop._finalize(
            agent, state, output, None, 0, remember=False
        ))
    finally:
        logger.remove(sink_id)

    answer = result["answer"]
    assert "免责声明" in answer
    assert len(answer) <= 2000
    assert loop.validator.validate_output(
        "consultation_agent", answer
    )["valid"]
    # 第二轮修复成功后不应再记录“仍有违规”
    assert any("二次自动修复后已无违规" in item for item in messages)
    assert not any("自动修复后仍有违规" in item for item in messages)
