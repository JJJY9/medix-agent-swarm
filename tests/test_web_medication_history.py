"""用药变更历史前端入口的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_medication_history_toggle_is_rendered():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "toggleMedicationHistory" in script
    assert "/history?limit=100" in script
    assert "收起历史" in script
    assert "medication-history-entry" in script
    assert "加载用药历史失败" in script
