"""本地备份恢复前预检测试。"""
import json

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.backup import create_backup
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    backup_dir = tmp_path / "backups"
    monkeypatch.setenv("MEDIX_BACKUP_DIR", str(backup_dir))
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        yield TestClient(api_server.app), store, backup_dir
    finally:
        store.close()


def _make_backup(store, backup_dir):
    store.save_exchange("inspect-session", "问题", "回答")
    store.add_metric("妈妈", "weight", 62)
    return create_backup(store.export_data(), directory=backup_dir)


def test_inspect_backup_counts_and_checksum(client_and_store):
    client, store, backup_dir = client_and_store
    backup = _make_backup(store, backup_dir)

    response = client.get(f"/api/backups/{backup.name}/inspect")

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == backup.name
    assert data["size"] > 0
    assert data["version"] == 1
    assert data["checksum_present"] is True
    assert data["checksum_ok"] is True
    assert data["counts"]["sessions"] == 1
    assert data["counts"]["messages"] == 2
    assert data["counts"]["health_metrics"] == 1
    assert data["total_records"] == 2
    assert data["invalid_sessions"] == 0
    assert data["restore_ready"] is True
    assert data["warnings"] == []
    assert data["exported_at"]


def test_inspect_detects_tampered_checksum(client_and_store):
    client, store, backup_dir = client_and_store
    backup = _make_backup(store, backup_dir)
    payload = json.loads(backup.read_text(encoding="utf-8"))
    payload["health_metrics"][0]["value1"] = 999
    backup.write_text(
        json.dumps(payload, ensure_ascii=False), encoding="utf-8"
    )

    response = client.get(f"/api/backups/{backup.name}/inspect")

    assert response.status_code == 200
    data = response.json()
    assert data["checksum_ok"] is False
    assert data["restore_ready"] is False
    assert "校验失败" in data["warnings"][0]


def test_inspect_flags_unrestorable_sessions(client_and_store):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    wrong_type = backup_dir / "medix-backup-20260103-000000.json"
    wrong_type.write_text(json.dumps({
        "version": 1,
        "sessions": "oops",
        "profiles": [{"name": "a", "context": {}}],
    }), encoding="utf-8")
    data = client.get(
        f"/api/backups/{wrong_type.name}/inspect"
    ).json()
    assert data["restore_ready"] is False
    assert data["counts"]["sessions"] == 0
    assert any("会话列表格式不正确" in item for item in data["warnings"])

    invalid_session = backup_dir / "medix-backup-20260104-000000.json"
    invalid_session.write_text(json.dumps({
        "version": 1,
        "sessions": [{
            "id": "s1",
            "messages": [{"role": "system", "content": "x"}],
        }],
    }), encoding="utf-8")
    data = client.get(
        f"/api/backups/{invalid_session.name}/inspect"
    ).json()
    assert data["counts"]["sessions"] == 0
    assert data["counts"]["messages"] == 0
    assert data["invalid_sessions"] == 1
    assert data["total_records"] == 0
    assert any("缺少编号或有效消息" in item for item in data["warnings"])
    assert any("没有可恢复记录" in item for item in data["warnings"])


def test_inspect_handles_unencodable_surrogates(client_and_store):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / "medix-backup-20260105-000000.json"
    backup.write_text(
        '{"version": 1, "checksum": "sha256:dead", '
        '"profiles": [{"name": "\\ud800"}]}',
        encoding="utf-8",
    )

    response = client.get(f"/api/backups/{backup.name}/inspect")

    assert response.status_code == 200
    data = response.json()
    assert data["checksum_ok"] is False
    assert data["restore_ready"] is False
    assert any("无法编码" in item for item in data["warnings"])


def test_inspect_missing_invalid_and_malformed(client_and_store):
    client, _store, backup_dir = client_and_store
    missing = client.get("/api/backups/medix-backup-20990101-000000.json/inspect")
    assert missing.status_code == 404

    invalid = client.get("/api/backups/..%2Fsecret.json/inspect")
    assert invalid.status_code in (400, 404)

    bad_name = backup_dir / "bad name.json"
    backup_dir.mkdir(parents=True, exist_ok=True)
    bad_name.write_text("{}", encoding="utf-8")
    assert client.get(
        "/api/backups/bad%20name.json/inspect"
    ).status_code == 400

    backup_dir.mkdir(parents=True, exist_ok=True)
    broken = backup_dir / "medix-backup-20260101-000000.json"
    broken.write_text("{not json", encoding="utf-8")
    response = client.get(f"/api/backups/{broken.name}/inspect")
    assert response.status_code == 400
    assert "损坏" in response.json()["detail"]


def test_inspect_empty_and_oversized(client_and_store, monkeypatch):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    empty = backup_dir / "medix-backup-20260102-000000.json"
    empty.write_text("{}", encoding="utf-8")

    data = client.get(f"/api/backups/{empty.name}/inspect").json()
    assert data["checksum_present"] is False
    assert data["checksum_ok"] is None
    assert data["total_records"] == 0
    assert data["restore_ready"] is False
    assert any("没有可恢复记录" in item for item in data["warnings"])
    assert any("没有校验信息" in item for item in data["warnings"])

    monkeypatch.setattr(api_server, "MAX_BACKUP_FILE_BYTES", 1)
    too_big = client.get(f"/api/backups/{empty.name}/inspect")
    assert too_big.status_code == 413
