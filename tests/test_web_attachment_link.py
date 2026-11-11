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
    assert "选择患者档案后可关联记录" in SCRIPT
    assert "暂无可关联记录" in SCRIPT
    assert "关联记录加载失败，可不关联" in SCRIPT
    assert "attachmentRefLabel(kind, item).slice(0, 100)" in SCRIPT


def test_attachment_upload_sends_ref_id():
    assert "const refId = els.attachmentRef && els.attachmentRefWrap" in SCRIPT
    assert "form.append('ref_id', refId);" in SCRIPT
    assert "els.attachmentKind.addEventListener(" in SCRIPT
    assert "'change', loadAttachmentRefOptions" in SCRIPT


def test_attachment_list_marks_linked_records():
    assert "已关联就诊" in SCRIPT
    assert "已关联化验" in SCRIPT
    assert "linkedLabel" in SCRIPT
    assert "loadAttachmentRefOptions();\n  loadMetricTargets()" in SCRIPT
    assert "loadAttachmentRefOptions();\n  resetVisitPrep();" in SCRIPT
