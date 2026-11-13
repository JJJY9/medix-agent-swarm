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


def test_volume_advice_is_not_prescription():
    assert not _contains_prescription("建议每天饮水 2000ml。")
    assert not _contains_prescription("每天建议摄入 50 克蛋白质。")
    assert not _contains_prescription("服用期间每天吃一片全麦面包。")
    # 食物名词跟在剂量单位之后同样是饮食建议
    assert not _contains_prescription("建议每天吃 200 克水果。")
    assert not _contains_prescription("建议每天吃 200 克蔬菜。")
    assert not _contains_prescription("建议每天保证 2000ml 饮水量。")
    assert not _contains_prescription("服药期间每天吃一片全麦面包。")
    assert not _contains_prescription("用药期间每天吃一片全麦面包。")
    assert not _contains_prescription(
        "您正在服用的降压药请继续，建议每天饮水 2000ml。"
    )
    assert not _contains_prescription("每天喝 500ml 牛奶。")
    # 非药物自我护理不算处方
    assert not _contains_prescription("建议使用 20ml 生理盐水冲洗鼻腔。")
    assert not _contains_prescription("建议使用 2000ml 温水坐浴。")
    # 真正的液体/散剂药物仍算处方（含用药动词、药名或剂型）
    assert _contains_prescription("建议服用布洛芬混悬液 10ml。")
    assert _contains_prescription("推荐对乙酰氨基酚 500mg。")
    assert _contains_prescription("建议服用蒙脱石散 3g，每日三次。")
    assert _contains_prescription("推荐蒙脱石散 3g，一日三次。")
    assert _contains_prescription("建议使用开塞露 20ml。")
    assert _contains_prescription("每天服用乳果糖 15ml。")
    assert _contains_prescription("建议口服蒙脱石散 3 克。")
    assert _contains_prescription("建议每天饮用 15ml 乳果糖。")
    assert _contains_prescription("建议喝 10ml 美林。")
    assert _contains_prescription("建议喝 3g 蒙脱石散。")
    assert _contains_prescription("建议蒙脱石散 3g 与牛奶同服。")
    # 微克/毫克不是克（含中文写法与 U+00B5）
    assert _contains_prescription("建议补充维生素D 10μg。")
    assert _contains_prescription("建议补充维生素D 10µg。")
    assert _contains_prescription("建议每天摄入维生素D 10微克。")
    assert _contains_prescription("建议每天摄入钙 500毫克。")
    assert _contains_prescription("用法：每次 0.5g，一日三次。")


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
