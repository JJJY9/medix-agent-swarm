"""前端状态完整性回归：HTML id 唯一、就诊摘要按钮不被改写、指标编辑随筛选取消。"""
import re
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
CSS = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_html_ids_are_unique():
    ids = re.findall(r'\bid="([^"]+)"', HTML)
    duplicates = {value for value in ids if ids.count(value) > 1}
    assert duplicates == set()


def test_visit_summary_button_is_not_hijacked_by_count_paragraph():
    assert 'id="visit-summary-text"' in HTML
    assert 'id="visit-summary"' in HTML
    assert "visitSummary: document.getElementById('visit-summary')" in SCRIPT
    assert (
        "visitSummaryText: document.getElementById('visit-summary-text')"
        in SCRIPT
    )
    assert "els.visitSummaryText.textContent" in SCRIPT
    assert "els.visitSummaryText.hidden = true;" in SCRIPT
    # 导出按钮仍使用 visitSummary，不被计数段落覆盖
    assert "els.visitSummary.textContent = '生成中…';" in SCRIPT


def test_metric_edit_is_cancelled_when_filter_changes():
    type_handler = re.search(
        r"els\.metricType\.addEventListener\('change', \(\) => \{(.*?)\n\}\);",
        SCRIPT,
        re.S,
    )
    profile_handler = re.search(
        r"els\.metricProfile\.addEventListener\('change', \(\) => \{(.*?)\n\}\);",
        SCRIPT,
        re.S,
    )
    assert type_handler and "resetMetricForm();" in type_handler.group(1)
    assert "已取消编辑：指标类型已切换。" in type_handler.group(1)
    assert profile_handler and "resetMetricForm();" in profile_handler.group(1)
    assert "已取消编辑：档案已切换。" in profile_handler.group(1)


def test_drawer_shows_active_profile_chip():
    assert 'id="metrics-profile-chip"' in HTML
    assert ".drawer-profile-chip {" in CSS
    assert "function syncMetricsProfileChip()" in SCRIPT
    assert "档案：${profile}" in SCRIPT
    assert "syncMetricsProfileChip();" in SCRIPT
