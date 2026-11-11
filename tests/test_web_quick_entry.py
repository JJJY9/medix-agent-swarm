"""一句话记录前端接线回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_quick_entry_panel_exists():
    assert 'id="quick-entry"' in HTML
    assert 'id="quick-entry-input"' in HTML
    assert 'id="quick-entry-parse"' in HTML
    assert 'id="quick-entry-save"' in HTML
    assert 'id="quick-entry-cancel"' in HTML
    assert 'id="quick-entry-undo"' in HTML
    assert 'id="quick-entry-preview"' in HTML
    assert 'id="quick-entry-status"' in HTML
    assert "确认保存" in HTML


def test_quick_entry_calls_parse_endpoint():
    assert "/api/metrics/quick-parse" in SCRIPT
    assert "parseQuickEntry" in SCRIPT
    assert "renderQuickEntryPreview" in SCRIPT
    assert "quickEntryRequestSeq" in SCRIPT
    assert "isComposing" in SCRIPT


def test_quick_entry_requires_confirmation_and_supports_undo():
    assert "async function saveQuickEntry()" in SCRIPT
    assert "async function undoQuickEntry()" in SCRIPT
    assert "/api/metrics/${quickEntryLastSavedId}" in SCRIPT
    assert "quickEntryLastSavedId" in SCRIPT
    assert "res.status !== 404" in SCRIPT
    assert "quickEntryUndoing" in SCRIPT
    assert "已解析，请确认后保存。" in SCRIPT
    assert "已撤销刚才的记录。" in SCRIPT


def test_quick_entry_invalidates_stale_previews():
    assert "els.quickEntryInput.addEventListener('input'" in SCRIPT
    assert "quickEntryRequestSeq += 1;" in SCRIPT
    assert "resetQuickEntryUndo();" in SCRIPT
    assert 'aria-label="撤销刚才通过快速记录保存的指标"' in HTML


def test_quick_entry_shows_critical_alerts():
    assert "data.critical_alerts || []" in SCRIPT
    assert "该数值达到危急值" in SCRIPT


def test_quick_entry_clears_on_profile_change():
    assert "clearQuickEntryPreview();\n  setQuickEntryStatus('');" in SCRIPT


def test_quick_entry_styles_exist():
    assert ".quick-entry {" in STYLES
    assert ".quick-entry-actions" in STYLES
    assert ".quick-entry-status.error" in STYLES
    assert "color: var(--triage-1);" in STYLES
