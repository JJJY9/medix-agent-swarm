"""会话持久化存储（SQLite）。

借鉴 MiQi 的会话管理思路：服务重启后仍能列出历史会诊、恢复多轮上下文。
只用 Python 标准库 sqlite3，不引入额外依赖；数据库默认位于 data/sessions.db。
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

#: 会话标题取第一轮主诉的前 40 个字符
MAX_TITLE_LENGTH = 40
#: 历史列表里展示的答复预览长度
PREVIEW_LENGTH = 80


def _new_usage_entry() -> Dict[str, Any]:
    return {
        "usage": {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
        },
        "llm_calls": 0,
        "turns": 0,
    }


def _accumulate_usage(entry: Dict[str, Any], metadata: Any) -> None:
    """把一条 assistant 消息的元数据累加进用量统计。"""
    entry["turns"] += 1
    if not isinstance(metadata, dict):
        return

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
        pass


def _match_snippet(content: str, query: str, radius: int = 60) -> str:
    """在消息正文中截取搜索词附近的片段。"""
    text = " ".join((content or "").split())
    needle = (query or "").strip()
    if not text or not needle:
        return text[:PREVIEW_LENGTH]

    index = text.lower().find(needle.lower())
    if index < 0:
        return text[:PREVIEW_LENGTH]

    start = max(0, index - radius)
    end = min(len(text), index + len(needle) + radius)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


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
                    updated_at TEXT NOT NULL,
                    favorite   INTEGER NOT NULL DEFAULT 0
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
            # 旧版本 sessions 表没有 favorite 列
            session_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(sessions)")
            }
            if "favorite" not in session_columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
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

    def list_sessions(
        self,
        limit: int = 50,
        query: str = "",
        offset: int = 0,
        favorites_only: bool = False
    ) -> List[Dict[str, Any]]:
        """按最近更新时间倒序返回会话，支持标题/编号模糊搜索与分页。"""
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        sql = """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.favorite,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'assistant'
                     ORDER BY m.id DESC LIMIT 1) AS last_answer
              FROM sessions s
        """
        conditions: List[str] = []
        params: List[Any] = []
        if query:
            conditions.append("""
                (s.title LIKE ? OR s.id LIKE ?
                 OR EXISTS (
                     SELECT 1 FROM messages mm
                      WHERE mm.session_id = s.id AND mm.content LIKE ?
                 ))
            """)
            like = f"%{query.strip()}%"
            params.extend([like, like, like])
        if favorites_only:
            conditions.append("s.favorite = 1")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY s.updated_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

            snippets: Dict[str, str] = {}
            if query and rows:
                ids = [row["id"] for row in rows]
                placeholders = ",".join("?" for _ in ids)
                like = f"%{query.strip()}%"
                match_rows = self._conn.execute(
                    f"""
                    SELECT session_id, content
                      FROM messages
                     WHERE session_id IN ({placeholders}) AND content LIKE ?
                     ORDER BY id
                    """,
                    [*ids, like],
                ).fetchall()
                for match in match_rows:
                    session_id = match["session_id"]
                    if session_id not in snippets:
                        snippets[session_id] = _match_snippet(
                            match["content"], query
                        )

        sessions = []
        for row in rows:
            item = dict(row)
            item["favorite"] = bool(item.get("favorite"))
            preview = (item.pop("last_answer", "") or "").replace("\n", " ")
            item["preview"] = preview[:PREVIEW_LENGTH]
            if query:
                item["match_snippet"] = snippets.get(item["id"], "")
            sessions.append(item)
        return sessions

    def count_sessions(self, query: str = "", favorites_only: bool = False) -> int:
        """返回匹配搜索条件的会话总数，供分页展示。"""
        sql = "SELECT COUNT(*) AS n FROM sessions s"
        conditions: List[str] = []
        params: List[Any] = []
        if query:
            conditions.append("""
                (s.title LIKE ? OR s.id LIKE ?
                 OR EXISTS (
                     SELECT 1 FROM messages mm
                      WHERE mm.session_id = s.id AND mm.content LIKE ?
                 ))
            """)
            like = f"%{query.strip()}%"
            params.extend([like, like, like])
        if favorites_only:
            conditions.append("s.favorite = 1")
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

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

            entry = stats.setdefault(row["session_id"], _new_usage_entry())
            _accumulate_usage(entry, metadata)

        return stats

    def usage_totals(
        self, query: str = "", favorites_only: bool = False
    ) -> Dict[str, Any]:
        """汇总所有匹配会话的历史用量（不受分页限制）。

        Returns:
            {"usage": {...}, "llm_calls": int, "turns": int}
        """
        sql = """
            SELECT m.metadata
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant'
        """
        conditions: List[str] = []
        params: List[Any] = []
        if query:
            conditions.append("""
                (s.title LIKE ? OR s.id LIKE ?
                 OR EXISTS (
                     SELECT 1 FROM messages mm
                      WHERE mm.session_id = s.id AND mm.content LIKE ?
                 ))
            """)
            like = f"%{query.strip()}%"
            params.extend([like, like, like])
        if favorites_only:
            conditions.append("s.favorite = 1")
        if conditions:
            sql += " AND " + " AND ".join(conditions)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        totals = _new_usage_entry()
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            _accumulate_usage(totals, metadata)

        return totals

    def daily_usage(
        self, days: int = 7, favorites_only: bool = False
    ) -> Dict[str, Any]:
        """按自然日汇总用量，返回最近 days 天（含没有记录的日期）。"""
        days = max(1, min(int(days or 7), 30))
        today = datetime.now().date()
        start = today - timedelta(days=days - 1)
        start_text = start.isoformat()

        sql = """
            SELECT m.metadata, m.created_at
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant' AND m.created_at >= ?
        """
        if favorites_only:
            sql += " AND s.favorite = 1"

        with self._lock:
            rows = self._conn.execute(sql, (start_text,)).fetchall()

        buckets: Dict[str, Dict[str, Any]] = {}
        for offset in range(days):
            day = (start + timedelta(days=offset)).isoformat()
            buckets[day] = {"date": day, **_new_usage_entry()}

        for row in rows:
            day = str(row["created_at"])[:10]
            bucket = buckets.get(day)
            if bucket is None:
                continue
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            _accumulate_usage(bucket, metadata)

        totals = _new_usage_entry()
        for bucket in buckets.values():
            totals["turns"] += bucket["turns"]
            totals["llm_calls"] += bucket["llm_calls"]
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                totals["usage"][key] += bucket["usage"][key]

        return {
            "days": list(buckets.values()),
            "totals": totals,
        }

    def export_data(self, limit: int = 200) -> Dict[str, Any]:
        """导出会话与消息，供本地备份或迁移。"""
        sessions = self.list_sessions(limit=limit)
        exported = []
        for item in sessions:
            exported.append({
                **item,
                "messages": self.get_messages(item["id"], limit=500),
            })

        return {
            "version": 1,
            "exported_at": self._now(),
            "session_count": len(exported),
            "sessions": exported,
        }

    def import_data(self, data: Any, max_sessions: int = 200) -> Dict[str, int]:
        """导入备份数据；已存在的会话编号直接跳过，不覆盖现有记录。"""
        if not isinstance(data, dict):
            raise ValueError("备份文件格式不正确。")

        raw_sessions = data.get("sessions")
        if not isinstance(raw_sessions, list) or not raw_sessions:
            raise ValueError("备份文件中没有会话。")
        if len(raw_sessions) > max_sessions:
            raise ValueError(f"一次最多导入 {max_sessions} 个会话。")

        imported = 0
        skipped = 0
        invalid = 0
        messages_imported = 0
        now = self._now()

        with self._lock, self._conn:
            for raw in raw_sessions:
                if not isinstance(raw, dict):
                    invalid += 1
                    continue

                session_id = str(raw.get("id") or "").strip()[:120]
                if not session_id:
                    invalid += 1
                    continue

                exists = self._conn.execute(
                    "SELECT 1 FROM sessions WHERE id = ?", (session_id,)
                ).fetchone()
                if exists:
                    skipped += 1
                    continue

                raw_messages = raw.get("messages")
                if not isinstance(raw_messages, list):
                    invalid += 1
                    continue

                prepared = []
                for message in raw_messages[:1000]:
                    if not isinstance(message, dict):
                        continue
                    role = message.get("role")
                    content = message.get("content")
                    if role not in ("user", "assistant"):
                        continue
                    if not isinstance(content, str) or not content.strip():
                        continue

                    metadata = message.get("metadata")
                    if not isinstance(metadata, dict):
                        metadata = {}
                    created_at = str(message.get("created_at") or now)[:40]
                    prepared.append((
                        role,
                        content,
                        json.dumps(metadata, ensure_ascii=False),
                        created_at,
                    ))

                if not prepared:
                    invalid += 1
                    continue

                title = (
                    str(raw.get("title") or "").strip()[:MAX_TITLE_LENGTH]
                    or self._make_title(prepared[0][1])
                )
                created_at = str(raw.get("created_at") or now)[:40]
                updated_at = str(raw.get("updated_at") or created_at)[:40]
                favorite = 1 if raw.get("favorite") in (1, True, "1") else 0

                self._conn.execute(
                    """
                    INSERT INTO sessions(id, title, created_at, updated_at, favorite)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (session_id, title, created_at, updated_at, favorite),
                )
                self._conn.executemany(
                    """
                    INSERT INTO messages(session_id, role, content, metadata, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (session_id, role, content, metadata_json, created_at_text)
                        for role, content, metadata_json, created_at_text in prepared
                    ],
                )
                imported += 1
                messages_imported += len(prepared)

        return {
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "messages": messages_imported,
        }

    def set_favorite(self, session_id: str, favorite: bool) -> bool:
        """收藏或取消收藏会话；会话不存在返回 False。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE sessions SET favorite = ? WHERE id = ?",
                (1 if favorite else 0, session_id),
            )
            return cursor.rowcount > 0

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
