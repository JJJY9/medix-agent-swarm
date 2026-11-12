"""加载更多失败与会诊忙时操作的反馈。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_metrics_append_failure_leaves_retry_label():
    assert "加载失败，点击重试" in SCRIPT
    assert "加载更早记录失败：" in SCRIPT
    assert "loaded = await loadMetrics(true);" in SCRIPT
    # 失败保留按钮内的失败文案；成功或被新请求取代后恢复固定文案
    assert "let loaded;" in SCRIPT
    assert "if (loaded !== false) {" in SCRIPT
    assert "els.metricsMore.textContent = '显示更早记录';" in SCRIPT


def test_busy_session_open_and_send_explain_themselves():
    assert SCRIPT.count("会诊进行中，请先停止当前会诊。") >= 3
    assert "if (busy) {\n    window.alert('会诊进行中，请先停止当前会诊。');\n    return false;" in SCRIPT
