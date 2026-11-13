"""从就诊/化验记录直接上传关联资料的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_attachment_record_upload_helper():
    assert "openAttachmentUploadForRecord" in SCRIPT
    assert "await openMetrics();" in SCRIPT
    assert "els.attachmentKind.value = kind;" in SCRIPT
    assert "els.attachmentKind.dispatchEvent(new Event('change', { bubbles: true }));" in SCRIPT
    assert "await loadAttachmentRefOptions();" in SCRIPT
    assert "els.attachmentRef.appendChild(option);" in SCRIPT
    assert "els.attachmentRef.value = targetValue;" in SCRIPT
    assert "els.attachmentFile.focus();" in SCRIPT
    assert "attachments-section" in SCRIPT


def test_visit_record_has_attachment_upload_button():
    assert "上传就诊资料：" in SCRIPT
    assert "openAttachmentUploadForRecord('visit', item)" in SCRIPT
    assert "actions.append(prep, upload, edit, del);" in SCRIPT


def test_lab_record_has_attachment_upload_button():
    assert "上传化验单：" in SCRIPT
    assert "openAttachmentUploadForRecord('lab', item)" in SCRIPT
    assert "actions.append(upload, edit, del);" in SCRIPT
