"""会话持久化存储（SQLite）。

借鉴 MiQi 的会话管理思路：服务重启后仍能列出历史会诊、恢复多轮上下文。
只用 Python 标准库 sqlite3，不引入额外依赖；数据库默认位于 data/sessions.db。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import threading
import time as _time
from contextlib import contextmanager
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from loguru import logger

from core.serialization import sanitize_json
from core.lab_values import lab_critical_alerts
from core.fs_permissions import restrict_permissions
from core.backup import (
    MAX_BACKUP_FILE_BYTES,
    MAX_BACKUP_MESSAGE_CHARS,
    MAX_BACKUP_SESSION_MESSAGES,
    MAX_BACKUP_TOTAL_CHARS,
)

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent / "data" / "sessions.db"

#: SQLite 参数绑定只接受有符号 64 位整数，超出会抛 OverflowError
MAX_SQLITE_INT = 2**63 - 1

#: 搜索词上限：SQLite LIKE 模式默认上限约 5 万字符，取 2000 留足余量；
#: 正常查询不会被截断，避免 SQL 过滤与 Python 侧摘要匹配语义不一致
MAX_SEARCH_QUERY_LENGTH = 2000


#: 用于区分「未提供字段」与显式空值
_UNSET = object()


def _bounded_offset(offset: Any) -> int:
    """把分页偏移限制在 SQLite 可绑定范围内。"""
    return max(0, min(int(offset or 0), MAX_SQLITE_INT))


def _date_range_conditions(
    column: str, start: Any, end: Any
) -> Tuple[List[str], List[Any]]:
    """按自然日闭区间生成日期过滤条件，供时间线/月历下推到 SQL。

    直接比较时间列可命中 (profile, time) 索引；结束日换算成次日再做
    小于比较，避免对列使用 substr 导致索引失效。
    """
    conditions: List[str] = []
    params: List[Any] = []
    start_day = str(start or "").strip()[:10]
    if start_day:
        conditions.append(f"{column} >= ?")
        params.append(start_day)
    end_day = str(end or "").strip()[:10]
    if end_day:
        try:
            next_day = (
                datetime.strptime(end_day, "%Y-%m-%d")
                + timedelta(days=1)
            ).strftime("%Y-%m-%d")
        except (ValueError, OverflowError):
            # 非法日期，或 9999-12-31 加一天越过 datetime.max
            conditions.append(f"substr({column}, 1, 10) <= ?")
            params.append(end_day)
        else:
            conditions.append(f"{column} < ?")
            params.append(next_day)
    return conditions, params


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
#: 疫苗接种记录的字段上限
MAX_VACCINE_NAME_LENGTH = 80
MAX_VACCINATION_NOTE_LENGTH = 300
MAX_VACCINE_DOSE = 20
#: 就诊预约
MAX_APPOINTMENT_TITLE_LENGTH = 120
MAX_APPOINTMENT_TEXT_LENGTH = 60
MAX_APPOINTMENT_NOTE_LENGTH = 300
APPOINTMENT_STATUSES = ("upcoming", "completed", "cancelled")
#: 化验结果
MAX_LAB_ITEM_LENGTH = 80
MAX_LAB_PANEL_LENGTH = 60
MAX_LAB_UNIT_LENGTH = 20
MAX_LAB_NAME_LENGTH = 80
MAX_LAB_NOTE_LENGTH = 300
MAX_LAB_VALUE = 1000000000.0
#: 过敏史
MAX_ALLERGY_SUBSTANCE_LENGTH = 80
MAX_ALLERGY_REACTION_LENGTH = 120
MAX_ALLERGY_NOTE_LENGTH = 300
ALLERGY_SEVERITIES = ("mild", "moderate", "severe")
ALLERGY_KINDS = ("drug", "food", "environment", "other")
#: 复查计划
MAX_CARE_TITLE_LENGTH = 120
MAX_CARE_NOTE_LENGTH = 300
MAX_CARE_INTERVAL_DAYS = 3650
#: 就诊记录
MAX_VISIT_TEXT_LENGTH = 300
MAX_VISIT_ADVICE_LENGTH = 1000
MAX_VISIT_FACILITY_LENGTH = 80
#: 慢病清单
MAX_CONDITION_NAME_LENGTH = 120
MAX_CONDITION_ICD10_LENGTH = 20
MAX_CONDITION_NOTE_LENGTH = 300
CONDITION_STATUSES = ("active", "controlled", "resolved")
CONDITION_SEVERITIES = ("mild", "moderate", "severe")
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
    "blood_type",
    "emergency_contacts",
    "duration",
    "history",
    "medications",
    "allergies",
)

#: 备份导入时每类数据的条数上限。与 export_data 的导出口径保持一致，
#: 避免应用自己导出的备份因超过导入上限而无法恢复
#: 各表参与身份配对的「内容列」：导入时用于识别候选是否其实是
#: 备份里另一条记录（软字段被编辑后仍能区分同名同秒的孪生行）
IMPORT_CONTENT_COLUMNS = {
    "health_metrics": (
        "measured_at", "value1", "IFNULL(value2, -1)", "context", "note",
    ),
    "symptoms": ("symptom", "recorded_at"),
    "medications": ("name", "dose", "frequency", "started_at"),
    "vaccinations": ("vaccine_name", "dose_number", "vaccinated_at"),
    "appointments": (
        "title", "scheduled_at", "department", "doctor", "location",
    ),
    "lab_results": (
        "panel", "item", "tested_at", "value", "unit",
        "IFNULL(ref_low, -1e308)", "IFNULL(ref_high, -1e308)",
        "lab_name", "note",
    ),
    "allergies": ("substance", "kind", "noted_at"),
    "care_items": ("title", "interval_days"),
    "visit_records": (
        "visit_date", "diagnosis", "department", "doctor",
    ),
    "conditions": ("name", "diagnosed_at"),
    "reminders": ("due_at",),
}

IMPORT_LIST_LIMITS = {
    "profiles": 100000,
    "health_metrics": 100000,
    "metric_targets": 100000,
    "symptoms": 100000,
    "medications": 100000,
    "medication_logs": 100000,
    "medication_history": 100000,
    "vaccinations": 100000,
    "appointments": 100000,
    "lab_results": 100000,
    "allergies": 100000,
    "care_items": 100000,
    "visit_records": 100000,
    "conditions": 100000,
    "reminders": 100000,
    "audit_log": 100000,
    "usage_events": 100000,
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
            items = []
            for item in value:
                if isinstance(item, bool) or item is None:
                    continue
                if isinstance(item, (str, int, float)):
                    text = str(item).strip()
                    if text:
                        items.append(text[:MAX_PROFILE_FIELD_LENGTH])
            text = "；".join(items)
        elif isinstance(value, dict):
            pairs = []
            for item_key, item_value in value.items():
                if isinstance(item_value, (dict, list, tuple, bool)):
                    continue
                if item_value is None:
                    continue
                if isinstance(item_value, (str, int, float)):
                    text = str(item_value).strip()
                    if text:
                        pairs.append(
                            f"{item_key}={text[:MAX_PROFILE_FIELD_LENGTH]}"
                        )
            text = "；".join(pairs)
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
    "sleep": ("睡眠时长", "小时", (0, 24)),
    "steps": ("步数", "步", (0, 100000)),
    "water": ("饮水量", "ml", (0, 20000)),
    "phq9": ("PHQ-9 评分", "分", (0, 27)),
    "gad7": ("GAD-7 评分", "分", (0, 21)),
    "cat": ("CAT 评分", "分", (0, 40)),
    "ess": ("ESS 评分", "分", (0, 24)),
    "auditc": ("AUDIT-C 评分", "分", (0, 12)),
    "mmrc": ("mMRC 分级", "级", (0, 4)),
}
MAX_METRIC_NOTE_LENGTH = 300

#: 指标的测量情境（血压晨起/睡前，血糖空腹/餐后等）
METRIC_CONTEXTS = {
    "bp": ("morning", "evening"),
    "glucose": ("fasting", "postprandial", "bedtime", "random"),
}
METRIC_CONTEXT_LABELS = {
    "morning": "晨起",
    "evening": "睡前",
    "fasting": "空腹",
    "postprandial": "餐后",
    "bedtime": "睡前",
    "random": "随机",
}


def metric_context_label(metric: str, context: Any) -> str:
    """返回情境中文名；未设置或不支持的类型返回空串。"""
    key = str(context or "").strip()
    if not key or key not in METRIC_CONTEXTS.get(str(metric or ""), ()):
        return ""
    return METRIC_CONTEXT_LABELS.get(key, "")

#: 血压分级（中国高血压防治指南 2018 成人标准），供统计分布使用。
BP_GRADE_KEYS = ("normal", "high_normal", "grade1", "grade2", "grade3", "low")


def critical_metric_alerts(
    metric: str, value1: Any, value2: Any = None
) -> List[Dict[str, str]]:
    """返回指标达到危急值时的提示；无危急值时返回空列表。"""
    metric = str(metric or "")
    try:
        first = float(value1)
    except (TypeError, ValueError, OverflowError):
        return []
    second: Optional[float] = None
    if value2 is not None and value2 != "":
        try:
            second = float(value2)
        except (TypeError, ValueError, OverflowError):
            second = None

    alerts: List[Dict[str, str]] = []
    if metric == "bp":
        systolic = first
        diastolic = second if second is not None else 0.0
        display = (
            f"{systolic:g}/{diastolic:g}"
            if second is not None else f"{systolic:g}"
        )
        if systolic >= 180 or (second is not None and diastolic >= 120):
            alerts.append({
                "level": "critical",
                "message": f"血压 {display} mmHg 达到危急值，请立即就医或拨打 120。",
            })
        elif systolic < 90:
            # 低血压危急值只按收缩压判定；150/55 这类单纯舒张压偏低
            # 不应被描述为整体血压偏低并升级为最高优先级警报
            alerts.append({
                "level": "critical",
                "message": f"血压 {display} mmHg 偏低，如伴头晕、乏力请及时就医。",
            })
    elif metric == "glucose":
        if first <= 3.9:
            alerts.append({
                "level": "critical",
                "message": f"血糖 {first:g} mmol/L 偏低，警惕低血糖，请及时处理。",
            })
        elif first >= 16.7:
            alerts.append({
                "level": "critical",
                "message": f"血糖 {first:g} mmol/L 明显升高，警惕高血糖急症，请及时就医。",
            })
    elif metric == "temperature":
        if first >= 39.0:
            alerts.append({
                "level": "critical",
                "message": f"体温 {first:g} °C，高热请及时就医。",
            })
        elif first <= 35.0:
            alerts.append({
                "level": "critical",
                "message": f"体温 {first:g} °C 偏低，警惕低体温，请及时就医。",
            })
    elif metric == "heart_rate":
        if first > 120:
            alerts.append({
                "level": "critical",
                "message": f"心率 {first:g} 次/分偏快，请及时就医。",
            })
        elif first < 50:
            alerts.append({
                "level": "critical",
                "message": f"心率 {first:g} 次/分偏慢，请及时就医。",
            })
    elif metric == "spo2":
        if first < 90:
            alerts.append({
                "level": "critical",
                "message": f"血氧饱和度 {first:g}%，请立即就医或拨打 120。",
            })
    return alerts


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
LOWER_BOUND_TARGET_METRICS = {"spo2", "sleep", "steps", "water"}

#: 按自然日累计的指标：目标是每日总量，单条记录只是当日的一部分
DAILY_ACCUMULATING_METRICS = {"steps", "water"}


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


def _normalize_datetime_text(value: Any, limit: int = 40) -> str:
    """把 Excel/WPS 常见的日期写法规整为 ISO 风格文本。

    只做格式规整，不判断日期是否真实存在；后续 fromisoformat 负责校验，
    因此 2026/2/30 之类仍会被拒绝。
    """
    text = str(value or "").strip()[:limit]
    match = re.match(
        r"^(\d{4})[-/.年](\d{1,2})[-/.月](\d{1,2})日?(.*)$", text
    )
    if not match:
        return text
    year, month, day, rest = match.groups()
    rest = rest.strip()
    if rest[:1] in ("T", "t"):
        rest = rest[1:].strip()
    if rest:
        clock = re.match(
            r"^(\d{1,2}):(\d{1,2})(?::(\d{1,2})(\.\d+)?)?(.*)$", rest
        )
        if clock:
            hour, minute, second, fraction, suffix = clock.groups()
            rest = f"T{int(hour):02d}:{int(minute):02d}"
            if second is not None:
                rest += f":{int(second):02d}{fraction or ''}"
            rest += suffix
        else:
            # 允许空格分隔日期与时间（Excel 另存 CSV 的常见格式）
            rest = "T" + rest
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}{rest}"


def _to_local_naive(parsed: datetime) -> Optional[datetime]:
    """带时区时间转本地 naive；平台限制或年份越界时返回 None。

    Windows 的 C localtime 不支持 1970 年前的负时间戳（astimezone
    抛 OSError），正时区把 9999 年换算到 10000 年则抛 OverflowError。
    这类输入应作为无效时间返回 400，而不是穿透成 500。
    """
    if parsed.year > 9998:
        # 与提醒路径的 year>9998 守卫一致：9999 年换算可能越界
        return None
    try:
        return parsed.astimezone().replace(tzinfo=None)
    except (OSError, OverflowError, ValueError):
        pass
    # 回退为纯 Python 偏移换算：保留历史日期可用性，不依赖平台 localtime
    try:
        own_offset = parsed.utcoffset() or timedelta(0)
        local_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
        local = parsed.replace(tzinfo=None) - own_offset + local_offset
    except (OverflowError, ValueError):
        return None
    if not 1 <= local.year <= 9999:
        return None
    return local


def _canonical_local_time(value: Any) -> Optional[str]:
    """把合法 ISO 时间规范化为本地 T 分隔格式；无效返回 None。"""
    text = _normalize_datetime_text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        local = _to_local_naive(parsed)
        if local is None:
            return None
        return local.isoformat(timespec="seconds")
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
    context: str = "",
) -> Optional[Dict[str, Any]]:
    """校验一条健康指标记录；无效时返回 None。"""
    metric = str(metric or "").strip()
    if metric not in METRIC_TYPES:
        return None
    context_key = str(context or "").strip().lower()
    allowed_contexts = METRIC_CONTEXTS.get(metric, ())
    if not allowed_contexts:
        context_key = ""
    elif context_key and context_key not in allowed_contexts:
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
        "context": context_key,
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


def _profile_deletion_key(profile: Any) -> str:
    """删除墓碑只保存档案名的哈希，已删除姓名不能残留在数据库里。"""
    name = _normalize_profile_name(profile)
    if not name:
        return ""
    return hashlib.sha256(
        ("medix-profile-deletion:" + name).encode("utf-8")
    ).hexdigest()


def _import_payload_problems(
    data: Dict[str, Any],
    max_sessions: int = 100000,
    skip_session_ids: Optional[set] = None,
) -> List[str]:
    """导入上限预检：返回全部问题，供恢复预检与导入共用。

    预检通过但恢复时才 400 会让用户空等一次完整导入；两边共用同一份
    规则，预检失败时直接给出可读原因。
    """
    problems: List[str] = []
    raw_sessions = data.get("sessions")
    if isinstance(raw_sessions, list) and len(raw_sessions) > max_sessions:
        problems.append(f"一次最多导入 {max_sessions} 个会话。")
    for key, label in (
        ("profiles", "档案"),
        ("health_metrics", "健康指标"),
        ("metric_targets", "指标目标"),
        ("symptoms", "症状记录"),
        ("medications", "用药"),
        ("medication_logs", "用药打卡"),
        ("medication_history", "用药变更"),
        ("vaccinations", "疫苗接种"),
        ("appointments", "就诊预约"),
        ("lab_results", "化验结果"),
        ("allergies", "过敏史"),
        ("care_items", "复查计划"),
        ("visit_records", "就诊记录"),
        ("conditions", "慢病清单"),
        ("reminders", "提醒"),
        ("audit_log", "审计记录"),
        ("usage_events", "用量流水"),
    ):
        items = data.get(key)
        cap = IMPORT_LIST_LIMITS[key]
        if isinstance(items, list) and len(items) > cap:
            problems.append(f"一次最多导入 {cap} 条{label}。")
    if not isinstance(raw_sessions, list):
        return problems
    total_message_chars = 0
    for raw in raw_sessions:
        if not isinstance(raw, dict):
            continue
        messages = raw.get("messages")
        if not isinstance(messages, list):
            continue
        sid = str(raw.get("id") or "").strip()[:120]
        skip_count = bool(skip_session_ids and sid in skip_session_ids)
        if (
            len(messages) > MAX_BACKUP_SESSION_MESSAGES
            and not skip_count
        ):
            # 已存在的会话整段跳过（旧行为），不因条数超限中断导入；
            # 消息长度/总量仍按全量校验
            problems.append(
                f"单个会话最多导入 {MAX_BACKUP_SESSION_MESSAGES} 条消息。"
            )
        for message in messages:
            if not isinstance(message, dict):
                continue
            content = message.get("content")
            if not isinstance(content, str):
                continue
            if len(content) > MAX_IMPORT_MESSAGE_CHARS:
                problems.append(
                    f"单条消息最多 {MAX_IMPORT_MESSAGE_CHARS} 个字符。"
                )
                continue
            metadata = message.get("metadata")
            if isinstance(metadata, (dict, list)):
                meta_length = len(
                    json.dumps(metadata, ensure_ascii=False)
                )
            else:
                meta_length = len(str(metadata or ""))
            if meta_length > MAX_IMPORT_MESSAGE_CHARS:
                problems.append("单条消息的元数据过大。")
                continue
            total_message_chars += len(content) + meta_length // 4
            if total_message_chars > MAX_IMPORT_TOTAL_CHARS:
                problems.append("备份中的消息总量过大。")
                return problems
    return problems


@contextmanager
def _db_init_lock(db_path: Path):
    """跨进程串行化建表/迁移。

    多进程同时首次打开同一库时，PRAGMA journal_mode=WAL 会抛
    database is locked，check-then-ALTER 迁移会抛 duplicate column name。
    """
    lock_path = Path(db_path).with_name(Path(db_path).name + ".init.lock")
    handle = None
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(lock_path, "a+b")
    except OSError:
        yield
        return
    locked = False
    try:
        if os.name == "nt":
            import msvcrt

            deadline = _time.monotonic() + 30
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                    locked = True
                    break
                except OSError:
                    if _time.monotonic() > deadline:
                        break
                    _time.sleep(0.05)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            locked = True
        yield
    finally:
        if locked:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
        handle.close()


def _import_probe_spec(raw: Dict[str, Any], columns, extract):
    """构造「身份 + 内容」精确配对的查询条件；无法判断时返回 None。

    只用于导入排序：让能精确配对的行先处理。第一列是 profile 时同时
    返回去掉 profile 的「改挂证据」条件（主键 + 创建时间 + 内容），
    让被 PATCH 改挂到另一档案的存活行先于内容配对的已删除行处理。
    任何不一致都退化为旧顺序，不会影响实际的插入/判重逻辑。
    """
    created = str(raw.get("created_at") or "").strip()[:40]
    if not created:
        return None
    try:
        values = extract(raw, created)
    except Exception:
        return None
    if values is None:
        return None
    where = " AND ".join(f"{column} = ?" for column in columns)
    moved_where = None
    moved_params: Tuple[Any, ...] = ()
    if len(columns) > 1 and columns[0] == "profile" and len(values) > 1:
        moved_where = " AND ".join(
            f"{column} = ?" for column in columns[1:]
        )
        moved_params = tuple(values[1:])
    return where, tuple(values), moved_where, moved_params


MAX_MEDICATION_NAME_LENGTH = 120
MAX_MEDICATION_TEXT_LENGTH = 60
MAX_MEDICATION_STOCK = 1000000.0
MAX_MEDICATION_UNIT_LENGTH = 10
MAX_MEDICATION_STOP_REASON_LENGTH = 200
#: 就医资料附件：允许的类型、单文件上限与展示名称长度
ATTACHMENT_KINDS = {
    "visit": "就诊资料",
    "lab": "化验单",
    "other": "其他",
}
ALLOWED_ATTACHMENT_MIMES = {
    "image/jpeg", "image/png", "image/webp", "application/pdf",
}
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024
MAX_ATTACHMENT_NAME_LENGTH = 120
#: 未服原因（键 → 中文名）
MEDICATION_SKIP_REASONS = {
    "forget": "忘记",
    "side_effect": "副作用",
    "worry_side_effect": "担心副作用",
    "cost": "费用",
    "inconvenient": "不方便",
    "other": "其他",
}


def _normalize_medication_quantity(
    value: Any, *, allow_none: bool
) -> Optional[float]:
    """校验库存/阈值：非负有限数，最多两位小数；空值按调用方决定。"""
    if value is None or (
        isinstance(value, str) and not value.strip()
    ):
        return None if allow_none else 0.0
    if isinstance(value, bool):
        raise ValueError("库存应为非负数字")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("库存应为非负数字")
    if (
        not math.isfinite(number)
        or number < 0
        or number > MAX_MEDICATION_STOCK
    ):
        raise ValueError("库存应为 0–1000000 的数字")
    return round(number, 2)


def _normalize_medication_expiry(value: Any) -> str:
    """校验药品有效期：空值表示未登记，否则规范为 YYYY-MM-DD。"""
    text = str(value or "").strip()[:40]
    if not text:
        return ""
    canonical = _canonical_local_time(text)
    if canonical is None:
        raise ValueError("有效期格式应为 YYYY-MM-DD 或 ISO 时间")
    return canonical[:10]


def _medication_stock_fields(
    row: Dict[str, Any], today: str
) -> Dict[str, Any]:
    """补充库存与效期告警字段，供列表、首页与家庭总览复用。"""
    stock = row.get("stock")
    threshold = float(row.get("low_stock_threshold") or 0)
    expiry = str(row.get("expiry_date") or "")[:10]
    days_to_expiry: Optional[int] = None
    if expiry:
        try:
            days_to_expiry = (
                datetime.strptime(expiry, "%Y-%m-%d").date()
                - datetime.strptime(today, "%Y-%m-%d").date()
            ).days
        except ValueError:
            days_to_expiry = None
    return {
        "stock": None if stock is None else round(float(stock), 2),
        "stock_unit": str(row.get("stock_unit") or ""),
        "low_stock_threshold": threshold,
        "expiry_date": expiry,
        "low_stock": stock is not None and float(stock) <= threshold,
        "out_of_stock": stock is not None and float(stock) <= 0,
        "days_to_expiry": days_to_expiry,
        "expired": days_to_expiry is not None and days_to_expiry < 0,
        "expiring_soon": (
            days_to_expiry is not None and 0 <= days_to_expiry <= 30
        ),
    }


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


#: 剂量中的数字+剂型，用于按实际服用量扣减库存
_DOSE_QUANTITY_PATTERN = re.compile(
    r"^(\d+(?:\.\d+)?)\s*(片|粒|袋|支|丸|包|毫升|ml|滴|喷)",
    re.IGNORECASE,
)
_DOSE_UNIT_ALIASES = {"毫升": "ml"}


def medication_dose_units(dose: Any, stock_unit: Any) -> float:
    """解析一次服用占用的库存单位数；不确定时保守按 1 计。"""
    match = _DOSE_QUANTITY_PATTERN.match(str(dose or "").strip())
    if not match:
        return 1.0
    try:
        quantity = float(match.group(1))
    except (TypeError, ValueError, OverflowError):
        return 1.0
    # 半片/四分之一片也是合法剂量，不能回退成整片扣库存
    if not (0 < quantity <= 100):
        return 1.0
    dose_unit = match.group(2).lower()
    dose_unit = _DOSE_UNIT_ALIASES.get(dose_unit, dose_unit)
    stock_key = str(stock_unit or "").strip().lower()
    stock_key = _DOSE_UNIT_ALIASES.get(stock_key, stock_key)
    if stock_key and dose_unit != stock_key:
        # 例如剂量 5 mg 而库存单位是「片」，换算关系未知
        return 1.0
    return quantity


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
    every_hours = re.search(
        r"每\s*(?:隔)?\s*([一二两三四五六七八九十\d]+)\s*(?:个)?\s*小时",
        text,
    )
    if every_hours:
        count = _frequency_count(every_hours.group(1))
        if count and 1 <= count <= 24:
            return 24 / count
    weekday_list = re.search(
        r"每(?:周|星期|礼拜)\s*"
        # 「每周三次」是频次不是星期三：星期列表后不能紧跟「次」
        r"([一二三四五六日天](?:[\s、，,和及]+[一二三四五六日天])*)"
        r"(?!\s*次)",
        text,
    )
    if weekday_list:
        weekdays = re.findall(r"[一二三四五六日天]", weekday_list.group(1))
        if weekdays:
            return len(weekdays) / 7
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
    per_day = re.search(
        r"([一二两三四五六七八九十\d]+)\s*次\s*/\s*(?:日|天)", text
    )
    if per_day:
        count = _frequency_count(per_day.group(1))
        return float(count) if count else None
    daily = re.search(
        r"(?:每(?:日|天|晚|早)|一日|一天|1日|1天)"
        r"\s*([一二两三四五六七八九十\d]+)?\s*次",
        text,
    )
    if daily:
        count = _frequency_count(daily.group(1) or "")
        return float(count) if count else None
    if re.search(r"每日|每天|每晚|每早|一日|睡前|晨起", text):
        return 1.0
    return None


def expected_doses_for_period(
    frequency: str,
    created_at: str,
    now: datetime,
    days: int = 7,
    stopped_at: str = "",
) -> Optional[int]:
    """按频次、用药开始与停止时间、统计天数估算应服次数。"""
    per_day = parse_frequency_doses_per_day(frequency)
    if not per_day:
        return None
    days = max(1, min(int(days or 7), 3650))
    try:
        created = datetime.fromisoformat(str(created_at or "")[:19]).date()
    except ValueError:
        created = now.date()
    start = max(created, (now - timedelta(days=days - 1)).date())
    end_date = now.date()
    stop_text = str(stopped_at or "").strip()[:19]
    if stop_text:
        try:
            stop_date = datetime.fromisoformat(stop_text).date()
        except ValueError:
            stop_date = None
        if stop_date is not None and stop_date < end_date:
            # 停药后不再产生应服次数
            end_date = stop_date
    day_count = (end_date - start).days + 1
    if day_count <= 0:
        return None
    return round(per_day * day_count)


def _expected_week_doses(
    frequency: str, created_at: str, now: datetime,
    stopped_at: str = "",
) -> Optional[int]:
    """按频次、开始与停止时间估算近 7 天应服次数。"""
    return expected_doses_for_period(
        frequency, created_at, now, 7, stopped_at
    )


def _normalize_medication_date(value: Any, label: str) -> str:
    text = str(value or "").strip()[:40]
    if not text:
        return ""
    canonical = _canonical_local_time(text)
    if canonical is None:
        raise ValueError(f"{label}格式应为 YYYY-MM-DD 或 ISO 时间")
    if _is_future_timestamp(canonical):
        raise ValueError(f"{label}不能晚于当前时间")
    return canonical[:10]


def _normalize_medication(
    profile: str,
    name: str,
    dose: str,
    frequency: str,
    note: str,
    active: Any = True,
    stock: Any = None,
    stock_unit: str = "",
    low_stock_threshold: Any = 0,
    expiry_date: str = "",
    started_at: str = "",
    stopped_at: str = "",
    stop_reason: str = "",
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
    try:
        normalized_stock = _normalize_medication_quantity(
            stock, allow_none=True
        )
        normalized_threshold = _normalize_medication_quantity(
            low_stock_threshold, allow_none=False
        )
        normalized_expiry = _normalize_medication_expiry(expiry_date)
        normalized_started = _normalize_medication_date(
            started_at, "开始日期"
        )
        normalized_stopped = _normalize_medication_date(
            stopped_at, "停药日期"
        )
        if (
            normalized_started
            and normalized_stopped
            and normalized_stopped < normalized_started
        ):
            raise ValueError("停药日期不能早于开始日期")
    except ValueError:
        return None
    return {
        "profile": _normalize_profile_name(profile),
        "name": name,
        "dose": " ".join(str(dose or "").split())[:MAX_MEDICATION_TEXT_LENGTH],
        "frequency": " ".join(
            str(frequency or "").split()
        )[:MAX_MEDICATION_TEXT_LENGTH],
        "note": " ".join(str(note or "").split())[:MAX_METRIC_NOTE_LENGTH],
        "active": bool(active_flag),
        "stock": normalized_stock,
        "stock_unit": " ".join(str(stock_unit or "").split())[
            :MAX_MEDICATION_UNIT_LENGTH
        ],
        "low_stock_threshold": normalized_threshold or 0.0,
        "expiry_date": normalized_expiry,
        "started_at": normalized_started,
        "stopped_at": normalized_stopped,
        "stop_reason": " ".join(str(stop_reason or "").split())[
            :MAX_MEDICATION_STOP_REASON_LENGTH
        ],
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


def _normalize_vaccination(
    profile: str,
    vaccine_name: str,
    dose_number: Any,
    vaccinated_at: str,
    next_due: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条疫苗接种记录；非法时抛出 ValueError。"""
    name = " ".join(str(vaccine_name or "").split())[:MAX_VACCINE_NAME_LENGTH]
    if not name:
        raise ValueError("疫苗名称不能为空")
    if isinstance(dose_number, bool):
        raise ValueError("剂次应为 1–20 的整数")
    try:
        dose = int(dose_number)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("剂次应为 1–20 的整数")
    if dose < 1 or dose > MAX_VACCINE_DOSE:
        raise ValueError("剂次应为 1–20 的整数")

    vaccinated = str(vaccinated_at or "").strip()[:40]
    if not vaccinated:
        vaccinated = datetime.now().isoformat(timespec="minutes")
    else:
        date_only = len(vaccinated) == 10
        vaccinated = _canonical_local_time(vaccinated)
        if vaccinated is None:
            raise ValueError("接种日期格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(vaccinated):
            raise ValueError("接种日期不能晚于当前时间")
        if date_only:
            vaccinated = vaccinated[:10]

    due = ""
    raw_due = str(next_due or "").strip()[:40]
    if raw_due:
        canonical_due = _canonical_local_time(raw_due)
        if canonical_due is None:
            raise ValueError("下一剂日期格式应为 YYYY-MM-DD 或 ISO 时间")
        due = canonical_due[:10]
        if due < vaccinated[:10]:
            raise ValueError("下一剂日期不能早于接种日期")

    return {
        "profile": _normalize_profile_name(profile),
        "vaccine_name": name,
        "dose_number": dose,
        "vaccinated_at": vaccinated,
        "next_due": due,
        "note": " ".join(str(note or "").split())[
            :MAX_VACCINATION_NOTE_LENGTH
        ],
    }


def _normalize_appointment(
    profile: str,
    title: str,
    department: str,
    doctor: str,
    location: str,
    scheduled_at: str,
    status: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条就诊预约；非法时抛出 ValueError。"""
    name = " ".join(str(title or "").split())[:MAX_APPOINTMENT_TITLE_LENGTH]
    if not name:
        raise ValueError("就诊主题不能为空")
    scheduled = str(scheduled_at or "").strip()[:40]
    if not scheduled:
        raise ValueError("请填写就诊时间")
    scheduled = _canonical_local_time(scheduled)
    if scheduled is None:
        raise ValueError("就诊时间格式应为 YYYY-MM-DD 或 ISO 时间")
    status_key = str(status or "upcoming").strip().lower()
    if status_key not in APPOINTMENT_STATUSES:
        raise ValueError("就诊状态应为 upcoming/completed/cancelled")
    return {
        "profile": _normalize_profile_name(profile),
        "title": name,
        "department": " ".join(str(department or "").split())[
            :MAX_APPOINTMENT_TEXT_LENGTH
        ],
        "doctor": " ".join(str(doctor or "").split())[
            :MAX_APPOINTMENT_TEXT_LENGTH
        ],
        "location": " ".join(str(location or "").split())[
            :MAX_APPOINTMENT_TEXT_LENGTH
        ],
        "scheduled_at": scheduled,
        "status": status_key,
        "note": " ".join(str(note or "").split())[
            :MAX_APPOINTMENT_NOTE_LENGTH
        ],
    }


def _appointment_view_fields(
    row: Dict[str, Any], today: str
) -> Dict[str, Any]:
    """补充倒计时与是否临近字段，供列表、首页与家庭总览复用。"""
    scheduled = str(row.get("scheduled_at") or "")[:16]
    status = str(row.get("status") or "upcoming")
    days_until: Optional[int] = None
    if scheduled:
        try:
            days_until = (
                datetime.strptime(scheduled[:10], "%Y-%m-%d").date()
                - datetime.strptime(today, "%Y-%m-%d").date()
            ).days
        except ValueError:
            days_until = None
    return {
        "days_until": days_until,
        "is_today": days_until == 0,
        "is_past": days_until is not None and days_until < 0,
        "soon": (
            status == "upcoming"
            and days_until is not None
            and 0 <= days_until <= 7
        ),
    }


def _normalize_lab_bound(value: Any, label: str) -> Optional[float]:
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    if isinstance(value, bool):
        raise ValueError(f"{label}应为数字")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label}应为数字")
    if not math.isfinite(number) or abs(number) > MAX_LAB_VALUE:
        raise ValueError(f"{label}超出支持范围")
    return round(number, 4)


def _normalize_lab_result(
    profile: str,
    panel: str,
    item: str,
    value: Any,
    unit: str,
    ref_low: Any,
    ref_high: Any,
    tested_at: str,
    lab_name: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条化验结果；非法时抛出 ValueError。"""
    item_name = " ".join(str(item or "").split())[:MAX_LAB_ITEM_LENGTH]
    if not item_name:
        raise ValueError("化验项目不能为空")
    if isinstance(value, bool):
        raise ValueError("化验数值应为数字")
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("化验数值应为数字")
    if not math.isfinite(number) or abs(number) > MAX_LAB_VALUE:
        raise ValueError("化验数值超出支持范围")
    low = _normalize_lab_bound(ref_low, "参考下限")
    high = _normalize_lab_bound(ref_high, "参考上限")
    if low is not None and high is not None and low > high:
        raise ValueError("参考下限不能高于参考上限")
    tested = str(tested_at or "").strip()[:40]
    if not tested:
        tested = datetime.now().isoformat(timespec="minutes")
    else:
        tested = _canonical_local_time(tested)
        if tested is None:
            raise ValueError("检测时间格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(tested):
            raise ValueError("检测时间不能晚于当前时间")
    return {
        "profile": _normalize_profile_name(profile),
        "panel": " ".join(str(panel or "").split())[:MAX_LAB_PANEL_LENGTH],
        "item": item_name,
        "value": round(number, 6),
        "unit": " ".join(str(unit or "").split())[:MAX_LAB_UNIT_LENGTH],
        "ref_low": low,
        "ref_high": high,
        "tested_at": tested,
        "lab_name": " ".join(str(lab_name or "").split())[
            :MAX_LAB_NAME_LENGTH
        ],
        "note": " ".join(str(note or "").split())[:MAX_LAB_NOTE_LENGTH],
    }


def _lab_result_view_fields(row: Dict[str, Any]) -> Dict[str, Any]:
    """补充参考范围文本、偏高/偏低与危急值标记。"""
    value = float(row.get("value") or 0)
    low = row.get("ref_low")
    high = row.get("ref_high")
    low_value = None if low is None else float(low)
    high_value = None if high is None else float(high)
    abnormal_low = low_value is not None and value < low_value
    abnormal_high = high_value is not None and value > high_value
    if low_value is not None and high_value is not None:
        ref_text = f"{low_value:g}–{high_value:g}"
    elif low_value is not None:
        ref_text = f"≥{low_value:g}"
    elif high_value is not None:
        ref_text = f"≤{high_value:g}"
    else:
        ref_text = ""
    critical_alerts = lab_critical_alerts(
        row.get("item"), value, row.get("unit"), row.get("tested_at"),
        row.get("panel"),
    )
    return {
        "ref_low": low_value,
        "ref_high": high_value,
        "ref_text": ref_text,
        "abnormal_low": abnormal_low,
        "abnormal_high": abnormal_high,
        "abnormal": abnormal_low or abnormal_high,
        "critical": bool(critical_alerts),
        "critical_alerts": critical_alerts,
    }


def _normalize_allergy(
    profile: str,
    substance: str,
    reaction: str,
    severity: str,
    kind: str,
    active: Any,
    noted_at: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条过敏史；非法时抛出 ValueError。"""
    name = " ".join(str(substance or "").split())[
        :MAX_ALLERGY_SUBSTANCE_LENGTH
    ]
    if not name:
        raise ValueError("过敏原不能为空")
    severity_key = str(severity or "moderate").strip().lower()
    if severity_key not in ALLERGY_SEVERITIES:
        raise ValueError("严重程度应为 mild/moderate/severe")
    kind_key = str(kind or "drug").strip().lower()
    if kind_key not in ALLERGY_KINDS:
        raise ValueError("过敏类型应为 drug/food/environment/other")
    if isinstance(active, bool):
        active_flag = 1 if active else 0
    else:
        active_flag = 1 if str(active).strip().lower() in {
            "1", "true", "on", "yes"
        } else 0
    noted = str(noted_at or "").strip()[:40]
    if noted:
        noted = _canonical_local_time(noted)
        if noted is None:
            raise ValueError("发现时间格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(noted):
            raise ValueError("发现时间不能晚于当前时间")
        noted = noted[:10]
    return {
        "profile": _normalize_profile_name(profile),
        "substance": name,
        "reaction": " ".join(str(reaction or "").split())[
            :MAX_ALLERGY_REACTION_LENGTH
        ],
        "severity": severity_key,
        "kind": kind_key,
        "active": bool(active_flag),
        "noted_at": noted,
        "note": " ".join(str(note or "").split())[
            :MAX_ALLERGY_NOTE_LENGTH
        ],
    }


def _normalize_care_item(
    profile: str,
    title: str,
    interval_days: Any,
    last_done_at: str,
    next_due_at: str,
    note: str,
    active: Any,
) -> Dict[str, Any]:
    """校验一条复查计划；非法时抛出 ValueError。"""
    name = " ".join(str(title or "").split())[:MAX_CARE_TITLE_LENGTH]
    if not name:
        raise ValueError("复查项目不能为空")
    if isinstance(interval_days, bool):
        raise ValueError("复查间隔应为 1–3650 天")
    try:
        interval = int(interval_days)
    except (TypeError, ValueError, OverflowError):
        raise ValueError("复查间隔应为 1–3650 天")
    if interval < 1 or interval > MAX_CARE_INTERVAL_DAYS:
        raise ValueError("复查间隔应为 1–3650 天")

    last_done = str(last_done_at or "").strip()[:40]
    if last_done:
        normalized_last = _canonical_local_time(last_done)
        if normalized_last is None:
            raise ValueError("上次完成时间格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(normalized_last):
            raise ValueError("上次完成时间不能晚于当前时间")
        last_done = normalized_last[:10]
    else:
        last_done = ""

    due_text = str(next_due_at or "").strip()[:40]
    if due_text:
        normalized_due = _canonical_local_time(due_text)
        if normalized_due is None:
            raise ValueError("下次复查日期格式应为 YYYY-MM-DD 或 ISO 时间")
        due = normalized_due[:10]
    else:
        base = (
            datetime.strptime(last_done, "%Y-%m-%d").date()
            if last_done else datetime.now().date()
        )
        due = (base + timedelta(days=interval)).strftime("%Y-%m-%d")
    if last_done and due < last_done:
        raise ValueError("下次复查日期不能早于上次完成日期")

    if isinstance(active, bool):
        active_flag = 1 if active else 0
    else:
        active_flag = 1 if str(active).strip().lower() in {
            "1", "true", "on", "yes"
        } else 0
    return {
        "profile": _normalize_profile_name(profile),
        "title": name,
        "interval_days": interval,
        "last_done_at": last_done,
        "next_due_at": due,
        "note": " ".join(str(note or "").split())[
            :MAX_CARE_NOTE_LENGTH
        ],
        "active": bool(active_flag),
    }


def _care_item_view_fields(
    row: Dict[str, Any], today: str
) -> Dict[str, Any]:
    """补充复查倒计时与逾期/临期标记。"""
    due = str(row.get("next_due_at") or "")[:10]
    days_until: Optional[int] = None
    if due:
        try:
            days_until = (
                datetime.strptime(due, "%Y-%m-%d").date()
                - datetime.strptime(today, "%Y-%m-%d").date()
            ).days
        except ValueError:
            days_until = None
    active = bool(row.get("active"))
    return {
        "days_until": days_until,
        "overdue": active and days_until is not None and days_until < 0,
        "due_today": active and days_until == 0,
        "due_soon": active and days_until is not None and 0 <= days_until <= 7,
    }


def _normalize_visit_record(
    profile: str,
    appointment_id: Any,
    visit_date: str,
    department: str,
    doctor: str,
    facility: str,
    diagnosis: str,
    advice: str,
    prescription: str,
    next_followup_date: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条就诊记录；非法时抛出 ValueError。"""
    visit = str(visit_date or "").strip()[:40]
    if not visit:
        visit = datetime.now().isoformat(timespec="minutes")
    else:
        visit = _canonical_local_time(visit)
        if visit is None:
            raise ValueError("就诊日期格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(visit):
            raise ValueError("就诊日期不能晚于当前时间")
    visit = visit[:10]

    followup = str(next_followup_date or "").strip()[:40]
    if followup:
        followup = _canonical_local_time(followup)
        if followup is None:
            raise ValueError("下次复诊日期格式应为 YYYY-MM-DD 或 ISO 时间")
        followup = followup[:10]
        if followup < visit:
            raise ValueError("下次复诊日期不能早于就诊日期")
    else:
        followup = ""

    return {
        "profile": _normalize_profile_name(profile),
        "appointment_id": _safe_db_id(appointment_id),
        "visit_date": visit,
        "department": " ".join(str(department or "").split())[:60],
        "doctor": " ".join(str(doctor or "").split())[:60],
        "facility": " ".join(str(facility or "").split())[
            :MAX_VISIT_FACILITY_LENGTH
        ],
        "diagnosis": " ".join(str(diagnosis or "").split())[
            :MAX_VISIT_TEXT_LENGTH
        ],
        "advice": " ".join(str(advice or "").split())[
            :MAX_VISIT_ADVICE_LENGTH
        ],
        "prescription": " ".join(str(prescription or "").split())[
            :MAX_VISIT_ADVICE_LENGTH
        ],
        "next_followup_date": followup,
        "note": " ".join(str(note or "").split())[
            :MAX_VISIT_TEXT_LENGTH
        ],
    }


def _visit_record_view_fields(
    row: Dict[str, Any], today: str
) -> Dict[str, Any]:
    followup = str(row.get("next_followup_date") or "")
    days_until: Optional[int] = None
    if followup:
        try:
            days_until = (
                datetime.strptime(followup, "%Y-%m-%d").date()
                - datetime.strptime(today, "%Y-%m-%d").date()
            ).days
        except ValueError:
            days_until = None
    return {
        "followup_days_until": days_until,
        "followup_overdue": (
            days_until is not None and days_until < 0
        ),
        "followup_scheduled": bool(
            int(row.get("followup_appointment_id") or 0)
        ),
    }


def _normalize_condition(
    profile: str,
    name: str,
    icd10: str,
    diagnosed_at: str,
    status: str,
    severity: str,
    note: str,
) -> Dict[str, Any]:
    """校验一条慢病/健康问题；非法时抛出 ValueError。"""
    title = " ".join(str(name or "").split())[
        :MAX_CONDITION_NAME_LENGTH
    ]
    if not title:
        raise ValueError("疾病/健康问题名称不能为空")
    status_key = str(status or "active").strip().lower()
    if status_key not in CONDITION_STATUSES:
        raise ValueError("状态应为 active/controlled/resolved")
    severity_key = str(severity or "moderate").strip().lower()
    if severity_key not in CONDITION_SEVERITIES:
        raise ValueError("严重程度应为 mild/moderate/severe")
    diagnosed = str(diagnosed_at or "").strip()[:40]
    if diagnosed:
        diagnosed = _canonical_local_time(diagnosed)
        if diagnosed is None:
            raise ValueError("确诊日期格式应为 YYYY-MM-DD 或 ISO 时间")
        if _is_future_timestamp(diagnosed):
            raise ValueError("确诊日期不能晚于当前时间")
        diagnosed = diagnosed[:10]
    return {
        "profile": _normalize_profile_name(profile),
        "name": title,
        "icd10": " ".join(str(icd10 or "").split()).upper()[
            :MAX_CONDITION_ICD10_LENGTH
        ],
        "diagnosed_at": diagnosed,
        "status": status_key,
        "severity": severity_key,
        "note": " ".join(str(note or "").split())[
            :MAX_CONDITION_NOTE_LENGTH
        ],
    }


def _normalize_reminder_due(value: Any) -> str:
    due = _normalize_datetime_text(value, limit=32)
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
    text = _normalize_datetime_text(value, limit=32)
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError("重复截止日期不是有效日期")
    if parsed.year > 9998:
        raise ValueError("重复截止日期超出支持范围")
    return text


def _validate_repeat_window(
    due_at: Any, repeat_days: Any, repeat_until: Any
) -> None:
    """重复截止不能早于首次到期日，否则规则永远不会生效。"""
    if not repeat_days or not repeat_until:
        return
    try:
        due_date = datetime.fromisoformat(str(due_at)).date()
        until_date = datetime.fromisoformat(str(repeat_until)).date()
    except (TypeError, ValueError):
        return
    if until_date < due_date:
        raise ValueError("重复截止日期不能早于提醒日期")


def _next_repeat_due(due_at: str, days: int) -> str:
    """在原到期日上按间隔顺延；已错过的周期自动跳到今天之后。"""
    days = max(1, int(days))
    try:
        parsed = datetime.fromisoformat(str(due_at))
    except ValueError:
        parsed = datetime.combine(datetime.now().date(), datetime.min.time())
    now = datetime.now()
    today = now.date()
    target = parsed.date() + timedelta(days=days)
    if target < today:
        # 顺延到第一个不早于今天的周期；恰好是今天的保留今天
        missed = (today - target).days
        steps = (missed + days - 1) // days
        target += timedelta(days=steps * days)
    if "T" in str(due_at):
        candidate = datetime.combine(target, parsed.time())
        if candidate.tzinfo is not None:
            candidate = candidate.replace(tzinfo=None)
        # 顺延到「今天」但该时刻已经过去：再推一个周期，
        # 否则完成操作会立刻生成一条已逾期的提醒
        if candidate <= now:
            target += timedelta(days=days)
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
        "feedback": {"helpful": 0, "unhelpful": 0},
    }


def _usage_int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0


#: 用量计数的上限：2^53-1 保证浏览器 JSON 解析/序列化往返不丢精度，
#: 也远小于 SQLite/浮点运算的溢出边界
MAX_USAGE_COUNT = 2**53 - 1


def _usage_count(value: Any) -> int:
    """把用量计数解析为非负整数并钳制在安全范围内。"""
    return min(max(0, _usage_int(value)), MAX_USAGE_COUNT)


def _clamp_usage_metadata(metadata: Any) -> Any:
    """钳制 metadata 里的用量字段，避免超大整数落库后撑爆统计接口。

    覆盖顶层 usage/llm_calls 与 agent_contributions 里的每份用量：
    后者会随备份导出，超过 2^53 的整数经浏览器 JSON 往返后会破坏
    checksum 自洽性。
    """
    if not isinstance(metadata, dict):
        return metadata

    def _clamp(value: Any) -> Any:
        # 非有限浮点交给 sanitize_json 统一变成 null，保持既有语义
        if isinstance(value, float) and not math.isfinite(value):
            return value
        return _usage_count(value)

    def _clamp_entry(entry: Any) -> Any:
        if not isinstance(entry, dict):
            return entry
        item = dict(entry)
        usage = item.get("usage")
        if isinstance(usage, dict):
            clamped = dict(usage)
            for key in (
                "prompt_tokens", "completion_tokens", "total_tokens",
            ):
                if key in clamped:
                    clamped[key] = _clamp(clamped[key])
            item["usage"] = clamped
        if "llm_calls" in item:
            item["llm_calls"] = _clamp(item["llm_calls"])
        return item

    cleaned = _clamp_entry(metadata)
    contributions = cleaned.get("agent_contributions")
    if isinstance(contributions, list):
        cleaned["agent_contributions"] = [
            _clamp_entry(item) for item in contributions
        ]
    return cleaned


def _accumulate_usage(entry: Dict[str, Any], metadata: Any) -> None:
    """把一条 assistant 消息的元数据累加进用量统计。"""
    entry["turns"] += 1
    if not isinstance(metadata, dict):
        return

    usage = metadata.get("usage")
    if isinstance(usage, dict):
        prompt = _usage_count(usage.get("prompt_tokens"))
        completion = _usage_count(usage.get("completion_tokens"))
        raw_total = usage.get("total_tokens")
        total = _usage_count(raw_total)
        if (
            not total
            and (prompt or completion)
        ):
            total = min(prompt + completion, MAX_USAGE_COUNT)
        entry["usage"]["prompt_tokens"] += prompt
        entry["usage"]["completion_tokens"] += completion
        entry["usage"]["total_tokens"] += total

    entry["llm_calls"] += _usage_count(metadata.get("llm_calls"))

    feedback = metadata.get("feedback")
    if isinstance(feedback, dict):
        try:
            rating = int(feedback.get("rating"))
        except (TypeError, ValueError, OverflowError):
            rating = 0
        if rating == 1:
            entry["feedback"]["helpful"] += 1
        elif rating == -1:
            entry["feedback"]["unhelpful"] += 1


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
        (query or "").strip()[:MAX_SEARCH_QUERY_LENGTH]
        .replace("\\", "\\\\")
        .replace("%", "\\%")
        .replace("_", "\\_")
    )
    return f"%{escaped}%"


def _like_any_condition(
    columns: Tuple[str, ...], query: Any
) -> Tuple[Optional[str], List[Any]]:
    """生成多列 LIKE 预过滤条件；空查询返回 (None, [])。

    调用方必须确保 columns 覆盖 Python 侧匹配的全部已存字段，
    否则预过滤会漏掉本来应命中的记录。
    """
    text = str(query or "").strip()
    if not text:
        return None, []
    like = _like_pattern(text)
    clause = "(" + " OR ".join(
        f"{column} LIKE ? ESCAPE '\\'" for column in columns
    ) + ")"
    return clause, [like] * len(columns)


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
    "medication_history",
    "vaccinations",
    "appointments",
    "lab_results",
    "allergies",
    "care_items",
    "visit_records",
    "conditions",
    "audit_log",
    "usage_events",
)

#: 首个校验版本的字段集，升级新增字段时不能改变旧摘要
_LEGACY_BACKUP_CHECKSUM_FIELDS = (
    "version", "sessions", "profiles", "health_metrics", "metric_targets",
    "reminders", "symptoms", "medications", "medication_logs",
    "medication_history", "vaccinations", "appointments", "lab_results",
    "allergies", "care_items", "visit_records", "conditions", "audit_log",
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


def _iter_canonical_json(value: Any) -> Iterator[str]:
    """流式产出与 ``json.dumps(_canonical_json_value(...))`` 相同的内容。

    备份校验和只需要摘要：逐块编码避免在内存里同时保留规范化深拷贝、
    完整 JSON 字符串与 UTF-8 字节串（大库可达数百 MB）。
    """
    if isinstance(value, float):
        sanitized = sanitize_json(value)
        if not isinstance(sanitized, float):
            yield "null"
        elif sanitized.is_integer():
            yield str(int(sanitized))
        else:
            yield json.dumps(sanitized, allow_nan=False)
        return
    if isinstance(value, dict):
        items = {str(key): item for key, item in value.items()}
        yield "{"
        for index, key in enumerate(sorted(items)):
            if index:
                yield ","
            yield json.dumps(key, ensure_ascii=False)
            yield ":"
            yield from _iter_canonical_json(items[key])
        yield "}"
        return
    if isinstance(value, (list, tuple, set)):
        yield "["
        for index, item in enumerate(value):
            if index:
                yield ","
            yield from _iter_canonical_json(item)
        yield "]"
        return
    yield json.dumps(value, ensure_ascii=False, allow_nan=False)


def _backup_checksum(payload: Dict[str, Any]) -> str:
    """计算备份内容的 SHA-256；只覆盖可往返的业务字段。

    旧版本备份可能缺少后续新增的字段，校验时跳过缺失字段，
    保证升级后仍能导入历史备份。
    """
    body = {
        key: payload[key]
        for key in _BACKUP_CHECKSUM_FIELDS
        if key in payload
    }
    hasher = hashlib.sha256()
    for chunk in _iter_canonical_json(body):
        hasher.update(chunk.encode("utf-8"))
    return "sha256:" + hasher.hexdigest()


class _ByteSizeCounter:
    """只统计 UTF-8 字节数、不保存内容的写入端。"""

    def __init__(self) -> None:
        self.bytes = 0

    def write(self, chunk: str) -> int:
        self.bytes += len(chunk.encode("utf-8"))
        return len(chunk)


def _json_payload_bytes(payload: Dict[str, Any]) -> int:
    """payload 序列化后的 UTF-8 字节数（与备份写盘同一编码参数）。"""
    counter = _ByteSizeCounter()
    encoder = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"))
    for chunk in encoder.iterencode(payload):
        counter.write(chunk)
    return counter.bytes


def _legacy_backup_checksum(payload: Dict[str, Any]) -> str:
    """首个校验版本的算法：缺失字段也计入 JSON（值为 null）。"""
    body = {
        key: payload.get(key)
        for key in _LEGACY_BACKUP_CHECKSUM_FIELDS
    }
    canonical = json.dumps(
        sanitize_json(body),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return "sha256:" + hashlib.sha256(
        canonical.encode("utf-8")
    ).hexdigest()


def _validate_export_payload(payload: Dict[str, Any]) -> None:
    """导出的备份必须能被 import_data 重新导入。

    单条消息/单会话消息数/总量上限只在导入端强制；导出端不校验会
    生成“下载成功却无法恢复”的备份。
    """
    total_chars = 0
    for session in payload.get("sessions") or []:
        if not isinstance(session, dict):
            continue
        messages = session.get("messages") or []
        if len(messages) > MAX_BACKUP_SESSION_MESSAGES:
            raise ValueError(
                f"单个会话的消息数超过可导入上限 "
                f"{MAX_BACKUP_SESSION_MESSAGES} 条，无法导出为可恢复备份。"
            )
        for message in messages:
            if not isinstance(message, dict):
                continue
            length = len(str(message.get("content") or ""))
            if length > MAX_BACKUP_MESSAGE_CHARS:
                raise ValueError(
                    f"存在超过 {MAX_BACKUP_MESSAGE_CHARS} 字符的单条消息，"
                    "无法导出为可恢复备份。"
                )
            metadata = message.get("metadata")
            if isinstance(metadata, (dict, list)):
                meta_length = len(
                    json.dumps(metadata, ensure_ascii=False)
                )
            else:
                meta_length = len(str(metadata or ""))
            if meta_length > MAX_BACKUP_MESSAGE_CHARS:
                raise ValueError(
                    f"存在超过 {MAX_BACKUP_MESSAGE_CHARS} 字符的消息元数据，"
                    "无法导出为可恢复备份。"
                )
            # 元数据可达正文数倍，全量计入会让长期用户无法导出；
            # 按 1/4 权重计入既保留总量上限，又不误伤正常备份
            total_chars += length + meta_length // 4
    if total_chars > MAX_BACKUP_TOTAL_CHARS:
        raise ValueError(
            "会话消息总量超过可导入上限，无法导出为可恢复备份。"
        )


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
        with _db_init_lock(self.db_path):
            self._init_schema()
        self._vacuum_if_needed(
            getattr(self, "_pending_vacuum", False)
        )
        self._harden_permissions()

    def _vacuum_if_needed(self, needed: bool) -> None:
        """旧库升级的一次性 VACUUM：清理 secure_delete 之前的空闲页。"""
        if not needed:
            return
        try:
            with self._lock:
                self._conn.execute("VACUUM")
            logger.info(
                "已完成旧库一次性 VACUUM（清理 secure_delete 之前的空闲页）"
            )
        except sqlite3.Error as e:
            logger.warning(f"旧库 VACUUM 失败（忽略）: {e}")

    def _harden_permissions(self) -> None:
        """把数据库与应用数据目录限制为仅当前用户可访问。

        自定义 MEDIX_SESSION_DB 只收紧数据库文件本身，不改变用户目录
        的权限；Windows 上的 chmod 不生效但会被忽略。
        """
        data_root = DEFAULT_DB_PATH.parent
        try:
            if self.db_path.resolve().parent == data_root.resolve():
                restrict_permissions(data_root, 0o700)
        except OSError:
            pass
        for suffix in ("", "-wal", "-shm"):
            restrict_permissions(Path(str(self.db_path) + suffix), 0o600)

    def _init_schema(self) -> None:
        with self._lock, self._conn:
            # 删除时立即清零释放的页面，避免问诊明文留在空闲页
            self._conn.execute("PRAGMA secure_delete=ON")
            for attempt in range(5):
                try:
                    self._conn.execute("PRAGMA journal_mode=WAL")
                    break
                except sqlite3.OperationalError:
                    if attempt == 4:
                        raise
                    _time.sleep(0.2 * (attempt + 1))
            # 早期开发版本的墓碑表存明文档案名且列名不同；不兼容时
            # 直接重建（墓碑只用于近期删除判定，可安全丢弃）
            columns = {
                row[1] for row in self._conn.execute(
                    "PRAGMA table_info(profile_deletions)"
                ).fetchall()
            }
            if columns and "profile_hash" not in columns:
                self._conn.execute("DROP TABLE profile_deletions")
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
                CREATE INDEX IF NOT EXISTS idx_messages_session_role
                    ON messages(session_id, role);
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
                    context     TEXT NOT NULL DEFAULT '',
                    note        TEXT NOT NULL DEFAULT '',
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL DEFAULT ''
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
                CREATE TABLE IF NOT EXISTS memory_cleanup_queue (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile    TEXT NOT NULL DEFAULT '',
                    session_id TEXT NOT NULL DEFAULT '',
                    attempts   INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(profile, session_id)
                );
                CREATE TABLE IF NOT EXISTS app_state (
                    key   TEXT PRIMARY KEY,
                    value TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS deleted_sessions (
                    session_id TEXT PRIMARY KEY,
                    deleted_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_deleted_sessions_at
                    ON deleted_sessions(deleted_at DESC);
                CREATE TABLE IF NOT EXISTS profile_deletions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_hash TEXT NOT NULL,
                    deleted_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profile_deletions_hash
                    ON profile_deletions(profile_hash, deleted_at DESC);
                CREATE TABLE IF NOT EXISTS profile_renames (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile_hash TEXT NOT NULL,
                    old_name     TEXT NOT NULL,
                    new_name     TEXT NOT NULL,
                    renamed_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_profile_renames_old
                    ON profile_renames(old_name, renamed_at DESC);
                CREATE TABLE IF NOT EXISTS usage_events (
                    id                INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id        TEXT NOT NULL DEFAULT '',
                    profile           TEXT NOT NULL DEFAULT '',
                    prompt_tokens     INTEGER NOT NULL DEFAULT 0,
                    completion_tokens INTEGER NOT NULL DEFAULT 0,
                    total_tokens      INTEGER NOT NULL DEFAULT 0,
                    llm_calls         INTEGER NOT NULL DEFAULT 0,
                    created_at        TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_usage_events_created
                    ON usage_events(created_at DESC);
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
                    vaccination_id INTEGER NOT NULL DEFAULT 0,
                    appointment_id INTEGER NOT NULL DEFAULT 0,
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
                    created_at  TEXT NOT NULL,
                    updated_at  TEXT NOT NULL DEFAULT ''
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
                    stock      REAL,
                    stock_unit TEXT NOT NULL DEFAULT '',
                    low_stock_threshold REAL NOT NULL DEFAULT 0,
                    expiry_date TEXT NOT NULL DEFAULT '',
                    started_at TEXT NOT NULL DEFAULT '',
                    stopped_at TEXT NOT NULL DEFAULT '',
                    stop_reason TEXT NOT NULL DEFAULT '',
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
                    stock_delta   REAL NOT NULL DEFAULT 0,
                    skipped       INTEGER NOT NULL DEFAULT 0,
                    skip_reason   TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_medication_logs_med_time
                    ON medication_logs(medication_id, taken_at DESC);
                CREATE INDEX IF NOT EXISTS idx_medication_logs_profile_time
                    ON medication_logs(profile, taken_at DESC);
                CREATE TABLE IF NOT EXISTS medication_history (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    medication_id INTEGER NOT NULL,
                    profile       TEXT NOT NULL DEFAULT '',
                    action        TEXT NOT NULL DEFAULT 'update',
                    detail        TEXT NOT NULL DEFAULT '',
                    changed_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_medication_history_med
                    ON medication_history(medication_id, changed_at DESC);
                CREATE TABLE IF NOT EXISTS attachments (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile       TEXT NOT NULL DEFAULT '',
                    kind          TEXT NOT NULL DEFAULT 'other',
                    ref_id        INTEGER NOT NULL DEFAULT 0,
                    original_name TEXT NOT NULL DEFAULT '',
                    stored_name   TEXT NOT NULL UNIQUE,
                    mime          TEXT NOT NULL DEFAULT '',
                    size          INTEGER NOT NULL DEFAULT 0,
                    note          TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_attachments_profile
                    ON attachments(profile, created_at DESC);
                CREATE TABLE IF NOT EXISTS vaccinations (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile       TEXT NOT NULL DEFAULT '',
                    vaccine_name  TEXT NOT NULL,
                    dose_number   INTEGER NOT NULL DEFAULT 1,
                    vaccinated_at TEXT NOT NULL,
                    next_due      TEXT NOT NULL DEFAULT '',
                    note          TEXT NOT NULL DEFAULT '',
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_vaccinations_profile_time
                    ON vaccinations(profile, vaccinated_at DESC);
                CREATE TABLE IF NOT EXISTS appointments (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile      TEXT NOT NULL DEFAULT '',
                    title        TEXT NOT NULL,
                    department   TEXT NOT NULL DEFAULT '',
                    doctor       TEXT NOT NULL DEFAULT '',
                    location     TEXT NOT NULL DEFAULT '',
                    scheduled_at TEXT NOT NULL,
                    status       TEXT NOT NULL DEFAULT 'upcoming',
                    note         TEXT NOT NULL DEFAULT '',
                    owner_visit_id INTEGER NOT NULL DEFAULT 0,
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_appointments_profile_time
                    ON appointments(profile, scheduled_at DESC);
                CREATE INDEX IF NOT EXISTS idx_appointments_status_time
                    ON appointments(status, scheduled_at);
                CREATE TABLE IF NOT EXISTS lab_results (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile    TEXT NOT NULL DEFAULT '',
                    panel      TEXT NOT NULL DEFAULT '',
                    item       TEXT NOT NULL,
                    value      REAL NOT NULL,
                    unit       TEXT NOT NULL DEFAULT '',
                    ref_low    REAL,
                    ref_high   REAL,
                    tested_at  TEXT NOT NULL,
                    lab_name   TEXT NOT NULL DEFAULT '',
                    note       TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_lab_results_profile_time
                    ON lab_results(profile, tested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lab_results_item_time
                    ON lab_results(item, tested_at DESC);
                CREATE INDEX IF NOT EXISTS idx_lab_results_time
                    ON lab_results(tested_at DESC);
                CREATE TABLE IF NOT EXISTS allergies (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile    TEXT NOT NULL DEFAULT '',
                    substance  TEXT NOT NULL,
                    reaction   TEXT NOT NULL DEFAULT '',
                    severity   TEXT NOT NULL DEFAULT 'moderate',
                    kind       TEXT NOT NULL DEFAULT 'drug',
                    active     INTEGER NOT NULL DEFAULT 1,
                    noted_at   TEXT NOT NULL DEFAULT '',
                    note       TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_allergies_profile
                    ON allergies(profile, active, substance);
                CREATE TABLE IF NOT EXISTS care_items (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile       TEXT NOT NULL DEFAULT '',
                    title         TEXT NOT NULL,
                    interval_days INTEGER NOT NULL DEFAULT 30,
                    last_done_at  TEXT NOT NULL DEFAULT '',
                    next_due_at   TEXT NOT NULL,
                    note          TEXT NOT NULL DEFAULT '',
                    active        INTEGER NOT NULL DEFAULT 1,
                    created_at    TEXT NOT NULL,
                    updated_at    TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_care_items_profile_due
                    ON care_items(profile, active, next_due_at);
                CREATE TABLE IF NOT EXISTS visit_records (
                    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile            TEXT NOT NULL DEFAULT '',
                    appointment_id     INTEGER NOT NULL DEFAULT 0,
                    visit_date         TEXT NOT NULL,
                    department         TEXT NOT NULL DEFAULT '',
                    doctor             TEXT NOT NULL DEFAULT '',
                    facility           TEXT NOT NULL DEFAULT '',
                    diagnosis          TEXT NOT NULL DEFAULT '',
                    advice             TEXT NOT NULL DEFAULT '',
                    prescription       TEXT NOT NULL DEFAULT '',
                    next_followup_date TEXT NOT NULL DEFAULT '',
                    followup_appointment_id INTEGER NOT NULL DEFAULT 0,
                    followup_reminder_id    INTEGER NOT NULL DEFAULT 0,
                    note               TEXT NOT NULL DEFAULT '',
                    created_at         TEXT NOT NULL,
                    updated_at         TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_visit_records_profile_date
                    ON visit_records(profile, visit_date DESC);
                CREATE TABLE IF NOT EXISTS conditions (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    profile      TEXT NOT NULL DEFAULT '',
                    name         TEXT NOT NULL,
                    icd10        TEXT NOT NULL DEFAULT '',
                    diagnosed_at TEXT NOT NULL DEFAULT '',
                    status       TEXT NOT NULL DEFAULT 'active',
                    severity     TEXT NOT NULL DEFAULT 'moderate',
                    note         TEXT NOT NULL DEFAULT '',
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conditions_profile
                    ON conditions(profile, status, name);
                """
            )
            # 备份导入按「档案 + 创建时间」身份配对，复合索引避免全表扫描
            self._conn.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_metrics_profile_created
                    ON health_metrics(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_symptoms_identity
                    ON symptoms(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_medications_identity
                    ON medications(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_vaccinations_identity
                    ON vaccinations(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_appointments_identity
                    ON appointments(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_allergies_identity
                    ON allergies(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_care_items_identity
                    ON care_items(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_visit_records_identity
                    ON visit_records(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_conditions_identity
                    ON conditions(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_reminders_identity
                    ON reminders(created_at);
                CREATE INDEX IF NOT EXISTS idx_lab_results_identity
                    ON lab_results(profile, created_at);
                CREATE INDEX IF NOT EXISTS idx_health_metrics_created
                    ON health_metrics(created_at);
                CREATE INDEX IF NOT EXISTS idx_symptoms_created
                    ON symptoms(created_at);
                CREATE INDEX IF NOT EXISTS idx_medications_created
                    ON medications(created_at);
                CREATE INDEX IF NOT EXISTS idx_vaccinations_created
                    ON vaccinations(created_at);
                CREATE INDEX IF NOT EXISTS idx_appointments_created
                    ON appointments(created_at);
                CREATE INDEX IF NOT EXISTS idx_lab_results_created
                    ON lab_results(created_at);
                CREATE INDEX IF NOT EXISTS idx_allergies_created
                    ON allergies(created_at);
                CREATE INDEX IF NOT EXISTS idx_care_items_created
                    ON care_items(created_at);
                CREATE INDEX IF NOT EXISTS idx_visit_records_created
                    ON visit_records(created_at);
                CREATE INDEX IF NOT EXISTS idx_conditions_created
                    ON conditions(created_at);
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
            # 旧提醒、以及升级中断或旧备份恢复后的提醒，都按
            # 「用药：药名 + 档案」补关联；语句幂等，每次启动可安全重跑
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
            if "vaccination_id" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN vaccination_id INTEGER NOT NULL DEFAULT 0"
                )
            if "appointment_id" not in reminder_columns:
                self._conn.execute(
                    "ALTER TABLE reminders ADD COLUMN appointment_id INTEGER NOT NULL DEFAULT 0"
                )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_medication
                    ON reminders(medication_id, done)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_vaccination
                    ON reminders(vaccination_id, done)
                """
            )
            self._conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_reminders_appointment
                    ON reminders(appointment_id, done)
                """
            )

            metric_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(health_metrics)"
                )
            }
            if "context" not in metric_columns:
                self._conn.execute(
                    "ALTER TABLE health_metrics ADD COLUMN context TEXT NOT NULL DEFAULT ''"
                )
            if "updated_at" not in metric_columns:
                self._conn.execute(
                    "ALTER TABLE health_metrics ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                "UPDATE health_metrics SET updated_at = created_at"
                " WHERE updated_at = ''"
            )
            symptom_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(symptoms)"
                )
            }
            if "updated_at" not in symptom_columns:
                self._conn.execute(
                    "ALTER TABLE symptoms ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''"
                )
            self._conn.execute(
                "UPDATE symptoms SET updated_at = created_at"
                " WHERE updated_at = ''"
            )

            # 旧版本就诊记录没有复诊安排关联，就地迁移
            visit_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(visit_records)"
                )
            }
            for column in (
                "followup_appointment_id", "followup_reminder_id",
            ):
                if column not in visit_columns:
                    self._conn.execute(
                        f"ALTER TABLE visit_records ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
                    )

            # 预约归属不再只依赖可被用户编辑的备注：持久化 owner_visit_id
            appointment_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(appointments)"
                )
            }
            if "owner_visit_id" not in appointment_columns:
                self._conn.execute(
                    "ALTER TABLE appointments ADD COLUMN"
                    " owner_visit_id INTEGER NOT NULL DEFAULT 0"
                )
            for row in self._conn.execute(
                "SELECT id, note, profile FROM appointments"
                " WHERE owner_visit_id = 0 AND note LIKE '%由就诊记录 #%'"
            ).fetchall():
                owner_match = re.search(
                    r"由就诊记录 #(\d+) 自动创建", str(row["note"] or "")
                )
                if owner_match is None:
                    continue
                target = _safe_db_id(owner_match.group(1))
                if not target:
                    continue
                visit_row = self._conn.execute(
                    "SELECT profile FROM visit_records WHERE id = ?",
                    (target,),
                ).fetchone()
                if visit_row is None:
                    # 备注里的 #N 指向不存在的就诊（导入残留/旧库 id）：
                    # 不能据此回填错误的归属
                    continue
                if _normalize_profile_name(
                    str(visit_row["profile"] or "")
                ) != _normalize_profile_name(str(row["profile"] or "")):
                    continue
                self._conn.execute(
                    "UPDATE appointments SET owner_visit_id = ?"
                    " WHERE id = ?",
                    (target, int(row["id"])),
                )
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_appointments_owner"
                " ON appointments(owner_visit_id, status)"
            )

            # 旧版本用药表没有库存、单位与有效期字段，就地迁移
            medication_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(medications)"
                )
            }
            for column, ddl in (
                ("stock", "REAL"),
                ("stock_unit", "TEXT NOT NULL DEFAULT ''"),
                ("low_stock_threshold", "REAL NOT NULL DEFAULT 0"),
                ("expiry_date", "TEXT NOT NULL DEFAULT ''"),
                ("started_at", "TEXT NOT NULL DEFAULT ''"),
                ("stopped_at", "TEXT NOT NULL DEFAULT ''"),
                ("stop_reason", "TEXT NOT NULL DEFAULT ''"),
            ):
                if column not in medication_columns:
                    self._conn.execute(
                        f"ALTER TABLE medications ADD COLUMN {column} {ddl}"
                    )
            log_columns = {
                row["name"]
                for row in self._conn.execute(
                    "PRAGMA table_info(medication_logs)"
                )
            }
            if "stock_delta" not in log_columns:
                self._conn.execute(
                    "ALTER TABLE medication_logs ADD COLUMN stock_delta REAL NOT NULL DEFAULT 0"
                )
            if "skipped" not in log_columns:
                self._conn.execute(
                    "ALTER TABLE medication_logs ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0"
                )
            if "skip_reason" not in log_columns:
                self._conn.execute(
                    "ALTER TABLE medication_logs ADD COLUMN skip_reason TEXT NOT NULL DEFAULT ''"
                )

            # 旧版本可能保存了空格分隔或仅日期的时间，统一为可排序格式。
            # 先用 GLOB 在 SQL 侧排除已是规范格式的行，避免每次启动把
            # 全部历史记录读进 Python 再逐行解析
            canonical_pattern = (
                "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"
                "T[0-9][0-9]:[0-9][0-9]"
            )
            for table, column in (
                ("health_metrics", "measured_at"),
                ("symptoms", "recorded_at"),
            ):
                rows = self._conn.execute(
                    f"SELECT id, {column} AS value FROM {table} "
                    f"WHERE {column} NOT GLOB ? "
                    f"AND {column} NOT GLOB ?",
                    (
                        canonical_pattern,
                        f"{canonical_pattern}:[0-9][0-9]",
                    ),
                ).fetchall()
                updates = []
                for row in rows:
                    canonical = _canonical_local_time(row["value"])
                    if canonical and canonical != row["value"]:
                        updates.append((canonical, row["id"]))
                if updates:
                    self._conn.executemany(
                        f"UPDATE {table} SET {column} = ? WHERE id = ?",
                        updates,
                    )

            schema_version = int(
                self._conn.execute("PRAGMA user_version").fetchone()[0] or 0
            )
            needs_vacuum = False
            if schema_version < 1:
                # 旧库升级前 secure_delete 未开启，已删问诊明文可能仍留在
                # 空闲页；用 user_version 标记只做一次性 VACUUM
                self._conn.execute("PRAGMA user_version=1")
                row = self._conn.execute(
                    "SELECT COUNT(*) AS total FROM sessions"
                ).fetchone()
                needs_vacuum = bool(int(row["total"] or 0) if row else 0)
            self._pending_vacuum = needs_vacuum
            cutoff = (
                datetime.now() - timedelta(days=90)
            ).isoformat(timespec="seconds")
            self._conn.execute(
                "DELETE FROM deleted_sessions WHERE deleted_at < ?",
                (cutoff,),
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
            # 显式重新创建同一编号时解除墓碑（内存 + 持久化）
            self._deleted_sessions.pop(session_id, None)
            self._conn.execute(
                "DELETE FROM deleted_sessions WHERE session_id = ?",
                (session_id,),
            )
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

    def _remember_deleted_locked(self, session_ids: Any) -> None:
        """登记删除墓碑（内存 + 持久化表）；调用方持有写事务。

        单条删除、批量删除与维护清理共用，避免只有单条路径写持久化表。
        """
        now = self._now()
        for raw in session_ids or []:
            session_id = str(raw or "").strip()
            if not session_id:
                continue
            self._deleted_sessions[session_id] = None
            self._conn.execute(
                "INSERT OR REPLACE INTO deleted_sessions"
                "(session_id, deleted_at) VALUES (?, ?)",
                (session_id, now),
            )
        while len(self._deleted_sessions) > self._max_deleted_sessions:
            self._deleted_sessions.pop(
                next(iter(self._deleted_sessions))
            )

    def _prune_table_locked(self, table: str, cap: int) -> None:
        """按主键保留最新 cap 行，避免审计/用量流水无限增长导致导出失败。"""
        try:
            row = self._conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()
        except sqlite3.Error:
            return
        count = int(row["n"] or 0) if row else 0
        if count <= cap:
            return
        self._conn.execute(
            f"DELETE FROM {table} WHERE id NOT IN "
            f"(SELECT id FROM {table} ORDER BY id DESC LIMIT ?)",
            (int(cap),),
        )

    def _existing_ids_locked(self, table: str) -> set:
        """表内当前全部主键的快照（导入配对区分“导入前已存在”用）。"""
        return {
            int(row["id"] if "id" in row.keys() else row[0])
            for row in self._conn.execute(f"SELECT id FROM {table}")
        }

    def mark_session_deleted(self, session_id: str) -> None:
        """登记已删除会话，阻止仍在进行的会诊把数据写回。"""
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._lock, self._conn:
            self._remember_deleted_locked([session_id])
        return
        with self._lock, self._conn:
            self._remember_deleted_locked([session_id])

    def is_session_deleted(self, session_id: str) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        with self._lock:
            if session_id in self._deleted_sessions:
                return True
            row = self._conn.execute(
                "SELECT 1 FROM deleted_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return row is not None

    def set_app_state(self, key: str, value: str) -> None:
        """持久化安装级状态（如「曾启用过长期记忆」）。"""
        name = str(key or "").strip()[:80]
        if not name:
            return
        now = self._now()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO app_state(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (name, str(value or "")[:200]),
            )
            # 顺带记录时间，便于诊断（不参与业务判断）
            self._conn.execute(
                "INSERT INTO app_state(key, value) VALUES (?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (f"{name}.updated_at", now),
            )

    def get_app_state(self, key: str, default: str = "") -> str:
        name = str(key or "").strip()[:80]
        if not name:
            return default
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM app_state WHERE key = ?", (name,)
            ).fetchone()
        return str(row["value"]) if row is not None else default

    def queue_memory_cleanup(self, entries: Any) -> None:
        """登记待清理的长期记忆作用域，供启动/后台任务重试。"""
        now = self._now()
        pairs = []
        for profile, session_id in entries or []:
            normalized = _normalize_profile_name(profile)
            # 会话 ID 允许 120 字符：截断后重试会删错作用域却报告成功
            sid = str(session_id or "").strip()[:120]
            if normalized or sid:
                pairs.append((normalized, sid))
        if not pairs:
            return
        with self._write_and_checkpoint():
            for profile, session_id in pairs:
                # 重复删除同一档案/会话时刷新删除时间与重试计数：
                # 时间窗按「最近一次删除」计算，否则中间一代患者的
                # 长期记忆会永远落在窗外而漏清理
                self._conn.execute(
                    """
                    INSERT INTO memory_cleanup_queue(
                        profile, session_id, attempts, last_error, created_at
                    ) VALUES (?, ?, 0, '', ?)
                    ON CONFLICT(profile, session_id) DO UPDATE SET
                        created_at = excluded.created_at,
                        attempts = 0,
                        last_error = ''
                    """,
                    (profile, session_id, now),
                )

    def list_memory_cleanup(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 500))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, profile, session_id, attempts, last_error,
                       created_at
                  FROM memory_cleanup_queue
                 ORDER BY id
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

    def complete_memory_cleanup(self, entries: Any) -> None:
        pairs = [
            (
                _normalize_profile_name(profile),
                str(session_id or "").strip()[:120],
            )
            for profile, session_id in entries or []
        ]
        if not pairs:
            return
        with self._write_and_checkpoint():
            for profile, session_id in pairs:
                self._conn.execute(
                    """
                    DELETE FROM memory_cleanup_queue
                     WHERE profile = ? AND session_id = ?
                    """,
                    (profile, session_id),
                )

    def fail_memory_cleanup(self, queue_id: int, error: str = "") -> None:
        with self._write_and_checkpoint():
            self._conn.execute(
                """
                UPDATE memory_cleanup_queue
                   SET attempts = attempts + 1, last_error = ?
                 WHERE id = ?
                """,
                (str(error or "")[:300], int(queue_id)),
            )

    def revive_session(self, session_id: str) -> None:
        """解除删除墓碑；用于同一编号被复用发起新会诊的场景。

        不写入任何行：save_exchange 已经使用 INSERT OR IGNORE，
        第一次问答会正常创建会话。删除进行中的会诊仍会重新登记墓碑，
        因此不会把迟到结果写回。
        """
        session_id = str(session_id or "").strip()
        if not session_id:
            return
        with self._lock, self._conn:
            self._deleted_sessions.pop(session_id, None)
            self._conn.execute(
                "DELETE FROM deleted_sessions WHERE session_id = ?",
                (session_id,),
            )

    def clear_message_usage(self, message_id: int) -> bool:
        """清除某条消息里的 usage/llm_calls 元数据。

        取消恰好落在落库线程时，该答复已写入 SQLite；取消路径还会把
        同一笔用量写入 usage_events，必须去掉消息上的用量避免双计。
        """
        try:
            message_id = int(message_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT metadata FROM messages WHERE id = ?",
                (message_id,),
            ).fetchone()
            if row is None:
                return False
            try:
                metadata = json.loads(row["metadata"] or "{}")
            except (TypeError, json.JSONDecodeError):
                metadata = {}
            if not isinstance(metadata, dict):
                return False
            changed = False
            for key in ("usage", "llm_calls"):
                if key in metadata:
                    metadata.pop(key, None)
                    changed = True
            if not changed:
                return False
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
            return True

    def set_session_profile(self, session_id: str, profile: str) -> bool:
        """把已有会话关联到档案（只在档案为空时生效）；不会创建会话。"""
        session_id = str(session_id or "").strip()
        profile = _normalize_profile_name(profile)
        if not session_id or not profile:
            return False
        with self._write_and_checkpoint():
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

    def profile_tombstone_exists(self, profile: str) -> bool:
        """该档案名是否留有删除墓碑（无论删除时间）。"""
        key = _profile_deletion_key(profile)
        if not key:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM profile_deletions"
                " WHERE profile_hash = ? LIMIT 1",
                (key,),
            ).fetchone()
        return row is not None

    def profile_rebinding_blocked(self, profile: str) -> bool:
        """档案留有删除墓碑且未被真正重建时，禁止重新绑定会话。

        删除档案按约定保留其余健康数据（指标/提醒等），因此不能用
        ``profile_name_exists`` 判断“已重建”，否则墓碑保护形同虚设；
        只认 patient_profiles 中重新出现的同名档案。
        """
        name = _normalize_profile_name(profile)
        if not name:
            return False
        if not self.profile_tombstone_exists(name):
            return False
        return not self.profile_created_at(name)

    def profile_deleted_at(self, profile: str) -> str:
        """档案最近一次删除的时间；没有墓碑返回空串。"""
        key = _profile_deletion_key(profile)
        if not key:
            return ""
        with self._lock:
            row = self._conn.execute(
                "SELECT MAX(deleted_at) AS deleted_at"
                " FROM profile_deletions WHERE profile_hash = ?",
                (key,),
            ).fetchone()
        return str((row["deleted_at"] if row else "") or "")

    def profile_deleted_since(self, profile: str, since: str) -> bool:
        """档案是否在 since（ISO 时间）之后被删除。

        用于中止删除进行中的会诊把结果写回已删除档案：长期记忆不能
        再写入旧命名空间，SQLite 也不能重新绑定已删除的档案名。
        """
        name = _normalize_profile_name(profile)
        since = str(since or "").strip()[:40]
        key = _profile_deletion_key(name)
        if not key or not since:
            return False
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM profile_deletions
                 WHERE profile_hash = ? AND deleted_at >= ?
                 LIMIT 1
                """,
                (key, since),
            ).fetchone()
        return row is not None

    #: 判断档案名是否仍在使用的数据表（与 rename_profile 的检查一致）
    _PROFILE_NAME_TABLES = (
        ("patient_profiles", "name"),
        ("sessions", "profile"),
        ("reminders", "profile"),
        ("health_metrics", "profile"),
        ("symptoms", "profile"),
        ("medications", "profile"),
        ("medication_logs", "profile"),
        ("medication_history", "profile"),
        ("vaccinations", "profile"),
        ("appointments", "profile"),
        ("lab_results", "profile"),
        ("allergies", "profile"),
        ("care_items", "profile"),
        ("visit_records", "profile"),
        ("conditions", "profile"),
        ("attachments", "profile"),
        ("usage_events", "profile"),
    )

    def profile_name_exists(self, name: str) -> bool:
        """档案名是否仍被任一数据表使用。"""
        name = _normalize_profile_name(name)
        if not name:
            return False
        with self._lock:
            for table, column in self._PROFILE_NAME_TABLES:
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE {column} = ? LIMIT 1",
                    (name,),
                ).fetchone()
                if row is not None:
                    return True
        return False

    def resolve_profile_alias(self, name: str) -> str:
        """把已改名档案的旧名称解析到当前名称。

        只跟随本库记录过的改名链；输入名仍被数据使用时原样返回（可能
        是另一位患者复用了这个名字）。中间名被新档案复用后，只跟随
        「新主人创建之前」产生的改名记录，避免把旧患者解析成新患者。
        """
        name = _normalize_profile_name(name)
        if not name:
            return ""
        if self.profile_name_exists(name):
            return name
        current = name
        visited = {name}
        with self._lock:
            for _ in range(10):
                rows = self._conn.execute(
                    """
                    SELECT new_name, renamed_at FROM profile_renames
                     WHERE old_name = ?
                     ORDER BY renamed_at ASC, id ASC
                    """,
                    (current,),
                ).fetchall()
                if not rows:
                    break
                owner_created = self.profile_created_at(current)
                if owner_created:
                    # 当前名已由新主人占用：只认新主人创建之前的改名
                    # 记录（属于旧链），之后的记录属于新主人
                    older = [
                        row for row in rows
                        if str(row["renamed_at"] or "") < owner_created
                    ]
                    if not older:
                        # 旧链在此中断：不能返回被他人占用的名字
                        return name
                    chosen = older[-1]
                else:
                    latest_deleted = self.profile_deleted_at(current)
                    if latest_deleted:
                        # 名字被删除后又复用过：只跟随删除之后产生的
                        # 改名记录（属于最近一次重建的档案）；更早的
                        # 记录属于已删除患者，不能把旧名解析到无关档案
                        fresh = [
                            row for row in rows
                            if str(row["renamed_at"] or "")
                            > latest_deleted
                        ]
                        if not fresh:
                            return name
                        chosen = fresh[-1]
                    elif len(rows) > 1:
                        # 无档案行且同名历史多次改名：无法判定归属，保守停止
                        return name
                    else:
                        chosen = rows[0]
                following = _normalize_profile_name(chosen["new_name"])
                if not following or following in visited:
                    break
                visited.add(following)
                current = following
        return current

    def profile_renamed_away(self, name: str) -> bool:
        """档案名是否已被改名到其它名称（旧名不再存在）。"""
        resolved = self.resolve_profile_alias(name)
        return bool(resolved) and resolved != _normalize_profile_name(name)

    def memory_scope_cleanup_pending(self, scope: str) -> bool:
        """该长期记忆作用域是否仍有未完成的删除清理。

        删除清理重试期间，云端可能还留着已删除档案/会话的旧摘要；
        按作用域检索（如 search_similar_cases 技能）必须同样跳过。
        """
        scope = str(scope or "").strip()
        if not scope:
            return False
        from core.session_context import (
            profile_memory_scope,
            session_memory_scope,
        )

        with self._lock:
            rows = self._conn.execute(
                "SELECT profile, session_id FROM memory_cleanup_queue"
            ).fetchall()
        for row in rows:
            profile = _normalize_profile_name(row["profile"])
            session_id = str(row["session_id"] or "")
            if profile and profile_memory_scope(profile) == scope:
                return True
            # 档案删除会把会话解绑为空档案并保留 (profile, session_id)
            # 条目：会话作用域同样可能仍有未删摘要，必须一起拦截
            if session_id and session_memory_scope(session_id) == scope:
                return True
        return False

    def profile_memory_cleanup_pending(self, profile: str) -> bool:
        """该档案是否存在尚未完成的整档长期记忆清理。

        profile 级清理（session_id 为空）成功前不能读取同名档案的云端
        记忆：删除的旧患者摘要可能仍在 Mem0 中，重建同名档案会召回。
        """
        name = _normalize_profile_name(profile)
        if not name:
            return False
        with self._lock:
            row = self._conn.execute(
                """
                SELECT 1 FROM memory_cleanup_queue
                 WHERE profile = ? AND session_id = ''
                 LIMIT 1
                """,
                (name,),
            ).fetchone()
        return row is not None

    def metric_daily_totals(
        self, day: str = "", profile: Optional[str] = None
    ) -> Dict[tuple, Dict[str, Any]]:
        """按自然日累计步数/饮水，返回 {(档案, 指标): {total, count}}。"""
        day = str(day or "").strip()[:10] or datetime.now().strftime(
            "%Y-%m-%d"
        )
        from_string = " AND profile = ?" if profile is not None else ""
        metrics = tuple(DAILY_ACCUMULATING_METRICS)
        placeholders = ",".join("?" for _ in metrics)
        params: List[Any] = [day, *metrics]
        if profile is not None:
            params.append(_normalize_profile_name(profile))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT profile, metric, COUNT(*) AS count,
                       SUM(value1) AS total
                  FROM health_metrics
                 WHERE substr(measured_at, 1, 10) = ?
                   AND metric IN ({placeholders}){from_string}
                 GROUP BY profile, metric
                """,
                params,
            ).fetchall()
        return {
            (str(row["profile"]), str(row["metric"])): {
                "count": int(row["count"] or 0),
                "total": round(float(row["total"] or 0), 1),
            }
            for row in rows
        }

    def metric_daily_series(
        self,
        profile: Optional[str],
        metric: str,
        days: int = 30,
    ) -> List[Dict[str, Any]]:
        """按自然日返回某指标在窗口内的合计（日期升序）。

        用于步数/饮水等每日累计指标的家庭对比：平均值、极值与达标
        次数都必须按“天”统计，不能按单条记录。
        """
        metric = str(metric or "").strip()
        if metric not in METRIC_TYPES:
            return []
        try:
            days = max(1, min(int(days or 30), 365))
        except (TypeError, ValueError, OverflowError):
            days = 30
        now = datetime.now()
        cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        end_of_today = now.replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
        conditions = ["metric = ?", "measured_at >= ?", "measured_at <= ?"]
        params: List[Any] = [metric, cutoff, end_of_today]
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT substr(measured_at, 1, 10) AS day,
                       COUNT(*) AS count, SUM(value1) AS total
                  FROM health_metrics
                 WHERE {' AND '.join(conditions)}
                 GROUP BY day
                 ORDER BY day
                """,
                params,
            ).fetchall()
        return [
            {
                "day": str(row["day"] or ""),
                "count": int(row["count"] or 0),
                "total": round(float(row["total"] or 0), 1),
            }
            for row in rows
            if row["day"]
        ]

    def profile_created_at(self, name: str) -> str:
        """档案的创建时间；不存在返回空串。"""
        name = _normalize_profile_name(name)
        if not name:
            return ""
        with self._lock:
            row = self._conn.execute(
                "SELECT created_at FROM patient_profiles WHERE name = ?",
                (name,),
            ).fetchone()
        return str(row["created_at"] or "") if row else ""

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
                ("vaccinations", "profile"),
                ("appointments", "profile"),
                ("lab_results", "profile"),
                ("allergies", "profile"),
                ("care_items", "profile"),
                ("visit_records", "profile"),
                ("conditions", "profile"),
                ("attachments", "profile"),
                ("usage_events", "profile"),
            ):
                rows = self._conn.execute(
                    f"SELECT DISTINCT {column} AS name FROM {table}"
                    f" WHERE {column} != ''"
                ).fetchall()
                names.update(row["name"] for row in rows)
        return sorted(names)

    def has_unassigned_records(self, days: int = 30) -> bool:
        """是否存在未指定档案（profile 为空）且会在健康档案中展示的记录。

        判定口径与 export_health_dossier 保持一致：已完成提醒不展示，
        症状/化验/会诊受 days 窗口限制。
        """
        try:
            days = max(1, min(int(days or 30), 365))
        except (TypeError, ValueError, OverflowError):
            days = 30
        cutoff = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        checks = (
            ("reminders", "profile = '' AND done = 0", ()),
            ("health_metrics", "profile = ''", ()),
            ("medications", "profile = ''", ()),
            ("vaccinations", "profile = ''", ()),
            ("appointments", "profile = ''", ()),
            ("allergies", "profile = ''", ()),
            ("care_items", "profile = ''", ()),
            ("visit_records", "profile = ''", ()),
            ("conditions", "profile = ''", ()),
            ("attachments", "profile = ''", ()),
            (
                "symptoms",
                "profile = '' AND substr(recorded_at, 1, 10) >= ?",
                (cutoff,),
            ),
            (
                "lab_results",
                "profile = '' AND substr(tested_at, 1, 10) >= ?",
                (cutoff,),
            ),
        )
        with self._lock:
            for table, where, params in checks:
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                    params,
                ).fetchone()
                if row is not None:
                    return True
            row = self._conn.execute(
                "SELECT 1 FROM sessions s WHERE s.profile = ''"
                " AND substr(COALESCE(s.updated_at, s.created_at), 1, 10)"
                " >= ?"
                " AND EXISTS (SELECT 1 FROM messages m"
                " WHERE m.session_id = s.id) LIMIT 1",
                (cutoff,),
            ).fetchone()
        return row is not None

    def save_exchange(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None,
        profile: str = "",
    ) -> Optional[int]:
        """保存一轮问答。标题取第一轮主诉。

        Args:
            metadata: 附加到 assistant 消息上的元数据（来源、科室意见等）
            profile: 会话关联的患者档案；首轮写入时一并绑定，避免客户端
                在落库后、绑定前断开导致会话没有档案归属

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
        profile_value = _normalize_profile_name(profile)
        metadata_json = json.dumps(
            sanitize_json(_clamp_usage_metadata(metadata or {})),
            ensure_ascii=False,
            allow_nan=False,
        )
        assistant_id: Optional[int] = None

        with self._lock, self._conn:
            # 删除与保存共用同一把锁：若会话已删除，禁止进行中的会诊写回；
            # 持久化墓碑覆盖多进程/多 worker 场景
            if session_id in self._deleted_sessions:
                return None
            tombstone = self._conn.execute(
                "SELECT 1 FROM deleted_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            if tombstone is not None:
                return None
            self._conn.execute(
                """
                INSERT OR IGNORE INTO sessions(
                    id, title, created_at, updated_at, profile
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (session_id, title, now, now, profile_value),
            )
            # 第一轮补上标题；后续轮次只更新时间；继续旧会话时自动移出归档。
            # 档案只在会话尚未绑定时补齐，避免把已绑定的会话改到别的患者。
            self._conn.execute(
                """
                UPDATE sessions
                   SET updated_at = ?,
                       archived = 0,
                       title = CASE WHEN title = '' THEN ? ELSE title END,
                       profile = CASE
                           WHEN profile = '' THEN ? ELSE profile
                       END
                 WHERE id = ?
                """,
                (now, title, profile_value, session_id),
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
        # 与 SQL LIKE 使用同一个搜索词，避免摘要匹配仍用超长原文
        query = str(query or "").strip()[:MAX_SEARCH_QUERY_LENGTH]
        sql = """
            SELECT s.id, s.title, s.created_at, s.updated_at, s.favorite,
                   s.archived, s.tags, s.profile,
                   (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count,
                   (SELECT content FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'assistant'
                     ORDER BY m.id DESC LIMIT 1) AS last_answer,
                   (SELECT substr(content, 1, 2000) FROM messages m
                     WHERE m.session_id = s.id AND m.role = 'assistant'
                       AND (
                           content LIKE '%风险等级%'
                           OR content LIKE '%高危%'
                           OR content LIKE '%中危%'
                           OR content LIKE '%低危%'
                           OR content LIKE '%紧急%'
                       )
                     -- 优先真正的分级答复：普通追问里的「紧急就医」
                     -- 不应盖掉本会话最近一次风险等级评估
                     ORDER BY (CASE WHEN content LIKE '%风险等级%'
                                    THEN 0 ELSE 1 END), m.id DESC
                     LIMIT 1) AS last_risk_answer
              FROM sessions s
        """
        conditions, params = _session_filter_conditions(
            query, favorites_only, days, archived, tag, profile
        )
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY s.updated_at DESC, s.id DESC LIMIT ? OFFSET ?"
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
            risk_answer = item.pop("last_risk_answer", "") or last_answer
            item["risk_text"] = str(risk_answer)[:2000]
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
        by_updated: bool = False,
    ) -> List[Dict[str, Any]]:
        """按时间返回会话，供健康时间线/统计合并展示。

        默认按创建时间（日历/时间线事件日期）；by_updated=True 时按
        最近活动时间，继续旧会话会算作近期会诊。
        """
        limit = max(1, min(int(limit or 200), 100000))
        time_col = (
            "COALESCE(updated_at, created_at)" if by_updated else "created_at"
        )
        order_col = (
            "COALESCE(s.updated_at, s.created_at)" if by_updated
            else "s.created_at"
        )
        conditions: List[str] = [f"{time_col} >= ?"]
        params: List[Any] = [str(start or "")]
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if end:
            conditions.append(f"{time_col} <= ?")
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
                SELECT s.id, s.title, s.created_at, s.updated_at, s.profile,
                       (
                           SELECT COUNT(*) FROM messages m
                            WHERE m.session_id = s.id
                       ) AS message_count{answer_column}
                  FROM sessions s
                 WHERE {where}
                 ORDER BY {order_col} DESC, s.id DESC
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
                       COALESCE(updated_at, created_at) AS last_active_at,
                       ROW_NUMBER() OVER (
                           PARTITION BY profile
                           ORDER BY COALESCE(updated_at, created_at) DESC,
                                    id DESC
                       ) AS rn
                  FROM sessions
                 {profile_filter}
            )
            SELECT l.profile, l.id AS session_id, l.created_at,
                   l.last_active_at,
                   COALESCE(
                       (
                           SELECT substr(m.content, 1, 2000)
                             FROM messages m
                            WHERE m.session_id = l.id
                              AND m.role = 'assistant'
                              AND (
                                  m.content LIKE '%风险等级%'
                                  OR m.content LIKE '%高危%'
                                  OR m.content LIKE '%中危%'
                                  OR m.content LIKE '%低危%'
                                  OR m.content LIKE '%紧急%'
                              )
                            ORDER BY m.id DESC LIMIT 1
                       ),
                       (
                           SELECT substr(m.content, 1, 2000)
                             FROM messages m
                            WHERE m.session_id = l.id
                              AND m.role = 'assistant'
                            ORDER BY m.id DESC LIMIT 1
                       )
                   ) AS last_answer,
                   COALESCE(
                       (
                           SELECT m.created_at
                             FROM messages m
                            WHERE m.session_id = l.id
                              AND m.role = 'assistant'
                              AND (
                                  m.content LIKE '%风险等级%'
                                  OR m.content LIKE '%高危%'
                                  OR m.content LIKE '%中危%'
                                  OR m.content LIKE '%低危%'
                                  OR m.content LIKE '%紧急%'
                              )
                            ORDER BY m.id DESC LIMIT 1
                       ),
                       (
                           SELECT m.created_at
                             FROM messages m
                            WHERE m.session_id = l.id
                              AND m.role = 'assistant'
                            ORDER BY m.id DESC LIMIT 1
                       )
                   ) AS answered_at
              FROM latest l
             WHERE l.rn = 1
        """
        with self._lock:
            rows = self._conn.execute(sql).fetchall()
        return [dict(row) for row in rows]

    def recent_risk_answers(
        self, since_day: str = "", per_profile: int = 200
    ) -> List[Dict[str, Any]]:
        """返回窗口内各档案最近的助手风险候选答复（新到旧）。

        「最后一条含关键词的答复」可能只是普通追问里的「紧急就医」，
        不能代表风险等级；调用方逐条解析并取最近一条可解析的答复，
        避免普通答复把近期高危会诊整体掩盖。
        """
        params: List[Any] = []
        day_clause = ""
        if since_day:
            day_clause = " AND substr(m.created_at, 1, 10) >= ?"
            params.append(str(since_day)[:10])
        limit = max(1, min(int(per_profile or 50), 100))
        sql = f"""
            WITH ranked AS (
                SELECT s.profile AS profile,
                       m.session_id AS session_id,
                       substr(m.content, 1, 2000) AS content,
                       m.created_at AS answered_at,
                       m.id AS message_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.profile
                           ORDER BY m.created_at DESC, m.id DESC
                       ) AS rn
                  FROM messages m
                  JOIN sessions s ON s.id = m.session_id
                 WHERE m.role = 'assistant'
                   AND (
                       m.content LIKE '%风险等级%'
                       OR m.content LIKE '%高危%'
                       OR m.content LIKE '%中危%'
                       OR m.content LIKE '%低危%'
                       OR m.content LIKE '%紧急%'
                   )
                   -- 模板化的「风险等级：不确定」无法解析，不应挤占
                   -- 回溯窗口把更早的真正分级答复挤出候选
                   AND m.content NOT LIKE '%风险等级：不确定%'
                   {day_clause}
            )
            SELECT profile, session_id, content, answered_at, message_id
              FROM ranked
             WHERE rn <= ?
             ORDER BY message_id DESC
        """
        params.append(limit)
        with self._lock:
            rows = self._conn.execute(sql, tuple(params)).fetchall()
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
            ("lab_results", "tested_at"),
            ("visit_records", "visit_date"),
            ("vaccinations", "vaccinated_at"),
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
            # 与家庭总览/时间线一致：续聊旧会话按最近一次提问计入今日
            "substr(COALESCE(updated_at, created_at), 1, 10) >= ?",
            "EXISTS (SELECT 1 FROM messages m"
            " WHERE m.session_id = sessions.id)",
        ]
        session_params: List[Any] = [cutoff]
        if profile is not None:
            session_conditions.append("profile = ?")
            session_params.append(_normalize_profile_name(profile))
        parts.append(
            "SELECT substr(COALESCE(updated_at, created_at), 1, 10)"
            " AS day FROM sessions"
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
            ("lab_results", "tested_at"),
            ("visit_records", "visit_date"),
            ("vaccinations", "vaccinated_at"),
        ):
            parts.append(
                f"SELECT profile, substr({column}, 1, 10) AS day"
                f" FROM {table} WHERE profile != ''"
                f" AND substr({column}, 1, 10) >= ?"
            )
            params.append(cutoff)
        parts.append(
            "SELECT profile, substr(COALESCE(updated_at, created_at),"
            " 1, 10) AS day"
            " FROM sessions WHERE profile != ''"
            " AND substr(COALESCE(updated_at, created_at), 1, 10) >= ?"
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

    def filter_existing_session_ids(self, session_ids: Any) -> set:
        """返回给定会话编号中已存在于本库的集合（分批查询）。"""
        values: List[str] = []
        seen = set()
        for value in session_ids or []:
            sid = str(value or "").strip()[:120]
            if sid and sid not in seen:
                seen.add(sid)
                values.append(sid)
        found: set = set()
        for start in range(0, len(values), 500):
            chunk = values[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT id FROM sessions"
                    f" WHERE id IN ({placeholders})",
                    chunk,
                ).fetchall()
            found.update(str(row["id"]) for row in rows)
        return found

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
                      FROM messages INDEXED BY idx_messages_session_role
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
            # 失败/取消调用的用量记在 usage_events，历史列表不能漏算
            for start in range(0, len(ids), 500):
                batch = ids[start:start + 500]
                placeholders = ",".join("?" for _ in batch)
                events = self._conn.execute(
                    f"""
                    SELECT session_id, prompt_tokens, completion_tokens,
                           total_tokens, llm_calls
                      FROM usage_events
                     WHERE session_id IN ({placeholders})
                    """,
                    batch,
                ).fetchall()
                for event in events:
                    entry = stats.setdefault(
                        event["session_id"], _new_usage_entry()
                    )
                    for key in (
                        "prompt_tokens", "completion_tokens",
                        "total_tokens",
                    ):
                        entry["usage"][key] += max(
                            0, int(event[key] or 0)
                        )
                    entry["llm_calls"] += max(
                        0, int(event["llm_calls"] or 0)
                    )

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

        with self._lock:
            event_sql = """
                SELECT e.prompt_tokens, e.completion_tokens,
                       e.total_tokens, e.llm_calls
                  FROM usage_events e
                  JOIN sessions s ON s.id = e.session_id
                 WHERE 1 = 1
            """
            if conditions:
                event_sql += " AND " + " AND ".join(conditions)
            event_rows = self._conn.execute(event_sql, params).fetchall()
        for event in event_rows:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                totals["usage"][key] += max(0, int(event[key] or 0))
            totals["llm_calls"] += max(0, int(event["llm_calls"] or 0))

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
            event_sql = """
                SELECT e.prompt_tokens, e.completion_tokens,
                       e.total_tokens, e.llm_calls
                  FROM usage_events e
                  JOIN sessions s ON s.id = e.session_id
                 WHERE 1 = 1
            """
            if conditions:
                event_sql += " AND " + " AND ".join(conditions)
            event_rows = self._conn.execute(event_sql, params).fetchall()

        totals = _new_usage_entry()
        helpful = 0
        unhelpful = 0
        for event in event_rows:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                totals["usage"][key] += max(0, int(event[key] or 0))
            totals["llm_calls"] += max(0, int(event["llm_calls"] or 0))
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

        with self._write_and_checkpoint():
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
            # 导入备份可能带未来时间；最近 N 天不应包含未来消息
            tomorrow = (
                datetime.now() + timedelta(days=1)
            ).strftime("%Y-%m-%dT00:00")
            sql += " AND m.created_at < ?"
            params.append(tomorrow)
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
                total_tokens += _usage_count(usage.get("total_tokens"))

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

    def record_usage_event(
        self,
        session_id: str,
        profile: str,
        usage: Any,
        llm_calls: Any = 0,
    ) -> None:
        """记录一次未落库调用（失败/中止）的已消耗用量。"""
        if not isinstance(usage, dict):
            usage = {}
        prompt = _usage_count(usage.get("prompt_tokens"))
        completion = _usage_count(usage.get("completion_tokens"))
        total = _usage_count(usage.get("total_tokens"))
        if not total and (prompt or completion):
            total = min(prompt + completion, MAX_USAGE_COUNT)
        calls = _usage_count(llm_calls)
        if not (prompt or completion or total or calls):
            return
        with self._write_and_checkpoint():
            self._conn.execute(
                """
                INSERT INTO usage_events(
                    session_id, profile, prompt_tokens, completion_tokens,
                    total_tokens, llm_calls, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(session_id or "").strip()[:64],
                    _normalize_profile_name(profile),
                    prompt, completion, total, calls, self._now(),
                ),
            )
            self._prune_table_locked("usage_events", 100000)

    def _usage_events_between(
        self, start: str, end: str
    ) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT session_id, profile, prompt_tokens,
                       completion_tokens, total_tokens, llm_calls,
                       created_at
                  FROM usage_events
                 WHERE created_at >= ? AND created_at < ?
                """,
                (start, end),
            ).fetchall()
        return [dict(row) for row in rows]

    def list_usage_events(self, limit: int = 100000) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100000), 100000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT id, session_id, profile, prompt_tokens,
                       completion_tokens, total_tokens, llm_calls, created_at
                  FROM usage_events
                 ORDER BY id
                 LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]

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

        # 失败/中止会诊没有 assistant 消息，用量来自单独的 usage_events；
        # 收藏筛选只限制到收藏会话的失败调用，不能整段跳过
        end_text = (today + timedelta(days=1)).isoformat()
        favorite_ids: Optional[set] = None
        if favorites_only:
            with self._lock:
                favorite_ids = {
                    str(row["id"])
                    for row in self._conn.execute(
                        "SELECT id FROM sessions WHERE favorite = 1"
                    )
                }
        for event in self._usage_events_between(start_text, end_text):
            if (
                favorites_only
                and str(event.get("session_id") or "")
                not in (favorite_ids or set())
            ):
                continue
            if profile and event.get("profile") != profile:
                continue
            bucket = buckets.get(str(event["created_at"])[:10])
            if bucket is None:
                continue
            for key in (
                "prompt_tokens", "completion_tokens", "total_tokens"
            ):
                bucket["usage"][key] += max(
                    0, int(event.get(key) or 0)
                )
            bucket["llm_calls"] += max(
                0, int(event.get("llm_calls") or 0)
            )

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

        for event in self._usage_events_between(start, next_month):
            for key in (
                "prompt_tokens", "completion_tokens", "total_tokens"
            ):
                totals["usage"][key] += max(
                    0, int(event.get(key) or 0)
                )
            totals["llm_calls"] += max(
                0, int(event.get("llm_calls") or 0)
            )

        return {
            "month": today.strftime("%Y-%m"),
            "usage": totals["usage"],
            "llm_calls": totals["llm_calls"],
            "turns": totals["turns"],
        }

    def export_data(self) -> Dict[str, Any]:
        """导出全部会话与全部消息，供本地备份或迁移。

        持锁贯穿所有表的读取，避免备份期间发生写入时产生跨表不一致；
        SHA-256 与可恢复性校验在大库上耗时明显，放到释放锁之后执行，
        不再延长全局 RLock 的持有时间（会冻结会诊等所有 DB 访问）。
        """
        with self._lock:
            payload = self._export_data()
        # 先做体积/可恢复性校验：超限备份不应先付出校验和的内存与时间
        _validate_export_payload(payload)
        # 字符上限把元数据按 1/4 计权，可能放行超过备份文件字节上限的
        # 负载；这里按真实 UTF-8 字节再校验，保证导出的文件必然可导入
        if _json_payload_bytes(payload) > MAX_BACKUP_FILE_BYTES - 65536:
            raise ValueError(
                "备份体积超过单个文件上限，无法导出为可恢复备份。"
            )
        payload["checksum"] = _backup_checksum(payload)
        return payload

    def _export_data(self) -> Dict[str, Any]:
        """在调用方持有可重入锁时收集各表快照。"""
        # 在单个锁内一次取快照：分页导出时若有会话被更新，
        # 会因 updated_at 排序漂移而被漏导或重复导出
        with self._lock:
            # 历史库可能已积累超过导出口径的审计/用量流水：先按上限
            # 裁剪最旧记录，避免备份与导出永久失败
            self._prune_table_locked("audit_log", 100000)
            self._prune_table_locked("usage_events", 100000)
            # 导出与导入共用同一上限：超限时明确报错，避免备份静默丢行
            session_count = self._conn.execute(
                "SELECT COUNT(*) AS n FROM sessions"
            ).fetchone()["n"]
            if session_count > 100000:
                raise ValueError("一次最多备份 100000 个会话。")
            for table, key, label in (
                ("patient_profiles", "profiles", "档案"),
                ("health_metrics", "health_metrics", "健康指标"),
                ("metric_targets", "metric_targets", "指标目标"),
                ("symptoms", "symptoms", "症状记录"),
                ("medications", "medications", "用药"),
                ("medication_logs", "medication_logs", "用药打卡"),
                ("medication_history", "medication_history", "用药变更"),
                ("vaccinations", "vaccinations", "疫苗接种"),
                ("appointments", "appointments", "就诊预约"),
                ("lab_results", "lab_results", "化验结果"),
                ("allergies", "allergies", "过敏史"),
                ("care_items", "care_items", "复查计划"),
                ("visit_records", "visit_records", "就诊记录"),
                ("conditions", "conditions", "慢病清单"),
                ("reminders", "reminders", "提醒"),
                ("audit_log", "audit_log", "审计记录"),
                ("usage_events", "usage_events", "用量流水"),
            ):
                count = self._conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                ).fetchone()["n"]
                cap = IMPORT_LIST_LIMITS[key]
                if count > cap:
                    raise ValueError(f"一次最多备份 {cap} 条{label}。")
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
            "medication_history": self.list_medication_history(
                limit=100000
            ),
            "vaccinations": self.list_vaccinations(limit=100000),
            "appointments": self.list_appointments(limit=100000),
            "lab_results": self.list_lab_results(limit=100000),
            "allergies": self.list_allergies(limit=100000),
            "care_items": self.list_care_items(limit=100000),
            "visit_records": self.list_visit_records(limit=100000),
            "conditions": self.list_conditions(limit=100000),
            "usage_events": self.list_usage_events(limit=100000),
        }
        return payload

    def _import_identity_match(
        self,
        table: str,
        columns: Tuple[str, ...],
        values: Tuple[Any, ...],
        content_columns: Tuple[str, ...],
        content_values: Tuple[Any, ...],
        state: Dict[tuple, set],
        raw_id: int = 0,
        created_at_value: Any = None,
        fallback_where: Optional[Tuple[str, Tuple[Any, ...]]] = None,
        pre_existing_ids: Optional[set] = None,
        match_where: Optional[Tuple[str, Tuple[Any, ...]]] = None,
        soft_where: Optional[Tuple[str, Tuple[Any, ...]]] = None,
        backup_group_size: int = 0,
        backup_soft_size: int = 0,
        backup_anchor_size: int = 0,
        backup_ids: Optional[set] = None,
        backup_profiles: Optional[Dict[int, str]] = None,
        backup_created: Optional[Dict[int, str]] = None,
        backup_contents: Optional[Dict[int, tuple]] = None,
    ) -> Optional[int]:
        """按「身份 + 内容」配对已存在的记录，返回可复用的行 id（没有则 None）。

        身份 = 档案 + 创建时间，不含 PATCH 可编辑字段；内容列（药名/
        剂量、预约主题/时间等）只用于在同一身份组内锚定同一条记录。
        配对顺序：
        1. 备份主键 + 身份 + 内容完全一致（同一库、未删除）；
        2. 同身份 + 同内容（跨库恢复或主键漂移）；
        3. 同身份按 id 升序兜底（本地编辑过的记录，保留编辑）；
        4. 没有可配对行时返回 None，调用方插入（还原被删记录）。
        重复导入保持幂等，删除一条后恢复按原内容补回。
        """
        identity = (table,) + tuple(values)
        moved_key = (table, "__moved__")
        moved = state.setdefault(moved_key, set())
        matched = state.setdefault((table, "__matched__"), set())
        #: 本次导入已处理过的备份原始主键：同名同秒的另一条备份记录
        #: 若已处理完，其本地存活行不再被它占用
        seen_raw_ids = state.setdefault((table, "__seen_raw__"), set())
        if raw_id > 0:
            seen_raw_ids.add(raw_id)
        where = " AND ".join(f"{column} = ?" for column in columns)

        def _claim(candidate: int, mark_moved: bool = False) -> int:
            """认领一行：从所有身份组与漂移候选中排除，避免二次认领。"""
            if mark_moved:
                moved.add(candidate)
            matched.add(candidate)
            claimed.discard(candidate)
            for cached in list(state.values()):
                if (
                    isinstance(cached, set)
                    and cached is not moved
                    and cached is not matched
                ):
                    cached.discard(candidate)
            state[identity] = claimed
            return candidate
        claimed = state.get(identity)
        if claimed is None:
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE {where} ORDER BY id",
                values,
            ).fetchall()
            claimed = {int(row["id"]) for row in rows}
        group_seen_key = (table, "__seen__", identity)
        group_seen = int(state.get(group_seen_key, 0)) + 1
        state[group_seen_key] = group_seen
        local_size_key = (table, "__local_size__", identity)
        local_group_size = state.get(local_size_key)
        if local_group_size is None:
            local_group_size = len(claimed)
            state[local_size_key] = local_group_size

        def _ambiguous() -> Optional[int]:
            """无法区分时按被删记录补回，绝不复制存活行旧版本。"""
            state[identity] = claimed
            return None

        def _content_owned_by_other(candidate: int) -> bool:
            """候选内容是否精确等于备份里另一条记录（而非当前行）。

            软字段/锚点都可被编辑；当内容指纹命中同批次备份中的
            另一条记录时，该候选是那条记录的存活行，不能认领给本行，
            否则被删记录无法补回、另一条记录会被复制。
            """
            if not backup_contents or raw_id <= 0:
                return False
            current = backup_contents.get(raw_id)
            if current is None:
                return False
            try:
                row = self._conn.execute(
                    f"SELECT {', '.join(content_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return False
            if row is None:
                return False
            fingerprint = self._db_content_fingerprint(
                row, content_columns
            )
            if fingerprint == current:
                return False
            return any(
                other_id != raw_id and contents == fingerprint
                for other_id, contents in backup_contents.items()
            )

        def _same_backup_origin() -> bool:
            """本地库是否可证明就是该备份的来源库（主键空间同源）。

            恢复后本地自增 id 与备份原始主键数值相等可能只是巧合；
            只有当所有落在备份主键范围内的既有行都在备份主键集合内、
            且 created_at（不可编辑）与备份一致时，才允许仅凭主键
            相等或「未编辑的同身份行」认领，否则只能按内容/编辑证据
            配对，缺行时插入补回。
            """
            if not backup_ids or not backup_created or not pre_existing_ids:
                return True
            cache_key = (table, "__same_origin__")
            cached = state.get(cache_key)
            if cached is not None:
                return bool(cached)
            result = True
            try:
                rows = self._conn.execute(
                    f"SELECT id, created_at FROM {table} WHERE id <= ?",
                    (max(backup_ids),),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            for row in rows:
                local_id = int(row["id"])
                if local_id not in pre_existing_ids:
                    continue
                if local_id not in backup_ids:
                    result = False
                    break
                expected = backup_created.get(local_id)
                if expected and str(
                    row["created_at"] or ""
                )[:40] != expected:
                    result = False
                    break
            state[cache_key] = result
            return result

        def _candidate_edited(candidate: int) -> bool:
            try:
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE id = ?"
                    " AND updated_at != created_at",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return False
            return row is not None

        def _other_backup_row_survives(candidate: int) -> bool:
            """备份中 raw id == candidate 的那条记录是否另有本地存活行。

            本地自增 id 与备份原始主键数值相等可能只是巧合（恢复后
            早已漂移）：若那条备份记录（同档案、同创建时间）在本地
            确实另有存活行，本候选更可能是当前备份行被改挂/编辑后的
            版本；否则把候选判给那条备份记录，避免被删记录的恢复抢占
            另一位成员的存活行。
            """
            if not backup_profiles or not backup_created:
                return False
            if candidate in seen_raw_ids:
                # 那条备份记录本次已经处理过：它的存活行（若有）已被
                # 认领或按被删记录补回，本候选可以归当前行
                return True
            profile_key = backup_profiles.get(candidate)
            created_key = backup_created.get(candidate)
            if profile_key is None or not created_key:
                return False
            try:
                rows = self._conn.execute(
                    f"SELECT id, profile FROM {table} WHERE created_at = ?"
                    " AND id != ?",
                    (created_key, candidate),
                ).fetchall()
            except sqlite3.Error:
                return False
            for other in rows:
                other_id = int(other["id"])
                if backup_ids and other_id in backup_ids:
                    # 只有该兄弟行确实对应备份里另一条「同档案同秒」
                    # 记录时才不能当作本备份记录的存活行证据；
                    # 数值相等可能只是本地自增 id 与备份主键的巧合
                    other_profile = (
                        backup_profiles.get(other_id)
                        if backup_profiles else None
                    )
                    other_created = (
                        backup_created.get(other_id)
                        if backup_created else None
                    )
                    if (
                        other_profile is not None
                        and other_created == created_key
                        and _normalize_profile_name(
                            str(other["profile"] or "")
                        ) == other_profile
                    ):
                        continue
                if pre_existing_ids is not None and (
                    other_id not in pre_existing_ids
                ):
                    # 本次导入刚插入的行不是「原有存活行」
                    continue
                if _normalize_profile_name(
                    str(other["profile"] or "")
                ) == profile_key:
                    return True
            return False
        content_where = " AND ".join(
            f"{column} = ?" for column in content_columns
        )
        if raw_id > 0 and content_where:
            step1_clause = ""
            step1_params: Tuple[Any, ...] = ()
            if match_where is not None and not (
                columns and columns[0] == "profile"
            ):
                # 提醒的身份只有创建时间：主键撞号但不同档案时不能
                # 直接认领，必须先满足档案约束
                step1_clause = " AND " + match_where[0]
                step1_params = tuple(match_where[1])
            row = self._conn.execute(
                f"SELECT id FROM {table} WHERE id = ? AND {where}"
                f" AND {content_where}{step1_clause}",
                (raw_id, *values, *content_values, *step1_params),
            ).fetchone()
            if (
                row is not None
                and int(row["id"]) in claimed
                and int(row["id"]) not in moved
                and int(row["id"]) not in matched
            ):
                return _claim(int(row["id"]))
        if raw_id > 0 and content_where and created_at_value:
            # 记录被 PATCH 改挂到另一档案：主键 + 创建时间 + 内容仍能确认
            # 是同一条，恢复旧备份时不能在原档案名下重新插入
            raw_scope_clause = ""
            raw_scope_params: Tuple[Any, ...] = ()
            if (
                (not columns or columns[0] != "profile")
                and match_where is not None
                and str(match_where[0]).strip().lower().startswith(
                    "profile"
                )
            ):
                raw_scope_clause = " AND " + match_where[0]
                raw_scope_params = tuple(match_where[1])
            row = self._conn.execute(
                f"SELECT id, profile FROM {table}"
                f" WHERE id = ? AND created_at = ? AND {content_where}"
                f"{raw_scope_clause}",
                (
                    raw_id, created_at_value, *content_values,
                    *raw_scope_params,
                ),
            ).fetchone()
            if row is not None:
                candidate = int(row["id"])
                claimable = True
                # 只接受「本次导入开始前已存在」的行：本次导入刚插入的
                # 同秒同内容行不能被当成改挂记录吞掉；但只跳过本步，
                # 后续内容配对仍可找到上次导入的同一条记录，保持幂等
                if pre_existing_ids is not None and (
                    candidate not in pre_existing_ids
                ):
                    claimable = False
                if claimable and (candidate in moved or candidate in matched):
                    claimable = False
                expected_row_profile = (
                    str(values[0])
                    if columns and columns[0] == "profile" else ""
                )
                if claimable and expected_row_profile and (
                    _normalize_profile_name(str(row["profile"] or ""))
                    != _normalize_profile_name(expected_row_profile)
                ):
                    # 跨档案认领同样需要改挂/编辑证据：另一位成员
                    # 同秒同内容的未编辑记录不能被吞掉
                    try:
                        meta = self._conn.execute(
                            f"SELECT created_at, updated_at FROM {table}"
                            " WHERE id = ?",
                            (candidate,),
                        ).fetchone()
                    except sqlite3.Error:
                        meta = None
                    if (
                        meta is None
                        or not str(meta["updated_at"] or "")
                        or str(meta["updated_at"])
                        == str(meta["created_at"] or "")
                    ):
                        claimable = False
                if claimable:
                    # 同一候选行不能再被任何身份组（目标档案的内容配对
                    # 与兜底）重复认领，否则被删的同秒记录会被当成
                    # “已存在”
                    return _claim(candidate, mark_moved=True)
            # 跨库/合并恢复导致主键漂移后，改挂不能只靠主键相等识别：
            # 按「创建时间 + 内容」在导入前已存在的行中找唯一候选
            if pre_existing_ids:
                # 空集合时候选必然为空，跳过可让空库/换机恢复保持线性
                # 与内容配对同样要遵守档案等额外约束：提醒只有
                # 创建时间+到期日，不能跨档案认领同一行
                drift_clause = ""
                drift_params: Tuple[Any, ...] = ()
                if match_where is not None:
                    drift_clause = " AND " + match_where[0]
                    drift_params = tuple(match_where[1])
                rows = self._conn.execute(
                    f"SELECT id, profile FROM {table}"
                    f" WHERE created_at = ? AND {content_where}"
                    f"{drift_clause}",
                    (created_at_value, *content_values, *drift_params),
                ).fetchall()
                row_profile = (
                    str(values[0])
                    if columns and columns[0] == "profile" else ""
                )
                candidates = []
                for row in rows:
                    candidate = int(row["id"])
                    if candidate not in pre_existing_ids:
                        continue
                    if candidate in moved or candidate in matched:
                        continue
                    if (
                        backup_ids
                        and candidate in backup_ids
                        and candidate != raw_id
                    ):
                        candidate_profile_key = (
                            backup_profiles.get(candidate)
                            if backup_profiles else None
                        )
                        candidate_created_key = (
                            backup_created.get(candidate)
                            if backup_created else None
                        )
                        if (
                            candidate_profile_key is not None
                            and candidate_created_key is not None
                            and candidate_created_key
                            == str(created_at_value or "")
                            and _normalize_profile_name(
                                str(row["profile"] or "")
                            ) == candidate_profile_key
                            and not _other_backup_row_survives(candidate)
                        ):
                            # 该本地行确实对应备份里的另一条同档案同秒
                            # 记录：不能被当作本行“改挂”的版本吞掉
                            continue
                    candidate_profile = str(row["profile"] or "")
                    if (
                        row_profile
                        and candidate_profile != row_profile
                    ):
                        # 跨档案只认「本地确实改挂/编辑过」的行：
                        # 另一位成员同秒同内容的未编辑记录不能被
                        # 当成已改挂吞掉，否则删除的记录无法恢复
                        try:
                            meta = self._conn.execute(
                                f"SELECT created_at, updated_at FROM {table}"
                                " WHERE id = ?",
                                (candidate,),
                            ).fetchone()
                        except sqlite3.Error:
                            meta = None
                        if (
                            meta is None
                            or not str(meta["updated_at"] or "")
                            or str(meta["updated_at"])
                            == str(meta["created_at"] or "")
                        ):
                            continue
                    candidates.append(candidate)
                if candidates and soft_where is not None:
                    # 漂移候选必须满足不可编辑的软字段（如提醒标题）：
                    # 同秒同到期日的另一条提醒不能被当成当前行的存活版本
                    soft_fragment, soft_extra = soft_where
                    placeholders = ",".join(
                        "?" for _ in candidates
                    )
                    soft_rows = self._conn.execute(
                        f"SELECT id FROM {table}"
                        f" WHERE id IN ({placeholders})"
                        f" AND {soft_fragment}",
                        (*candidates, *soft_extra),
                    ).fetchall()
                    soft_ids = {int(row["id"]) for row in soft_rows}
                    candidates = [
                        candidate for candidate in candidates
                        if candidate in soft_ids
                    ]
                if len(candidates) == 1:
                    if _content_owned_by_other(candidates[0]):
                        return _ambiguous()
                    return _claim(candidates[0], mark_moved=True)
        if content_where:
            match_clause = ""
            match_params: Tuple[Any, ...] = ()
            if match_where is not None:
                # 提醒的身份只有创建时间：内容配对再要求同档案，
                # 避免跨档案认领同秒同到期日的提醒
                match_clause = " AND " + match_where[0]
                match_params = tuple(match_where[1])
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE {where}"
                f" AND {content_where}{match_clause} ORDER BY id",
                (*values, *content_values, *match_params),
            ).fetchall()
            for row in rows:
                candidate = int(row["id"])
                if candidate in moved or candidate in matched:
                    continue
                if candidate in claimed:
                    if soft_where is not None:
                        # 内容列（如提醒的到期日）可能与另一条同秒
                        # 记录相同：必须再满足不可编辑的软字段，
                        # 否则被删记录不会被补回
                        soft_fragment, soft_extra = soft_where
                        soft_row = self._conn.execute(
                            f"SELECT 1 FROM {table}"
                            f" WHERE id = ? AND {soft_fragment}",
                            (candidate, *soft_extra),
                        ).fetchone()
                        if soft_row is None:
                            continue
                    return _claim(candidate)
        candidates = {
            c for c in claimed if c not in moved and c not in matched
        }
        if backup_ids:
            # 本地行若对应备份里的另一条同档案记录，不能拿来认领本行；
            # 改挂到其它档案的记录仍可参与漂移配对
            filtered_candidates = set()
            for candidate_idx in candidates:
                if candidate_idx == raw_id or candidate_idx not in backup_ids:
                    filtered_candidates.add(candidate_idx)
                    continue
                expected_profile = (
                    backup_profiles.get(candidate_idx)
                    if backup_profiles else None
                )
                expected_created = (
                    backup_created.get(candidate_idx)
                    if backup_created else None
                )
                if (
                    expected_profile is None
                    or (
                        expected_created is not None
                        and expected_created
                        != str(created_at_value or "")
                    )
                    or _other_backup_row_survives(candidate_idx)
                ):
                    # 主键数值相同但属于备份里的另一秒记录，或那条备份
                    # 记录已在本地另有存活行：本候选仍是本行的存活行
                    filtered_candidates.add(candidate_idx)
                    continue
                candidate_meta = self._conn.execute(
                    f"SELECT profile FROM {table} WHERE id = ?",
                    (candidate_idx,),
                ).fetchone()
                if candidate_meta is None or _normalize_profile_name(
                    candidate_meta["profile"] or ""
                ) != expected_profile:
                    filtered_candidates.add(candidate_idx)
            candidates = filtered_candidates
        if (
            raw_id > 0
            and raw_id in candidates
            and (pre_existing_ids is None or raw_id in pre_existing_ids)
            and (
                backup_group_size <= 1
                or local_group_size >= backup_group_size
            )
        ):
            expected_profile = None
            if columns and columns[0] == "profile":
                expected_profile = _normalize_profile_name(values[0])
            elif match_where is not None and str(
                match_where[0]
            ).strip().lower().startswith("profile"):
                expected_profile = _normalize_profile_name(
                    match_where[1][0]
                )
            same_profile_candidate = True
            if expected_profile is not None:
                candidate_row = self._conn.execute(
                    f"SELECT profile FROM {table} WHERE id = ?",
                    (raw_id,),
                ).fetchone()
                if candidate_row is None or (
                    _normalize_profile_name(
                        candidate_row["profile"] or ""
                    )
                    != expected_profile
                ):
                    same_profile_candidate = False
            if same_profile_candidate and _same_backup_origin():
                # 本地存活行不少于备份行：这条备份行就是本地存活行的
                # 旧版本，即使内容/软字段都被编辑过也认领自身。
                # 本地确有缺行时仍走插入，优先补回被删记录
                return _claim(raw_id)
        if not candidates:
            state[identity] = claimed
            return None
        if fallback_where is not None:
            # 兜底配对只允许落在同一档案的候选行：同秒创建的另一位
            # 成员的提醒/记录不能被当成“本地编辑过的那一条”
            fragment, extra = fallback_where
            fallback_pool = set(candidates)
            ordered = sorted(candidates)
            placeholders = ",".join("?" for _ in ordered)
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE id IN ({placeholders})"
                f" AND {fragment}",
                (*ordered, *extra),
            ).fetchall()
            candidates = {int(row["id"]) for row in rows}
            if candidates:
                if backup_group_size > 1 and backup_anchor_size > 1:
                    # 同身份组内多条备份行共享同一锚点（如同秒同测量
                    # 时间的两条读数）：到最后一行时才认领唯一候选，
                    # 之前的行按被删记录补回
                    return _ambiguous()
            if not candidates:
                # 兜底锚点未命中：可能是本地编辑了锚点字段。先回到锚点
                # 前的候选，让软字段决定；没有软字段时只有备份身份组
                # 唯一（无同秒孪生歧义）才认领唯一候选
                candidates = fallback_pool
                if match_where is not None and candidates:
                    # 保留档案等硬约束：另一位成员的同秒行不能被当作
                    # 本行“改过内容”的存活版本
                    pool = sorted(candidates)
                    pool_placeholders = ",".join("?" for _ in pool)
                    scope_rows = self._conn.execute(
                        f"SELECT id FROM {table}"
                        f" WHERE id IN ({pool_placeholders})"
                        f" AND {match_where[0]}",
                        (*pool, *match_where[1]),
                    ).fetchall()
                    candidates = {
                        int(row["id"]) for row in scope_rows
                    }
                if soft_where is None:
                    if (
                        backup_group_size <= 1
                        and len(candidates) == 1
                        and (
                            _same_backup_origin()
                            or _candidate_edited(min(candidates))
                        )
                    ):
                        return _claim(min(candidates))
                    return _ambiguous()
        if not candidates:
            state[identity] = claimed
            return None
        if soft_where is not None and candidates:
            # 按不可编辑的软字段（标题/药名等）锚定本地编辑过的那条；
            # 没有软字段匹配时只允许认领未编辑过的行，避免把被删
            # 记录的旧版本插到存活记录旁边或反过来吞掉被删记录
            fragment, extra = soft_where
            ordered = sorted(candidates)
            placeholders = ",".join("?" for _ in ordered)
            soft_rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE id IN ({placeholders})"
                f" AND {fragment}",
                (*ordered, *extra),
            ).fetchall()
            soft = {int(row["id"]) for row in soft_rows}
            if soft:
                # 软字段命中：备份组只有一行，或该软字段在备份组内
                # 唯一时才能认领；同秒同名孪生仍不可区分
                if backup_group_size <= 1 or backup_soft_size == 1:
                    candidates = soft
                else:
                    return _ambiguous()
            else:
                # 备份组内存在多行同身份记录时，唯一候选可能是被删
                # 记录的孪生兄弟，到最后一行时才认领
                if backup_group_size > 1:
                    return _ambiguous()
                try:
                    fresh_rows = self._conn.execute(
                        f"SELECT id FROM {table}"
                        f" WHERE id IN ({placeholders})"
                        " AND updated_at = created_at",
                        tuple(ordered),
                    ).fetchall()
                    fresh = {int(row["id"]) for row in fresh_rows}
                except sqlite3.Error:
                    fresh = set()
                if fresh:
                    if not _same_backup_origin():
                        # 未编辑的同身份行在非同源库中更可能对应
                        # 备份里的另一条记录：按被删记录补回
                        return _ambiguous()
                    candidates = fresh
                # 组内只剩唯一候选：软字段已被本地编辑（改名/改标题），
                # 它仍是这条备份行的存活版本，不能把旧版本再插一份
        if len(candidates) == 1:
            candidate = min(candidates)
            if _content_owned_by_other(candidate):
                # 候选内容精确对应备份里的另一条记录：按被删记录补回
                return _ambiguous()
            if not _same_backup_origin() and not _candidate_edited(
                candidate
            ):
                # 非同源且看不到任何编辑证据：不能凭撞号认领遗留行
                return _ambiguous()
        return _claim(min(candidates))

    @staticmethod
    def _identity_group_sizes(
        raw_items: Any, limit: int, key_func
    ) -> Dict[tuple, int]:
        """统计备份中每个身份组的行数，供兜底认领判断是否有歧义。"""
        counts: Dict[tuple, int] = {}
        if not isinstance(raw_items, list):
            return counts
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            try:
                key = key_func(item)
            except Exception:
                key = None
            if key is None:
                continue
            counts[key] = counts.get(key, 0) + 1
        return counts

    @classmethod
    def _identity_group_meta(
        cls, raw_items: Any, limit: int, identity_key, soft_key
    ) -> Tuple[Dict[tuple, int], Dict[tuple, int]]:
        """返回（身份组行数, 身份组内软字段取值行数）两张表。"""
        return (
            cls._identity_group_sizes(raw_items, limit, identity_key),
            cls._identity_group_sizes(raw_items, limit, soft_key),
        )

    @staticmethod
    def _import_raw_ids(raw_items: Any, limit: int) -> set:
        """备份中出现的原始主键集合（用于拒绝跨备份行的漂移认领）。"""
        if not isinstance(raw_items, list):
            return set()
        result = set()
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            raw_id = _safe_db_id(item.get("id"))
            if raw_id > 0:
                result.add(raw_id)
        return result

    @staticmethod
    def _backup_content_fingerprint(
        item: Dict[str, Any], columns: tuple
    ) -> tuple:
        """按内容列生成备份行指纹（与 DB 读出值同样的字符串化口径）。"""
        values = []
        for column in columns:
            name = column
            default: Any = None
            match = re.match(
                r"IFNULL\(\s*(\w+)\s*,\s*([^)]+)\)", column
            )
            if match is not None:
                name = match.group(1)
                default = match.group(2).strip()
            value = item.get(name)
            if value is None:
                value = default
            if value is None:
                values.append("")
            elif isinstance(value, (int, float)) and not isinstance(
                value, bool
            ):
                values.append(f"{float(value):.10g}")
            else:
                values.append(" ".join(str(value).split()))
        return tuple(values)

    @staticmethod
    def _db_content_fingerprint(row: Any, columns: tuple) -> tuple:
        values = []
        for column in columns:
            try:
                value = row[column]
            except (IndexError, KeyError):
                value = None
            if value is None:
                values.append("")
            elif isinstance(value, (int, float)) and not isinstance(
                value, bool
            ):
                values.append(f"{float(value):.10g}")
            else:
                values.append(" ".join(str(value).split()))
        return tuple(values)

    @classmethod
    def _import_backup_meta(
        cls, raw_items: Any, limit: int, table: str = ""
    ) -> Dict[str, Any]:
        """备份原始主键 → （档案、创建时间、内容指纹）映射。"""
        ids = set()
        profiles: Dict[int, str] = {}
        created: Dict[int, str] = {}
        contents: Dict[int, tuple] = {}
        columns = IMPORT_CONTENT_COLUMNS.get(table)
        if not isinstance(raw_items, list):
            return {
                "ids": ids, "profiles": profiles, "created": created,
                "contents": contents,
            }
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            raw_id = _safe_db_id(item.get("id"))
            if raw_id <= 0:
                continue
            ids.add(raw_id)
            profiles[raw_id] = _normalize_profile_name(
                item.get("profile")
            )
            created[raw_id] = str(
                item.get("created_at") or ""
            ).strip()[:40]
            if columns:
                contents[raw_id] = cls._backup_content_fingerprint(
                    item, columns
                )
        return {
            "ids": ids, "profiles": profiles, "created": created,
            "contents": contents,
        }

    def _import_rows(
        self, table: str, raw_items: Any, limit: int, match_probe=None
    ) -> List[Dict[str, Any]]:
        """整理待导入行：能按「身份 + 内容」精确配对的行优先处理。

        同一身份（档案 + 创建时间）组里，精确内容匹配的行先与本地行配对，
        避免已被删除的行在兜底配对时抢占存活行的候选（主键漂移时尤其
        明显：备份主键被其它记录占用，删除一条同秒记录后再恢复会出现
        「删掉的没还原、存活的被复制」）。其次是主键仍在本地存在的行，
        其余行按主键升序处理，无 id 的 CSV 行保持原顺序。

        match_probe(raw) 返回 (where 子句, 参数) 时用于查询本地是否已有
        同身份同内容的行；返回 None 表示无法判断（如 CSV 没有创建时间）。
        """
        if not isinstance(raw_items, list):
            return []
        rows = [
            item for item in raw_items[:limit]
            if isinstance(item, dict)
        ]
        raw_ids = {_safe_db_id(item.get("id")) for item in rows}
        raw_ids.discard(0)
        existing: set = set()
        ordered = sorted(raw_ids)
        for start in range(0, len(ordered), 500):
            chunk = ordered[start:start + 500]
            placeholders = ",".join("?" for _ in chunk)
            with self._lock:
                found = self._conn.execute(
                    f"SELECT id FROM {table}"
                    f" WHERE id IN ({placeholders})",
                    chunk,
                ).fetchall()
            existing.update(int(row["id"]) for row in found)

        content_matched: Dict[int, bool] = {}
        moved_evidence: Dict[int, bool] = {}
        if match_probe is not None:
            for index, item in enumerate(rows):
                try:
                    probe = match_probe(item)
                except Exception:
                    probe = None
                if probe is None:
                    continue
                moved_where = None
                moved_params: Tuple[Any, ...] = ()
                if isinstance(probe, tuple) and len(probe) == 4:
                    where, params, moved_where, moved_params = probe
                elif isinstance(probe, tuple) and len(probe) == 2:
                    where, params = probe
                else:
                    continue
                try:
                    with self._lock:
                        found_row = self._conn.execute(
                            f"SELECT 1 FROM {table} WHERE {where} LIMIT 1",
                            params,
                        ).fetchone()
                except Exception:
                    continue
                if found_row is not None:
                    content_matched[index] = True
                raw_id = _safe_db_id(item.get("id"))
                if raw_id and moved_where and raw_id in existing:
                    try:
                        with self._lock:
                            moved_row = self._conn.execute(
                                f"SELECT 1 FROM {table}"
                                f" WHERE id = ? AND {moved_where} LIMIT 1",
                                (raw_id, *moved_params),
                            ).fetchone()
                    except Exception:
                        moved_row = None
                    if moved_row is not None:
                        moved_evidence[index] = True

        def _sort_tier(pair) -> int:
            index = pair[0]
            if moved_evidence.get(index):
                # 主键 + 创建时间 + 内容能确认同一条：先处理改挂行，
                # 否则同内容的已删除行会把它当成“已存在”吞掉
                return 0
            if content_matched.get(index):
                return 1
            return 2

        indexed = list(enumerate(rows))
        # 先处理内容可精确配对的行，其余按备份主键升序。主键仍在本地
        # 但内容已改的行不再提前处理：同秒组内“删一条+改另一条”时，
        # 提前处理被编辑的存活行会让最后一条被删行认领它
        indexed.sort(key=lambda pair: (
            _sort_tier(pair),
            _safe_db_id(pair[1].get("id")),
        ))
        return [item for _, item in indexed]

    def _remap_import_aliases(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """恢复备份时把已改名档案的旧名称解析到当前名称。"""
        profile_fields = (
            "sessions", "metric_targets", "health_metrics", "symptoms",
            "medications", "medication_logs", "medication_history",
            "vaccinations", "appointments", "lab_results", "allergies",
            "care_items", "visit_records", "conditions", "reminders",
            "usage_events",
        )
        cache: Dict[str, str] = {}

        def mapped(value: Any) -> str:
            name = str(value or "")
            if not name:
                return name
            if name not in cache:
                try:
                    cache[name] = self.resolve_profile_alias(name) or name
                except Exception:
                    cache[name] = name
            return cache[name]

        changed = False
        result = dict(data)
        for key in profile_fields:
            items = data.get(key)
            if not isinstance(items, list):
                continue
            new_items = []
            for item in items:
                if isinstance(item, dict) and item.get("profile"):
                    replacement = mapped(item.get("profile"))
                    if replacement != item.get("profile"):
                        item = {**item, "profile": replacement}
                        changed = True
                new_items.append(item)
            result[key] = new_items
        profiles = data.get("profiles")
        if isinstance(profiles, list):
            new_profiles = []
            for item in profiles:
                if isinstance(item, dict) and item.get("name"):
                    replacement = mapped(item.get("name"))
                    if replacement != item.get("name"):
                        item = {**item, "name": replacement}
                        changed = True
                new_profiles.append(item)
            result["profiles"] = new_profiles
        return result if changed else data

    def import_data(
        self, data: Any, max_sessions: int = 100000
    ) -> Dict[str, int]:
        """导入备份数据；已存在的会话编号与档案名称直接跳过，不覆盖现有记录。"""
        if not isinstance(data, dict):
            raise ValueError("备份文件格式不正确。")

        checksum = data.get("checksum")
        if isinstance(checksum, str) and checksum.strip():
            expected = checksum.strip()
            # 首个校验版本把缺失字段按 null 计入，升级后仍要能导入
            if expected not in {
                _backup_checksum(data),
                _legacy_backup_checksum(data),
            }:
                raise ValueError(
                    "备份校验失败，文件可能已损坏或被修改。"
                )

        # 恢复改名前的备份：把旧档案名解析到当前名称，避免整套数据
        # 作为另一个档案重新插入（校验在改写前完成）
        data = self._remap_import_aliases(data)

        raw_sessions = data.get("sessions")
        raw_profiles = data.get("profiles")
        raw_metrics = data.get("health_metrics")
        raw_targets = data.get("metric_targets")
        raw_reminders = data.get("reminders")
        raw_symptoms = data.get("symptoms")
        raw_medications = data.get("medications")
        raw_medication_logs = data.get("medication_logs")
        raw_medication_history = data.get("medication_history")
        raw_vaccinations = data.get("vaccinations")
        raw_appointments = data.get("appointments")
        raw_lab_results = data.get("lab_results")
        raw_allergies = data.get("allergies")
        raw_care_items = data.get("care_items")
        raw_visit_records = data.get("visit_records")
        raw_conditions = data.get("conditions")
        raw_audit = data.get("audit_log")
        raw_usage_events = data.get("usage_events")
        if raw_sessions is None and (
            isinstance(raw_profiles, list)
            or isinstance(raw_metrics, list)
            or isinstance(raw_targets, list)
            or isinstance(raw_reminders, list)
            or isinstance(raw_symptoms, list)
            or isinstance(raw_medications, list)
            or isinstance(raw_medication_logs, list)
            or isinstance(raw_medication_history, list)
            or isinstance(raw_vaccinations, list)
            or isinstance(raw_appointments, list)
            or isinstance(raw_lab_results, list)
            or isinstance(raw_allergies, list)
            or isinstance(raw_care_items, list)
            or isinstance(raw_visit_records, list)
            or isinstance(raw_conditions, list)
            or isinstance(raw_audit, list)
            or isinstance(raw_usage_events, list)
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
            and not isinstance(raw_medication_history, list)
            and not isinstance(raw_vaccinations, list)
            and not isinstance(raw_appointments, list)
            and not isinstance(raw_lab_results, list)
            and not isinstance(raw_allergies, list)
            and not isinstance(raw_care_items, list)
            and not isinstance(raw_visit_records, list)
            and not isinstance(raw_conditions, list)
            and not isinstance(raw_audit, list)
            and not isinstance(raw_usage_events, list)
        ):
            raise ValueError("备份文件中没有会话。")
        # 与 export_data/恢复预检共用同一份上限规则，超限时明确报错；
        # 已存在的会话整段跳过，其消息条数不受导入上限约束
        existing_session_ids = self.filter_existing_session_ids([
            raw.get("id")
            for raw in raw_sessions
            if isinstance(raw, dict)
        ])
        problems = _import_payload_problems(
            data, max_sessions, skip_session_ids=existing_session_ids
        )
        if problems:
            raise ValueError(problems[0])

        imported = 0
        skipped = 0
        invalid = 0
        messages_imported = 0
        revived_sessions: List[str] = []
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
                        sanitize_json(_clamp_usage_metadata(metadata)),
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
                # 显式恢复备份即重新启用该会话；等事务提交后再解除墓碑，
                # 避免中途校验失败回滚时把删除状态也一起丢掉
                revived_sessions.append(session_id)
                self._conn.execute(
                    "DELETE FROM deleted_sessions WHERE session_id = ?",
                    (session_id,),
                )
                # 恢复的会话不再需要长期记忆清理（否则队列循环会把它
                # 当作已删除旧数据整段清空）
                self._conn.execute(
                    "DELETE FROM memory_cleanup_queue WHERE session_id = ?",
                    (session_id,),
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
                for raw in raw_profiles[:IMPORT_LIST_LIMITS["profiles"]]:
                    if not isinstance(raw, dict):
                        continue
                    name = " ".join(
                        str(raw.get("name") or "").split()
                    )[:MAX_PROFILE_NAME_LENGTH]
                    cleaned = _normalize_profile_context(raw.get("context"))
                    if not name or cleaned is None:
                        invalid += 1
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
                    # 不能在这里删除 memory_cleanup_queue：队列行代表
                    # 尚未完成的远端删除，保留后清理循环会按删除时间窗
                    # 清掉旧摘要并保留恢复后新写入的记忆

            metrics_imported = 0
            # 备份行带 created_at 时按「档案 + 指标 + 创建时间」配对，
            # 同身份组内用内容（测量时间/数值/情境）锚定；未配对的行再按
            # 内容计数兜底，保持跨来源去重的既有契约
            existing_metric_counts: Dict[tuple, int] = {}
            imported_metric_counts: Dict[tuple, int] = {}
            metric_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_metrics, list):
                metric_pre_ids = self._existing_ids_locked(
                    "health_metrics"
                )
                metric_group_sizes = self._identity_group_sizes(
                    raw_metrics,
                    IMPORT_LIST_LIMITS["health_metrics"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("metric") or "").strip(),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                metric_anchor_sizes = self._identity_group_sizes(
                    raw_metrics,
                    IMPORT_LIST_LIMITS["health_metrics"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("metric") or "").strip(),
                        str(item.get("created_at") or "").strip()[:40],
                        str(item.get("measured_at") or ""),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                metric_backup_meta = self._import_backup_meta(
                    raw_metrics, IMPORT_LIST_LIMITS["health_metrics"], "health_metrics"
                )
                for raw in self._import_rows(
                    "health_metrics", raw_metrics,
                    IMPORT_LIST_LIMITS["health_metrics"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "metric", "created_at",
                            "measured_at", "value1", "IFNULL(value2, -1)",
                            "context", "note",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            str(r.get("metric") or "").strip(),
                            c,
                            r.get("measured_at"),
                            r.get("value1"),
                            -1 if r.get("value2") is None else r.get("value2"),
                            str(r.get("context") or ""),
                            str(r.get("note") or ""),
                        ),
                    ),
                ):
                    if not isinstance(raw, dict):
                        continue
                    normalized = _normalize_metric(
                        raw.get("profile", ""),
                        raw.get("metric", ""),
                        raw.get("value1"),
                        raw.get("value2"),
                        raw.get("measured_at", ""),
                        raw.get("note", ""),
                        raw.get("context", ""),
                    )
                    if normalized is None:
                        invalid += 1
                        continue
                    metric_key = (
                        normalized["profile"],
                        normalized["metric"],
                        normalized["measured_at"],
                        normalized["value1"],
                        normalized["value2"],
                        normalized["note"],
                        normalized["context"],
                    )
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        existing_metric_id = self._import_identity_match(
                            "health_metrics",
                            ("profile", "metric", "created_at"),
                            (
                                normalized["profile"],
                                normalized["metric"],
                                created_key,
                            ),
                            (
                                "measured_at", "value1",
                                "IFNULL(value2, -1)", "context", "note",
                            ),
                            (
                                normalized["measured_at"],
                                normalized["value1"],
                                -1 if normalized["value2"] is None
                                else normalized["value2"],
                                normalized["context"],
                                normalized["note"],
                            ),
                            metric_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=metric_pre_ids,
                            backup_ids=metric_backup_meta["ids"],
                            backup_profiles=metric_backup_meta["profiles"],
                            backup_created=metric_backup_meta["created"],
                            backup_contents=metric_backup_meta["contents"],
                            backup_group_size=metric_group_sizes.get(
                                (
                                    normalized["profile"],
                                    normalized["metric"],
                                    created_key,
                                ),
                                1,
                            ),
                            backup_anchor_size=metric_anchor_sizes.get(
                                (
                                    normalized["profile"],
                                    normalized["metric"],
                                    created_key,
                                    normalized["measured_at"],
                                ),
                                1,
                            ),
                            # 读数数值可被编辑：兜底配对用测量时间锚定，
                            # 避免被删的同秒读数抢走已编辑的存活读数
                            fallback_where=(
                                "measured_at = ?",
                                (normalized["measured_at"],),
                            ),
                        )
                        if existing_metric_id is not None:
                            # 配对成功的行也要计入内容计数：
                            # 同一内容的备份行与本地行按数量对账
                            imported_metric_counts[metric_key] = (
                                imported_metric_counts.get(metric_key, 0)
                                + 1
                            )
                            continue
                    if metric_key not in existing_metric_counts:
                        count_row = self._conn.execute(
                            """
                            SELECT COUNT(*) AS total FROM health_metrics
                             WHERE profile = ? AND metric = ?
                               AND measured_at = ?
                               AND value1 = ?
                               AND IFNULL(value2, -1) = IFNULL(?, -1)
                               AND note = ? AND context = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["metric"],
                                normalized["measured_at"],
                                normalized["value1"],
                                normalized["value2"],
                                normalized["note"],
                                normalized["context"],
                            ),
                        ).fetchone()
                        existing_metric_counts[metric_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_metric_counts.get(metric_key, 0)
                    imported_metric_counts[metric_key] = seen + 1
                    if seen < existing_metric_counts[metric_key]:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO health_metrics(
                            profile, metric, value1, value2, unit,
                            measured_at, context, note, created_at,
                            updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"],
                            normalized["metric"],
                            normalized["value1"],
                            normalized["value2"],
                            normalized["unit"],
                            normalized["measured_at"],
                            normalized["context"],
                            normalized["note"],
                            created_key or now,
                            str(
                                raw.get("updated_at")
                                or created_key or now
                            )[:40],
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
                        invalid += 1
                        continue
                    _, _, (low, high) = METRIC_TYPES[metric]
                    try:
                        first = float(raw.get("value1"))
                    except (TypeError, ValueError, OverflowError):
                        invalid += 1
                        continue
                    if not (low <= first <= high):
                        invalid += 1
                        continue
                    second: Optional[float] = None
                    if metric == "bp":
                        try:
                            second = float(raw.get("value2"))
                        except (TypeError, ValueError, OverflowError):
                            invalid += 1
                            continue
                        if not (30 <= second <= 200) or first <= second:
                            invalid += 1
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
            symptom_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_symptoms, list):
                symptom_pre_ids = self._existing_ids_locked("symptoms")
                symptom_group_sizes = self._identity_group_sizes(
                    raw_symptoms,
                    IMPORT_LIST_LIMITS["symptoms"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                symptom_anchor_sizes = self._identity_group_sizes(
                    raw_symptoms,
                    IMPORT_LIST_LIMITS["symptoms"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        str(item.get("recorded_at") or ""),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                symptom_backup_meta = self._import_backup_meta(
                    raw_symptoms, IMPORT_LIST_LIMITS["symptoms"], "symptoms"
                )
                for raw in self._import_rows(
                    "symptoms", raw_symptoms,
                    IMPORT_LIST_LIMITS["symptoms"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "symptom",
                            "recorded_at",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("symptom") or ""),
                            str(r.get("recorded_at") or ""),
                        ),
                    ),
                ):
                    if not str(raw.get("recorded_at") or "").strip():
                        invalid += 1
                        continue
                    normalized = _normalize_symptom(
                        raw.get("profile", ""),
                        raw.get("symptom", ""),
                        raw.get("severity"),
                        raw.get("recorded_at", ""),
                        raw.get("note", ""),
                    )
                    if normalized is None:
                        invalid += 1
                        continue
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        # 严重程度/备注可编辑：身份只用档案 + 创建时间，
                        # 同身份组内用症状名与记录时间锚定
                        exists = self._import_identity_match(
                            "symptoms",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            ("symptom", "recorded_at"),
                            (
                                normalized["symptom"],
                                normalized["recorded_at"],
                            ),
                            symptom_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=symptom_pre_ids,
                            backup_ids=symptom_backup_meta["ids"],
                            backup_profiles=symptom_backup_meta["profiles"],
                            backup_created=symptom_backup_meta["created"],
                            backup_contents=symptom_backup_meta["contents"],
                            backup_group_size=symptom_group_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                ),
                                1,
                            ),
                            backup_anchor_size=symptom_anchor_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["recorded_at"],
                                ),
                                1,
                            ),
                            # 严重程度/备注可编辑：兜底用记录时间锚定
                            fallback_where=(
                                "recorded_at = ?",
                                (normalized["recorded_at"],),
                            ),
                        )
                    else:
                        exists = self._conn.execute(
                            """
                            SELECT 1 FROM symptoms
                             WHERE profile = ? AND symptom = ?
                               AND severity = ? AND recorded_at = ?
                               AND note = ?
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
                            recorded_at, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"],
                            normalized["symptom"],
                            normalized["severity"],
                            normalized["note"],
                            normalized["recorded_at"],
                            created_key or now,
                            str(
                                raw.get("updated_at")
                                or created_key or now
                            )[:40],
                        ),
                    )
                    symptoms_imported += 1

            medications_imported = 0
            medication_id_map: Dict[int, Dict[str, Any]] = {}
            medication_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_medications, list):
                medication_pre_ids = self._existing_ids_locked(
                    "medications"
                )
                medication_group_sizes = self._identity_group_sizes(
                    raw_medications,
                    IMPORT_LIST_LIMITS["medications"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                medication_soft_sizes = self._identity_group_sizes(
                    raw_medications,
                    IMPORT_LIST_LIMITS["medications"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(str(item.get("name") or "").split()),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                medication_backup_meta = self._import_backup_meta(
                    raw_medications, IMPORT_LIST_LIMITS["medications"], "medications"
                )
                for raw in self._import_rows(
                    "medications", raw_medications,
                    IMPORT_LIST_LIMITS["medications"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "name", "dose",
                            "frequency", "COALESCE(started_at, '')",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("name") or ""),
                            str(r.get("dose") or ""),
                            str(r.get("frequency") or ""),
                            str(r.get("started_at") or ""),
                        ),
                    ),
                ):
                    normalized = _normalize_medication(
                        raw.get("profile", ""),
                        raw.get("name", ""),
                        raw.get("dose", ""),
                        raw.get("frequency", ""),
                        raw.get("note", ""),
                        raw.get("active", True),
                        raw.get("stock"),
                        raw.get("stock_unit", ""),
                        raw.get("low_stock_threshold", 0),
                        raw.get("expiry_date", ""),
                        raw.get("started_at", ""),
                        raw.get("stopped_at", ""),
                        raw.get("stop_reason", ""),
                    )
                    if normalized is None:
                        invalid += 1
                        continue
                    created_key = str(raw.get("created_at") or "").strip()[:40]
                    if created_key:
                        # 备份带创建时间：身份只用档案 + 创建时间，
                        # 同身份组内用名称/剂量/频次/开始日期锚定；
                        # 停药等状态编辑不会让旧记录在恢复时复活
                        existing_id = self._import_identity_match(
                            "medications",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            ("name", "dose", "frequency", "started_at"),
                            (
                                normalized["name"],
                                normalized["dose"],
                                normalized["frequency"],
                                normalized["started_at"],
                            ),
                            medication_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=medication_pre_ids,
                            backup_ids=medication_backup_meta["ids"],
                            backup_profiles=medication_backup_meta["profiles"],
                            backup_created=medication_backup_meta["created"],
                            backup_contents=medication_backup_meta["contents"],
                            backup_group_size=(
                                medication_group_sizes.get(
                                    (
                                        normalized["profile"],
                                        created_key,
                                    ),
                                    1,
                                )
                            ),
                            backup_soft_size=(
                                medication_soft_sizes.get(
                                    (
                                        normalized["profile"],
                                        created_key,
                                        normalized["name"],
                                    ),
                                    1,
                                )
                            ),
                            fallback_where=(
                                "name = ? AND dose = ? AND frequency = ?"
                                " AND started_at = ?",
                                (
                                    normalized["name"],
                                    normalized["dose"],
                                    normalized["frequency"],
                                    normalized["started_at"],
                                ),
                            ),
                            soft_where=(
                                "name = ?", (normalized["name"],)
                            ),
                        )
                        existing = (
                            {"id": existing_id}
                            if existing_id is not None else None
                        )
                    else:
                        # CSV 导入没有创建时间：按完整疗程字段判重，
                        # 同一药物的不同开始/停药日期必须各自保留
                        existing = self._conn.execute(
                            """
                            SELECT id FROM medications
                             WHERE profile = ? AND name = ? AND dose = ?
                               AND frequency = ?
                               AND COALESCE(started_at, '') = ?
                               AND COALESCE(stopped_at, '') = ?
                               AND stop_reason = ? AND active = ?
                            """,
                            (
                                normalized["profile"], normalized["name"],
                                normalized["dose"], normalized["frequency"],
                                normalized["started_at"],
                                normalized["stopped_at"],
                                normalized["stop_reason"],
                                normalized["active"],
                            ),
                        ).fetchone()
                    if existing is not None:
                        actual_id = int(existing["id"])
                    else:
                        created_at = created_key or now
                        updated_at = str(
                            raw.get("updated_at") or created_at
                        )[:40]
                        cursor = self._conn.execute(
                            """
                            INSERT INTO medications(
                                profile, name, dose, frequency, note, active,
                                stock, stock_unit, low_stock_threshold,
                                expiry_date, started_at, stopped_at,
                                stop_reason, created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                normalized["profile"], normalized["name"],
                                normalized["dose"], normalized["frequency"],
                                normalized["note"], normalized["active"],
                                normalized["stock"],
                                normalized["stock_unit"],
                                normalized["low_stock_threshold"],
                                normalized["expiry_date"],
                                normalized["started_at"],
                                normalized["stopped_at"],
                                normalized["stop_reason"],
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
                        invalid += 1
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
                            invalid += 1
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
                            invalid += 1
                            continue
                        target = {
                            "id": int(row["id"]),
                            "profile": row["profile"],
                            "name": row["name"],
                        }
                    note = " ".join(str(raw.get("note") or "").split())[:300]
                    skipped_flag = 1 if str(
                        raw.get("skipped")
                    ).lower() in {"1", "true", "on", "yes"} else 0
                    reason_key = ""
                    if skipped_flag:
                        reason_key = str(
                            raw.get("skip_reason") or ""
                        ).strip().lower()
                        if reason_key not in MEDICATION_SKIP_REASONS:
                            reason_key = "other"
                    created_at = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if not created_at:
                        created_at = now
                    log_key = (
                        target["id"], taken_at, note, skipped_flag,
                    )
                    if log_key not in existing_log_counts:
                        count_row = self._conn.execute(
                            """
                            SELECT COUNT(*) AS total FROM medication_logs
                             WHERE medication_id = ? AND taken_at = ?
                               AND note = ? AND skipped = ?
                            """,
                            (
                                target["id"], taken_at, note,
                                skipped_flag,
                            ),
                        ).fetchone()
                        existing_log_counts[log_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_log_counts.get(log_key, 0)
                    imported_log_counts[log_key] = seen + 1
                    if seen < existing_log_counts[log_key]:
                        continue
                    try:
                        stock_delta = float(raw.get("stock_delta") or 0)
                    except (TypeError, ValueError, OverflowError):
                        stock_delta = 0.0
                    if not math.isfinite(stock_delta):
                        stock_delta = 0.0
                    self._conn.execute(
                        """
                        INSERT INTO medication_logs(
                            medication_id, profile, taken_at, note,
                            stock_delta, skipped, skip_reason, created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            target["id"],
                            target["profile"],
                            taken_at,
                            note,
                            stock_delta,
                            skipped_flag,
                            reason_key,
                            created_at,
                        ),
                    )
                    medication_logs_imported += 1

            medication_history_imported = 0
            if isinstance(raw_medication_history, list):
                for raw in raw_medication_history[
                    :IMPORT_LIST_LIMITS["medication_history"]
                ]:
                    if not isinstance(raw, dict):
                        continue
                    try:
                        target = medication_id_map.get(
                            int(raw.get("medication_id"))
                        )
                    except (TypeError, ValueError, OverflowError):
                        target = None
                    if target is None:
                        invalid += 1
                        continue
                    action = " ".join(
                        str(raw.get("action") or "update").split()
                    )[:20] or "update"
                    detail = " ".join(
                        str(raw.get("detail") or "").split()
                    )[:300]
                    changed_at = str(raw.get("changed_at") or now)[:40]
                    exists = self._conn.execute(
                        """
                        SELECT 1 FROM medication_history
                         WHERE medication_id = ? AND action = ?
                           AND detail = ? AND changed_at = ?
                        """,
                        (target["id"], action, detail, changed_at),
                    ).fetchone()
                    if exists:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO medication_history(
                            medication_id, profile, action, detail, changed_at
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            target["id"], target["profile"],
                            action, detail, changed_at,
                        ),
                    )
                    medication_history_imported += 1

            vaccinations_imported = 0
            raw_vaccination_id_map: Dict[int, int] = {}
            vaccination_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_vaccinations, list):
                vaccination_pre_ids = self._existing_ids_locked(
                    "vaccinations"
                )
                (
                    vaccination_group_sizes,
                    vaccination_soft_sizes,
                ) = self._identity_group_meta(
                    raw_vaccinations,
                    IMPORT_LIST_LIMITS["vaccinations"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(
                            str(item.get("vaccine_name") or "").split()
                        ),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                vaccination_backup_meta = self._import_backup_meta(
                    raw_vaccinations, IMPORT_LIST_LIMITS["vaccinations"], "vaccinations"
                )
                for raw in self._import_rows(
                    "vaccinations", raw_vaccinations,
                    IMPORT_LIST_LIMITS["vaccinations"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "vaccine_name",
                            "dose_number", "vaccinated_at",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(
                                r.get("vaccine_name") or r.get("name") or ""
                            ),
                            r.get("dose_number") or 1,
                            str(r.get("vaccinated_at") or ""),
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_vaccination(
                            raw.get("profile") or "",
                            raw.get("vaccine_name") or raw.get("name") or "",
                            raw.get("dose_number") or 1,
                            raw.get("vaccinated_at") or "",
                            raw.get("next_due") or "",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    raw_id = _safe_db_id(raw.get("id"))
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        vaccination_id = self._import_identity_match(
                            "vaccinations",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            (
                                "vaccine_name", "dose_number",
                                "vaccinated_at",
                            ),
                            (
                                normalized["vaccine_name"],
                                normalized["dose_number"],
                                normalized["vaccinated_at"],
                            ),
                            vaccination_identity_claims,
                            raw_id=raw_id,
                            created_at_value=created_key,
                            pre_existing_ids=vaccination_pre_ids,
                            backup_ids=vaccination_backup_meta["ids"],
                            backup_profiles=vaccination_backup_meta["profiles"],
                            backup_created=vaccination_backup_meta["created"],
                            backup_contents=vaccination_backup_meta["contents"],
                            soft_where=(
                                "vaccine_name = ?",
                                (normalized["vaccine_name"],),
                            ),
                            backup_group_size=vaccination_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=vaccination_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["vaccine_name"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": vaccination_id}
                            if vaccination_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM vaccinations
                             WHERE profile = ? AND vaccine_name = ?
                               AND dose_number = ? AND vaccinated_at = ?
                               AND next_due = ? AND note = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["vaccine_name"],
                                normalized["dose_number"],
                                normalized["vaccinated_at"],
                                normalized["next_due"],
                                normalized["note"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        vaccination_id = int(duplicate["id"])
                    else:
                        cursor = self._conn.execute(
                            """
                            INSERT INTO vaccinations(
                                profile, vaccine_name, dose_number,
                                vaccinated_at, next_due, note,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                normalized["profile"],
                                normalized["vaccine_name"],
                                normalized["dose_number"],
                                normalized["vaccinated_at"],
                                normalized["next_due"],
                                normalized["note"],
                                created_key or now,
                                str(raw.get("updated_at") or now)[:40],
                            ),
                        )
                        vaccination_id = int(cursor.lastrowid)
                        vaccinations_imported += 1
                    if raw_id > 0 and vaccination_id > 0:
                        raw_vaccination_id_map[raw_id] = vaccination_id

            appointments_imported = 0
            imported_appointment_ids: set = set()
            raw_appointment_id_map: Dict[int, int] = {}
            appointment_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_appointments, list):
                appointment_pre_ids = self._existing_ids_locked(
                    "appointments"
                )
                (
                    appointment_group_sizes,
                    appointment_soft_sizes,
                ) = self._identity_group_meta(
                    raw_appointments,
                    IMPORT_LIST_LIMITS["appointments"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(str(item.get("title") or "").split()),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                appointment_backup_meta = self._import_backup_meta(
                    raw_appointments, IMPORT_LIST_LIMITS["appointments"], "appointments"
                )
                for raw in self._import_rows(
                    "appointments", raw_appointments,
                    IMPORT_LIST_LIMITS["appointments"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "title",
                            "scheduled_at", "department", "doctor",
                            "location",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("title") or ""),
                            str(r.get("scheduled_at") or ""),
                            str(r.get("department") or ""),
                            str(r.get("doctor") or ""),
                            str(r.get("location") or ""),
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_appointment(
                            raw.get("profile") or "",
                            raw.get("title") or "",
                            raw.get("department") or "",
                            raw.get("doctor") or "",
                            raw.get("location") or "",
                            raw.get("scheduled_at") or "",
                            raw.get("status") or "upcoming",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    raw_id = _safe_db_id(raw.get("id"))
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        appointment_id = self._import_identity_match(
                            "appointments",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            (
                                "title", "scheduled_at", "department",
                                "doctor", "location",
                            ),
                            (
                                normalized["title"],
                                normalized["scheduled_at"],
                                normalized["department"],
                                normalized["doctor"],
                                normalized["location"],
                            ),
                            appointment_identity_claims,
                            raw_id=raw_id,
                            created_at_value=created_key,
                            pre_existing_ids=appointment_pre_ids,
                            backup_ids=appointment_backup_meta["ids"],
                            backup_profiles=appointment_backup_meta["profiles"],
                            backup_created=appointment_backup_meta["created"],
                            backup_contents=appointment_backup_meta["contents"],
                            soft_where=(
                                "title = ?", (normalized["title"],)
                            ),
                            backup_group_size=appointment_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=appointment_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["title"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": appointment_id}
                            if appointment_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM appointments
                             WHERE profile = ? AND title = ?
                               AND scheduled_at = ? AND department = ?
                               AND doctor = ? AND location = ?
                               AND status = ? AND note = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["title"],
                                normalized["scheduled_at"],
                                normalized["department"],
                                normalized["doctor"],
                                normalized["location"],
                                normalized["status"],
                                normalized["note"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        appointment_id = int(duplicate["id"])
                    else:
                        cursor = self._conn.execute(
                            """
                            INSERT INTO appointments(
                                profile, title, department, doctor, location,
                                scheduled_at, status, note,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                normalized["profile"], normalized["title"],
                                normalized["department"],
                                normalized["doctor"],
                                normalized["location"],
                                normalized["scheduled_at"],
                                normalized["status"], normalized["note"],
                                created_key or now,
                                str(raw.get("updated_at") or now)[:40],
                            ),
                        )
                        appointment_id = int(cursor.lastrowid)
                        imported_appointment_ids.add(appointment_id)
                        appointments_imported += 1
                    if raw_id > 0 and appointment_id > 0:
                        raw_appointment_id_map[raw_id] = appointment_id

            lab_results_imported = 0
            existing_lab_counts: Dict[tuple, int] = {}
            imported_lab_counts: Dict[tuple, int] = {}
            lab_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_lab_results, list):
                lab_pre_ids = self._existing_ids_locked("lab_results")
                lab_group_sizes, lab_soft_sizes = self._identity_group_meta(
                    raw_lab_results,
                    IMPORT_LIST_LIMITS["lab_results"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(str(item.get("panel") or "").split()),
                        " ".join(str(item.get("item") or "").split()),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                lab_backup_meta = self._import_backup_meta(
                    raw_lab_results, IMPORT_LIST_LIMITS["lab_results"], "lab_results"
                )
                for raw in self._import_rows(
                    "lab_results", raw_lab_results,
                    IMPORT_LIST_LIMITS["lab_results"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "panel", "item",
                            "tested_at", "value", "unit",
                            "IFNULL(ref_low, -1e308)",
                            "IFNULL(ref_high, -1e308)",
                            "lab_name", "note",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("panel") or ""),
                            str(r.get("item") or ""),
                            str(r.get("tested_at") or ""),
                            r.get("value"),
                            str(r.get("unit") or ""),
                            -1e308 if r.get("ref_low") is None
                            else r.get("ref_low"),
                            -1e308 if r.get("ref_high") is None
                            else r.get("ref_high"),
                            str(r.get("lab_name") or ""),
                            str(r.get("note") or ""),
                        ),
                    ),
                ):
                    if not isinstance(raw, dict):
                        continue
                    try:
                        normalized = _normalize_lab_result(
                            raw.get("profile") or "",
                            raw.get("panel") or "",
                            raw.get("item") or "",
                            raw.get("value"),
                            raw.get("unit") or "",
                            raw.get("ref_low"),
                            raw.get("ref_high"),
                            raw.get("tested_at") or "",
                            raw.get("lab_name") or "",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        # 备份带创建时间：身份用「档案 + 创建时间」、内容锚定。
                        # 修改化验值后恢复不会把旧值补回；删除的读数仍按
                        # 原内容还原
                        ref_low_key = (
                            -1e308 if normalized["ref_low"] is None
                            else normalized["ref_low"]
                        )
                        ref_high_key = (
                            -1e308 if normalized["ref_high"] is None
                            else normalized["ref_high"]
                        )
                        existing_lab_id = self._import_identity_match(
                            "lab_results",
                            ("profile", "created_at"),
                            (normalized["profile"], created_key),
                            (
                                "panel", "item", "tested_at", "value",
                                "unit", "IFNULL(ref_low, -1e308)",
                                "IFNULL(ref_high, -1e308)",
                                "lab_name", "note",
                            ),
                            (
                                normalized["panel"], normalized["item"],
                                normalized["tested_at"],
                                normalized["value"], normalized["unit"],
                                ref_low_key, ref_high_key,
                                normalized["lab_name"], normalized["note"],
                            ),
                            lab_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=lab_pre_ids,
                            backup_ids=lab_backup_meta["ids"],
                            backup_profiles=lab_backup_meta["profiles"],
                            backup_created=lab_backup_meta["created"],
                            backup_contents=lab_backup_meta["contents"],
                            soft_where=(
                                "panel = ? AND item = ?",
                                (normalized["panel"], normalized["item"]),
                            ),
                            backup_group_size=lab_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=lab_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["panel"],
                                    normalized["item"],
                                ),
                                1,
                            ),
                        )
                        if existing_lab_id is not None:
                            continue
                        self._conn.execute(
                            """
                            INSERT INTO lab_results(
                                profile, panel, item, value, unit, ref_low,
                                ref_high, tested_at, lab_name, note,
                                created_at, updated_at
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                normalized["profile"], normalized["panel"],
                                normalized["item"], normalized["value"],
                                normalized["unit"], normalized["ref_low"],
                                normalized["ref_high"],
                                normalized["tested_at"],
                                normalized["lab_name"], normalized["note"],
                                created_key,
                                str(raw.get("updated_at") or now)[:40],
                            ),
                        )
                        lab_results_imported += 1
                        continue
                    # CSV 没有创建时间：按完整内容计数判重
                    conditions = [
                        "profile = ?", "panel = ?", "item = ?",
                        "tested_at = ?", "value = ?", "unit = ?",
                        "ref_low IS ?", "ref_high IS ?",
                        "lab_name = ?", "note = ?",
                    ]
                    params: List[Any] = [
                        normalized["profile"], normalized["panel"],
                        normalized["item"], normalized["tested_at"],
                        normalized["value"], normalized["unit"],
                        normalized["ref_low"], normalized["ref_high"],
                        normalized["lab_name"], normalized["note"],
                    ]
                    lab_key = (
                        normalized["profile"], normalized["panel"],
                        normalized["item"], normalized["tested_at"],
                        normalized["value"], normalized["unit"],
                        normalized["ref_low"], normalized["ref_high"],
                        normalized["lab_name"], normalized["note"],
                    )
                    if lab_key not in existing_lab_counts:
                        count_row = self._conn.execute(
                            "SELECT COUNT(*) AS total FROM lab_results "
                            "WHERE " + " AND ".join(conditions),
                            params,
                        ).fetchone()
                        existing_lab_counts[lab_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_lab_counts.get(lab_key, 0)
                    imported_lab_counts[lab_key] = seen + 1
                    if seen < existing_lab_counts[lab_key]:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO lab_results(
                            profile, panel, item, value, unit, ref_low,
                            ref_high, tested_at, lab_name, note,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"], normalized["panel"],
                            normalized["item"], normalized["value"],
                            normalized["unit"], normalized["ref_low"],
                            normalized["ref_high"], normalized["tested_at"],
                            normalized["lab_name"], normalized["note"],
                            now, str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    lab_results_imported += 1

            allergies_imported = 0
            allergy_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_allergies, list):
                allergy_pre_ids = self._existing_ids_locked("allergies")
                (
                    allergy_group_sizes,
                    allergy_soft_sizes,
                ) = self._identity_group_meta(
                    raw_allergies,
                    IMPORT_LIST_LIMITS["allergies"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(
                            str(item.get("substance") or "").split()
                        ),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                allergy_backup_meta = self._import_backup_meta(
                    raw_allergies, IMPORT_LIST_LIMITS["allergies"], "allergies"
                )
                for raw in self._import_rows(
                    "allergies", raw_allergies,
                    IMPORT_LIST_LIMITS["allergies"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "substance",
                            "kind", "noted_at",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("substance") or ""),
                            str(r.get("kind") or "drug"),
                            str(r.get("noted_at") or ""),
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_allergy(
                            raw.get("profile") or "",
                            raw.get("substance") or "",
                            raw.get("reaction") or "",
                            raw.get("severity") or "moderate",
                            raw.get("kind") or "drug",
                            raw.get("active", True),
                            raw.get("noted_at") or "",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    # 同一过敏原可能有多次不同反应（如先皮疹后休克），
                    # 身份只用档案 + 创建时间；反应/程度/物质可编辑，
                    # 本地修改后恢复备份不能重复插入
                    if created_key:
                        existing_id = self._import_identity_match(
                            "allergies",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            ("substance", "kind", "noted_at"),
                            (
                                normalized["substance"],
                                normalized["kind"],
                                normalized["noted_at"],
                            ),
                            allergy_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=allergy_pre_ids,
                            backup_ids=allergy_backup_meta["ids"],
                            backup_profiles=allergy_backup_meta["profiles"],
                            backup_created=allergy_backup_meta["created"],
                            backup_contents=allergy_backup_meta["contents"],
                            soft_where=(
                                "substance = ?",
                                (normalized["substance"],),
                            ),
                            backup_group_size=allergy_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=allergy_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["substance"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": existing_id}
                            if existing_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM allergies
                             WHERE profile = ? AND substance = ?
                               AND kind = ? AND reaction = ?
                               AND severity = ? AND noted_at = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["substance"],
                                normalized["kind"],
                                normalized["reaction"],
                                normalized["severity"],
                                normalized["noted_at"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO allergies(
                            profile, substance, reaction, severity, kind,
                            active, noted_at, note, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"], normalized["substance"],
                            normalized["reaction"], normalized["severity"],
                            normalized["kind"], normalized["active"],
                            normalized["noted_at"], normalized["note"],
                            created_key or now,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    allergies_imported += 1

            care_items_imported = 0
            care_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_care_items, list):
                care_pre_ids = self._existing_ids_locked("care_items")
                care_group_sizes, care_soft_sizes = (
                    self._identity_group_meta(
                        raw_care_items,
                        IMPORT_LIST_LIMITS["care_items"],
                        lambda item: (
                            _normalize_profile_name(item.get("profile")),
                            str(item.get("created_at") or "").strip()[:40],
                        ) if str(
                            item.get("created_at") or ""
                        ).strip() else None,
                        lambda item: (
                            _normalize_profile_name(item.get("profile")),
                            str(item.get("created_at") or "").strip()[:40],
                            " ".join(
                                str(item.get("title") or "").split()
                            ),
                        ) if str(
                            item.get("created_at") or ""
                        ).strip() else None,
                    )
                )
                care_backup_meta = self._import_backup_meta(
                    raw_care_items, IMPORT_LIST_LIMITS["care_items"], "care_items"
                )
                for raw in self._import_rows(
                    "care_items", raw_care_items,
                    IMPORT_LIST_LIMITS["care_items"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "title",
                            "interval_days",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("title") or ""),
                            r.get("interval_days") or 30,
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_care_item(
                            raw.get("profile") or "",
                            raw.get("title") or "",
                            raw.get("interval_days") or 30,
                            raw.get("last_done_at") or "",
                            raw.get("next_due_at") or "",
                            raw.get("note") or "",
                            raw.get("active", True),
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        existing_id = self._import_identity_match(
                            "care_items",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            ("title", "interval_days"),
                            (
                                normalized["title"],
                                normalized["interval_days"],
                            ),
                            care_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=care_pre_ids,
                            backup_ids=care_backup_meta["ids"],
                            backup_profiles=care_backup_meta["profiles"],
                            backup_created=care_backup_meta["created"],
                            backup_contents=care_backup_meta["contents"],
                            soft_where=(
                                "title = ?", (normalized["title"],)
                            ),
                            backup_group_size=care_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=care_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["title"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": existing_id}
                            if existing_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM care_items
                             WHERE profile = ? AND title = ?
                               AND interval_days = ? AND last_done_at = ?
                               AND next_due_at = ? AND note = ?
                               AND active = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["title"],
                                normalized["interval_days"],
                                normalized["last_done_at"],
                                normalized["next_due_at"],
                                normalized["note"],
                                normalized["active"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO care_items(
                            profile, title, interval_days, last_done_at,
                            next_due_at, note, active, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"], normalized["title"],
                            normalized["interval_days"],
                            normalized["last_done_at"],
                            normalized["next_due_at"], normalized["note"],
                            normalized["active"],
                            created_key or now,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    care_items_imported += 1

            visit_records_imported = 0
            visit_link_updates: List[Tuple[int, int, int, int, bool]] = []
            visit_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_visit_records, list):
                visit_pre_ids = self._existing_ids_locked("visit_records")
                visit_group_sizes, visit_soft_sizes = (
                    self._identity_group_meta(
                        raw_visit_records,
                        IMPORT_LIST_LIMITS["visit_records"],
                        lambda item: (
                            _normalize_profile_name(item.get("profile")),
                            str(item.get("created_at") or "").strip()[:40],
                        ) if str(
                            item.get("created_at") or ""
                        ).strip() else None,
                        lambda item: (
                            _normalize_profile_name(item.get("profile")),
                            str(item.get("created_at") or "").strip()[:40],
                            str(item.get("visit_date") or "").strip()[:10],
                            " ".join(
                                str(item.get("diagnosis") or "").split()
                            ),
                        ) if str(
                            item.get("created_at") or ""
                        ).strip() else None,
                    )
                )
                visit_backup_meta = self._import_backup_meta(
                    raw_visit_records, IMPORT_LIST_LIMITS["visit_records"], "visit_records"
                )
                for raw in self._import_rows(
                    "visit_records", raw_visit_records,
                    IMPORT_LIST_LIMITS["visit_records"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "visit_date",
                            "diagnosis", "department", "doctor",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("visit_date") or ""),
                            str(r.get("diagnosis") or ""),
                            str(r.get("department") or ""),
                            str(r.get("doctor") or ""),
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_visit_record(
                            raw.get("profile") or "",
                            raw.get("appointment_id") or 0,
                            raw.get("visit_date") or "",
                            raw.get("department") or "",
                            raw.get("doctor") or "",
                            raw.get("facility") or "",
                            raw.get("diagnosis") or "",
                            raw.get("advice") or "",
                            raw.get("prescription") or "",
                            raw.get("next_followup_date") or "",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    raw_appointment = _safe_db_id(
                        raw.get("appointment_id")
                    )
                    mapped_appointment_id = (
                        raw_appointment_id_map.get(raw_appointment, 0)
                        if raw_appointment else 0
                    )
                    raw_followup_appointment = _safe_db_id(
                        raw.get("followup_appointment_id")
                    )
                    mapped_followup_appointment_id = (
                        raw_appointment_id_map.get(
                            raw_followup_appointment, 0
                        )
                        if raw_followup_appointment else 0
                    )
                    raw_followup_reminder = _safe_db_id(
                        raw.get("followup_reminder_id")
                    )
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    if created_key:
                        # 就诊记录的内容可编辑：身份只用档案 + 创建时间，
                        # 改诊断/医嘱后恢复不会复制旧记录
                        duplicate_id = self._import_identity_match(
                            "visit_records",
                            ("profile", "created_at"),
                            (normalized["profile"], created_key),
                            (
                                "visit_date", "diagnosis", "department",
                                "doctor",
                            ),
                            (
                                normalized["visit_date"],
                                normalized["diagnosis"],
                                normalized["department"],
                                normalized["doctor"],
                            ),
                            visit_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=visit_pre_ids,
                            backup_ids=visit_backup_meta["ids"],
                            backup_profiles=visit_backup_meta["profiles"],
                            backup_created=visit_backup_meta["created"],
                            backup_contents=visit_backup_meta["contents"],
                            soft_where=(
                                "visit_date = ? AND diagnosis = ?",
                                (
                                    normalized["visit_date"],
                                    normalized["diagnosis"],
                                ),
                            ),
                            backup_group_size=visit_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=visit_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["visit_date"],
                                    normalized["diagnosis"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": duplicate_id}
                            if duplicate_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM visit_records
                             WHERE profile = ? AND visit_date = ?
                               AND diagnosis = ? AND department = ?
                               AND doctor = ? AND facility = ?
                               AND advice = ? AND prescription = ?
                               AND note = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["visit_date"],
                                normalized["diagnosis"],
                                normalized["department"],
                                normalized["doctor"],
                                normalized["facility"],
                                normalized["advice"],
                                normalized["prescription"],
                                normalized["note"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        # 本地已有该就诊：仍要记录 id 映射，供预约备注
                        # 归属标记改写与缺失的复诊链接回填
                        existing_visit_id = _safe_db_id(duplicate["id"])
                        if existing_visit_id and (
                            mapped_followup_appointment_id
                            or raw_followup_reminder
                        ):
                            visit_link_updates.append((
                                existing_visit_id,
                                mapped_followup_appointment_id,
                                raw_followup_reminder,
                                _safe_db_id(raw.get("id")),
                                False,
                            ))
                        continue
                    cursor = self._conn.execute(
                        """
                        INSERT INTO visit_records(
                            profile, appointment_id, visit_date, department,
                            doctor, facility, diagnosis, advice,
                            prescription, next_followup_date,
                            followup_appointment_id, followup_reminder_id,
                            note, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"],
                            mapped_appointment_id,
                            normalized["visit_date"],
                            normalized["department"],
                            normalized["doctor"], normalized["facility"],
                            normalized["diagnosis"], normalized["advice"],
                            normalized["prescription"],
                            normalized["next_followup_date"],
                            mapped_followup_appointment_id,
                            0,
                            normalized["note"],
                            created_key or now,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    new_visit_id = int(cursor.lastrowid or 0)
                    if new_visit_id and (
                        mapped_followup_appointment_id
                        or raw_followup_reminder
                    ):
                        visit_link_updates.append((
                            new_visit_id,
                            mapped_followup_appointment_id,
                            raw_followup_reminder,
                            _safe_db_id(raw.get("id")),
                            True,
                        ))
                    visit_records_imported += 1

            conditions_imported = 0
            condition_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_conditions, list):
                condition_pre_ids = self._existing_ids_locked("conditions")
                (
                    condition_group_sizes,
                    condition_soft_sizes,
                ) = self._identity_group_meta(
                    raw_conditions,
                    IMPORT_LIST_LIMITS["conditions"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(str(item.get("name") or "").split()),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                condition_backup_meta = self._import_backup_meta(
                    raw_conditions, IMPORT_LIST_LIMITS["conditions"], "conditions"
                )
                for raw in self._import_rows(
                    "conditions", raw_conditions,
                    IMPORT_LIST_LIMITS["conditions"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "name",
                            "diagnosed_at",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("name") or ""),
                            str(r.get("diagnosed_at") or ""),
                        ),
                    ),
                ):
                    try:
                        normalized = _normalize_condition(
                            raw.get("profile") or "",
                            raw.get("name") or "",
                            raw.get("icd10") or "",
                            raw.get("diagnosed_at") or "",
                            raw.get("status") or "active",
                            raw.get("severity") or "moderate",
                            raw.get("note") or "",
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    created_key = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    # 同名慢病可能是不同次诊断；身份只用档案 + 创建时间，
                    # 名称/状态/严重程度/诊断日期可编辑，不参与判重
                    if created_key:
                        existing_id = self._import_identity_match(
                            "conditions",
                            ("profile", "created_at"),
                            (
                                normalized["profile"],
                                created_key,
                            ),
                            ("name", "diagnosed_at"),
                            (
                                normalized["name"],
                                normalized["diagnosed_at"],
                            ),
                            condition_identity_claims,
                            raw_id=_safe_db_id(raw.get("id")),
                            created_at_value=created_key,
                            pre_existing_ids=condition_pre_ids,
                            backup_ids=condition_backup_meta["ids"],
                            backup_profiles=condition_backup_meta["profiles"],
                            backup_created=condition_backup_meta["created"],
                            backup_contents=condition_backup_meta["contents"],
                            soft_where=(
                                "name = ?", (normalized["name"],)
                            ),
                            backup_group_size=condition_group_sizes.get(
                                (normalized["profile"], created_key), 1
                            ),
                            backup_soft_size=condition_soft_sizes.get(
                                (
                                    normalized["profile"],
                                    created_key,
                                    normalized["name"],
                                ),
                                1,
                            ),
                        )
                        duplicate = (
                            {"id": existing_id}
                            if existing_id is not None else None
                        )
                    else:
                        duplicate = self._conn.execute(
                            """
                            SELECT id FROM conditions
                             WHERE profile = ? AND name = ?
                               AND diagnosed_at = ? AND status = ?
                               AND severity = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["name"],
                                normalized["diagnosed_at"],
                                normalized["status"],
                                normalized["severity"],
                            ),
                        ).fetchone()
                    if duplicate is not None:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO conditions(
                            profile, name, icd10, diagnosed_at, status,
                            severity, note, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            normalized["profile"], normalized["name"],
                            normalized["icd10"],
                            normalized["diagnosed_at"],
                            normalized["status"],
                            normalized["severity"], normalized["note"],
                            created_key or now,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    conditions_imported += 1

            reminders_imported = 0
            imported_reminder_ids: set = set()
            reminder_links: List[Tuple[int, int]] = []
            raw_reminder_id_map: Dict[int, int] = {}
            existing_reminder_counts: Dict[tuple, int] = {}
            imported_reminder_counts: Dict[tuple, int] = {}
            reminder_identity_claims: Dict[tuple, set] = {}
            pre_existing_reminder_ids = {
                int(row["id"])
                for row in self._conn.execute(
                    "SELECT id FROM reminders"
                ).fetchall()
            }
            reminder_backup_meta = self._import_backup_meta(
                raw_reminders, IMPORT_LIST_LIMITS["reminders"], "reminders"
            )
            if isinstance(raw_reminders, list):
                reminder_group_sizes = self._identity_group_sizes(
                    raw_reminders,
                    IMPORT_LIST_LIMITS["reminders"],
                    lambda item: (
                        str(item.get("created_at") or "").strip()[:40],
                        _normalize_profile_name(item.get("profile")),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                reminder_soft_sizes = self._identity_group_sizes(
                    raw_reminders,
                    IMPORT_LIST_LIMITS["reminders"],
                    lambda item: (
                        str(item.get("created_at") or "").strip()[:40],
                        _normalize_profile_name(item.get("profile")),
                        " ".join(
                            str(item.get("title") or "").split()
                        )[:MAX_REMINDER_TITLE_LENGTH],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                for raw in self._import_rows(
                    "reminders", raw_reminders,
                    IMPORT_LIST_LIMITS["reminders"],
                    match_probe=lambda raw: _import_probe_spec(
                        raw,
                        (
                            "profile", "created_at", "due_at",
                        ),
                        lambda r, c: (
                            _normalize_profile_name(r.get("profile")),
                            c,
                            str(r.get("due_at") or ""),
                        ),
                    ),
                ):
                    if not isinstance(raw, dict):
                        continue
                    title = " ".join(
                        str(raw.get("title") or "").split()
                    )[:MAX_REMINDER_TITLE_LENGTH]
                    if not title:
                        invalid += 1
                        continue
                    try:
                        due_at = _normalize_reminder_due(raw.get("due_at"))
                    except ValueError:
                        invalid += 1
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
                        repeat_days = _normalize_reminder_repeat_days(
                            raw.get("repeat_days")
                        )
                        repeat_until = _normalize_reminder_repeat_until(
                            raw.get("repeat_until")
                        )
                        # 重复截止早于到期日会让规则永不生效：
                        # 导入时按无效行跳过，而不是静默变成无限重复
                        _validate_repeat_window(
                            due_at, repeat_days, repeat_until
                        )
                    except ValueError:
                        invalid += 1
                        continue
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
                    if not medication_id and title.startswith("用药："):
                        # 旧备份没有 medication_id 字段，按「用药：药名」
                        # 标题恢复与本次导入药物的联动
                        med_row = self._conn.execute(
                            """
                            SELECT id FROM medications
                             WHERE profile = ?
                               AND substr('用药：' || name, 1, 120) = ?
                             ORDER BY id LIMIT 1
                            """,
                            (profile, title),
                        ).fetchone()
                        if med_row is not None:
                            medication_id = int(med_row["id"])
                    raw_vaccination_id = _safe_db_id(
                        raw.get("vaccination_id")
                    )
                    vaccination_id = raw_vaccination_id_map.get(
                        raw_vaccination_id, 0
                    )
                    if not vaccination_id and raw_vaccination_id > 0:
                        # CSV 只带疫苗 id：确认同档案后关联到库中已有记录
                        vac_row = self._conn.execute(
                            "SELECT id, profile FROM vaccinations WHERE id = ?",
                            (raw_vaccination_id,),
                        ).fetchone()
                        if (
                            vac_row is not None
                            and _normalize_profile_name(vac_row["profile"])
                            == profile
                        ):
                            vaccination_id = int(vac_row["id"])
                    raw_appointment_id = _safe_db_id(
                        raw.get("appointment_id")
                    )
                    appointment_id = raw_appointment_id_map.get(
                        raw_appointment_id, 0
                    )
                    if not appointment_id and raw_appointment_id > 0:
                        appt_row = self._conn.execute(
                            "SELECT id, profile FROM appointments WHERE id = ?",
                            (raw_appointment_id,),
                        ).fetchone()
                        if (
                            appt_row is not None
                            and _normalize_profile_name(appt_row["profile"])
                            == profile
                        ):
                            appointment_id = int(appt_row["id"])
                    done = 1 if raw.get("done") in (1, True, "1") else 0
                    created_at = str(raw.get("created_at") or now)[:40]
                    updated_at = str(raw.get("updated_at") or created_at)[:40]

                    reminder_id = _safe_db_id(raw.get("id"))
                    raw_created = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    has_created = bool(raw_created)
                    if has_created:
                        # 提醒带创建时间：身份用创建时间、内容用到期日锚定。
                        # 标题/备注/档案可被本地编辑，恢复旧备份不能复制
                        # 出改前的旧版本
                        existing_reminder_id = self._import_identity_match(
                            "reminders",
                            ("created_at",),
                            (raw_created,),
                            ("due_at",),
                            (due_at,),
                            reminder_identity_claims,
                            raw_id=reminder_id,
                            created_at_value=raw_created,
                            fallback_where=("profile = ?", (profile,)),
                            pre_existing_ids=pre_existing_reminder_ids,
                            backup_ids=reminder_backup_meta["ids"],
                            backup_profiles=reminder_backup_meta["profiles"],
                            backup_created=reminder_backup_meta["created"],
                            backup_contents=reminder_backup_meta["contents"],
                            match_where=("profile = ?", (profile,)),
                            soft_where=("title = ?", (title,)),
                            backup_group_size=reminder_group_sizes.get(
                                (raw_created, profile), 1
                            ),
                            backup_soft_size=reminder_soft_sizes.get(
                                (raw_created, profile, title), 1
                            ),
                        )
                        if existing_reminder_id is not None:
                            if reminder_id > 0:
                                raw_reminder_id_map.setdefault(
                                    reminder_id, existing_reminder_id
                                )
                            continue
                    else:
                        # CSV 没有创建时间：退回标题/档案/备注/到期日计数判重
                        identity_key = (
                            title, profile, note, session_id, due_at,
                        )
                        if identity_key not in existing_reminder_counts:
                            count_row = self._conn.execute(
                                """
                                SELECT COUNT(*) AS total FROM reminders
                                 WHERE title = ? AND profile = ?
                                   AND note = ? AND session_id = ?
                                   AND due_at = ?
                                """,
                                (
                                    title, profile, note, session_id,
                                    due_at,
                                ),
                            ).fetchone()
                            existing_reminder_counts[identity_key] = int(
                                count_row["total"] if count_row else 0
                            )
                        seen = imported_reminder_counts.get(identity_key, 0)
                        imported_reminder_counts[identity_key] = seen + 1
                        # 同一文件内的完全重复行只导入一次；重复导入
                        # 同一文件时按库中已有数量跳过
                        skip_existing = (
                            seen >= 1
                            or existing_reminder_counts[identity_key] > 0
                        )
                        if skip_existing:
                            if reminder_id > 0:
                                existing_row = self._conn.execute(
                                    """
                                    SELECT id FROM reminders
                                     WHERE title = ? AND profile = ?
                                       AND note = ? AND session_id = ?
                                       AND due_at = ?
                                     ORDER BY id LIMIT 1
                                    """,
                                    (
                                        title, profile, note, session_id,
                                        due_at,
                                    ),
                                ).fetchone()
                                if existing_row is not None:
                                    raw_reminder_id_map[reminder_id] = int(
                                        existing_row["id"]
                                    )
                            continue
                    if appointment_id and not done:
                        # 同一预约已有等价的待办提醒（恢复待就诊重建的
                        # 提醒与备份旧提醒创建时间不同，身份配对看不到）：
                        # 复用现有提醒，避免同一预约两条重复待办
                        existing_pending = self._conn.execute(
                            "SELECT id FROM reminders"
                            " WHERE appointment_id = ? AND due_at = ?"
                            " AND done = 0 AND profile = ? AND title = ?"
                            " ORDER BY id LIMIT 1",
                            (appointment_id, due_at, profile, title),
                        ).fetchone()
                        if existing_pending is not None:
                            if reminder_id > 0:
                                raw_reminder_id_map.setdefault(
                                    reminder_id,
                                    int(existing_pending["id"]),
                                )
                            continue
                    cursor = self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, medication_id, vaccination_id,
                            appointment_id, done,
                            created_at, updated_at
                        )
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            0, medication_id, vaccination_id, appointment_id,
                            done, created_at, updated_at,
                        ),
                    )
                    new_reminder_id = int(cursor.lastrowid or 0)
                    if new_reminder_id:
                        imported_reminder_ids.add(new_reminder_id)
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

            # 复诊提醒在就诊记录之后导入，回填就诊记录的复诊关联
            stale_appointment_ids: List[int] = []
            for (
                visit_id,
                followup_appointment_id,
                raw_followup_reminder_id,
                raw_visit_id,
                visit_is_new,
            ) in visit_link_updates:
                followup_reminder_id = raw_reminder_id_map.get(
                    raw_followup_reminder_id, 0
                )
                if not followup_appointment_id and not followup_reminder_id:
                    continue
                visit_row = self._conn.execute(
                    "SELECT followup_appointment_id,"
                    " next_followup_date FROM visit_records"
                    " WHERE id = ?",
                    (int(visit_id),),
                ).fetchone()
                appt_row = (
                    self._conn.execute(
                        "SELECT status, scheduled_at"
                        " FROM appointments WHERE id = ?",
                        (int(followup_appointment_id),),
                    ).fetchone()
                    if followup_appointment_id else None
                )
                appointment_ok = True
                if followup_appointment_id:
                    visit_followup_date = (
                        str(visit_row["next_followup_date"] or "")
                        if visit_row is not None else ""
                    )
                    # 无论就诊是新插入还是已有：只有恢复出的预约仍是
                    # 待就诊/已完成且与该就诊复诊日期一致才允许写链接，
                    # 否则逾期复诊提示会被错挂链接永久抑制。
                    # 已完成预约是就诊历史，恢复链接才能避免已按时
                    # 复诊被重新报成逾期
                    appointment_ok = bool(
                        appt_row is not None
                        and str(appt_row["status"] or "")
                        in ("upcoming", "completed")
                        and visit_followup_date
                        and str(appt_row["scheduled_at"] or "")[:10]
                        == visit_followup_date[:10]
                    )
                if visit_is_new:
                    apply_links = bool(
                        appointment_ok
                        and (followup_appointment_id or followup_reminder_id)
                    )
                    if followup_appointment_id and not appointment_ok:
                        # 插入新就诊时已写入映射链接：这里必须清掉，
                        # 避免指向已改期/已取消的旧预约
                        if (
                            followup_reminder_id
                            and followup_reminder_id
                            in imported_reminder_ids
                        ):
                            # 只删除本次导入新插入的提醒：映射可能命中
                            # 导入前已存在的待办，不能误删用户在用提醒
                            self._conn.execute(
                                "DELETE FROM reminders WHERE id = ?"
                                " AND done = 0",
                                (int(followup_reminder_id),),
                            )
                            followup_reminder_id = 0
                        else:
                            followup_reminder_id = 0
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = 0,"
                            " followup_reminder_id = 0 WHERE id = ?",
                            (int(visit_id),),
                        )
                else:
                    # 已有就诊只在本地没有复诊链接、且恢复出的预约仍为
                    # 待就诊并与当前复诊日期一致时回填，避免覆盖本地改期
                    apply_links = bool(
                        visit_row is not None
                        and int(visit_row["followup_appointment_id"] or 0)
                        == 0
                        and appointment_ok
                        and (followup_appointment_id or followup_reminder_id)
                    )
                    local_link = int(
                        visit_row["followup_appointment_id"] or 0
                    ) if visit_row is not None else 0
                    if (
                        followup_appointment_id
                        and (
                            not appointment_ok
                            or local_link not in (
                                0, int(followup_appointment_id)
                            )
                        )
                    ):
                        # 恢复出的旧日期预约已失效，或就诊已链接另一条
                        # 待就诊复诊预约：当作重复项清理，不挂链接
                        stale_appointment_ids.append(
                            int(followup_appointment_id)
                        )
                if apply_links:
                    self._conn.execute(
                        """
                        UPDATE visit_records
                           SET followup_appointment_id = ?,
                               followup_reminder_id = ?
                         WHERE id = ?
                        """,
                        (
                            followup_appointment_id,
                            followup_reminder_id,
                            visit_id,
                        ),
                    )
                if followup_appointment_id and raw_visit_id:
                    # 归属标记里是旧库就诊 id：无论链接是否写回都要
                    # 重映射备注，否则陈旧 #N 会让恢复/改期挂到错误的
                    # 就诊并产生重复预约
                    appointment_row = self._conn.execute(
                        "SELECT note FROM appointments WHERE id = ?",
                        (int(followup_appointment_id),),
                    ).fetchone()
                    if appointment_row is not None:
                        old_marker = (
                            f"由就诊记录 #{int(raw_visit_id)} 自动创建"
                        )
                        new_marker = (
                            f"由就诊记录 #{int(visit_id)} 自动创建"
                        )
                        note_text = str(appointment_row["note"] or "")
                        if old_marker in note_text:
                            self._conn.execute(
                                "UPDATE appointments SET note = ?,"
                                " updated_at = ? WHERE id = ?",
                                (
                                    note_text.replace(
                                        old_marker, new_marker
                                    ),
                                    now,
                                    int(followup_appointment_id),
                                ),
                            )
                    if apply_links:
                        # 链接写回成功才宣称归属：拒绝链接时改写
                        # owner_visit_id 会让改期静默覆盖就诊复诊日期
                        self._conn.execute(
                            "UPDATE appointments SET owner_visit_id = ?"
                            " WHERE id = ?",
                            (int(visit_id), int(followup_appointment_id)),
                        )

            for stale_id in set(stale_appointment_ids):
                if stale_id not in imported_appointment_ids:
                    # 只清理本次导入新插入的重复预约：导入前已存在
                    # （或上次恢复保留）的待就诊预约不能因再次恢复
                    # 同一备份被静默删除
                    continue
                # 只删除未被任何就诊引用的 upcoming 旧日期预约
                self._conn.execute(
                    "DELETE FROM appointments WHERE id = ?"
                    " AND status = 'upcoming'"
                    " AND id NOT IN (SELECT followup_appointment_id"
                    " FROM visit_records"
                    " WHERE followup_appointment_id != 0)",
                    (stale_id,),
                )
                self._conn.execute(
                    "DELETE FROM reminders WHERE done = 0"
                    " AND appointment_id = ?",
                    (stale_id,),
                )

            # 备份恢复不能把提醒/复诊链接重新挂到已取消/已完成的预约：
            # 已取消/已完成预约都不应再有待办提醒（与 update_appointment
            # 的运行时行为一致）；复诊链接只在预约已取消或悬空时清空，
            # 已完成的预约是就诊历史，清链接会让已按时复诊重新报逾期
            self._conn.execute(
                "DELETE FROM reminders WHERE done = 0"
                " AND appointment_id != 0 AND appointment_id NOT IN ("
                "SELECT id FROM appointments WHERE status = 'upcoming')"
            )
            self._conn.execute(
                "UPDATE visit_records"
                " SET followup_appointment_id = 0,"
                " followup_reminder_id = 0"
                " WHERE followup_appointment_id != 0 AND ("
                "followup_appointment_id NOT IN ("
                "SELECT id FROM appointments)"
                " OR followup_appointment_id IN ("
                "SELECT id FROM appointments WHERE status = 'cancelled'))"
            )

            audit_imported = 0
            # 同一分钟内的相同审计事件是正常数据，按数量判重：
            # 重复导入同一备份保持幂等，备份内的多重事件不会被丢弃
            existing_audit_counts: Dict[tuple, int] = {}
            imported_audit_counts: Dict[tuple, int] = {}
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
                    audit_key = (action, target, detail, created_at)
                    if audit_key not in existing_audit_counts:
                        count_row = self._conn.execute(
                            """
                            SELECT COUNT(*) AS total FROM audit_log
                             WHERE action = ? AND target = ?
                               AND detail = ? AND created_at = ?
                            """,
                            audit_key,
                        ).fetchone()
                        existing_audit_counts[audit_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_audit_counts.get(audit_key, 0)
                    imported_audit_counts[audit_key] = seen + 1
                    if seen < existing_audit_counts[audit_key]:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO audit_log(action, target, detail, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        audit_key,
                    )
                    audit_imported += 1

            usage_events_imported = 0
            if isinstance(raw_usage_events, list):
                existing_usage_counts: Dict[tuple, int] = {}
                imported_usage_counts: Dict[tuple, int] = {}
                for raw in raw_usage_events[
                    :IMPORT_LIST_LIMITS["usage_events"]
                ]:
                    if not isinstance(raw, dict):
                        continue
                    session_id = str(
                        raw.get("session_id") or ""
                    ).strip()[:64]
                    profile = _normalize_profile_name(
                        raw.get("profile")
                    )
                    prompt = _usage_count(raw.get("prompt_tokens"))
                    completion = _usage_count(raw.get("completion_tokens"))
                    total = _usage_count(raw.get("total_tokens"))
                    if not total and (prompt or completion):
                        total = min(prompt + completion, MAX_USAGE_COUNT)
                    calls = _usage_count(raw.get("llm_calls"))
                    created_at = str(
                        raw.get("created_at") or now
                    )[:40]
                    usage_key = (
                        session_id, profile, prompt, completion,
                        total, calls, created_at,
                    )
                    if usage_key not in existing_usage_counts:
                        count_row = self._conn.execute(
                            """
                            SELECT COUNT(*) AS total FROM usage_events
                             WHERE session_id = ? AND profile = ?
                               AND prompt_tokens = ?
                               AND completion_tokens = ?
                               AND total_tokens = ? AND llm_calls = ?
                               AND created_at = ?
                            """,
                            usage_key,
                        ).fetchone()
                        existing_usage_counts[usage_key] = int(
                            count_row["total"] if count_row else 0
                        )
                    seen = imported_usage_counts.get(usage_key, 0)
                    imported_usage_counts[usage_key] = seen + 1
                    if seen < existing_usage_counts[usage_key]:
                        continue
                    self._conn.execute(
                        """
                        INSERT INTO usage_events(
                            session_id, profile, prompt_tokens,
                            completion_tokens, total_tokens, llm_calls,
                            created_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            session_id, profile, prompt, completion,
                            total, calls, created_at,
                        ),
                    )
                    usage_events_imported += 1

        if revived_sessions:
            with self._lock:
                for session_id in revived_sessions:
                    self._deleted_sessions.pop(session_id, None)

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
            "medication_history": medication_history_imported,
            "vaccinations": vaccinations_imported,
            "appointments": appointments_imported,
            "lab_results": lab_results_imported,
            "allergies": allergies_imported,
            "care_items": care_items_imported,
            "visit_records": visit_records_imported,
            "conditions": conditions_imported,
            "audit_log": audit_imported,
            "usage_events": usage_events_imported,
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

    def set_archived_many(
        self, session_ids: List[str], archived: bool
    ) -> Dict[str, List[str]]:
        """批量归档或移出归档；返回实际更新与缺失的会话 id。"""
        ids: List[str] = []
        for raw in session_ids:
            session_id = str(raw or "").strip()
            if session_id and session_id not in ids:
                ids.append(session_id)
        if not ids:
            return {"updated": [], "missing": []}
        placeholders = ",".join("?" for _ in ids)
        with self._lock, self._conn:
            rows = self._conn.execute(
                f"SELECT id FROM sessions WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            found = [row["id"] for row in rows]
            if found:
                marks = ",".join("?" for _ in found)
                self._conn.execute(
                    f"UPDATE sessions SET archived = ? "
                    f"WHERE id IN ({marks})",
                    [1 if archived else 0, *found],
                )
        found_set = set(found)
        return {
            "updated": [sid for sid in ids if sid in found_set],
            "missing": [sid for sid in ids if sid not in found_set],
        }

    def delete_sessions_many(
        self, session_ids: List[str]
    ) -> Dict[str, Any]:
        """批量删除会话与消息，只提交并 checkpoint 一次。

        返回被删会话的 id、缺失 id 及档案名，供调用方清理长期记忆。
        """
        ids: List[str] = []
        for raw in session_ids:
            session_id = str(raw or "").strip()
            if session_id and session_id not in ids:
                ids.append(session_id)
        if not ids:
            return {"deleted": [], "missing": [], "profiles": {}}
        placeholders = ",".join("?" for _ in ids)
        with self._write_and_checkpoint():
            rows = self._conn.execute(
                f"SELECT id, profile FROM sessions "
                f"WHERE id IN ({placeholders})",
                ids,
            ).fetchall()
            profiles = {
                row["id"]: row["profile"] or "" for row in rows
            }
            if profiles:
                found = list(profiles)
                marks = ",".join("?" for _ in found)
                self._conn.execute(
                    f"DELETE FROM messages WHERE session_id IN ({marks})",
                    found,
                )
                self._conn.execute(
                    f"DELETE FROM sessions WHERE id IN ({marks})",
                    found,
                )
            # 与 delete_session 一致：即使会话行尚未创建（第一轮会诊
            # 进行中），也登记墓碑；批量路径同样要写持久化表，
            # 否则重启/多 worker 下删除状态失效
            self._remember_deleted_locked(ids)
        return {
            "deleted": [sid for sid in ids if sid in profiles],
            "missing": [sid for sid in ids if sid not in profiles],
            "profiles": profiles,
        }

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
        with self._write_and_checkpoint():
            # 改名只是标题编辑，不算健康活动：不能刷新 updated_at，
            # 否则旧会诊会被统计为「近 N 天会诊」并重新触发风险提示
            cursor = self._conn.execute(
                "UPDATE sessions SET title = ? WHERE id = ?",
                (title, session_id),
            )
            return cursor.rowcount > 0

    def _checkpoint_wal(self) -> None:
        """把 WAL 合并回主库并截断，避免已删除内容残留在 -wal 文件。

        其它连接（外部只读查看器、备份工具）持有读事务时，TRUNCATE 会
        等满 busy_timeout（默认 5s），期间进程内所有 DB 访问一起停摆。
        这里用很短的超时尝试截断，busy 时退化为 PASSIVE 合并，把彻底
        截断留给下一次。
        """
        try:
            self._conn.execute("PRAGMA busy_timeout=250")
        except sqlite3.Error:
            pass
        try:
            result = self._conn.execute(
                "PRAGMA wal_checkpoint(TRUNCATE)"
            ).fetchone()
            busy = int(result[0] or 0) if result else 0
            if busy:
                self._conn.execute("PRAGMA wal_checkpoint(PASSIVE)")
        except sqlite3.Error as e:
            # 有长读事务时 checkpoint 会 busy；下次删除/关闭时会再尝试
            logger.warning(f"WAL checkpoint 失败（忽略）: {e}")
        finally:
            try:
                self._conn.execute("PRAGMA busy_timeout=5000")
            except sqlite3.Error:
                pass

    @contextmanager
    def _write_and_checkpoint(self):
        """在锁内提交写事务后立即 checkpoint。

        删除健康数据后旧页帧仍可能留在 -wal；checkpoint 必须在持有
        连接锁时执行，否则并发事务会让 TRUNCATE 静默失败。
        """
        with self._lock:
            with self._conn:
                yield
            self._checkpoint_wal()

    def delete_session(self, session_id: str) -> bool:
        session_id = str(session_id or "").strip()
        if not session_id:
            return False
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM sessions WHERE id = ?", (session_id,)
            )
            self._conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            # 与 save_exchange 共用锁：先登记墓碑，进行中的会诊无法再写回；
            # 持久化表让多进程/重启后同样能识别已删会话
            self._remember_deleted_locked([session_id])
            deleted = cursor.rowcount > 0
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
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM patient_profiles WHERE name = ?", (name,)
            )
            if cursor.rowcount == 0:
                return False
            # 墓碑用于中止删除进行中的会诊写回，并让清理队列识别
            # 「同名档案已重建」，避免误删新档案刚写入的长期记忆；
            # 只存哈希，已删除的姓名不会残留
            self._conn.execute(
                "INSERT INTO profile_deletions(profile_hash, deleted_at)"
                " VALUES (?, ?)",
                (_profile_deletion_key(name), self._now()),
            )
            # 指标目标属于档案配置，删除档案时一并清理，避免重建同名档案继承旧目标
            self._conn.execute(
                "DELETE FROM metric_targets WHERE profile = ?", (name,)
            )
            # 旧会话不再绑定已删除档案：否则继续提问会写回同名命名空间，
            # 重建同名档案后召回已删除患者的病史
            self._conn.execute(
                "UPDATE sessions SET profile = '' WHERE profile = ?",
                (name,),
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
                    ("medication_history", "profile"),
                    ("vaccinations", "profile"),
                    ("appointments", "profile"),
                    ("lab_results", "profile"),
                    ("allergies", "profile"),
                    ("care_items", "profile"),
                    ("visit_records", "profile"),
                    ("conditions", "profile"),
                    ("attachments", "profile"),
                    ("usage_events", "profile"),
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
                "medication_history",
                "vaccinations",
                "appointments",
                "lab_results",
                "allergies",
                "care_items",
                "visit_records",
                "conditions",
                "sessions",
                "attachments",
                "usage_events",
            ):
                self._conn.execute(
                    f"UPDATE {table} SET profile = ? WHERE profile = ?",
                    (new_name, old_name),
                )
            # 改名链：会诊中途改名、恢复改名前的备份都按它解析到新名称
            self._conn.execute(
                """
                INSERT INTO profile_renames(
                    profile_hash, old_name, new_name, renamed_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _profile_deletion_key(old_name),
                    old_name, new_name, now,
                ),
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
        context: str = "",
    ) -> Optional[Dict[str, Any]]:
        """新增一条健康指标记录；字段无效时返回 None。"""
        normalized = _normalize_metric(
            profile, metric, value1, value2, measured_at, note, context
        )
        if normalized is None:
            return None

        now = self._now()
        auto_time = not str(measured_at or "").strip()
        retry_now = datetime.now()
        # 只把数十秒内的重复提交视为重试；同值复测通常发生在更久之后
        retry_cutoff = (
            retry_now - timedelta(seconds=30)
        ).isoformat(timespec="seconds")
        auto_cutoff = (
            retry_now - timedelta(seconds=30)
        ).isoformat(timespec="minutes")
        with self._lock, self._conn:
            # 响应丢失后的重试：显式测量时间时按原值比对；未填时间时服务端
            # 会按当前分钟重新计算，跨分钟重试也要能识别为同一次保存
            if auto_time:
                time_condition = "measured_at >= ?"
                time_value = auto_cutoff
            else:
                time_condition = "measured_at = ?"
                time_value = normalized["measured_at"]
            duplicate = self._conn.execute(
                f"""
                SELECT id, profile, metric, value1, value2, unit,
                       measured_at, context, note, created_at
                  FROM health_metrics
                 WHERE profile = ? AND metric = ? AND value1 = ?
                   AND IFNULL(value2, -1) = IFNULL(?, -1)
                   AND context = ? AND note = ?
                   AND {time_condition}
                   AND created_at >= ?
                 ORDER BY id DESC LIMIT 1
                """,
                (
                    normalized["profile"],
                    normalized["metric"],
                    normalized["value1"],
                    normalized["value2"],
                    normalized["context"],
                    normalized["note"],
                    time_value,
                    retry_cutoff,
                ),
            ).fetchone()
            if duplicate is not None:
                return {**dict(duplicate), "duplicate": True}
            cursor = self._conn.execute(
                """
                INSERT INTO health_metrics(
                    profile, metric, value1, value2, unit,
                    measured_at, context, note, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["metric"],
                    normalized["value1"],
                    normalized["value2"],
                    normalized["unit"],
                    normalized["measured_at"],
                    normalized["context"],
                    normalized["note"],
                    now,
                    now,
                ),
            )
            metric_id = int(cursor.lastrowid)

        return {
            "id": metric_id,
            **normalized,
            "created_at": now,
            "updated_at": now,
        }

    def list_metrics(
        self,
        profile: Optional[str] = None,
        metric: Optional[str] = None,
        limit: int = 100,
        days: int = 0,
        offset: int = 0,
        q: str = "",
        start: str = "",
        end: str = "",
        search_q: str = "",
        search_metric_codes: Optional[List[str]] = None,
        search_contexts: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """按测量时间倒序返回健康指标记录，可按最近天数筛选与分页。"""
        limit = max(1, min(int(limit or 100), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        sql = """
            SELECT id, profile, metric, value1, value2, unit,
                   measured_at, context, note, created_at, updated_at
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
        search_parts: List[str] = []
        search_part_params: List[Any] = []
        metric_codes = [
            code for code in (search_metric_codes or ())
            if code in METRIC_TYPES
        ]
        if metric_codes:
            search_parts.append(
                "metric IN (" + ",".join("?" for _ in metric_codes) + ")"
            )
            search_part_params.extend(metric_codes)
        context_codes = [
            code for code in (search_contexts or ())
            if code in METRIC_CONTEXT_LABELS
        ]
        if context_codes:
            search_parts.append(
                "context IN (" + ",".join("?" for _ in context_codes) + ")"
            )
            search_part_params.extend(context_codes)
        search_clause, search_params = _like_any_condition(
            ("metric", "unit", "context", "note"), search_q
        )
        if search_clause:
            search_parts.append(search_clause)
            search_part_params.extend(search_params)
        if search_parts:
            conditions.append("(" + " OR ".join(search_parts) + ")")
            params.extend(search_part_params)
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("measured_at >= ?")
            params.append(cutoff)
            end_of_today = datetime.now().replace(
                hour=23, minute=59, second=59
            ).isoformat(timespec="seconds")
            conditions.append("measured_at <= ?")
            params.append(end_of_today)
        range_conditions, range_params = _date_range_conditions(
            "measured_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
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
        alias_where = (
            " WHERE " + " AND ".join(f"h.{item}" for item in conditions)
            if conditions else ""
        )
        sql = f"""
            WITH latest AS (
                SELECT metric, MAX(measured_at) AS max_at
                  FROM health_metrics{where}
                 GROUP BY metric
            ),
            picked AS (
                SELECT h.metric, MAX(h.id) AS max_id
                  FROM health_metrics h
                  JOIN latest l
                    ON h.metric = l.metric
                   AND h.measured_at = l.max_at
                 {alias_where}
                 GROUP BY h.metric
            )
            SELECT h.id, h.profile, h.metric, h.value1, h.value2, h.unit,
                   h.measured_at, h.context, h.note, h.created_at
              FROM health_metrics h
              JOIN picked p ON h.id = p.max_id
             ORDER BY h.measured_at DESC
        """
        with self._lock:
            rows = self._conn.execute(sql, [*params, *params]).fetchall()
        return [dict(row) for row in rows]

    def latest_metrics_by_profile(self) -> List[Dict[str, Any]]:
        """返回每个档案下每种指标最新的一条记录（家庭总览用）。"""
        end_of_today = datetime.now().replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
        sql = """
            WITH latest AS (
                SELECT profile, metric, MAX(measured_at) AS max_at
                  FROM health_metrics
                 WHERE measured_at <= ?
                 GROUP BY profile, metric
            ),
            picked AS (
                SELECT h.profile, h.metric, MAX(h.id) AS max_id
                  FROM health_metrics h
                  JOIN latest l
                    ON h.profile = l.profile AND h.metric = l.metric
                   AND h.measured_at = l.max_at
                 GROUP BY h.profile, h.metric
            )
            SELECT h.id, h.profile, h.metric, h.value1, h.value2, h.unit,
                   h.measured_at, h.context, h.note, h.created_at
              FROM health_metrics h
              JOIN picked p ON h.id = p.max_id
             ORDER BY h.profile, h.metric
        """
        with self._lock:
            rows = self._conn.execute(sql, (end_of_today,)).fetchall()
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
                       measured_at, context, note, created_at
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

        # 步数/饮水的目标是每日总量：给出今日累计与达标情况，
        # 单条记录不参与这条判定（历史「达标条数」语义保持不变）
        daily_total = None
        daily_count = 0
        daily_within_target = None
        if metric in DAILY_ACCUMULATING_METRICS:
            today_start = now.replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            today_conditions = list(base_conditions) + [
                "measured_at >= ?",
                "measured_at <= ?",
            ]
            today_params = base_params + [
                today_start.isoformat(timespec="minutes"),
                end_of_today,
            ]
            with self._lock:
                today_row = self._conn.execute(
                    "SELECT COUNT(*) AS count, SUM(value1) AS total"
                    "  FROM health_metrics WHERE "
                    + " AND ".join(today_conditions),
                    today_params,
                ).fetchone()
            daily_count = (
                int(today_row["count"] or 0) if today_row else 0
            )
            daily_total = (
                round(float(today_row["total"] or 0), 1)
                if daily_count and today_row else None
            )
            if target_row is not None and daily_count:
                daily_within_target = (
                    daily_total >= float(target_row["value1"])
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
            "daily_total": daily_total,
            "daily_count": daily_count,
            "daily_within_target": daily_within_target,
        }

    def add_vaccination(
        self,
        profile: str,
        vaccine_name: str,
        dose_number: Any = 1,
        vaccinated_at: str = "",
        next_due: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """新增一条疫苗接种记录并返回完整行。"""
        normalized = _normalize_vaccination(
            profile, vaccine_name, dose_number, vaccinated_at, next_due, note
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO vaccinations(
                    profile, vaccine_name, dose_number, vaccinated_at,
                    next_due, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["vaccine_name"],
                    normalized["dose_number"], normalized["vaccinated_at"],
                    normalized["next_due"], normalized["note"], now, now,
                ),
            )
            vaccination_id = int(cursor.lastrowid)
        return {**normalized, "id": vaccination_id, "created_at": now,
                "updated_at": now}

    def update_vaccination_partial(
        self, vaccination_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_vaccination(vaccination_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_vaccination(
                vaccination_id,
                merged.get("profile") or "",
                merged.get("vaccine_name") or "",
                merged.get("dose_number"),
                merged.get("vaccinated_at") or "",
                merged.get("next_due") or "",
                merged.get("note") or "",
            )

    def update_vaccination(
        self,
        vaccination_id: int,
        profile: str,
        vaccine_name: str,
        dose_number: Any,
        vaccinated_at: str,
        next_due: str,
        note: str,
    ) -> Optional[Dict[str, Any]]:
        """整体更新一条疫苗接种记录；记录不存在时返回 None。"""
        normalized = _normalize_vaccination(
            profile, vaccine_name, dose_number, vaccinated_at, next_due, note
        )
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                "SELECT next_due FROM vaccinations WHERE id = ?",
                (int(vaccination_id),),
            ).fetchone()
            cursor = self._conn.execute(
                """
                UPDATE vaccinations
                   SET profile = ?, vaccine_name = ?, dose_number = ?,
                       vaccinated_at = ?, next_due = ?, note = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["vaccine_name"],
                    normalized["dose_number"], normalized["vaccinated_at"],
                    normalized["next_due"], normalized["note"], now,
                    int(vaccination_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            # 只有下一剂日期变化才同步接种提醒；只改备注/剂次不能把
            # 用户手动推迟的提醒拨回原日期。清空日期表示系列结束
            next_due = str(normalized["next_due"] or "")
            due_changed = (
                previous is None
                or str(previous["next_due"] or "") != next_due
            )
            if next_due and due_changed:
                self._conn.execute(
                    """
                    UPDATE reminders SET due_at = ?, updated_at = ?
                     WHERE done = 0 AND vaccination_id = ?
                    """,
                    (next_due, now, int(vaccination_id)),
                )
            elif not next_due:
                self._conn.execute(
                    "DELETE FROM reminders "
                    "WHERE done = 0 AND vaccination_id = ?",
                    (int(vaccination_id),),
                )
        return self.get_vaccination(vaccination_id)

    def get_vaccination(
        self, vaccination_id: int
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, vaccine_name, dose_number,
                       vaccinated_at, next_due, note, created_at, updated_at
                  FROM vaccinations WHERE id = ?
                """,
                (int(vaccination_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_vaccinations(
        self,
        profile: Optional[str] = None,
        limit: int = 1000,
        start: str = "",
        end: str = "",
        search_q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        conditions = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        range_conditions, range_params = _date_range_conditions(
            "vaccinated_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        search_clause, search_params = _like_any_condition(
            ("vaccine_name", "note"), search_q
        )
        if search_clause:
            conditions.append(search_clause)
            params.extend(search_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, vaccine_name, dose_number,
                       vaccinated_at, next_due, note, created_at, updated_at
                  FROM vaccinations
                 {where}
                 ORDER BY vaccinated_at DESC, id DESC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_vaccination(self, vaccination_id: int) -> bool:
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM vaccinations WHERE id = ?",
                (int(vaccination_id),),
            )
            if cursor.rowcount == 0:
                return False
            self._conn.execute(
                "DELETE FROM reminders WHERE done = 0 AND vaccination_id = ?",
                (int(vaccination_id),),
            )
            return True

    def add_appointment(
        self,
        profile: str,
        title: str,
        department: str = "",
        doctor: str = "",
        location: str = "",
        scheduled_at: str = "",
        status: str = "upcoming",
        note: str = "",
        owner_visit_id: Any = 0,
    ) -> Dict[str, Any]:
        """新增一条就诊预约并返回完整行。"""
        normalized = _normalize_appointment(
            profile, title, department, doctor, location,
            scheduled_at, status, note,
        )
        owner_visit = _safe_db_id(owner_visit_id)
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO appointments(
                    profile, title, department, doctor, location,
                    scheduled_at, status, note, owner_visit_id,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["title"],
                    normalized["department"], normalized["doctor"],
                    normalized["location"], normalized["scheduled_at"],
                    normalized["status"], normalized["note"], owner_visit,
                    now, now,
                ),
            )
            appointment_id = int(cursor.lastrowid)
        data = {"id": appointment_id, **normalized, "created_at": now,
                "updated_at": now, "owner_visit_id": owner_visit}
        return {
            **data,
            **_appointment_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def update_appointment_partial(
        self, appointment_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_appointment(appointment_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_appointment(
                appointment_id,
                merged.get("profile") or "",
                merged.get("title") or "",
                merged.get("department") or "",
                merged.get("doctor") or "",
                merged.get("location") or "",
                merged.get("scheduled_at") or "",
                merged.get("status") or "upcoming",
                merged.get("note") or "",
            )

    def _appointment_owner_visit(
        self, note: Any, owner_visit_id: Any, profile: str = ""
    ) -> int:
        """解析预约归属就诊：备注标记与持久 owner 冲突时以持久值为准。

        备注可被用户编辑、导入时也可能残留旧库 id；只有标记指向真实
        存在（且档案一致、与 owner_visit_id 不矛盾）的就诊才可采用。
        """
        owner = _safe_db_id(owner_visit_id)
        marker = re.search(
            r"由就诊记录 #(\d+) 自动创建", str(note or "")
        )
        marker_id = (
            _safe_db_id(marker.group(1)) if marker is not None else 0
        )
        if marker_id and owner and marker_id != owner:
            marker_id = 0
        for candidate in (marker_id, owner):
            if not candidate:
                continue
            row = self._conn.execute(
                "SELECT profile FROM visit_records WHERE id = ?",
                (candidate,),
            ).fetchone()
            if row is None:
                continue
            if profile and _normalize_profile_name(
                str(row["profile"] or "")
            ) != profile:
                continue
            return candidate
        return 0

    def update_appointment(
        self,
        appointment_id: int,
        profile: str,
        title: str,
        department: str = "",
        doctor: str = "",
        location: str = "",
        scheduled_at: str = "",
        status: str = "upcoming",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """整体更新一条就诊预约；记录不存在时返回 None。"""
        normalized = _normalize_appointment(
            profile, title, department, doctor, location,
            scheduled_at, status, note,
        )
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                "SELECT profile, scheduled_at, status, title, department,"
                " doctor, location, note, owner_visit_id"
                " FROM appointments WHERE id = ?",
                (int(appointment_id),),
            ).fetchone()
            if previous is None:
                return None
            # 恢复旧预约前先确认该就诊没有另一条待就诊自动预约：
            # 否则会同时存在两条复诊预约和两条待办提醒。
            # completed 同样可被前端“恢复待就诊”，必须走同一校验
            if (
                normalized["status"] == "upcoming"
                and str(previous["status"] or "") != "upcoming"
            ):
                restore_visit_id = self._appointment_owner_visit(
                    previous["note"],
                    previous["owner_visit_id"],
                    normalized["profile"],
                )
                if restore_visit_id:
                    linked_visit = self._conn.execute(
                        "SELECT profile, followup_appointment_id,"
                        " next_followup_date"
                        " FROM visit_records WHERE id = ?",
                        (restore_visit_id,),
                    ).fetchone()
                    if linked_visit is not None and (
                        _normalize_profile_name(
                            linked_visit["profile"] or ""
                        )
                        != normalized["profile"]
                    ):
                        # 备注里的 #N 指向本库同号的另一档案就诊：
                        # 不能把预约恢复并挂到无关成员名下
                        linked_visit = None
                    if linked_visit is not None:
                        linked_id = int(
                            linked_visit["followup_appointment_id"] or 0
                        )
                        if linked_id not in (0, int(appointment_id)):
                            raise ValueError(
                                "该就诊已有另一条待就诊复诊预约，"
                                "不能恢复旧预约"
                            )
                        visit_date = str(
                            linked_visit["next_followup_date"] or ""
                        )[:10]
                        if visit_date and visit_date != str(
                            normalized["scheduled_at"] or ""
                        )[:10]:
                            raise ValueError(
                                "就诊复诊日期已变更，不能恢复旧日期预约"
                            )
            cursor = self._conn.execute(
                """
                UPDATE appointments
                   SET profile = ?, title = ?, department = ?, doctor = ?,
                       location = ?, scheduled_at = ?, status = ?,
                       note = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["title"],
                    normalized["department"], normalized["doctor"],
                    normalized["location"], normalized["scheduled_at"],
                    normalized["status"], normalized["note"], now,
                    int(appointment_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            # 预约改档案时提醒跟随，避免提醒留在旧成员名下
            self._conn.execute(
                "UPDATE reminders SET profile = ?"
                " WHERE appointment_id = ?",
                (normalized["profile"], int(appointment_id)),
            )
            profile_changed = (
                _normalize_profile_name(str(previous["profile"] or ""))
                != normalized["profile"]
            )
            if profile_changed:
                # 另一位成员的就诊记录不能继续把这条预约当「已安排
                # 复诊」：清掉不匹配档案的链接与归属，避免删除/改期
                # 该就诊时连带操作别人的预约
                self._conn.execute(
                    """
                    UPDATE visit_records
                       SET followup_appointment_id = 0,
                           followup_reminder_id = 0,
                           updated_at = ?
                     WHERE followup_appointment_id = ?
                       AND profile != ?
                    """,
                    (
                        now, int(appointment_id),
                        normalized["profile"],
                    ),
                )
                self._conn.execute(
                    "UPDATE appointments SET owner_visit_id = 0"
                    " WHERE id = ? AND owner_visit_id != 0 AND"
                    " owner_visit_id NOT IN ("
                    "SELECT id FROM visit_records WHERE profile = ?)",
                    (int(appointment_id), normalized["profile"]),
                )
            # 取消/完成时去掉待办提醒；只有改期或状态变化才重算提醒时间，
            # 只改备注/医生不能把用户手动推迟的提醒拨回原日期
            reminder_due = ""
            if (
                normalized["status"] == "upcoming"
                and normalized["scheduled_at"]
            ):
                try:
                    scheduled_dt = datetime.fromisoformat(
                        normalized["scheduled_at"]
                    )
                except ValueError:
                    scheduled_dt = None
                if scheduled_dt is not None:
                    try:
                        reminder_date = (
                            scheduled_dt.date() - timedelta(days=1)
                        )
                    except OverflowError:
                        reminder_date = datetime.now().date()
                    reminder_due = max(
                        reminder_date, datetime.now().date()
                    ).strftime("%Y-%m-%d")
            def _create_reminder() -> int:
                """按当前预约字段生成一条待办提醒并返回 id。"""
                followup_style = str(
                    previous["title"] or ""
                ).startswith("复诊：")
                generated_title = (
                    normalized["title"] if followup_style
                    else f"就诊：{normalized['title'] or '就诊'}"
                )[:120]
                if followup_style:
                    note_parts = [
                        "复诊 " + str(normalized["scheduled_at"])[:10],
                        normalized["department"],
                        normalized["doctor"],
                    ]
                else:
                    note_parts = [
                        str(normalized["scheduled_at"]).replace(
                            "T", " "
                        )[:16],
                        normalized["department"],
                        normalized["doctor"],
                        normalized["location"],
                    ]
                generated_note = " ".join(
                    part for part in note_parts if part
                )[:300]
                cursor = self._conn.execute(
                    """
                    INSERT INTO reminders(
                        title, note, due_at, session_id, profile,
                        advance_days, repeat_days, repeat_until,
                        medication_id, vaccination_id,
                        appointment_id, done, created_at, updated_at
                    ) VALUES (?, ?, ?, '', ?, 0, 0, '', 0, 0, ?, 0, ?, ?)
                    """,
                    (
                        generated_title, generated_note, reminder_due,
                        normalized["profile"], int(appointment_id),
                        now, now,
                    ),
                )
                return int(cursor.lastrowid or 0)

            schedule_changed = (
                str(previous["scheduled_at"] or "")
                != normalized["scheduled_at"]
                or str(previous["status"] or "") != normalized["status"]
            )
            if normalized["status"] != "upcoming":
                self._conn.execute(
                    "DELETE FROM reminders "
                    "WHERE done = 0 AND appointment_id = ?",
                    (int(appointment_id),),
                )
                # 已完成提醒不再指向已取消/已完成的预约，避免被重新
                # 打开后复活成悬空待办
                self._conn.execute(
                    "UPDATE reminders SET appointment_id = 0, updated_at = ?"
                    " WHERE done = 1 AND appointment_id = ?",
                    (now, int(appointment_id)),
                )
                if normalized["status"] == "cancelled":
                    # 取消后已无预约/提醒：清空就诊记录的复诊链接，
                    # 否则逾期复诊提示会被永久抑制
                    self._conn.execute(
                        """
                        UPDATE visit_records
                           SET followup_appointment_id = 0,
                               followup_reminder_id = 0,
                               updated_at = ?
                         WHERE followup_appointment_id = ?
                        """,
                        (now, int(appointment_id)),
                    )
            elif schedule_changed:
                if reminder_due:
                    updated = self._conn.execute(
                        """
                        UPDATE reminders SET due_at = ?, updated_at = ?
                         WHERE done = 0 AND appointment_id = ?
                        """,
                        (reminder_due, now, int(appointment_id)),
                    )
                    if updated.rowcount == 0:
                        # 该预约唯一的提醒已被勾选完成：改期后必须重建
                        # 待办，否则复诊前没有任何提醒
                        rebuilt_id = _create_reminder()
                        if rebuilt_id:
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_reminder_id = ?,"
                                " updated_at = ?"
                                " WHERE followup_appointment_id = ?",
                                (
                                    rebuilt_id, now,
                                    int(appointment_id),
                                ),
                            )
                else:
                    self._conn.execute(
                        "DELETE FROM reminders "
                        "WHERE done = 0 AND appointment_id = ?",
                        (int(appointment_id),),
                    )
            # 取消/完成后恢复待就诊：待办提醒在取消时已被删除，按当前
            # 预约字段重建一条（手改文案无法找回，但至少不会漏提醒）
            was_upcoming = str(previous["status"] or "") == "upcoming"
            if (
                normalized["status"] == "upcoming"
                and not was_upcoming
                and reminder_due
            ):
                existing = self._conn.execute(
                    "SELECT id FROM reminders WHERE done = 0"
                    " AND appointment_id = ? LIMIT 1",
                    (int(appointment_id),),
                ).fetchone()
                restored_reminder_id = (
                    int(existing["id"]) if existing is not None else 0
                )
                if existing is None:
                    restored_reminder_id = _create_reminder()
                if restored_reminder_id:
                    # 恢复待就诊后把就诊记录的复诊链接写回：否则逾期
                    # 复诊提示会被抑制、再次安排复诊会产生重复预约
                    visit_target = self._appointment_owner_visit(
                        previous["note"],
                        previous["owner_visit_id"],
                        normalized["profile"],
                    )
                    if not visit_target:
                        linked = self._conn.execute(
                            "SELECT id FROM visit_records"
                            " WHERE followup_appointment_id = ?",
                            (int(appointment_id),),
                        ).fetchone()
                        if linked is not None:
                            visit_target = int(linked["id"])
                    if visit_target:
                        # 复诊日期在取消期间被改过时不能把链接挂回旧日期
                        # 的预约，否则后续“安排复诊”会误报已安排
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = ?,"
                            " followup_reminder_id = ?, updated_at = ?"
                            " WHERE id = ? AND (followup_appointment_id = 0"
                            " OR followup_appointment_id = ?)"
                            " AND profile = ?"
                            " AND next_followup_date != ''"
                            " AND substr(next_followup_date, 1, 10)"
                            " = substr(?, 1, 10)",
                            (
                                int(appointment_id),
                                restored_reminder_id,
                                now,
                                visit_target,
                                int(appointment_id),
                                normalized["profile"],
                                normalized["scheduled_at"],
                            ),
                        )
            # 自动复诊预约被改期后，把新日期同步回就诊记录：否则就诊
            # 仍显示旧日期，再次“安排复诊”会按旧日期新建第二条预约
            if (
                normalized["status"] == "upcoming"
                and str(previous["status"] or "") == "upcoming"
                and normalized["scheduled_at"]
                and str(previous["scheduled_at"] or "")
                != normalized["scheduled_at"]
            ):
                visit_target = self._appointment_owner_visit(
                    previous["note"],
                    previous["owner_visit_id"],
                    normalized["profile"],
                )
                if not visit_target:
                    # 备注可被用户编辑：就诊链接同样是归属证据
                    linked = self._conn.execute(
                        "SELECT id FROM visit_records"
                        " WHERE followup_appointment_id = ?",
                        (int(appointment_id),),
                    ).fetchone()
                    if linked is not None:
                        visit_target = int(linked["id"])
                if visit_target:
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET next_followup_date = ?, updated_at = ?"
                        " WHERE id = ? AND profile = ?"
                        " AND next_followup_date != ?",
                        (
                            str(normalized["scheduled_at"])[:10],
                            now,
                            visit_target,
                            normalized["profile"],
                            str(normalized["scheduled_at"])[:10],
                        ),
                    )
            # 提醒的标题/备注是创建时按预约字段生成的快照：改期或改名
            # 后必须同步，否则列表仍显示旧时间/科室/医生。两种自动文案
            # （就诊：X / 复诊：X）都要覆盖；仅在提醒仍是自动生成内容
            # 时更新，不覆盖用户手动修改的文案
            def _reminder_texts(
                row_title: str, scheduled: str, department: str,
                doctor: str, location: str,
            ) -> List[Tuple[str, str]]:
                full = (
                    f"就诊：{row_title or '就诊'}"[:120],
                    " ".join(part for part in [
                        str(scheduled or "").replace("T", " ")[:16],
                        department or "", doctor or "", location or "",
                    ] if part)[:300],
                )
                follow = (
                    str(row_title or "复诊")[:120],
                    " ".join(part for part in [
                        "复诊 " + str(scheduled or "")[:10],
                        department or "", doctor or "",
                    ] if part)[:300],
                )
                return [full, follow]

            old_texts = _reminder_texts(
                previous["title"], previous["scheduled_at"],
                previous["department"], previous["doctor"],
                previous["location"],
            )
            new_texts = _reminder_texts(
                normalized["title"], normalized["scheduled_at"],
                normalized["department"], normalized["doctor"],
                normalized["location"],
            )
            for (old_title, old_note), (new_title, new_note) in zip(
                old_texts, new_texts
            ):
                if new_title != old_title:
                    self._conn.execute(
                        "UPDATE reminders SET title = ?, updated_at = ?"
                        " WHERE done = 0 AND appointment_id = ?"
                        " AND title = ?",
                        (new_title, now, int(appointment_id), old_title),
                    )
                if new_note != old_note:
                    self._conn.execute(
                        "UPDATE reminders SET note = ?, updated_at = ?"
                        " WHERE done = 0 AND appointment_id = ?"
                        " AND note = ?",
                        (new_note, now, int(appointment_id), old_note),
                    )
        return self.get_appointment(appointment_id)

    def get_appointment(
        self, appointment_id: int
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, title, department, doctor, location,
                       scheduled_at, status, note, owner_visit_id,
                       created_at, updated_at
                  FROM appointments WHERE id = ?
                """,
                (int(appointment_id),),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {
            **data,
            **_appointment_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def list_appointments(
        self,
        profile: Optional[str] = None,
        status: Optional[str] = None,
        upcoming: bool = False,
        limit: int = 1000,
        offset: int = 0,
        start: str = "",
        end: str = "",
        q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        status_key = str(status or "").strip().lower()
        if status_key:
            if status_key not in APPOINTMENT_STATUSES:
                raise ValueError("就诊状态应为 upcoming/completed/cancelled")
            conditions.append("status = ?")
            params.append(status_key)
        today = datetime.now().strftime("%Y-%m-%d")
        if upcoming:
            conditions.append("status = 'upcoming'")
            conditions.append("substr(scheduled_at, 1, 10) >= ?")
            params.append(today)
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(title LIKE ? ESCAPE '\\' OR department LIKE ? ESCAPE '\\'"
                " OR doctor LIKE ? ESCAPE '\\' OR location LIKE ? ESCAPE '\\'"
                " OR note LIKE ? ESCAPE '\\' OR profile LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 6)
        range_conditions, range_params = _date_range_conditions(
            "scheduled_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, title, department, doctor, location,
                       scheduled_at, status, note, owner_visit_id,
                       created_at, updated_at
                  FROM appointments
                 {where}
                 ORDER BY scheduled_at DESC, id DESC
                 LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return [
            {
                **dict(row),
                **_appointment_view_fields(dict(row), today),
            }
            for row in rows
        ]

    def delete_appointment(self, appointment_id: int) -> bool:
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM appointments WHERE id = ?",
                (int(appointment_id),),
            )
            if cursor.rowcount == 0:
                return False
            # 预约提醒随预约删除，避免遗留的待办在之后显示逾期
            self._conn.execute(
                "DELETE FROM reminders WHERE done = 0 AND appointment_id = ?",
                (int(appointment_id),),
            )
            # 已完成提醒保留历史，但不能继续指向已删除的预约：
            # 否则重新打开会复活一条悬空待办并造成重复提醒
            self._conn.execute(
                "UPDATE reminders SET appointment_id = 0, updated_at = ?"
                " WHERE done = 1 AND appointment_id = ?",
                (self._now(), int(appointment_id)),
            )
            # 复诊安排随预约删除回到未安排状态，便于重新一键安排
            self._conn.execute(
                """
                UPDATE visit_records
                   SET followup_appointment_id = 0,
                       followup_reminder_id = 0,
                       updated_at = ?
                 WHERE followup_appointment_id = ?
                """,
                (self._now(), int(appointment_id)),
            )
            # 从该预约补记的就诊记录不能留下悬空引用
            self._conn.execute(
                """
                UPDATE visit_records
                   SET appointment_id = 0, updated_at = ?
                 WHERE appointment_id = ?
                """,
                (self._now(), int(appointment_id)),
            )
            return True

    def add_lab_result(
        self,
        profile: str,
        item: str,
        value: Any,
        unit: str = "",
        ref_low: Any = None,
        ref_high: Any = None,
        tested_at: str = "",
        panel: str = "",
        lab_name: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """新增一条化验结果并返回完整行。"""
        normalized = _normalize_lab_result(
            profile, panel, item, value, unit,
            ref_low, ref_high, tested_at, lab_name, note,
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO lab_results(
                    profile, panel, item, value, unit, ref_low, ref_high,
                    tested_at, lab_name, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["panel"],
                    normalized["item"], normalized["value"],
                    normalized["unit"], normalized["ref_low"],
                    normalized["ref_high"], normalized["tested_at"],
                    normalized["lab_name"], normalized["note"],
                    now, now,
                ),
            )
            lab_id = int(cursor.lastrowid)
        data = {"id": lab_id, **normalized, "created_at": now,
                "updated_at": now}
        return {**data, **_lab_result_view_fields(data)}

    def update_lab_result_partial(
        self, lab_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_lab_result(lab_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_lab_result(
                lab_id,
                merged.get("profile") or "",
                merged.get("item") or "",
                merged.get("value"),
                merged.get("unit") or "",
                merged.get("ref_low"),
                merged.get("ref_high"),
                merged.get("tested_at") or "",
                merged.get("panel") or "",
                merged.get("lab_name") or "",
                merged.get("note") or "",
            )

    def update_lab_result(
        self,
        lab_id: int,
        profile: str,
        item: str,
        value: Any,
        unit: str = "",
        ref_low: Any = None,
        ref_high: Any = None,
        tested_at: str = "",
        panel: str = "",
        lab_name: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """整体更新一条化验结果；记录不存在时返回 None。"""
        normalized = _normalize_lab_result(
            profile, panel, item, value, unit,
            ref_low, ref_high, tested_at, lab_name, note,
        )
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                "SELECT profile FROM lab_results WHERE id = ?",
                (int(lab_id),),
            ).fetchone()
            cursor = self._conn.execute(
                """
                UPDATE lab_results
                   SET profile = ?, panel = ?, item = ?, value = ?,
                       unit = ?, ref_low = ?, ref_high = ?, tested_at = ?,
                       lab_name = ?, note = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["panel"],
                    normalized["item"], normalized["value"],
                    normalized["unit"], normalized["ref_low"],
                    normalized["ref_high"], normalized["tested_at"],
                    normalized["lab_name"], normalized["note"], now,
                    int(lab_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            if (
                previous is not None
                and _normalize_profile_name(previous["profile"] or "")
                != normalized["profile"]
            ):
                self._conn.execute(
                    "UPDATE attachments SET profile = ?"
                    " WHERE kind = 'lab' AND ref_id = ?",
                    (normalized["profile"], int(lab_id)),
                )
        return self.get_lab_result(lab_id)

    def get_lab_result(self, lab_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, panel, item, value, unit, ref_low,
                       ref_high, tested_at, lab_name, note,
                       created_at, updated_at
                  FROM lab_results WHERE id = ?
                """,
                (int(lab_id),),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {**data, **_lab_result_view_fields(data)}

    def list_lab_results(
        self,
        profile: Optional[str] = None,
        item: Optional[str] = None,
        panel: Optional[str] = None,
        days: int = 0,
        limit: int = 1000,
        offset: int = 0,
        start: str = "",
        end: str = "",
        search_q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        item_key = " ".join(str(item or "").split())[:MAX_LAB_ITEM_LENGTH]
        if item_key:
            conditions.append("item = ?")
            params.append(item_key)
        panel_key = " ".join(str(panel or "").split())[:MAX_LAB_PANEL_LENGTH]
        if panel_key:
            conditions.append("panel = ?")
            params.append(panel_key)
        cutoff = _days_cutoff(days)
        if cutoff:
            # 用原始时间戳比较才能命中 (profile, tested_at) 索引；
            # substr(tested_at, 1, 10) 会退化为逐行扫描
            conditions.append("tested_at >= ?")
            params.append(cutoff)
            # 与症状/打卡一致：最近 N 天不包含明天的化验
            tomorrow = (
                datetime.now() + timedelta(days=1)
            ).strftime("%Y-%m-%dT00:00")
            conditions.append("tested_at < ?")
            params.append(tomorrow)
        range_conditions, range_params = _date_range_conditions(
            "tested_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        search_clause, search_params = _like_any_condition(
            ("item", "panel", "lab_name", "unit", "note"), search_q
        )
        if search_clause:
            conditions.append(search_clause)
            params.extend(search_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, panel, item, value, unit, ref_low,
                       ref_high, tested_at, lab_name, note,
                       created_at, updated_at
                  FROM lab_results
                 {where}
                 ORDER BY tested_at DESC, id DESC
                 LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return [
            {**dict(row), **_lab_result_view_fields(dict(row))}
            for row in rows
        ]

    def latest_lab_results(
        self, profile: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """每个（项目, 分组）最近一次结果，避免尿/血同名项目互相覆盖。"""
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        sub_where = ""
        sub_params: List[Any] = []
        if profile is not None:
            sub_where = "AND l2.profile = ?"
            sub_params.append(params[0])
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT l.id, l.profile, l.panel, l.item, l.value, l.unit,
                       l.ref_low, l.ref_high, l.tested_at, l.lab_name,
                       l.note, l.created_at, l.updated_at
                  FROM lab_results l
                  JOIN (
                      SELECT DISTINCT item, panel FROM lab_results {where}
                  ) d ON d.item = l.item AND d.panel = l.panel
                 WHERE l.id = (
                       SELECT l2.id FROM lab_results l2
                        WHERE l2.item = d.item AND l2.panel = d.panel
                          {sub_where}
                        ORDER BY l2.tested_at DESC, l2.id DESC
                        LIMIT 1
                 )
                 ORDER BY l.item ASC, l.panel ASC
                """,
                [*params, *sub_params],
            ).fetchall()
        return [
            {**dict(row), **_lab_result_view_fields(dict(row))}
            for row in rows
        ]

    def delete_lab_result(self, lab_id: int) -> bool:
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM lab_results WHERE id = ?",
                (int(lab_id),),
            )
            return cursor.rowcount > 0

    def add_allergy(
        self,
        profile: str,
        substance: str,
        reaction: str = "",
        severity: str = "moderate",
        kind: str = "drug",
        active: Any = True,
        noted_at: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """新增一条过敏史并返回完整行。"""
        normalized = _normalize_allergy(
            profile, substance, reaction, severity, kind,
            active, noted_at, note,
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO allergies(
                    profile, substance, reaction, severity, kind,
                    active, noted_at, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["substance"],
                    normalized["reaction"], normalized["severity"],
                    normalized["kind"], normalized["active"],
                    normalized["noted_at"], normalized["note"], now, now,
                ),
            )
            allergy_id = int(cursor.lastrowid)
        return {
            "id": allergy_id, **normalized,
            "created_at": now, "updated_at": now,
        }

    def update_allergy(
        self,
        allergy_id: int,
        profile: Any = _UNSET,
        substance: Any = _UNSET,
        reaction: Any = _UNSET,
        severity: Any = _UNSET,
        kind: Any = _UNSET,
        active: Any = _UNSET,
        noted_at: Any = _UNSET,
        note: Any = _UNSET,
    ) -> Optional[Dict[str, Any]]:
        """更新一条过敏史；未给出的字段沿用事务内读到的原值。"""
        try:
            allergy_id = int(allergy_id)
        except (TypeError, ValueError, OverflowError):
            return None
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                """
                SELECT profile, substance, reaction, severity, kind,
                       active, noted_at, note
                  FROM allergies WHERE id = ?
                """,
                (allergy_id,),
            ).fetchone()
            if previous is None:
                return None
            normalized = _normalize_allergy(
                previous["profile"] if profile is _UNSET else profile,
                previous["substance"]
                if substance is _UNSET else substance,
                previous["reaction"] if reaction is _UNSET else reaction,
                previous["severity"] if severity is _UNSET else severity,
                previous["kind"] if kind is _UNSET else kind,
                previous["active"] if active is _UNSET else active,
                previous["noted_at"] if noted_at is _UNSET else noted_at,
                previous["note"] if note is _UNSET else note,
            )
            cursor = self._conn.execute(
                """
                UPDATE allergies
                   SET profile = ?, substance = ?, reaction = ?,
                       severity = ?, kind = ?, active = ?,
                       noted_at = ?, note = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["substance"],
                    normalized["reaction"], normalized["severity"],
                    normalized["kind"], normalized["active"],
                    normalized["noted_at"], normalized["note"], now,
                    allergy_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_allergy(allergy_id)

    def get_allergy(self, allergy_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, substance, reaction, severity, kind,
                       active, noted_at, note, created_at, updated_at
                  FROM allergies WHERE id = ?
                """,
                (int(allergy_id),),
            ).fetchone()
        if row is None:
            return None
        return {**dict(row), "active": bool(row["active"])}

    def list_allergies(
        self,
        profile: Optional[str] = None,
        active_only: bool = False,
        kind: Optional[str] = None,
        q: str = "",
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if active_only:
            conditions.append("active = 1")
        kind_key = str(kind or "").strip().lower()
        if kind_key:
            if kind_key not in ALLERGY_KINDS:
                raise ValueError("过敏类型应为 drug/food/environment/other")
            conditions.append("kind = ?")
            params.append(kind_key)
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(substance LIKE ? ESCAPE '\\' OR reaction LIKE ? ESCAPE '\\'"
                " OR note LIKE ? ESCAPE '\\')"
            )
            params.extend([like, like, like])
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, substance, reaction, severity, kind,
                       active, noted_at, note, created_at, updated_at
                  FROM allergies
                 {where}
                 ORDER BY active DESC, kind ASC, substance ASC, id ASC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [
            {**dict(row), "active": bool(row["active"])}
            for row in rows
        ]

    def delete_allergy(self, allergy_id: int) -> bool:
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM allergies WHERE id = ?",
                (int(allergy_id),),
            )
            return cursor.rowcount > 0

    def add_care_item(
        self,
        profile: str,
        title: str,
        interval_days: Any = 30,
        last_done_at: str = "",
        next_due_at: str = "",
        note: str = "",
        active: Any = True,
    ) -> Dict[str, Any]:
        """新增一条复查计划并返回完整行。"""
        normalized = _normalize_care_item(
            profile, title, interval_days, last_done_at,
            next_due_at, note, active,
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO care_items(
                    profile, title, interval_days, last_done_at,
                    next_due_at, note, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["title"],
                    normalized["interval_days"], normalized["last_done_at"],
                    normalized["next_due_at"], normalized["note"],
                    normalized["active"], now, now,
                ),
            )
            care_id = int(cursor.lastrowid)
        data = {"id": care_id, **normalized, "created_at": now,
                "updated_at": now}
        return {
            **data,
            **_care_item_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def update_care_item_partial(
        self, care_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_care_item(care_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_care_item(
                care_id,
                merged.get("profile") or "",
                merged.get("title") or "",
                merged.get("interval_days") or 30,
                merged.get("last_done_at") or "",
                merged.get("next_due_at") or "",
                merged.get("note") or "",
                bool(merged.get("active", True)),
            )

    def update_care_item(
        self,
        care_id: int,
        profile: str,
        title: str,
        interval_days: Any = 30,
        last_done_at: str = "",
        next_due_at: str = "",
        note: str = "",
        active: Any = True,
    ) -> Optional[Dict[str, Any]]:
        """整体更新一条复查计划；记录不存在时返回 None。"""
        normalized = _normalize_care_item(
            profile, title, interval_days, last_done_at,
            next_due_at, note, active,
        )
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                """
                SELECT profile, title, next_due_at, active
                  FROM care_items WHERE id = ?
                """,
                (int(care_id),),
            ).fetchone()
            if previous is None:
                return None
            cursor = self._conn.execute(
                """
                UPDATE care_items
                   SET profile = ?, title = ?, interval_days = ?,
                       last_done_at = ?, next_due_at = ?, note = ?,
                       active = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["title"],
                    normalized["interval_days"], normalized["last_done_at"],
                    normalized["next_due_at"], normalized["note"],
                    normalized["active"], now, int(care_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            old_title = f"复查：{previous['title'] or '复查'}"[:120]
            new_title = f"复查：{normalized['title'] or '复查'}"[:120]
            old_profile = previous["profile"] or ""
            new_profile = normalized["profile"]
            was_active = bool(int(previous["active"] or 0))
            now_active = bool(int(normalized["active"] or 0))

            def reminder_title_shared(profile: str, title: str) -> bool:
                """同一档案下是否还有另一个启用中的同名复查计划。

                复查提醒没有 id 关联，标题撞名时无法判断归属，保守跳过
                同步，避免删掉/改写另一条计划的提醒。
                """
                row = self._conn.execute(
                    "SELECT 1 FROM care_items "
                    "WHERE id != ? AND active = 1 "
                    "AND profile = ? AND title = ? LIMIT 1",
                    (int(care_id), profile, title),
                ).fetchone()
                return row is not None

            old_shared = reminder_title_shared(
                old_profile, previous["title"] or ""
            )
            new_shared = reminder_title_shared(
                new_profile, normalized["title"] or ""
            )
            if was_active and not now_active:
                # 停用复查计划后不再提醒复查；重新启用时由用户按需重建
                if not old_shared:
                    self._conn.execute(
                        "DELETE FROM reminders "
                        "WHERE done = 0 AND profile = ? AND title = ?",
                        (old_profile, old_title),
                    )
            else:
                if (
                    (old_title != new_title or old_profile != new_profile)
                    and not (old_shared or new_shared)
                ):
                    self._conn.execute(
                        """
                        UPDATE reminders
                           SET title = ?, profile = ?, updated_at = ?
                         WHERE done = 0 AND profile = ? AND title = ?
                        """,
                        (
                            new_title, new_profile, now,
                            old_profile, old_title,
                        ),
                    )
                due = str(normalized["next_due_at"] or "")[:10]
                if (
                    now_active and due
                    and due != (previous["next_due_at"] or "")
                    and not new_shared
                ):
                    try:
                        due_date = datetime.strptime(
                            due, "%Y-%m-%d"
                        ).date()
                    except ValueError:
                        due_date = None
                    if due_date is not None:
                        reminder_due = max(
                            due_date - timedelta(days=7),
                            datetime.now().date(),
                        ).strftime("%Y-%m-%d")
                        self._conn.execute(
                            """
                            UPDATE reminders
                               SET due_at = ?, note = ?, updated_at = ?
                             WHERE done = 0 AND profile = ? AND title = ?
                            """,
                            (
                                reminder_due, f"下次复查 {due}", now,
                                new_profile, new_title,
                            ),
                        )
        return self.get_care_item(care_id)

    def get_care_item(self, care_id: int) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, title, interval_days, last_done_at,
                       next_due_at, note, active, created_at, updated_at
                  FROM care_items WHERE id = ?
                """,
                (int(care_id),),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {
            **data,
            **_care_item_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
            "active": bool(row["active"]),
        }

    def list_care_items(
        self,
        profile: Optional[str] = None,
        active_only: bool = False,
        overdue: bool = False,
        limit: int = 1000,
        start: str = "",
        end: str = "",
        q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if active_only:
            conditions.append("active = 1")
        today = datetime.now().strftime("%Y-%m-%d")
        if overdue:
            conditions.append("active = 1")
            conditions.append("next_due_at < ?")
            params.append(today)
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(title LIKE ? ESCAPE '\\' OR note LIKE ? ESCAPE '\\'"
                " OR profile LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 3)
        range_conditions, range_params = _date_range_conditions(
            "next_due_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, title, interval_days, last_done_at,
                       next_due_at, note, active, created_at, updated_at
                  FROM care_items
                 {where}
                 ORDER BY active DESC,
                          CASE WHEN active = 1 THEN next_due_at END ASC,
                          next_due_at DESC, id DESC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [
            {
                **dict(row),
                **_care_item_view_fields(dict(row), today),
                "active": bool(row["active"]),
            }
            for row in rows
        ]

    def mark_care_item_done(
        self, care_id: int, done_at: str = ""
    ) -> Optional[Dict[str, Any]]:
        """记录一次复查完成，并按间隔推算下次复查日期。"""
        try:
            care_id = int(care_id)
        except (TypeError, ValueError, OverflowError):
            return None
        done_text = str(done_at or "").strip()[:40]
        if done_text:
            done_text = _canonical_local_time(done_text)
            if done_text is None:
                raise ValueError("完成时间格式应为 YYYY-MM-DD 或 ISO 时间")
            if _is_future_timestamp(done_text):
                raise ValueError("完成时间不能晚于当前时间")
            done_text = done_text[:10]
        else:
            done_text = datetime.now().strftime("%Y-%m-%d")
        try:
            done_date = datetime.strptime(done_text, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("完成时间格式应为 YYYY-MM-DD")
        now = self._now()
        with self._lock, self._conn:
            # 间隔与写回必须在同一事务内读取，避免并发改期间隔时
            # 用旧间隔算出错误的 next_due
            row = self._conn.execute(
                "SELECT interval_days FROM care_items WHERE id = ?",
                (care_id,),
            ).fetchone()
            if row is None:
                return None
            interval = max(1, int(row["interval_days"] or 1))
            next_due = (
                done_date + timedelta(days=interval)
            ).strftime("%Y-%m-%d")
            cursor = self._conn.execute(
                """
                UPDATE care_items
                   SET last_done_at = ?, next_due_at = ?, updated_at = ?
                 WHERE id = ?
                """,
                (done_text, next_due, now, care_id),
            )
            if cursor.rowcount == 0:
                return None
            # 返回更新后的整行也在同一锁域内读取
            updated = self._conn.execute(
                """
                SELECT id, profile, title, interval_days, last_done_at,
                       next_due_at, note, active, created_at, updated_at
                  FROM care_items WHERE id = ?
                """,
                (care_id,),
            ).fetchone()
            if updated is None:
                return None
            data = dict(updated)
            return {
                **data,
                **_care_item_view_fields(
                    data, datetime.now().strftime("%Y-%m-%d")
                ),
                "active": bool(updated["active"]),
            }

    def delete_care_item(self, care_id: int) -> bool:
        with self._write_and_checkpoint():
            row = self._conn.execute(
                "SELECT profile, title FROM care_items WHERE id = ?",
                (int(care_id),),
            ).fetchone()
            cursor = self._conn.execute(
                "DELETE FROM care_items WHERE id = ?",
                (int(care_id),),
            )
            if cursor.rowcount == 0:
                return False
            if row is not None:
                # 复查提醒由标题自动生成；同档案还有其它启用中的同名计划
                # 时无法判断提醒归属，保守保留，避免误删另一条计划的提醒
                shared = self._conn.execute(
                    "SELECT 1 FROM care_items "
                    "WHERE active = 1 AND profile = ? AND title = ? "
                    "LIMIT 1",
                    (row["profile"] or "", row["title"] or ""),
                ).fetchone()
                if shared is None:
                    title = f"复查：{row['title'] or '复查'}"[:120]
                    self._conn.execute(
                        "DELETE FROM reminders "
                        "WHERE done = 0 AND profile = ? AND title = ?",
                        (row["profile"] or "", title),
                    )
            return True

    def add_visit_record(
        self,
        profile: str,
        appointment_id: Any = 0,
        visit_date: str = "",
        department: str = "",
        doctor: str = "",
        facility: str = "",
        diagnosis: str = "",
        advice: str = "",
        prescription: str = "",
        next_followup_date: str = "",
        note: str = "",
    ) -> Dict[str, Any]:
        """新增一条就诊记录并返回完整行。"""
        normalized = _normalize_visit_record(
            profile, appointment_id, visit_date, department, doctor,
            facility, diagnosis, advice, prescription,
            next_followup_date, note,
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO visit_records(
                    profile, appointment_id, visit_date, department,
                    doctor, facility, diagnosis, advice, prescription,
                    next_followup_date, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["appointment_id"],
                    normalized["visit_date"], normalized["department"],
                    normalized["doctor"], normalized["facility"],
                    normalized["diagnosis"], normalized["advice"],
                    normalized["prescription"],
                    normalized["next_followup_date"], normalized["note"],
                    now, now,
                ),
            )
            visit_id = int(cursor.lastrowid)
        data = {"id": visit_id, **normalized, "created_at": now,
                "updated_at": now}
        return {
            **data,
            **_visit_record_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def update_visit_record_partial(
        self, visit_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_visit_record(visit_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_visit_record(
                visit_id,
                merged.get("profile") or "",
                merged.get("appointment_id") or 0,
                merged.get("visit_date") or "",
                merged.get("department") or "",
                merged.get("doctor") or "",
                merged.get("facility") or "",
                merged.get("diagnosis") or "",
                merged.get("advice") or "",
                merged.get("prescription") or "",
                merged.get("next_followup_date") or "",
                merged.get("note") or "",
            )

    def _drop_visit_followup_locked(
        self,
        visit_id: int,
        appointment_id: Any,
        reminder_id: Any,
        keep_date: str = "",
    ) -> bool:
        """删除就诊记录自动创建的复诊预约与提醒（调用方持有写锁）。

        keep_date 对应的自动预约已经改到该日期时保留，返回 True：
        用户先手工改期、再把就诊记录的复诊日期改成同一天，不应把刚
        改好的预约静默删除。
        """
        appointment = _safe_db_id(appointment_id)
        if appointment:
            row = self._conn.execute(
                "SELECT note, status, scheduled_at, owner_visit_id, profile"
                " FROM appointments WHERE id = ?",
                (appointment,),
            ).fetchone()
            visit_row = self._conn.execute(
                "SELECT profile FROM visit_records WHERE id = ?",
                (int(visit_id),),
            ).fetchone()
            same_profile = bool(
                row is not None
                and visit_row is not None
                and _normalize_profile_name(row["profile"] or "")
                == _normalize_profile_name(visit_row["profile"] or "")
            )
            auto_created = same_profile and (
                f"由就诊记录 #{int(visit_id)} 自动创建"
                in str(row["note"] or "")
            )
            if (
                same_profile
                and not auto_created
                and int(row["owner_visit_id"] or 0) == int(visit_id)
            ):
                auto_created = True
            if row is not None and not auto_created:
                # 备注可被用户编辑：就诊记录自己的复诊链接同样是归属
                # 证据，不能因为改备注就留下无法级联清理的旧预约
                linked = self._conn.execute(
                    "SELECT 1 FROM visit_records"
                    " WHERE id = ? AND followup_appointment_id = ?",
                    (int(visit_id), appointment),
                ).fetchone()
                auto_created = linked is not None
            # 只有仍未就诊的自动复诊预约才删除；已完成/已取消的是就诊
            # 历史，不能因为修改上一次就诊而复诊日期就消失
            if auto_created and str(row["status"] or "") == "upcoming":
                if keep_date and (
                    str(row["scheduled_at"] or "")[:10] == keep_date
                ):
                    return True
                self._conn.execute(
                    "DELETE FROM appointments WHERE id = ?",
                    (appointment,),
                )
                self._conn.execute(
                    "DELETE FROM reminders"
                    " WHERE done = 0 AND appointment_id = ?",
                    (appointment,),
                )
                # 从该预约补记的就诊记录不能留下悬空引用
                self._conn.execute(
                    "UPDATE visit_records SET appointment_id = 0"
                    " WHERE appointment_id = ?",
                    (appointment,),
                )
            self._conn.execute(
                "UPDATE visit_records SET followup_appointment_id = 0"
                " WHERE followup_appointment_id = ?",
                (appointment,),
            )
        reminder = _safe_db_id(reminder_id)
        if reminder:
            # 已完成的提醒是历史记录，不能随复诊改期删除
            self._conn.execute(
                "DELETE FROM reminders WHERE id = ? AND done = 0",
                (reminder,),
            )
        return False

    def update_visit_record(
        self,
        visit_id: int,
        profile: str,
        appointment_id: Any = 0,
        visit_date: str = "",
        department: str = "",
        doctor: str = "",
        facility: str = "",
        diagnosis: str = "",
        advice: str = "",
        prescription: str = "",
        next_followup_date: str = "",
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """整体更新一条就诊记录；记录不存在时返回 None。"""
        normalized = _normalize_visit_record(
            profile, appointment_id, visit_date, department, doctor,
            facility, diagnosis, advice, prescription,
            next_followup_date, note,
        )
        now = self._now()
        with self._lock, self._conn:
            old_row = self._conn.execute(
                """
                SELECT profile, next_followup_date, followup_appointment_id,
                       followup_reminder_id
                  FROM visit_records WHERE id = ?
                """,
                (int(visit_id),),
            ).fetchone()
            cursor = self._conn.execute(
                """
                UPDATE visit_records
                   SET profile = ?, appointment_id = ?, visit_date = ?,
                       department = ?, doctor = ?, facility = ?,
                       diagnosis = ?, advice = ?, prescription = ?,
                       next_followup_date = ?, note = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["appointment_id"],
                    normalized["visit_date"], normalized["department"],
                    normalized["doctor"], normalized["facility"],
                    normalized["diagnosis"], normalized["advice"],
                    normalized["prescription"],
                    normalized["next_followup_date"], normalized["note"],
                    now, int(visit_id),
                ),
            )
            if cursor.rowcount == 0:
                return None
            # 记录改到其它档案时，已关联的附件必须跟随，保持
            # 「附件与记录同档案」的不变量
            if (
                old_row is not None
                and _normalize_profile_name(old_row["profile"] or "")
                != normalized["profile"]
            ):
                self._conn.execute(
                    "UPDATE attachments SET profile = ?"
                    " WHERE kind = 'visit' AND ref_id = ?",
                    (normalized["profile"], int(visit_id)),
                )
                # 复诊预约与提醒跟随就诊改档案，否则新档案看不到提醒、
                # 旧档案却保留别人的待办
                linked_appointment = _safe_db_id(
                    old_row["followup_appointment_id"]
                )
                linked_reminder = _safe_db_id(
                    old_row["followup_reminder_id"]
                )
                if linked_appointment:
                    self._conn.execute(
                        "UPDATE appointments SET profile = ?,"
                        " updated_at = ? WHERE id = ?",
                        (
                            normalized["profile"], now,
                            linked_appointment,
                        ),
                    )
                    self._conn.execute(
                        "UPDATE reminders SET profile = ?,"
                        " updated_at = ? WHERE appointment_id = ?",
                        (
                            normalized["profile"], now,
                            linked_appointment,
                        ),
                    )
                if linked_reminder:
                    self._conn.execute(
                        "UPDATE reminders SET profile = ?,"
                        " updated_at = ? WHERE id = ?",
                        (normalized["profile"], now, linked_reminder),
                    )
            # 复诊日期变化后撤销旧的自动预约/提醒：只清关联编号会留下
            # 幽灵预约和仍会到期的旧提醒，再次安排时两套并存
            if (
                old_row is not None
                and str(old_row["next_followup_date"] or "")
                != normalized["next_followup_date"]
            ):
                kept = self._drop_visit_followup_locked(
                    int(visit_id),
                    old_row["followup_appointment_id"],
                    old_row["followup_reminder_id"],
                    keep_date=normalized["next_followup_date"],
                )
                if not kept:
                    self._conn.execute(
                        """
                        UPDATE visit_records
                           SET followup_appointment_id = 0,
                               followup_reminder_id = 0
                         WHERE id = ?
                        """,
                        (int(visit_id),),
                    )
        return self.get_visit_record(visit_id)

    def set_visit_followup_links(
        self,
        visit_id: int,
        appointment_id: Any = 0,
        reminder_id: Any = 0,
    ) -> bool:
        """记录就诊复诊安排对应的预约与提醒。

        记录不存在、预约已取消/日期不符、提醒已完成时返回 False，
        避免并发下写入指向失效预约的链接（逾期复诊会被永久抑制）。
        """
        appointment = _safe_db_id(appointment_id)
        reminder = _safe_db_id(reminder_id)
        now = self._now()
        with self._lock, self._conn:
            visit_row = self._conn.execute(
                "SELECT profile, next_followup_date FROM visit_records"
                " WHERE id = ?",
                (int(visit_id),),
            ).fetchone()
            if visit_row is None:
                return False
            visit_profile = _normalize_profile_name(
                str(visit_row["profile"] or "")
            )
            if appointment:
                appointment_row = self._conn.execute(
                    "SELECT profile, status, scheduled_at"
                    " FROM appointments WHERE id = ?",
                    (appointment,),
                ).fetchone()
                if appointment_row is None:
                    return False
                if str(appointment_row["status"] or "") not in (
                    "upcoming", "completed",
                ):
                    return False
                if _normalize_profile_name(
                    str(appointment_row["profile"] or "")
                ) != visit_profile:
                    return False
                followup_date = str(
                    visit_row["next_followup_date"] or ""
                )[:10]
                scheduled = str(
                    appointment_row["scheduled_at"] or ""
                )[:10]
                if followup_date and scheduled and (
                    followup_date != scheduled
                ):
                    return False
            if reminder:
                reminder_row = self._conn.execute(
                    "SELECT profile, done, appointment_id"
                    " FROM reminders WHERE id = ?",
                    (reminder,),
                ).fetchone()
                if reminder_row is None:
                    return False
                if reminder_row["done"]:
                    return False
                if _normalize_profile_name(
                    str(reminder_row["profile"] or "")
                ) != visit_profile:
                    return False
                linked_appointment = int(
                    reminder_row["appointment_id"] or 0
                )
                if appointment and linked_appointment not in (
                    0, appointment
                ):
                    return False
            cursor = self._conn.execute(
                """
                UPDATE visit_records
                   SET followup_appointment_id = ?,
                       followup_reminder_id = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (appointment, reminder, now, int(visit_id)),
            )
            return cursor.rowcount > 0

    def get_visit_record(
        self, visit_id: int
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, appointment_id, visit_date, department,
                       doctor, facility, diagnosis, advice, prescription,
                       next_followup_date, followup_appointment_id,
                       followup_reminder_id, note, created_at, updated_at
                  FROM visit_records WHERE id = ?
                """,
                (int(visit_id),),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {
            **data,
            **_visit_record_view_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def list_visit_records(
        self,
        profile: Optional[str] = None,
        days: int = 0,
        q: str = "",
        limit: int = 1000,
        start: str = "",
        end: str = "",
        search_q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        cutoff = _days_cutoff(days)
        if cutoff:
            conditions.append("substr(visit_date, 1, 10) >= ?")
            params.append(cutoff[:10])
        range_conditions, range_params = _date_range_conditions(
            "visit_date", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(diagnosis LIKE ? ESCAPE '\\' OR advice LIKE ? ESCAPE '\\'"
                " OR doctor LIKE ? ESCAPE '\\'"
                " OR department LIKE ? ESCAPE '\\'"
                " OR facility LIKE ? ESCAPE '\\'"
                " OR note LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 6)
        search_clause, search_params = _like_any_condition(
            (
                "diagnosis", "advice", "prescription", "doctor",
                "department", "facility", "note",
            ),
            search_q,
        )
        if search_clause:
            conditions.append(search_clause)
            params.extend(search_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, appointment_id, visit_date, department,
                       doctor, facility, diagnosis, advice, prescription,
                       next_followup_date, followup_appointment_id,
                       followup_reminder_id, note, created_at, updated_at
                  FROM visit_records
                 {where}
                 ORDER BY visit_date DESC, id DESC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            {**dict(row), **_visit_record_view_fields(dict(row), today)}
            for row in rows
        ]

    def delete_visit_record(self, visit_id: int) -> bool:
        with self._write_and_checkpoint():
            row = self._conn.execute(
                "SELECT followup_appointment_id, followup_reminder_id"
                "  FROM visit_records WHERE id = ?",
                (int(visit_id),),
            ).fetchone()
            if row is not None:
                # 删除就诊记录时一并撤销它自动创建的复诊预约与提醒
                self._drop_visit_followup_locked(
                    int(visit_id),
                    row["followup_appointment_id"],
                    row["followup_reminder_id"],
                )
            cursor = self._conn.execute(
                "DELETE FROM visit_records WHERE id = ?",
                (int(visit_id),),
            )
            return cursor.rowcount > 0

    def add_condition(
        self,
        profile: str,
        name: str,
        icd10: str = "",
        diagnosed_at: str = "",
        status: str = "active",
        severity: str = "moderate",
        note: str = "",
    ) -> Dict[str, Any]:
        """新增一条慢病/健康问题并返回完整行。"""
        normalized = _normalize_condition(
            profile, name, icd10, diagnosed_at, status, severity, note,
        )
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO conditions(
                    profile, name, icd10, diagnosed_at, status,
                    severity, note, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"], normalized["name"],
                    normalized["icd10"], normalized["diagnosed_at"],
                    normalized["status"], normalized["severity"],
                    normalized["note"], now, now,
                ),
            )
            condition_id = int(cursor.lastrowid)
        return {
            "id": condition_id, **normalized,
            "created_at": now, "updated_at": now,
        }

    def update_condition(
        self,
        condition_id: int,
        profile: Any = _UNSET,
        name: Any = _UNSET,
        icd10: Any = _UNSET,
        diagnosed_at: Any = _UNSET,
        status: Any = _UNSET,
        severity: Any = _UNSET,
        note: Any = _UNSET,
    ) -> Optional[Dict[str, Any]]:
        """更新一条慢病/健康问题；未给出的字段沿用事务内读到的原值。"""
        try:
            condition_id = int(condition_id)
        except (TypeError, ValueError, OverflowError):
            return None
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                """
                SELECT profile, name, icd10, diagnosed_at, status,
                       severity, note
                  FROM conditions WHERE id = ?
                """,
                (condition_id,),
            ).fetchone()
            if previous is None:
                return None
            normalized = _normalize_condition(
                previous["profile"] if profile is _UNSET else profile,
                previous["name"] if name is _UNSET else name,
                previous["icd10"] if icd10 is _UNSET else icd10,
                previous["diagnosed_at"]
                if diagnosed_at is _UNSET else diagnosed_at,
                previous["status"] if status is _UNSET else status,
                previous["severity"] if severity is _UNSET else severity,
                previous["note"] if note is _UNSET else note,
            )
            cursor = self._conn.execute(
                """
                UPDATE conditions
                   SET profile = ?, name = ?, icd10 = ?, diagnosed_at = ?,
                       status = ?, severity = ?, note = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"], normalized["name"],
                    normalized["icd10"], normalized["diagnosed_at"],
                    normalized["status"], normalized["severity"],
                    normalized["note"], now, condition_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_condition(condition_id)

    def get_condition(
        self, condition_id: int
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, name, icd10, diagnosed_at, status,
                       severity, note, created_at, updated_at
                  FROM conditions WHERE id = ?
                """,
                (int(condition_id),),
            ).fetchone()
        return dict(row) if row else None

    def list_conditions(
        self,
        profile: Optional[str] = None,
        active_only: bool = False,
        status: Optional[str] = None,
        limit: int = 1000,
        q: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 1000), 100000))
        conditions_sql: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions_sql.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        if active_only:
            conditions_sql.append("status != 'resolved'")
        status_key = str(status or "").strip().lower()
        if status_key:
            if status_key not in CONDITION_STATUSES:
                raise ValueError("状态应为 active/controlled/resolved")
            conditions_sql.append("status = ?")
            params.append(status_key)
        query = str(q or "").strip()
        if query:
            like = _like_pattern(query)
            conditions_sql.append(
                "(name LIKE ? ESCAPE '\\' OR icd10 LIKE ? ESCAPE '\\'"
                " OR note LIKE ? ESCAPE '\\' OR profile LIKE ? ESCAPE '\\')"
            )
            params.extend([like] * 4)
        where = (
            f"WHERE {' AND '.join(conditions_sql)}"
            if conditions_sql else ""
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, name, icd10, diagnosed_at, status,
                       severity, note, created_at, updated_at
                  FROM conditions
                 {where}
                 ORDER BY CASE status
                              WHEN 'active' THEN 0
                              WHEN 'controlled' THEN 1
                              ELSE 2
                          END,
                          name ASC, id ASC
                 LIMIT ?
                """,
                [*params, limit],
            ).fetchall()
        return [dict(row) for row in rows]

    def delete_condition(self, condition_id: int) -> bool:
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM conditions WHERE id = ?",
                (int(condition_id),),
            )
            return cursor.rowcount > 0

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
                    profile, symptom, severity, note, recorded_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["symptom"],
                    normalized["severity"],
                    normalized["note"],
                    normalized["recorded_at"],
                    now,
                    now,
                ),
            )
            symptom_id = int(cursor.lastrowid)
        return {
            "id": symptom_id, **normalized,
            "created_at": now, "updated_at": now,
        }

    def list_symptoms(
        self,
        profile: Optional[str] = None,
        limit: int = 100,
        days: int = 0,
        offset: int = 0,
        start: str = "",
        end: str = "",
        search_q: str = "",
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
        range_conditions, range_params = _date_range_conditions(
            "recorded_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        search_clause, search_params = _like_any_condition(
            ("symptom", "note"), search_q
        )
        if search_clause:
            conditions.append(search_clause)
            params.extend(search_params)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        sql = f"""
            SELECT id, profile, symptom, severity, note, recorded_at,
                   created_at, updated_at
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

    def update_symptom_partial(
        self, symptom_id: int, **fields: Any
    ) -> Optional[Dict[str, Any]]:
        """事务内读取当前值后合并部分字段，避免锁外快照覆盖并发修改。"""
        with self._lock:
            current = self.get_symptom(symptom_id)
            if current is None:
                return None
            merged = {**current, **fields}
            return self.update_symptom(
                symptom_id,
                merged.get("profile") or "",
                merged.get("symptom") or "",
                merged.get("severity"),
                merged.get("recorded_at") or "",
                merged.get("note") or "",
            )

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
            now = self._now()
            cursor = self._conn.execute(
                """
                UPDATE symptoms
                   SET profile = ?, symptom = ?, severity = ?,
                       note = ?, recorded_at = ?, updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["symptom"],
                    normalized["severity"],
                    normalized["note"],
                    normalized["recorded_at"],
                    now,
                    symptom_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            row = self._conn.execute(
                """
                SELECT id, profile, symptom, severity, note,
                       recorded_at, created_at, updated_at
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
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM symptoms WHERE id = ?", (symptom_id,)
            )
            return cursor.rowcount > 0

    def _insert_medication_history(
        self,
        medication_id: int,
        profile: str,
        action: str,
        detail: str,
        changed_at: str,
    ) -> None:
        """在用药事务内追加一条变更记录。"""
        self._conn.execute(
            """
            INSERT INTO medication_history(
                medication_id, profile, action, detail, changed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                int(medication_id),
                _normalize_profile_name(profile),
                str(action or "update")[:20],
                " ".join(str(detail or "").split())[:300],
                str(changed_at or self._now())[:40],
            ),
        )

    def list_medication_history(
        self,
        medication_id: Optional[int] = None,
        limit: int = 1000,
    ) -> List[Dict[str, Any]]:
        """按时间倒序返回用药变更记录，可按药物筛选。"""
        limit = max(1, min(int(limit or 1000), 100000))
        sql = (
            "SELECT id, medication_id, profile, action, detail, changed_at "
            "FROM medication_history"
        )
        params: List[Any] = []
        if medication_id is not None:
            sql += " WHERE medication_id = ?"
            params.append(_safe_db_id(medication_id))
        sql += " ORDER BY changed_at DESC, id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(
                sql, [*params, limit]
            ).fetchall()
        return [dict(row) for row in rows]

    def add_medication(
        self,
        profile: str,
        name: str,
        dose: str = "",
        frequency: str = "",
        note: str = "",
        active: Any = True,
        stock: Any = None,
        stock_unit: str = "",
        low_stock_threshold: Any = 0,
        expiry_date: str = "",
        started_at: str = "",
        stopped_at: str = "",
        stop_reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """新增一条用药记录；无效时返回 None。"""
        normalized = _normalize_medication(
            profile, name, dose, frequency, note, active,
            stock, stock_unit, low_stock_threshold, expiry_date,
            started_at, stopped_at, stop_reason,
        )
        if normalized is None:
            return None
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO medications(
                    profile, name, dose, frequency, note, active,
                    stock, stock_unit, low_stock_threshold, expiry_date,
                    started_at, stopped_at, stop_reason,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized["profile"],
                    normalized["name"],
                    normalized["dose"],
                    normalized["frequency"],
                    normalized["note"],
                    normalized["active"],
                    normalized["stock"],
                    normalized["stock_unit"],
                    normalized["low_stock_threshold"],
                    normalized["expiry_date"],
                    normalized["started_at"],
                    normalized["stopped_at"],
                    normalized["stop_reason"],
                    now,
                    now,
                ),
            )
            medication_id = int(cursor.lastrowid)
            creation = " ".join(
                part for part in (
                    normalized["name"],
                    normalized["dose"],
                    normalized["frequency"],
                ) if part
            )
            self._insert_medication_history(
                medication_id,
                normalized["profile"],
                "create",
                f"创建：{creation}" if creation else "创建用药记录",
                now,
            )
        data = {"id": medication_id, **normalized, "created_at": now,
                "updated_at": now}
        return {
            **data,
            **_medication_stock_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
        }

    def list_medications(
        self,
        profile: Optional[str] = None,
        active_only: bool = False,
        limit: int = 1000,
        offset: int = 0,
        days: int = 7,
        search_q: str = "",
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
        search_clause, search_params = _like_any_condition(
            ("name", "dose", "frequency", "note"), search_q
        )
        if search_clause:
            conditions.append(search_clause)
            params.extend(search_params)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        today_start = f"{today}T00:00"
        tomorrow_start = (
            now + timedelta(days=1)
        ).strftime("%Y-%m-%dT00:00")
        week_cutoff = (now - timedelta(days=6)).strftime("%Y-%m-%d")
        range_cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        week_start = f"{week_cutoff}T00:00"
        range_start = f"{range_cutoff}T00:00"
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, name, dose, frequency, note, active,
                       stock, stock_unit, low_stock_threshold, expiry_date,
                       started_at, stopped_at, stop_reason,
                       created_at, updated_at,
                       (
                           SELECT MAX(taken_at) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 0
                              AND l.taken_at < ?
                       ) AS last_taken_at,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 0
                              AND l.taken_at >= ? AND l.taken_at < ?
                       ) AS today_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 0
                              AND l.taken_at >= ? AND l.taken_at < ?
                       ) AS week_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 0
                              AND l.taken_at >= ? AND l.taken_at < ?
                       ) AS range_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 1
                              AND l.taken_at >= ? AND l.taken_at < ?
                       ) AS skipped_today,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 1
                              AND l.taken_at >= ? AND l.taken_at < ?
                       ) AS skipped_range,
                       (
                           SELECT MAX(taken_at) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 1
                              AND l.taken_at < ?
                       ) AS last_skip_at,
                       (
                           SELECT l.skip_reason FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 1
                              AND l.taken_at < ?
                            ORDER BY l.taken_at DESC, l.id DESC LIMIT 1
                       ) AS last_skip_reason
                  FROM medications{where}
                 ORDER BY active DESC, name ASC, id ASC
                 LIMIT ? OFFSET ?
                """,
                [
                    tomorrow_start,
                    today_start, tomorrow_start,
                    week_start, tomorrow_start,
                    range_start, tomorrow_start,
                    today_start, tomorrow_start,
                    range_start, tomorrow_start,
                    tomorrow_start, tomorrow_start,
                ]
                + params
                + [limit, offset],
            ).fetchall()
        return [
            {
                **dict(row),
                **_medication_stock_fields(dict(row), today),
                "active": bool(row["active"]),
                "today_count": int(row["today_count"] or 0),
                "week_count": int(row["week_count"] or 0),
                "range_count": int(row["range_count"] or 0),
                "range_days": days,
                "skipped_today": int(row["skipped_today"] or 0),
                "skipped_range": int(row["skipped_range"] or 0),
                "last_skip_at": row["last_skip_at"] or "",
                "last_skip_reason": row["last_skip_reason"] or "",
                "last_skip_reason_label": MEDICATION_SKIP_REASONS.get(
                    row["last_skip_reason"] or "", ""
                ),
                "last_taken_at": row["last_taken_at"] or "",
                "expected_week_doses": _expected_week_doses(
                    row["frequency"],
                    row["started_at"] or row["created_at"],
                    now,
                    row["stopped_at"] or "",
                ),
                "expected_range_doses": expected_doses_for_period(
                    row["frequency"],
                    row["started_at"] or row["created_at"],
                    now,
                    days,
                    row["stopped_at"] or "",
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
                       stock, stock_unit, low_stock_threshold, expiry_date,
                       started_at, stopped_at, stop_reason,
                       created_at, updated_at
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {
            **data,
            **_medication_stock_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
            "active": bool(row["active"]),
        }

    def update_medication(
        self,
        medication_id: int,
        profile: Any = _UNSET,
        name: Any = _UNSET,
        dose: Any = _UNSET,
        frequency: Any = _UNSET,
        note: Any = _UNSET,
        active: Any = _UNSET,
        stock: Any = _UNSET,
        stock_unit: Any = _UNSET,
        low_stock_threshold: Any = _UNSET,
        expiry_date: Any = _UNSET,
        started_at: Any = _UNSET,
        stopped_at: Any = _UNSET,
        stop_reason: Any = _UNSET,
    ) -> Optional[Dict[str, Any]]:
        """更新一条用药记录；未给出的字段沿用事务内读到的原值。"""
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return None
        now = self._now()
        with self._lock, self._conn:
            previous = self._conn.execute(
                """
                SELECT profile, name, dose, frequency, note, active,
                       stock, stock_unit, low_stock_threshold, expiry_date,
                       started_at, stopped_at, stop_reason
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
            if previous is None:
                return None
            normalized = _normalize_medication(
                previous["profile"] if profile is _UNSET else profile,
                previous["name"] if name is _UNSET else name,
                previous["dose"] if dose is _UNSET else dose,
                previous["frequency"]
                if frequency is _UNSET else frequency,
                previous["note"] if note is _UNSET else note,
                previous["active"] if active is _UNSET else active,
                previous["stock"] if stock is _UNSET else stock,
                previous["stock_unit"]
                if stock_unit is _UNSET else stock_unit,
                previous["low_stock_threshold"]
                if low_stock_threshold is _UNSET else low_stock_threshold,
                previous["expiry_date"]
                if expiry_date is _UNSET else expiry_date,
                previous["started_at"]
                if started_at is _UNSET else started_at,
                previous["stopped_at"]
                if stopped_at is _UNSET else stopped_at,
                previous["stop_reason"]
                if stop_reason is _UNSET else stop_reason,
            )
            if normalized is None:
                return None
            # 只有库存基线实际变化（盘点/换单位）时才清空历史打卡的
            # stock_delta；普通编辑即使回传当前库存也不能破坏历史扣减。
            reset_stock = (
                normalized["stock"] != previous["stock"]
                or normalized["stock_unit"] != previous["stock_unit"]
            )
            cursor = self._conn.execute(
                """
                UPDATE medications
                   SET profile = ?, name = ?, dose = ?, frequency = ?,
                       note = ?, active = ?, stock = ?, stock_unit = ?,
                       low_stock_threshold = ?, expiry_date = ?,
                       started_at = ?, stopped_at = ?, stop_reason = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["name"],
                    normalized["dose"],
                    normalized["frequency"],
                    normalized["note"],
                    normalized["active"],
                    normalized["stock"],
                    normalized["stock_unit"],
                    normalized["low_stock_threshold"],
                    normalized["expiry_date"],
                    normalized["started_at"],
                    normalized["stopped_at"],
                    normalized["stop_reason"],
                    now,
                    medication_id,
                ),
            )
            if cursor.rowcount == 0:
                return None
            if reset_stock:
                # 手动盘点后，历史打卡不再影响新的库存基线
                self._conn.execute(
                    """
                    UPDATE medication_logs SET stock_delta = 0
                     WHERE medication_id = ?
                    """,
                    (medication_id,),
                )
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
            changes: List[str] = []
            if previous["name"] != normalized["name"]:
                changes.append(
                    f"药名 {previous['name']} → {normalized['name']}"
                )
            if previous["dose"] != normalized["dose"]:
                changes.append(
                    f"剂量 {previous['dose'] or '—'} → "
                    f"{normalized['dose'] or '—'}"
                )
            if previous["frequency"] != normalized["frequency"]:
                changes.append(
                    f"频次 {previous['frequency'] or '—'} → "
                    f"{normalized['frequency'] or '—'}"
                )
            was_active = bool(int(previous["active"] or 0))
            now_active = bool(int(normalized["active"] or 0))
            if was_active != now_active:
                if now_active:
                    changes.append("重新启用")
                else:
                    reason = normalized["stop_reason"]
                    changes.append(
                        f"停药{('：' + reason) if reason else ''}"
                    )
                    # 只删除服药提醒；补货/药物到期提醒仍需保留
                    self._conn.execute(
                        """
                        DELETE FROM reminders
                         WHERE done = 0 AND medication_id = ? AND title = ?
                        """,
                        (
                            medication_id,
                            f"用药：{normalized['name']}"[:120],
                        ),
                    )
            if previous["started_at"] != normalized["started_at"]:
                changes.append(
                    f"开始日期 {previous['started_at'] or '—'} → "
                    f"{normalized['started_at'] or '—'}"
                )
            if previous["stopped_at"] != normalized["stopped_at"]:
                changes.append(
                    f"停药日期 {previous['stopped_at'] or '—'} → "
                    f"{normalized['stopped_at'] or '—'}"
                )
            if (
                previous["stop_reason"] != normalized["stop_reason"]
                and not (was_active and not now_active)
            ):
                changes.append(
                    f"停药原因 {previous['stop_reason'] or '—'} → "
                    f"{normalized['stop_reason'] or '—'}"
                )
            if changes:
                if was_active and not now_active:
                    action = "stop"
                elif not was_active and now_active:
                    action = "start"
                else:
                    action = "update"
                self._insert_medication_history(
                    medication_id,
                    normalized["profile"],
                    action,
                    "；".join(changes),
                    now,
                )
            row = self._conn.execute(
                """
                SELECT id, profile, name, dose, frequency, note, active,
                       stock, stock_unit, low_stock_threshold, expiry_date,
                       started_at, stopped_at, stop_reason,
                       created_at, updated_at
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        return {
            **data,
            **_medication_stock_fields(
                data, datetime.now().strftime("%Y-%m-%d")
            ),
            "active": bool(row["active"]),
        }

    def delete_medication(self, medication_id: int) -> bool:
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._write_and_checkpoint():
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
                "DELETE FROM medication_history WHERE medication_id = ?",
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
        skipped: Any = False,
        skip_reason: str = "",
    ) -> Optional[Dict[str, Any]]:
        """记录一次用药或未服；药物不存在、时间或原因无效时返回 None。"""
        try:
            medication_id = int(medication_id)
        except (TypeError, ValueError, OverflowError):
            return None
        skipped_flag = 1 if str(skipped).lower() in {"1", "true", "on", "yes"} else 0
        reason_key = ""
        if skipped_flag:
            reason_key = str(skip_reason or "").strip().lower()
            if reason_key not in MEDICATION_SKIP_REASONS:
                return None
        explicit_time = bool(str(taken_at or "").strip())
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
                """
                SELECT id, profile, stock, dose, stock_unit
                  FROM medications WHERE id = ?
                """,
                (medication_id,),
            ).fetchone()
            if medication is None:
                return None
            stock = medication["stock"]
            if not explicit_time:
                # 默认时间戳的重复打卡（双击/响应丢失重试）短窗口内只记一次；
                # 重试可能跨到下一分钟，因此按分钟窗口而不是精确相等匹配；
                # 显式传入时间的记录用于补录或统计，仍允许多条
                retry_now = datetime.now()
                retry_cutoff = (
                    retry_now - timedelta(seconds=120)
                ).isoformat(timespec="seconds")
                minute_cutoff = (
                    retry_now - timedelta(seconds=120)
                ).isoformat(timespec="minutes")
                if skipped_flag:
                    duplicate = self._conn.execute(
                        """
                        SELECT id, note, skip_reason, taken_at, created_at
                          FROM medication_logs
                         WHERE medication_id = ? AND skipped = 1
                           AND skip_reason = ?
                           AND taken_at >= ? AND created_at >= ?
                         ORDER BY id DESC LIMIT 1
                        """,
                        (
                            medication_id, reason_key,
                            minute_cutoff, retry_cutoff,
                        ),
                    ).fetchone()
                    if duplicate is not None:
                        return {
                            "id": int(duplicate["id"]),
                            "medication_id": medication["id"],
                            "profile": medication["profile"],
                            "taken_at": duplicate["taken_at"],
                            "note": duplicate["note"],
                            "stock_delta": 0.0,
                            "stock_after": stock,
                            "skipped": True,
                            "skip_reason": duplicate["skip_reason"] or "",
                            "skip_reason_label": MEDICATION_SKIP_REASONS.get(
                                duplicate["skip_reason"] or "", ""
                            ),
                            "created_at": duplicate["created_at"],
                            "duplicate": True,
                        }
                else:
                    duplicate = self._conn.execute(
                        """
                        SELECT id, note, stock_delta, taken_at, created_at
                          FROM medication_logs
                         WHERE medication_id = ? AND skipped = 0
                           AND taken_at >= ? AND note = ?
                           AND created_at >= ?
                         ORDER BY id LIMIT 1
                        """,
                        (medication_id, minute_cutoff, note, retry_cutoff),
                    ).fetchone()
                    if duplicate is not None:
                        return {
                            "id": int(duplicate["id"]),
                            "medication_id": medication["id"],
                            "profile": medication["profile"],
                            "taken_at": duplicate["taken_at"],
                            "note": duplicate["note"],
                            "stock_delta": float(duplicate["stock_delta"] or 0),
                            "stock_after": stock,
                            "skipped": False,
                            "skip_reason": "",
                            "skip_reason_label": "",
                            "created_at": duplicate["created_at"],
                            "duplicate": True,
                        }
            stock_delta = 0.0
            stock_after: Optional[float] = None
            if not skipped_flag and stock is not None:
                stock_before = max(0.0, float(stock))
                dose_units = medication_dose_units(
                    medication["dose"], medication["stock_unit"]
                )
                stock_delta = -min(dose_units, stock_before)
                stock_after = round(stock_before + stock_delta, 2)
                self._conn.execute(
                    """
                    UPDATE medications
                       SET stock = ?, updated_at = ?
                     WHERE id = ?
                    """,
                    (stock_after, now, medication_id),
                )
            cursor = self._conn.execute(
                """
                INSERT INTO medication_logs(
                    medication_id, profile, taken_at, note, stock_delta,
                    skipped, skip_reason, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    medication["id"],
                    medication["profile"],
                    taken_at,
                    note,
                    stock_delta,
                    skipped_flag,
                    reason_key,
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
            "stock_delta": stock_delta,
            "stock_after": stock_after,
            "skipped": bool(skipped_flag),
            "skip_reason": reason_key,
            "skip_reason_label": MEDICATION_SKIP_REASONS.get(reason_key, ""),
            "created_at": now,
        }

    def list_medication_logs(
        self,
        medication_id: Optional[int] = None,
        profile: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        days: int = 0,
        start: str = "",
        end: str = "",
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
            # 原始时间戳范围才能命中 (medication_id, taken_at) 索引
            conditions.append("l.taken_at >= ?")
            params.append(cutoff)
            tomorrow = (
                datetime.now() + timedelta(days=1)
            ).strftime("%Y-%m-%dT00:00")
            conditions.append("l.taken_at < ?")
            params.append(tomorrow)
        range_conditions, range_params = _date_range_conditions(
            "l.taken_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT l.id, l.medication_id, l.profile, l.taken_at, l.note,
                       l.stock_delta, l.skipped, l.skip_reason, l.created_at,
                       m.name AS medication_name,
                       m.dose AS medication_dose
                  FROM medication_logs l
                  LEFT JOIN medications m ON m.id = l.medication_id{where}
                 ORDER BY l.taken_at DESC, l.id DESC
                 LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        rows = [dict(row) for row in rows]
        for row in rows:
            row["skipped"] = bool(row.get("skipped"))
            row["skip_reason_label"] = MEDICATION_SKIP_REASONS.get(
                row.get("skip_reason") or "", ""
            )
        return rows

    def get_medication_log(self, log_id: int) -> Optional[Dict[str, Any]]:
        try:
            log_id = int(log_id)
        except (TypeError, ValueError, OverflowError):
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT l.id, l.medication_id, l.profile, l.taken_at, l.note,
                       l.stock_delta, l.skipped, l.skip_reason, l.created_at,
                       m.name AS medication_name
                  FROM medication_logs l
                  LEFT JOIN medications m ON m.id = l.medication_id
                 WHERE l.id = ?
                """,
                (log_id,),
            ).fetchone()
        if row is None:
            return None
        result = dict(row)
        result["skipped"] = bool(result.get("skipped"))
        result["skip_reason_label"] = MEDICATION_SKIP_REASONS.get(
            result.get("skip_reason") or "", ""
        )
        return result

    def delete_medication_log(self, log_id: int) -> bool:
        try:
            log_id = int(log_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._write_and_checkpoint():
            row = self._conn.execute(
                """
                SELECT medication_id, stock_delta
                  FROM medication_logs WHERE id = ?
                """,
                (log_id,),
            ).fetchone()
            if row is None:
                return False
            cursor = self._conn.execute(
                "DELETE FROM medication_logs WHERE id = ?", (log_id,)
            )
            if cursor.rowcount > 0 and float(row["stock_delta"] or 0):
                self._conn.execute(
                    """
                    UPDATE medications
                       SET stock = ROUND(stock - ?, 2), updated_at = ?
                     WHERE id = ? AND stock IS NOT NULL
                    """,
                    (
                        float(row["stock_delta"]),
                        self._now(),
                        int(row["medication_id"]),
                    ),
                )
            return cursor.rowcount > 0

    def add_attachment(
        self,
        profile: str,
        kind: str,
        ref_id: Any,
        original_name: str,
        stored_name: str,
        mime: str,
        size: Any,
        note: str = "",
    ) -> Optional[Dict[str, Any]]:
        """登记一条本地附件；文件本身由调用方保存到附件目录。"""
        kind_key = str(kind or "other").strip().lower()
        if kind_key not in ATTACHMENT_KINDS:
            kind_key = "other"
        try:
            reference = max(0, int(ref_id or 0))
        except (TypeError, ValueError, OverflowError):
            return None
        if reference > MAX_SQLITE_INT:
            return None
        stored = str(stored_name or "").strip()[:80]
        if (
            not stored
            or "/" in stored or "\\" in stored or ".." in stored
        ):
            return None
        name = " ".join(str(original_name or "").split())[
            :MAX_ATTACHMENT_NAME_LENGTH
        ] or "附件"
        mime_key = str(mime or "").strip().lower()
        if mime_key not in ALLOWED_ATTACHMENT_MIMES:
            return None
        try:
            size_value = int(size)
        except (TypeError, ValueError, OverflowError):
            return None
        if size_value <= 0 or size_value > MAX_ATTACHMENT_BYTES:
            return None
        note = " ".join(str(note or "").split())[:300]
        normalized_profile = _normalize_profile_name(profile)
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO attachments(
                    profile, kind, ref_id, original_name, stored_name,
                    mime, size, note, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    normalized_profile,
                    kind_key,
                    reference,
                    name,
                    stored,
                    mime_key,
                    size_value,
                    note,
                    now,
                ),
            )
            attachment_id = int(cursor.lastrowid)
        return {
            "id": attachment_id,
            "profile": normalized_profile,
            "kind": kind_key,
            "kind_label": ATTACHMENT_KINDS[kind_key],
            "ref_id": reference,
            "original_name": name,
            "stored_name": stored,
            "mime": mime_key,
            "size": size_value,
            "note": note,
            "created_at": now,
        }

    def list_attachments(
        self,
        profile: Optional[str] = None,
        kind: Optional[str] = None,
        ref_id: Optional[int] = None,
        limit: int = 100,
        offset: int = 0,
        q: str = "",
        linked: Optional[bool] = None,
        start: str = "",
        end: str = "",
        order: str = "",
    ) -> List[Dict[str, Any]]:
        """按时间倒序返回附件登记信息（文件内容不在数据库中）。"""
        limit = max(1, min(int(limit or 100), 1000))
        try:
            offset = _bounded_offset(offset)
        except (TypeError, ValueError, OverflowError):
            offset = 0
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        kind_key = str(kind or "").strip().lower()
        if kind_key in ATTACHMENT_KINDS:
            conditions.append("kind = ?")
            params.append(kind_key)
        if ref_id is not None:
            try:
                reference = int(ref_id)
            except (TypeError, ValueError, OverflowError):
                return []
            if reference < 0 or reference > MAX_SQLITE_INT:
                return []
            conditions.append("ref_id = ?")
            params.append(reference)
        if linked is not None:
            conditions.append("ref_id > 0" if linked else "ref_id = 0")
        start_day = str(start or "").strip()[:10]
        if start_day:
            conditions.append("substr(created_at, 1, 10) >= ?")
            params.append(start_day)
        end_day = str(end or "").strip()[:10]
        if end_day:
            conditions.append("substr(created_at, 1, 10) <= ?")
            params.append(end_day)
        query = " ".join(str(q or "").split())
        if query:
            like = _like_pattern(query)
            conditions.append(
                "(original_name LIKE ? ESCAPE '\\' "
                "OR note LIKE ? ESCAPE '\\' "
                "OR (kind = 'visit' AND ref_id IN ("
                "SELECT id FROM visit_records "
                "WHERE diagnosis LIKE ? ESCAPE '\\' "
                "OR department LIKE ? ESCAPE '\\' "
                "OR doctor LIKE ? ESCAPE '\\' "
                "OR facility LIKE ? ESCAPE '\\' "
                "OR note LIKE ? ESCAPE '\\')) "
                "OR (kind = 'lab' AND ref_id IN ("
                "SELECT id FROM lab_results "
                "WHERE item LIKE ? ESCAPE '\\' "
                "OR panel LIKE ? ESCAPE '\\' "
                "OR lab_name LIKE ? ESCAPE '\\' "
                "OR note LIKE ? ESCAPE '\\')))"
            )
            params.extend([like] * 11)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        order_sql = {
            "created_asc": "created_at ASC, id ASC",
            "size_desc": "size DESC, id DESC",
            "size_asc": "size ASC, id DESC",
            "name_asc": "original_name COLLATE NOCASE ASC, id DESC",
        }.get(
            str(order or "").strip().lower(),
            "created_at DESC, id DESC",
        )
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, kind, ref_id, original_name,
                       stored_name, mime, size, note, created_at,
                       COUNT(*) OVER() AS _total_count,
                       COALESCE(SUM(size) OVER(), 0) AS _total_size
                  FROM attachments{where}
                 ORDER BY {order_sql}
                 LIMIT ? OFFSET ?
                """,
                params + [limit, offset],
            ).fetchall()
        return [
            {
                **dict(row),
                "kind_label": ATTACHMENT_KINDS.get(
                    row["kind"], ATTACHMENT_KINDS["other"]
                ),
            }
            for row in rows
        ]

    def list_all_attachments(
        self,
        profile: Optional[str] = None,
        start: str = "",
        end: str = "",
    ) -> List[Dict[str, Any]]:
        """返回附件登记（可按档案与日期筛选，不做分页上限截断）。

        供完整性检查、时间线与健康搜索使用：一次扫描取全量，
        避免逐页 OFFSET + 窗口聚合的重复开销。
        """
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        start_day = str(start or "").strip()[:10]
        if start_day:
            conditions.append("substr(created_at, 1, 10) >= ?")
            params.append(start_day)
        end_day = str(end or "").strip()[:10]
        if end_day:
            conditions.append("substr(created_at, 1, 10) <= ?")
            params.append(end_day)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, profile, kind, ref_id, original_name,
                       stored_name, mime, size, note, created_at
                  FROM attachments{where}
                 ORDER BY created_at DESC, id DESC
                """,
                params,
            ).fetchall()
        return [
            {
                **dict(row),
                "kind_label": ATTACHMENT_KINDS.get(
                    row["kind"], ATTACHMENT_KINDS["other"]
                ),
            }
            for row in rows
        ]

    def count_attachments_by_day(
        self,
        profile: Optional[str] = None,
        start: str = "",
        end: str = "",
    ) -> Dict[str, int]:
        """按上传日期统计附件份数，供健康月历使用。"""
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        range_conditions, range_params = _date_range_conditions(
            "created_at", start, end
        )
        conditions.extend(range_conditions)
        params.extend(range_params)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT substr(created_at, 1, 10) AS day,
                       COUNT(*) AS total
                  FROM attachments
                 {where}
                 GROUP BY day
                """,
                params,
            ).fetchall()
        return {
            str(row["day"]): int(row["total"])
            for row in rows if row["day"]
        }

    def list_attachment_stored_names(self) -> List[str]:
        """返回全部附件的存储文件名，用于清理前复核。"""
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT stored_name FROM attachments
                 WHERE stored_name != ''
                """
            ).fetchall()
        return [str(row["stored_name"]) for row in rows]

    def attachment_kind_stats(
        self, profile: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """按资料类型统计附件份数与总大小。"""
        conditions: List[str] = []
        params: List[Any] = []
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT kind, COUNT(*) AS total,
                       COALESCE(SUM(size), 0) AS total_size
                  FROM attachments{where}
                 GROUP BY kind
                """,
                params,
            ).fetchall()
        return [
            {
                "kind": str(row["kind"]),
                "kind_label": ATTACHMENT_KINDS.get(
                    row["kind"], ATTACHMENT_KINDS["other"]
                ),
                "count": int(row["total"]),
                "size": int(row["total_size"] or 0),
            }
            for row in rows
        ]

    def count_attachments_by_ref(self, kind: str) -> Dict[int, int]:
        """按关联记录统计附件数量，仅返回已关联的附件。"""
        kind_key = str(kind or "").strip().lower()
        if kind_key not in ATTACHMENT_KINDS:
            return {}
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT ref_id, COUNT(*) AS total
                  FROM attachments
                 WHERE kind = ? AND ref_id > 0
                 GROUP BY ref_id
                """,
                (kind_key,),
            ).fetchall()
        return {
            int(row["ref_id"]): int(row["total"]) for row in rows
        }

    def list_visit_records_by_ids(
        self, ids: Any
    ) -> List[Dict[str, Any]]:
        """按编号批量读取就诊记录，供附件关联摘要使用。"""
        values = {
            parsed for parsed in (_safe_db_id(value) for value in (ids or []))
            if parsed
        }
        if not values:
            return []
        ordered = sorted(values)
        rows: List[Any] = []
        with self._lock:
            for index in range(0, len(ordered), 500):
                chunk = ordered[index:index + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(self._conn.execute(
                    f"""
                    SELECT id, profile, appointment_id, visit_date,
                           department, doctor, facility, diagnosis, advice,
                           prescription, next_followup_date,
                           followup_appointment_id, followup_reminder_id,
                           note, created_at, updated_at
                      FROM visit_records
                     WHERE id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall())
        today = datetime.now().strftime("%Y-%m-%d")
        return [
            {**dict(row), **_visit_record_view_fields(dict(row), today)}
            for row in rows
        ]

    def list_lab_results_by_ids(
        self, ids: Any
    ) -> List[Dict[str, Any]]:
        """按编号批量读取化验结果，供附件关联摘要使用。"""
        values = {
            parsed for parsed in (_safe_db_id(value) for value in (ids or []))
            if parsed
        }
        if not values:
            return []
        ordered = sorted(values)
        rows: List[Any] = []
        with self._lock:
            for index in range(0, len(ordered), 500):
                chunk = ordered[index:index + 500]
                placeholders = ",".join("?" for _ in chunk)
                rows.extend(self._conn.execute(
                    f"""
                    SELECT id, profile, panel, item, value, unit, ref_low,
                           ref_high, tested_at, lab_name, note,
                           created_at, updated_at
                      FROM lab_results
                     WHERE id IN ({placeholders})
                    """,
                    chunk,
                ).fetchall())
        return [
            {**dict(row), **_lab_result_view_fields(dict(row))}
            for row in rows
        ]

    def get_attachment(
        self, attachment_id: int
    ) -> Optional[Dict[str, Any]]:
        try:
            attachment_id = int(attachment_id)
        except (TypeError, ValueError, OverflowError):
            return None
        if attachment_id <= 0 or attachment_id > MAX_SQLITE_INT:
            return None
        with self._lock:
            row = self._conn.execute(
                """
                SELECT id, profile, kind, ref_id, original_name,
                       stored_name, mime, size, note, created_at
                  FROM attachments WHERE id = ?
                """,
                (attachment_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["kind_label"] = ATTACHMENT_KINDS.get(
            data["kind"], ATTACHMENT_KINDS["other"]
        )
        return data

    def set_attachment_refs(
        self,
        attachment_ids: Any,
        ref_id: Any = 0,
        kind: Optional[str] = None,
        profile: Optional[str] = None,
    ) -> int:
        """批量更新附件关联记录；可限定类型与档案，避免跨档案关联。"""
        try:
            reference = max(0, int(ref_id or 0))
        except (TypeError, ValueError, OverflowError):
            return 0
        if reference > MAX_SQLITE_INT:
            return 0
        try:
            ids = sorted({int(value) for value in attachment_ids})
        except (TypeError, ValueError, OverflowError):
            return 0
        if (
            not ids
            or any(value <= 0 or value > MAX_SQLITE_INT for value in ids)
        ):
            return 0
        conditions = [f"id IN ({', '.join('?' for _ in ids)})"]
        params: List[Any] = [reference, *ids]
        kind_key = str(kind or "").strip().lower()
        if kind_key:
            if kind_key not in ATTACHMENT_KINDS:
                return 0
            conditions.append("kind = ?")
            params.append(kind_key)
        if profile is not None:
            conditions.append("profile = ?")
            params.append(_normalize_profile_name(profile))
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE attachments SET ref_id = ? "
                f"WHERE {' AND '.join(conditions)}",
                params,
            )
        return int(cursor.rowcount or 0)

    def update_attachment_note(
        self, attachment_id: int, note: str
    ) -> Optional[Dict[str, Any]]:
        """更新附件备注并返回最新记录；备注空白会清空。"""
        try:
            attachment_id = int(attachment_id)
        except (TypeError, ValueError, OverflowError):
            return None
        if attachment_id <= 0 or attachment_id > MAX_SQLITE_INT:
            return None
        note = " ".join(str(note or "").split())[:300]
        with self._lock, self._conn:
            cursor = self._conn.execute(
                "UPDATE attachments SET note = ? WHERE id = ?",
                (note, attachment_id),
            )
        if int(cursor.rowcount or 0) <= 0:
            return None
        return self.get_attachment(attachment_id)

    def delete_attachment(
        self, attachment_id: int
    ) -> Optional[Dict[str, Any]]:
        """删除附件登记并返回被删记录；文件由调用方清理。"""
        attachment = self.get_attachment(attachment_id)
        if attachment is None:
            return None
        with self._write_and_checkpoint():
            self._conn.execute(
                "DELETE FROM attachments WHERE id = ?", (int(attachment_id),)
            )
        return attachment

    def delete_attachments_bulk(
        self, attachment_ids: Any
    ) -> List[Dict[str, Any]]:
        """批量删除附件登记，只提交并 checkpoint 一次；文件由调用方清理。"""
        values = sorted({
            parsed for parsed in (
                _safe_db_id(value) for value in (attachment_ids or [])
            ) if parsed
        })
        if not values:
            return []
        attachments = []
        for value in values:
            item = self.get_attachment(value)
            if item is not None:
                attachments.append(item)
        if not attachments:
            return []
        placeholders = ",".join("?" for _ in attachments)
        with self._write_and_checkpoint():
            self._conn.execute(
                f"DELETE FROM attachments WHERE id IN ({placeholders})",
                [item["id"] for item in attachments],
            )
        return attachments

    def detach_attachments(self, kind: str, ref_id: int) -> int:
        """解除附件与已删除业务记录（就诊/化验）的关联。"""
        kind_key = str(kind or "").strip().lower()
        if kind_key not in ATTACHMENT_KINDS:
            return 0
        try:
            reference = int(ref_id)
        except (TypeError, ValueError, OverflowError):
            return 0
        if reference <= 0 or reference > MAX_SQLITE_INT:
            return 0
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                UPDATE attachments SET ref_id = 0
                 WHERE kind = ? AND ref_id = ?
                """,
                (kind_key, reference),
            )
            return cursor.rowcount

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
                       measured_at, context, note, created_at
                  FROM health_metrics
                 WHERE id = ?
                """,
                (metric_id,),
            ).fetchone()
        return dict(row) if row else None

    def update_metric(
        self,
        metric_id: int,
        profile: Any = _UNSET,
        metric: Any = _UNSET,
        value1: Any = _UNSET,
        value2: Any = _UNSET,
        measured_at: Any = _UNSET,
        note: Any = _UNSET,
        context: Any = _UNSET,
    ) -> Optional[Dict[str, Any]]:
        """更新一条健康指标记录；未给出的字段沿用事务内读到的原值。"""
        try:
            metric_id = int(metric_id)
        except (TypeError, ValueError, OverflowError):
            return None

        with self._lock, self._conn:
            previous = self._conn.execute(
                """
                SELECT profile, metric, value1, value2, measured_at,
                       note, context
                  FROM health_metrics WHERE id = ?
                """,
                (metric_id,),
            ).fetchone()
            if previous is None:
                return None
            normalized = _normalize_metric(
                previous["profile"] if profile is _UNSET else profile,
                previous["metric"] if metric is _UNSET else metric,
                previous["value1"] if value1 is _UNSET else value1,
                previous["value2"] if value2 is _UNSET else value2,
                previous["measured_at"]
                if measured_at is _UNSET else measured_at,
                previous["note"] if note is _UNSET else note,
                previous["context"] if context is _UNSET else context,
            )
            if normalized is None:
                return None
            now = self._now()
            self._conn.execute(
                """
                UPDATE health_metrics
                   SET profile = ?, metric = ?, value1 = ?, value2 = ?,
                       unit = ?, measured_at = ?, context = ?, note = ?,
                       updated_at = ?
                 WHERE id = ?
                """,
                (
                    normalized["profile"],
                    normalized["metric"],
                    normalized["value1"],
                    normalized["value2"],
                    normalized["unit"],
                    normalized["measured_at"],
                    normalized["context"],
                    normalized["note"],
                    now,
                    metric_id,
                ),
            )

        return {"id": metric_id, **normalized, "updated_at": now}

    def delete_metric(self, metric_id: int) -> bool:
        try:
            metric_id = int(metric_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._write_and_checkpoint():
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
        with self._write_and_checkpoint():
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
            self._prune_table_locked("audit_log", 100000)
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
        match_all: bool = False,
    ) -> int:
        """把以 prefix 开头的审计 target 替换为不可识别内容。

        用于删除健康记录/档案时清理历史审计中残留的症状、药名或档案名。
        match_all=True 时忽略 target 前缀，清理该 action 的全部历史行
        （批量操作没有单条 target，无法按前缀定位）。
        """
        actions = [str(action) for action in actions if str(action)]
        prefix = str(prefix or "")
        if not actions or (not prefix and not match_all):
            return 0
        placeholders = ",".join("?" for _ in actions)
        sets = ["target = ?"]
        params: List[Any] = [str(replacement)]
        if detail is not None:
            sets.append("detail = ?")
            params.append(str(detail)[:500])
        params.extend(actions)
        target_filter = ""
        if not match_all:
            target_filter = "AND (target = ? OR substr(target, 1, ?) = ?)"
            params.extend([prefix, len(prefix), prefix])
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                f"""
                UPDATE audit_log
                   SET {", ".join(sets)}
                 WHERE action IN ({placeholders})
                   {target_filter}
                """,
                params,
            )
            return cursor.rowcount

    def ensure_reminder(
        self,
        title: Any,
        due_at: Any,
        note: str = "",
        session_id: str = "",
        profile: str = "",
        advance_days: Any = 0,
        medication_id: Any = 0,
        vaccination_id: Any = 0,
        appointment_id: Any = 0,
        match_title: bool = True,
        match_due: bool = False,
    ) -> Tuple[Optional[Dict[str, Any]], bool]:
        """原子地查重并插入提醒；返回 (提醒, 是否新建)。

        自动建提醒的四个入口原先都是"先查后插"两段独立加锁，双击或
        并发请求会各插一条。这里把查重与插入放在同一个事务里。
        """
        title_text = " ".join(str(title or "").split())[
            :MAX_REMINDER_TITLE_LENGTH
        ]
        if not title_text:
            return None, False
        try:
            due_text = _normalize_reminder_due(due_at)
            advance = _normalize_reminder_advance(advance_days)
        except ValueError:
            return None, False
        note_text = " ".join(str(note or "").split())[
            :MAX_REMINDER_NOTE_LENGTH
        ]
        profile_text = _normalize_profile_name(profile)
        session_text = str(session_id or "").strip()[:64]
        try:
            medication_key = max(0, int(medication_id or 0))
            vaccination_key = max(0, int(vaccination_id or 0))
            appointment_key = max(0, int(appointment_id or 0))
        except (TypeError, ValueError, OverflowError):
            return None, False

        conditions = ["done = 0", "profile = ?"]
        params: List[Any] = [profile_text]
        if match_title:
            conditions.append("title = ?")
            params.append(title_text)
        if match_due:
            conditions.append("due_at = ?")
            params.append(due_text)
        if medication_key:
            conditions.append("medication_id = ?")
            params.append(medication_key)
        if vaccination_key:
            conditions.append("vaccination_id = ?")
            params.append(vaccination_key)
        if appointment_key:
            conditions.append("appointment_id = ?")
            params.append(appointment_key)

        now = self._now()
        with self._lock, self._conn:
            row = self._conn.execute(
                f"""
                SELECT id, title, note, due_at, session_id, profile,
                       advance_days, repeat_days, repeat_until, repeats_from,
                       medication_id, vaccination_id, appointment_id, done,
                       created_at, updated_at
                  FROM reminders
                 WHERE {' AND '.join(conditions)}
                 ORDER BY id LIMIT 1
                """,
                params,
            ).fetchone()
            if row is not None:
                return {**dict(row), "done": False}, False
            cursor = self._conn.execute(
                """
                INSERT INTO reminders(
                    title, note, due_at, session_id, profile,
                    advance_days, medication_id, vaccination_id,
                    appointment_id, done, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    title_text, note_text, due_text, session_text,
                    profile_text, advance, medication_key,
                    vaccination_key, appointment_key, now, now,
                ),
            )
            new_id = int(cursor.lastrowid)
            row = self._conn.execute(
                """
                SELECT id, title, note, due_at, session_id, profile,
                       advance_days, repeat_days, repeat_until, repeats_from,
                       medication_id, vaccination_id, appointment_id, done,
                       created_at, updated_at
                  FROM reminders WHERE id = ?
                """,
                (new_id,),
            ).fetchone()
        return {**dict(row), "done": False}, True

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
        vaccination_id: int = 0,
        appointment_id: int = 0,
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
        _validate_repeat_window(due_at, repeat_days, repeat_until)
        medication_id = _safe_db_id(medication_id)
        vaccination_id = _safe_db_id(vaccination_id)
        appointment_id = _safe_db_id(appointment_id)
        now = self._now()
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """
                INSERT INTO reminders(
                    title, note, due_at, session_id, profile, advance_days,
                    repeat_days, repeat_until, medication_id, vaccination_id,
                    appointment_id, done, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    title, note, due_at, session_id, profile,
                    advance_days, repeat_days, repeat_until,
                    medication_id, vaccination_id, appointment_id, now, now,
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
            "vaccination_id": vaccination_id,
            "appointment_id": appointment_id,
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
                       vaccination_id, appointment_id, done, created_at,
                       updated_at
                  FROM reminders WHERE id = ?
                """,
                (int(reminder_id),),
            ).fetchone()
        return {**dict(row), "done": bool(row["done"])} if row else None

    def list_reminders(
        self,
        include_done: bool = False,
        profile: Optional[str] = None,
        search_q: str = "",
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
        search_clause, search_params = _like_any_condition(
            ("title", "note"), search_q
        )
        if search_clause:
            clauses.append(search_clause)
            params.extend(search_params)
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT id, title, note, due_at, session_id, profile, advance_days,
                       repeat_days, repeat_until, repeats_from, medication_id,
                       vaccination_id, appointment_id, done, created_at,
                       updated_at
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

    def finish_reminder(self, reminder_id: int) -> bool:
        """把提醒标记为已完成，但不触发重复顺延。

        供系统清理旧提醒使用：例如复查计划完成时关闭旧的关联提醒，
        这条提醒的 repeat_days 若被用户编辑过，普通完成会额外生成
        一条与计划脱节的待办。
        """
        try:
            reminder_id = int(reminder_id)
        except (TypeError, ValueError, OverflowError):
            return False
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "UPDATE reminders SET done = 1, updated_at = ?"
                " WHERE id = ?",
                (self._now(), reminder_id),
            )
            return cursor.rowcount > 0

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
                       repeats_from, medication_id, vaccination_id,
                       appointment_id, done, created_at, updated_at
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
                    except (ValueError, TypeError, OSError, OverflowError):
                        # 无法解析时退回按日期比较
                        if next_due[:10] > repeat_until[:10]:
                            in_range = False
                if in_range:
                    cursor = self._conn.execute(
                        """
                        INSERT INTO reminders(
                            title, note, due_at, session_id, profile,
                            advance_days, repeat_days, repeat_until,
                            repeats_from, medication_id, vaccination_id,
                            appointment_id, done,
                            created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)
                        """,
                        (
                            row["title"], row["note"], next_due,
                            row["session_id"], row["profile"],
                            row["advance_days"], repeat_days,
                            repeat_until, reminder_id,
                            row["medication_id"], row["vaccination_id"],
                            row["appointment_id"], now, now,
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
                        "vaccination_id": row["vaccination_id"],
                        "appointment_id": row["appointment_id"],
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
            # 指向已删除/已取消预约的历史提醒只能作为独立提醒恢复，
            # 否则会成为挂在不存在预约上的待办并与新提醒重复
            self._conn.execute(
                "UPDATE reminders SET appointment_id = 0"
                " WHERE id = ? AND appointment_id != 0"
                " AND appointment_id NOT IN ("
                "SELECT id FROM appointments WHERE status = 'upcoming')",
                (reminder_id,),
            )
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
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                "DELETE FROM reminders WHERE id = ?", (int(reminder_id),)
            )
            return cursor.rowcount > 0

    def delete_reminders_bulk(
        self, reminder_ids: Any, only_done: bool = False
    ) -> int:
        """批量删除提醒，只提交并 checkpoint 一次。

        only_done=True 供「清理已完成」使用：在 SQL 层只删除已完成
        行，其它标签页/设备期间恢复（reopen）的提醒不会被误删。
        """
        values = sorted({
            parsed for parsed in (
                _safe_db_id(value) for value in (reminder_ids or [])
            ) if parsed
        })
        if not values:
            return 0
        placeholders = ",".join("?" for _ in values)
        condition = " AND done = 1" if only_done else ""
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                f"DELETE FROM reminders"
                f" WHERE id IN ({placeholders}){condition}",
                values,
            )
            return int(cursor.rowcount or 0)

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
            current = self._conn.execute(
                "SELECT due_at, repeat_days, repeat_until "
                "FROM reminders WHERE id = ?",
                (int(reminder_id),),
            ).fetchone()
            if current is None:
                return None
            _validate_repeat_window(
                due_at if due_at is not None else current["due_at"],
                repeat_days if repeat_days is not None
                else int(current["repeat_days"] or 0),
                repeat_until if repeat_until is not None
                else current["repeat_until"],
            )
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
                       vaccination_id, appointment_id, done, created_at,
                       updated_at
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
                       vaccination_id, appointment_id, done, created_at,
                       updated_at
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

        with self._write_and_checkpoint():
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
            # 进行中的会诊不能在清理后把这些会话写回（多进程同样有效）
            self._remember_deleted_locked(session_ids)
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
