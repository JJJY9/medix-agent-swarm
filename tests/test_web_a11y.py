"""会诊日志 live region 的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_chat_live_region_is_scoped():
    assert 'role="log" aria-live="off"' in HTML
    assert 'id="chat-live"' in HTML
    assert "announceChat('会诊意见已生成')" in SCRIPT
    assert "announceChat('已停止生成')" in SCRIPT
    assert "announceChat('会诊中断')" in SCRIPT
    # 不再把整页问诊日志恢复为 polite
    assert "els.thread.setAttribute('aria-live', 'polite')" not in SCRIPT
