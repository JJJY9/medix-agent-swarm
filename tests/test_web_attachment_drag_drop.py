"""附件拖拽上传的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_attachment_dropzone_exists():
    assert 'id="attachment-dropzone"' in HTML
    assert "把文件拖到这里上传" in HTML
    assert 'role="button"' in HTML
    assert 'tabindex="0"' in HTML


def test_attachment_dropzone_wiring():
    assert "attachmentDropzone" in SCRIPT
    assert "setAttachmentDropActive" in SCRIPT
    assert "handleAttachmentDrop" in SCRIPT
    assert "new DataTransfer()" in SCRIPT
    assert "els.attachmentFile.files = transfer.files;" in SCRIPT
    assert "uploadAttachment();" in SCRIPT
    assert "els.attachmentDropzone.addEventListener(" in SCRIPT
    assert "'drop', handleAttachmentDrop" in SCRIPT
    assert "event.key === 'Enter' || event.key === ' '" in SCRIPT
    assert "当前浏览器不支持拖拽上传" in SCRIPT


def test_attachment_dropzone_styles():
    assert ".attachment-dropzone" in STYLES
    assert ".attachment-dropzone.is-dragover" in STYLES
