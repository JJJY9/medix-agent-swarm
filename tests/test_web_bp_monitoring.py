"""家庭血压监测前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_bp_monitoring_panel_exists():
    assert 'id="bp-monitoring-section"' in HTML
    assert 'id="bp-monitoring-summary"' in HTML
    assert 'id="bp-monitoring-start"' in HTML
    assert 'id="bp-monitoring-days"' in HTML
    assert 'id="bp-monitoring-copy"' in HTML
    assert 'id="bp-monitoring-body"' in HTML
    assert 'id="bp-monitoring-empty"' in HTML
    assert 'id="bp-monitoring-status"' in HTML


def test_bp_monitoring_fetches_and_renders():
    assert "/api/health/bp-monitoring?" in SCRIPT
    assert "loadBpMonitoring" in SCRIPT
    assert "renderBpMonitoring" in SCRIPT
    assert "bpMonitoringRequestSeq" in SCRIPT
    assert "if (!els.bpMonitoringBody || !metricsOpen) return;" in SCRIPT
    assert "bpMonitoringDefaultStart" in SCRIPT
    assert "URLSearchParams({ days: String(days) })" in SCRIPT
    assert "params.set('profile', profile);" in SCRIPT
    assert "completion_rate" in SCRIPT
    assert "未测时段" in SCRIPT
    assert "仅统计家庭测量记录完成度" in SCRIPT
    assert "统计至 ${data.end}" in SCRIPT


def test_bp_monitoring_refresh_and_copy_wiring():
    assert "loadDataQuality();\n  loadBpMonitoring();" in SCRIPT
    assert (
        "loadBpMonitoring();\n  loadAttachments();\n"
        "  loadAttachmentRefOptions();\n  resetVisitPrep();"
        "\n  quickEntryRequestSeq"
    ) in SCRIPT
    assert (
        "loadBpMonitoring();\n  loadAttachments();\n"
        "  loadAttachmentRefOptions();\n  loadMetricTargets()"
    ) in SCRIPT
    assert "await refreshMetrics();\n    loadBpMonitoring();" in SCRIPT
    assert "loadBpMonitoring();\n    refreshOverview();" in SCRIPT
    assert "els.bpMonitoringCopy.addEventListener" in SCRIPT
    assert "copyBpMonitoring" in SCRIPT
    assert "bpMonitoringCache.profile === profile" in SCRIPT
    assert "'· 加载失败'" in SCRIPT


def test_bp_monitoring_start_auto_and_listeners():
    assert "bpMonitoringStartAuto" in SCRIPT
    assert "bpMonitoringStartAuto = true;" in SCRIPT
    assert "bpMonitoringStartAuto = false;" in SCRIPT
    assert "els.bpMonitoringDays.addEventListener('change'" in SCRIPT
    assert "els.bpMonitoringStart.addEventListener('change'" in SCRIPT


def test_bp_monitoring_styles_exist():
    assert ".bp-monitoring-controls" in STYLES
    assert "#bp-monitoring-status.error" in STYLES
