"""知识库分块不应切断医学数值与单位。"""
from types import SimpleNamespace

import pytest

from knowledge.milvus_kb import MedicalKnowledgeBase


def _chunker():
    kb = object.__new__(MedicalKnowledgeBase)
    kb.embedding_model = SimpleNamespace(max_seq_length=0)
    return kb


def test_chunk_keeps_numeric_units_intact():
    pytest.importorskip("sentence_transformers")
    kb = _chunker()
    text = "A" * 10 + " 140/90mmHg " + "B" * 10

    chunks = kb._chunk_text(text, chunk_size=12, overlap=0)

    assert any("140/90mmHg" in chunk for chunk in chunks)
    for current, following in zip(chunks, chunks[1:]):
        assert not (
            current.endswith("mm") and following.startswith("Hg")
        )
        assert not (
            current.endswith("140/90") and following.startswith("mmHg")
        )


def test_chunk_prefers_sentence_boundary():
    pytest.importorskip("sentence_transformers")
    kb = _chunker()
    text = "第一句内容比较长。第二句内容也比较长。第三句。"

    chunks = kb._chunk_text(text, chunk_size=10, overlap=0)

    assert chunks[0].endswith("。")
    assert "".join(chunks) == text


def test_chunk_makes_progress_on_long_token():
    pytest.importorskip("sentence_transformers")
    kb = _chunker()
    text = "x" * 50

    chunks = kb._chunk_text(text, chunk_size=10, overlap=2)

    assert all(chunks)
    assert len(chunks) < 20
