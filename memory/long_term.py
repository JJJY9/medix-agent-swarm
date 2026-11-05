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
from typing import Dict, List, Any, Optional
from datetime import datetime
from loguru import logger
import math
import os
import sys

from core.session_context import session_memory_scope

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False

# 加载上层目录的config.py
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
try:
    from config import MEM0_CONFIG
except ImportError:
    MEM0_CONFIG = None

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
        if not MEM0_AVAILABLE:
            self.enabled = False
            logger.warning("Mem0 not available. Long-term memory disabled.")
            return

        self.enabled = True

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

            if not mem0_api_key:
                raise ValueError("MEM0_API_KEY not found. Set it in ../config.py or .env")

            # 初始化 Mem0 云服务客户端
            self.mem0 = MemoryClient(api_key=mem0_api_key)
            logger.info("LongTermMemory initialized with Mem0 cloud service")

        except Exception as e:
            logger.warning(f"Failed to initialize Mem0: {e}")
            logger.warning("Long-term memory disabled. System will work without Mem0.")
            logger.info("To enable Mem0: set MEM0_API_KEY in ../config.py or .env")
            self.enabled = False

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
        if not self.enabled:
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
                user_id=str(user_id or "medix_local")[:120],
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
        if not self.enabled:
            return []

        try:
            result_limit = max(1, min(int(limit or 5), 20))
        except (TypeError, ValueError, OverflowError):
            result_limit = 5

        try:
            results = self.mem0.search(
                query=query,
                user_id=str(user_id or "medix_local")[:120],
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



