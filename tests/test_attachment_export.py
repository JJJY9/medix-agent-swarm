"""附件导出 ZIP 测试。"""
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.ratelimit import SlidingWindowLimiter
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    attachment_dir = tmp_path / "attachments"
    monkeypatch.setattr(api_server, "ATTACHMENT_DIR", attachment_dir)
    monkeypatch.setattr(
        api_server, "attachment_export_limiter", SlidingWindowLimiter(1000)
    )
    try:
        yield TestClient(api_server.app), store, attachment_dir
    finally:
        store.close()


def _add(store, stored_name, original_name, size=10):
    return store.add_attachment(
        "妈妈", "visit", 0, original_name, stored_name,
        "application/pdf", size, "",
    )


def test_export_zip_includes_files_and_manifest(client_and_store):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "good.pdf").write_bytes(b"%PDF-1.4 good")
    _add(store, "good.pdf", "出院小结.pdf", 13)
    _add(store, "missing.pdf", "丢失.pdf", 10)
    (directory / "orphan.pdf").write_bytes(b"orphan")

    response = client.get("/api/attachments/export.zip")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/zip")
    assert "attachment;" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-medix-attachment-count"] == "2"
    assert response.headers["x-medix-attachment-missing"] == "1"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {"manifest.json", "files/good.pdf"}
        assert archive.read("files/good.pdf") == b"%PDF-1.4 good"
        manifest = json.loads(
            archive.read("manifest.json").decode("utf-8")
        )
    assert manifest["version"] == 1
    assert manifest["count"] == 2
    rows = {row["stored_name"]: row for row in manifest["items"]}
    assert rows["good.pdf"]["file_present"] is True
    assert rows["missing.pdf"]["file_present"] is False
    assert rows["good.pdf"]["original_name"] == "出院小结.pdf"


def test_export_zip_with_missing_directory_and_empty_store(
    client_and_store,
):
    client, _store, _directory = client_and_store
    response = client.get("/api/attachments/export.zip")
    assert response.status_code == 200
    assert response.headers["x-medix-attachment-count"] == "0"
    assert response.headers["x-medix-attachment-missing"] == "0"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert set(archive.namelist()) == {"manifest.json"}
        manifest = json.loads(
            archive.read("manifest.json").decode("utf-8")
        )
    assert manifest["count"] == 0
    assert manifest["items"] == []


def test_export_zip_selected_ids_only(client_and_store):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        (directory / name).write_bytes(f"%PDF-1.4 {name}".encode())
    first = _add(store, "a.pdf", "A.pdf", 12)
    _add(store, "b.pdf", "B.pdf", 12)
    third = _add(store, "c.pdf", "C.pdf", 12)

    response = client.get(
        "/api/attachments/export.zip",
        params=[("ids", first["id"]), ("ids", third["id"])],
    )

    assert response.status_code == 200
    assert response.headers["x-medix-attachment-count"] == "2"
    assert response.headers["x-medix-attachment-missing"] == "0"
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = set(archive.namelist())
        assert names == {
            "manifest.json", "files/a.pdf", "files/c.pdf",
        }
        manifest = json.loads(
            archive.read("manifest.json").decode("utf-8")
        )
    assert manifest["count"] == 2
    assert [row["id"] for row in manifest["items"]] == [
        first["id"], third["id"],
    ]


def test_export_zip_selected_ids_validation(client_and_store):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "a.pdf").write_bytes(b"%PDF-1.4")
    item = _add(store, "a.pdf", "A.pdf", 8)

    missing = client.get(
        "/api/attachments/export.zip", params=[("ids", 999999)]
    )
    assert missing.status_code == 404
    assert "不存在" in missing.json()["detail"]
    invalid = client.get(
        "/api/attachments/export.zip", params=[("ids", 0)]
    )
    assert invalid.status_code == 400
    too_many = client.get(
        "/api/attachments/export.zip?"
        + "&".join(f"ids={value}" for value in range(1, 202))
    )
    assert too_many.status_code == 400
    dedup = client.get(
        "/api/attachments/export.zip",
        params=[("ids", item["id"]), ("ids", item["id"])],
    )
    assert dedup.status_code == 200
    assert dedup.headers["x-medix-attachment-count"] == "1"


def test_export_zip_rejects_oversized_total(
    client_and_store, monkeypatch
):
    client, store, directory = client_and_store
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "big.pdf").write_bytes(b"x" * 20)
    _add(store, "big.pdf", "大文件.pdf", 20)
    monkeypatch.setattr(
        api_server, "MAX_ATTACHMENT_EXPORT_BYTES", 10
    )
    response = client.get("/api/attachments/export.zip")
    assert response.status_code == 413
    assert "上限" in response.json()["detail"]


def test_export_zip_rate_limit(client_and_store, monkeypatch):
    client, _store, _directory = client_and_store
    monkeypatch.setattr(
        api_server, "attachment_export_limiter", SlidingWindowLimiter(1)
    )
    assert client.get("/api/attachments/export.zip").status_code == 200
    limited = client.get("/api/attachments/export.zip")
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"
