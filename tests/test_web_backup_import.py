"""整库备份导入请求体的前端回归测试。"""
from pathlib import Path

from memory.session_store import _BACKUP_CHECKSUM_FIELDS

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_backup_import_payload_includes_all_checksum_fields():
    # 前端导入白名单必须覆盖后端校验和覆盖的所有字段，否则新导出的
    # 备份会因缺字段导致 checksum 校验失败（#690 的 usage_events 回归）
    for key in _BACKUP_CHECKSUM_FIELDS:
        assert f"{key}: data.{key}" in SCRIPT, key
    assert "checksum: data.checksum" in SCRIPT


def test_backup_import_recognizes_usage_events():
    assert "data.usage_events" in SCRIPT
    assert "条用量流水" in SCRIPT
