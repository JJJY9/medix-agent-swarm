"""会话持久化存储（SQLite）。

借鉴 MiQi 的会话管理思路：服务重启后仍能列出历史会诊、恢复多轮上下文。
只用 Python 标准库 sqlite3，不引入额外依赖；数据库默认位于 data/sessions.db。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from loguru import logger

from core.serialization import sanitize_json
from core.backup import (
    MAX_BACKUP_MESSAGE_CHARS,
    MAX_BACKUP_SESSION_MESSAGES,
    MAX_BACKUP_TOTAL_CHARS,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

#: SQLite 参数绑定只接受有符号 64 位整数，超出会抛 OverflowError
MAX_SQLITE_INT = 2**63 - 1


def _bounded_offset(offset: Any) -> int:
    """把分页偏移限制在 SQLite 可绑定范围内。"""
    return max(0, min(int(offset or 0), MAX_SQLITE_INT))


def _safe_db_id(value: Any) -> int:
    """把导入数据里的 id 限制在 SQLite 可绑定范围，越界按 0 处理。"""
    try:
        parsed = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    if parsed < 0 or parsed > MAX_SQLITE_INT:
        return 0
    return parsed

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

#: 备份导入时每类数据的条数上限。与 export_data 的导出口径保持一致，
#: 避免应用自己导出的备份因超过导入上限而无法恢复
IMPORT_LIST_LIMITS = {
    "profiles": 100000,
    "health_metrics": 100000,
    "metric_targets": 100000,
    "symptoms": 100000,
    "medications": 100000,
    "medication_logs": 100000,
    "reminders": 100000,
    "audit_log": 100000,
}

#: 单条消息与整次导入的文本上限，兼顾超大备份可恢复与内存/锁占用。
#: 整库请求体上限 128MB；按 UTF-8 最坏 4 字节/字符计算，30M 字符仍能送达。
MAX_IMPORT_MESSAGE_CHARS = MAX_BACKUP_MESSAGE_CHARS
MAX_IMPORT_TOTAL_CHARS = MAX_BACKUP_TOTAL_CHARS


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

#: 血压分级（中国高血压防治指南 2018 成人标准），供统计分布使用。
BP_GRADE_KEYS = ("normal", "high_normal", "grade1", "grade2", "grade3", "low")


def classify_bp_grade(systolic: Any, diastolic: Any) -> Optional[str]:
    """按诊室血压分级；收缩压与舒张压分属不同级别时取较高者。"""
    try:
        sys_v = float(systolic)
        dia_v = float(diastolic)
    except (TypeError, ValueError, OverflowError):
        return None
    if sys_v >= 180 or dia_v >= 110:
        return "grade3"
    if sys_v >= 160 or dia_v >= 100:
        return "grade2"
    if sys_v >= 140 or dia_v >= 90:
        return "grade1"
    if sys_v >= 120 or dia_v >= 80:
        return "high_normal"
    if sys_v < 90 or dia_v < 60:
        return "low"
    return "normal"

#: 目标为下限的指标（数值越高越好，低于目标视为未达标）。
LOWER_BOUND_TARGET_METRICS = {"spo2"}


def metric_over_target(
    metric: str, value1: Any, value2: Any, target: Dict[str, Any]
) -> bool:
    """判断一条指标是否超出目标（血压同时比较舒张压，血氧按下限）。"""
    number1 = float(value1 or 0)
    if metric in LOWER_BOUND_TARGET_METRICS:
        return number1 < float(target["value1"])
    if number1 > float(target["value1"]):
        return True
    if metric == "bp" and target.get("value2") is not None:
        return float(value2 or 0) > float(target["value2"])
    return False


def adherence_rate(taken: Any, expected: Any) -> Optional[int]:
    """按已服/应服计算百分比（半值进位）；应服为 0 时返回 None。"""
    try:
        taken_value = float(taken or 0)
        expected_value = float(expected or 0)
    except (TypeError, ValueError):
        return None
    if expected_value <= 0:
        return None
    return min(100, round(taken_value / expected_value * 100 + 1e-9))


def _canonical_local_time(value: Any) -> Optional[str]:
    """把合法 ISO 时间规范化为本地 T 分隔格式；无效返回 None。"""
    text = str(value or "").strip()[:40]
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return (
            parsed.astimezone().replace(tzinfo=None)
            .isoformat(timespec="seconds")
        )
    if len(text) == 10 or (
        parsed.second == 0 and parsed.microsecond == 0
    ):
        return parsed.isoformat(timespec="minutes")
    return parsed.isoformat(timespec="seconds")


#: 允许的时钟偏差；客户端与服务端可能处于不同时区，留出 24 小时余量
FUTURE_TIME_TOLERANCE = timedelta(hours=24)


def _is_future_timestamp(value: str) -> bool:
    """判断规范化后的时间是否明显晚于当前时间。"""
    try:
        parsed = datetime.fromisoformat(str(value or ""))
    except ValueError:
        return False
    return parsed > datetime.now() + FUTURE_TIME_TOLERANCE


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
        measured_at = _canonical_local_time(measured_at)
        if measured_at is None:
            return None
        if _is_future_timestamp(measured_at):
            return None

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


MAX_MEDICATION_NAME_LENGTH = 120
MAX_MEDICATION_TEXT_LENGTH = 60

#: 常见中文数字，用于解析「每日三次」这类频次描述
_CN_DIGITS = {
    "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
    "六": 6, "七": 7, "八": 8, "九": 9,
}


def _frequency_count(token: str) -> Optional[int]:
    token = str(token or "").strip()
    if not token:
        return 1
    if token.isdigit():
        value = int(token)
        return value if 1 <= value <= 24 else None
    if token == "十":
        return 10
    return _CN_DIGITS.get(token)


def parse_frequency_doses_per_day(frequency: Any) -> Optional[float]:
    """按常见频次描述估算每日应服次数；按需服用或无法识别时返回 None。"""
    text = " ".join(str(frequency or "").split())
    if not text:
        return None
    if re.search(r"必要时|按需|临时|prn", text, re.IGNORECASE):
        return None
    lowered = text.lower()
    for key, value in (
        ("qd", 1), ("bid", 2), ("tid", 3), ("qid", 4),
        ("q12h", 2), ("q8h", 3),
    ):
        if re.search(rf"(?<![a-z]){key}(?![a-z])", lowered):
            return float(value)
    if re.search(r"每(?:两|2)\s*(?:周|星期|礼拜)", text):
        return 1 / 14
    weekly = re.search(
        r"每(?:周|星期|礼拜)\s*([一二两三四五六七八九十\d]+)?\s*次?", text
    )
    if weekly:
        count = _frequency_count(weekly.group(1) or "")
        return count / 7 if count else None
    if re.search(r"隔日|隔天|每隔一天|每隔1天|每两天|每二天", text):
        return 1 / 2
    every_days = re.search(
        r"每\s*隔?\s*([一二两三四五六七八九十\d]+)\s*天", text
    )
    if every_days:
        count = _frequency_count(every_days.group(1))
        return 1 / count if count else None
    if re.search(r"每(?:个)?月", text):
        return 1 / 30
    daily = re.search(
        r"(?:每(?:日|天|晚|早)|一日|一天)\s*([一二两三四五六七八九十\d]+)?\s*次",
        text,
    )
    if daily:
        count = _frequency_count(daily.group(1) or "")
        return float(count) if count else None
    if re.search(r"每日|每天|每晚|每早|一日|睡前|晨起", text):
        return 1.0
    return None


def expected_doses_for_period(
    frequency: str, created_at: str, now: datetime, days: int = 7
) -> Optional[int]:
    """按频次、用药开始时间与统计天数估算应服次数。"""
    per_day = parse_frequency_doses_per_day(frequency)
    if not per_day:
        return None
    days = max(1, min(int(days or 7), 3650))
    try:
        created = datetime.fromisoformat(str(created_at or "")[:19]).date()
    except ValueError:
        created = now.date()
    start = max(created, (now - timedelta(days=days - 1)).date())
    day_count = (now.date() - start).days + 1
    if day_count <= 0:
        return None
    return round(per_day * day_count)


def _expected_week_doses(
    frequency: str, created_at: str, now: datetime
) -> Optional[int]:
    """按频次与用药开始时间估算近 7 天应服次数。"""
    return expected_doses_for_period(frequency, created_at, now, 7)


def _normalize_medication(
    profile: str,
    name: str,
    dose: str,
    frequency: str,
    note: str,
    active: Any = True,
) -> Optional[Dict[str, Any]]:
    """校验一条用药记录；无效时返回 None。"""
    name = " ".join(str(name or "").split())[:MAX_MEDICATION_NAME_LENGTH]
    if not name:
        return None
    if isinstance(active, bool):
        active_flag = 1 if active else 0
    else:
        active_flag = 1 if str(active).strip().lower() in {
            "1", "true", "on", "yes"
        } else 0
    return {
        "profile": _normalize_profile_name(profile),
        "name": name,
        "dose": " ".join(str(dose or "").split())[:MAX_MEDICATION_TEXT_LENGTH],
        "frequency": " ".join(
            str(frequency or "").split()
        )[:MAX_MEDICATION_TEXT_LENGTH],
        "note": " ".join(str(note or "").split())[:MAX_METRIC_NOTE_LENGTH],
        "active": bool(active_flag),
    }


MAX_SYMPTOM_LENGTH = 120


def _normalize_symptom(
    profile: str,
    symptom: str,
    severity: Any,
    recorded_at: str,
    note: str,
) -> Optional[Dict[str, Any]]:
    """校验一条症状记录；无效时返回 None。"""
    text = " ".join(str(symptom or "").split())[:MAX_SYMPTOM_LENGTH]
    if not text:
        return None
    if isinstance(severity, bool):
        return None
    try:
        level = int(severity)
    except (TypeError, ValueError, OverflowError):
        return None
    if level < 0 or level > 10:
        return None

    recorded_at = str(recorded_at or "").strip()[:40]
    if not recorded_at:
        recorded_at = datetime.now().isoformat(timespec="minutes")
    else:
        recorded_at = _canonical_local_time(recorded_at)
        if recorded_at is None:
            return None
        if _is_future_timestamp(recorded_at):
            return None

    return {
        "profile": _normalize_profile_name(profile),
        "symptom": text,
        "severity": level,
        "note": " ".join(str(note or "").split())[:MAX_METRIC_NOTE_LENGTH],
        "recorded_at": recorded_at,
    }


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
    if target < today:
        # 顺延到第一个不早于今天的周期；恰好是今天的保留今天
        missed = (today - target).days
        steps = (missed + days - 1) // days
        target += timedelta(days=steps * days)
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


def _usage_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


def _accumulate_usage(entry: Dict[str, Any], metadata: Any) -> None:
    """把一条 assistant 消息的元数据累加进用量统计。"""
    entry["turns"] += 1
    if not isinstance(metadata, dict):
        return

    usage = metadata.get("usage")
    if isinstance(usage, dict):
        prompt = _usage_int(usage.get("prompt_tokens"))
        completion = _usage_int(usage.get("completion_tokens"))
        raw_total = usage.get("total_tokens")
        total = _usage_int(raw_total)
        if (
            not total
            and (prompt or completion)
        ):
            total = prompt + completion
        entry["usage"]["prompt_tokens"] += prompt
        entry["usage"]["completion_tokens"] += completion
        entry["usage"]["total_tokens"] += total

    entry["llm_calls"] += _usage_int(metadata.get("llm_calls"))


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
    """把“最近 N 天”转换成自然日窗口的下限（含今天，共 N 天）。"""
    try:
        days = int(days or 0)
    except (TypeError, ValueError):
        return None
    if days <= 0:
        return None
    return (
        (datetime.now() - timedelta(days=days - 1))
        .strftime("%Y-%m-%d")
        # 与 _canonical_local_time 的分钟精度保持一致：
        # 午夜整点记录存成 T00:00，用 T00:00:00 比较会被漏掉
        + "T00:00"
    )


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


#: 参与备份校验和的字段（导出时间与会话计数是派生信息，不参与）
_BACKUP_CHECKSUM_FIELDS = (
    "version",
    "sessions",
    "profiles",
    "health_metrics",
    "metric_targets",
    "reminders",
    "symptoms",
    "medications",
    "medication_logs",
    "audit_log",
)


def _canonical_json_value(value: Any) -> Any:
    """把 JSON 数值规范化为与语言无关的表示。

    浏览器 ``JSON.stringify(70.0)`` 会输出 ``70``，而 Python 导出的是
    ``70.0``；校验和必须先消除这种差异。
    """
    if isinstance(value, float):
        if not isinstance(sanitize_json(value), float):
            return None
        if value.is_integer():
            return int(value)
        return value
    if isinstance(value, dict):
        return {
            str(key): _canonical_json_value(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_canonical_json_value(item) for item in value]
    return value


def _backup_checksum(payload: Dict[str, Any]) -> str:
    """计算备份内容的 SHA-256；只覆盖可往返的业务字段。"""
    body = {key: payload.get(key) for key in _BACKUP_CHECKSUM_FIELDS}
    canonical = json.dumps(
        _canonical_json_value(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


class SessionStore:
    """基于 SQLite 的会话与消息存储。"""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = Path(db_path or os.getenv("MEDIX_SESSION_DB") or DEFAULT_DB_PATH)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 已删除会话的墓碑：阻止进行中的会诊在删除后把会话写回来
        self._deleted_sessions: Dict[str, None] = {}
        self._max_deleted_sessions = 4096
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            # 删除时立即清零释放的页面，避免问诊明文留在空闲页
            self._conn.execute("PRAGMA secure_delete=ON")
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
                CREATE INDEX IF NOT EXISTS idx_messages_role_created
                    ON messages(role, created_at);
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
                    medication_id INTEGER NOT NULL DEFAULT 0,
                    done       INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_reminders_due
                    ON reminders(done, due_at ASC);
                CREATE TABLE IF NOT EXISTS symptoms (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile     TEXT NOT NULL DEFAULT '',
                    symptom     TEXT NOT NULL,
                    severity    INTEGER NOT NULL,
                    note        TEXT NOT NULL DEFAULT '',
                    recorded_at TEXT NOT NULL,
                    created_at  TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_symptoms_profile_time
                    ON symptoms(profile, recorded_at DESC);
                CREATE TABLE IF NOT EXISTS medications (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile    TEXT NOT NULL DEFAULT '',
                    name       TEXT NOT NULL,
                    dose       TEXT NOT NULL DEFAULT '',
                    frequency  TEXT NOT NULL DEFAULT '',
                    note       TEXT NOT NULL DEFAULT '',
                    active     INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_medications_profile
                    ON medications(profile, active, name);
                CREATE TABLE IF NOT EXISTS medication_logs (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    medication_id INTEGER NOT NULL,
                    profile       TEXT NOT NULL DEFAULT '',
                    taken_at      TEXT NOT NULL,
                    note          TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_medication_logs_med_time
                    ON medication_logs(medication_id, taken_at DESC);
                CREATE INDEX IF NOT EXISTS idx_medication_logs_profile_time
                    ON medication_logs(profile, taken_at DESC);
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
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_created
                    ON sessions(created_at DESC)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_profile_created
                    ON sessions(profile, created_at DESC)
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
            if "medication_id" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN medication_id INTEGER NOT NULL DEFAULT 0"
                )
                # 旧提醒按「用药：药名 + 档案」回填关联，尽量恢复联动
                self._conn.execute(
                    """
                    UPDATE reminders
                       SET medication_id = (
                           SELECT m.id FROM medications m
                            WHERE reminders.title = substr(
                                      '用药：' || m.name, 1, 120
                                  )
                              AND m.profile = reminders.profile
                            ORDER BY m.id LIMIT 1
                       )
                     WHERE medication_id = 0
                       AND EXISTS (
                           SELECT 1 FROM medications m
                            WHERE reminders.title = substr(
                                      '用药：' || m.name, 1, 120
                                  )
                              AND m.profile = reminders.profile
                       )
                    """
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_medication
                    ON reminders(medication_id, done)
                """
            )

            # 旧版本可能保存了空格分隔或仅日期的时间，统一为可排序格式
            for table, column in (
                ("health_metrics", "measured_at"),
                ("symptoms", "recorded_at"),
            ):
                rows = self._conn.execute(
                    f"SELECT id, {column} AS value FROM {table}"
                ).fetchall()
                for row in rows:
                    canonical = _canonical_local_time(row["value"])
                    if canonical and canonical != row["value"]:
                        self._conn.execute(
                            f"UPDATE {table} SET {column} = ? WHERE id = ?",
                            (canonical, row["id"]),
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
        session_id = str(session_id or "").strip()
        with self._lock, self._conn:
            # 显式重新创建同一编号时解除墓碑
            self._deleted_sessions.pop(session_id, None)
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

    def mark_session_deleted(self, session_id: str) -> None:
        """登记已删除会话，阻止仍在进行的会诊把数据写回。"""
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._lock:
            self._deleted_sessions[session_id] = None
            while len(self._deleted_sessions) > self._max_deleted_sessions:
                self._deleted_sessions.pop(
                    next(iter(self._deleted_sessions))
                )

    def is_session_deleted(self, session_id: str) -> bool:
        with self._lock:
            return str(session_id or "").strip() in self._deleted_sessions

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
                ("symptoms", "profile"),
                ("medications", "profile"),
                ("medication_logs", "profile"),
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
        session_id = str(session_id or "").strip()
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
            # 删除与保存共用同一把锁：若会话已删除，禁止进行中的会诊写回
            if session_id in self._deleted_sessions:
                return None
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
        limit = max(1, min(int(limit or 50), 100000))
        offset = _bounded_offset(offset)
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
            last_answer = item.pop("last_answer", "") or ""
            item["risk_text"] = last_answer[:2000]
            preview = last_answer.replace("\n", " ")
            item["preview"] = preview[:PREVIEW_LENGTH]
            if query:
                item["match_snippet"] = snippets.get(item["id"], "")
            sessions.append(item)
        return sessions

    def list_sessions_for_timeline(
        self,
        profile: Optional[str] = None,
        start: str = "",
        end: str = "",
        limit: int = 200,
        include_answer: bool = False,
    ) -> List[Dict[str, Any]]:
        """按创建时间倒序返回会话，供健康时间线合并展示。"""
        limit = max(1, min(int(limit or 200), 100000))
        conditions: List[str] = ["created_at >= ?"]
        params: List[Any] = [str(start or "")]
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if end:
            conditions.append("created_at <= ?")
            params.append(str(end))
        where = " AND ".join(conditions)
        answer_column = ""
        if include_answer:
            answer_column = """,
                   (
                       SELECT substr(m.content, 1, 2000)
                         FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'assistant'
                        ORDER BY m.id DESC LIMIT 1
                   ) AS last_answer"""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT s.id, s.title, s.created_at, s.profile,
                       (
                           SELECT COUNT(*) FROM messages m
                            WHERE m.session_id = s.id
                       ) AS message_count{answer_column}
                  FROM sessions s
                 WHERE {where}
                 ORDER BY s.created_at DESC, s.id DESC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def latest_session_answers_by_profile(
        self, include_empty: bool = False
    ) -> List[Dict[str, Any]]:
        """返回每个档案最近一次会诊及其最后一条答复（截断 2000 字）。

        include_empty=True 时也返回未指定档案的最近会话，供周报按
        “未指定”筛选时计算风险等级。
        """
        profile_filter = "" if include_empty else "WHERE profile != ''"
        sql = f"""
            WITH latest AS (
                SELECT profile, id, created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY profile
                           ORDER BY created_at DESC, id DESC
                       ) AS rn
                  FROM sessions
                 {profile_filter}
            )
            SELECT l.profile, l.id AS session_id, l.created_at,
                   (
                       SELECT substr(m.content, 1, 2000)
                         FROM messages m
                        WHERE m.session_id = l.id AND m.role = 'assistant'
                        ORDER BY m.id DESC LIMIT 1
                   ) AS last_answer
              FROM latest l
             WHERE l.rn = 1
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def recorded_days(
        self, profile: Optional[str] = None, days: int = 365
    ) -> List[str]:
        """返回有健康记录（指标/症状/用药打卡/会诊）的日期，按日期倒序。"""
        try:
            days = max(1, min(int(days or 365), 3650))
        except (TypeError, ValueError, OverflowError):
            days = 365
        cutoff = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        parts: List[str] = []
        params: List[Any] = []
        for table, column in (
            ("health_metrics", "measured_at"),
            ("symptoms", "recorded_at"),
            ("medication_logs", "taken_at"),
        ):
            conditions = [f"substr({column}, 1, 10) >= ?"]
            values: List[Any] = [cutoff]
            if profile is not None:
                conditions.append("profile = ?")
                values.append(_normalize_profile_name(profile))
            parts.append(
                f"SELECT substr({column}, 1, 10) AS day FROM {table}"
                f" WHERE {' AND '.join(conditions)}"
            )
            params.extend(values)
        session_conditions = [
            "substr(created_at, 1, 10) >= ?",
            "EXISTS (SELECT 1 FROM messages m"
            " WHERE m.session_id = sessions.id)",
        ]
        session_params: List[Any] = [cutoff]
        if profile is not None:
            session_conditions.append("profile = ?")
            session_params.append(_normalize_profile_name(profile))
        parts.append(
            "SELECT substr(created_at, 1, 10) AS day FROM sessions"
            f" WHERE {' AND '.join(session_conditions)}"
        )
        params.extend(session_params)
        sql = (
            "SELECT DISTINCT day FROM ("
            + " UNION ".join(parts)
            + ") ORDER BY day DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [str(row["day"]) for row in rows if row["day"]]

    def recorded_days_by_profile(
        self, days: int = 365
    ) -> Dict[str, List[str]]:
        """按档案返回有健康记录的日期（仅已命名档案），日期升序。"""
        try:
            days = max(1, min(int(days or 365), 3650))
        except (TypeError, ValueError, OverflowError):
            days = 365
        cutoff = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        parts: List[str] = []
        params: List[Any] = []
        for table, column in (
            ("health_metrics", "measured_at"),
            ("symptoms", "recorded_at"),
            ("medication_logs", "taken_at"),
        ):
            parts.append(
                f"SELECT profile, substr({column}, 1, 10) AS day"
                f" FROM {table} WHERE profile != ''"
                f" AND substr({column}, 1, 10) >= ?"
            )
            params.append(cutoff)
        parts.append(
            "SELECT profile, substr(created_at, 1, 10) AS day"
            " FROM sessions WHERE profile != ''"
            " AND substr(created_at, 1, 10) >= ?"
            " AND EXISTS (SELECT 1 FROM messages m"
            " WHERE m.session_id = sessions.id)"
        )
        params.append(cutoff)
        sql = (
            "SELECT profile, day FROM ("
            + " UNION ".join(parts)
            + ") GROUP BY profile, day ORDER BY profile, day"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        result: Dict[str, List[str]] = {}
        for row in rows:
            if not row["day"]:
                continue
            result.setdefault(str(row["profile"]), []).append(str(row["day"]))
        return result

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

    def get_all_messages(
        self, session_id: str, max_messages: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """返回会话的全部消息（备份导出用）；可按需限制最大条数。"""
        with self._lock:
            if max_messages is not None:
                limit = max(1, min(int(max_messages), 10000))
                rows = self._conn.execute(
                    """
                    SELECT * FROM (
                        SELECT id, role, content, metadata, created_at
                          FROM messages
                         WHERE session_id = ?
                         ORDER BY id DESC
                         LIMIT ?
                    ) ORDER BY id
                    """,
                    (session_id, limit),
                ).fetchall()
            else:
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

        stats: Dict[str, Dict[str, Any]] = {}
        with self._lock:
            # SQLite 变量上限约 32766，分批查询避免大导出直接报错
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                rows = self._conn.execute(
                    f"""
                    SELECT session_id, metadata
                      FROM messages
                     WHERE role = 'assistant'
                       AND session_id IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
                for row in rows:
                    try:
                        metadata = json.loads(row["metadata"] or "{}")
                    except (TypeError, json.JSONDecodeError):
                        metadata = {}
                    entry = stats.setdefault(
                        row["session_id"], _new_usage_entry()
                    )
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
        if today.month == 12:
            next_month = today.replace(
                year=today.year + 1, month=1, day=1
            ).isoformat()
        else:
            next_month = today.replace(
                month=today.month + 1, day=1
            ).isoformat()

        with self._lock:
            rows = self._conn.execute(
                """
                SELECT metadata
                  FROM messages
                 WHERE role = 'assistant'
                   AND created_at >= ?
                   AND created_at < ?
                """,
                (start, next_month),
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
        """导出全部会话与全部消息，供本地备份或迁移。

        持锁贯穿所有表的读取，避免备份期间发生写入时产生跨表不一致。
        """
        with self._lock:
            return self._export_data()

    def _export_data(self) -> Dict[str, Any]:
        """在调用方持有可重入锁时收集各表快照。"""
        # 在单个锁内一次取快照：分页导出时若有会话被更新，
        # 会因 updated_at 排序漂移而被漏导或重复导出
        with self._lock:
            session_rows = self._conn.execute(
                """
                SELECT s.id, s.title, s.created_at, s.updated_at, s.favorite,
                       s.archived, s.tags, s.profile
                  FROM sessions s
                 ORDER BY s.updated_at DESC
                """
            ).fetchall()
            message_rows = self._conn.execute(
                """
                SELECT session_id, id, role, content, metadata, created_at
                  FROM messages
                 ORDER BY session_id, id
                """
            ).fetchall()

        messages_by_session: Dict[str, List[Dict[str, Any]]] = {}
        for row in message_rows:
            item = dict(row)
            session_id = item.pop("session_id")
            try:
                item["metadata"] = sanitize_json(
                    json.loads(item.get("metadata") or "{}")
                )
            except (TypeError, json.JSONDecodeError):
                item["metadata"] = {}
            messages_by_session.setdefault(session_id, []).append(item)

        exported = []
        for row in session_rows:
            item = dict(row)
            item["favorite"] = bool(item.get("favorite"))
            item["archived"] = bool(item.get("archived"))
            item["tags"] = _parse_tags(item.get("tags"))
            exported.append({
                **item,
                "messages": messages_by_session.get(item["id"], []),
            })

        payload = {
            "version": 1,
            "exported_at": self._now(),
            "session_count": len(exported),
            "sessions": exported,
            "audit_log": self.list_audit_log(limit=100000),
            "profiles": self.list_profiles(),
            "health_metrics": self.list_metrics(limit=100000),
            "metric_targets": self.list_metric_targets(),
            "reminders": self.list_reminders(include_done=True),
            "symptoms": self.list_symptoms(limit=100000),
            "medications": self.list_medications(limit=100000),
            "medication_logs": self.list_medication_logs(limit=100000),
        }
        payload["checksum"] = _backup_checksum(payload)
        return payload

    def import_data(
        self, data: Any, max_sessions: int = 100000
    ) -> Dict[str, int]:
        """导入备份数据；已存在的会话编号与档案名称直接跳过，不覆盖现有记录。"""
        if not isinstance(data, dict):
            raise ValueError("备份文件格式不正确。")

        checksum = data.get("checksum")
        if isinstance(checksum, str) and checksum.strip():
            if checksum.strip() != _backup_checksum(data):
                raise ValueError(
                    "备份校验失败，文件可能已损坏或被修改。"
                )

        raw_sessions = data.get("sessions")
        raw_profiles = data.get("profiles")
        raw_metrics = data.get("health_metrics")
        raw_targets = data.get("metric_targets")
        raw_reminders = data.get("reminders")
        raw_symptoms = data.get("symptoms")
        raw_medications = data.get("medications")
        raw_medication_logs = data.get("medication_logs")
        raw_audit = data.get("audit_log")
        if raw_sessions is None and (
            isinstance(raw_profiles, list)
            or isinstance(raw_metrics, list)
            or isinstance(raw_targets, list)
            or isinstance(raw_reminders, list)
            or isinstance(raw_symptoms, list)
            or isinstance(raw_medications, list)
            or isinstance(raw_medication_logs, list)
            or isinstance(raw_audit, list)
        ):
            raw_sessions = []
        if not isinstance(raw_sessions, list):
            raise ValueError("备份文件中没有会话。")
        if (
            not raw_sessions
            and not isinstance(raw_profiles, list)
            and not isinstance(raw_metrics, list)
            and not isinstance(raw_targets, list)
            and not isinstance(raw_reminders, list)
            and not isinstance(raw_symptoms, list)
            and not isinstance(raw_medications, list)
            and not isinstance(raw_medication_logs, list)
            and not isinstance(raw_audit, list)
        ):
            raise ValueError("备份文件中没有会话。")
        if len(raw_sessions) > max_sessions:
            raise ValueError(f"一次最多导入 {max_sessions} 个会话。")

        # 与 export_data 的导出口径一致：超限时明确报错，避免静默丢弃备份数据
        for key, label, items in (
            ("profiles", "档案", raw_profiles),
            ("health_metrics", "健康指标", raw_metrics),
            ("metric_targets", "指标目标", raw_targets),
            ("symptoms", "症状记录", raw_symptoms),
            ("medications", "用药", raw_medications),
            ("medication_logs", "用药打卡", raw_medication_logs),
            ("reminders", "提醒", raw_reminders),
            ("audit_log", "审计记录", raw_audit),
        ):
            cap = IMPORT_LIST_LIMITS[key]
            if isinstance(items, list) and len(items) > cap:
                raise ValueError(f"一次最多导入 {cap} 条{label}。")

        total_message_chars = 0
        for raw in raw_sessions:
            if not isinstance(raw, dict):
                continue
            messages = raw.get("messages")
            if not isinstance(messages, list):
                continue
            for message in messages:
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, str):
                    continue
                if len(content) > MAX_IMPORT_MESSAGE_CHARS:
                    raise ValueError(
                        f"单条消息最多 {MAX_IMPORT_MESSAGE_CHARS} 个字符。"
                    )
                total_message_chars += len(content)
                if total_message_chars > MAX_IMPORT_TOTAL_CHARS:
                    raise ValueError("备份中的消息总量过大。")

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
                if len(raw_messages) > MAX_BACKUP_SESSION_MESSAGES:
                    raise ValueError(
                        f"单个会话最多导入 {MAX_BACKUP_SESSION_MESSAGES} 条消息。"
                    )

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
                # 显式恢复备份即重新启用该会话，解除删除墓碑
                self._deleted_sessions.pop(session_id, None)
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
                for raw in raw_profiles[:IMPORT_LIST_LIMITS["profiles"]]:
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
                for raw in raw_metrics[:IMPORT_LIST_LIMITS["health_metrics"]]:
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
                for raw in raw_targets[:IMPORT_LIST_LIMITS["metric_targets"]]:
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

            symptoms_imported = 0
            if isinstance(raw_symptoms, list):
                for raw in raw_symptoms[:IMPORT_LIST_LIMITS["symptoms"]]:
                    if not isinstance(raw, dict):
                        continue
                    if not str(raw.get("recorded_at") or "").strip():
                        continue
                    normalized = _normalize_symptom(
                        raw.get("profile", ""),
                        raw.get("symptom", ""),
                        raw.get("severity"),
                        raw.get("recorded_at", ""),
                        raw.get("note", ""),
                    )
                    if normalized is None:
                        continue
                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM symptoms
                         WHERE profile = ? AND symptom = ? AND severity = ?
                           AND recorded_at = ? AND note = ?
                        """,
                        (
                            normalized["profile"],
                            normalized["symptom"],
                            normalized["severity"],
                            normalized["recorded_at"],
                            normalized["note"],
                        ),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO symptoms(
                            profile, symptom, severity, note,
                            recorded_at, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"],
                            normalized["symptom"],
                            normalized["severity"],
                            normalized["note"],
                            normalized["recorded_at"],
                            now,
                        ),
                    )
                    symptoms_imported += 1

            medications_imported = 0
            medication_id_map: Dict[int, Dict[str, Any]] = {}
            if isinstance(raw_medications, list):
                for raw in raw_medications[:IMPORT_LIST_LIMITS["medications"]]:
                    if not isinstance(raw, dict):
                        continue
                    normalized = _normalize_medication(
                        raw.get("profile", ""),
                        raw.get("name", ""),
                        raw.get("dose", ""),
                        raw.get("frequency", ""),
                        raw.get("note", ""),
                        raw.get("active", True),
                    )
                    if normalized is None:
                        continue
                    existing = self._conn.execute(
                        """
                        SELECT id FROM medications
                         WHERE profile = ? AND name = ? AND dose = ?
                           AND frequency = ?
                        """,
                        (
                            normalized["profile"], normalized["name"],
                            normalized["dose"], normalized["frequency"],
                        ),
                    ).fetchone()
                    if existing is not None:
                        actual_id = int(existing["id"])
                    else:
                        created_at = str(raw.get("created_at") or now)[:40]
                        updated_at = str(
                            raw.get("updated_at") or created_at
                        )[:40]
                        cursor = self._conn.execute(
                            """
                            INSERT INTO medications(
                                profile, name, dose, frequency, note, active,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                normalized["profile"], normalized["name"],
                                normalized["dose"], normalized["frequency"],
                                normalized["note"], normalized["active"],
                                created_at, updated_at,
                            ),
                        )
                        actual_id = int(cursor.lastrowid)
                        medications_imported += 1
                    try:
                        raw_id = int(raw.get("id"))
                    except (TypeError, ValueError, OverflowError):
                        raw_id = 0
                    if raw_id:
                        medication_id_map[raw_id] = {
                            "id": actual_id,
                            "profile": normalized["profile"],
                            "name": normalized["name"],
                        }

            medication_logs_imported = 0
            existing_log_counts: Dict[tuple, int] = {}
            imported_log_counts: Dict[tuple, int] = {}
            if isinstance(raw_medication_logs, list):
                for raw in raw_medication_logs[:IMPORT_LIST_LIMITS["medication_logs"]]:
                    if not isinstance(raw, dict):
                        continue
                    taken_at = _canonical_local_time(raw.get("taken_at"))
                    if taken_at is None:
                        continue
                    target: Optional[Dict[str, Any]] = None
                    try:
                        target = medication_id_map.get(
                            int(raw.get("medication_id"))
                        )
                    except (TypeError, ValueError, OverflowError):
                        target = None
                    if target is None:
                        name = " ".join(
                            str(raw.get("medication_name") or "").split()
                        )[:MAX_MEDICATION_NAME_LENGTH]
                        if not name:
                            continue
                        profile = _normalize_profile_name(raw.get("profile"))
                        dose = " ".join(
                            str(
                                raw.get("medication_dose")
                                or raw.get("dose")
                                or ""
                            ).split()
                        )[:MAX_MEDICATION_TEXT_LENGTH]
                        row = None
                        if dose:
                            row = self._conn.execute(
                                """
                                SELECT id, profile, name FROM medications
                                 WHERE profile = ? AND name = ? AND dose = ?
                                 ORDER BY id LIMIT 1
                                """,
                                (profile, name, dose),
                            ).fetchone()
                        if row is None:
                            row = self._conn.execute(
                                """
                                SELECT id, profile, name FROM medications
                                 WHERE profile = ? AND name = ?
                                 ORDER BY id LIMIT 1
                                """,
                                (profile, name),
                            ).fetchone()
                        if row is None:
                            continue
                        target = {
                            "id": int(row["id"]),
                            "profile": row["profile"],
                            "name": row["name"],
                        }
                    note = " ".join(str(raw.get("note") or "").split())[:300]
                    created_at = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if not created_at:
                        created_at = now
                    log_key = (target["id"], taken_at, note)
                    if log_key not in existing_log_counts:
                        count_row = self._conn.execute(
                            """
                            SELECT COUNT(*) AS total FROM medication_logs
                             WHERE medication_id = ? AND taken_at = ?
                               AND note = ?
                            """,
                            (target["id"], taken_at, note),
                        ).fetchone()
                        existing_log_counts[log_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_log_counts.get(log_key, 0)
                    imported_log_counts[log_key] = seen + 1
                    if seen < existing_log_counts[log_key]:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO medication_logs(
                            medication_id, profile, taken_at, note, created_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            target["id"],
                            target["profile"],
                            taken_at,
                            note,
                            created_at,
                        ),
                    )
                    medication_logs_imported += 1

            reminders_imported = 0
            reminder_links: List[Tuple[int, int]] = []
            raw_reminder_id_map: Dict[int, int] = {}
            pre_existing_reminder_ids = {
                int(row["id"])
                for row in self._conn.execute(
                    "SELECT id FROM reminders"
                ).fetchall()
            }
            if isinstance(raw_reminders, list):
                for raw in raw_reminders[:IMPORT_LIST_LIMITS["reminders"]]:
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
                    repeats_from = _safe_db_id(raw.get("repeats_from"))
                    raw_medication_id = _safe_db_id(
                        raw.get("medication_id")
                    )
                    mapped_medication = medication_id_map.get(
                        raw_medication_id
                    )
                    medication_id = (
                        mapped_medication["id"]
                        if mapped_medication else 0
                    )
                    medication_name = " ".join(
                        str(raw.get("medication_name") or "").split()
                    )[:MAX_MEDICATION_NAME_LENGTH]
                    if not medication_id and raw_medication_id > 0:
                        med_row = self._conn.execute(
                            """
                            SELECT id, profile, name FROM medications
                             WHERE id = ?
                            """,
                            (raw_medication_id,),
                        ).fetchone()
                        if (
                            med_row is not None
                            and _normalize_profile_name(med_row["profile"])
                            == profile
                            and (
                                not medication_name
                                or str(med_row["name"]) == medication_name
                            )
                        ):
                            medication_id = int(med_row["id"])
                    if not medication_id:
                        if medication_name:
                            med_row = self._conn.execute(
                                """
                                SELECT id FROM medications
                                 WHERE profile = ? AND name = ?
                                 ORDER BY id LIMIT 1
                                """,
                                (profile, medication_name),
                            ).fetchone()
                            if med_row is not None:
                                medication_id = int(med_row["id"])
                    done = 1 if raw.get("done") in (1, True, "1") else 0
                    created_at = str(raw.get("created_at") or now)[:40]
                    updated_at = str(raw.get("updated_at") or created_at)[:40]

                    reminder_id = _safe_db_id(raw.get("id"))

                    # 备份里的自增 id 只用于重建 repeats_from 关系；
                    # 数据行一律插入新 id，撞号时不能静默丢弃
                    if reminder_id > 0:
                        same_id = self._conn.execute(
                            "SELECT id, title, profile FROM reminders WHERE id = ?",
                            (reminder_id,),
                        ).fetchone()
                        if (
                            same_id is not None
                            and int(same_id["id"]) in pre_existing_reminder_ids
                            and str(same_id["title"]) == title
                            and _normalize_profile_name(same_id["profile"])
                            == profile
                        ):
                            # 同一编号、同标题、同档案才视为已导入
                            raw_reminder_id_map[reminder_id] = int(
                                same_id["id"]
                            )
                            continue
                    if reminder_id > 0 and raw.get("created_at"):
                        # 备份导入用 created_at 去重：提醒改期后仍能识别
                        exists = self._conn.execute(
                            """
                            SELECT id FROM reminders
                             WHERE title = ? AND profile = ? AND note = ?
                               AND session_id = ? AND created_at = ?
                            """,
                            (title, profile, note, session_id, created_at),
                        ).fetchone()
                    else:
                        # CSV 导入没有 created_at：按内容与到期日去重
                        exists = self._conn.execute(
                            """
                            SELECT id FROM reminders
                             WHERE title = ? AND profile = ? AND note = ?
                               AND session_id = ? AND due_at = ?
                            """,
                            (title, profile, note, session_id, due_at),
                        ).fetchone()
                    if (
                        exists is not None
                        and reminder_id > 0
                        and raw.get("created_at")
                        and int(exists["id"]) not in pre_existing_reminder_ids
                    ):
                        # 不能把本次备份导入刚插入的提醒当成既有记录，否则会
                        # 丢掉备份里同标题/同日期的另一条提醒；CSV 导入没有
                        # 备份 id/created_at，仍需按内容去重同一文件内的重复行
                        exists = None
                    if exists is not None:
                        if reminder_id > 0:
                            raw_reminder_id_map[reminder_id] = int(exists["id"])
                        continue
                    cursor = self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, medication_id, done,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            0, medication_id, done,
                            created_at, updated_at,
                        ),
                    )
                    new_reminder_id = int(cursor.lastrowid or 0)
                    if reminder_id > 0 and new_reminder_id:
                        raw_reminder_id_map[reminder_id] = new_reminder_id
                    if new_reminder_id and repeats_from > 0:
                        reminder_links.append(
                            (new_reminder_id, repeats_from)
                        )
                    reminders_imported += 1

            for new_id, raw_parent in reminder_links:
                parent_id = raw_reminder_id_map.get(raw_parent)
                if parent_id and parent_id != new_id:
                    self._conn.execute(
                        "UPDATE reminders SET repeats_from = ? WHERE id = ?",
                        (parent_id, new_id),
                    )

            audit_imported = 0
            if isinstance(raw_audit, list):
                for raw in raw_audit[:IMPORT_LIST_LIMITS["audit_log"]]:
                    if not isinstance(raw, dict):
                        continue
                    action = " ".join(
                        str(raw.get("action") or "").split()
                    )[:80]
                    if not action:
                        continue
                    target = " ".join(
                        str(raw.get("target") or "").split()
                    )[:120]
                    detail = " ".join(
                        str(raw.get("detail") or "").split()
                    )[:500]
                    created_at = str(raw.get("created_at") or now)[:40]
                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM audit_log
                         WHERE action = ? AND target = ?
                           AND detail = ? AND created_at = ?
                        """,
                        (action, target, detail, created_at),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO audit_log(action, target, detail, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (action, target, detail, created_at),
                    )
                    audit_imported += 1

        return {
            "imported": imported,
            "skipped": skipped,
            "invalid": invalid,
            "messages": messages_imported,
            "profiles": profiles_imported,
            "metrics": metrics_imported,
            "targets": targets_imported,
            "reminders": reminders_imported,
            "symptoms": symptoms_imported,
            "medications": medications_imported,
            "medication_logs": medication_logs_imported,
            "audit_log": audit_imported,
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
        try:
            days = int(days or 0)
        except (TypeError, ValueError):
            return 0
        if days <= 0:
            return 0
        # 自动归档按“最后更新于 N×24 小时之前”计算，而不是从今天零点起算，
        # 否则 days=1 会把昨天深夜更新的会话提前归档
        cutoff = (
            datetime.now() - timedelta(days=days)
        ).isoformat(timespec="seconds")
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE sessions
                   SET archived = 1
                 WHERE archived = 0
                   AND datetime(updated_at) IS NOT NULL
                   AND datetime(updated_at) < datetime(?)
                   AND favorite = 0
                   AND TRIM(tags) IN ('', '[]')
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
        display: Dict[str, str] = {}
        for row in rows:
            for tag in _parse_tags(row["tags"]):
                # 与 _normalize_tags 的 casefold 语义保持一致，
                # HTN 与 htn 统计为同一个标签
                key = tag.casefold()
                counts[key] = counts.get(key, 0) + 1
                display.setdefault(key, tag)

        return [
            {"name": display[key], "count": count}
            for key, count in sorted(
                counts.items(),
                key=lambda item: (-item[1], display[item[0]].casefold())
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

    def _checkpoint_wal(self) -> None:
        """把 WAL 合并回主库并截断，避免已删除内容残留在 -wal 文件。"""
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        except sqlite3.Error as e:
            # 有长读事务时 checkpoint 会 busy；下次删除/关闭时会再尝试
            logger.warning(f"WAL checkpoint 失败（忽略）: {e}")

    def delete_session(self, session_id: str) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            # 与 save_exchange 共用锁：先登记墓碑，进行中的会诊无法再写回
            self._deleted_sessions[session_id] = None
            while len(self._deleted_sessions) > self._max_deleted_sessions:
                self._deleted_sessions.pop(
                    next(iter(self._deleted_sessions))
                )
            deleted = cursor.rowcount > 0
        # 事务提交后再 checkpoint，把 WAL 中已删除的页面清出
        self._checkpoint_wal()
        return deleted

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
            if cursor.rowcount == 0:
                return False
            # 指标目标属于档案配置，删除档案时一并清理，避免重建同名档案继承旧目标
            self._conn.execute(
                "DELETE FROM metric_targets WHERE profile = ?", (name,)
            )
            return True

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

        with self._lock, self._conn:
            def _profile_rows(name: str) -> bool:
                """档案是否在档案表或任一业务表中存在（数据-only 档案也可重命名）。"""
                for table, column in (
                    ("patient_profiles", "name"),
                    ("sessions", "profile"),
                    ("reminders", "profile"),
                    ("health_metrics", "profile"),
                    ("symptoms", "profile"),
                    ("medications", "profile"),
                    ("medication_logs", "profile"),
                ):
                    row = self._conn.execute(
                        f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
                        (name,),
                    ).fetchone()
                    if row is not None:
                        return True
                return False

            if not _profile_rows(old_name):
                return None
            if old_name == new_name:
                return new_name
            # metric_targets 只作为配置在下面合并，不作为占用依据
            if _profile_rows(new_name):
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
                "symptoms",
                "medications",
                "medication_logs",
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
        q: str = "",
    ) -> List[Dict[str, Any]]:
        """按测量时间倒序返回健康指标记录，可按最近天数筛选与分页。"""
        limit = max(1, min(int(limit or 100), 100000))
        try:
            offset = _bounded_offset(offset)
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
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(note LIKE ? ESCAPE '\\' OR profile LIKE ? ESCAPE '\\')"
            )
            params.extend([like, like])
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

    def latest_metrics(
        self, profile: Optional[str] = None, days: int = 0
    ) -> List[Dict[str, Any]]:
        """返回每种指标类型最新的一条记录（可按档案与最近天数筛选）。"""
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("measured_at >= ?")
            params.append(cutoff)
        end_of_today = datetime.now().replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
        conditions.append("measured_at <= ?")
        params.append(end_of_today)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            WITH ranked AS (
                SELECT id, profile, metric, value1, value2, unit,
                       measured_at, note, created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY metric
                           ORDER BY measured_at DESC, id DESC
                       ) AS rn
                  FROM health_metrics{where}
            )
            SELECT id, profile, metric, value1, value2, unit,
                   measured_at, note, created_at
              FROM ranked
             WHERE rn = 1
             ORDER BY measured_at DESC
        """
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def latest_metrics_by_profile(self) -> List[Dict[str, Any]]:
        """返回每个档案下每种指标最新的一条记录（家庭总览用）。"""
        sql = """
            WITH ranked AS (
                SELECT id, profile, metric, value1, value2, unit,
                       measured_at, note, created_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY profile, metric
                           ORDER BY measured_at DESC, id DESC
                       ) AS rn
                  FROM health_metrics
            )
            SELECT id, profile, metric, value1, value2, unit,
                   measured_at, note, created_at
              FROM ranked
             WHERE rn = 1
             ORDER BY profile, metric
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
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
            current_start = (now - timedelta(days=days - 1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            # 与写入侧的分钟精度一致：午夜记录存成 T00:00，
            # 用 T00:00:00 比较会被漏出当前周期
            current_cutoff = current_start.isoformat(timespec="minutes")
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
                prev_start.isoformat(timespec="minutes"),
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

        bp_grades = None
        if metric == "bp":
            bp_grades = {key: 0 for key in BP_GRADE_KEYS}
            with self._lock:
                grade_rows = self._conn.execute(
                    f"SELECT value1, value2 FROM health_metrics WHERE {where}",
                    params,
                ).fetchall()
            for grade_row in grade_rows:
                grade = classify_bp_grade(
                    grade_row["value1"], grade_row["value2"]
                )
                if grade:
                    bp_grades[grade] += 1

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
            "bp_grades": bp_grades,
            "target_lower_bound": (
                metric in LOWER_BOUND_TARGET_METRICS
                if target_row is not None else None
            ),
        }

    def add_symptom(
        self,
        profile: str,
        symptom: str,
        severity: Any,
        recorded_at: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """新增一条症状记录；无效时返回 None。"""
        normalized = _normalize_symptom(
            profile, symptom, severity, recorded_at, note
        )
        if normalized is None:
            return None
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO symptoms(
                    profile, symptom, severity, note, recorded_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["symptom"],
                    normalized["severity"],
                    normalized["note"],
                    normalized["recorded_at"],
                    now,
                ),
            )
            symptom_id = int(cursor.lastrowid)
        return {"id": symptom_id, **normalized, "created_at": now}

    def list_symptoms(
        self,
        profile: Optional[str] = None,
        limit: int = 100,
        days: int = 0,
        offset: int = 0,
    ) -> List[Dict[str, Any]]:
        """按记录时间倒序返回症状，可按档案与最近天数筛选。"""
        limit = max(1, min(int(limit or 100), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0

        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("recorded_at >= ?")
            params.append(cutoff)
            end_of_today = datetime.now().replace(
                hour=23, minute=59, second=59
            ).isoformat(timespec="seconds")
            conditions.append("recorded_at <= ?")
            params.append(end_of_today)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT id, profile, symptom, severity, note, recorded_at, created_at
              FROM symptoms{where}
             ORDER BY recorded_at DESC, id DESC
             LIMIT ? OFFSET ?
        """
        params.extend([limit, offset])
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(row) for row in rows]

    def get_symptom(self, symptom_id: int) -> Optional[Dict[str, Any]]:
        try:
            symptom_id = int(symptom_id)
        except (TypeError, ValueError, OverflowError):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, symptom, severity, note,
                       recorded_at, created_at
                  FROM symptoms WHERE id = ?
                """,
                (symptom_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_symptom(
        self,
        symptom_id: int,
        profile: str,
        symptom: str,
        severity: Any,
        recorded_at: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """更新一条症状记录；不存在或字段无效时返回 None。"""
        try:
            symptom_id = int(symptom_id)
        except (TypeError, ValueError, OverflowError):
            return None
        normalized = _normalize_symptom(
            profile, symptom, severity, recorded_at, note
        )
        if normalized is None:
            return None
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE symptoms
                   SET profile = ?, symptom = ?, severity = ?,
                       note = ?, recorded_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["symptom"],
                    normalized["severity"],
                    normalized["note"],
                    normalized["recorded_at"],
                    symptom_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                """
                SELECT id, profile, symptom, severity, note,
                       recorded_at, created_at
                  FROM symptoms WHERE id = ?
                """,
                (symptom_id,),
            ).fetchone()
        return dict(row) if row else None

    def delete_symptom(self, symptom_id: int) -> bool:
        try:
            symptom_id = int(symptom_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM symptoms WHERE id = ?", (symptom_id,)
            )
            return cursor.rowcount > 0

    def add_medication(
        self,
        profile: str,
        name: str,
        dose: str = "",
        frequency: str = "",
        note: str = "",
        active: Any = True,
    ) -> Optional[Dict[str, Any]]:
        """新增一条用药记录；无效时返回 None。"""
        normalized = _normalize_medication(
            profile, name, dose, frequency, note, active
        )
        if normalized is None:
            return None
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO medications(
                    profile, name, dose, frequency, note, active,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["name"],
                    normalized["dose"],
                    normalized["frequency"],
                    normalized["note"],
                    normalized["active"],
                    now,
                    now,
                ),
            )
            medication_id = int(cursor.lastrowid)
        return {"id": medication_id, **normalized, "created_at": now,
                "updated_at": now}

    def list_medications(
        self,
        profile: Optional[str] = None,
        active_only: bool = False,
        limit: int = 1000,
        offset: int = 0,
        days: int = 7,
    ) -> List[Dict[str, Any]]:
        """按名称返回用药清单，可按档案、在用状态与最近天数筛选。"""
        limit = max(1, min(int(limit or 1000), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        try:
            days = max(1, min(int(days or 7), 365))
        except (TypeError, ValueError, OverflowError):
            days = 7
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if active_only:
            conditions.append("active = 1")
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        week_cutoff = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        range_cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, name, dose, frequency, note, active,
                       created_at, updated_at,
                       (
                           SELECT MAX(taken_at) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND substr(l.taken_at, 1, 10) <= ?
                       ) AS last_taken_at,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND substr(l.taken_at, 1, 10) = ?
                       ) AS today_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND substr(l.taken_at, 1, 10) >= ?
                              AND substr(l.taken_at, 1, 10) <= ?
                       ) AS week_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND substr(l.taken_at, 1, 10) >= ?
                              AND substr(l.taken_at, 1, 10) <= ?
                       ) AS range_count
                  FROM medications{where}
                 ORDER BY active DESC, name ASC, id ASC
                 LIMIT ? OFFSET ?
                """,
                [today, today, week_cutoff, today, range_cutoff, today]
                + params
                + [limit, offset],
            ).fetchall()
        return [
            {
                **dict(row),
                "active": bool(row["active"]),
                "today_count": int(row["today_count"] or 0),
                "week_count": int(row["week_count"] or 0),
                "range_count": int(row["range_count"] or 0),
                "range_days": days,
                "last_taken_at": row["last_taken_at"] or "",
                "expected_week_doses": _expected_week_doses(
                    row["frequency"], row["created_at"], now
                ),
                "expected_range_doses": expected_doses_for_period(
                    row["frequency"], row["created_at"], now, days
                ),
            }
            for row in rows
        ]

    def get_medication(self, medication_id: int) -> Optional[Dict[str, Any]]:
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, name, dose, frequency, note, active,
                       created_at, updated_at
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "active": bool(row["active"])}

    def update_medication(
        self,
        medication_id: int,
        profile: str,
        name: str,
        dose: str = "",
        frequency: str = "",
        note: str = "",
        active: Any = True,
    ) -> Optional[Dict[str, Any]]:
        """更新一条用药记录；不存在或无效时返回 None。"""
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return None
        normalized = _normalize_medication(
            profile, name, dose, frequency, note, active
        )
        if normalized is None:
            return None
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                "SELECT profile, name FROM medications WHERE id = ?",
                (medication_id,),
            ).fetchone()
            if previous is None:
                return None
            cursor = self._conn.execute(
                """
                UPDATE medications
                   SET profile = ?, name = ?, dose = ?, frequency = ?,
                       note = ?, active = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["name"],
                    normalized["dose"],
                    normalized["frequency"],
                    normalized["note"],
                    normalized["active"],
                    now,
                    medication_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            self._conn.execute(
                """
                UPDATE medication_logs
                   SET profile = ?
                 WHERE medication_id = ? AND profile != ?
                """,
                (
                    normalized["profile"],
                    medication_id,
                    normalized["profile"],
                ),
            )
            if (
                previous["name"] != normalized["name"]
                or previous["profile"] != normalized["profile"]
            ):
                self._conn.execute(
                    """
                    UPDATE reminders
                       SET title = ?, profile = ?, updated_at = ?
                     WHERE medication_id = ? AND done = 0 AND title = ?
                    """,
                    (
                        f"用药：{normalized['name']}",
                        normalized["profile"],
                        now,
                        medication_id,
                        f"用药：{previous['name']}",
                    ),
                )
                self._conn.execute(
                    """
                    UPDATE reminders
                       SET profile = ?, updated_at = ?
                     WHERE medication_id = ? AND done = 0 AND title != ?
                    """,
                    (
                        normalized["profile"],
                        now,
                        medication_id,
                        f"用药：{previous['name']}",
                    ),
                )
            row = self._conn.execute(
                """
                SELECT id, profile, name, dose, frequency, note, active,
                       created_at, updated_at
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "active": bool(row["active"])}

    def delete_medication(self, medication_id: int) -> bool:
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, self._conn:
            medication = self._conn.execute(
                "SELECT name, profile FROM medications WHERE id = ?",
                (medication_id,),
            ).fetchone()
            if medication is None:
                return False
            self._conn.execute(
                """
                DELETE FROM reminders
                 WHERE done = 0 AND medication_id = ?
                """,
                (medication_id,),
            )
            self._conn.execute(
                "DELETE FROM medication_logs WHERE medication_id = ?",
                (medication_id,),
            )
            self._conn.execute(
                "DELETE FROM medications WHERE id = ?", (medication_id,)
            )
            return True

    def add_medication_log(
        self,
        medication_id: int,
        taken_at: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """记录一次用药；药物不存在或时间无效时返回 None。"""
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return None
        taken_at = str(taken_at or "").strip()[:40]
        if not taken_at:
            taken_at = datetime.now().isoformat(timespec="minutes")
        else:
            taken_at = _canonical_local_time(taken_at)
            if taken_at is None:
                return None
            if _is_future_timestamp(taken_at):
                return None
        note = " ".join(str(note or "").split())[:300]
        now = datetime.now().isoformat(timespec="microseconds")
        with self._lock, self._conn:
            medication = self._conn.execute(
                "SELECT id, profile FROM medications WHERE id = ?",
                (medication_id,),
            ).fetchone()
            if medication is None:
                return None
            cursor = self._conn.execute(
                """
                INSERT INTO medication_logs(
                    medication_id, profile, taken_at, note, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    medication["id"],
                    medication["profile"],
                    taken_at,
                    note,
                    now,
                ),
            )
            log_id = int(cursor.lastrowid)
        return {
            "id": log_id,
            "medication_id": medication["id"],
            "profile": medication["profile"],
            "taken_at": taken_at,
            "note": note,
            "created_at": now,
        }

    def list_medication_logs(
        self,
        medication_id: Optional[int] = None,
        profile: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        days: int = 0,
    ) -> List[Dict[str, Any]]:
        """按服用时间倒序返回到药记录，可按药物、档案与最近天数筛选。"""
        limit = max(1, min(int(limit or 100), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        conditions: List[str] = []
        params: List[Any] = []
        if medication_id is not None:
            try:
                medication_id = int(medication_id)
            except (TypeError, ValueError, OverflowError):
                return []
            conditions.append("l.medication_id = ?")
            params.append(medication_id)
        if profile is not None:
            conditions.append("l.profile = ?")
            params.append(_normalize_profile_name(profile))
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("substr(l.taken_at, 1, 10) >= ?")
            params.append(cutoff[:10])
            end_of_today = datetime.now().strftime("%Y-%m-%d")
            conditions.append("substr(l.taken_at, 1, 10) <= ?")
            params.append(end_of_today)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT l.id, l.medication_id, l.profile, l.taken_at, l.note,
                       l.created_at, m.name AS medication_name,
                       m.dose AS medication_dose
                  FROM medication_logs l
                  LEFT JOIN medications m ON m.id = l.medication_id{where}
                 ORDER BY l.taken_at DESC, l.id DESC
                 LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return [dict(row) for row in rows]

    def get_medication_log(self, log_id: int) -> Optional[Dict[str, Any]]:
        try:
            log_id = int(log_id)
        except (TypeError, ValueError, OverflowError):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT l.id, l.medication_id, l.profile, l.taken_at, l.note,
                       l.created_at, m.name AS medication_name
                  FROM medication_logs l
                  LEFT JOIN medications m ON m.id = l.medication_id
                 WHERE l.id = ?
                """,
                (log_id,),
            ).fetchone()
        return dict(row) if row is not None else None

    def delete_medication_log(self, log_id: int) -> bool:
        try:
            log_id = int(log_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "DELETE FROM medication_logs WHERE id = ?", (log_id,)
            )
            return cursor.rowcount > 0

    def get_metric(self, metric_id: int) -> Optional[Dict[str, Any]]:
        """按编号读取一条健康指标记录。"""
        try:
            metric_id = int(metric_id)
        except (TypeError, ValueError, OverflowError):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, metric, value1, value2, unit,
                       measured_at, note, created_at
                  FROM health_metrics
                 WHERE id = ?
                """,
                (metric_id,),
            ).fetchone()
        return dict(row) if row else None

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
        if profile is not None:
            profile = _normalize_profile_name(profile)
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
            # 秒级边界：日期补到 23:59:59，分钟精度补到该分钟最后一秒
            if len(end) == 10:
                end = end + "T23:59:59"
            elif len(end) == 16:
                end = end + ":59"
            clauses.append("created_at <= ?")
            params.append(end)

        keyword = " ".join(str(keyword or "").split())[:80]
        if keyword:
            pattern = _like_pattern(keyword)
            clauses.append(
                "(target LIKE ? ESCAPE '\\' OR detail LIKE ? ESCAPE '\\')"
            )
            params.extend([pattern, pattern])

        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, action, target, detail, created_at
                  FROM audit_log{where}
                 ORDER BY datetime(created_at) DESC, id DESC
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

    def redact_audit_targets(
        self,
        actions: List[str],
        prefix: str,
        replacement: str,
        detail: Optional[str] = None,
    ) -> int:
        """把以 prefix 开头的审计 target 替换为不可识别内容。

        用于删除健康记录/档案时清理历史审计中残留的症状、药名或档案名。
        """
        actions = [str(action) for action in actions if str(action)]
        prefix = str(prefix or "")
        if not actions or not prefix:
            return 0
        placeholders = ",".join("?" for _ in actions)
        sets = ["target = ?"]
        params: List[Any] = [str(replacement)]
        if detail is not None:
            sets.append("detail = ?")
            params.append(str(detail)[:500])
        params.extend(actions)
        params.extend([prefix, len(prefix), prefix])
        with self._lock, self._conn:
            cursor = self._conn.execute(
                f"""
                UPDATE audit_log
                   SET {", ".join(sets)}
                 WHERE action IN ({placeholders})
                   AND (target = ? OR substr(target, 1, ?) = ?)
                """,
                params,
            )
            return cursor.rowcount

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
        medication_id: int = 0,
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
        medication_id = _safe_db_id(medication_id)
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO reminders(
                    title, note, due_at, session_id, profile, advance_days,
                    repeat_days, repeat_until, medication_id,
                    done, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    title, note, due_at, session_id, profile,
                    advance_days, repeat_days, repeat_until,
                    medication_id, now, now,
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
            "medication_id": medication_id,
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
                       repeat_days, repeat_until, repeats_from, medication_id,
                       done, created_at, updated_at
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
        if profile is not None:
            profile = _normalize_profile_name(profile)
            clauses.append("profile = ?")
            params.append(profile)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, medication_id,
                       done, created_at, updated_at
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
                       repeats_from, medication_id, done,
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
                in_range = bool(next_due)
                if in_range and repeat_until:
                    try:
                        next_dt = datetime.fromisoformat(next_due)
                        until_dt = datetime.fromisoformat(repeat_until)
                        # repeat_until 允许带时区；统一转成 naive 本地时间再比较
                        if until_dt.tzinfo is not None:
                            until_dt = until_dt.astimezone().replace(
                                tzinfo=None
                            )
                        if len(repeat_until) == 10:
                            until_dt = datetime.combine(
                                until_dt.date(), time(23, 59, 59)
                            )
                        if next_dt > until_dt:
                            in_range = False
                    except (ValueError, TypeError):
                        # 无法解析时退回按日期比较
                        if next_due[:10] > repeat_until[:10]:
                            in_range = False
                if in_range:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, medication_id, done,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            row["title"], row["note"], next_due,
                            row["session_id"], row["profile"],
                            row["advance_days"], repeat_days,
                            repeat_until, reminder_id,
                            row["medication_id"], now, now,
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
                        "medication_id": row["medication_id"],
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
                    UNION
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
                # 排除被恢复的提醒本身：异常数据里 repeats_from 可能指向自己
                self._conn.execute(
                    f"DELETE FROM reminders WHERE done = 0 "
                    f"AND id IN ({placeholders}) AND id != ?",
                    [*ids, reminder_id],
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
                       repeat_days, repeat_until, repeats_from, medication_id,
                       done, created_at, updated_at
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
                "SELECT due_at, done FROM reminders WHERE id = ?",
                (int(reminder_id),),
            ).fetchone()
            if row is None or row["done"]:
                # 已完成的提醒不再推迟：否则会带着新日期继续计入统计
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
                       repeat_days, repeat_until, repeats_from, medication_id,
                       done, created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (int(reminder_id),),
            ).fetchone()
        return {**dict(updated), "done": bool(updated["done"])}

    def count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM sessions").fetchone()
        return int(row["n"]) if row else 0

    def purge_sessions_before(self, cutoff: str) -> Dict[str, Any]:
        """删除 cutoff（ISO 时间）之前更新的会话及其消息，返回删除数量。"""
        cutoff = str(cutoff or "").strip()[:40]
        empty = {
            "sessions": 0,
            "messages": 0,
            "session_ids": [],
            "session_profiles": {},
        }
        if not cutoff:
            return empty

        with self._lock, self._conn:
            # 统一交给 SQLite 的 datetime() 解析，兼容 "T" 与空格分隔、
            # 时区后缀和带时分秒/仅日期的历史数据；无效时间不参与删除
            condition = (
                "datetime(updated_at) IS NOT NULL "
                "AND datetime(updated_at) < datetime(?)"
            )
            rows = self._conn.execute(
                f"SELECT id, profile FROM sessions WHERE {condition}", (cutoff,)
            ).fetchall()
            session_ids = [row["id"] for row in rows]
            session_profiles = {
                row["id"]: row["profile"] or "" for row in rows
            }
            messages_deleted = self._conn.execute(
                f"DELETE FROM messages WHERE session_id IN "
                f"(SELECT id FROM sessions WHERE {condition})",
                (cutoff,),
            ).rowcount
            sessions_deleted = self._conn.execute(
                f"DELETE FROM sessions WHERE {condition}", (cutoff,)
            ).rowcount
            # 同时清理历史遗留的孤儿消息（其会话行早已不存在）
            messages_deleted += self._conn.execute(
                """
                DELETE FROM messages
                 WHERE session_id NOT IN (SELECT id FROM sessions)
                """
            ).rowcount
            # 进行中的会诊不能在清理后把这些会话写回
            for session_id in session_ids:
                self._deleted_sessions[session_id] = None
            while len(self._deleted_sessions) > self._max_deleted_sessions:
                self._deleted_sessions.pop(
                    next(iter(self._deleted_sessions))
                )
        self._checkpoint_wal()
        return {
            "sessions": int(sessions_deleted),
            "messages": int(messages_deleted),
            "session_ids": session_ids,
            "session_profiles": session_profiles,
        }

    def close(self) -> None:
        with self._lock:
            self._checkpoint_wal()
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
