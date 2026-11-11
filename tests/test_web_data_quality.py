"""健康数据质量面板的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_quality_panel_exists():
    assert 'id="quality-section"' in HTML
    assert 'id="quality-summary"' in HTML
    assert 'id="quality-list"' in HTML
    assert 'id="quality-empty"' in HTML
    assert 'id="quality-empty" hidden role="status"' in HTML
    assert "数据质量" in HTML


def test_quality_loaded_with_current_profile():
    assert "/api/health/data-quality?profile=" in SCRIPT
    assert "loadDataQuality" in SCRIPT
    assert "renderDataQuality" in SCRIPT
    assert "dataQualityRequestSeq" in SCRIPT
    assert "loadDataQuality();" in SCRIPT
    # 只在抽屉打开时扫描，指标类型/时间范围切换不再触发全量检查
    assert "if (!metricsOpen) return;" in SCRIPT
    assert "loadHealthInsights();\n  loadDataQuality();" not in SCRIPT
    assert "resetMetricForm();" in SCRIPT
    assert "resetQuickEntryUndo();\n  loadDataQuality();" in SCRIPT
    assert "refreshMetrics();\n  loadDataQuality();" in SCRIPT
    assert (
        "loadFamilyOverview(),\n    loadHealthInsights(),\n"
        "    loadDataQuality(),"
        in SCRIPT
    )


def test_quality_renders_summary_empty_and_failure_states():
    assert "`· ${issues.length} 项`" in SCRIPT
    assert "'· 良好'" in SCRIPT
    assert "'· 加载失败'" in SCRIPT
    assert "数据质量检查失败，请稍后重试。" in SCRIPT
    assert "els.qualityEmpty.hidden = issues.length > 0;" in SCRIPT


def test_quality_renders_issues_and_links():
    assert "insight-level-badge is-${item.level" in SCRIPT
    assert "INSIGHT_LEVEL_LABELS[item.level] || '提示'" in SCRIPT
    assert "detail.textContent = item.detail" in SCRIPT
    assert "hint.textContent = item.hint" in SCRIPT
    assert "open.textContent = '查看'" in SCRIPT
    assert "openInsightSection(item)" in SCRIPT
    assert "暂无数据质量问题" in SCRIPT
