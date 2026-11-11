"""就医资料附件前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_attachment_panel_exists():
    assert 'id="attachments-section"' in HTML
    assert 'id="attachments-summary"' in HTML
    assert 'id="attachment-kind"' in HTML
    assert 'id="attachment-file"' in HTML
    assert 'id="attachment-note"' in HTML
    assert 'id="attachment-upload"' in HTML
    assert 'id="attachment-status"' in HTML
    assert 'id="attachment-list"' in HTML
    assert 'id="attachment-more"' in HTML
    assert 'id="attachment-empty"' in HTML
    assert "JPG/PNG/WebP/PDF" in HTML
    assert "可多选" in HTML
    assert 'id="attachment-file" multiple' in HTML
    assert "最多 5 个" in HTML


def test_attachment_upload_and_list_wiring():
    assert "new URLSearchParams({" in SCRIPT
    assert "limit: String(ATTACHMENT_PAGE_SIZE)" in SCRIPT
    assert "offset: String(offset)" in SCRIPT
    assert "loadAttachments" in SCRIPT
    assert "renderAttachments" in SCRIPT
    assert "attachmentRequestSeq" in SCRIPT
    assert "requestSeq !== attachmentRequestSeq" in SCRIPT
    assert "new FormData()" in SCRIPT
    assert "form.append('profile', profile);" in SCRIPT
    assert "form.append('kind', kind);" in SCRIPT
    assert "form.append('file', file);" in SCRIPT
    assert "fetch('/api/attachments'" in SCRIPT


def test_attachment_upload_guards_and_feedback():
    assert "if (attachmentUploading) return;" in SCRIPT
    assert "attachmentUploading = true;" in SCRIPT
    assert "file.size > ATTACHMENT_MAX_BYTES" in SCRIPT
    assert "ATTACHMENT_EXTENSIONS.includes(extension)" in SCRIPT
    assert "正在上传 ${index + 1}/${files.length}…" in SCRIPT
    assert "els.attachmentUpload.addEventListener('click', uploadAttachment);" in SCRIPT
    assert "ATTACHMENT_MAX_BATCH" in SCRIPT
    assert "Array.from(" in SCRIPT
    assert "一次最多上传" in SCRIPT
    assert "第 ${index + 1} 个文件超过 10 MB" in SCRIPT
    assert "els.metricProfile.disabled = true;" in SCRIPT
    assert "els.metricProfile.disabled = false;" in SCRIPT
    assert "els.attachmentFile.disabled = true;" in SCRIPT
    assert "els.attachmentNote.disabled = false;" in SCRIPT
    assert "关联记录正在加载，请稍后再上传" in SCRIPT
    assert "个失败：" in SCRIPT
    assert "请重新选择失败文件重试" in SCRIPT
    assert "秒后重试" in SCRIPT


def test_attachment_pagination_wiring():
    assert "attachmentHasMore" in SCRIPT
    assert "attachmentLoadingMore" in SCRIPT
    assert "els.attachmentMore.hidden = !attachmentHasMore;" in SCRIPT
    assert "els.attachmentMore.addEventListener(" in SCRIPT
    assert "() => loadAttachments(true)" in SCRIPT
    assert "items.length >= ATTACHMENT_PAGE_SIZE" in SCRIPT


def test_attachment_view_download_delete_wiring():
    assert "/api/attachments/${item.id}/file" in SCRIPT
    assert "URL.createObjectURL(blob)" in SCRIPT
    assert "const opened = window.open(url, '_blank');" in SCRIPT
    assert "if (!opened) {" in SCRIPT
    assert "URL.revokeObjectURL(url);" in SCRIPT
    assert "浏览器拦截了新窗口" in SCRIPT
    assert "anchor.download = item.original_name" in SCRIPT
    assert "fetch(`/api/attachments/${item.id}`, {" in SCRIPT
    assert "删除附件「${item.original_name" in SCRIPT
    assert "附件只保存在本机 data/attachments" in HTML
    assert "不进入整库 JSON 备份" in HTML


def test_attachment_styles_exist():
    assert ".attachment-upload" in STYLES
    assert "#attachment-status.error" in STYLES
    assert ".attachment-more" in STYLES
    assert ".attachment-missing" in STYLES
    assert "item.file_missing" in SCRIPT
    assert "文件缺失，请运行完整性检查" in SCRIPT


def test_attachment_bulk_delete_wiring():
    assert 'id="attachment-select-all"' in HTML
    assert 'id="attachment-delete-selected"' in HTML
    assert "全选当前" in HTML
    assert "attachmentSelectedIds" in SCRIPT
    assert "updateAttachmentSelection" in SCRIPT
    assert "deleteSelectedAttachments" in SCRIPT
    assert "attachment-select" in SCRIPT
    assert "删除选中的 ${ids.length} 份附件" in SCRIPT
    assert "els.attachmentDeleteSelected.addEventListener(" in SCRIPT
    assert "els.attachmentSelectAll.addEventListener(" in SCRIPT
    assert ".attachment-select-all" in STYLES
    assert ".attachment-select {" in STYLES


def test_attachment_export_selected_wiring():
    assert 'id="attachment-export-selected"' in HTML
    assert "导出选中" in HTML
    assert "导出选中（${attachmentSelectedIds.size}）" in SCRIPT
    assert "attachmentExporting || attachmentSelectedIds.size === 0" in SCRIPT
    assert "exportSelectedAttachments" in SCRIPT
    assert "exportAttachmentArchive([...attachmentSelectedIds]);" in SCRIPT
    assert "els.attachmentExportSelected.addEventListener(" in SCRIPT

def test_attachment_size_summary_wiring():
    assert "attachmentTotalCount" in SCRIPT
    assert "attachmentTotalSize" in SCRIPT
    assert "Number(data.total_count)" in SCRIPT
    assert "Number(data.total_size)" in SCRIPT
    assert "`· ${attachmentTotalCount} 份`" in SCRIPT
    assert "formatAttachmentSize(attachmentTotalSize)" in SCRIPT


def test_attachment_note_edit_wiring():
    assert "editAttachmentNote" in SCRIPT
    assert "noteEdit.textContent = '备注'" in SCRIPT
    assert "`编辑附件备注：${item.original_name}`" in SCRIPT
    assert "method: 'PATCH'" in SCRIPT
    assert "body: JSON.stringify({ note })" in SCRIPT
    assert "附件备注已保存。" in SCRIPT
    assert "附件备注不能超过 300 字。" in SCRIPT
