"""备份恢复预检前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_restore_runs_preflight_before_confirm():
    assert "/api/backups/${encodeURIComponent(target.name)}/inspect" in SCRIPT
    assert "cache: 'no-store'" in SCRIPT
    assert "备份预检失败" in SCRIPT
    assert "预检：${inspectSummary}" in SCRIPT
    assert "会话 ${counts.sessions || 0}（消息 ${counts.messages || 0}）" in SCRIPT
    assert "指标 ${counts.health_metrics || 0}" in SCRIPT
    assert "化验 ${counts.lab_results || 0}" in SCRIPT


def test_restore_blocks_failed_checksum_and_warns_missing():
    assert "inspectInfo.checksum_ok === false" in SCRIPT
    assert "已停止恢复" in SCRIPT
    assert "inspectInfo.checksum_present === false" in SCRIPT
    assert "该备份没有校验信息" in SCRIPT


def test_restore_inspect_happens_before_restore_call():
    inspect_pos = SCRIPT.index("/inspect")
    restore_pos = SCRIPT.index("/restore`")
    assert inspect_pos < restore_pos
