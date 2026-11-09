"""规则化健康提示前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
CSS = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_insight_panel_exists_in_health_drawer():
    assert 'id="insight-section"' in HTML
    assert 'id="insight-list"' in HTML
    assert 'id="insight-summary"' in HTML
    assert 'id="insight-empty"' in HTML
    assert "健康提示" in HTML


def test_insights_are_loaded_for_current_profile():
    assert "/api/health/insights?days=30" in SCRIPT
    assert "&profile=${encodeURIComponent(profile)}" in SCRIPT
    assert "loadHealthInsights" in SCRIPT
    assert "renderHealthInsights" in SCRIPT
    assert "healthInsightRequestSeq" in SCRIPT
    assert "INSIGHT_LABELS" not in SCRIPT
    assert "INSIGHT_LEVEL_LABELS" in SCRIPT
    assert "loadHealthInsights();" in SCRIPT
    # 打开抽屉、切换档案与健康记录保存后都会刷新提示
    assert "loadFamilyOverview(),\n    loadHealthInsights()," in SCRIPT


def test_insight_actions_open_matching_sections():
    assert "openInsightSection" in SCRIPT
    assert "openInsightSection(item)" in SCRIPT
    assert "locateHealthRecord({ type: 'metric', profile, metric }, '')" in SCRIPT
    assert "locateHealthRecord({ type: section, profile }, '')" in SCRIPT
    assert "insight-level-badge" in SCRIPT


def test_insight_summary_renders_counts():
    assert "setInsightSummary" in SCRIPT
    assert "危急 ${counts.danger}" in SCRIPT
    assert "需关注 ${counts.warning}" in SCRIPT
    assert "renderHealthInsights" in SCRIPT


def test_insight_styles_and_dark_mode():
    assert ".insight-level-badge" in CSS
    assert ".insight-level-badge.is-danger" in CSS
    assert ".insight-level-badge.is-warning" in CSS
    assert ".insight-level-badge.is-info" in CSS
    assert ".insight-level-badge.is-ok" in CSS
    assert 'html[data-theme="dark"] .insight-level-badge.is-warning' in CSS
    assert 'html[data-theme="dark"] .insight-level-badge.is-ok' in CSS
