"""附件粘贴上传的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_paste_upload_wiring():
    assert "handleAttachmentPaste" in SCRIPT
    assert "document.addEventListener('paste', handleAttachmentPaste);" in SCRIPT
    assert "event.clipboardData && event.clipboardData.items" in SCRIPT
    assert "item.kind !== 'file'" in SCRIPT
    assert "'image/png': 'png'" in SCRIPT
    assert "'image/jpeg': 'jpg'" in SCRIPT
    assert "'image/webp': 'webp'" in SCRIPT
    assert "`粘贴图片-${stamp}-${files.length + 1}.${extension}`" in SCRIPT
    assert "els.attachmentFile.files = transfer.files;" in SCRIPT


def test_paste_upload_ignores_form_targets_and_closed_panel():
    assert "target.closest('input, textarea, select, [contenteditable=\"true\"]')" in SCRIPT
    assert "if (!metricsOpen || attachmentUploading) return;" in SCRIPT
    assert "if (!section || !section.open) return;" in SCRIPT
    assert "当前浏览器不支持粘贴上传" in SCRIPT
