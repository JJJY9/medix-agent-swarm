"""慢病模板前端入口的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_condition_template_button_is_rendered():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "applyConditionTemplate" in script
    assert "/apply-template" in script
    assert "套用模板" in script
    assert "template_key" in script
    assert "模板：" in script
    assert "loadMetricTargets" in script
