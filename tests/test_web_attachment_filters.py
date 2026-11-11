"""附件列表筛选与搜索前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_attachment_filter_controls_exist():
    assert 'id="attachment-filter-kind"' in HTML
    assert 'id="attachment-filter-linked"' in HTML
    assert '>已关联<' in HTML
    assert '>未关联<' in HTML
    assert 'id="attachment-filter-q"' in HTML
    assert '>全部类型<' in HTML
    assert 'placeholder="搜索文件名、备注或关联记录"' in HTML


def test_attachment_filters_are_sent_to_api():
    assert "attachmentFilterKind" in SCRIPT
    assert "attachmentFilterQ" in SCRIPT
    assert "if (filterKind) params.set('kind', filterKind);" in SCRIPT
    assert "if (filterLinked) params.set('linked', filterLinked);" in SCRIPT
    assert "attachmentFilterLinked" in SCRIPT
    assert "els.attachmentFilterLinked.addEventListener(" in SCRIPT
    assert "if (filterQuery) params.set('q', filterQuery);" in SCRIPT
    assert "attachmentFilterTimer" in SCRIPT
    assert "scheduleAttachmentFilterSearch" in SCRIPT
    assert "event.isComposing" in SCRIPT
    assert "'compositionend', scheduleAttachmentFilterSearch" in SCRIPT
    assert "els.attachmentFilterQ.addEventListener('input'" in SCRIPT
    assert "els.attachmentFilterKind.addEventListener(" in SCRIPT


def test_attachment_date_range_filter_wiring():
    assert 'id="attachment-filter-start"' in HTML
    assert 'id="attachment-filter-end"' in HTML
    assert "附件起始日期" in HTML
    assert "附件结束日期" in HTML
    assert "attachmentFilterStart" in SCRIPT
    assert "attachmentFilterEnd" in SCRIPT
    assert "params.set('start', filterStart);" in SCRIPT
    assert "params.set('end', filterEnd);" in SCRIPT
    assert "onAttachmentDateChange" in SCRIPT
    assert "开始日期不能晚于结束日期。" in SCRIPT
    assert "els.attachmentFilterStart.addEventListener(" in SCRIPT
    assert "els.attachmentFilterEnd.addEventListener(" in SCRIPT
    assert ".attachment-date" in STYLES


def test_attachment_filter_empty_state_and_styles():
    assert "没有符合条件的资料。" in SCRIPT
    assert "const hasFilter = Boolean(" in SCRIPT
    assert "filtered: hasFilter," in SCRIPT
    assert "emptyText: hasFilter" in SCRIPT
    assert "options.filtered ? '（已筛选）' : ''" in SCRIPT
    assert "attachmentRequestSeq += 1;" in SCRIPT
    assert ".attachment-filters" in STYLES
    assert ".attachment-filters input[type=\"search\"]" in STYLES
