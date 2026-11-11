"""按关联记录查看附件的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_linked_attachment_filter_controls_exist():
    assert 'id="attachment-ref-filter"' in HTML
    assert 'id="attachment-ref-filter-label"' in HTML
    assert 'id="attachment-ref-filter-clear"' in HTML
    assert ">清除<" in HTML


def test_linked_attachment_filter_wiring():
    assert "attachmentRefFilter" in SCRIPT
    assert "openAttachmentListForRecord" in SCRIPT
    assert "clearAttachmentRefFilter" in SCRIPT
    assert "params.set('ref_id', String(attachmentRefFilter.refId));" in SCRIPT
    assert "`仅看：${filter.label}`" in SCRIPT
    assert "els.attachmentRefFilter.hidden = !filter;" in SCRIPT
    assert "els.attachmentRefFilterClear.addEventListener(" in SCRIPT
    assert SCRIPT.count("openAttachmentListForRecord(") >= 3


def test_attachment_count_badges_open_filtered_list():
    assert "attachment-count-badge" in SCRIPT
    assert "查看就诊记录关联的 ${attachmentCount} 份资料" in SCRIPT
    assert "查看化验结果关联的 ${attachmentCount} 份资料" in SCRIPT
    assert "openAttachmentListForRecord('visit', item)" in SCRIPT
    assert "openAttachmentListForRecord('lab', item)" in SCRIPT


def test_linked_attachment_filter_styles():
    assert ".attachment-ref-filter" in STYLES
    assert ".attachment-ref-filter[hidden]" in STYLES
    assert ".attachment-count-badge" in STYLES
