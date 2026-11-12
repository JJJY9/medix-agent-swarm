"""前端模态态回归：附件预览与抽屉的 Esc/打开顺序。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_escape_closes_preview_before_drawers():
    drawer_escape = SCRIPT.index("if (historyOpen) closeHistory(true);")
    start = SCRIPT.rindex(
        "if (e.key === 'Escape') {", 0, drawer_escape
    )
    block = SCRIPT[start:drawer_escape + len("if (historyOpen)")]
    assert "els.attachmentPreview && !els.attachmentPreview.hidden" in block
    assert block.index("closeAttachmentPreview();") < block.index(
        "if (historyOpen)"
    )


def test_opening_drawers_closes_leftover_preview():
    for marker in (
        "function openHistory",
        "function openKb",
        "async function openReminders",
        "async function openMetrics",
        "function showAuthGate",
    ):
        start = SCRIPT.index(marker)
        block = SCRIPT[start:start + 900]
        assert "closeAttachmentPreview();" in block, marker


def test_reopening_metrics_reruns_active_search():
    start = SCRIPT.index("async function openMetrics")
    block = SCRIPT[start:start + 2000]
    assert (
        "if (healthSearchQuery) {\n"
        "    runHealthSearch(healthSearchQuery);\n"
        "  }"
    ) in block
