"""附件预览焦点、取消按钮焦点与 PWA 更新的回归测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
API_SOURCE = (ROOT / "api_server.py").read_text(encoding="utf-8")


def test_attachment_shortcut_closes_preview_first():
    # 预览打开时 Ctrl+Shift+K 不能把焦点移到遮罩后的搜索框
    assert SCRIPT.count(
        "els.attachmentPreview && !els.attachmentPreview.hidden"
    ) >= 2
    assert "closeAttachmentPreview();" in SCRIPT


def test_pdf_preview_iframe_not_tabbable_and_guarded():
    assert "frame.tabIndex = -1;" in SCRIPT
    assert "attachmentPreviewFocusGuard" in SCRIPT
    assert "els.attachmentPreview.contains(event.target)" in SCRIPT


def test_hidden_cancel_buttons_restore_focus():
    assert (
        "document.activeElement === els.metricCancel" in SCRIPT
    )
    assert "hadFocus && els.reminderTitle" in SCRIPT
    assert "saveHadFocus && els.quickEntryInput" in SCRIPT


def test_feedback_submit_restores_focus():
    assert "buttons[Number(current)] || buttons[next]" in SCRIPT
    assert "focusTarget.isConnected" in SCRIPT


def test_search_and_timeline_results_are_live_regions():
    assert 'id="history-summary" role="status"' in HTML
    assert 'id="history-empty" role="status"' in HTML
    assert 'id="timeline-summary" role="status"' in HTML


def test_status_recovery_checks_for_new_shell():
    assert "swRegistration.waiting || swRegistration.installing" in SCRIPT
    assert "无法连接本地后端服务，请确认服务已启动" in SCRIPT


def test_build_id_includes_shell_icons():
    assert '"icons/icon-192.png", "icons/icon-512.png"' in API_SOURCE
