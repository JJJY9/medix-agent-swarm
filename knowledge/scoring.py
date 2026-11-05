"""知识库检索分数的归一化工具。

Milvus 在 COSINE 指标下返回的 ``distance`` 本身就是余弦相似度（越大越相似），
取值范围为 [-1, 1]。业务侧的阈值判断（例如「相似度 > 0.5 才算相关」）使用的是
[0, 1] 区间的分数，这里统一裁剪，保证 Milvus Lite 与本地降级向量库行为一致。
"""
from __future__ import annotations

import json
import math
from typing import Any, Dict


def cosine_similarity_to_score(similarity: float) -> float:
    """把 COSINE 检索结果转换为 [0, 1] 的相似度分数。

    Args:
        similarity: 向量库返回的余弦相似度（COSINE 指标下即相似度本身）。

    Returns:
        裁剪到 [0, 1] 的相似度分数；无法解析或 NaN 时返回 0.0。
    """
    try:
        value = float(similarity)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(value):
        # NaN 参与 min/max 会得到 1.0，被当成满分相关
        return 0.0
    return max(0.0, min(1.0, value))


def hit_to_document(hit: Dict[str, Any]) -> Dict[str, Any]:
    """把向量库返回的一条命中记录转换为业务侧的文档结构。

    Args:
        hit: MilvusClient.search 返回的命中项，包含 id、distance、entity。

    Returns:
        {"id", "content", "metadata", "score"} 结构。
    """
    return {
        "id": hit["id"],
        "content": hit["entity"]["content"],
        "metadata": json.loads(hit["entity"]["metadata"]),
        "score": cosine_similarity_to_score(hit["distance"]),
    }
