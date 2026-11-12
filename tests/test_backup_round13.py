"""备份重试、列表上限、预检编码与临时文件清理的回归测试。"""
import asyncio
import json
import os
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import core.backup as backup_module


class _StopLoop(Exception):
    pass


def test_failed_backup_retries_within_an_hour(monkeypatch):
    durations = []

    async def fake_sleep(seconds):
        durations.append(seconds)
        raise _StopLoop

    async def failing_backup():
        raise RuntimeError("disk full")

    monkeypatch.setenv("MEDIX_AUTO_BACKUP_HOURS", "720")
    monkeypatch.setattr(api_server.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(
        api_server, "auto_backup_wait_seconds", lambda hours: 0
    )
    monkeypatch.setattr(api_server, "auto_backup_once", failing_backup)

    with pytest.raises(_StopLoop):
        asyncio.run(api_server.auto_backup_loop())

    # 失败后不能睡满 30 天，最长 1 小时就重试
    assert durations
    assert durations[0] == 3600
    assert all(seconds <= 3600 for seconds in durations)


def test_backup_list_uses_keep_limit(tmp_path, monkeypatch):
    directory = tmp_path / "backups"
    directory.mkdir()
    for index in range(60):
        name = f"medix-backup-20260101-{index:06d}.json"
        (directory / name).write_text("{}", encoding="utf-8")
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(directory))
    monkeypatch.setattr(backup_module, "get_backup_keep", lambda: 100)
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)

    client = TestClient(api_server.app)
    data = client.get("/api/backups").json()

    # 列表上限与保留策略一致，60 份都能在界面恢复/删除
    assert len(data["backups"]) == 60


def test_inspect_sanitizes_unencodable_metadata(tmp_path, monkeypatch):
    directory = tmp_path / "backups"
    directory.mkdir()
    name = "medix-backup-20260101-000000.json"
    payload = {
        "version": float("nan"),
        "exported_at": "\ud800",
        "sessions": [],
    }
    (directory / name).write_text(
        json.dumps(payload), encoding="utf-8"
    )
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(directory))
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)

    client = TestClient(api_server.app)
    response = client.get(f"/api/backups/{name}/inspect")

    assert response.status_code == 200
    data = response.json()
    assert data["version"] is None
    assert "\ud800" not in str(data["exported_at"])


def test_create_backup_removes_stale_temp_files(tmp_path):
    directory = tmp_path / "backups"
    directory.mkdir()
    stale = directory / "medix-backup-20260101-000000.json.abc.tmp"
    stale.write_text("partial", encoding="utf-8")
    old = time.time() - 7200
    os.utime(stale, (old, old))

    path = backup_module.create_backup({}, directory=directory)

    assert path.is_file()
    assert not stale.exists()
