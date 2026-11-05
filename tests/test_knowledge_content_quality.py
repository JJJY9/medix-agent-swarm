"""内置医学文档关键安全表述的回归测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "knowledge" / "data" / "documents"


def _doc(name: str) -> str:
    return (DOCS / name).read_text(encoding="utf-8")


def test_emergency_doc_has_safe_medication_guidance():
    text = _doc("05_symptoms_emergency.txt")

    assert "24小时不超过2g" in text
    assert "最大4g/日" not in text
    assert "疑似主动脉夹层" in text
    assert "阿司匹林300mg" in text


def test_hypertension_guideline_uses_current_heart_failure_regimen():
    text = _doc("20_guideline_hypertension.txt")

    assert "新四联" in text
    assert "金三角" not in text
    assert "硫酸镁仅用于子痫" in text


def test_gonorrhea_regimen_is_current():
    text = _doc("12_icd10_infectious.txt")

    assert "头孢曲松1g单次肌注或静注" in text
    assert "单次肌注250mg" not in text
    assert "规范治疗病死率约10-15%" in text


def test_salt_recommendation_is_consistent():
    for name in (
        "01_lifestyle_hypertension.txt",
        "02_lifestyle_diabetes.txt",
        "04_lifestyle_general_health.txt",
        "20_guideline_hypertension.txt",
    ):
        text = _doc(name)
        assert "<6g" not in text and "<6克" not in text, name


def test_coronary_icd_description_distinguishes_acute_codes():
    text = _doc("10_icd10_cardiovascular.txt")

    assert "I20.0" in text and "I21" in text and "I25.2" in text
