"""第十六轮审查：急症/慢病/用药安全的否定与同义词回归测试。"""
from pathlib import Path

from core.condition_drug_safety import check_condition_drug_safety
from core.lab_values import normalize_unit_key
from core.medication_safety import check_medication_safety, find_drugs
from core.triage import detect_red_flags, has_visit_recommendation


def test_double_negation_keeps_emergency_flags():
    for text in (
        "不能排除酮症酸中毒",
        "不排除自杀风险",
        "医生说不能排除酮症酸中毒，我该怎么办？",
    ):
        result = detect_red_flags(text)
        assert result["level"] == "emergency", text
        assert result["matches"], text

    for text in ("排除酮症酸中毒", "没有酮症酸中毒"):
        result = detect_red_flags(text)
        assert result["level"] == "none", text


def test_call_120_negation_and_question_are_not_advice():
    assert has_visit_recommendation("不用拨打120，先在家观察。") is False
    assert has_visit_recommendation("别打120。") is False
    assert has_visit_recommendation("需要拨打120吗？") is False
    assert has_visit_recommendation("请立即拨打120。") is True


def test_infant_and_immunosuppressed_fever_variants():
    infant = detect_red_flags("宝宝高热39度", {"age": "2个月"})
    assert infant["level"] == "emergency"
    assert any("婴儿" in flag for flag in infant["matches"])

    immunosuppressed = detect_red_flags(
        "持续高热三天", {"history": "化疗后"}
    )
    assert immunosuppressed["level"] == "emergency"

    burned = detect_red_flags("宝宝烧了三天", {"age": "2个月"})
    assert burned["level"] == "emergency"

    afebrile = detect_red_flags("已经退烧了", {"age": "2个月"})
    assert afebrile["level"] == "none"


def test_uncontrolled_conditions_are_active():
    diabetes = check_condition_drug_safety(
        ["泼尼松"], ["未控制的2型糖尿病"]
    )
    assert diabetes["alerts"]

    asthma = check_condition_drug_safety(["普萘洛尔"], ["未控制的哮喘"])
    assert asthma["alerts"]

    ulcer = check_condition_drug_safety(["布洛芬"], ["未愈合的胃溃疡"])
    assert ulcer["alerts"]

    peptic = check_condition_drug_safety(["布洛芬"], ["消化性溃疡"])
    assert peptic["alerts"]

    nocturnal = check_condition_drug_safety(
        ["propranolol"], ["nocturnal asthma"]
    )
    assert nocturnal["alerts"]

    negated_diabetes = check_condition_drug_safety(
        ["泼尼松"], ["无糖尿病，血压未达标"]
    )
    assert not any(
        "糖尿病" in alert["title"] for alert in negated_diabetes["alerts"]
    )


def test_alcohol_rules_cover_common_phrases_and_negation():
    drunk = check_medication_safety(
        "", {"medications": "头孢克肟；喝了点酒"}
    )
    assert any("酒精" in alert["title"] for alert in drunk["alerts"])

    abstinent = check_medication_safety(
        "", {"medications": "头孢克肟；已经不喝酒了"}
    )
    assert not any("酒精" in alert["title"] for alert in abstinent["alerts"])

    abstinent_beer = check_medication_safety(
        "", {"medications": "头孢克肟；平时不喝啤酒"}
    )
    assert not any(
        "酒精" in alert["title"] for alert in abstinent_beer["alerts"]
    )

    forbidden = check_medication_safety(
        "", {"medications": "头孢克肟；别喝红酒"}
    )
    assert not any(
        "酒精" in alert["title"] for alert in forbidden["alerts"]
    )

    forced = check_medication_safety(
        "", {"medications": "头孢克肟；应酬时不得不喝酒"}
    )
    assert any("酒精" in alert["title"] for alert in forced["alerts"])

    forced_noun = check_medication_safety(
        "", {"medications": "头孢克肟；应酬不得不喝白酒"}
    )
    assert any(
        "酒精" in alert["title"] for alert in forced_noun["alerts"]
    )

    dependent = check_medication_safety(
        "", {"medications": "对乙酰氨基酚；不喝酒就睡不着"}
    )
    assert any(
        "酒精" in alert["title"] for alert in dependent["alerts"]
    )

    acetaminophen = check_medication_safety(
        "", {"medications": "对乙酰氨基酚；喝了点酒"}
    )
    assert any("酒精" in alert["title"] for alert in acetaminophen["alerts"])


def test_drug_groups_include_common_members():
    assert find_drugs("艾司唑仑", "benzodiazepines") == ["艾司唑仑"]
    assert find_drugs("氢化可的松", "corticosteroids") == ["氢化可的松"]
    assert find_drugs("倍他米松", "corticosteroids") == ["倍他米松"]
    # 外用制剂不进入全身糖皮质激素相互作用
    assert find_drugs("氢化可的松乳膏", "corticosteroids") == []
    assert find_drugs("倍他米松滴眼液", "corticosteroids") == []


def test_red_flag_synonyms_and_pregnancy_negation():
    assert detect_red_flags("他突然意识丧失")["level"] == "emergency"
    assert detect_red_flags("咳血两天了")["level"] == "emergency"

    not_pregnant = detect_red_flags("没有怀孕，但是阴道出血")
    assert "孕产期出血" not in not_pregnant["matches"]

    unsure = detect_red_flags("有没有怀孕不确定，现在阴道出血")
    assert "孕产期出血" in unsure["matches"]

    unsure_ab = detect_red_flags("不知道是不是怀孕，见红")
    assert "孕产期出血" in unsure_ab["matches"]


def test_fever_false_positives_are_excluded():
    afebrile = detect_red_flags("已经不发烧了", {"age": "2个月"})
    assert afebrile["level"] == "none"

    calories = detect_red_flags(
        "高热量饮食可以吗", {"history": "化疗后"}
    )
    assert calories["level"] == "none"

    weather = detect_red_flags(
        "今天气温38度", {"history": "化疗后"}
    )
    assert weather["level"] == "none"

    liquor = detect_red_flags(
        "喝40度的白酒可以吗", {"history": "化疗后"}
    )
    assert liquor["level"] == "none"

    real = detect_red_flags(
        "今天气温39度，宝宝体温39度", {"age": "2个月"}
    )
    assert real["level"] == "emergency"


def test_call_120_advice_not_negated_by_earlier_word():
    assert has_visit_recommendation(
        "不需要犹豫，请立即拨打120。"
    ) is True


def test_egfr_unit_with_space_normalizes():
    assert normalize_unit_key("mL/min/1.73 m2") == "ml/min/1.73m2"
    assert normalize_unit_key("mL/min/1.73 m²") == "ml/min/1.73m2"


def test_similar_cases_builds_memory_off_event_loop():
    root = Path(__file__).resolve().parents[1]
    script = (
        root / ".claude" / "skills" / "search-similar-cases"
        / "script" / "search.py"
    ).read_text(encoding="utf-8")
    assert "await asyncio.to_thread(LongTermMemory)" in script
