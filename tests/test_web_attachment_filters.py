"""附件列表筛选与搜索前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_attachment_filter_controls_exist():
    assert 'id="attachment-filter-kind"' in HTML
    assert 'id="attachment-filter-q"' in HTML
    assert '>全部类型<' in HTML
    assert 'placeholder="搜索文件名或备注"' in HTML


def test_attachment_filters_are_sent_to_api():
    assert "attachmentFilterKind" in SCRIPT
    assert "attachmentFilterQ" in SCRIPT
    assert "if (filterKind) params.set('kind', filterKind);" in SCRIPT
    assert "if (filterQuery) params.set('q', filterQuery);" in SCRIPT
    assert "attachmentFilterTimer" in SCRIPT
    assert "els.attachmentFilterQ.addEventListener('input'" in SCRIPT
    assert "els.attachmentFilterKind.addEventListener(" in SCRIPT


def test_attachment_filter_empty_state_and_styles():
    assert "没有符合条件的资料。" in SCRIPT
    assert "emptyText: (filterKind || filterQuery)" in SCRIPT
    assert ".attachment-filters" in STYLES
    assert ".attachment-filters input[type=\"search\"]" in STYLES
