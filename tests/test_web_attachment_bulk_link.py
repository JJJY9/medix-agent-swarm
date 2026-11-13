"""附件批量关联/取消关联的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_attachment_bulk_link_controls_exist():
    assert 'id="attachment-link-selected"' in HTML
    assert 'id="attachment-link-bar"' in HTML
    assert 'id="attachment-link-kind"' in HTML
    assert 'id="attachment-link-ref"' in HTML
    assert 'id="attachment-link-apply"' in HTML
    assert 'id="attachment-link-unlink"' in HTML
    assert 'id="attachment-link-cancel"' in HTML
    assert "批量关联" in HTML
    assert ">就诊记录<" in HTML
    assert ">化验记录<" in HTML
    assert ">确认关联<" in HTML
    assert ">取消关联<" in HTML


def test_attachment_bulk_link_wiring():
    assert "selectedAttachmentItems" in SCRIPT
    assert "openAttachmentLinkBar" in SCRIPT
    assert "loadAttachmentLinkRefOptions" in SCRIPT
    assert "applyAttachmentLink" in SCRIPT
    assert "unlinkSelectedAttachments" in SCRIPT
    assert "closeAttachmentLinkBar" in SCRIPT
    assert "attachmentLinkRefRequestSeq" in SCRIPT
    assert "attachmentLinking" in SCRIPT
    assert "fetch('/api/attachments/link'" in SCRIPT
    assert "JSON.stringify({ ids: chunk, ref_id: refId })" in SCRIPT
    assert "关联选中（${selectedItems.length}）" in SCRIPT
    assert "只有同类型的就诊资料或化验单可以批量关联。" in SCRIPT
    assert "请选择要关联的就诊或化验记录。" in SCRIPT
    assert "已关联 ${updated || ids.length} 份附件。" in SCRIPT
    assert "已取消 ${updated || ids.length} 份附件的关联。" in SCRIPT
    assert "取消选中的 ${ids.length} 份附件的关联？" in SCRIPT
    assert "els.attachmentLinkSelected.addEventListener(" in SCRIPT
    assert "els.attachmentLinkApply.addEventListener(" in SCRIPT
    assert "els.attachmentLinkUnlink.addEventListener(" in SCRIPT
    assert "els.attachmentLinkCancel.addEventListener(" in SCRIPT


def test_attachment_bulk_link_options_follow_selected_kind():
    assert "els.attachmentLinkKind.value = kind;" in SCRIPT
    assert "new URLSearchParams({ limit: '1000' })" in SCRIPT
    assert "`/api/visits?${params.toString()}`" in SCRIPT
    assert "`/api/lab-results?${params.toString()}`" in SCRIPT
    assert "attachmentRefLabel(kind, item).slice(0, 60)" in SCRIPT
    assert "请选择${kind === 'visit' ? '就诊' : '化验'}记录" in SCRIPT
    assert "res.attachmentLinkRef.focus()" not in SCRIPT
    assert "els.attachmentLinkRef.focus()" in SCRIPT


def test_attachment_bulk_link_styles_and_selection_state():
    assert ".attachment-link-bar" in STYLES
    assert ".attachment-link-bar select" in STYLES
    assert ".attachment-link-bar[hidden]" in STYLES
    assert "const canLink = selectedItems.length > 0" in SCRIPT
    assert "selectedItems.every((item) => item.kind === linkKind)" in SCRIPT
    assert "if (!canLink && els.attachmentLinkBar)" in SCRIPT
