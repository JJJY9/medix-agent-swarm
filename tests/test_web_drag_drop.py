"""拖入文件时的全局默认行为防护测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_file_drag_outside_dropzones_is_prevented():
    assert "for (const type of ['dragover', 'drop'])" in SCRIPT
    assert (
        "Array.from(event.dataTransfer.types || []).includes('Files')"
        in SCRIPT
    )
    assert "event.preventDefault();" in SCRIPT
