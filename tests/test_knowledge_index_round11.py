"""知识库空向量库重建与上传失败清理的回归测试。"""
import pytest

from knowledge.service import KnowledgeService


class FakeKB:
    def __init__(self):
        self.documents = []
        self.rebuilds = 0

    def add_documents(self, documents, replace_existing=True):
        self.documents = list(documents)
        self.rebuilds += 1
        return len(self.documents)

    def count_documents(self):
        return len(self.documents)

    def search(self, query, top_k=3, filter_type=None):
        return []


def _dirs(tmp_path):
    docs = tmp_path / "docs"
    uploads = tmp_path / "uploads"
    docs.mkdir()
    uploads.mkdir()
    (docs / "guide.txt").write_text("高血压 指南 内容", encoding="utf-8")
    return docs, uploads


def test_search_rebuilds_when_vector_store_empty(tmp_path):
    docs, uploads = _dirs(tmp_path)
    kb = FakeKB()
    service = KnowledgeService(docs_dir=docs, upload_dir=uploads, kb=kb)
    service.rebuild()
    assert kb.documents

    # 模拟向量库被删除或换目录：文档指纹未变但索引为空
    kb.documents = []
    service.search("高血压", top_k=3)

    assert kb.documents
    assert kb.rebuilds >= 2


def test_upload_write_failure_leaves_no_partial_file(tmp_path, monkeypatch):
    docs, uploads = _dirs(tmp_path)
    service = KnowledgeService(
        docs_dir=docs, upload_dir=uploads, kb=FakeKB()
    )

    def broken_write(self, data):
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_bytes", broken_write)
    with pytest.raises(OSError):
        service.save_upload("partial.md", b"# demo")

    monkeypatch.undo()
    assert list(uploads.iterdir()) == []
