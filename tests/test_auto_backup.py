"""本地自动备份的离线测试。"""
import asyncio
import json
from datetime import datetime
from pathlib import Path

import pytest

import api_server
import memory
from core.backup import (
    MAX_BACKUP_KEEP,
    create_backup,
    get_auto_backup_hours,
    get_backup_keep,
    latest_backup,
    list_backups,
    resolve_backup,
)


def test_create_backup_writes_and_prunes(tmp_path):
    paths = []
    for minute in range(3):
        paths.append(create_backup(
            {"version": 1, "index": minute},
            directory=tmp_path,
            keep=2,
            now=datetime(2026, 1, 1, 0, minute, 0),
        ))

    backups = sorted(tmp_path.glob("medix-backup-*.json"))
    assert len(backups) == 2
    assert paths[0] not in backups
    data = json.loads(backups[-1].read_text(encoding="utf-8"))
    assert data["index"] == 2
    assert not list(tmp_path.glob("*.tmp"))
    assert latest_backup(tmp_path) == backups[-1]


def test_create_backup_avoids_same_second_collision(tmp_path):
    now = datetime(2026, 1, 1, 8, 0, 0)

    first = create_backup({"n": 1}, directory=tmp_path, keep=5, now=now)
    second = create_backup({"n": 2}, directory=tmp_path, keep=5, now=now)

    assert first != second
    assert first.exists() and second.exists()


def test_same_second_backup_order_uses_numeric_suffix(tmp_path):
    now = datetime(2026, 1, 1, 8, 0, 0)
    paths = [
        create_backup({"n": index}, directory=tmp_path, keep=20, now=now)
        for index in range(12)
    ]

    # 后缀按数字比较：-11 比 -9 新，不能用文件名字典序判断
    assert latest_backup(tmp_path) == paths[-1]
    assert latest_backup(tmp_path).name.endswith("-11.json")
    assert list_backups(tmp_path)[0] == paths[-1]


def test_resolve_backup_rejects_unsafe_names(tmp_path):
    path = create_backup(
        {"n": 1}, directory=tmp_path, keep=5,
        now=datetime(2026, 1, 1, 8, 0, 0),
    )

    assert resolve_backup(path.name, tmp_path) == path
    with pytest.raises(ValueError):
        resolve_backup("../outside.json", tmp_path)
    with pytest.raises(ValueError):
        resolve_backup("not-a-backup.json", tmp_path)
    with pytest.raises(FileNotFoundError):
        resolve_backup("medix-backup-19990101-000000.json", tmp_path)


def test_create_backup_rejects_oversized_payload(tmp_path, monkeypatch):
    import core.backup as backup_module

    monkeypatch.setattr(backup_module, "MAX_BACKUP_FILE_BYTES", 64)
    with pytest.raises(ValueError):
        create_backup(
            {"data": "x" * 100}, directory=tmp_path,
            now=datetime(2026, 1, 1, 8, 0, 0),
        )
    assert not list(tmp_path.glob("*.json"))
    assert not list(tmp_path.glob("*.tmp"))


def test_backup_config_defaults_and_caps(monkeypatch):
    monkeypatch.delenv("MEDIX_AUTO_BACKUP_HOURS", raising=False)
    monkeypatch.delenv("MEDIX_BACKUP_KEEP", raising=False)
    assert get_auto_backup_hours() == 0
    assert get_backup_keep() == 7

    monkeypatch.setenv("MEDIX_AUTO_BACKUP_HOURS", "12")
    monkeypatch.setenv("MEDIX_BACKUP_KEEP", "9999")
    assert get_auto_backup_hours() == 12
    assert get_backup_keep() == MAX_BACKUP_KEEP

    monkeypatch.setenv("MEDIX_AUTO_BACKUP_HOURS", "bad")
    assert get_auto_backup_hours() == 0


def test_auto_backup_once_uses_export_snapshot(tmp_path, monkeypatch):
    class FakeStore:
        def export_data(self):
            return {
                "version": 1,
                "session_count": 1,
                "sessions": [{"id": "s1", "messages": []}],
                "checksum": "sha256:test",
            }

    monkeypatch.setenv("MEDIX_AUTO_BACKUP_HOURS", "6")
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(tmp_path))
    monkeypatch.setattr(memory, "get_session_store", lambda: FakeStore())

    path = asyncio.run(api_server.auto_backup_once())

    assert path is not None
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    assert data["sessions"][0]["id"] == "s1"
    assert data["checksum"] == "sha256:test"


def test_auto_backup_once_disabled(monkeypatch):
    monkeypatch.delenv("MEDIX_AUTO_BACKUP_HOURS", raising=False)

    assert asyncio.run(api_server.auto_backup_once()) is None
