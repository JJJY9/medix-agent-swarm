"""第二十七轮审查：知识库空库、文件名与本地向量库落盘回归测试。"""
from pathlib import Path

import pytest

import knowledge.local_vector_store as local_vector_store
from knowledge.local_vector_store import LocalVectorStore
from knowledge.service import KnowledgeService


class FakeEmptyKB:
    def __init__(self):
        self.cleared = False

    def add_documents(self, documents, replace_existing=False):
        return 0

    def count_documents(self):
        return 0

    def search(self, query, top_k=5, filter_type=None):
        return []

    def clear_index(self):
        self.cleared = True


def test_empty_knowledge_base_rebuild_and_search(tmp_path):
    docs = tmp_path / "docs"
    uploads = tmp_path / "uploads"
    docs.mkdir()
    uploads.mkdir()
    kb = FakeEmptyKB()
    service = KnowledgeService(docs_dir=docs, upload_dir=uploads, kb=kb)

    result = service.rebuild()
    assert result["documents"] == 0
    assert kb.cleared is True

    payload = service.search("高血压")
    assert payload["results"] == []


def test_safe_filename_respects_byte_length(tmp_path):
    service = KnowledgeService(
        docs_dir=tmp_path / "docs",
        upload_dir=tmp_path / "uploads",
        kb=FakeEmptyKB(),
    )

    name = service._safe_filename("测" * 90 + ".md")

    assert len(name.encode("utf-8")) <= 200
    assert name.endswith(".md")


def test_local_vector_store_uses_unique_temp_files(
    tmp_path, monkeypatch
):
    db = tmp_path / "milvus_lite.db"
    store = LocalVectorStore(str(db))
    store.create_collection("c", 2)

    created = []
    real_mkstemp = local_vector_store.tempfile.mkstemp

    def recording_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created.append(path)
        return fd, path

    monkeypatch.setattr(
        local_vector_store.tempfile, "mkstemp", recording_mkstemp
    )

    store.insert("c", [{
        "vector": [1.0, 0.0], "content": "a", "metadata": "{}",
    }])
    store.insert("c", [{
        "vector": [0.0, 1.0], "content": "b", "metadata": "{}",
    }])

    assert len(created) == 2
    assert created[0] != created[1]
    assert not (tmp_path / f"{db.name}.local.json.tmp").exists()


def test_local_vector_store_two_instances_keep_both_rows(tmp_path):
    db = tmp_path / "milvus_lite.db"
    first = LocalVectorStore(str(db))
    first.create_collection("c", 2)
    second = LocalVectorStore(str(db))

    for index in range(5):
        first.insert("c", [{
            "vector": [1.0, 0.0],
            "content": f"first-{index}",
            "metadata": "{}",
        }])
        second.insert("c", [{
            "vector": [0.0, 1.0],
            "content": f"second-{index}",
            "metadata": "{}",
        }])

    reloaded = LocalVectorStore(str(db))
    contents = reloaded.collections["c"]["contents"]
    assert len(contents) == 10
    assert sum(1 for item in contents if item.startswith("first-")) == 5
    assert sum(1 for item in contents if item.startswith("second-")) == 5
