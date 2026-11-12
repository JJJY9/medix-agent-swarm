"""附件目录跟随会话库与备注 PATCH 语义的回归测试。"""
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


def test_attachment_dir_follows_session_db(tmp_path, monkeypatch):
    store = SessionStore(str(tmp_path / "custom" / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    try:
        directory = api_server._attachment_dir()
    finally:
        store.close()

    # 切换 MEDIX_SESSION_DB 后附件目录必须跟随，孤儿清理才不会误删
    assert directory == tmp_path / "custom" / "attachments"


def test_patch_attachment_empty_body_keeps_note(client_and_store):
    client, store = client_and_store
    item = store.add_attachment(
        "妈妈", "visit", 0, "出院小结.pdf", "report.pdf",
        "application/pdf", 10, "重要的出院注意事项",
    )

    empty = client.patch(f"/api/attachments/{item['id']}", json={})
    kept = client.get("/api/attachments").json()["attachments"][0]
    cleared = client.patch(
        f"/api/attachments/{item['id']}", json={"note": ""}
    )

    # 空对象是 no-op；空字符串才表示清空备注
    assert empty.status_code == 200
    assert empty.json()["attachment"]["note"] == "重要的出院注意事项"
    assert kept["note"] == "重要的出院注意事项"
    assert cleared.json()["attachment"]["note"] == ""
