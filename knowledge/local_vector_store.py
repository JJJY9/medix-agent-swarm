"""
本地向量库（Windows 兼容）

背景：Milvus Lite（`MilvusClient("./xxx.db")`）只提供 Linux / macOS 的 wheel，
Windows 上导入 `milvus_lite` 会直接 ModuleNotFoundError。

本模块用 numpy 实现了一个与 pymilvus `MilvusClient` 常用接口对齐的本地向量库，
作为 Windows 上的降级方案：接口一致，`MedicalKnowledgeBase` 无需区分平台。
知识库只有几十个文档块，暴力计算余弦相似度的开销可以忽略。

数据以 JSON 形式持久化到 `<db_path>.local.json`。
"""
import json
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from loguru import logger


def _decode_like_pattern(expression: str) -> Optional[str]:
    """
    解析 Milvus 的 `field like "%..."` 过滤表达式，取出其中的匹配子串。

    只支持知识库代码实际用到的形式（metadata like "%\"type\": \"lifestyle\"%"），
    其余表达式返回 None，表示不过滤。
    """
    match = re.match(r'^\s*\w+\s+like\s+"(.*)"\s*$', expression, re.DOTALL)
    if not match:
        return None

    raw = match.group(1)
    # Milvus 的转义写法 \" -> "
    raw = raw.replace('\\"', '"').replace("\\\\", "\\")
    # SQL LIKE 的通配符：去掉首尾的 %
    return raw.strip("%")


class LocalVectorStore:
    """
    numpy 实现的本地向量库，接口对齐 pymilvus 的 MilvusClient。

    只实现了 MedicalKnowledgeBase 用到的子集：
    has_collection / create_collection / insert / search / drop_collection
    / describe_collection
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.store_path = self.db_path.with_suffix(self.db_path.suffix + ".local.json")
        self.collections: Dict[str, Dict[str, Any]] = {}
        #: 索引文件损坏时记录原因，搜索/统计会显式报错而不是静默返回空
        self.load_error = ""
        self._load()

    # ===== 持久化 =====

    def _load(self):
        if not self.store_path.exists():
            return
        try:
            data = json.loads(self.store_path.read_text(encoding="utf-8"))
            for name, coll in data.items():
                coll["vectors"] = np.array(coll["vectors"], dtype=np.float32)
                self.collections[name] = coll
            logger.info(f"Loaded local vector store: {self.store_path}")
        except Exception as e:
            self.load_error = str(e)
            logger.error(f"Failed to load local vector store: {e}")

    def _save(self):
        try:
            self.store_path.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for name, coll in self.collections.items():
                data[name] = {
                    "dimension": coll["dimension"],
                    "metric_type": coll["metric_type"],
                    "next_id": coll["next_id"],
                    "vectors": coll["vectors"].tolist(),
                    "contents": coll["contents"],
                    "metadata": coll["metadata"],
                }
            payload = json.dumps(data, ensure_ascii=False)
            temp_path = self.store_path.with_name(
                f"{self.store_path.name}.tmp"
            )
            temp_path.write_text(payload, encoding="utf-8")
            os.replace(temp_path, self.store_path)
            # 成功落盘后之前“文件损坏”的状态已解除（重建可恢复）
            self.load_error = ""
        except Exception as e:
            logger.error(f"Failed to save local vector store: {e}")
            raise

    # ===== MilvusClient 兼容接口 =====

    def has_collection(self, collection_name: str) -> bool:
        return collection_name in self.collections

    def create_collection(
        self,
        collection_name: str,
        dimension: int,
        metric_type: str = "COSINE",
        auto_id: bool = True,
        **kwargs,
    ):
        if collection_name in self.collections:
            return
        self.collections[collection_name] = {
            "dimension": dimension,
            "metric_type": metric_type,
            "next_id": 1,
            "vectors": np.zeros((0, dimension), dtype=np.float32),
            "contents": [],
            "metadata": [],
        }
        logger.info(f"Created local collection: {collection_name} (dim={dimension})")

    def insert(self, collection_name: str, data: List[Dict[str, Any]]) -> Dict[str, Any]:
        coll = self.collections.get(collection_name)
        if coll is None:
            raise ValueError(f"Collection not found: {collection_name}")
        if not data:
            return {"insert_count": 0, "ids": []}

        vectors = np.array([row["vector"] for row in data], dtype=np.float32)
        ids = list(range(coll["next_id"], coll["next_id"] + len(data)))

        coll["vectors"] = np.vstack([coll["vectors"], vectors])
        coll["contents"].extend(row["content"] for row in data)
        coll["metadata"].extend(row["metadata"] for row in data)
        coll["next_id"] += len(data)

        self._save()
        return {"insert_count": len(data), "ids": ids}

    def search(
        self,
        collection_name: str,
        data: List[List[float]],
        limit: int = 5,
        filter: Optional[str] = None,
        output_fields: Optional[List[str]] = None,
        **kwargs,
    ) -> List[List[Dict[str, Any]]]:
        if self.load_error:
            raise RuntimeError(
                f"本地向量索引加载失败，请重建知识库: {self.load_error}"
            )
        coll = self.collections.get(collection_name)
        if coll is None or len(coll["contents"]) == 0:
            return [[]]

        # 过滤候选集
        pattern = _decode_like_pattern(filter) if filter else None
        if pattern:
            candidates = [
                i for i, meta in enumerate(coll["metadata"]) if pattern in meta
            ]
        else:
            candidates = list(range(len(coll["contents"])))

        results = []
        for query in data:
            if not candidates:
                results.append([])
                continue

            q = np.array(query, dtype=np.float32)
            q_norm = np.linalg.norm(q)
            mat = coll["vectors"][candidates]
            norms = np.linalg.norm(mat, axis=1)

            denom = norms * q_norm
            denom[denom == 0] = 1e-10
            similarities = (mat @ q) / denom

            order = np.argsort(similarities)[::-1][:limit]

            hits = []
            for idx in order:
                real_idx = candidates[int(idx)]
                hits.append({
                    "id": real_idx,
                    # 与 Milvus COSINE 语义保持一致：distance 即余弦相似度
                    "distance": float(similarities[int(idx)]),
                    "entity": {
                        "content": coll["contents"][real_idx],
                        "metadata": coll["metadata"][real_idx],
                    },
                })
            results.append(hits)

        return results

    def drop_collection(self, collection_name: str):
        self.collections.pop(collection_name, None)
        self._save()

    def rename_collection(self, old_name: str, new_name: str):
        """原子改名：仅移动内存中的引用后一次性落盘。"""
        if old_name not in self.collections:
            raise ValueError(f"Collection not found: {old_name}")
        if new_name in self.collections:
            raise ValueError(f"Collection already exists: {new_name}")
        self.collections[new_name] = self.collections.pop(old_name)
        self._save()

    def describe_collection(self, collection_name: str) -> Dict[str, Any]:
        if self.load_error:
            raise RuntimeError(
                f"本地向量索引加载失败，请重建知识库: {self.load_error}"
            )
        coll = self.collections.get(collection_name)
        if coll is None:
            return {}
        return {"num_entities": len(coll["contents"])}
