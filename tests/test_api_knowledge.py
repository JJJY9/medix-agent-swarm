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

    def preview_document(self, filename):
        if filename == "missing.md":
            return None
        if filename == "bad.md":
            raise ValueError("文档内容为空或无法解析。")
        return {
            "filename": filename,
            "builtin": False,
            "size": 10,
            "suffix": ".md",
            "type": "general",
            "source": "自定义文档",
            "content": "预览内容",
            "truncated": False,
            "total_length": 4,
        }

    def search(self, query, top_k=5):
        if not query.strip():
            raise ValueError("请输入检索关键词。")
        return {
            "query": query,
            "top_k": top_k,
            "results": [{
                "title": "高血压",
                "source": "生活方式建议数据库",
                "filename": "01_lifestyle_hypertension.txt",
                "type": "lifestyle",
                "score": 0.83,
                "snippet": "低盐饮食",
            }],
        }


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


def test_knowledge_search():
    client = TestClient(api_server.app)

    response = client.get(
        "/api/knowledge/search", params={"q": "高血压", "top_k": 3}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["query"] == "高血压"
    assert data["top_k"] == 3
    assert data["results"][0]["score"] == 0.83
    assert data["results"][0]["snippet"] == "低盐饮食"


def test_knowledge_search_empty_query():
    client = TestClient(api_server.app)

    response = client.get("/api/knowledge/search", params={"q": "  "})

    assert response.status_code == 400


def test_knowledge_preview():
    client = TestClient(api_server.app)

    response = client.get("/api/knowledge/documents/a.md/preview")

    assert response.status_code == 200
    data = response.json()
    assert data["filename"] == "a.md"
    assert data["content"] == "预览内容"
    assert data["builtin"] is False


def test_knowledge_preview_missing_and_invalid():
    client = TestClient(api_server.app)

    assert client.get(
        "/api/knowledge/documents/missing.md/preview"
    ).status_code == 404
    assert client.get(
        "/api/knowledge/documents/bad.md/preview"
    ).status_code == 400


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
