"""未服原因前端接线回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_skip_button_and_form_wiring():
    assert "MEDICATION_SKIP_REASON_OPTIONS" in SCRIPT
    assert "openMedicationSkipForm" in SCRIPT
    assert "attachMedicationSkipForm" in SCRIPT
    assert "saveMedicationSkip" in SCRIPT
    assert "记录一次未服" in SCRIPT
    assert "保存未服" in SCRIPT
    assert "未服原因" in SCRIPT
    assert "/api/medications/${item.id}/skip" in SCRIPT
    assert "actions.append(toggle, log, skip, history, remind)" in SCRIPT
    assert "body: JSON.stringify({ reason, note })" in SCRIPT
    assert "medicationSkipDraft" in SCRIPT
    assert "aria-expanded" in SCRIPT


def test_delete_and_undo_ignore_skips():
    assert ".filter((log) => !log.skipped)" in SCRIPT
    assert "const reopenNote = item.skipped" in SCRIPT
    assert "const kind = item.skipped ? '未服记录' : '打卡记录'" in SCRIPT


def test_skip_status_and_counts_rendered():
    assert "今日未服 ${Number(item.skipped_today)} 次" in SCRIPT
    assert "近 ${rangeDays} 天未服 ${Number(item.skipped_range)} 次" in SCRIPT
    assert "（未服）" in SCRIPT
    assert "未服原因：${item.skip_reason_label" in SCRIPT
    assert "未服 ${skippedDoses} 次" in SCRIPT


def test_skip_form_styles_exist():
    assert ".medication-skip-form" in STYLES
