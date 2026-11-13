"""首页速览健康提示卡片的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
CSS = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_home_glance_insight_card_exists():
    assert 'id="home-glance-insight"' in HTML
    assert "homeGlanceInsight" in SCRIPT


def test_home_glance_loads_insights_for_current_profile():
    assert "/api/health/insights?days=30" in SCRIPT
    assert "const insightsUrl = profile" in SCRIPT
    assert "&profile=${encodeURIComponent(profile)}" in SCRIPT
    assert "fetch(insightsUrl).catch(() => null)" in SCRIPT
    assert "insightsPayload" in SCRIPT
    assert "insightsPayload, profile" in SCRIPT
    assert "INSIGHT_LEVEL_LABELS[top.level]" in SCRIPT
    assert "健康提示 · ${profile}" in SCRIPT
    assert (
        "top.level === 'danger' || top.level === 'warning'"
        in SCRIPT
    )
    # 空数据/请求失败时隐藏卡片并清除高亮
    assert "els.homeGlanceInsight.hidden = true" in SCRIPT
    assert "els.homeGlanceInsight.classList.remove('is-alert')" in SCRIPT


def test_home_glance_refreshes_after_profile_changes():
    assert (
        "applyPatientContext({});\n  }\n  loadHomeGlance();"
        in SCRIPT
    )
    assert (
        "  loadFamilyOverview();\n  loadHomeGlance();\n}"
        in SCRIPT
    )
    assert (
        "detachSessionForProfileChange();\n    loadFamilyOverview();\n"
        "    loadHomeGlance();"
        in SCRIPT
    )
    assert (
        "refreshProfileOptions('');\n    loadFamilyOverview();\n"
        "    loadHomeGlance();"
        in SCRIPT
    )


def test_home_glance_insight_opens_panel():
    assert "els.insightSection.open = true" in SCRIPT
    assert "els.metricProfile.value = profile;" in SCRIPT
    assert "thread.scrollTo({ top, behavior: 'smooth' });" in SCRIPT
    assert "els.homeGlanceInsight.classList.toggle" in SCRIPT


def test_metric_changes_refresh_family_overview():
    assert "loadHomeGlance();\n  loadFamilyOverview();\n}" in SCRIPT


def test_home_glance_insight_style():
    assert ".home-glance-card.is-alert .home-glance-value" in CSS


def test_home_glance_weekly_summary_skips_previous_window():
    # 首页小结不展示环比，请求时跳过上一周期数据
    assert (
        "fetch('/api/reports/weekly?include_previous=false')"
        in SCRIPT
    )
