"""附件完整性检查前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_integrity_controls_exist():
    assert 'id="attachment-integrity-check"' in HTML
    assert 'id="attachment-cleanup"' in HTML
    assert 'id="attachment-integrity-status"' in HTML
    assert "检查文件完整性" in HTML
    assert "清理孤儿文件" in HTML


def test_integrity_fetches_and_renders_counts():
    assert "checkAttachmentIntegrity" in SCRIPT
    assert "fetch('/api/attachments/integrity')" in SCRIPT
    assert "attachmentIntegrityRequestSeq" in SCRIPT
    assert "requestSeq !== attachmentIntegrityRequestSeq" in SCRIPT
    assert "缺失 ${missing} 份" in SCRIPT
    assert "孤儿文件 ${orphanTotal} 个" in SCRIPT
    assert "刚写入，暂不清理" in SCRIPT
    assert "els.attachmentCleanup.hidden = attachmentCleanableCount === 0;" in SCRIPT


def test_cleanup_posts_confirmation_and_reports():
    assert "cleanupAttachmentOrphans" in SCRIPT
    assert "attachmentExporting" in SCRIPT
    assert "async function checkAttachmentIntegrity(prefix = '', force = false)" in SCRIPT
    assert "if (!force && (attachmentExporting || attachmentCleaning)) return;" in SCRIPT
    assert "els.attachmentCleanup.disabled = true;" in SCRIPT
    assert "els.attachmentCleanup.disabled = false;" in SCRIPT
    assert "fetch('/api/attachments/cleanup'" in SCRIPT
    assert "body: JSON.stringify({" in SCRIPT
    assert "expected_orphans: attachmentOrphanCount," in SCRIPT
    assert "attachmentCleanableCount" in SCRIPT
    assert "await checkAttachmentIntegrity(summary, true);" in SCRIPT
    assert "|| attachmentCleaning\n    || attachmentExporting" in SCRIPT
    assert "孤儿文件数量已变化，请重新确认" in SCRIPT
    assert SCRIPT.count("refreshAttachmentIntegrityIfVisible();") >= 2
    assert "已清理 ${data.removed || 0} 个孤儿文件" in SCRIPT
    assert "个新文件已保留" in SCRIPT
    assert "清理 ${attachmentCleanableCount} 个没有登记到数据库的附件文件？" in SCRIPT


def test_integrity_listeners_and_styles():
    assert "els.attachmentIntegrityCheck.addEventListener(" in SCRIPT
    assert "'click', checkAttachmentIntegrity" in SCRIPT
    assert "'click', cleanupAttachmentOrphans" in SCRIPT
    assert ".attachment-integrity" in STYLES
    assert "#attachment-integrity-status.error" in STYLES
    assert ".attachment-integrity .metric-meta" in STYLES
    assert "#attachment-export:disabled" in STYLES


def test_attachment_export_zip_wiring():
    assert 'id="attachment-export"' in HTML
    assert "导出全部附件包" in HTML
    assert "exportAttachmentArchive" in SCRIPT
    assert "fetch('/api/attachments/export.zip', {" in SCRIPT
    assert "signal: controller.signal," in SCRIPT
    assert "if (attachmentExporting || attachmentCleaning) return;" in SCRIPT
    assert "medix-attachments-${stamp}.zip" in SCRIPT
    assert "已导出全部档案附件包（共 ${exported} 份" in SCRIPT
    assert "X-Medix-Attachment-Missing" in SCRIPT
    assert "controller.abort()" in SCRIPT
    assert "导出超时，请稍后重试" in SCRIPT
    assert "els.attachmentExport.addEventListener(" in SCRIPT
    assert "'click', exportAttachmentArchive" in SCRIPT
