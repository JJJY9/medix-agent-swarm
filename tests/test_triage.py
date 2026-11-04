"""急症红旗预检的单元测试。"""
from core.triage import detect_red_flags


def test_common_symptoms_are_not_flagged():
    result = detect_red_flags("我最近有点咳嗽和流鼻涕，没有发烧")

    assert result["level"] == "none"
    assert result["matches"] == []
    assert result["message"] == ""


def test_chest_pain_and_dyspnea_are_flagged():
    result = detect_red_flags("我突然胸痛、呼吸困难，出冷汗")

    assert result["level"] == "emergency"
    assert result["matches"] == ["胸痛", "呼吸困难"]
    assert "120" in result["message"]


def test_negated_symptom_is_ignored():
    result = detect_red_flags("我没有胸痛，只是有点咳嗽")

    assert result["level"] == "none"


def test_mixed_negation_still_detects_other_symptoms():
    result = detect_red_flags("没有胸痛，但呼吸困难越来越重")

    assert result["level"] == "emergency"
    assert result["matches"] == ["呼吸困难"]


def test_stroke_like_signs_are_flagged():
    result = detect_red_flags("家里人突然说话不清、嘴歪、一侧肢体无力")

    assert result["level"] == "emergency"
    assert "言语不清" in result["matches"]
    assert "口角歪斜" in result["matches"]
    assert "单侧肢体无力" in result["matches"]


def test_meningitis_signs_are_flagged():
    result = detect_red_flags("发热三天，伴颈部僵硬和剧烈头痛")

    assert result["level"] == "emergency"
    assert "颈项强直" in result["matches"]
    assert "剧烈头痛" in result["matches"]


def test_duplicate_labels_are_collapsed():
    result = detect_red_flags("胸痛，心前区痛，胸口压榨感")

    assert result["matches"].count("胸痛") == 1
    assert "胸部压榨感" in result["matches"]


def test_empty_text_is_not_flagged():
    assert detect_red_flags("")["level"] == "none"
    assert detect_red_flags("   ")["level"] == "none"


def test_infant_fever_is_flagged_with_age_context():
    result = detect_red_flags("宝宝发烧到 38 度", {"age": "2个月"})

    assert result["level"] == "emergency"
    assert "3 个月以下婴儿发热" in result["matches"]
    assert "儿科" in result["message"]


def test_older_infant_fever_is_not_flagged_by_context_rule():
    result = detect_red_flags("宝宝发烧到 38 度", {"age": "6个月"})

    assert result["level"] == "none"


def test_negated_fever_is_not_flagged_by_context_rule():
    result = detect_red_flags("宝宝没有发烧，只是流鼻涕", {"age": "2个月"})

    assert result["level"] == "none"


def test_age_in_years_and_days_are_parsed():
    assert detect_red_flags("新生儿发烧", {"age": "新生儿"})["level"] == "emergency"
    assert detect_red_flags("发烧 38 度", {"age": "45天"})["level"] == "emergency"
    assert detect_red_flags("发烧 38 度", {"age": "58"})["level"] == "none"


def test_immunosuppressed_patient_with_fever_is_flagged():
    result = detect_red_flags("最近发热三天", {"history": "化疗后"})

    assert result["level"] == "emergency"
    assert "免疫功能低下伴发热" in result["matches"]
    assert "主诊医生" in result["message"]


def test_immunosuppressed_without_fever_is_not_flagged():
    result = detect_red_flags("最近有点乏力", {"history": "化疗后"})

    assert result["level"] == "none"


def test_anticoagulant_with_head_trauma_is_flagged():
    result = detect_red_flags(
        "昨晚摔倒撞到头部，现在有点头晕",
        {"medications": "华法林"},
    )

    assert result["level"] == "emergency"
    assert "抗凝治疗期间头部外伤" in result["matches"]
    assert "急诊" in result["message"]


def test_anticoagulant_without_head_trauma_is_not_flagged():
    result = detect_red_flags("今天有点头晕", {"medications": "华法林"})

    assert result["level"] == "none"


def test_context_rules_combine_with_keyword_matches():
    result = detect_red_flags(
        "宝宝发烧，刚才还抽搐了一次",
        {"age": "2个月"},
    )

    assert result["level"] == "emergency"
    assert "抽搐" in result["matches"]
    assert "3 个月以下婴儿发热" in result["matches"]


def test_urine_output_and_diabetes_emergencies_are_flagged():
    assert detect_red_flags("两天没有小便，人都肿了")["matches"] == ["无尿/少尿"]
    assert detect_red_flags("最近尿量明显减少")["matches"] == ["无尿/少尿"]
    assert detect_red_flags("医生说可能是糖尿病酮症酸中毒")["matches"] == ["糖尿病急症"]
    assert "糖尿病急症" in detect_red_flags("出现高渗性昏迷")["matches"]


def test_pregnancy_bleeding_is_flagged():
    result = detect_red_flags("怀孕两个月，突然阴道大量出血")

    assert result["level"] == "emergency"
    assert "孕产期出血" in result["matches"]
    assert "大量阴道出血" in result["matches"]


def test_new_red_flags_respect_negation():
    assert detect_red_flags("没有少尿，尿量正常")["level"] == "none"
    assert detect_red_flags("没有酮症酸中毒")["level"] == "none"
