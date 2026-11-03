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
