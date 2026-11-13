"""第二十一轮审查：处方/诊断识别与截断免责声明回归测试。"""
from constraints.validator import (
    _contains_definitive_diagnosis,
    _contains_prescription,
)
from validation.auto_fixer import AutoFixer


def test_prescription_recognizes_recommended_drug_without_use_verb():
    assert _contains_prescription("推荐依折麦布 10mg，每日一次。")
    assert _contains_prescription("首选布洛芬 200mg。")
    # 食物计数仍不算处方
    assert not _contains_prescription("建议每天吃一片面包。")


def test_safety_warning_does_not_hide_real_prescription():
    assert _contains_prescription(
        "建议服用布洛芬 200mg，请勿超量。"
    )
    assert _contains_prescription(
        "可服用对乙酰氨基酚 500mg，不要超过每日 2g。"
    )
    # 真正的误服/过量描述仍按中毒语境跳过
    assert not _contains_prescription("误服布洛芬超过 20 片。")


def test_definitive_diagnosis_variants():
    assert _contains_definitive_diagnosis("医生诊断您为肺炎。")
    assert _contains_definitive_diagnosis("诊断结果：社区获得性肺炎")
    assert _contains_definitive_diagnosis("您患的是高血压。")
    assert not _contains_definitive_diagnosis("不能诊断为肺炎。")


def test_long_disclaimer_is_preserved_when_truncating():
    fixer = AutoFixer()
    disclaimer = "【免责声明】\n特别声明：" + "长" * 300
    output = "正文" * 800 + "\n\n" + disclaimer

    result = fixer._truncate_preserving_disclaimer(
        output, 500, ensure_disclaimer=True
    )

    assert "特别声明：" in result
    assert result.count("【免责声明】") == 1
