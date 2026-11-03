"""会话持久化存储（SQLite）。

借鉴 MiQi 的会话管理思路：服务重启后仍能列出历史会诊、恢复多轮上下文。
只用 Python 标准库 sqlite3，不引入额外依赖；数据库默认位于 data/sessions.db。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

#: 会话标题取第一轮主诉的前 40 个字符
MAX_TITLE_LENGTH = 40
#: 历史列表里展示的答复预览长度
PREVIEW_LENGTH = 80


class SessionStore:
    """基于 SQLite 的会话与消息存储。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or os.getenv("MEDIX_SESSION_DB") or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id         TEXT PRIMARY KEY,
                    title      TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role       TEXT NOT NULL,
                    content    TEXT NOT NULL,
                    metadata   TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_messages_session
                    ON messages(session_id, id);
                CREATE INDEX IF NOT EXISTS idx_sessions_updated
                    ON sessions(updated_at DESC);
                """
            )
            # 旧版本数据库没有 metadata 列，就地迁移
            columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(messages)")
            }
            if "metadata" not in columns:
                self._conn.execute(
                    "ALTER TABLE messages ADD COLUMN metadata TEXT NOT NULL DEFAULT '{}'"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _make_title(question: str) -> str:
        title = " ".join(question.split())
        return title[:MAX_TITLE_LENGTH] or "未命名会诊"

    def ensure_session(self, session_id: str, title: str = "") -> None:
        """确保会话存在；已有会话不会被覆盖。"""
        now = self._now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, title, now, now),
            )

    def save_exchange(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> None:
        """保存一轮问答。标题取第一轮主诉。

        Args:
            metadata: 附加到 assistant 消息上的元数据（来源、科室意见等）
        """
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not session_id or not question:
            return

        now = self._now()
        title = self._make_title(question)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False)

        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions(id, title, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (session_id, title, now, now),
            )
            # 第一轮补上标题；后续轮次只更新时间
            self._conn.execute(
                """
                UPDATE sessions
                   SET updated_at = ?,
                       title = CASE WHEN title = '' THEN ? ELSE title END
                 WHERE id = ?
                """,
                (now, title, session_id),
            )
            self._conn.execute(
                """
                INSERT INTO messages(session_id, role, content, created_at)
                VALUES (?, 'user', ?, ?)
                """,
                (session_id, question, now),
            )
            if answer:
                self._conn.execute(
                    """
                    INSERT INTO messages(session_id, role, content, metadata, created_at)
                    VALUES (?, 'assistant', ?, ?, ?)
                    """,
                    (session_id, answer, metadata_json, now),
                )

    def list_sessions(self, limit: int = 50, query: str = "") -> List[Dict[str, Any]]:
        """按最近更新时间倒序返回会话，支持标题/编号模糊搜索。"""
        limit = max(1, min(int(limit or 50), 200))
        sql = """
            SELECT s.id, s.title, s.created_at, s.updated_at,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'assistant'
                     ORDER BY m.id DESC LIMIT 1) AS last_answer
              FROM sessions s
        """
        params: List[Any] = []
        if query:
            sql += " WHERE s.title LIKE ? OR s.id LIKE ?"
            like = f"%{query.strip()}%"
            params.extend([like, like])
        sql += " ORDER BY s.updated_at DESC LIMIT ?"
        params.append(limit)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        sessions = []
        for row in rows:
            item = dict(row)
            preview = (item.pop("last_answer", "") or "").replace("\n", " ")
            item["preview"] = preview[:PREVIEW_LENGTH]
            sessions.append(item)
        return sessions

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """返回最近 limit 条消息，按时间正序；附带解析后的 metadata。"""
        limit = max(1, min(int(limit or 100), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT role, content, metadata, created_at
                  FROM messages
                 WHERE session_id = ?
                 ORDER BY id DESC
                 LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()

        messages = []
        for row in reversed(rows):
            item = dict(row)
            try:
                item["metadata"] = json.loads(item.get("metadata") or "{}")
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            messages.append(item)
        return messages

    def usage_by_session(
        self, session_ids: List[str]
    ) -> Dict[str, Dict[str, Any]]:
        """按会话汇总 assistant 消息里记录的历史用量。

        Returns:
            {session_id: {"usage": {...}, "llm_calls": int, "turns": int}}
            只有存在 assistant 消息的会话才会出现在结果中。
        """
        ids = [str(session_id) for session_id in session_ids if session_id]
        if not ids:
            return {}

        placeholders = ",".join("?" for _ in ids)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT session_id, metadata
                  FROM messages
                 WHERE role = 'assistant' AND session_id IN ({placeholders})
                """,
                ids,
            ).fetchall()

        stats: Dict[str, Dict[str, Any]] = {}
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            entry = stats.setdefault(
                row["session_id"],
                {
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0,
                    },
                    "llm_calls": 0,
                    "turns": 0,
                },
            )
            entry["turns"] += 1

            usage = metadata.get("usage")
            if isinstance(usage, dict):
                for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                    try:
                        entry["usage"][key] += int(usage.get(key) or 0)
                    except (TypeError, ValueError):
                        continue

            try:
                entry["llm_calls"] += int(metadata.get("llm_calls") or 0)
            except (TypeError, ValueError):
                continue

        return stats

    def rename_session(self, session_id: str, title: str) -> bool:
        title = (title or "").strip()[:MAX_TITLE_LENGTH]
        if not title:
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE sessions SET title = ?, updated_at = ? WHERE id = ?",
                (title, self._now(), session_id),
            )
            return cursor.rowcount > 0

    def delete_session(self, session_id: str) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            return cursor.rowcount > 0

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row else 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_store: Optional[SessionStore] = None
_store_lock = threading.Lock()


def get_session_store() -> SessionStore:
    """进程级共享的会话存储。"""
    global _store
    if _store is None:
        with _store_lock:
            if _store is None:
                _store = SessionStore()
                logger.info(f"Session store ready: {_store.db_path}")
    return _store


def reset_session_store() -> None:
    """关闭并清空共享实例（测试或重载配置时使用）。"""
    global _store
    with _store_lock:
        if _store is not None:
            _store.close()
        _store = None
