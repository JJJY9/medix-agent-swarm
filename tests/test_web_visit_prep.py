"""复诊准备单前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_visit_prep_panel_exists():
    assert 'id="visit-prep-section"' in HTML
    assert 'id="visit-prep-summary"' in HTML
    assert 'id="visit-prep-days"' in HTML
    assert 'id="visit-prep-generate"' in HTML
    assert 'id="visit-prep-copy"' in HTML
    assert 'id="visit-prep-export"' in HTML
    assert 'id="visit-prep-print"' in HTML
    assert '>打印<' in HTML
    assert 'id="visit-prep-status"' in HTML
    assert 'id="visit-prep-body"' in HTML
    assert 'id="visit-prep-empty"' in HTML
    assert "不构成医疗建议" in HTML


def test_visit_prep_fetches_and_renders():
    assert "/api/health/visit-prep?profile=" in SCRIPT
    assert "+ `&days=${days}`" in SCRIPT
    assert "loadVisitPrep" in SCRIPT
    assert "renderVisitPrep" in SCRIPT
    assert "requestSeq !== visitPrepRequestSeq" in SCRIPT
    assert SCRIPT.count("requestSeq !== visitPrepRequestSeq") >= 2
    assert "visitPrepRequestSeq += 1;" in SCRIPT
    assert "visitPrepCache" in SCRIPT
    assert "cache.profile !== profile || cache.days !== days" in SCRIPT
    assert SCRIPT.count(
        "cache.profile !== profile || cache.days !== days"
    ) >= 2
    assert "选择患者档案后生成准备单。" in SCRIPT


def test_visit_prep_sections_and_questions():
    assert "需要告知医生的异常" in SCRIPT
    assert "上次就诊" in SCRIPT
    assert "目前用药（" in SCRIPT
    assert "过敏与慢病" in SCRIPT
    assert "待办与复查" in SCRIPT
    assert "携带资料（" in SCRIPT
    assert "attachment_count" in SCRIPT
    assert "另有 ${attachmentCount - attachmentLines.length} 份未列出" in SCRIPT
    assert "建议询问医生" in SCRIPT
    assert "visit-prep-questions" in SCRIPT
    assert "危急值" in SCRIPT
    assert "item.target_direction === 'low'" in SCRIPT
    assert "低于目标" in SCRIPT
    assert "已生成：" in SCRIPT


def test_visit_prep_copy_export_and_reset():
    assert "copyText(cache.data.text || '')" in SCRIPT
    assert "已复制复诊准备单。" in SCRIPT
    assert "medix-visit-prep-${stamp}.md" in SCRIPT
    assert "已导出复诊准备单 Markdown。" in SCRIPT
    assert "准备单尚未生成，请先点击生成。" in SCRIPT
    assert "function resetVisitPrep()" in SCRIPT
    assert "function setMetricProfileValue(value)" in SCRIPT
    assert "setMetricProfileValue(activeProfile);" in SCRIPT
    assert "if (current !== nextProfile) {" in SCRIPT
    assert "准备单生成失败，请稍后重试。" in SCRIPT
    assert "els.visitPrepGenerate.addEventListener('click', loadVisitPrep);" in SCRIPT
    assert "els.visitPrepDays.addEventListener('change', resetVisitPrep);" in SCRIPT
    assert "resetVisitPrep();" in SCRIPT


def test_visit_prep_gating_and_disabled_state():
    assert "if (visitPrepLoading) return;" in SCRIPT
    assert "els.visitPrepCopy.disabled = false;" in SCRIPT
    assert "els.visitPrepExport.disabled = false;" in SCRIPT
    assert "els.visitPrepCopy.disabled = true;" in SCRIPT
    assert "els.visitPrepExport.disabled = true;" in SCRIPT
    assert "if (!profile) {" in SCRIPT


def test_visit_prep_print_wiring():
    assert "printVisitPrep" in SCRIPT
    assert "/api/health/visit-prep/print?profile=" in SCRIPT
    assert "打开打印版失败" in SCRIPT
    assert "请允许弹窗后重试" in SCRIPT
    assert "可使用浏览器菜单打印或另存为 PDF" in SCRIPT
    assert "els.visitPrepPrint.addEventListener('click', printVisitPrep);" in SCRIPT
    assert "URL.revokeObjectURL(url);" in SCRIPT


def test_visit_prep_styles_exist():
    assert ".visit-prep-heading" in STYLES
    assert ".visit-prep-questions" in STYLES
    assert "#visit-prep-status.error" in STYLES
