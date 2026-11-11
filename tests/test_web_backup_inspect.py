"""备份恢复预检前端接线测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_restore_runs_preflight_before_confirm():
    assert "/api/backups/${encodeURIComponent(target.name)}/inspect" in SCRIPT
    assert "/inspect`,\n        { cache: 'no-store' }" in SCRIPT
    assert "备份预检失败" in SCRIPT
    assert "共 ${Number(inspectInfo.total_records) || 0} 条记录" in SCRIPT
    assert "预检：${inspectSummary}" in SCRIPT
    assert "会话 ${counts.sessions || 0}（消息 ${counts.messages || 0}）" in SCRIPT
    assert "指标 ${counts.health_metrics || 0}" in SCRIPT
    assert "化验 ${counts.lab_results || 0}" in SCRIPT
    assert "inspectInfo.checksum_ok !== true" in SCRIPT
    assert "备份预检响应格式不正确" in SCRIPT


def test_restore_blocks_failed_checksum_and_warns_missing():
    assert "if (inspectInfo.checksum_ok === false) {" in SCRIPT
    assert "备份校验失败，文件可能已损坏或被修改，已停止恢复。" in SCRIPT
    assert "inspectInfo.restore_ready === false" in SCRIPT
    assert "备份预检未通过，已停止恢复" in SCRIPT
    assert "inspectInfo.checksum_present === false" in SCRIPT
    assert (
        "inspectInfo.checksum_present === false\n"
        "      ? '该备份没有校验信息，请确认来源可靠；已有记录会跳过。'\n"
        "      : '校验通过，已有记录会跳过，不会删除现有数据。'"
    ) in SCRIPT
    assert "Array.isArray(inspectInfo.warnings)" in SCRIPT
    assert "注意：${warnings.join('；')}" in SCRIPT


def test_restore_inspect_happens_after_selection_and_before_restore():
    select_pos = SCRIPT.index("target = candidates[index];")
    inspect_pos = SCRIPT.index("let inspectInfo;")
    restore_pos = SCRIPT.index("/restore`")
    assert select_pos < inspect_pos < restore_pos
