"""知识库分块边界的回归测试（需要 sentence-transformers）。"""
import pytest

pytest.importorskip("sentence_transformers")

from knowledge.milvus_kb import (  # noqa: E402
    MedicalKnowledgeBase,
    _is_decimal_point,
    _sentence_boundary,
)


def test_decimal_point_is_not_a_sentence_boundary():
    text = "剂量 3.5 mg，继续观察。"

    assert _is_decimal_point(text, text.index(".")) is True
    boundary = _sentence_boundary(text, 0, len(text))
    assert text[boundary] == "。"


def test_chunk_text_does_not_split_decimal():
    kb = object.__new__(MedicalKnowledgeBase)
    kb.embedding_model = type(
        "Model", (), {"max_seq_length": 0}
    )()
    text = ("a" * 8) + " 3.5 mg " + ("b" * 20)

    chunks = kb._chunk_text(text, chunk_size=12, overlap=0)

    assert all(not chunk.endswith("3.") for chunk in chunks)
    assert any("3.5" in chunk for chunk in chunks)
