"""
医学知识库（Milvus）

功能：
1. 文档向量化和存储
2. 语义检索
3. 知识库管理
"""
import json
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional
from loguru import logger

try:
    from pymilvus import MilvusClient
except ImportError:  # pymilvus 未安装时直接使用本地向量库
    MilvusClient = None

from sentence_transformers import SentenceTransformer

from .scoring import hit_to_document


def _create_vector_store(db_path: str):
    """
    创建向量库客户端。

    Milvus Lite 只有 Linux / macOS 的 wheel，Windows 上无法使用，
    此时降级到 LocalVectorStore（接口一致，纯 numpy 实现）。
    """
    try:
        client = MilvusClient(db_path)
        logger.info(f"Connected to Milvus Lite: {db_path}")
        return client
    except Exception as e:
        logger.warning(f"Milvus Lite unavailable ({e}), falling back to local vector store")
        from .local_vector_store import LocalVectorStore
        return LocalVectorStore(db_path)


class MedicalKnowledgeBase:
    """医学知识库"""

    _instance = None
    _instance_lock = threading.Lock()
    _init_lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        """实现线程安全的单例模式"""
        with cls._instance_lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
            return cls._instance

    def __init__(self, *args, **kwargs):
        with type(self)._init_lock:
            if hasattr(self, '_initialized'):
                return
            self._initialize(*args, **kwargs)

    def _initialize(
        self,
        db_path: str = "./knowledge/data/milvus_lite.db",
        collection_name: str = "medical_knowledge",
        embedding_model: str = "BAAI/bge-small-zh-v1.5"
    ):
        """
        初始化医学知识库

        Args:
            db_path: Milvus Lite 数据库文件路径
            collection_name: Collection 名称
            embedding_model: Embedding 模型名称或本地路径
        """
        # 防止重复初始化
        if hasattr(self, '_initialized'):
            return

        self.db_path = db_path
        self.collection_name = collection_name
        # 重建索引在 drop→rename 之间会短暂没有 collection；
        # 用读写锁让并发检索等待替换完成，避免会诊静默检索为空
        self._index_lock = threading.RLock()

        # 确保数据目录存在
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        # 初始化 Embedding 模型（支持本地路径）
        # 优先检查本地缓存路径
        local_model_path = Path.home() / ".cache" / "huggingface" / "hub" / "models--BAAI--bge-small-zh-v1.5" / "snapshots"

        if local_model_path.exists():
            # 找到最新的 snapshot
            snapshots = sorted(local_model_path.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)
            if snapshots:
                model_path = str(snapshots[0])
                logger.info(f"Loading embedding model from local cache: {model_path}")
                self.embedding_model = SentenceTransformer(model_path, device='cpu')
            else:
                logger.info(f"Loading embedding model: {embedding_model}")
                self.embedding_model = SentenceTransformer(embedding_model, device='cpu')
        else:
            logger.info(f"Loading embedding model: {embedding_model}")
            self.embedding_model = SentenceTransformer(embedding_model, device='cpu')

        self.embedding_dim = self.embedding_model.get_sentence_embedding_dimension()
        logger.info(f"Embedding model loaded (dimension={self.embedding_dim})")

        # 初始化向量库（Milvus Lite，Windows 上自动降级到本地实现）
        logger.info(f"Initializing vector store: {db_path}")
        self.milvus_client = _create_vector_store(db_path)

        # 创建 collection（如果不存在）
        self._ensure_collection()

        self._initialized = True

    def _ensure_collection(self):
        """确保 collection 存在（重建索引后会再次调用）。"""
        if not self.milvus_client.has_collection(self.collection_name):
            logger.info(f"Creating collection: {self.collection_name}")
            self.milvus_client.create_collection(
                collection_name=self.collection_name,
                dimension=self.embedding_dim,
                metric_type="COSINE",  # 余弦相似度
                auto_id=True  # 自动生成整数ID
            )
        else:
            logger.info(f"Collection already exists: {self.collection_name}")

    def _chunk_text(self, text: str, chunk_size: int = 1024, overlap: int = 100) -> List[str]:
        """
        分块文本

        Args:
            text: 原始文本
            chunk_size: 块大小（字符数）
            overlap: 重叠字符数

        Returns:
            文本块列表
        """
        # Embedding 模型有最大序列长度，超出部分会被静默截断；
        # 按字符分块时至少把块长限制在模型上限内
        max_tokens = getattr(self.embedding_model, "max_seq_length", 0)
        try:
            max_tokens = int(max_tokens or 0)
        except (TypeError, ValueError):
            max_tokens = 0
        if max_tokens > 0:
            chunk_size = min(chunk_size, max_tokens)
            overlap = min(overlap, max(1, chunk_size // 4))

        if len(text) <= chunk_size:
            return [text]

        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunk = text[start:end]
            chunks.append(chunk)
            start = end - overlap  # 重叠

        return chunks

    def add_documents(
        self,
        documents: List[Dict[str, Any]],
        chunk_size: int = 1024,
        replace_existing: bool = False
    ) -> int:
        """
        添加文档到知识库（支持分块）

        Args:
            documents: 文档列表，每个文档包含 id, content, metadata
            chunk_size: 分块大小（字符数），默认 1024
            replace_existing: 是否先删除旧 collection 再导入（幂等重建）

        Returns:
            成功添加的文档块数量
        """
        if not documents:
            logger.warning("No documents to add")
            return 0

        logger.info(f"Adding {len(documents)} documents to knowledge base (chunk_size={chunk_size})...")

        # 分块并向量化
        all_chunks = []
        for doc in documents:
            chunks = self._chunk_text(doc["content"], chunk_size=chunk_size)
            for i, chunk in enumerate(chunks):
                metadata = doc.get("metadata", {}).copy()
                metadata["doc_id"] = doc["id"]
                metadata["chunk_id"] = i
                metadata["total_chunks"] = len(chunks)

                all_chunks.append({
                    "content": chunk,
                    "metadata": metadata
                })

        logger.info(f"Split into {len(all_chunks)} chunks")

        # 向量化
        contents = [chunk["content"] for chunk in all_chunks]
        vectors = self.embedding_model.encode(contents, show_progress_bar=True)

        # 准备数据
        data = []
        for i, chunk in enumerate(all_chunks):
            data.append({
                "vector": vectors[i].tolist(),
                "content": chunk["content"],
                "metadata": json.dumps(chunk["metadata"], ensure_ascii=False)
            })

        # 先完成分块与向量化；重建时先写入临时 collection，
        # 成功后再原子替换，避免插入失败把旧索引清空
        if replace_existing:
            with self._index_lock:
                return self._replace_with_staging(data)

        # 插入
        self.milvus_client.insert(self.collection_name, data)
        logger.info(f"Successfully added {len(data)} chunks")

        return len(data)

    def _replace_with_staging(self, data: List[Dict[str, Any]]) -> int:
        """在持有索引锁时用 staging collection 原子替换正式索引。"""
        staging_name = f"{self.collection_name}__staging"
        if self.milvus_client.has_collection(staging_name):
            self.milvus_client.drop_collection(staging_name)
        self.milvus_client.create_collection(
            collection_name=staging_name,
            dimension=self.embedding_dim,
            metric_type="COSINE",
            auto_id=True,
        )
        try:
            self.milvus_client.insert(staging_name, data)
        except Exception:
            self.milvus_client.drop_collection(staging_name)
            raise

        try:
            if self.milvus_client.has_collection(self.collection_name):
                self.milvus_client.drop_collection(self.collection_name)
            self.milvus_client.rename_collection(
                staging_name, self.collection_name
            )
            logger.info(f"Successfully replaced with {len(data)} chunks")
            return len(data)
        except Exception as e:
            # 后端不支持 rename 时退回“清空后重建”，此时数据已在
            # staging 中写入过一次，二次插入失败的概率显著降低
            logger.warning(f"原子替换索引失败，回退到重建: {e}")
            if self.milvus_client.has_collection(self.collection_name):
                self.milvus_client.drop_collection(self.collection_name)
            self._ensure_collection()
            self.milvus_client.insert(self.collection_name, data)
            if self.milvus_client.has_collection(staging_name):
                self.milvus_client.drop_collection(staging_name)
            logger.info(f"Successfully added {len(data)} chunks")
            return len(data)

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        检索相关文档

        Args:
            query: 查询文本
            top_k: 返回top K个结果
            filter_type: 可选的类型过滤（如 "lifestyle", "disease_classification"）

        Returns:
            文档列表，每个文档包含 id, content, metadata, score
        """
        logger.debug(
            f"Searching knowledge base (query length={len(str(query or ''))}, "
            f"top_k={top_k}, filter_type={filter_type})"
        )

        # 向量化查询
        query_vector = self.embedding_model.encode([query])[0]

        # 构建过滤条件
        filter_expr = None
        if filter_type:
            filter_expr = f'metadata like "%\\"type\\": \\"{filter_type}\\"%"'

        # 检索
        try:
            # 重建期间 drop→rename 之间没有正式 collection：
            # 检索在锁内等待替换完成，而不是静默返回空结果
            with self._index_lock:
                results = self.milvus_client.search(
                    collection_name=self.collection_name,
                    data=[query_vector.tolist()],
                    limit=top_k,
                    filter=filter_expr,
                    output_fields=["content", "metadata"]
                )
        except Exception as e:
            logger.error(f"Search failed: {e}")
            raise ValueError(f"知识库检索失败：{e}") from e

        # 格式化结果
        documents = []
        parse_failures = 0
        for hits in results:
            for hit in hits:
                try:
                    # COSINE 指标下 distance 已经是余弦相似度（越大越相似），
                    # 直接作为 score，不能再做 1 - distance。
                    documents.append(hit_to_document(hit))
                except Exception as e:
                    parse_failures += 1
                    logger.warning(f"Failed to parse result: {e}")
                    continue

        if parse_failures and not documents:
            raise ValueError(
                f"知识库检索结果解析失败（{parse_failures} 条）"
            )

        logger.debug(f"Found {len(documents)} documents")
        return documents

    def delete_collection(self):
        """删除 collection（用于测试）"""
        if self.milvus_client.has_collection(self.collection_name):
            self.milvus_client.drop_collection(self.collection_name)
            logger.info(f"Deleted collection: {self.collection_name}")

    def count_documents(self) -> int:
        """统计向量块数量。"""
        load_error = getattr(self.milvus_client, "load_error", "")
        if load_error:
            raise RuntimeError(
                f"本地向量索引加载失败，请重建知识库: {load_error}"
            )
        try:
            # MilvusClient.describe_collection 不返回向量行数，
            # 行数由 get_collection_stats 提供
            stats = self.milvus_client.get_collection_stats(
                self.collection_name
            )
            if isinstance(stats, dict) and "row_count" in stats:
                return int(stats.get("row_count") or 0)
        except (AttributeError, TypeError):
            pass
        except Exception as e:
            logger.warning(f"Failed to read collection stats: {e}")
        try:
            stats = self.milvus_client.describe_collection(self.collection_name)
            # Note: Milvus Lite may not return accurate count, this is a best-effort
            return int(stats.get("num_entities", 0) or 0)
        except Exception as e:
            logger.warning(f"Failed to count documents: {e}")
            return 0
