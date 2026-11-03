"""知识库检索分数换算的回归测试。

历史缺陷：COSINE 指标下 Milvus 返回的 distance 已经是相似度，
旧代码用 ``1 - distance`` 换算，导致相关文档分数被反转，
所有依赖 ``score > 0.5`` 的 Skill 都会筛掉真正相关的结果。
"""
from knowledge.scoring import cosine_similarity_to_score, hit_to_document


def test_relevant_similarity_is_kept():
    assert cosine_similarity_to_score(0.82) == 0.82
    assert cosine_similarity_to_score(0.51) == 0.51


def test_negative_similarity_is_clamped_to_zero():
    assert cosine_similarity_to_score(-0.3) == 0.0


def test_upper_bound_is_clamped_to_one():
    assert cosine_similarity_to_score(1.4) == 1.0


def test_invalid_values_fall_back_to_zero():
    assert cosine_similarity_to_score(None) == 0.0
    assert cosine_similarity_to_score("not-a-number") == 0.0


def test_relevance_threshold_behaves_as_expected():
    # 与技能里的阈值判断保持一致：高相似度应通过，低相似度不通过
    assert cosine_similarity_to_score(0.78) > 0.5
    assert cosine_similarity_to_score(0.12) < 0.5


def test_hit_to_document_uses_distance_as_similarity():
    hit = {
        "id": 42,
        "distance": 0.8557,
        "entity": {
            "content": "高血压患者应低盐饮食",
            "metadata": '{"type": "lifestyle", "disease": "hypertension"}',
        },
    }

    document = hit_to_document(hit)

    assert document["id"] == 42
    assert document["score"] == 0.8557
    assert document["metadata"]["type"] == "lifestyle"
    assert document["content"] == "高血压患者应低盐饮食"
