"""知识库管理 API 的离线测试（假服务，不加载模型）。"""
import asyncio

import pytest
from fastapi.testclient import TestClient

import api_server
import knowledge.service as knowledge_service


class FakeService:
    def list_documents(self):
        return {
            "documents": [
                {"filename": "a.md", "size": 3, "suffix": ".md", "builtin": False}
            ],
            "document_count": 1,
            "chunk_count": 2,
            "docs_dir": "docs",
            "upload_dir": "uploads",
        }

    def rebuild(self, replace_existing=True):
        return {
            "documents": 1,
            "chunks": 2,
            "chunk_count": 2,
            "builtin_documents": 0,
            "uploaded_documents": 1,
        }

    def save_upload(self, filename, content):
        return {"filename": "a.md", "size": len(content)}

    def delete_document(self, filename):
        return True


@pytest.fixture(autouse=True)
def fake_knowledge(monkeypatch):
    api_server.state.lock = asyncio.Lock()
    monkeypatch.setattr(
        knowledge_service, "get_knowledge_service", lambda: FakeService()
    )
    yield


def test_knowledge_overview():
    client = TestClient(api_server.app)

    response = client.get("/api/knowledge")

    assert response.status_code == 200
    assert response.json()["document_count"] == 1


def test_knowledge_rebuild():
    client = TestClient(api_server.app)

    response = client.post("/api/knowledge/rebuild")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["chunks"] == 2


def test_knowledge_upload_and_rebuild():
    client = TestClient(api_server.app)

    response = client.post(
        "/api/knowledge/upload",
        files={"file": ("guide.md", b"hello", "text/markdown")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["document"]["filename"] == "a.md"
    assert body["rebuild"]["chunks"] == 2


def test_knowledge_delete():
    client = TestClient(api_server.app)

    response = client.delete("/api/knowledge/documents/guide.md")

    assert response.status_code == 200
    assert response.json()["ok"] is True
