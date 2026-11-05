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
        if filename == "bad.exe":
            raise ValueError("仅支持 .txt / .md / .markdown / .pdf 文件。")
        return {"filename": "a.md", "size": len(content)}

    def delete_document(self, filename):
        return True

    def preview_document(self, filename, max_chars=4000):
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

    def update_document(self, filename, content):
        if filename == "builtin.md":
            raise ValueError("内置文档不能在线编辑。")
        if not content:
            raise ValueError("文件内容为空。")
        return {"filename": filename, "size": len(content)}

    def search(self, query, top_k=5, filter_type=None):
        if not query.strip():
            raise ValueError("请输入检索关键词。")
        return {
            "query": query,
            "top_k": top_k,
            "filter_type": filter_type or "",
            "results": [{
                "title": "高血压",
                "source": "生活方式建议数据库",
                "filename": "01_lifestyle_hypertension.txt",
                "type": filter_type or "lifestyle",
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


def test_knowledge_batch_upload():
    client = TestClient(api_server.app)

    response = client.post(
        "/api/knowledge/upload-batch",
        files=[
            ("files", ("a.md", b"hello", "text/markdown")),
            ("files", ("b.txt", b"world", "text/plain")),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["documents"]) == 2
    assert data["failed"] == []
    assert data["rebuild"]["chunks"] == 2


def test_knowledge_batch_upload_reports_partial_failure():
    client = TestClient(api_server.app)

    response = client.post(
        "/api/knowledge/upload-batch",
        files=[
            ("files", ("a.md", b"hello", "text/markdown")),
            ("files", ("bad.exe", b"binary", "application/octet-stream")),
        ],
    )

    assert response.status_code == 200
    data = response.json()
    assert len(data["documents"]) == 1
    assert data["failed"][0]["filename"] == "bad.exe"


def test_knowledge_batch_upload_rejects_all_failed_and_too_many():
    client = TestClient(api_server.app)

    all_failed = client.post(
        "/api/knowledge/upload-batch",
        files=[("files", ("bad.exe", b"x", "application/octet-stream"))],
    )
    too_many = client.post(
        "/api/knowledge/upload-batch",
        files=[
            ("files", (f"doc{i}.md", b"x", "text/markdown"))
            for i in range(11)
        ],
    )

    assert all_failed.status_code == 400
    assert "所有文件均未上传成功" in all_failed.json()["detail"]
    assert too_many.status_code == 400
    assert "最多上传 10 个文件" in too_many.json()["detail"]


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


def test_knowledge_search_filters_by_type():
    client = TestClient(api_server.app)

    filtered = client.get(
        "/api/knowledge/search",
        params={"q": "高血压", "filter_type": "lifestyle"},
    )
    invalid = client.get(
        "/api/knowledge/search",
        params={"q": "高血压", "filter_type": "unknown"},
    )

    assert filtered.status_code == 200
    assert filtered.json()["filter_type"] == "lifestyle"
    assert filtered.json()["results"][0]["type"] == "lifestyle"
    assert invalid.status_code == 400


def test_knowledge_search_empty_query():
    client = TestClient(api_server.app)

    response = client.get("/api/knowledge/search", params={"q": "  "})

    assert response.status_code == 400


def test_knowledge_update():
    client = TestClient(api_server.app)

    response = client.put(
        "/api/knowledge/documents/a.md",
        json={"content": "新的内容"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["ok"] is True
    assert data["document"]["filename"] == "a.md"
    assert data["rebuild"]["documents"] == 1


def test_knowledge_update_rejects_builtin():
    client = TestClient(api_server.app)

    response = client.put(
        "/api/knowledge/documents/builtin.md",
        json={"content": "新的内容"},
    )

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


def test_knowledge_upload_rejects_oversized_file():
    client = TestClient(api_server.app)
    big = b"x" * (api_server.MAX_UPLOAD_BYTES + 1)

    response = client.post(
        "/api/knowledge/upload",
        files={"file": ("big.md", big, "text/markdown")},
    )

    assert response.status_code == 413
    assert "文件过大" in response.json()["detail"]


def test_knowledge_batch_reports_oversized_file():
    client = TestClient(api_server.app)
    big = b"x" * (api_server.MAX_UPLOAD_BYTES + 1)

    response = client.post(
        "/api/knowledge/upload-batch",
        files=[("files", ("big.md", big, "text/markdown"))],
    )

    assert response.status_code == 400
    assert "未上传成功" in response.json()["detail"]


def test_knowledge_delete():
    client = TestClient(api_server.app)

    response = client.delete("/api/knowledge/documents/guide.md")

    assert response.status_code == 200
    assert response.json()["ok"] is True
