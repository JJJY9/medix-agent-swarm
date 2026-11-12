"""健康指标前端范围校验回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_metric_ranges_are_defined_for_all_types():
    for token in (
        "bp: { min1: 50, max1: 300, min2: 30, max2: 200 }",
        "weight: { min1: 10, max1: 400 }",
        "temperature: { min1: 30, max1: 45 }",
        "glucose: { min1: 1, max1: 50 }",
        "spo2: { min1: 50, max1: 100 }",
        "water: { min1: 0, max1: 20000 }",
    ):
        assert token in SCRIPT


def test_metric_form_validates_range_and_bp_order():
    assert "els.metricValue.max = String(range.max1)" in SCRIPT
    assert "应在 " in SCRIPT
    assert "收缩压应高于舒张压" in SCRIPT
    assert "舒张压应在 " in SCRIPT
