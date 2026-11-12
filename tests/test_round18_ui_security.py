"""第十八轮审查：前端反馈/E2E 可访问性与索引权限回归测试。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
STYLES = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")


def test_download_revokes_blob_after_delay():
    # Firefox 在 click 后异步读取 blob，立即 revoke 会静默失败
    assert "setTimeout(() => URL.revokeObjectURL(url), 60000)" in APP


def test_banner_skips_identical_aria_live_updates():
    assert "bannerMessageHtml" in APP
    start = APP.index("function showBanner(html)")
    body = APP[start:start + 900]
    assert "bannerMessageHtml !== html" in body


def test_preview_focus_trap_ignores_pdf_iframe():
    assert 'iframe:not([tabindex="-1"])' in APP


def test_metrics_error_scrolls_into_view():
    start = APP.index("function setMetricsStatus")
    body = APP[start:start + 500]
    assert "scrollIntoView({ block: 'nearest' })" in body


def test_stacked_submit_follows_latest_turn():
    assert "requestAnimationFrame(() => scrollThreadToBottom(true))" in APP
    assert "els.thread.scrollIntoView({ behavior: 'smooth', block: 'start' })" not in APP


def test_jump_latest_below_composer_on_narrow_screens():
    marker = ".jump-latest {\n    display: inline-flex;"
    block = STYLES[STYLES.index(marker):STYLES.index(marker) + 400]
    assert "z-index: 1" in block
