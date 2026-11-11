"""附件登记与本地文件完整性检查测试。"""
import os
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    attachment_dir = tmp_path / "attachments"
    monkeypatch.setattr(api_server, "ATTACHMENT_DIR", attachment_dir)
    try:
        yield TestClient(api_server.app), store, attachment_dir
    finally:
        store.close()


def _add(store, stored_name, original_name="资料.pdf", size=13):
    return store.add_attachment(
        "妈妈", "visit", 0, original_name, stored_name,
        "application/pdf", size, "",
    )


def _write(directory, name, old=False):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    path.write_bytes(b"%PDF-1.4 test")
    if old:
        stamp = time.time() - 3600
        os.utime(path, (stamp, stamp))
    return path


def test_integrity_reports_missing_and_orphans(client_and_store):
    client, store, directory = client_and_store
    _add(store, "good.pdf", "正常.pdf")
    _write(directory, "good.pdf")
    _add(store, "missing.pdf", "丢失.pdf")
    _write(directory, "orphan.pdf", old=True)
    _write(directory, "recent.pdf")

    response = client.get("/api/attachments/integrity")

    assert response.status_code == 200
    data = response.json()
    assert data["db_count"] == 2
    assert data["file_count"] == 3
    assert data["missing_count"] == 1
    assert data["orphan_count"] == 2
    assert data["recent_orphan_count"] == 1
    assert data["missing"][0]["stored_name"] == "missing.pdf"
    assert data["missing"][0]["original_name"] == "丢失.pdf"
    assert {item["name"] for item in data["orphans"]} == {
        "orphan.pdf", "recent.pdf",
    }
    assert next(
        item for item in data["orphans"] if item["name"] == "recent.pdf"
    )["recent"] is True


def test_integrity_reports_size_mismatch(client_and_store):
    client, store, directory = client_and_store
    _add(store, "good.pdf", "正常.pdf")
    _write(directory, "good.pdf")
    _add(store, "changed.pdf", "被截断.pdf", size=999)
    _write(directory, "changed.pdf")

    response = client.get("/api/attachments/integrity")

    assert response.status_code == 200
    data = response.json()
    assert data["missing_count"] == 0
    assert data["size_mismatch_count"] == 1
    assert data["size_mismatch"][0]["stored_name"] == "changed.pdf"
    assert data["size_mismatch"][0]["original_name"] == "被截断.pdf"
    assert data["size_mismatch"][0]["db_size"] == 999
    assert data["size_mismatch"][0]["file_size"] == 13


def test_cleanup_requires_confirm_and_only_removes_old_orphans(
    client_and_store,
):
    client, store, directory = client_and_store
    _add(store, "good.pdf")
    _write(directory, "good.pdf")
    _add(store, "missing.pdf")
    orphan = _write(directory, "orphan.pdf", old=True)
    recent = _write(directory, "recent.pdf")

    assert client.post(
        "/api/attachments/cleanup", json={"confirm": False, "expected_orphans": 2}
    ).status_code == 400

    response = client.post(
        "/api/attachments/cleanup", json={"confirm": True, "expected_orphans": 2}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["removed"] == 1
    assert data["failed"] == 0
    assert data["skipped"] == 1
    assert not orphan.exists()
    assert recent.exists()
    assert (directory / "good.pdf").exists()
    assert {item["stored_name"] for item in store.list_attachments(
        "妈妈"
    )} == {"good.pdf", "missing.pdf"}

    second = client.post(
        "/api/attachments/cleanup",
        json={"confirm": True, "expected_orphans": 1},
    ).json()
    assert second["removed"] == 0
    assert second["skipped"] == 1


def test_cleanup_rejects_stale_orphan_count(client_and_store):
    client, _store, directory = client_and_store
    orphan = _write(directory, "orphan.pdf", old=True)
    response = client.post(
        "/api/attachments/cleanup",
        json={"confirm": True, "expected_orphans": 0},
    )
    assert response.status_code == 409
    assert "重新检查" in response.json()["detail"]
    assert orphan.exists()


def test_cleanup_reports_locked_orphan(client_and_store, monkeypatch):
    client, _store, directory = client_and_store
    _write(directory, "locked.pdf", old=True)
    real_unlink = Path.unlink

    def locked(self, *args, **kwargs):
        if self.name == "locked.pdf":
            raise PermissionError("file in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    data = client.post(
        "/api/attachments/cleanup", json={"confirm": True, "expected_orphans": 1}
    ).json()
    assert data["removed"] == 0
    assert data["failed"] == 1
    assert (directory / "locked.pdf").exists()


def test_integrity_handles_more_than_list_page_limit(
    client_and_store,
):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)
    for index in range(1001):
        _add(store, f"f{index}.pdf")
        (directory / f"f{index}.pdf").write_bytes(b"x")
    data = client.get("/api/attachments/integrity").json()
    assert data["db_count"] == 1001
    assert data["file_count"] == 1001
    assert data["missing_count"] == 0
    assert data["orphan_count"] == 0


def test_integrity_with_directory_path_as_file(client_and_store):
    client, _store, directory = client_and_store
    directory.parent.mkdir(parents=True, exist_ok=True)
    directory.write_text("not a directory", encoding="utf-8")
    data = client.get("/api/attachments/integrity").json()
    assert data["file_count"] == 0
    assert data["missing_count"] == 0
    assert data["orphan_count"] == 0


def test_integrity_handles_out_of_range_mtime(client_and_store):
    client, _store, directory = client_and_store
    orphan = _write(directory, "future.pdf", old=True)
    try:
        os.utime(orphan, (300000000000, 300000000000))
    except OSError:
        pytest.skip("filesystem does not accept out-of-range mtime")
    data = client.get("/api/attachments/integrity").json()
    assert data["orphan_count"] == 1
    # 部分文件系统会把越界时间钳制到可表示范围，另一部分会让
    # fromtimestamp 抛错；两种情况下接口都必须正常返回
    assert isinstance(data["orphans"][0]["modified_at"], str)


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive paths")
def test_integrity_matches_windows_case_rename(client_and_store):
    client, store, directory = client_and_store
    _add(store, "abc.pdf")
    _write(directory, "ABC.PDF", old=True)
    data = client.get("/api/attachments/integrity").json()
    assert data["missing_count"] == 0
    assert data["orphan_count"] == 0


def test_integrity_without_directory(client_and_store):
    client, store, _directory = client_and_store
    _add(store, "missing.pdf")
    data = client.get("/api/attachments/integrity").json()
    assert data["file_count"] == 0
    assert data["missing_count"] == 1
    assert data["orphan_count"] == 0
