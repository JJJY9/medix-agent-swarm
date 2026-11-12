"""知识库索引新鲜度与损坏恢复的回归测试。"""
import json

import pytest

from knowledge.local_vector_store import LocalVectorStore
from knowledge.milvus_kb import (
    MedicalKnowledgeBase,
    _is_decimal_point,
    _sentence_boundary,
)
from knowledge.service import KnowledgeService, ensure_knowledge_index_fresh


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


def _service(tmp_path):
    docs = tmp_path / "docs"
    uploads = tmp_path / "uploads"
    docs.mkdir()
    uploads.mkdir()
    (docs / "guide.txt").write_text("高血压指南", encoding="utf-8")
    kb = FakeKB()
    return (
        KnowledgeService(docs_dir=docs, upload_dir=uploads, kb=kb),
        kb,
        uploads,
    )


def test_ensure_fresh_rebuilds_after_document_change(tmp_path):
    service, kb, uploads = _service(tmp_path)
    service.rebuild()
    first = kb.rebuilds

    (uploads / "custom.md").write_text("新上传文档", encoding="utf-8")
    assert service.index_is_stale() is True
    service.ensure_fresh()

    assert kb.rebuilds == first + 1
    assert service.index_is_stale() is False


def test_manifest_non_object_json_is_treated_as_stale(tmp_path):
    service, _kb, _uploads = _service(tmp_path)
    manifest = service.upload_dir.parent / "index_manifest.json"
    manifest.write_text("[1, 2, 3]", encoding="utf-8")

    # 合法 JSON 但不是对象，不能抛 AttributeError
    assert service.index_is_stale() is True


def test_local_store_rejects_inconsistent_index(tmp_path):
    store_path = tmp_path / "kb.db.local.json"
    store_path.write_text(
        json.dumps({
            "medical_knowledge": {
                "dimension": 4,
                "metric_type": "COSINE",
                "next_id": 2,
                "vectors": [[0.1, 0.2, 0.3, 0.4]],
                "contents": ["一", "二"],
                "metadata": [{"type": "general"}, {}],
            }
        }),
        encoding="utf-8",
    )

    store = LocalVectorStore(str(tmp_path / "kb.db"))

    assert store.load_error
    assert not store.has_collection("medical_knowledge")


def test_local_store_accepts_empty_vectors(tmp_path):
    store_path = tmp_path / "kb.db.local.json"
    store_path.write_text(
        json.dumps({
            "medical_knowledge": {
                "dimension": 4,
                "metric_type": "COSINE",
                "next_id": 1,
                "vectors": [],
                "contents": [],
                "metadata": [],
            }
        }),
        encoding="utf-8",
    )

    store = LocalVectorStore(str(tmp_path / "kb.db"))

    assert not store.load_error
    assert store.has_collection("medical_knowledge")
    assert store.collections["medical_knowledge"]["vectors"].shape == (0, 4)


def test_decimal_point_is_not_a_sentence_boundary():
    text = "剂量 3.5 mg，继续观察。"

    assert _is_decimal_point(text, text.index(".")) is True
    # 文本末尾的句号才是句界
    boundary = _sentence_boundary(text, 0, len(text))
    assert text[boundary] == "。"


def test_chunk_text_does_not_split_decimal(tmp_path):
    kb = object.__new__(MedicalKnowledgeBase)
    kb.embedding_model = type(
        "Model", (), {"max_seq_length": 0}
    )()
    text = ("a" * 8) + " 3.5 mg " + ("b" * 20)

    chunks = kb._chunk_text(text, chunk_size=12, overlap=0)

    assert all(not chunk.endswith("3.") for chunk in chunks)
    assert any("3.5" in chunk for chunk in chunks)


def test_module_level_freshness_helper(monkeypatch):
    calls = []

    class FakeService:
        def ensure_fresh(self):
            calls.append(True)

    monkeypatch.setattr(
        "knowledge.service.get_knowledge_service",
        lambda: FakeService(),
    )
    ensure_knowledge_index_fresh()

    assert calls == [True]


def test_skill_scripts_refresh_index_before_creating_kb():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    scripts = {
        "analyze-symptoms": "symptoms.py",
        "assess-risk": "risk.py",
        "clinical-guideline": "guideline.py",
        "disease-code": "code.py",
        "recommend-lifestyle": "lifestyle.py",
        "search-knowledge": "search.py",
    }
    for name, filename in scripts.items():
        script = (
            root / ".claude" / "skills" / name / "script" / filename
        ).read_text(encoding="utf-8")
        assert "ensure_knowledge_index_fresh()" in script, name
    research = (
        root / "research" / "deep_research_workflow.py"
    ).read_text(encoding="utf-8")
    assert "ensure_knowledge_index_fresh()" in research
