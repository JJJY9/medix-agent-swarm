"""急症红旗、用药安全与临床评分的规则回归测试。"""
from core.citations import merge_sources
from core.clinical_tools import calculate_abcd2
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags


def test_triage_keeps_positive_chest_pain_with_modifiers():
    for text in (
        "无诱因突发胸痛",
        "不能缓解的胸痛",
        "最近不舒服伴胸痛",
    ):
        result = detect_red_flags(text)
        assert result["level"] == "emergency", text
        assert "胸痛" in result["matches"]


def test_triage_still_respects_real_negation():
    for text in ("没有胸痛", "否认胸痛", "无呼吸困难"):
        assert detect_red_flags(text)["level"] == "none", text


def test_triage_head_trauma_respects_negation():
    context = {"medications": "华法林"}

    negated = detect_red_flags("今天没有摔倒，只是有点头晕", context)
    positive = detect_red_flags("今天摔倒了，头部撞到门框", context)

    assert "抗凝治疗期间头部外伤" not in negated["matches"]
    assert "抗凝治疗期间头部外伤" in positive["matches"]


def test_triage_pregnancy_bleeding_needs_bleeding_site():
    assert "孕产期出血" not in detect_red_flags(
        "怀孕两个月，刷牙时牙龈出血"
    )["matches"]
    assert "孕产期出血" in detect_red_flags(
        "怀孕两个月，阴道出血"
    )["matches"]


def test_medication_allergy_uses_explicit_statements():
    pen_allergy = check_medication_safety("我对青霉素过敏，能吃头孢吗？")
    titles = [alert["title"] for alert in pen_allergy["alerts"]]
    assert any("青霉素类过敏史" in title for title in titles)

    negated = check_medication_safety("我没有阿司匹林过敏，能吃布洛芬吗？")
    negated_titles = [alert["title"] for alert in negated["alerts"]]
    assert not any("过敏史" in title for title in negated_titles)


def test_medication_allergy_from_context_still_works():
    result = check_medication_safety("能吃头孢吗", {"allergies": "青霉素"})

    assert any("青霉素类过敏史" in alert["title"] for alert in result["alerts"])


def test_abcd2_uses_standard_risk_thresholds():
    moderate = calculate_abcd2(
        age_ge_60=True,
        unilateral_weakness=True,
        duration_ge_60=True,
    )
    high = calculate_abcd2(
        age_ge_60=True,
        bp_ge_140_90=True,
        unilateral_weakness=True,
        duration_ge_60=True,
    )
    low = calculate_abcd2(unilateral_weakness=True)

    assert moderate["score"] == 5
    assert moderate["risk"] == "中风险"
    assert high["score"] == 6
    assert high["risk"] == "高风险"
    assert low["score"] == 2
    assert low["risk"] == "低风险"


def test_merge_sources_keeps_highest_score_duplicate():
    sources = [
        {"source": "指南", "filename": "a.md", "snippet": "同一段", "score": 0.1},
        {"source": "指南", "filename": "a.md", "snippet": "同一段", "score": 0.95},
    ]
    sources += [
        {"source": "其他", "filename": f"b{i}.md", "snippet": "x", "score": 0.8}
        for i in range(5)
    ]

    merged = merge_sources(sources, limit=5)

    assert merged[0]["score"] == 0.95
    assert len(merged) == 5
