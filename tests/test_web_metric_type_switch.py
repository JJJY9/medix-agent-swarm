"""指标类型卡片切换与编辑态互斥的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_metric_type_switch_exits_edit_mode():
    assert "function selectMetricType(type)" in SCRIPT
    assert "已取消编辑：指标类型已切换。" in SCRIPT
    # 最近指标卡片与近 7 天变化卡片都走统一入口
    assert SCRIPT.count("selectMetricType(metric)") == 2
    # 不再存在直接改类型、保留 editingMetricId 的旧路径
    assert "setMetricType(metric);\n      refreshMetrics();" not in SCRIPT


def test_metric_type_dropdown_still_resets_edit():
    assert "if (editingMetricId) {\n    resetMetricForm();" in SCRIPT
