"""
长期记忆：基于 Mem0 的跨会话记忆管理

功能：
- 使用 Mem0 API 存储会话总结
- 向量相似度搜索历史会话
- 支持多用户和多 Agent

Mem0 优势：
- 自动向量化和相似度搜索
- 云端或本地 Qdrant 存储
- 内置去重和摘要
"""
from typing import Dict, List, Any, Optional, Iterator
from datetime import datetime
from loguru import logger
import math
import os
import threading
import time

from core.session_context import session_memory_scope

#: 初始化失败后的重试冷却时间（秒），避免每次读写都重建客户端
REINIT_COOLDOWN_SECONDS = 300.0

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False

# config.py 统一按显式路径加载：仓库根目录优先，其次兼容项目上层目录；
# 不再把父目录插到 sys.path（会被无关 config.py 遮蔽或误执行）
from core.config_loader import load_config_value

MEM0_CONFIG = load_config_value("MEM0_CONFIG")

try:
    from mem0 import MemoryClient
    MEM0_AVAILABLE = True
except ImportError:
    MEM0_AVAILABLE = False
    logger.warning("mem0ai not installed. Long-term memory disabled. Install with: pip install mem0ai")


class LongTermMemory:
    """
    长期记忆管理器（基于 Mem0）

    功能：
    1. 存储会话总结到 Mem0
    2. 搜索相似历史会话（向量相似度）

    使用场景：
    - 会话结束后保存总结
    - 会话开始前检索相似案例
    """

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        """
        初始化长期记忆管理器

        Args:
            config: Mem0 配置字典，包含 api_key 等配置
        """
        #: 重连锁与冷却时间：网络抖动导致的初始化失败可以自动恢复，
        #: 但不能每次请求都重建客户端
        self._init_lock = threading.Lock()
        self._reinit_at = 0.0
        self._api_key = ""
        if not MEM0_AVAILABLE:
            self.enabled = False
            #: 是否配置了 API key；用于区分「未启用云记忆」和「初始化失败」
            self.api_key_configured = False
            self.init_error = ""
            logger.warning("Mem0 not available. Long-term memory disabled.")
            return

        self.enabled = True
        #: 是否配置了 API key；用于区分「未启用云记忆」和「初始化失败」
        self.api_key_configured = False
        #: 最近一次初始化错误；非空表示云记忆已配置但暂时不可用
        self.init_error = ""
        #: 最近一次删除操作的错误；供上层判断是否需要重试
        self.last_error = ""

        # Harness Engineering: 熵管理器
        self.entropy_manager = MemoryEntropyManager() if ENTROPY_ENABLED else None
        if ENTROPY_ENABLED:
            logger.debug("✅ Entropy management enabled for long-term memory")

        try:
            # 获取 API key（优先级：参数 config > 环境变量 > 全局 config.py）
            # config.py 仓库模板里 api_key 是空字符串，不能让空值挡住 .env
            mem0_api_key = (
                (config or {}).get("api_key")
                or os.getenv("MEM0_API_KEY")
                or (MEM0_CONFIG or {}).get("api_key")
            )
            self.api_key_configured = bool(mem0_api_key)
            self._api_key = str(mem0_api_key or "")

            if not mem0_api_key:
                raise ValueError("MEM0_API_KEY not found. Set it in ../config.py or .env")

            # 初始化 Mem0 云服务客户端
            self.mem0 = MemoryClient(api_key=mem0_api_key)
            logger.info("LongTermMemory initialized with Mem0 cloud service")

        except Exception as e:
            self.init_error = f"{type(e).__name__}: {e}"[:200]
            logger.warning(f"Failed to initialize Mem0: {e}")
            logger.warning("Long-term memory disabled. System will work without Mem0.")
            logger.info("To enable Mem0: set MEM0_API_KEY in ../config.py or .env")
            self.enabled = False

    def ensure_available(self, force: bool = False) -> bool:
        """已配置但初始化失败时，按冷却时间重试建立 Mem0 客户端。

        MemoryClient 初始化会同步校验 API key，启动时的网络抖动会把
        长期记忆永久禁用；这里让实例可以自愈，删除清理队列也才能在
        不重启进程的情况下继续排空。
        """
        if getattr(self, "enabled", False):
            return True
        if not getattr(self, "api_key_configured", False):
            return False
        if not MEM0_AVAILABLE:
            return False
        now = time.monotonic()
        with self._init_lock:
            if getattr(self, "enabled", False):
                return True
            if not force and now < self._reinit_at:
                return False
            # 先占位冷却窗口再释放锁：MemoryClient 构造会同步联网校验
            # API key（httpx 超时可达 60s），不能在持锁期间进行，否则
            # 所有记忆读写都会排队等它
            self._reinit_at = now + REINIT_COOLDOWN_SECONDS
        api_key = self._api_key or os.getenv("MEM0_API_KEY") or (
            MEM0_CONFIG or {}
        ).get("api_key")
        if not api_key:
            return False
        try:
            client = MemoryClient(api_key=api_key)
        except Exception as e:
            self.init_error = f"{type(e).__name__}: {e}"[:200]
            logger.warning(f"Retrying Mem0 initialization failed: {e}")
            return False
        with self._init_lock:
            self.mem0 = client
            self.enabled = True
            self.init_error = ""
        logger.info("LongTermMemory re-initialized after transient failure")
        return True

    def add_session_summary(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None,
        user_id: str = "medix_local",
    ) -> Optional[str]:
        """
        添加会话总结到 Mem0

        Args:
            session_id: 会话ID
            question: 用户问题
            answer: 系统回答
            metadata: 额外元数据（复杂度、时间等）

        Returns:
            记忆ID，失败返回 None
        """
        if not self.ensure_available():
            return None
        user_scope = str(user_id or "").strip()
        if not user_scope:
            # 空作用域会静默落到共享 medix_local：调用方一定没能确定
            # 患者/会话命名空间（档案已删除等），不能写入共享记忆
            logger.warning("Empty memory scope; skip Mem0 summary")
            return None

        try:
            # 构建记忆文本（包含问题和答案摘要），限制长度避免超大 payload
            memory_text = (
                f"问题：{str(question or '')[:2000]}\n"
                f"回答：{str(answer or '')[:500]}..."
            )

            # 添加到 Mem0
            result = self.mem0.add(
                messages=[{"role": "user", "content": memory_text}],
                user_id=user_scope[:120],
                metadata={
                    **(metadata or {}),
                    "type": "session_summary",
                    "session_id": session_memory_scope(session_id),
                    "timestamp": datetime.now().isoformat(),
                }
            )

            # 提取记忆ID
            if isinstance(result, dict):
                memory_id = result.get("id")
                if memory_id is None:
                    results = result.get("results")
                    if isinstance(results, list) and results:
                        first = results[0]
                        if isinstance(first, dict):
                            memory_id = first.get("id")
            else:
                memory_id = str(result)

            logger.info(f"Added session summary to Mem0: {memory_id}")
            return memory_id

        except Exception as e:
            logger.error(f"Failed to add session summary to Mem0: {e}")
            return None

    def search_similar_sessions(
        self,
        query: str,
        limit: int = 5,
        user_id: str = "medix_local",
    ) -> List[Dict[str, Any]]:
        """
        搜索相似的历史会话（向量相似度搜索，自动去重）

        Args:
            query: 查询文本（通常是用户问题）
            limit: 返回结果数量

        Returns:
            相似会话列表，每个包含 memory_id、content、score、metadata
        """
        if not self.ensure_available():
            return []
        user_scope = str(user_id or "").strip()
        if not user_scope:
            logger.warning("Empty memory scope; skip Mem0 search")
            return []

        try:
            result_limit = max(1, min(int(limit or 5), 20))
        except (TypeError, ValueError, OverflowError):
            result_limit = 5

        try:
            results = self.mem0.search(
                query=query,
                user_id=user_scope[:120],
                limit=result_limit * 2,
            )

            # 添加调试日志
            logger.debug(f"Mem0 search called with limit={result_limit * 2}")

            # 格式化结果
            formatted_results = []

            # 处理不同格式的 Mem0 返回值
            if isinstance(results, dict):
                results_list = results.get("results", [])
                logger.debug(f"Mem0 returned dict with {len(results_list)} results (requested limit={result_limit * 2})")
            elif isinstance(results, list):
                results_list = results
                logger.debug(f"Mem0 returned list with {len(results_list)} results (requested limit={result_limit * 2})")
            else:
                results_list = []

            for result in results_list:
                if not isinstance(result, dict):
                    continue
                metadata = result.get("metadata")
                if not isinstance(metadata, dict):
                    metadata = {}
                content = result.get("memory", result.get("text", ""))
                if not isinstance(content, str) or not content.strip():
                    continue
                try:
                    score = float(result.get("score", 0.0))
                except (TypeError, ValueError, OverflowError):
                    score = 0.0
                if not math.isfinite(score):
                    score = 0.0
                formatted_results.append({
                    "memory_id": result.get("id", "unknown"),
                    "content": content,
                    "score": score,
                    "metadata": metadata,
                    "timestamp": metadata.get("timestamp"),
                })

            # Harness Engineering: 熵管理 - 去重相似会话
            if self.entropy_manager and len(formatted_results) > 0:
                formatted_results = self.entropy_manager.deduplicate_sessions(formatted_results)
                

            # 限制返回数量
            formatted_results = formatted_results[:result_limit]

            # 只记录检索规模，不把患者问题写进日志
            logger.debug(
                f"Found {len(formatted_results)} similar sessions "
                f"(query length={len(query)})"
            )
            return formatted_results

        except Exception as e:
            logger.error(f"Failed to search similar sessions: {e}")
            return []

    # ===== 删除（隐私 / 数据留存） =====

    @staticmethod
    def _extract_memories(result: Any) -> List[Dict[str, Any]]:
        """兼容 Mem0 不同版本 get_all 的返回结构。"""
        if isinstance(result, dict):
            items = result.get("results", result.get("memories", []))
        elif isinstance(result, list):
            items = result
        else:
            return []
        return [item for item in items if isinstance(item, dict)]

    def _iter_memories(
        self, user_id: str, max_pages: int = 20, page_size: int = 100
    ) -> Iterator[Dict[str, Any]]:
        """分页遍历某个命名空间下的全部记忆。"""
        page = 1
        last_first_id = None
        while page <= max_pages:
            try:
                result = self.mem0.get_all(
                    user_id=user_id, page=page, page_size=page_size
                )
            except TypeError:
                # 旧版客户端不支持 page/page_size，退回一次性读取
                try:
                    result = self.mem0.get_all(
                        filters={"user_id": user_id}
                    )
                except TypeError:
                    result = self.mem0.get_all(user_id=user_id)
                yield from self._extract_memories(result)
                return
            batch = self._extract_memories(result)
            if not batch:
                return
            first_id = batch[0].get("id")
            if (
                page > 1
                and first_id is not None
                and str(first_id) == str(last_first_id)
            ):
                # 服务端忽略 page 参数时每页都返回同一批，继续翻页只会
                # 重复处理；停止并让调用方按已枚举结果处理
                logger.warning(
                    "Mem0 get_all 分页未前进，停止遍历该命名空间"
                )
                return
            last_first_id = first_id
            yield from batch
            if len(batch) < page_size:
                return
            page += 1
        logger.warning(
            f"Mem0 记忆遍历达到最大页数 {max_pages}，可能漏掉部分记忆"
        )

    def _delete_memory(self, memory_id: str) -> bool:
        try:
            self.mem0.delete(memory_id=memory_id)
            return True
        except TypeError:
            self.mem0.delete(memory_id)
            return True

    def _delete_matching(
        self, user_id: str, predicate
    ) -> int:
        """按 metadata 条件逐条删除记忆，返回删除条数。

        先完整枚举并收集 id，再统一删除：边翻页边删除会让服务端分页
        偏移前移，超过一页的命名空间会漏删。predicate 为 None 时删除
        该命名空间下的全部记忆（含 metadata 缺失/非对象的条目）。
        """
        matching_ids: List[str] = []
        self.last_error = ""
        try:
            for item in self._iter_memories(user_id):
                if predicate is not None:
                    metadata = item.get("metadata")
                    if not isinstance(metadata, dict):
                        continue
                    if not predicate(metadata):
                        continue
                memory_id = item.get("id")
                if not memory_id:
                    continue
                matching_ids.append(str(memory_id))
        except Exception as e:
            self.last_error = str(e)
            logger.error(f"Failed to list Mem0 memories for deletion: {e}")

        deleted = 0
        for memory_id in matching_ids:
            try:
                if self._delete_memory(memory_id):
                    deleted += 1
            except Exception as e:
                self.last_error = str(e)
                logger.warning(
                    f"Failed to delete Mem0 memory {memory_id}: {e}"
                )
        return deleted

    def delete_session_summary(
        self,
        session_id: str,
        user_id: str = "medix_local",
        before: str = "",
    ) -> int:
        """删除某个会话写入指定命名空间的记忆。

        档案作用域由多个会话共享，因此只能按 metadata.session_id
        精确删除，不能清空整个 user_id 命名空间。before 非空时只删除
        该时间点之前写入的记忆（目标已重建时保留新数据）。
        """
        if not self.ensure_available():
            return 0
        scope = session_memory_scope(session_id)

        def _match(meta: Dict[str, Any]) -> bool:
            if str(meta.get("session_id") or "") != scope:
                return False
            if before:
                stamp = str(meta.get("timestamp") or "")
                # 缺少时间戳的旧条目按更早处理
                return not stamp or stamp < before
            return True

        deleted = self._delete_matching(
            str(user_id or "medix_local")[:120], _match
        )
        if deleted:
            logger.info(
                f"Deleted {deleted} Mem0 memories for session {scope[:12]}…"
            )
        return deleted

    def delete_scope_before(self, user_id: str, cutoff: str) -> int:
        """只删除命名空间中早于 cutoff 写入的记忆。

        同名档案/同号会话已被重建时，按整段命名空间清空会误删新写入
        的长期记忆；这里按 metadata.timestamp 过滤，保留新数据。
        """
        cutoff = str(cutoff or "").strip()
        if not self.ensure_available() or not cutoff:
            return 0
        self.last_error = ""

        def _older(meta: Dict[str, Any]) -> bool:
            stamp = str(meta.get("timestamp") or "")
            return not stamp or stamp < cutoff

        return self._delete_matching(str(user_id or "medix_local")[:120], _older)

    def delete_scope(self, user_id: str) -> int:
        """删除一个命名空间下的全部记忆（删除患者档案时使用）。"""
        if not self.ensure_available():
            return 0
        self.last_error = ""
        scope = str(user_id or "medix_local")[:120]
        try:
            result = self.mem0.delete_all(user_id=scope)
            if isinstance(result, dict):
                for key in ("deleted", "count", "deleted_count"):
                    value = result.get(key)
                    if isinstance(value, int):
                        return value
            return 0
        except Exception as e:
            logger.warning(
                f"Mem0 delete_all failed, falling back to per-item: {e}"
            )
            return self._delete_matching(scope, None)



