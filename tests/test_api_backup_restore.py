"""本地备份列表与恢复接口的离线测试。"""
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


def test_create_backup_now(client_and_store):
    client, store, backup_dir = client_and_store
    store.save_exchange("manual-backup-session", "问题", "回答")

    created = client.post("/api/backups", json={"confirm": True})

    assert created.status_code == 200
    data = created.json()
    assert data["ok"] is True
    assert data["backup"].startswith("medix-backup-")
    assert data["size"] > 0
    assert (backup_dir / data["backup"]).exists()
    listing = client.get("/api/backups").json()["backups"]
    assert listing[0]["name"] == data["backup"]


def test_create_backup_requires_confirmation(client_and_store):
    client, _store, backup_dir = client_and_store

    response = client.post("/api/backups", json={"confirm": False})

    assert response.status_code == 400
    assert not backup_dir.exists() or not list(backup_dir.glob("*.json"))


def test_list_and_restore_latest_backup(client_and_store):
    client, store, backup_dir = client_and_store
    store.save_exchange("restore-session", "问题", "回答")
    backup = create_backup(store.export_data(), directory=backup_dir)

    listing = client.get("/api/backups")

    assert listing.status_code == 200
    backups = listing.json()["backups"]
    assert [item["name"] for item in backups] == [backup.name]
    assert backups[0]["size"] > 0
    assert backups[0]["modified_at"]

    # 模拟数据丢失后从本地备份恢复
    assert store.delete_session("restore-session") is True
    assert store.get_session("restore-session") is None

    restored = client.post(
        f"/api/backups/{backup.name}/restore", json={"confirm": True}
    )

    assert restored.status_code == 200
    data = restored.json()
    assert data["ok"] is True
    assert data["backup"] == backup.name
    assert data["imported"] == 1
    assert store.get_session("restore-session") is not None


def test_restore_rejects_corrupted_backup(client_and_store):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    (backup_dir / "medix-backup-20260101-000000.json").write_text(
        "{not json", encoding="utf-8"
    )

    response = client.post(
        "/api/backups/medix-backup-20260101-000000.json/restore",
        json={"confirm": True},
    )

    assert response.status_code == 400
    assert "损坏" in response.json()["detail"]


def test_restore_rejects_tampered_checksum(client_and_store):
    client, store, backup_dir = client_and_store
    store.save_exchange("tamper-session", "问题", "回答")
    payload = store.export_data()
    payload["sessions"][0]["title"] = "被篡改"
    backup = create_backup(payload, directory=backup_dir)

    response = client.post(
        f"/api/backups/{backup.name}/restore", json={"confirm": True}
    )

    assert response.status_code == 400
    assert "校验失败" in response.json()["detail"]


def test_restore_requires_confirmation(client_and_store):
    client, store, backup_dir = client_and_store
    store.save_exchange("restore-confirm-session", "问题", "回答")
    backup = create_backup(store.export_data(), directory=backup_dir)
    assert store.delete_session("restore-confirm-session") is True

    response = client.post(
        f"/api/backups/{backup.name}/restore", json={"confirm": False}
    )

    assert response.status_code == 400
    assert store.get_session("restore-confirm-session") is None


def test_list_backups_returns_empty_when_missing(client_and_store):
    client, _store, _backup_dir = client_and_store

    response = client.get("/api/backups")

    assert response.status_code == 200
    assert response.json()["backups"] == []


def test_restore_rejects_path_outside_backup_dir(client_and_store):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    outside = backup_dir.parent / "medix-backup-20260101-000000.json"
    outside.write_text(json.dumps({"version": 1}), encoding="utf-8")

    response = client.post(
        "/api/backups/..%2Fmedix-backup-20260101-000000.json/restore",
        json={"confirm": True},
    )

    # 编码斜杠可能先被路由拒绝，也可能进入端点后被文件名校验拒绝
    assert response.status_code in (400, 404)


def test_backup_manage_ui_is_wired(client_and_store):
    client, _store, _backup_dir = client_and_store
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    assert 'id="history-manage-backups"' in index
    assert "manageLocalBackups" in app_js
    assert "method: 'DELETE'" in app_js
    assert "/api/backups/${encodeURIComponent(target.name)}" in app_js


def test_backup_picker_lists_all_backups_returned_by_api(client_and_store):
    client, _store, backup_dir = client_and_store
    backup_dir.mkdir(parents=True, exist_ok=True)
    for index in range(12):
        (backup_dir / f"medix-backup-20260101-{index:06d}.json").write_text(
            "{}", encoding="utf-8"
        )

    listing = client.get("/api/backups").json()["backups"]

    assert len(listing) == 12
    app_js = client.get("/static/app.js").text
    for function_name in (
        "async function restoreBackup()",
        "async function manageLocalBackups()",
    ):
        start = app_js.index(function_name)
        end = app_js.find("\nasync function ", start + len(function_name))
        body = app_js[start:end if end != -1 else None]
        assert "slice(0, 10)" not in body
        assert "backups || []" in body
