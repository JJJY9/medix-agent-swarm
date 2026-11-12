"""全局健康搜索前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
CSS = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_search_ui_exists():
    assert 'id="health-search-toggle"' in HTML
    assert 'id="health-search-input"' in HTML
    assert 'id="health-search-results"' in HTML
    assert 'id="health-search-status"' in HTML
    assert 'aria-keyshortcuts="Control+Shift+K Meta+Shift+K"' in HTML


def test_search_requests_scoped_endpoint():
    assert "/api/health/search?q=" in SCRIPT
    assert "&limit=200&profile=${encodeURIComponent(profile)}" in SCRIPT
    assert "runHealthSearch" in SCRIPT
    assert "renderHealthSearchResults" in SCRIPT
    assert "renderHealthSearchItem" in SCRIPT
    assert "HEALTH_SEARCH_LABELS" in SCRIPT
    assert "attachment: '资料'" in SCRIPT
    assert "event.isComposing" in SCRIPT


def test_attachment_result_has_preview_action():
    assert "item.type === 'attachment' && item.id" in SCRIPT
    assert "preview.textContent = '预览';" in SCRIPT
    assert "`预览附件：${item.title || ''}`" in SCRIPT
    assert "openAttachmentFile({" in SCRIPT
    assert "mime: item.mime || ''" in SCRIPT
    assert "event.stopPropagation();" in SCRIPT


def test_result_click_locates_record():
    assert "HEALTH_SECTION_TARGETS = {" in SCRIPT
    assert "async function locateHealthRecord" in SCRIPT
    assert "target.loader()" in SCRIPT
    for section_id in (
        "symptom-section",
        "vaccination-section",
        "appointment-section",
        "lab-section",
        "allergy-section",
        "care-section",
        "visit-section",
        "condition-section",
        "medication-section",
    ):
        assert section_id in SCRIPT
    assert "openReminders()" in SCRIPT
    assert "item.type === 'attachment'" in SCRIPT
    assert "els.attachmentFilterQ.value = item.title || query || '';" in SCRIPT
    assert "attachments-section" in SCRIPT
    assert "已定位附件" in SCRIPT
    assert "els.remindersShowDone.checked = Boolean(item.done)" in SCRIPT
    assert "scrollIntoView({ block: 'start' })" in SCRIPT


def test_locate_aligns_section_filters_with_search_fields():
    assert "item.abnormal_high ? '偏高' : '偏低'" in SCRIPT
    assert "ALLERGY_SEVERITY_LABELS[item.severity]" in SCRIPT
    assert "CONDITION_STATUS_LABELS[item.status]" in SCRIPT
    assert "item.active ? '在用' : '停用'" in SCRIPT


def test_metric_locate_widens_range_or_opens_timeline():
    assert "selectCalendarDay(atDate)" in SCRIPT
    assert "els.metricRange.value !== '90'" in SCRIPT


def test_truncation_is_reported_to_user():
    assert "显示 ${results.length} 条" in SCRIPT


def test_search_scope_is_visible():
    assert "const scopeText = profile ? `档案「${profile}」` : '未指定档案';" in SCRIPT
    assert "`${scopeText} · 共 ${total} 条`" in SCRIPT
    assert "`${scopeText}没有匹配「${query}」的记录。`" in SCRIPT


def test_deep_link_and_shortcut():
    assert "action === 'search'" in SCRIPT
    assert "await runHealthSearch(searchQuery)" in SCRIPT
    assert "&& e.shiftKey" in SCRIPT
    assert "openHealthSearch" in SCRIPT


def test_search_styles():
    assert ".health-search {" in CSS
    assert ".health-search-status" in CSS
    assert ".health-search-results" in CSS
    assert ".search-result" in CSS
    assert ".search-result mark" in CSS
