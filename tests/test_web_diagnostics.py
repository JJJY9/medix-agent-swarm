"""系统自检附件统计的前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_diagnostics_shows_attachment_storage():
    assert "attachment_dir_exists" in SCRIPT
    assert "attachment_count" in SCRIPT
    assert "attachment_size" in SCRIPT
    assert "'就医资料'" in SCRIPT
    assert "formatBytes(runtime.attachment_size || 0)" in SCRIPT
    assert "目录未生成" in SCRIPT
