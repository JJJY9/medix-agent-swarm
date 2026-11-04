"""会话持久化存储（SQLite）。

借鉴 MiQi 的会话管理思路：服务重启后仍能列出历史会诊、恢复多轮上下文。
只用 Python 标准库 sqlite3，不引入额外依赖；数据库默认位于 data/sessions.db。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from core.serialization import sanitize_json

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

#: 会诊标题取第一轮主诉的前 40 个字符
MAX_TITLE_LENGTH = 40
#: 历史列表里展示的答复预览长度
PREVIEW_LENGTH = 80
#: 每个会话最多保存的标签数量与单个标签长度
MAX_TAGS = 5
MAX_TAG_LENGTH = 20
#: 会诊反馈备注的最大长度
MAX_FEEDBACK_LENGTH = 500
#: 随访提醒的字段长度与日期格式
MAX_REMINDER_TITLE_LENGTH = 120
MAX_REMINDER_NOTE_LENGTH = 300
REMINDER_DATE_PATTERN = re.compile(
    r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$"
)
#: 常用患者档案的名称与字段长度上限
MAX_PROFILE_NAME_LENGTH = 40
MAX_PROFILE_FIELD_LENGTH = 2000
#: 档案只保存结构化补充信息里的已知字段
PROFILE_CONTEXT_KEYS = (
    "age",
    "sex",
    "duration",
    "history",
    "medications",
    "allergies",
)


def _normalize_profile_context(context: Any) -> Optional[Dict[str, str]]:
    """校验并收敛档案字段；没有有效内容时返回 None。"""
    if not isinstance(context, dict):
        return None

    cleaned: Dict[str, str] = {}
    for key in PROFILE_CONTEXT_KEYS:
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        if isinstance(value, (list, tuple)):
            text = "；".join(str(item).strip() for item in value if str(item).strip())
        elif isinstance(value, dict):
            text = "；".join(
                f"{item_key}={str(item_value).strip()}"
                for item_key, item_value in value.items()
                if str(item_value).strip()
            )
        else:
            text = str(value).strip()
        if text:
            cleaned[key] = text[:MAX_PROFILE_FIELD_LENGTH]

    return cleaned or None


#: 健康指标类型 → (标签, 单位, 取值范围)
METRIC_TYPES = {
    "bp": ("血压", "mmHg", (50, 300)),
    "weight": ("体重", "kg", (10, 400)),
    "glucose": ("血糖", "mmol/L", (1, 50)),
    "temperature": ("体温", "°C", (30, 45)),
    "heart_rate": ("心率", "次/分", (30, 250)),
    "spo2": ("血氧饱和度", "%", (50, 100)),
    "waist": ("腰围", "cm", (30, 200)),
    "phq9": ("PHQ-9 评分", "分", (0, 27)),
    "gad7": ("GAD-7 评分", "分", (0, 21)),
    "cat": ("CAT 评分", "分", (0, 40)),
    "ess": ("ESS 评分", "分", (0, 24)),
    "auditc": ("AUDIT-C 评分", "分", (0, 12)),
    "mmrc": ("mMRC 分级", "级", (0, 4)),
}
MAX_METRIC_NOTE_LENGTH = 300

#: 目标为下限的指标（数值越高越好，低于目标视为未达标）。
LOWER_BOUND_TARGET_METRICS = {"spo2"}


def _normalize_metric(
    profile: str,
    metric: str,
    value1: Any,
    value2: Any,
    measured_at: str,
    note: str,
) -> Optional[Dict[str, Any]]:
    """校验一条健康指标记录；无效时返回 None。"""
    metric = str(metric or "").strip()
    if metric not in METRIC_TYPES:
        return None

    _, unit, (low, high) = METRIC_TYPES[metric]
    try:
        first = float(value1)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (low <= first <= high):
        return None

    second: Optional[float] = None
    if metric == "bp":
        try:
            second = float(value2)
        except (TypeError, ValueError, OverflowError):
            return None
        if not (30 <= second <= 200) or first <= second:
            return None

    measured_at = str(measured_at or "").strip()[:40]
    if not measured_at:
        measured_at = datetime.now().isoformat(timespec="minutes")
    else:
        try:
            parsed = datetime.fromisoformat(
                measured_at.replace("Z", "+00:00")
            )
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            measured_at = (
                parsed.astimezone()
                .replace(tzinfo=None)
                .isoformat(timespec="seconds")
            )
        elif len(measured_at) == 10:
            measured_at = parsed.isoformat(timespec="minutes")

    return {
        "profile": " ".join(str(profile or "").split())[:MAX_PROFILE_NAME_LENGTH],
        "metric": metric,
        "value1": round(first, 2),
        "value2": round(second, 2) if second is not None else None,
        "unit": unit,
        "measured_at": measured_at,
        "note": " ".join(str(note or "").split())[:MAX_METRIC_NOTE_LENGTH],
    }


def _normalize_tags(tags: Any) -> List[str]:
    """整理标签：去重、去控制字符、限制数量与长度。"""
    if isinstance(tags, str):
        tags = [tags]
    if not isinstance(tags, (list, tuple)):
        return []

    result: List[str] = []
    seen = set()
    for raw in tags:
        text = " ".join(str(raw or "").split())
        text = text.replace('"', "").replace("\\", "")
        text = text[:MAX_TAG_LENGTH]
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
        if len(result) >= MAX_TAGS:
            break
    return result


def _parse_tags(raw: Any) -> List[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return _normalize_tags(value)


def _normalize_profile_name(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_PROFILE_NAME_LENGTH]


def _normalize_reminder_due(value: Any) -> str:
    due = str(value or "").strip()[:32]
    if not due or not REMINDER_DATE_PATTERN.match(due):
        raise ValueError("提醒日期格式应为 YYYY-MM-DD 或 ISO 时间")
    try:
        parsed = datetime.fromisoformat(due)
    except ValueError:
        raise ValueError("提醒日期不是有效日期")
    if parsed.year > 9998:
        raise ValueError("提醒日期超出支持范围")
    return due


def _normalize_reminder_advance(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("提前提醒天数应为 0-30 的整数")
    if days < 0 or days > 30:
        raise ValueError("提前提醒天数应为 0-30 的整数")
    return days


MAX_REMINDER_REPEAT_DAYS = 365


def _normalize_reminder_repeat_days(value: Any) -> int:
    if value is None or value == "":
        return 0
    try:
        days = int(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("重复间隔应为 0-365 的整数")
    if days < 0 or days > MAX_REMINDER_REPEAT_DAYS:
        raise ValueError("重复间隔应为 0-365 的整数")
    return days


def _normalize_reminder_repeat_until(value: Any) -> str:
    text = str(value or "").strip()[:32]
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("重复截止日期不是有效日期")
    if parsed.year > 9998:
        raise ValueError("重复截止日期超出支持范围")
    return text


def _next_repeat_due(due_at: str, days: int) -> str:
    """在原到期日上按间隔顺延；已错过的周期自动跳到今天之后。"""
    days = max(1, int(days))
    try:
        parsed = datetime.fromisoformat(str(due_at))
    except ValueError:
        parsed = datetime.combine(datetime.now().date(), datetime.min.time())
    target = parsed.date() + timedelta(days=days)
    today = datetime.now().date()
    if target <= today:
        missed = (today - target).days
        target += timedelta(days=((missed // days) + 1) * days)
    if "T" in str(due_at):
        return datetime.combine(
            target, parsed.time()
        ).isoformat(timespec="seconds")
    return target.isoformat()


def _tag_like_pattern(tag: str) -> str:
    escaped = (
        str(tag or "")
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
        .replace('"', "")
    )
    return f'%"{escaped}"%'


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
            except (TypeError, ValueError, OverflowError):
                continue

    try:
        entry["llm_calls"] += int(metadata.get("llm_calls") or 0)
    except (TypeError, ValueError, OverflowError):
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


def _like_pattern(query: str) -> str:
    """生成转义过 % / _ 的 LIKE 模式，避免搜索词被当作通配符。"""
    escaped = (
        (query or "").strip()
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _days_cutoff(days: int) -> Optional[str]:
    """把“最近 N 天”转换成 updated_at 的下限。"""
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")


def _session_filter_conditions(
    query: str = "",
    favorites_only: bool = False,
    days: int = 0,
    archived: Optional[bool] = None,
    tag: Optional[str] = None,
    profile: Optional[str] = None,
) -> Tuple[List[str], List[Any]]:
    """构建历史列表/统计共用的 WHERE 条件与参数。"""
    conditions: List[str] = []
    params: List[Any] = []
    if query:
        conditions.append("""
            (s.title LIKE ? ESCAPE '\\' OR s.id LIKE ? ESCAPE '\\'
             OR EXISTS (
                 SELECT 1 FROM messages mm
                  WHERE mm.session_id = s.id AND mm.content LIKE ? ESCAPE '\\'
             ))
        """)
        like = _like_pattern(query)
        params.extend([like, like, like])
    if favorites_only:
        conditions.append("s.favorite = 1")
    if archived is not None:
        conditions.append("s.archived = ?")
        params.append(1 if archived else 0)
    if tag:
        conditions.append("s.tags LIKE ? ESCAPE '\\'")
        params.append(_tag_like_pattern(tag))
    profile = _normalize_profile_name(profile)
    if profile:
        conditions.append("s.profile = ?")
        params.append(profile)
    cutoff = _days_cutoff(days)
    if cutoff:
        conditions.append("s.updated_at >= ?")
        params.append(cutoff)
    return conditions, params


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
                    favorite   INTEGER NOT NULL DEFAULT 0,
                    archived   INTEGER NOT NULL DEFAULT 0,
                    tags       TEXT NOT NULL DEFAULT '[]',
                    profile    TEXT NOT NULL DEFAULT ''
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
                CREATE TABLE IF NOT EXISTS patient_profiles (
                    name       TEXT PRIMARY KEY,
                    context    TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profiles_updated
                    ON patient_profiles(updated_at DESC);
                CREATE TABLE IF NOT EXISTS health_metrics (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile     TEXT NOT NULL DEFAULT '',
                    metric      TEXT NOT NULL,
                    value1      REAL NOT NULL,
                    value2      REAL,
                    unit        TEXT NOT NULL DEFAULT '',
                    measured_at TEXT NOT NULL,
                    note        TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_metrics_profile_time
                    ON health_metrics(profile, metric, measured_at DESC);
                CREATE TABLE IF NOT EXISTS metric_targets (
                    profile    TEXT NOT NULL DEFAULT '',
                    metric     TEXT NOT NULL,
                    value1     REAL NOT NULL,
                    value2     REAL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (profile, metric)
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    action     TEXT NOT NULL,
                    target     TEXT NOT NULL DEFAULT '',
                    detail     TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_audit_created
                    ON audit_log(created_at DESC);
                CREATE TABLE IF NOT EXISTS reminders (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    title      TEXT NOT NULL,
                    note       TEXT NOT NULL DEFAULT '',
                    due_at     TEXT NOT NULL,
                    session_id TEXT NOT NULL DEFAULT '',
                    profile    TEXT NOT NULL DEFAULT '',
                    advance_days INTEGER NOT NULL DEFAULT 0,
                    repeat_days INTEGER NOT NULL DEFAULT 0,
                    repeat_until TEXT NOT NULL DEFAULT '',
                    repeats_from INTEGER NOT NULL DEFAULT 0,
                    done       INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(done, due_at ASC);
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
            if "archived" not in session_columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN archived INTEGER NOT NULL DEFAULT 0"
                )
            if "tags" not in session_columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN tags TEXT NOT NULL DEFAULT '[]'"
                )
            if "profile" not in session_columns:
                self._conn.execute(
                    "ALTER TABLE sessions ADD COLUMN profile TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_profile_updated
                    ON sessions(profile, updated_at DESC)
                """
            )
            reminder_columns = {
                row["name"]
                for row in self._conn.execute("PRAGMA table_info(reminders)")
            }
            if "profile" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN profile TEXT NOT NULL DEFAULT ''"
                )
            if "advance_days" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN advance_days INTEGER NOT NULL DEFAULT 0"
                )
            if "repeat_days" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN repeat_days INTEGER NOT NULL DEFAULT 0"
                )
            if "repeat_until" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN repeat_until TEXT NOT NULL DEFAULT ''"
                )
            if "repeats_from" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN repeats_from INTEGER NOT NULL DEFAULT 0"
                )

    @staticmethod
    def _now() -> str:
        return datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _make_title(question: str) -> str:
        title = " ".join(question.split())
        return title[:MAX_TITLE_LENGTH] or "未命名会诊"

    def ensure_session(
        self, session_id: str, title: str = "", profile: str = ""
    ) -> None:
        """确保会话存在；已有会话不会被覆盖，档案只在为空时补上。"""
        now = self._now()
        profile = _normalize_profile_name(profile)
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions(id, title, profile, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, title, profile, now, now),
            )
            if profile:
                self._conn.execute(
                    """
                    UPDATE sessions SET profile = ?
                     WHERE id = ? AND profile = ''
                    """,
                    (profile, session_id),
                )

    def set_session_profile(self, session_id: str, profile: str) -> bool:
        """把已有会话关联到档案（只在档案为空时生效）；不会创建会话。"""
        session_id = str(session_id or "").strip()
        profile = _normalize_profile_name(profile)
        if not session_id or not profile:
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions SET profile = ?
                 WHERE id = ? AND profile = ''
                """,
                (profile, session_id),
            )
            return cursor.rowcount > 0

    def list_session_profiles(self) -> List[Dict[str, Any]]:
        """返回会话中出现过的档案名及数量（含已删除档案留下的历史名字）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT profile, COUNT(*) AS count
                  FROM sessions
                 WHERE profile != ''
                 GROUP BY profile
                 ORDER BY profile
                """
            ).fetchall()
        return [
            {"name": row["profile"], "count": int(row["count"])}
            for row in rows
        ]

    def list_used_profile_names(self) -> List[str]:
        """返回所有数据表中出现过的档案名（含已删除档案留下的历史名字）。"""
        names = set()
        with self._lock:
            for table, column in (
                ("patient_profiles", "name"),
                ("sessions", "profile"),
                ("reminders", "profile"),
                ("metric_targets", "profile"),
                ("health_metrics", "profile"),
            ):
                rows = self._conn.execute(
                    f"SELECT DISTINCT {column} AS name FROM {table}"
                    f" WHERE {column} != ''"
                ).fetchall()
                names.update(row["name"] for row in rows)
        return sorted(names)

    def save_exchange(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """保存一轮问答。标题取第一轮主诉。

        Args:
            metadata: 附加到 assistant 消息上的元数据（来源、科室意见等）

        Returns:
            assistant 消息的数据库 id；没有答复时返回 None。
        """
        question = (question or "").strip()
        answer = (answer or "").strip()
        if not session_id or not question:
            return None

        now = self._now()
        title = self._make_title(question)
        metadata_json = json.dumps(
            sanitize_json(metadata or {}),
            ensure_ascii=False,
            allow_nan=False,
        )
        assistant_id: Optional[int] = None

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
                cursor = self._conn.execute(
                    """
                    INSERT INTO messages(session_id, role, content, metadata, created_at)
                    VALUES (?, 'assistant', ?, ?, ?)
                    """,
                    (session_id, answer, metadata_json, now),
                )
                assistant_id = int(cursor.lastrowid)

        return assistant_id

    def list_sessions(
        self,
        limit: int = 50,
        query: str = "",
        offset: int = 0,
        favorites_only: bool = False,
        days: int = 0,
        archived: Optional[bool] = None,
        tag: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按最近更新时间倒序返回会话，支持搜索、分页、收藏、时间范围、归档、标签与档案筛选。

        archived 为 None 时返回全部（备份导出用）；False 只看未归档；True 只看已归档。
        """
        limit = max(1, min(int(limit or 50), 200))
        offset = max(0, int(offset or 0))
        sql = """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.favorite,
                   s.archived, s.tags, s.profile,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'assistant'
                     ORDER BY m.id DESC LIMIT 1) AS last_answer
              FROM sessions s
        """
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
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
                like = _like_pattern(query)
                match_rows = self._conn.execute(
                    f"""
                    SELECT session_id, content
                      FROM messages
                     WHERE session_id IN ({placeholders}) AND content LIKE ? ESCAPE '\\'
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
            item["archived"] = bool(item.get("archived"))
            item["tags"] = _parse_tags(item.get("tags"))
            preview = (item.pop("last_answer", "") or "").replace("\n", " ")
            item["preview"] = preview[:PREVIEW_LENGTH]
            if query:
                item["match_snippet"] = snippets.get(item["id"], "")
            sessions.append(item)
        return sessions

    def count_sessions(
        self,
        query: str = "",
        favorites_only: bool = False,
        days: int = 0,
        archived: Optional[bool] = None,
        tag: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> int:
        """返回匹配搜索条件的会话总数，供分页展示。"""
        sql = "SELECT COUNT(*) AS n FROM sessions s"
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return int(row["n"]) if row else 0

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, title, profile, created_at, updated_at FROM sessions WHERE id = ?",
                (session_id,),
            ).fetchone()
        return dict(row) if row else None

    def get_messages(self, session_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        """返回最近 limit 条消息，按时间正序；附带解析后的 metadata。"""
        limit = max(1, min(int(limit or 100), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, role, content, metadata, created_at
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
                item["metadata"] = sanitize_json(
                    json.loads(item.get("metadata") or "{}")
                )
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            messages.append(item)
        return messages

    def get_all_messages(self, session_id: str) -> List[Dict[str, Any]]:
        """返回会话的全部消息（备份导出用，不做条数截断）。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, role, content, metadata, created_at
                  FROM messages
                 WHERE session_id = ?
                 ORDER BY id
                """,
                (session_id,),
            ).fetchall()

        messages = []
        for row in rows:
            item = dict(row)
            try:
                item["metadata"] = sanitize_json(
                    json.loads(item.get("metadata") or "{}")
                )
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
        self,
        query: str = "",
        favorites_only: bool = False,
        days: int = 0,
        archived: Optional[bool] = None,
        tag: Optional[str] = None,
        profile: Optional[str] = None,
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
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
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

    def feedback_totals(
        self,
        query: str = "",
        favorites_only: bool = False,
        days: int = 0,
        archived: Optional[bool] = None,
        tag: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """汇总会诊答复的用户反馈，支持与历史列表相同的筛选条件。"""
        sql = """
            SELECT m.metadata
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant'
        """
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
        if conditions:
            sql += " AND " + " AND ".join(conditions)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        helpful = 0
        unhelpful = 0
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            feedback = metadata.get("feedback")
            if not isinstance(feedback, dict):
                continue
            try:
                rating = int(feedback.get("rating"))
            except (TypeError, ValueError, OverflowError):
                continue
            if rating == 1:
                helpful += 1
            elif rating == -1:
                unhelpful += 1

        total = helpful + unhelpful
        return {
            "total": total,
            "helpful": helpful,
            "unhelpful": unhelpful,
            "rate": round(helpful / total * 100, 1) if total else None,
        }

    def session_totals(
        self,
        query: str = "",
        favorites_only: bool = False,
        days: int = 0,
        archived: Optional[bool] = None,
        tag: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """一次扫描同时汇总匹配会话的用量与反馈（供历史列表使用）。"""
        sql = """
            SELECT m.metadata
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant'
        """
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
        if conditions:
            sql += " AND " + " AND ".join(conditions)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        totals = _new_usage_entry()
        helpful = 0
        unhelpful = 0
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            _accumulate_usage(totals, metadata)

            feedback = metadata.get("feedback")
            if not isinstance(feedback, dict):
                continue
            try:
                rating = int(feedback.get("rating"))
            except (TypeError, ValueError, OverflowError):
                continue
            if rating == 1:
                helpful += 1
            elif rating == -1:
                unhelpful += 1

        feedback_total = helpful + unhelpful
        return {
            "usage": totals["usage"],
            "llm_calls": totals["llm_calls"],
            "turns": totals["turns"],
            "feedback": {
                "total": feedback_total,
                "helpful": helpful,
                "unhelpful": unhelpful,
                "rate": round(helpful / feedback_total * 100, 1)
                if feedback_total
                else None,
            },
        }

    def recent_feedback(
        self,
        limit: int = 10,
        rating: int = -1,
        profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """返回最近带指定评分的会诊反馈，供质量复核。"""
        try:
            rating = int(rating)
        except (TypeError, ValueError, OverflowError):
            return []
        limit = max(1, min(int(limit or 10), 100000))

        sql = """
            SELECT m.id AS message_id, m.session_id, m.metadata,
                   m.created_at, s.title AS session_title
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant'
        """
        params: List[Any] = []
        profile = _normalize_profile_name(profile)
        if profile:
            sql += " AND s.profile = ?"
            params.append(profile)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        items: List[Dict[str, Any]] = []
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(metadata, dict):
                continue
            feedback = metadata.get("feedback")
            if not isinstance(feedback, dict):
                continue
            try:
                item_rating = int(feedback.get("rating"))
            except (TypeError, ValueError, OverflowError):
                continue
            if item_rating != rating:
                continue
            items.append({
                "message_id": row["message_id"],
                "session_id": row["session_id"],
                "session_title": row["session_title"],
                "rating": item_rating,
                "comment": str(feedback.get("comment") or "")[:MAX_FEEDBACK_LENGTH],
                "updated_at": str(feedback.get("updated_at") or row["created_at"]),
            })

        items.sort(key=lambda item: item["updated_at"], reverse=True)
        return items[:limit]

    def set_message_feedback(
        self,
        session_id: str,
        message_id: int,
        rating: int,
        comment: str = ""
    ) -> Optional[Dict[str, Any]]:
        """记录或清除某条会诊答复的反馈。

        rating: 1 有用，-1 待改进，0 清除反馈。
        消息不存在、不属于该会话或不是 assistant 消息时返回 None。
        """
        try:
            message_id = int(message_id)
            rating = int(rating)
        except (TypeError, ValueError, OverflowError):
            return None
        if rating not in (-1, 0, 1):
            return None

        comment = " ".join((comment or "").split())[:MAX_FEEDBACK_LENGTH]
        now = self._now()

        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT metadata FROM messages
                 WHERE id = ? AND session_id = ? AND role = 'assistant'
                """,
                (message_id, session_id),
            ).fetchone()
            if row is None:
                return None

            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            if rating == 0:
                metadata.pop("feedback", None)
            else:
                metadata["feedback"] = {
                    "rating": rating,
                    "comment": comment,
                    "updated_at": now,
                }

            self._conn.execute(
                "UPDATE messages SET metadata = ? WHERE id = ?",
                (
                    json.dumps(
                        sanitize_json(metadata),
                        ensure_ascii=False,
                        allow_nan=False,
                    ),
                    message_id,
                ),
            )

        return {"message_id": message_id, "rating": rating, "comment": comment}

    def quality_insights(
        self, days: int = 30, profile: Optional[str] = None
    ) -> Dict[str, Any]:
        """汇总最近一段时间的会诊质量信号。

        只做统计，不包含完整会话正文；用于历史抽屉的质量概览。
        """
        days = max(1, min(int(days or 30), 365))
        cutoff = _days_cutoff(days)
        sql = """
            SELECT m.session_id, m.metadata, m.created_at
              FROM messages m
              JOIN sessions s ON s.id = m.session_id
             WHERE m.role = 'assistant'
        """
        params: List[Any] = []
        if cutoff:
            sql += " AND m.created_at >= ?"
            params.append(cutoff)
        profile = _normalize_profile_name(profile)
        if profile:
            sql += " AND s.profile = ?"
            params.append(profile)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        sessions = set()
        turns = 0
        emergency = 0
        medication_sessions = set()
        medication_alerts = 0
        helpful = 0
        unhelpful = 0
        swarm_turns = 0
        total_time = 0.0
        timed_turns = 0
        total_tokens = 0
        risk_counts: Dict[str, int] = {}
        alert_counts: Dict[str, int] = {}

        for row in rows:
            turns += 1
            sessions.add(row["session_id"])

            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                metadata = {}

            if metadata.get("swarm_enabled") is True:
                swarm_turns += 1

            triage = metadata.get("triage")
            if isinstance(triage, dict) and triage.get("level") == "emergency":
                emergency += 1
                matches = triage.get("matches")
                if isinstance(matches, list):
                    for label in matches:
                        text = str(label or "").strip()
                        if text:
                            risk_counts[text] = risk_counts.get(text, 0) + 1

            safety = metadata.get("medication_safety")
            if isinstance(safety, dict):
                alerts = safety.get("alerts")
                if isinstance(alerts, list) and alerts:
                    medication_sessions.add(row["session_id"])
                    for alert in alerts:
                        if not isinstance(alert, dict):
                            continue
                        title = str(alert.get("title") or "").strip()
                        if not title:
                            continue
                        medication_alerts += 1
                        alert_counts[title] = alert_counts.get(title, 0) + 1

            feedback = metadata.get("feedback")
            if isinstance(feedback, dict):
                try:
                    rating = int(feedback.get("rating"))
                except (TypeError, ValueError, OverflowError):
                    rating = 0
                if rating == 1:
                    helpful += 1
                elif rating == -1:
                    unhelpful += 1

            try:
                value = float(metadata.get("total_time"))
            except (TypeError, ValueError, OverflowError):
                value = 0.0
            if value > 0:
                total_time += value
                timed_turns += 1

            usage = metadata.get("usage")
            if isinstance(usage, dict):
                try:
                    total_tokens += int(usage.get("total_tokens") or 0)
                except (TypeError, ValueError, OverflowError):
                    pass

        feedback_total = helpful + unhelpful
        top_risks = sorted(
            risk_counts.items(), key=lambda item: (-item[1], item[0])
        )[:5]
        top_alerts = sorted(
            alert_counts.items(), key=lambda item: (-item[1], item[0])
        )[:5]

        return {
            "days": days,
            "sessions": len(sessions),
            "turns": turns,
            "emergency": emergency,
            "medication_sessions": len(medication_sessions),
            "medication_alerts": medication_alerts,
            "feedback_total": feedback_total,
            "helpful": helpful,
            "unhelpful": unhelpful,
            "rate": round(helpful / feedback_total * 100, 1) if feedback_total else None,
            "swarm_turns": swarm_turns,
            "avg_time": round(total_time / timed_turns, 1) if timed_turns else None,
            "avg_tokens": round(total_tokens / turns) if turns else 0,
            "top_risks": [
                {"label": label, "count": count}
                for label, count in top_risks
            ],
            "top_medication_alerts": [
                {"title": title, "count": count}
                for title, count in top_alerts
            ],
        }

    def daily_usage(
        self,
        days: int = 7,
        favorites_only: bool = False,
        profile: Optional[str] = None,
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
        params: List[Any] = [start_text]
        if favorites_only:
            sql += " AND s.favorite = 1"
        profile = _normalize_profile_name(profile)
        if profile:
            sql += " AND s.profile = ?"
            params.append(profile)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

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

    def month_usage(self) -> Dict[str, Any]:
        """汇总当前自然月的用量。"""
        today = datetime.now().date()
        start = today.replace(day=1).isoformat()

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT metadata
                  FROM messages
                 WHERE role = 'assistant' AND created_at >= ?
                """,
                (start,),
            ).fetchall()

        totals = _new_usage_entry()
        for row in rows:
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            _accumulate_usage(totals, metadata)

        return {
            "month": today.strftime("%Y-%m"),
            "usage": totals["usage"],
            "llm_calls": totals["llm_calls"],
            "turns": totals["turns"],
        }

    def export_data(self) -> Dict[str, Any]:
        """导出全部会话与全部消息，供本地备份或迁移。"""
        sessions = []
        offset = 0
        page_size = 200
        while True:
            batch = self.list_sessions(limit=page_size, offset=offset)
            sessions.extend(batch)
            if len(batch) < page_size:
                break
            offset += len(batch)

        exported = []
        for item in sessions:
            exported.append({
                **item,
                "messages": self.get_all_messages(item["id"]),
            })

        return {
            "version": 1,
            "exported_at": self._now(),
            "session_count": len(exported),
            "sessions": exported,
            "profiles": self.list_profiles(),
            "health_metrics": self.list_metrics(limit=100000),
            "metric_targets": self.list_metric_targets(),
            "reminders": self.list_reminders(include_done=True),
        }

    def import_data(self, data: Any, max_sessions: int = 5000) -> Dict[str, int]:
        """导入备份数据；已存在的会话编号与档案名称直接跳过，不覆盖现有记录。"""
        if not isinstance(data, dict):
            raise ValueError("备份文件格式不正确。")

        raw_sessions = data.get("sessions")
        raw_profiles = data.get("profiles")
        raw_metrics = data.get("health_metrics")
        raw_targets = data.get("metric_targets")
        raw_reminders = data.get("reminders")
        if raw_sessions is None and (
            isinstance(raw_profiles, list)
            or isinstance(raw_metrics, list)
            or isinstance(raw_reminders, list)
        ):
            raw_sessions = []
        if not isinstance(raw_sessions, list):
            raise ValueError("备份文件中没有会话。")
        if (
            not raw_sessions
            and not isinstance(raw_profiles, list)
            and not isinstance(raw_metrics, list)
            and not isinstance(raw_reminders, list)
        ):
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
                if len(raw_messages) > 10000:
                    invalid += 1
                    continue

                prepared = []
                for message in raw_messages:
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
                    metadata_json = json.dumps(
                        sanitize_json(metadata),
                        ensure_ascii=False,
                        allow_nan=False,
                    )
                    created_at = str(message.get("created_at") or now)[:40]
                    prepared.append((
                        role,
                        content,
                        metadata_json,
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
                archived = 1 if raw.get("archived") in (1, True, "1") else 0
                tags = _normalize_tags(raw.get("tags") or [])
                profile = _normalize_profile_name(raw.get("profile"))

                self._conn.execute(
                    """
                    INSERT INTO sessions(
                        id, title, created_at, updated_at, favorite, archived, tags,
                        profile
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        session_id, title, created_at, updated_at,
                        favorite, archived,
                        json.dumps(tags, ensure_ascii=False),
                        profile,
                    ),
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

            profiles_imported = 0
            if isinstance(raw_profiles, list):
                for raw in raw_profiles[:500]:
                    if not isinstance(raw, dict):
                        continue
                    name = " ".join(
                        str(raw.get("name") or "").split()
                    )[:MAX_PROFILE_NAME_LENGTH]
                    cleaned = _normalize_profile_context(raw.get("context"))
                    if not name or cleaned is None:
                        continue
                    exists = self._conn.execute(
                        "SELECT 1 FROM patient_profiles WHERE name = ?",
                        (name,),
                    ).fetchone()
                    if exists:
                        continue
                    created_at = str(raw.get("created_at") or now)[:40]
                    updated_at = str(raw.get("updated_at") or created_at)[:40]
                    self._conn.execute(
                        """
                        INSERT INTO patient_profiles(name, context, created_at, updated_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (
                            name,
                            json.dumps(
                                sanitize_json(cleaned),
                                ensure_ascii=False,
                                allow_nan=False,
                            ),
                            created_at,
                            updated_at,
                        ),
                    )
                    profiles_imported += 1

            metrics_imported = 0
            if isinstance(raw_metrics, list):
                for raw in raw_metrics[:10000]:
                    if not isinstance(raw, dict):
                        continue
                    normalized = _normalize_metric(
                        raw.get("profile", ""),
                        raw.get("metric", ""),
                        raw.get("value1"),
                        raw.get("value2"),
                        raw.get("measured_at", ""),
                        raw.get("note", ""),
                    )
                    if normalized is None:
                        continue
                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM health_metrics
                         WHERE profile = ? AND metric = ? AND measured_at = ?
                           AND value1 = ? AND IFNULL(value2, -1) = IFNULL(?, -1)
                           AND note = ?
                        """,
                        (
                            normalized["profile"],
                            normalized["metric"],
                            normalized["measured_at"],
                            normalized["value1"],
                            normalized["value2"],
                            normalized["note"],
                        ),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO health_metrics(
                            profile, metric, value1, value2, unit,
                            measured_at, note, created_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"],
                            normalized["metric"],
                            normalized["value1"],
                            normalized["value2"],
                            normalized["unit"],
                            normalized["measured_at"],
                            normalized["note"],
                            now,
                        ),
                    )
                    metrics_imported += 1

            targets_imported = 0
            if isinstance(raw_targets, list):
                for raw in raw_targets[:500]:
                    if not isinstance(raw, dict):
                        continue
                    metric = str(raw.get("metric") or "").strip()
                    if metric not in METRIC_TYPES:
                        continue
                    _, _, (low, high) = METRIC_TYPES[metric]
                    try:
                        first = float(raw.get("value1"))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if not (low <= first <= high):
                        continue
                    second: Optional[float] = None
                    if metric == "bp":
                        try:
                            second = float(raw.get("value2"))
                        except (TypeError, ValueError, OverflowError):
                            continue
                        if not (30 <= second <= 200) or first <= second:
                            continue
                    if metric != "bp":
                        second = None
                    profile = _normalize_profile_name(raw.get("profile"))
                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM metric_targets
                         WHERE profile = ? AND metric = ?
                        """,
                        (profile, metric),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO metric_targets(
                            profile, metric, value1, value2, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            profile,
                            metric,
                            round(first, 2),
                            round(second, 2) if second is not None else None,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    targets_imported += 1

            reminders_imported = 0
            if isinstance(raw_reminders, list):
                for raw in raw_reminders[:2000]:
                    if not isinstance(raw, dict):
                        continue
                    title = " ".join(
                        str(raw.get("title") or "").split()
                    )[:MAX_REMINDER_TITLE_LENGTH]
                    if not title:
                        continue
                    try:
                        due_at = _normalize_reminder_due(raw.get("due_at"))
                    except ValueError:
                        continue
                    note = " ".join(
                        str(raw.get("note") or "").split()
                    )[:MAX_REMINDER_NOTE_LENGTH]
                    profile = _normalize_profile_name(raw.get("profile"))
                    session_id = str(raw.get("session_id") or "").strip()[:64]
                    try:
                        advance_days = _normalize_reminder_advance(
                            raw.get("advance_days")
                        )
                    except ValueError:
                        advance_days = 0
                    try:
                        repeat_days = _normalize_reminder_repeat_days(
                            raw.get("repeat_days")
                        )
                    except ValueError:
                        repeat_days = 0
                    try:
                        repeat_until = _normalize_reminder_repeat_until(
                            raw.get("repeat_until")
                        )
                    except ValueError:
                        repeat_until = ""
                    try:
                        repeats_from = int(raw.get("repeats_from") or 0)
                    except (TypeError, ValueError, OverflowError):
                        repeats_from = 0
                    done = 1 if raw.get("done") in (1, True, "1") else 0
                    created_at = str(raw.get("created_at") or now)[:40]
                    updated_at = str(raw.get("updated_at") or created_at)[:40]

                    try:
                        reminder_id = int(raw.get("id") or 0)
                    except (TypeError, ValueError, OverflowError):
                        reminder_id = 0

                    if reminder_id > 0:
                        exists = self._conn.execute(
                            "SELECT 1 FROM reminders WHERE id = ?",
                            (reminder_id,),
                        ).fetchone()
                        if exists:
                            continue
                        self._conn.execute(
                            """
                            INSERT INTO reminders(
                                id, title, note, due_at, session_id, profile,
                                advance_days, repeat_days, repeat_until,
                                repeats_from, done, created_at, updated_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                reminder_id, title, note, due_at, session_id,
                                profile, advance_days, repeat_days,
                                repeat_until, repeats_from, done,
                                created_at, updated_at,
                            ),
                        )
                        reminders_imported += 1
                        continue

                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM reminders
                         WHERE title = ? AND due_at = ? AND profile = ?
                           AND note = ? AND session_id = ?
                        """,
                        (title, due_at, profile, note, session_id),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, done, created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, done, created_at, updated_at,
                        ),
                    )
                    reminders_imported += 1

        return {
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "messages": messages_imported,
            "profiles": profiles_imported,
            "metrics": metrics_imported,
            "targets": targets_imported,
            "reminders": reminders_imported,
        }

    def set_favorite(self, session_id: str, favorite: bool) -> bool:
        """收藏或取消收藏会话；会话不存在返回 False。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE sessions SET favorite = ? WHERE id = ?",
                (1 if favorite else 0, session_id),
            )
            return cursor.rowcount > 0

    def set_archived(self, session_id: str, archived: bool) -> bool:
        """归档或移出归档；会话不存在返回 False。"""
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE sessions SET archived = ? WHERE id = ?",
                (1 if archived else 0, session_id),
            )
            return cursor.rowcount > 0

    def archive_older_than(self, days: int) -> int:
        """把最后更新早于 N 天的未归档会话自动归档，返回处理数量。"""
        cutoff = _days_cutoff(days)
        if not cutoff:
            return 0
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                   SET archived = 1
                 WHERE archived = 0 AND updated_at < ?
                """,
                (cutoff,),
            )
            return cursor.rowcount

    def set_tags(
        self, session_id: str, tags: Any
    ) -> Optional[List[str]]:
        """覆盖会话标签；会话不存在返回 None。"""
        normalized = _normalize_tags(tags)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE sessions SET tags = ? WHERE id = ?",
                (json.dumps(normalized, ensure_ascii=False), session_id),
            )
            if cursor.rowcount == 0:
                return None
        return normalized

    def list_tags(
        self,
        archived: Optional[bool] = False,
        profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """统计会话标签及数量，默认只看未归档会话，可按档案筛选。"""
        sql = "SELECT tags FROM sessions"
        params: List[Any] = []
        conditions: List[str] = []
        if archived is not None:
            conditions.append("archived = ?")
            params.append(1 if archived else 0)
        profile = _normalize_profile_name(profile)
        if profile:
            conditions.append("profile = ?")
            params.append(profile)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        counts: Dict[str, int] = {}
        for row in rows:
            for tag in _parse_tags(row["tags"]):
                counts[tag] = counts.get(tag, 0) + 1

        return [
            {"name": name, "count": count}
            for name, count in sorted(
                counts.items(), key=lambda item: (-item[1], item[0])
            )
        ]

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

    def list_profiles(self) -> List[Dict[str, Any]]:
        """按名称返回全部常用患者档案。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT name, context, created_at, updated_at
                  FROM patient_profiles
                 ORDER BY name
                """
            ).fetchall()

        profiles = []
        for row in rows:
            item = dict(row)
            try:
                context = sanitize_json(json.loads(item.get("context") or "{}"))
            except (TypeError, json.JSONDecodeError):
                context = {}
            item["context"] = context if isinstance(context, dict) else {}
            profiles.append(item)
        return profiles

    def save_profile(
        self, name: str, context: Any
    ) -> Optional[Dict[str, Any]]:
        """新增或覆盖常用档案；名称或内容无效时返回 None。"""
        name = " ".join(str(name or "").split())[:MAX_PROFILE_NAME_LENGTH]
        cleaned = _normalize_profile_context(context)
        if not name or cleaned is None:
            return None

        now = self._now()
        context_json = json.dumps(
            sanitize_json(cleaned),
            ensure_ascii=False,
            allow_nan=False,
        )
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT created_at FROM patient_profiles WHERE name = ?",
                (name,),
            ).fetchone()
            created_at = row["created_at"] if row else now
            if row:
                self._conn.execute(
                    """
                    UPDATE patient_profiles
                       SET context = ?, updated_at = ?
                     WHERE name = ?
                    """,
                    (context_json, now, name),
                )
            else:
                self._conn.execute(
                    """
                    INSERT INTO patient_profiles(name, context, created_at, updated_at)
                    VALUES (?, ?, ?, ?)
                    """,
                    (name, context_json, now, now),
                )

        return {
            "name": name,
            "context": cleaned,
            "created_at": created_at,
            "updated_at": now,
        }

    def delete_profile(self, name: str) -> bool:
        name = " ".join(str(name or "").split())[:MAX_PROFILE_NAME_LENGTH]
        if not name:
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM patient_profiles WHERE name = ?", (name,)
            )
            return cursor.rowcount > 0

    def rename_profile(
        self, old_name: str, new_name: str
    ) -> Optional[str]:
        """重命名患者档案，并同步所有以档案名为键的数据。

        旧档案不存在返回 None；新名称已被占用时抛出 ValueError。
        """
        old_name = " ".join(str(old_name or "").split())[:MAX_PROFILE_NAME_LENGTH]
        new_name = " ".join(str(new_name or "").split())[:MAX_PROFILE_NAME_LENGTH]
        if not old_name or not new_name:
            raise ValueError("档案名称不能为空")
        if old_name == new_name:
            return new_name

        with self._lock, self._conn:
            exists = self._conn.execute(
                "SELECT 1 FROM patient_profiles WHERE name = ?", (old_name,)
            ).fetchone()
            if exists is None:
                return None
            duplicate = self._conn.execute(
                "SELECT 1 FROM patient_profiles WHERE name = ?", (new_name,)
            ).fetchone()
            if duplicate is not None:
                raise ValueError("新档案名称已存在")

            now = self._now()
            self._conn.execute(
                """
                UPDATE patient_profiles
                   SET name = ?, updated_at = ?
                 WHERE name = ?
                """,
                (new_name, now, old_name),
            )
            # 目标表以 (profile, metric) 为主键：先清掉与新名称冲突的旧目标
            self._conn.execute(
                """
                DELETE FROM metric_targets
                 WHERE profile = ?
                   AND metric IN (
                       SELECT metric FROM metric_targets WHERE profile = ?
                   )
                """,
                (new_name, old_name),
            )
            for table in (
                "health_metrics",
                "metric_targets",
                "reminders",
                "sessions",
            ):
                self._conn.execute(
                    f"UPDATE {table} SET profile = ? WHERE profile = ?",
                    (new_name, old_name),
                )
        return new_name

    def add_metric(
        self,
        profile: str,
        metric: str,
        value1: Any,
        value2: Any = None,
        measured_at: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """新增一条健康指标记录；字段无效时返回 None。"""
        normalized = _normalize_metric(
            profile, metric, value1, value2, measured_at, note
        )
        if normalized is None:
            return None

        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO health_metrics(
                    profile, metric, value1, value2, unit,
                    measured_at, note, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["metric"],
                    normalized["value1"],
                    normalized["value2"],
                    normalized["unit"],
                    normalized["measured_at"],
                    normalized["note"],
                    now,
                ),
            )
            metric_id = int(cursor.lastrowid)

        return {
            "id": metric_id,
            **normalized,
            "created_at": now,
        }

    def list_metrics(
        self,
        profile: Optional[str] = None,
        metric: Optional[str] = None,
        limit: int = 100,
        days: int = 0,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """按测量时间倒序返回健康指标记录，可按最近天数筛选与分页。"""
        limit = max(1, min(int(limit or 100), 100000))
        try:
            offset = max(0, int(offset or 0))
        except (TypeError, ValueError, OverflowError):
            offset = 0
        sql = """
            SELECT id, profile, metric, value1, value2, unit,
                   measured_at, note, created_at
              FROM health_metrics
        """
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            normalized_profile = _normalize_profile_name(profile)
            conditions.append("profile = ?")
            params.append(normalized_profile)
        if metric:
            if metric not in METRIC_TYPES:
                return []
            conditions.append("metric = ?")
            params.append(metric)
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("measured_at >= ?")
            params.append(cutoff)
            end_of_today = datetime.now().replace(
                hour=23, minute=59, second=59
            ).isoformat(timespec="seconds")
            conditions.append("measured_at <= ?")
            params.append(end_of_today)
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY measured_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def metric_summary(
        self,
        profile: Optional[str] = None,
        metric: str = "",
        days: int = 0,
    ) -> Optional[Dict[str, Any]]:
        """统计所选范围内的指标，并给出上一等长周期的均值用于对比。"""
        metric = str(metric or "").strip()
        if metric not in METRIC_TYPES:
            return None
        try:
            days = max(0, min(int(days or 0), 3650))
        except (TypeError, ValueError, OverflowError):
            days = 0

        base_conditions: List[str] = []
        base_params: List[Any] = []
        if profile is not None:
            base_conditions.append("profile = ?")
            base_params.append(_normalize_profile_name(profile))
        base_conditions.append("metric = ?")
        base_params.append(metric)

        now = datetime.now()
        end_of_today = now.replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
        conditions = list(base_conditions)
        params = list(base_params)
        prev_conditions: Optional[List[str]] = None
        prev_params: List[Any] = []
        if days > 0:
            current_start = now - timedelta(days=days)
            current_cutoff = current_start.isoformat(timespec="seconds")
            conditions.append("measured_at >= ?")
            params.append(current_cutoff)
            conditions.append("measured_at <= ?")
            params.append(end_of_today)
            prev_start = current_start - timedelta(days=days)
            prev_conditions = base_conditions + [
                "measured_at >= ?",
                "measured_at < ?",
            ]
            prev_params = base_params + [
                prev_start.isoformat(timespec="seconds"),
                current_cutoff,
            ]
        else:
            conditions.append("measured_at <= ?")
            params.append(end_of_today)

        where = " AND ".join(conditions)
        with self._lock:
            row = self._conn.execute(
                f"""
                SELECT COUNT(*) AS count,
                       AVG(value1) AS avg1,
                       MIN(value1) AS min1,
                       MAX(value1) AS max1,
                       AVG(value2) AS avg2,
                       MIN(value2) AS min2,
                       MAX(value2) AS max2
                  FROM health_metrics
                 WHERE {where}
                """,
                params,
            ).fetchone()
            latest_row = self._conn.execute(
                f"""
                SELECT id, profile, metric, value1, value2, unit,
                       measured_at, note, created_at
                  FROM health_metrics
                 WHERE {where}
                 ORDER BY measured_at DESC, id DESC
                 LIMIT 1
                """,
                params,
            ).fetchone()
            prev_row = None
            if prev_conditions:
                prev_row = self._conn.execute(
                    f"""
                    SELECT AVG(value1) AS avg1, AVG(value2) AS avg2
                      FROM health_metrics
                     WHERE {' AND '.join(prev_conditions)}
                    """,
                    prev_params,
                ).fetchone()
            target_row = None
            if profile is not None:
                target_row = self._conn.execute(
                    """
                    SELECT value1, value2 FROM metric_targets
                     WHERE profile = ? AND metric = ?
                    """,
                    (_normalize_profile_name(profile), metric),
                ).fetchone()

        count = int(row["count"] or 0) if row else 0

        within_target = None
        if target_row is not None and count:
            target_conditions = list(conditions)
            target_params = list(params)
            target_conditions.append(
                "value1 >= ?" if metric in LOWER_BOUND_TARGET_METRICS
                else "value1 <= ?"
            )
            target_params.append(float(target_row["value1"]))
            if metric == "bp" and target_row["value2"] is not None:
                target_conditions.append("value2 <= ?")
                target_params.append(float(target_row["value2"]))
            with self._lock:
                within_target = int(self._conn.execute(
                    "SELECT COUNT(*) FROM health_metrics WHERE "
                    + " AND ".join(target_conditions),
                    target_params,
                ).fetchone()[0] or 0)

        def stat(value):
            return round(float(value), 1) if value is not None else None

        def build(avg, low, high, prev_avg):
            if avg is None and low is None and high is None:
                return None
            return {
                "avg": stat(avg),
                "min": stat(low),
                "max": stat(high),
                "prev_avg": stat(prev_avg),
            }

        value1 = build(
            row["avg1"] if row else None,
            row["min1"] if row else None,
            row["max1"] if row else None,
            prev_row["avg1"] if prev_row else None,
        )
        value2 = None
        if metric == "bp":
            value2 = build(
                row["avg2"] if row else None,
                row["min2"] if row else None,
                row["max2"] if row else None,
                prev_row["avg2"] if prev_row else None,
            )

        return {
            "metric": metric,
            "count": count,
            "days": days,
            "value1": value1,
            "value2": value2,
            "latest": dict(latest_row) if latest_row else None,
            "target": None if target_row is None else {
                "value1": float(target_row["value1"]),
                "value2": (
                    float(target_row["value2"])
                    if target_row["value2"] is not None else None
                ),
            },
            "within_target": within_target,
        }

    def update_metric(
        self,
        metric_id: int,
        profile: str,
        metric: str,
        value1: Any,
        value2: Any = None,
        measured_at: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """更新一条健康指标记录；记录不存在或字段无效时返回 None。"""
        try:
            metric_id = int(metric_id)
        except (TypeError, ValueError, OverflowError):
            return None

        normalized = _normalize_metric(
            profile, metric, value1, value2, measured_at, note
        )
        if normalized is None:
            return None

        with self._lock, self._conn:
            existing = self._conn.execute(
                "SELECT 1 FROM health_metrics WHERE id = ?", (metric_id,)
            ).fetchone()
            if existing is None:
                return None
            self._conn.execute(
                """
                UPDATE health_metrics
                   SET profile = ?, metric = ?, value1 = ?, value2 = ?,
                       unit = ?, measured_at = ?, note = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["metric"],
                    normalized["value1"],
                    normalized["value2"],
                    normalized["unit"],
                    normalized["measured_at"],
                    normalized["note"],
                    metric_id,
                ),
            )

        return {"id": metric_id, **normalized}

    def delete_metric(self, metric_id: int) -> bool:
        try:
            metric_id = int(metric_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM health_metrics WHERE id = ?", (metric_id,)
            )
            return cursor.rowcount > 0

    def set_metric_target(
        self,
        profile: str,
        metric: str,
        value1: Any,
        value2: Any = None,
    ) -> Optional[Dict[str, Any]]:
        """设置（或覆盖）某档案某指标的目标上限；无效时返回 None。"""
        metric = str(metric or "").strip()
        if metric not in METRIC_TYPES:
            return None
        _, unit, (low, high) = METRIC_TYPES[metric]
        try:
            first = float(value1)
        except (TypeError, ValueError, OverflowError):
            return None
        if not (low <= first <= high):
            return None

        second: Optional[float] = None
        if metric == "bp":
            try:
                second = float(value2)
            except (TypeError, ValueError, OverflowError):
                return None
            if not (30 <= second <= 200) or first <= second:
                return None

        profile = _normalize_profile_name(profile)
        now = self._now()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO metric_targets(
                    profile, metric, value1, value2, updated_at
                )
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(profile, metric) DO UPDATE SET
                    value1 = excluded.value1,
                    value2 = excluded.value2,
                    updated_at = excluded.updated_at
                """,
                (
                    profile,
                    metric,
                    round(first, 2),
                    round(second, 2) if second is not None else None,
                    now,
                ),
            )
        return {
            "profile": profile,
            "metric": metric,
            "value1": round(first, 2),
            "value2": round(second, 2) if second is not None else None,
            "unit": unit,
            "updated_at": now,
        }

    def list_metric_targets(
        self, profile: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """返回自定义指标目标；可按档案筛选。"""
        sql = "SELECT profile, metric, value1, value2, updated_at FROM metric_targets"
        params: List[Any] = []
        profile = _normalize_profile_name(profile)
        if profile:
            sql += " WHERE profile = ?"
            params.append(profile)
        sql += " ORDER BY profile, metric"

        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()

        result = []
        for row in rows:
            item = dict(row)
            item["unit"] = METRIC_TYPES.get(item["metric"], ("", "", (0, 0)))[1]
            result.append(item)
        return result

    def delete_metric_target(self, profile: str, metric: str) -> bool:
        profile = _normalize_profile_name(profile)
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM metric_targets WHERE profile = ? AND metric = ?",
                (profile, str(metric or "").strip()),
            )
            return cursor.rowcount > 0

    def log_action(
        self, action: str, target: str = "", detail: str = ""
    ) -> int:
        """记录一条操作审计（只保存动作、对象与简短说明）。"""
        action = " ".join(str(action or "").split())[:80]
        if not action:
            return 0
        target = " ".join(str(target or "").split())[:120]
        detail = " ".join(str(detail or "").split())[:500]
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO audit_log(action, target, detail, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (action, target, detail, now),
            )
            return int(cursor.lastrowid)

    def list_audit_log(
        self,
        limit: int = 50,
        action: Optional[str] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按时间倒序返回操作审计，可按动作、日期范围与关键字筛选。"""
        limit = max(1, min(int(limit or 50), 100000))
        clauses: List[str] = []
        params: List[Any] = []

        action = " ".join(str(action or "").split())[:80]
        if action:
            clauses.append("action = ?")
            params.append(action)

        start = str(start or "").strip()[:32]
        if start:
            clauses.append("created_at >= ?")
            params.append(start)

        end = str(end or "").strip()[:32]
        if end:
            clauses.append("created_at <= ?")
            params.append(end + "T23:59:59" if len(end) == 10 else end)

        keyword = " ".join(str(keyword or "").split())[:80]
        if keyword:
            pattern = f"%{keyword}%"
            clauses.append("(target LIKE ? OR detail LIKE ?)")
            params.extend([pattern, pattern])

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, action, target, detail, created_at
                  FROM audit_log{where}
                 ORDER BY id DESC
                 LIMIT ?
                """,
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def list_audit_actions(self) -> List[str]:
        """返回出现过的审计动作类型（去重排序）。"""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT action FROM audit_log ORDER BY action"
            ).fetchall()
        return [row["action"] for row in rows]

    def add_reminder(
        self,
        title: str,
        due_at: str,
        note: str = "",
        session_id: str = "",
        profile: str = "",
        advance_days: int = 0,
        repeat_days: int = 0,
        repeat_until: str = "",
    ) -> Dict[str, Any]:
        """新增一条随访提醒，日期接受 YYYY-MM-DD 或 ISO 时间。"""
        title = " ".join(str(title or "").split())[:MAX_REMINDER_TITLE_LENGTH]
        if not title:
            raise ValueError("提醒内容不能为空")
        due_at = _normalize_reminder_due(due_at)
        note = " ".join(str(note or "").split())[:MAX_REMINDER_NOTE_LENGTH]
        session_id = str(session_id or "").strip()[:64]
        profile = _normalize_profile_name(profile)
        advance_days = _normalize_reminder_advance(advance_days)
        repeat_days = _normalize_reminder_repeat_days(repeat_days)
        repeat_until = _normalize_reminder_repeat_until(repeat_until)
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO reminders(
                    title, note, due_at, session_id, profile, advance_days,
                    repeat_days, repeat_until, done, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    title, note, due_at, session_id, profile,
                    advance_days, repeat_days, repeat_until, now, now,
                ),
            )
            reminder_id = int(cursor.lastrowid)
        return {
            "id": reminder_id,
            "title": title,
            "note": note,
            "due_at": due_at,
            "session_id": session_id,
            "profile": profile,
            "advance_days": advance_days,
            "repeat_days": repeat_days,
            "repeat_until": repeat_until,
            "done": False,
            "created_at": now,
            "updated_at": now,
        }

    def get_reminder(self, reminder_id: int) -> Optional[Dict[str, Any]]:
        """按 id 返回单条提醒，不存在时返回 None。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, done,
                       created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (int(reminder_id),),
            ).fetchone()
        return {**dict(row), "done": bool(row["done"])} if row else None

    def list_reminders(
        self,
        include_done: bool = False,
        profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """按到期时间升序返回随访提醒，默认只返回未完成的，可按档案筛选。"""
        clauses: List[str] = []
        params: List[Any] = []
        if not include_done:
            clauses.append("done = 0")
        profile = _normalize_profile_name(profile)
        if profile:
            clauses.append("profile = ?")
            params.append(profile)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, done,
                       created_at, updated_at
                  FROM reminders{where}
                 ORDER BY done ASC, due_at ASC, id ASC
                """,
                params,
            ).fetchall()
        return [
            {**dict(row), "done": bool(row["done"])}
            for row in rows
        ]

    def complete_reminder(self, reminder_id: int) -> bool:
        """把提醒标记为已完成，不存在时返回 False。"""
        return self.complete_reminder_with_repeat(reminder_id) is not None

    def complete_reminder_with_repeat(
        self, reminder_id: int
    ) -> Optional[Dict[str, Any]]:
        """完成提醒并按重复设置安排下一次；不存在时返回 None。"""
        try:
            reminder_id = int(reminder_id)
        except (TypeError, ValueError, OverflowError):
            return None
        now = self._now()
        with self._lock, self._conn:
            row = self._conn.execute(
                """
                SELECT id, title, note, due_at, session_id, profile,
                       advance_days, repeat_days, repeat_until,
                       repeats_from, done,
                       created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (reminder_id,),
            ).fetchone()
            if row is None:
                return None

            already_done = bool(row["done"])
            self._conn.execute(
                "UPDATE reminders SET done = 1, updated_at = ? WHERE id = ?",
                (now, reminder_id),
            )

            next_item = None
            repeat_days = int(row["repeat_days"] or 0)
            if repeat_days > 0 and not already_done:
                try:
                    next_due = _next_repeat_due(row["due_at"], repeat_days)
                except (TypeError, ValueError, OverflowError):
                    next_due = ""
                repeat_until = str(row["repeat_until"] or "").strip()
                in_range = (
                    next_due
                    and (
                        not repeat_until
                        or next_due[:10] <= repeat_until[:10]
                    )
                )
                if in_range:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, done, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            row["title"], row["note"], next_due,
                            row["session_id"], row["profile"],
                            row["advance_days"], repeat_days,
                            repeat_until, reminder_id, now, now,
                        ),
                    )
                    next_id = int(cursor.lastrowid)
                    next_item = {
                        "id": next_id,
                        "title": row["title"],
                        "note": row["note"],
                        "due_at": next_due,
                        "session_id": row["session_id"],
                        "profile": row["profile"],
                        "advance_days": row["advance_days"],
                        "repeat_days": repeat_days,
                        "repeat_until": repeat_until,
                        "repeats_from": reminder_id,
                        "done": False,
                        "created_at": now,
                        "updated_at": now,
                    }

        return {"completed": True, "next": next_item}

    def reopen_reminder(self, reminder_id: int) -> bool:
        """恢复未完成状态，并清理该提醒自动生成的后续待办。"""
        try:
            reminder_id = int(reminder_id)
        except (TypeError, ValueError, OverflowError):
            return False
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE reminders SET done = 0, updated_at = ? WHERE id = ?",
                (now, reminder_id),
            )
            if cursor.rowcount == 0:
                return False
            rows = self._conn.execute(
                """
                WITH RECURSIVE chain(id) AS (
                    SELECT id FROM reminders WHERE repeats_from = ?
                    UNION ALL
                    SELECT r.id FROM reminders r
                      JOIN chain c ON r.repeats_from = c.id
                )
                SELECT id FROM chain
                """,
                (reminder_id,),
            ).fetchall()
            ids = [row["id"] for row in rows]
            if ids:
                placeholders = ",".join("?" for _ in ids)
                self._conn.execute(
                    f"DELETE FROM reminders WHERE done = 0 "
                    f"AND id IN ({placeholders})",
                    ids,
                )
            return True

    def delete_reminder(self, reminder_id: int) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM reminders WHERE id = ?", (int(reminder_id),)
            )
            return cursor.rowcount > 0

    def update_reminder(
        self,
        reminder_id: int,
        title: Optional[str] = None,
        due_at: Optional[str] = None,
        note: Optional[str] = None,
        advance_days: Optional[int] = None,
        profile: Optional[str] = None,
        repeat_days: Optional[int] = None,
        repeat_until: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        """修改提醒的标题、日期、备注、提前天数、档案或重复设置。"""
        updates: List[str] = []
        params: List[Any] = []

        if title is not None:
            title = " ".join(str(title).split())[:MAX_REMINDER_TITLE_LENGTH]
            if not title:
                raise ValueError("提醒内容不能为空")
            updates.append("title = ?")
            params.append(title)

        if due_at is not None:
            due_at = _normalize_reminder_due(due_at)
            updates.append("due_at = ?")
            params.append(due_at)

        if note is not None:
            note = " ".join(str(note).split())[:MAX_REMINDER_NOTE_LENGTH]
            updates.append("note = ?")
            params.append(note)

        if advance_days is not None:
            advance_days = _normalize_reminder_advance(advance_days)
            updates.append("advance_days = ?")
            params.append(advance_days)

        if profile is not None:
            updates.append("profile = ?")
            params.append(_normalize_profile_name(profile))

        if repeat_days is not None:
            repeat_days = _normalize_reminder_repeat_days(repeat_days)
            updates.append("repeat_days = ?")
            params.append(repeat_days)

        if repeat_until is not None:
            repeat_until = _normalize_reminder_repeat_until(repeat_until)
            updates.append("repeat_until = ?")
            params.append(repeat_until)

        if not updates:
            return None

        now = self._now()
        updates.append("updated_at = ?")
        params.append(now)
        params.append(int(reminder_id))

        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?",
                params,
            )
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                """
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, done,
                       created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (int(reminder_id),),
            ).fetchone()
        return {**dict(row), "done": bool(row["done"])}

    def snooze_reminder(
        self, reminder_id: int, days: int = 7
    ) -> Optional[Dict[str, Any]]:
        """把提醒推迟若干天；从今天与原到期日中较晚者起算。"""
        days = max(1, min(int(days or 7), 365))
        now = self._now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT due_at FROM reminders WHERE id = ?", (int(reminder_id),)
            ).fetchone()
            if row is None:
                return None
            raw_due = str(row["due_at"] or "").strip()
            try:
                current_dt = datetime.fromisoformat(raw_due)
            except ValueError:
                current_dt = datetime.combine(
                    datetime.now().date(), datetime.min.time()
                )
            base = max(current_dt.date(), datetime.now().date())
            try:
                target = base + timedelta(days=days)
            except OverflowError:
                return None
            if "T" in raw_due:
                new_due = datetime.combine(
                    target, current_dt.time()
                ).isoformat(timespec="seconds")
            else:
                new_due = target.isoformat()
            self._conn.execute(
                "UPDATE reminders SET due_at = ?, updated_at = ? WHERE id = ?",
                (new_due, now, int(reminder_id)),
            )
            updated = self._conn.execute(
                """
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, done,
                       created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (int(reminder_id),),
            ).fetchone()
        return {**dict(updated), "done": bool(updated["done"])}

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row else 0

    def purge_sessions_before(self, cutoff: str) -> Dict[str, int]:
        """删除 cutoff（ISO 时间）之前更新的会话及其消息，返回删除数量。"""
        cutoff = str(cutoff or "").strip()[:40]
        empty = {"sessions": 0, "messages": 0}
        if not cutoff:
            return empty

        with self._lock, self._conn:
            ids = [
                row["id"] for row in self._conn.execute(
                    "SELECT id FROM sessions WHERE updated_at < ?", (cutoff,)
                )
            ]
            if not ids:
                return empty
            placeholders = ",".join("?" for _ in ids)
            messages_deleted = self._conn.execute(
                f"DELETE FROM messages WHERE session_id IN ({placeholders})",
                ids,
            ).rowcount
            sessions_deleted = self._conn.execute(
                f"DELETE FROM sessions WHERE id IN ({placeholders})",
                ids,
            ).rowcount
        return {
            "sessions": int(sessions_deleted),
            "messages": int(messages_deleted),
        }

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
