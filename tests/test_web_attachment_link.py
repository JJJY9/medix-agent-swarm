"""就医资料关联就诊/化验记录的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_attachment_ref_select_exists():
    assert 'id="attachment-ref-wrap"' in HTML
    assert 'id="attachment-ref"' in HTML
    assert ">关联记录" in HTML
    assert '>不关联<' in HTML


def test_attachment_ref_options_loaded_by_kind_and_profile():
    assert "loadAttachmentRefOptions" in SCRIPT
    assert "attachmentRefRequestSeq" in SCRIPT
    assert "`/api/visits?${params.toString()}`" in SCRIPT
    assert "`/api/lab-results?${params.toString()}`" in SCRIPT
    assert "params.set('profile', profile);" in SCRIPT
    assert "选择患者档案后可关联记录" not in SCRIPT
    assert "暂无可关联记录" in SCRIPT
    assert "关联记录加载失败，可不关联" in SCRIPT
    assert "正在加载关联记录…" in SCRIPT
    assert "params.set('profile', profile);" in SCRIPT
    assert "new URLSearchParams({ limit: '1000' })" in SCRIPT
    assert "if (!metricsOpen) return;" in SCRIPT
    assert (
        "const url = kind === 'visit'\n"
        "    ? `/api/visits?${params.toString()}`\n"
        "    : `/api/lab-results?${params.toString()}`;"
    ) in SCRIPT
    assert (
        "const items = kind === 'visit'\n"
        "      ? (data.visits || [])\n"
        "      : (data.lab_results || []);"
    ) in SCRIPT
    assert SCRIPT.count(
        "requestSeq !== attachmentRefRequestSeq"
    ) >= 2
    assert "attachmentRefLabel(kind, item).slice(0, 60)" in SCRIPT


def test_attachment_upload_sends_ref_id():
    assert (
        "const refId = els.attachmentRef && els.attachmentRefWrap\n"
        "    && !els.attachmentRefWrap.hidden\n"
        "    ? (els.attachmentRef.value || '0')\n"
        "    : '0';"
    ) in SCRIPT
    assert "form.append('ref_id', refId);" in SCRIPT
    assert "els.attachmentKind.addEventListener(" in SCRIPT
    assert "'change', loadAttachmentRefOptions" in SCRIPT
    assert "await loadAttachments();\n    loadAttachmentRefOptions();" not in SCRIPT


def test_record_changes_refresh_attachment_links():
    assert SCRIPT.count("refreshAttachmentReferences();") >= 5
    assert "function refreshAttachmentReferences()" in SCRIPT
    assert "loadAttachmentRefOptions();\n  loadAttachments();" in SCRIPT


def test_attachment_list_marks_linked_records():
    assert (
        "const linkedLabel = item.ref_id\n"
        "      ? (item.kind === 'visit'\n"
        "        ? '已关联就诊'\n"
        "        : item.kind === 'lab' ? '已关联化验' : '已关联')\n"
        "      : '';"
    ) in SCRIPT
    assert "linkedLabel" in SCRIPT
    assert "loadAttachmentRefOptions();\n  loadMetricTargets()" in SCRIPT
    assert "resetVisitPrep();\n    loadAttachmentRefOptions();" in SCRIPT
    assert "loadAttachmentRefOptions();\n  resetVisitPrep();" in SCRIPT
