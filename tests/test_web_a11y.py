"""会诊日志 live region 的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
STYLES = (WEB_DIR / "styles.css").read_text(encoding="utf-8")


def test_chat_live_region_is_scoped():
    assert 'role="log" aria-live="off"' in HTML
    assert 'id="chat-live"' in HTML
    assert "announceChat('会诊意见已生成')" in SCRIPT
    assert "announceChat('已停止生成')" in SCRIPT
    assert "announceChat('会诊中断')" in SCRIPT
    # 不再把整页问诊日志恢复为 polite
    assert "els.thread.setAttribute('aria-live', 'polite')" not in SCRIPT


def test_drawer_head_wraps_for_long_profile_names():
    assert "flex-wrap: wrap" in STYLES
    assert "max-width: 45%" in STYLES
    assert "text-overflow: ellipsis" in STYLES
    assert ".drawer-head-actions" in STYLES


def test_kb_editor_escape_exits_edit_and_keeps_focus():
    assert 'aria-label="编辑文档内容"' in HTML
    assert "let previewEditOriginal = '';" in SCRIPT
    assert "previewEditOriginal = els.kbPreviewEditor.value;" in SCRIPT
    assert "放弃未保存的修改" in SCRIPT
    assert "if (kbOpen && previewEditing) {" in SCRIPT
    # Esc 分支在关闭抽屉之前处理编辑态
    escape_pos = SCRIPT.index("if (e.key === 'Escape') {")
    close_pos = SCRIPT.index("if (kbOpen) closeKb(true);", escape_pos)
    edit_pos = SCRIPT.index("if (kbOpen && previewEditing) {", escape_pos)
    assert edit_pos < close_pos
