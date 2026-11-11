"""就诊/化验记录关联附件数量徽标的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_attachment_ref_counts_loaded():
    assert "attachmentRefCounts" in SCRIPT
    assert "loadAttachmentRefCounts" in SCRIPT
    assert "`/api/attachments/counts?${params.toString()}`" in SCRIPT
    assert "attachmentRefCounts = {" in SCRIPT
    assert "attachmentKindStats" in SCRIPT
    assert "formatAttachmentSize(item.size)" in SCRIPT
    assert "els.attachmentKindStats.hidden = parts.length === 0;" in SCRIPT
    assert "if (els.visitList) renderVisits(filterVisits(visitItemsCache));" in SCRIPT
    assert "if (els.labList) renderLabs(filterLabs(labItemsCache));" in SCRIPT


def test_visit_and_lab_rendering_show_counts():
    assert "attachmentRefCounts.visit[item.id]" in SCRIPT
    assert "attachmentRefCounts.lab[item.id]" in SCRIPT
    assert "`资料 ${attachmentCount}`" in SCRIPT
    assert SCRIPT.count("`资料 ${attachmentCount}`") >= 2


def test_counts_refresh_after_attachment_changes():
    assert "loadAttachmentRefCounts();" in SCRIPT
    assert SCRIPT.count("loadAttachmentRefCounts();") >= 6
    assert "loadVisits();\n  loadAttachmentRefCounts();" in SCRIPT
    assert "loadAttachments();\n  loadAttachmentRefCounts();" in SCRIPT
