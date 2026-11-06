"""化验结果趋势迷你图的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_lab_trend_sparkline_is_rendered():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "renderLabSparkline" in script
    assert "lab-sparkline-line" in script
    assert "lab-sparkline-dot" in script
    assert "renderLabSparkline(series)" in script


def test_lab_trend_style_exists():
    css = (WEB_DIR / "styles.css").read_text(encoding="utf-8")
    assert ".lab-sparkline" in css
    assert ".lab-sparkline-line" in css
