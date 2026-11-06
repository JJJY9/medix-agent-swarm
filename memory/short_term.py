"""
短期记忆：会话级对话历史管理

功能：
- 管理会话级的对话历史（messages）
- 支持两种存储后端：内存（默认）和 Redis（可选）
- 自动过期机制（Redis 1小时）
- 熵管理：自动去重和压缩（Harness Engineering）
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Any
import json
import threading
from loguru import logger

# Harness Engineering: 熵管理
try:
    from .entropy_manager import MemoryEntropyManager
    ENTROPY_ENABLED = True
except ImportError:
    logger.warning("EntropyManager not found, running without entropy management")
    ENTROPY_ENABLED = False


#: 单会话消息上限与进程内会话上限，避免长期运行 OOM
MAX_MESSAGES_PER_SESSION = 1000
MAX_IN_MEMORY_SESSIONS = 200


@dataclass
class ConversationHistory:
    """对话历史数据类"""
    session_id: str
    messages: List[Dict[str, str]] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.now)
    last_updated: datetime = field(default_factory=datetime.now)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def add_message(self, role: str, content: str):
        """添加消息"""
        self.messages.append({
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat()
        })
        if len(self.messages) > MAX_MESSAGES_PER_SESSION:
            del self.messages[:-MAX_MESSAGES_PER_SESSION]
        self.last_updated = datetime.now()

    def get_recent_messages(self, limit: int = 50) -> List[Dict[str, str]]:
        """获取最近的消息"""
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        # 负数/零切片（messages[-0:]）会返回整段历史
        if limit <= 0:
            return []
        return self.messages[-limit:]

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典（用于 Redis 存储）"""
        return {
            "session_id": self.session_id,
            "messages": self.messages,
            "created_at": self.created_at.isoformat(),
            "last_updated": self.last_updated.isoformat(),
            "metadata": self.metadata
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ConversationHistory":
        """从字典创建（从 Redis 加载）"""
        return cls(
            session_id=data["session_id"],
            messages=data["messages"],
            created_at=datetime.fromisoformat(data["created_at"]),
            last_updated=datetime.fromisoformat(data["last_updated"]),
            metadata=data.get("metadata", {})
        )


class ShortTermMemory:
    """
    短期记忆管理器（单例模式）

    支持两种存储后端：
    1. memory：纯内存存储（默认，快速但不持久）
    2. redis：Redis 存储（可选，持久但需要 Redis 服务）

    使用场景：
    - 管理单次会话的对话历史
    - Agent Loop 中的消息记录
    - 会话结束后转换为长期记忆
    """

    _instance = None  # 单例实例
    _lock = None  # 用于线程安全（如果需要）

    def __new__(cls, *args, **kwargs):
        """单例模式：确保只有一个 ShortTermMemory 实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(
        self,
        storage_type: str = "memory",
        redis_config: Optional[Dict[str, Any]] = None
    ):
        """
        初始化短期记忆管理器

        Args:
            storage_type: 存储类型，"memory" 或 "redis"
            redis_config: Redis 配置（storage_type="redis" 时需要）
        """
        # 防止重复初始化
        if not hasattr(self, "_local_fallback_at"):
            self._local_fallback_at: Dict[str, datetime] = {}
        if not hasattr(self, "_cleared_sessions"):
            self._cleared_sessions: set[str] = set()
        if not hasattr(self, "_sealed_sessions"):
            # 通过 API 删除的会话：迟到的消息必须直接丢弃，
            # 普通 clear_session（测试/重置）不进入此集合
            self._sealed_sessions: set[str] = set()
        if not hasattr(self, "_state_lock"):
            # 串行化 load→modify→save 与清理/墓碑变更，避免并发下把已删会话写回
            self._state_lock = threading.RLock()
        if hasattr(self, '_initialized'):
            if getattr(self, 'storage_type', None) not in (None, storage_type):
                logger.warning(
                    "ShortTermMemory 已按 storage_type=%s 初始化，"
                    "忽略本次 storage_type=%s",
                    getattr(self, 'storage_type', None),
                    storage_type,
                )
            return

        self.storage_type = storage_type
        self.sessions: Dict[str, ConversationHistory] = {}
        self.redis_client = None
        # Redis 写失败时的本地兜底时间戳，以及已删除会话的墓碑
        self._local_fallback_at: Dict[str, datetime] = {}
        self._cleared_sessions: set[str] = set()
        self._sealed_sessions: set[str] = set()
        self._initialized = True

        # Harness Engineering: 熵管理器
        self.entropy_manager = MemoryEntropyManager() if ENTROPY_ENABLED else None
        if ENTROPY_ENABLED:
            logger.debug("✅ Entropy management enabled for short-term memory")

        if storage_type == "redis":
            try:
                import redis
                config = redis_config or {}
                self.redis_client = redis.Redis(
                    host=config.get("host", "localhost"),
                    port=config.get("port", 6379),
                    db=config.get("db", 0),
                    password=config.get("password"),
                    decode_responses=True
                )
                # 测试连接
                self.redis_client.ping()
                logger.info("ShortTermMemory initialized with Redis")
            except Exception as e:
                logger.error(f"Failed to connect to Redis: {e}. Falling back to memory storage.")
                self.storage_type = "memory"
                self.redis_client = None
        else:
            logger.info("ShortTermMemory initialized with in-memory storage")

    def _evict_local_sessions(self) -> None:
        """限制进程内会话数量，优先淘汰最久未更新的会话。"""
        overflow = len(self.sessions) - MAX_IN_MEMORY_SESSIONS
        if overflow <= 0:
            return
        oldest = sorted(
            self.sessions.items(),
            key=lambda item: item[1].last_updated,
        )[:overflow]
        for session_id, _ in oldest:
            self.sessions.pop(session_id, None)

    def create_session(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ConversationHistory:
        """
        创建新会话

        Args:
            session_id: 会话ID
            metadata: 会话元数据

        Returns:
            ConversationHistory 对象
        """
        with self._state_lock:
            return self._create_session_locked(session_id, metadata)

    def _create_session_locked(
        self,
        session_id: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> ConversationHistory:
        history = ConversationHistory(
            session_id=session_id,
            metadata=metadata or {}
        )
        self._cleared_sessions.discard(session_id)
        self._sealed_sessions.discard(session_id)

        if self.storage_type == "memory":
            self.sessions[session_id] = history
            self._evict_local_sessions()
        elif self.storage_type == "redis" and self.redis_client:
            if not self._save_to_redis(history):
                # Redis 暂时不可用时保留进程内副本，避免本轮直接丢失
                self.sessions[session_id] = history
                self._local_fallback_at[session_id] = datetime.now()
                self._evict_local_sessions()
            else:
                self.sessions.pop(session_id, None)
                self._local_fallback_at.pop(session_id, None)

        logger.debug(f"Created session: {session_id}")
        return history

    def add_message(
        self,
        session_id: str,
        role: str,
        content: str
    ):
        """
        添加消息到会话历史

        Args:
            session_id: 会话ID
            role: 消息角色（user/assistant/tool）
            content: 消息内容
        """
        with self._state_lock:
            self._add_message_locked(session_id, role, content)

    def _add_message_locked(
        self,
        session_id: str,
        role: str,
        content: str
    ):
        local_history = self.sessions.get(session_id)
        if session_id in self._sealed_sessions and local_history is None:
            # 会话已通过 API 删除：丢弃进行中会诊迟到的消息，
            # 不能重新创建本地历史让已删内容通过短期记忆回退复活
            logger.info("Session deleted, dropping late short-term message")
            return
        if self.storage_type == "redis" and self.redis_client:
            if session_id in self._cleared_sessions:
                # 删除墓碑存在时，Redis 中可能仍有删除失败留下的旧会话；
                # 新消息应基于清理后的本地副本开启全新历史，不能合并旧内容。
                # 墓碑要等 Redis 写回成功后再解除，避免写失败时旧会话重新可见
                history = local_history or ConversationHistory(
                    session_id=session_id
                )
            else:
                try:
                    # 「不存在」返回 None；读取失败则抛错——不能用空历史
                    # 覆盖 Redis 中原有的整段会话
                    history = self._load_from_redis(session_id, strict=True)
                except RuntimeError as e:
                    logger.error(
                        f"读取 Redis 会话失败，使用本地副本继续: {e}"
                    )
                    history = local_history
                    if history is None:
                        history = ConversationHistory(session_id=session_id)
                        self.sessions[session_id] = history
                    # 新消息意味着会话重新启用，解除之前的删除墓碑
                    self._cleared_sessions.discard(session_id)
                    history.add_message(role, content)
                    self._local_fallback_at[session_id] = datetime.now()
                    self._evict_local_sessions()
                    logger.debug(f"Added {role} message to session {session_id} (local fallback)")
                    return

                # Redis 中的版本可能比本地兜底更旧（上次写入失败），不能覆盖本地新消息
                if history is None or (
                    local_history is not None
                    and len(local_history.messages) > len(history.messages)
                ):
                    history = local_history or history
        else:
            history = self.get_session(session_id)

        if history is None:
            history = self.create_session(session_id)

        history.add_message(role, content)

        # 保存到存储
        if self.storage_type == "redis" and self.redis_client:
            if self._save_to_redis(history):
                self.sessions.pop(session_id, None)
                self._local_fallback_at.pop(session_id, None)
                # 成功写回后会话重新启用，解除墓碑
                self._cleared_sessions.discard(session_id)
            else:
                # 写 Redis 失败时退回本地副本，至少保证当前进程还能续聊
                self.sessions[session_id] = history
                self._local_fallback_at[session_id] = datetime.now()
                self._evict_local_sessions()
        elif self.storage_type == "memory":
            self.sessions[session_id] = history
            self._evict_local_sessions()

        logger.debug(f"Added {role} message to session {session_id}")

    def get_session(self, session_id: str) -> Optional[ConversationHistory]:
        """
        获取会话历史

        Args:
            session_id: 会话ID

        Returns:
            ConversationHistory 对象，如果不存在返回 None
        """
        with self._state_lock:
            return self._get_session_locked(session_id)

    def _get_session_locked(
        self, session_id: str
    ) -> Optional[ConversationHistory]:
        if session_id in self._cleared_sessions:
            # 墓碑期间忽略 Redis 中的旧值，但清理后新写入的本地副本仍可读
            local = self.sessions.get(session_id)
            fallback_at = self._local_fallback_at.get(session_id)
            if local is None or fallback_at is None:
                return None
            if (datetime.now() - fallback_at).total_seconds() > 3600:
                self.sessions.pop(session_id, None)
                self._local_fallback_at.pop(session_id, None)
                return None
            return local
        if self.storage_type == "memory":
            return self.sessions.get(session_id)
        elif self.storage_type == "redis" and self.redis_client:
            history = self._load_from_redis(session_id)
            # Redis 写入失败后本地兜底副本可能比 Redis 更新；
            # 只要兜底未过期且更新更晚，就优先返回本地，避免丢上一轮对话
            local = self.sessions.get(session_id)
            fallback_at = self._local_fallback_at.get(session_id)
            local_fresh = (
                local is not None
                and fallback_at is not None
                and (datetime.now() - fallback_at).total_seconds() <= 3600
            )
            if local_fresh and (
                history is None
                or len(local.messages) > len(history.messages)
                or local.last_updated > history.last_updated
            ):
                return local
            if history is not None:
                return history
            if not local_fresh:
                self.sessions.pop(session_id, None)
                self._local_fallback_at.pop(session_id, None)
                return None
            return local
        return None

    def get_recent_messages(
        self,
        session_id: str,
        limit: int = 50
    ) -> List[Dict[str, str]]:
        """
        获取最近的消息（自动熵管理）

        Args:
            session_id: 会话ID
            limit: 最大消息数

        Returns:
            消息列表（去重和压缩后）
        """
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 50
        if limit <= 0:
            return []

        history = self.get_session(session_id)
        if history:
            # 有熵管理器时多取一段更早的消息，交给压缩逻辑生成历史摘要；
            # 只取 limit 条会让压缩永远达不到触发条件。
            window = limit * 3 if self.entropy_manager else limit
            messages = history.get_recent_messages(window)

            # Harness Engineering: 统一熵管理
            if self.entropy_manager and len(messages) > 0:
                # 估算熵（用于监控系统健康）
                if len(messages) >= 10:
                    entropy_info = self.entropy_manager.estimate_entropy(messages)
                    if entropy_info["entropy_level"] == "high":
                        logger.warning(
                            f"📊 会话 {session_id} 熵等级: {entropy_info['entropy_level']} "
                            f"(消息数: {entropy_info['total_messages']}, "
                            f"重复率: {entropy_info['duplicate_rate']:.1%})"
                        )

                # 统一使用 auto_clean: 自动去重+压缩
                messages = self.entropy_manager.auto_clean(
                    messages,
                    enable_deduplication=True,
                    enable_compression=True,
                    max_messages=limit
                )
                if len(messages) > limit:
                    messages = messages[-limit:]

            return messages
        return []

    def get_history(
        self,
        session_id: str,
        limit: int = 10
    ) -> List[Dict[str, str]]:
        """
        获取历史对话（OpenAI 格式，用于 Agent Loop）

        Args:
            session_id: 会话ID
            limit: 最大轮数（一轮 = user + assistant）

        Returns:
            消息列表（OpenAI 格式: [{"role": "user", "content": "..."}, ...]）
        """
        # get_recent_messages 已处理熵管理，这里只做格式转换
        messages = self.get_recent_messages(session_id, limit * 2)  # 每轮2条消息

        # 转换为 OpenAI 格式（保留 user/assistant，以及压缩产生的 system 摘要）
        openai_messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in messages
            if msg.get("role") in ["user", "assistant", "system"]
            and msg.get("content")
        ]

        # 摘要之后的第一条历史应从用户消息开始；如果窗口里根本没有
        # 用户消息（尾部是连续工具/助手消息），保留这些回答而不是清空
        if openai_messages and openai_messages[0]["role"] == "system":
            prefix = openai_messages[:1]
            start = 1
        else:
            prefix = []
            start = 0
        first_user = next(
            (
                index for index in range(start, len(openai_messages))
                if openai_messages[index]["role"] == "user"
            ),
            None,
        )
        if first_user is not None:
            start = first_user
        openai_messages = prefix + openai_messages[start:]

        return openai_messages

    def seal_session(self, session_id: str) -> None:
        """标记会话已被 API 删除：迟到的短期记忆写入直接丢弃。"""
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._state_lock:
            self._sealed_sessions.add(session_id)
            while len(self._sealed_sessions) > 1000:
                self._sealed_sessions.pop()

    def clear_tombstone(self, session_id: str) -> None:
        """显式恢复/重新启用会话时解除短期记忆墓碑。"""
        with self._state_lock:
            session_id = str(session_id or "").strip()
            self._cleared_sessions.discard(session_id)
            self._sealed_sessions.discard(session_id)

    def clear_session(self, session_id: str) -> bool:
        """
        清空会话

        Returns:
            Redis 删除是否成功；本地存储始终视为成功。
        """
        with self._state_lock:
            return self._clear_session_locked(session_id)

    def _clear_session_locked(self, session_id: str) -> bool:
        self._cleared_sessions.add(session_id)
        if len(self._cleared_sessions) > 1000:
            # 墓碑只用于避免本次进程内的会话复活，限制集合规模
            self._cleared_sessions.clear()
            self._cleared_sessions.add(session_id)
        self.sessions.pop(session_id, None)
        self._local_fallback_at.pop(session_id, None)

        deleted = True
        if self.storage_type == "redis" and self.redis_client:
            try:
                key = f"session:{session_id}"
                self.redis_client.delete(key)
            except Exception as e:
                # Redis 删除失败时保留墓碑，至少当前进程不会再读到该会话
                logger.error(f"Failed to clear Redis session: {e}")
                deleted = False

        logger.debug(f"Cleared session: {session_id}")
        return deleted

    def _save_to_redis(self, history: ConversationHistory) -> bool:
        """保存到 Redis（内部方法）；失败返回 False，调用方可感知。"""
        if not self.redis_client:
            return False

        try:
            key = f"session:{history.session_id}"
            value = json.dumps(history.to_dict())
            # 设置过期时间：1小时（3600秒）
            self.redis_client.setex(key, 3600, value)
            return True
        except Exception as e:
            logger.error(f"Failed to save to Redis: {e}")
            return False

    def _load_from_redis(
        self, session_id: str, strict: bool = False
    ) -> Optional[ConversationHistory]:
        """从 Redis 加载（内部方法）。

        strict=True 时读取/解析失败会抛出 RuntimeError，供写入路径区分
        「键不存在」与「读取失败」。
        """
        if not self.redis_client:
            return None

        try:
            key = f"session:{session_id}"
            value = self.redis_client.get(key)

            if value:
                data = json.loads(value)
                return ConversationHistory.from_dict(data)
        except Exception as e:
            logger.error(f"Failed to load from Redis: {e}")
            if strict:
                raise RuntimeError(str(e)) from e

        return None
