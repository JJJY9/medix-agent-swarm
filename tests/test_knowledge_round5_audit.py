"""第五轮知识库与 RAG 审计回归：分块、重建、上传元数据与去重。"""
import asyncio
import importlib.util
import json
import threading
from pathlib import Path

import numpy as np
import pytest

from knowledge.documents import load_document
from knowledge.local_vector_store import LocalVectorStore
from knowledge.service import KnowledgeService

PROJECT_ROOT = Path(__file__).resolve().parent.parent


class FakeEmbeddingModel:
    def __init__(self, max_seq_length=512):
        self.max_seq_length = max_seq_length

    def encode(self, texts, show_progress_bar=False):
        return np.ones((len(texts), 3), dtype=np.float32)


def _knowledge_base(client, collection="c"):
    # CI 不安装 sentence-transformers，相关用例在缺少依赖时跳过
    pytest.importorskip("sentence_transformers")
    from knowledge.milvus_kb import MedicalKnowledgeBase

    kb = object.__new__(MedicalKnowledgeBase)
    kb.milvus_client = client
    kb.collection_name = collection
    kb.embedding_dim = 3
    kb.embedding_model = FakeEmbeddingModel()
    kb._initialized = True
    # 绕过 __init__ 构造时需补齐索引读写锁，重建路径会在锁内替换集合
    kb._index_lock = threading.RLock()
    return kb


def test_chunks_are_capped_by_embedding_max_length():
    kb = _knowledge_base(None)
    kb.embedding_model = FakeEmbeddingModel(max_seq_length=512)

    chunks = kb._chunk_text("x" * 2000, chunk_size=1024, overlap=100)

    assert all(len(chunk) <= 512 for chunk in chunks)
    assert len(chunks) >= 4


def test_rebuild_uses_staging_collection(tmp_path):
    store = LocalVectorStore(str(tmp_path / "kb.db"))
    kb = _knowledge_base(store)

    first = kb.add_documents(
        [{"id": "d1", "content": "旧内容", "metadata": {"type": "general"}}],
        replace_existing=True,
    )
    assert first == 1

    second = kb.add_documents(
        [{"id": "d1", "content": "新内容", "metadata": {"type": "general"}}],
        replace_existing=True,
    )
    assert second == 1

    assert not store.has_collection("c__staging")
    hits = store.search("c", [[1.0, 1.0, 1.0]], limit=5)
    contents = [hit["entity"]["content"] for hit in hits[0]]
    assert contents == ["新内容"]


def test_shadowed_upload_is_not_indexed(tmp_path):
    docs = tmp_path / "documents"
    uploads = tmp_path / "uploads"
    docs.mkdir()
    uploads.mkdir()
    (docs / "20_guideline_hypertension.txt").write_text(
        "内置高血压指南", encoding="utf-8"
    )
    (uploads / "20_guideline_hypertension.txt").write_text(
        "过时的同名上传", encoding="utf-8"
    )

    class RecordingKB:
        def __init__(self):
            self.documents = None

        def add_documents(self, documents, replace_existing=True):
            self.documents = documents
            return len(documents)

        def count_documents(self):
            return len(self.documents or [])

    kb = RecordingKB()
    service = KnowledgeService(docs_dir=docs, upload_dir=uploads, kb=kb)

    result = service.rebuild()

    assert result["shadowed_uploads"] == 1
    assert result["uploaded_documents"] == 0
    assert len(kb.documents) == 1
    assert not kb.documents[0]["id"].startswith("upload_")
    assert kb.documents[0]["content"] == "内置高血压指南"


def test_upload_metadata_ignores_builtin_filename_convention(tmp_path):
    path = tmp_path / "20_guideline_hypertension.txt"
    path.write_text("用户上传的指南摘要", encoding="utf-8")

    document = load_document(path, id_prefix="upload_", is_upload=True)

    assert document["metadata"]["type"] == "general"
    assert document["metadata"]["source"] == "自定义文档"
    assert document["id"].startswith("upload_general_")


def _load_guideline_skill():
    path = (
        PROJECT_ROOT / ".claude" / "skills" / "clinical-guideline"
        / "script" / "guideline.py"
    )
    spec = importlib.util.spec_from_file_location("medix_guideline_skill", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_guideline_title_ignores_mid_document_chunk(monkeypatch):
    module = _load_guideline_skill()

    class FakeKB:
        def search(self, **kwargs):
            return [{
                "content": "0/90mmHg，无并发症的患者可先生活方式干预。",
                "metadata": {
                    "disease": "高血压",
                    "chunk_id": 3,
                    "type": "clinical_guideline",
                },
                "score": 0.9,
            }]

    monkeypatch.setattr(module, "get_knowledge_base", lambda: FakeKB())

    result = asyncio.run(module.clinical_guideline("高血压"))

    assert result["guideline_title"] == "高血压相关临床指南"


def test_count_documents_uses_collection_stats():
    class StatsClient:
        def get_collection_stats(self, name):
            return {"row_count": 7}

        def describe_collection(self, name):
            return {"collection_name": name}

    kb = _knowledge_base(StatsClient())

    assert kb.count_documents() == 7


def test_corrupt_local_index_is_reported_and_rebuildable(tmp_path):
    store_path = tmp_path / "kb.db.local.json"
    store_path.write_text("{not json", encoding="utf-8")

    store = LocalVectorStore(str(tmp_path / "kb.db"))

    assert store.load_error
    try:
        store.search("c", [[1.0, 1.0, 1.0]])
    except RuntimeError as exc:
        assert "重建知识库" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("损坏的索引不应静默返回空结果")

    # 重建路径（drop + create + insert）可以覆盖损坏文件并恢复
    store.drop_collection("c")
    store.create_collection("c", 3)
    store.insert("c", [{
        "vector": [0.1, 0.2, 0.3],
        "content": "恢复内容",
        "metadata": json.dumps({"type": "general"}),
    }])
    assert store.load_error == ""
    assert store.describe_collection("c")["num_entities"] == 1


def test_deep_research_deduplicates_kb_hits():
    pytest.importorskip("sentence_transformers")
    from research.deep_research_workflow import DeepResearchWorkflow

    hits = [
        {
            "content": "同一段指南",
            "metadata": {"doc_id": "d1", "chunk_id": 0},
            "score": 0.8,
        },
        {
            "content": "同一段指南",
            "metadata": {"doc_id": "d1", "chunk_id": 0},
            "score": 0.6,
        },
        {
            "content": "另一段指南",
            "metadata": {"doc_id": "d1", "chunk_id": 1},
            "score": 0.5,
        },
    ]

    deduped = DeepResearchWorkflow._dedupe_kb_results(hits)

    assert len(deduped) == 2
    assert deduped[0]["score"] == 0.8
