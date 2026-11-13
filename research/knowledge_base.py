"""
本地知识库模块

基于 Qdrant 向量数据库，支持医学文档的存储和检索
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass, field
from datetime import datetime
from loguru import logger
import uuid
import asyncio

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    QDRANT_AVAILABLE = True
except ImportError:
    QDRANT_AVAILABLE = False
    logger.warning("Qdrant client not available, will use in-memory storage")

try:
    from sentence_transformers import SentenceTransformer
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    logger.warning("sentence-transformers not available, embeddings disabled")


@dataclass
class Document:
    """文档数据结构"""
    id: str
    content: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    vector: Optional[List[float]] = None
    score: float = 0.0  # 相似度分数
    created_at: datetime = field(default_factory=datetime.now)


class KnowledgeBase:
    """
    本地知识库

    功能：
    - 文档向量化和存储
    - 基于向量相似度的检索
    - 支持 Qdrant 向量数据库或内存存储
    """

    def __init__(
        self,
        collection_name: str = "medical_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5",
        qdrant_host: str = "localhost",
        qdrant_port: int = 6333,
        use_memory: bool = False  # 是否强制使用内存存储
    ):
        """
        初始化知识库

        Args:
            collection_name: 集合名称
            embedding_model: Embedding 模型名称
            qdrant_host: Qdrant 服务地址
            qdrant_port: Qdrant 服务端口
            use_memory: 是否使用内存存储
        """
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model
        self.use_memory = use_memory

        # 初始化 Embedding 模型；内存存储同样需要向量才能检索
        self.embedding_model = None
        self.vector_size = 0

        if EMBEDDING_AVAILABLE:
            try:
                logger.info(f"Loading embedding model: {embedding_model}")
                self.embedding_model = SentenceTransformer(embedding_model)
                self.vector_size = self.embedding_model.get_sentence_embedding_dimension()
                logger.info(f"Embedding model loaded (dimension={self.vector_size})")
            except Exception as e:
                logger.error(f"Failed to load embedding model: {e}")
        else:
            logger.error("sentence-transformers 不可用，知识库无法向量化")

        # 初始化存储后端
        self.qdrant_client = None
        self.memory_storage: Dict[str, Document] = {}  # 内存存储
        #: Qdrant 初始化失败原因；非空表示已降级为易失内存存储
        self.backend_error = ""

        if not use_memory and QDRANT_AVAILABLE and self.embedding_model:
            try:
                # 尝试连接 Qdrant
                self.qdrant_client = QdrantClient(
                    host=qdrant_host, port=qdrant_port
                )
                self._init_collection()
                logger.info(
                    f"Connected to Qdrant at {qdrant_host}:{qdrant_port}"
                )
            except Exception as e:
                # 明确记录降级：内存存储重启即丢失，不能伪装成已持久化
                self.qdrant_client = None
                self.backend_error = str(e)
                logger.warning(
                    "Failed to connect to Qdrant: "
                    f"{e}；已降级为进程内存存储（重启后丢失）"
                )
        else:
            logger.info("Using in-memory storage for knowledge base")

    def _init_collection(self):
        """初始化 Qdrant 集合"""
        if not self.qdrant_client:
            return

        try:
            # 检查集合是否存在
            collections = self.qdrant_client.get_collections().collections
            collection_names = [c.name for c in collections]

            if self.collection_name not in collection_names:
                # 创建集合
                self.qdrant_client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                logger.info(f"Created collection: {self.collection_name}")
            else:
                logger.info(f"Collection already exists: {self.collection_name}")

        except Exception as e:
            logger.error(f"Failed to initialize collection: {e}")
            # 交给构造函数记录降级原因；不能在这里假装连接成功
            raise RuntimeError(f"Qdrant 集合初始化失败：{e}") from e

    def _embed_text(self, text: str) -> Optional[List[float]]:
        """
        文本向量化

        Args:
            text: 输入文本

        Returns:
            向量表示
        """
        if not self.embedding_model:
            return None

        try:
            vector = self.embedding_model.encode(text, convert_to_numpy=True)
            return vector.tolist()
        except Exception as e:
            logger.error(f"Failed to embed text: {e}")
            return None

    async def add_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None
    ) -> str:
        """
        添加文档到知识库

        Args:
            content: 文档内容
            metadata: 元数据
            doc_id: 文档ID（可选）

        Returns:
            文档ID
        """
        if doc_id is None:
            doc_id = str(uuid.uuid4())

        if metadata is None:
            metadata = {}

        # 向量化
        vector = self._embed_text(content)
        if vector is None:
            raise RuntimeError(
                "向量模型不可用，文档未写入知识库（请检查 sentence-transformers）"
            )

        # 创建文档对象
        document = Document(
            id=doc_id,
            content=content,
            metadata=metadata,
            vector=vector
        )

        # 存储
        if self.qdrant_client:
            # 存储到 Qdrant
            try:
                point = PointStruct(
                    id=doc_id,
                    vector=vector,
                    payload={
                        "content": content,
                        "metadata": metadata,
                        "created_at": document.created_at.isoformat()
                    }
                )
                self.qdrant_client.upsert(
                    collection_name=self.collection_name,
                    points=[point]
                )
                logger.info(f"Document added to Qdrant: {doc_id}")
            except Exception as e:
                logger.error(f"Failed to add document to Qdrant: {e}")
                # 写入失败必须让调用方知道，不能返回空 ID 伪装成成功
                raise RuntimeError(f"Qdrant 写入失败：{e}") from e
        else:
            # 存储到内存
            self.memory_storage[doc_id] = document
            logger.info(f"Document added to memory: {doc_id}")

        return doc_id

    @staticmethod
    def _matches_filters(
        metadata: Any, filters: Optional[Dict[str, Any]]
    ) -> bool:
        """按 metadata 过滤；值为列表时按“属于其中任一”处理。"""
        if not filters:
            return True
        if not isinstance(metadata, dict):
            return False
        for key, expected in filters.items():
            actual = metadata.get(key)
            if isinstance(expected, (list, tuple, set)):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _build_qdrant_filter(filters: Optional[Dict[str, Any]]):
        """构造 Qdrant 过滤条件；返回 (filter, 是否已由服务端过滤)。"""
        if not filters:
            return None, False
        try:
            from qdrant_client.models import (
                FieldCondition,
                Filter,
                MatchValue,
            )
        except Exception:
            return None, False
        conditions = []
        for key, value in filters.items():
            if not isinstance(value, (str, int, float, bool)):
                # 列表等复杂条件退回客户端过滤，保证结果正确
                return None, False
            conditions.append(FieldCondition(
                key=f"metadata.{key}",
                match=MatchValue(value=value),
            ))
        return Filter(must=conditions), True

    async def search(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: float = 0.0,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Document]:
        """
        检索相似文档

        Args:
            query: 查询文本
            top_k: 返回结果数量
            score_threshold: 相似度阈值
            filters: 过滤条件

        Returns:
            相似文档列表
        """
        # 向量化查询
        query_vector = self._embed_text(query)
        if query_vector is None:
            raise ValueError(
                "向量模型不可用，无法检索知识库（请检查 sentence-transformers）"
            )

        if self.qdrant_client:
            # 从 Qdrant 检索
            try:
                qdrant_filter, server_filtered = (
                    self._build_qdrant_filter(filters)
                )
                # 复杂过滤条件退回客户端过滤时多取一些候选
                limit = top_k
                if filters and not server_filtered:
                    limit = max(top_k * 5, 50)
                search_result = self.qdrant_client.search(
                    collection_name=self.collection_name,
                    query_vector=query_vector,
                    limit=limit,
                    score_threshold=score_threshold,
                    query_filter=qdrant_filter,
                )

                documents = []
                for hit in search_result:
                    payload = hit.payload or {}
                    metadata = payload.get("metadata") or {}
                    if not self._matches_filters(metadata, filters):
                        continue
                    doc = Document(
                        id=hit.id,
                        content=payload.get("content", ""),
                        metadata=metadata,
                        score=hit.score
                    )
                    documents.append(doc)

                documents = documents[:top_k]
                logger.info(f"Found {len(documents)} documents from Qdrant")
                return documents

            except Exception as e:
                logger.error(f"Qdrant search error: {e}")
                # 后端故障不能伪装成“没有匹配文档”
                raise RuntimeError(f"Qdrant 检索失败：{e}") from e
        else:
            # 从内存检索（简单的余弦相似度）；返回副本，避免后续检索
            # 原地改写已返回结果的相关度
            results = []
            for doc in self.memory_storage.values():
                if not doc.vector:
                    continue
                if not self._matches_filters(doc.metadata, filters):
                    continue
                score = self._cosine_similarity(
                    query_vector, doc.vector
                )
                if score >= score_threshold:
                    results.append(Document(
                        id=doc.id,
                        content=doc.content,
                        metadata=dict(doc.metadata or {}),
                        vector=list(doc.vector),
                        score=score,
                        created_at=doc.created_at,
                    ))

            # 按分数排序
            results.sort(key=lambda x: x.score, reverse=True)
            results = results[:top_k]

            logger.info(f"Found {len(results)} documents from memory")
            return results

    def _cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """计算余弦相似度"""
        import math

        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(b * b for b in vec2))

        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0

        return dot_product / (magnitude1 * magnitude2)

    async def bulk_add(
        self,
        documents: List[Dict[str, Any]],
        batch_size: int = 100
    ) -> int:
        """
        批量添加文档

        Args:
            documents: 文档列表，每个文档包含 'content' 和 'metadata'
            batch_size: 批处理大小

        Returns:
            成功添加的文档数量
        """
        added_count = 0

        for i in range(0, len(documents), batch_size):
            batch = documents[i:i + batch_size]

            for doc in batch:
                doc_id = await self.add_document(
                    content=doc.get("content", ""),
                    metadata=doc.get("metadata", {})
                )
                if doc_id:
                    added_count += 1

            logger.info(f"Batch {i // batch_size + 1}: Added {added_count}/{len(documents)} documents")

        return added_count

    def get_stats(self) -> Dict[str, Any]:
        """获取知识库统计信息"""
        if self.qdrant_client:
            try:
                collection_info = self.qdrant_client.get_collection(self.collection_name)
                return {
                    "backend": "qdrant",
                    "collection": self.collection_name,
                    "count": collection_info.points_count,
                    "vector_size": self.vector_size
                }
            except Exception as e:
                logger.error(f"Failed to get Qdrant stats: {e}")
                return {"backend": "qdrant", "error": str(e)}
        else:
            stats = {
                "backend": "memory",
                "count": len(self.memory_storage),
                "vector_size": self.vector_size if self.embedding_model else 0,
            }
            if self.backend_error:
                stats["fallback_reason"] = self.backend_error
                stats["volatile"] = True
            return stats


# 便捷函数
async def search_knowledge_base(
    query: str,
    top_k: int = 5
) -> List[Document]:
    """
    快速检索知识库

    Args:
        query: 查询文本
        top_k: 返回结果数量

    Returns:
        相似文档列表
    """
    kb = KnowledgeBase()
    return await kb.search(query, top_k=top_k)
