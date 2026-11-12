"""指标 CSV 导入档案归属提示的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_metric_import_sends_current_profile_as_fallback():
    assert "form.append('profile', targetProfile)" in SCRIPT
    assert "空档案行归入「${targetProfile}」" in SCRIPT
