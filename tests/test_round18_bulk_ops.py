"""第十八轮审查：提醒/附件批量删除端点回归测试。"""
from datetime import datetime, timedelta

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
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def test_bulk_delete_reminders_single_transaction(client_and_store):
    client, store = client_and_store
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    ids = [
        client.post("/api/reminders", json={
            "title": f"提醒{index}", "due_at": tomorrow,
        }).json()["reminder"]["id"]
        for index in range(3)
    ]

    response = client.post(
        "/api/reminders/bulk_delete", json={"ids": ids}
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 3
    assert store.list_reminders(True) == []


def test_bulk_delete_attachments_removes_files_and_rows(
    client_and_store,
):
    client, store = client_and_store
    attachment_dir = store.db_path.parent / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    items = []
    for index in range(2):
        stored = f"file-{index}.pdf"
        (attachment_dir / stored).write_bytes(b"%PDF-1.4")
        items.append(store.add_attachment(
            "妈妈", "lab", 0, f"化验单{index}.pdf", stored,
            "application/pdf", 10, "",
        ))

    response = client.post(
        "/api/attachments/bulk_delete",
        json={"ids": [item["id"] for item in items]},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["deleted"] == 2
    assert store.list_attachments("妈妈") == []
    assert list(attachment_dir.iterdir()) == []


def test_bulk_delete_attachments_skips_missing_ids(client_and_store):
    client, _store = client_and_store

    response = client.post(
        "/api/attachments/bulk_delete", json={"ids": [999]}
    )

    assert response.status_code == 200
    assert response.json()["deleted"] == 0
    assert response.json()["skipped"] == [999]


def test_bulk_delete_attachments_rejects_out_of_range_ids(
    client_and_store,
):
    client, store = client_and_store
    attachment_dir = store.db_path.parent / "attachments"
    attachment_dir.mkdir(parents=True, exist_ok=True)
    stored = "valid.pdf"
    (attachment_dir / stored).write_bytes(b"%PDF-1.4")
    item = store.add_attachment(
        "妈妈", "lab", 0, "化验单.pdf", stored,
        "application/pdf", 10, "",
    )

    response = client.post(
        "/api/attachments/bulk_delete",
        json={"ids": [item["id"], 2**63]},
    )

    # 超出 SQLite 整数范围的编号必须整体拒绝，且不能先删掉文件
    assert response.status_code == 400
    assert (attachment_dir / stored).exists()
    assert store.get_attachment(item["id"]) is not None
