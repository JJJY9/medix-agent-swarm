"""流式输出性能与滚动跟随的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_streaming_appends_text_instead_of_rewriting():
    assert (
        "streamBody.insertAdjacentText('beforeend', event.delta || '')"
        in SCRIPT
    )
    assert "streamBody.textContent = streamBuffer" not in SCRIPT


def test_manual_scroll_pauses_auto_follow():
    start = SCRIPT.index("els.thread.addEventListener('scroll'")
    snippet = SCRIPT[start:start + 500]
    assert "userPausedFollow = true;" in snippet
    assert "els.jumpLatest.hidden = false;" in snippet

    window_start = SCRIPT.index("window.addEventListener('scroll'")
    window_snippet = SCRIPT[window_start:window_start + 600]
    assert "else if (isStacked())" in window_snippet
    assert "userPausedFollow = true;" in window_snippet
