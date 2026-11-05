"""知识库文档解析、来源卡片与分数边界的回归测试。"""
import math

import pytest

from knowledge.documents import infer_metadata, read_text_file
from knowledge.scoring import cosine_similarity_to_score
from knowledge.service import KnowledgeService
from knowledge.sources import build_sources


def test_reads_utf16_with_bom(tmp_path):
    path = tmp_path / "utf16.txt"
    path.write_bytes(b"\xff\xfe" + "高血压饮食建议".encode("utf-16-le"))

    assert read_text_file(path) == "高血压饮食建议"


def test_reads_utf8_bom_without_marker(tmp_path):
    path = tmp_path / "bom.md"
    path.write_bytes(b"\xef\xbb\xbf" + "内容".encode("utf-8"))

    assert read_text_file(path) == "内容"


def test_reads_gb18030(tmp_path):
    path = tmp_path / "gbk.txt"
    path.write_bytes("中文内容".encode("gb18030"))

    assert read_text_file(path) == "中文内容"


def test_rejects_binary_garbage(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_bytes(b"\x00\x01\x02" * 100)

    with pytest.raises(ValueError, match="编码"):
        read_text_file(path)


def test_infer_metadata_maps_symptoms_category():
    metadata = infer_metadata("05_symptoms_emergency.txt", "内容")

    assert metadata["type"] == "symptoms"
    assert metadata["source"] == "症状与急症提示库"
    assert metadata["disease"] == "emergency"


def test_infer_metadata_ignores_unrelated_third_segment():
    metadata = infer_metadata("health_tips_2024.md", "健康小贴士\n内容")

    assert metadata["type"] == "general"
    assert metadata["disease"] == "健康小贴士"


def test_safe_filename_keeps_extension_when_truncated():
    name = KnowledgeService._safe_filename("a" * 125 + ".md")

    assert name.endswith(".md")
    assert len(name) <= 120


def test_build_sources_deduplicates_same_document():
    results = [
        {
            "id": 1,
            "content": "块一",
            "metadata": {"filename": "a.md", "disease": "甲"},
            "score": 0.4,
        },
        {
            "id": 2,
            "content": "块二",
            "metadata": {"filename": "a.md", "disease": "甲"},
            "score": 0.9,
        },
        {
            "id": 3,
            "content": "另一篇",
            "metadata": {"filename": "b.md", "disease": "乙"},
            "score": 0.6,
        },
    ]

    sources = build_sources(results, max_items=3)

    assert len(sources) == 2
    assert sources[0]["filename"] == "a.md"
    assert sources[0]["score"] == 0.9
    assert sources[0]["snippet"] == "块二"


def test_nan_similarity_is_not_top_score():
    assert cosine_similarity_to_score(float("nan")) == 0.0
    assert cosine_similarity_to_score(float("inf")) == 0.0


def test_build_sources_handles_nan_score():
    sources = build_sources([
        {
            "id": 1,
            "content": "x",
            "metadata": {"filename": "a.md"},
            "score": math.nan,
        }
    ])

    assert sources[0]["score"] == 0.0
