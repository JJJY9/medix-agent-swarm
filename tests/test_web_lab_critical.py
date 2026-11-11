"""化验危急值前端接线回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_lab_list_renders_critical_badge():
    assert "lab-critical-badge" in SCRIPT
    assert "flag.textContent = '危急值'" in SCRIPT
    assert "(item.critical_alerts || []).length" in SCRIPT
    assert "flag.title = item.critical_alerts[0].message" in SCRIPT


def test_lab_summary_and_search_include_critical():
    assert "· 危急值 ${critical} 项" in SCRIPT
    assert "(item.critical_alerts || []).length ? '危急值' : ''" in SCRIPT


def test_lab_save_shows_critical_message():
    assert "const criticalAlerts = saved.critical_alerts || [];" in SCRIPT
    assert "criticalAlerts[0].message" in SCRIPT


def test_weekly_summary_mentions_critical_labs():
    assert "labText += `，危急值 ${labs.critical} 项`" in SCRIPT


def test_family_overview_shows_critical_labs():
    assert "化验危急值 ${labs.critical} 项" in SCRIPT
    assert "family-chip-alert" in SCRIPT
    assert "parts.push(`化验危急值 ${labs.critical} 项`)" in SCRIPT


def test_critical_badge_styles_exist():
    assert ".lab-critical-badge" in STYLES
    assert "html[data-theme=\"dark\"] .lab-critical-badge" in STYLES
