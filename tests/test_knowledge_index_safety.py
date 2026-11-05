"""知识库索引失败可见性与本地向量库持久化测试。"""
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest

from knowledge.local_vector_store import LocalVectorStore

# sentence_transformers 体积较大，离线 CI 不安装；相关用例在缺少依赖时跳过
HAS_SENTENCE_TRANSFORMERS = (
    importlib.util.find_spec("sentence_transformers") is not None
)


class FakeEmbedding:
    def encode(self, *args, **kwargs):
        texts = args[0] if args else []
        return np.zeros((len(texts) or 1, 3), dtype=np.float32)


class BrokenEmbedding:
    def encode(self, *args, **kwargs):
        raise RuntimeError("model not ready")


class FakeMilvusClient:
    def __init__(self):
        self.drop_count = 0
        self.inserted = None

    def has_collection(self, name):
        return True

    def drop_collection(self, name):
        self.drop_count += 1

    def create_collection(self, *args, **kwargs):
        return None

    def insert(self, name, data):
        self.inserted = data

    def search(self, *args, **kwargs):
        raise RuntimeError("index broken")


def _knowledge_base(embedding, client):
    from knowledge.milvus_kb import MedicalKnowledgeBase

    kb = object.__new__(MedicalKnowledgeBase)
    kb.collection_name = "kb"
    kb.embedding_model = embedding
    kb.milvus_client = client
    return kb


@pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS, reason="需要 sentence_transformers"
)
def test_add_documents_keeps_index_when_embedding_fails():
    client = FakeMilvusClient()
    kb = _knowledge_base(BrokenEmbedding(), client)

    with pytest.raises(RuntimeError, match="model not ready"):
        kb.add_documents(
            [{"id": "d1", "content": "内容", "metadata": {}}],
            replace_existing=True,
        )

    assert client.drop_count == 0


@pytest.mark.skipif(
    not HAS_SENTENCE_TRANSFORMERS, reason="需要 sentence_transformers"
)
def test_search_raises_on_backend_error():
    kb = _knowledge_base(FakeEmbedding(), FakeMilvusClient())

    with pytest.raises(ValueError, match="知识库检索失败"):
        kb.search("高血压")


def test_local_store_persists_atomically(tmp_path):
    store = LocalVectorStore(str(tmp_path / "kb.db"))
    store.create_collection("c", 3)
    store.insert("c", [{
        "vector": [0.1, 0.2, 0.3],
        "content": "x",
        "metadata": "{}",
    }])

    store_path = tmp_path / "kb.db.local.json"
    assert store_path.exists()
    assert not store_path.with_name(store_path.name + ".tmp").exists()
    data = json.loads(store_path.read_text(encoding="utf-8"))
    assert "c" in data
    assert len(data["c"]["contents"]) == 1


def test_local_store_save_errors_are_raised(tmp_path, monkeypatch):
    store = LocalVectorStore(str(tmp_path / "kb.db"))
    store.create_collection("c", 3)

    def broken_write(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", broken_write)

    with pytest.raises(OSError):
        store.insert("c", [{
            "vector": [0.1, 0.2, 0.3],
            "content": "x",
            "metadata": "{}",
        }])
