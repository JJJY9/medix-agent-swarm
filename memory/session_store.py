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

#: 各表参与孪生归属判断的「尾字段」：内容列相同、仅这些字段不同的
#: 同秒记录（如一条在用/一条停药）恢复时要按尾字段区分归属
IMPORT_TAIL_COLUMNS = {
    "symptoms": ("severity", "note"),
    "medications": (
        "note", "active", "stopped_at", "stop_reason", "stock",
        "stock_unit", "low_stock_threshold", "expiry_date",
    ),
    "vaccinations": ("next_due", "note"),
    "appointments": ("status", "note"),
    "allergies": ("reaction", "severity", "active", "note"),
    "care_items": ("last_done_at", "next_due_at", "active"),
    "visit_records": (
        "facility", "advice", "prescription", "next_followup_date",
    ),
    "conditions": ("icd10", "status", "severity"),
    "reminders": ("note", "advance_days", "repeat_days", "repeat_until", "done"),
}

#: 各表参与「软字段归属」判断的列：软字段精确等于另一条同秒备份
#: 记录时，候选属于那条记录的存活版本（如被改挂提醒的标题命中兄弟行）
IMPORT_SOFT_COLUMNS = {
    "medications": ("name",),
    "vaccinations": ("vaccine_name",),
    "appointments": ("title",),
    "lab_results": ("panel", "item"),
    "allergies": ("substance",),
    "care_items": ("title",),
    "visit_records": ("visit_date", "diagnosis"),
    "conditions": ("name",),
    "reminders": ("title",),
}

#: 各表中代表「记录身份」的内容列：这些列不同说明候选是另一条记录，
#: 仅时间/数值列不同则可能是同一记录被本地编辑
IMPORT_IDENTITY_NAME_COLUMNS = {
    "symptoms": ("symptom",),
    "medications": ("name",),
    "vaccinations": ("vaccine_name",),
    "appointments": ("title",),
    "lab_results": ("panel", "item"),
    "allergies": ("substance",),
    "care_items": ("title",),
    "visit_records": ("diagnosis",),
    "conditions": ("name",),
    "reminders": ("title",),
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
    try:
        profile = _normalize_profile_name(profile, strict=True)
    except ValueError:
        return None
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


#: 因档案被删除而解绑的会话标记：统计时跳过，避免归入「未指定」
DELETED_PROFILE_SESSION_TAG = "__deleted_profile__"


def _parse_tags(raw: Any) -> List[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return _normalize_tags(value)


def _normalize_profile_name(value: Any, strict: bool = False) -> str:
    text = " ".join(str(value or "").split())
    if strict and text and (
        text in (".", "..")
        or "/" in text or "\\" in text
        or any(not char.isprintable() for char in text)
    ):
        # 含路径分隔符/点段的档案名无法经 /api/profiles/{name} 删除，
        # 也永远无法改名：所有记录写入边界统一拒绝
        raise ValueError("档案名不能包含路径分隔符或控制字符")
    return text[:MAX_PROFILE_NAME_LENGTH]


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


def _import_valid_rows(raw_items: Any, limit: int, validator) -> list:
    """返回能通过导入校验的备份行（非法行不参与身份组大小统计）。"""
    valid = []
    if not isinstance(raw_items, list):
        return valid
    for item in raw_items[:limit]:
        if not isinstance(item, dict):
            continue
        try:
            normalized = validator(item)
        except Exception:
            normalized = None
        if normalized is not None:
            valid.append(item)
    return valid


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
    active: bool = True,
) -> Optional[int]:
    """按频次、用药开始与停止时间、统计天数估算应服次数。

    停药日只在药物已停用时生效：重新启用的用药若保留历史停药
    日期，不能被整段截断为 0 次（第七十三轮审查）。
    """
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
    stop_text = str(stopped_at or "").strip()[:19] if not active else ""
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
    stopped_at: str = "", active: bool = True,
) -> Optional[int]:
    """按频次、开始与停止时间估算近 7 天应服次数。"""
    return expected_doses_for_period(
        frequency, created_at, now, 7, stopped_at, active
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
    try:
        profile = _normalize_profile_name(profile, strict=True)
    except ValueError:
        return None
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
        "profile": _normalize_profile_name(profile, strict=True),
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
    try:
        profile = _normalize_profile_name(profile, strict=True)
    except ValueError:
        return None
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
        "profile": _normalize_profile_name(profile, strict=True),
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
                    resumed_at TEXT NOT NULL DEFAULT '',
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
                ("resumed_at", "TEXT NOT NULL DEFAULT ''"),
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
        try:
            profile = _normalize_profile_name(profile, strict=True)
        except ValueError:
            profile = ""
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
                self._rebind_session_profile_locked(session_id, profile)

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

    def _rebind_session_profile_locked(
        self, session_id: str, profile: str
    ) -> bool:
        """把空档案会话改绑到指定档案，并清除「已删除档案」标记。"""
        row = self._conn.execute(
            "SELECT tags FROM sessions WHERE id = ? AND profile = ''",
            (session_id,),
        ).fetchone()
        if row is None:
            return False
        tags = _parse_tags(row["tags"])
        if DELETED_PROFILE_SESSION_TAG in tags:
            tags = [
                tag for tag in tags
                if tag != DELETED_PROFILE_SESSION_TAG
            ]
        self._conn.execute(
            "UPDATE sessions SET profile = ?, tags = ?"
            " WHERE id = ? AND profile = ''",
            (profile, json.dumps(tags, ensure_ascii=False), session_id),
        )
        return True

    def set_session_profile(self, session_id: str, profile: str) -> bool:
        """把已有会话关联到档案（只在档案为空时生效）；不会创建会话。"""
        session_id = str(session_id or "").strip()
        try:
            profile = _normalize_profile_name(profile, strict=True)
        except ValueError:
            return False
        if not session_id or not profile:
            return False
        with self._write_and_checkpoint():
            return self._rebind_session_profile_locked(
                session_id, profile
            )

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

    def resolve_profile_alias(self, name: str, after: str = "") -> str:
        """把已改名档案的旧名称解析到当前名称。

        只跟随本库记录过的改名链；输入名仍被数据使用时原样返回（可能
        是另一位患者复用了这个名字）。中间名被新档案复用后，只跟随
        「新主人创建之前」产生的改名记录，避免把旧患者解析成新患者。
        after 非空时只跟随该时间之后的改名（恢复旧备份时用快照导出
        时间作界：快照内已体现的改名不能再改写快照里的同名档案）。
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
                alias_query = (
                    "SELECT new_name, renamed_at FROM profile_renames"
                    " WHERE old_name = ?"
                )
                alias_params: List[Any] = [current]
                if after:
                    # 同一秒内的「导出→改名→导入旧备份」也要能解析；
                    # 快照内已重建的同名档案由调用方按 created_at 拦下
                    alias_query += " AND renamed_at >= ?"
                    alias_params.append(after)
                alias_query += " ORDER BY renamed_at ASC, id ASC"
                rows = self._conn.execute(
                    alias_query, tuple(alias_params)
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
                renamed_at = str(chosen["renamed_at"] or "")
                following_deleted = self.profile_deleted_at(following)
                if (
                    following_deleted
                    and renamed_at
                    and following_deleted >= renamed_at
                ):
                    # 目标名在改名之后被删除：旧链已过期，不能解析到
                    # 之后同名重建的新患者
                    break
                # 中间名被新主人复用是既有支持场景（第四十二/三十四轮：
                # 下一轮会按 owner_created 只跟随更早的改名记录），不在此
                # 处中断；只有「目标名在改名后被删除再重建」才视为旧链过期
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
            days = max(0, min(int(days or 0), 3650))
        except (TypeError, ValueError, OverflowError):
            days = 0
        now = datetime.now()
        end_of_today = now.replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
        conditions = ["metric = ?", "measured_at <= ?"]
        params: List[Any] = [metric, end_of_today]
        if days > 0:
            cutoff = (
                now - timedelta(days=days - 1)
            ).strftime("%Y-%m-%d")
            conditions.insert(1, "measured_at >= ?")
            params.insert(1, cutoff)
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
        now = datetime.now()
        cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        today = now.strftime("%Y-%m-%d")
        checks = (
            ("reminders", "profile = '' AND done = 0", ()),
            (
                # 未来时间的记录不会在健康档案中展示：不能凭空生成
                # 空的「未指定」卡片
                "health_metrics",
                "profile = '' AND substr(measured_at, 1, 10) <= ?",
                (today,),
            ),
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
                "profile = '' AND substr(recorded_at, 1, 10) >= ?"
                " AND substr(recorded_at, 1, 10) <= ?",
                (cutoff, today),
            ),
            (
                "lab_results",
                "profile = '' AND substr(tested_at, 1, 10) >= ?"
                " AND substr(tested_at, 1, 10) <= ?",
                (cutoff, today),
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
                " AND s.tags NOT LIKE '%__deleted_profile__%'"
                " AND substr(COALESCE(s.updated_at, s.created_at), 1, 10)"
                " >= ?"
                " AND substr(COALESCE(s.updated_at, s.created_at), 1, 10)"
                " <= ?"
                " AND EXISTS (SELECT 1 FROM messages m"
                " WHERE m.session_id = s.id) LIMIT 1",
                (cutoff, today),
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
        try:
            profile_value = _normalize_profile_name(
                profile, strict=True
            )
        except ValueError:
            profile_value = ""
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
            if profile_value:
                # 会话从「未指定」改绑到新档案时清除删除档案标记，
                # 否则新会诊会被所有「未指定」统计过滤掉
                bound = self._conn.execute(
                    "SELECT tags FROM sessions WHERE id = ?"
                    " AND tags LIKE '%__deleted_profile__%'",
                    (session_id,),
                ).fetchone()
                if bound is not None:
                    raw_tags = str(bound["tags"] or "")
                    try:
                        parsed_tags = json.loads(raw_tags)
                    except (TypeError, json.JSONDecodeError):
                        parsed_tags = []
                    if isinstance(parsed_tags, list):
                        parsed_tags = [
                            tag for tag in parsed_tags
                            if str(tag) != DELETED_PROFILE_SESSION_TAG
                        ]
                        self._conn.execute(
                            "UPDATE sessions SET tags = ? WHERE id = ?",
                            (
                                json.dumps(
                                    _normalize_tags(parsed_tags),
                                    ensure_ascii=False,
                                ),
                                session_id,
                            ),
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
                     AND content NOT LIKE '%风险等级：不确定%'
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
            raw_tags_value = str(item.get("tags") or "")
            item["tags"] = _parse_tags(raw_tags_value)
            if (
                DELETED_PROFILE_SESSION_TAG in raw_tags_value
                and DELETED_PROFILE_SESSION_TAG not in item["tags"]
            ):
                item["tags"].append(DELETED_PROFILE_SESSION_TAG)
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
                   ) AS last_answer,
                   (
                       SELECT substr(m.content, 1, 2000)
                         FROM messages m
                        WHERE m.session_id = s.id AND m.role = 'assistant'
                          AND (
                              content LIKE '%风险等级%'
                              OR content LIKE '%高危%'
                              OR content LIKE '%中危%'
                              OR content LIKE '%低危%'
                              OR content LIKE '%紧急%'
                          )
                        AND content NOT LIKE '%风险等级：不确定%'
                        ORDER BY (CASE WHEN content LIKE '%风险等级%'
                                       THEN 0 ELSE 1 END), m.id DESC
                        LIMIT 1
                   ) AS last_risk_answer"""
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT s.id, s.title, s.created_at, s.updated_at, s.profile,
                       s.tags,
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
        items = []
        for row in rows:
            item = dict(row)
            raw_tags_value = str(item.get("tags") or "")
            item["tags"] = _parse_tags(raw_tags_value)
            if (
                DELETED_PROFILE_SESSION_TAG in raw_tags_value
                and DELETED_PROFILE_SESSION_TAG not in item["tags"]
            ):
                item["tags"].append(DELETED_PROFILE_SESSION_TAG)
            last_answer = item.get("last_answer", "") or ""
            risk_answer = (
                item.pop("last_risk_answer", "") or last_answer
            )
            item["risk_text"] = str(risk_answer)[:2000]
            items.append(item)
        return items

    def latest_session_answers_by_profile(
        self, include_empty: bool = False
    ) -> List[Dict[str, Any]]:
        """返回每个档案最近一次会诊及其最后一条答复（截断 2000 字）。

        include_empty=True 时也返回未指定档案的最近会话，供周报按
        “未指定”筛选时计算风险等级。
        """
        tag_filter = "tags NOT LIKE '%__deleted_profile__%'"
        profile_filter = (
            f"WHERE {tag_filter}"
            if include_empty
            else f"WHERE profile != '' AND {tag_filter}"
        )
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
        limit = max(1, min(int(per_profile or 200), 500))
        sql = f"""
            WITH ranked AS (
                SELECT s.profile AS profile,
                       m.session_id AS session_id,
                       substr(m.content, 1, 2000) AS content,
                       m.created_at AS answered_at,
                       m.id AS message_id,
                       ROW_NUMBER() OVER (
                           PARTITION BY s.profile
                           ORDER BY
                               CASE WHEN m.content LIKE '%风险等级%'
                                    THEN 0 ELSE 1 END,
                               m.created_at DESC, m.id DESC
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
                   AND s.tags NOT LIKE '%__deleted_profile__%'
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

    def session_ids_with_messages(
        self,
        start: str,
        end: str,
        profile: Optional[str] = None,
    ) -> set:
        """返回在 [start, end) 内有消息的会话 ID（按消息日期归属）。

        会话的 updated_at 只保留最近一次活动：跨周续聊后，上一周期
        的真实会诊活动会从 created/updated 判断中消失，需要按消息
        自身的 created_at 统计周期活动。
        """
        conditions = [
            "m.created_at >= ?",
            "m.created_at < ?",
            "s.tags NOT LIKE '%__deleted_profile__%'",
        ]
        params: List[Any] = [str(start), str(end)]
        if profile is not None:
            conditions.append("s.profile = ?")
            params.append(_normalize_profile_name(profile))
        sql = (
            "SELECT DISTINCT m.session_id FROM messages m"
            " JOIN sessions s ON s.id = m.session_id"
            f" WHERE {' AND '.join(conditions)}"
        )
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return {str(row["session_id"]) for row in rows}

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
            # 完成复查计划同样是一次健康记录：不纳入会让家庭卡在
            # 完成当天显示「今日未记录」、连续天数不前进，与周报的
            # 复查完成口径矛盾（第七十三轮审查）
            ("care_items", "last_done_at"),
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
            # 删除档案时解绑的会话不能算作「未指定」成员的记录
            "tags NOT LIKE '%__deleted_profile__%'",
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
        # 同一会话跨天续聊：早期消息日期同样算「有记录的一天」，
        # 不能只取被刷新到今天的 updated_at
        message_conditions = [
            "m.created_at >= ?",
            "s.tags NOT LIKE '%__deleted_profile__%'",
        ]
        message_params: List[Any] = [cutoff]
        if profile is not None:
            message_conditions.append("s.profile = ?")
            message_params.append(_normalize_profile_name(profile))
        parts.append(
            "SELECT substr(m.created_at, 1, 10) AS day"
            " FROM sessions s JOIN messages m ON m.session_id = s.id"
            f" WHERE {' AND '.join(message_conditions)}"
        )
        params.extend(message_params)
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
            ("care_items", "last_done_at"),
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
            " AND tags NOT LIKE '%__deleted_profile__%'"
            " AND EXISTS (SELECT 1 FROM messages m"
            " WHERE m.session_id = sessions.id)"
        )
        params.append(cutoff)
        # 同一会话跨天续聊：按每条消息的日期展开，保留早期日期
        parts.append(
            "SELECT s.profile, substr(m.created_at, 1, 10) AS day"
            " FROM sessions s JOIN messages m ON m.session_id = s.id"
            " WHERE s.profile != '' AND m.created_at >= ?"
            " AND s.tags NOT LIKE '%__deleted_profile__%'"
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
        try:
            profile_text = _normalize_profile_name(profile, strict=True)
        except ValueError:
            # 失败/中止路径同样拒绝不可打印/路径仿冒的档案名：
            # usage_events 没有删除接口，注入后长期残留
            profile_text = ""
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
                    profile_text,
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
            raw_tags = str(item.get("tags") or "")
            item["tags"] = _parse_tags(raw_tags)
            if (
                DELETED_PROFILE_SESSION_TAG in raw_tags
                and DELETED_PROFILE_SESSION_TAG not in item["tags"]
            ):
                # 内部标记不占用户标签配额：MAX_TAGS 截断不能丢
                item["tags"].append(DELETED_PROFILE_SESSION_TAG)
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
            # 改名历史含旧姓名明文，且自第七十九轮起导入端已不消费：
            # 不再写入导出/备份，避免改名后旧姓名仍随文件外泄
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
        backup_exported_at: str = "",
        tail_where: Optional[Tuple[str, Tuple[Any, ...]]] = None,
        backup_ids: Optional[set] = None,
        backup_profiles: Optional[Dict[int, str]] = None,
        backup_created: Optional[Dict[int, str]] = None,
        backup_contents: Optional[Dict[int, tuple]] = None,
        backup_tails: Optional[Dict[int, tuple]] = None,
        backup_softs: Optional[Dict[int, tuple]] = None,
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
        #: 提醒等身份列不含档案：分组计数必须与组大小口径一致，
        #: 否则其它成员同秒创建的行会把本档案孪生组提前判为末行
        scope_profile = ""
        if match_where is not None and str(
            match_where[0]
        ).strip().lower().startswith("profile"):
            scope_profile = _normalize_profile_name(match_where[1][0])
        #: 当前备份行的档案：软字段/孪生辅助函数在漂移分支之前也可能
        #: 被调用，必须提前绑定
        row_profile = (
            str(values[0]) if columns and columns[0] == "profile" else ""
        )
        if not row_profile and scope_profile:
            row_profile = str(match_where[1][0])
        if (
            raw_id <= 0
            and backup_tails
            and backup_profiles
            and created_at_value
        ):
            # 无 id 备份行：用档案+创建时间+内容+尾字段在合成元数据
            # 中定位本行，使尾字段归属比较对无 id 备份同样生效
            # （第九十六轮 [2][3]）
            wanted_profile = _normalize_profile_name(row_profile)
            created_key = str(created_at_value or "").strip()[:40]
            expected_tail = (
                tuple(
                    self._tail_value_key(value)
                    for value in tail_where[1]
                )
                if tail_where is not None else None
            )
            #: 本行内容按内容指纹口径归一化：同秒同尾孪生（内容不同、
            #: 尾字段相同）不能都锚到同一个合成行（第九十九轮 [8]）
            wanted_content = None
            if backup_contents and content_values:
                wanted_content = tuple(
                    _canonical_local_time(
                        " ".join(str(value).split())
                    )
                    or " ".join(str(value).split())
                    for value in content_values
                )
            synth_match = None
            synth_fallback = None
            for synth_id in backup_tails:
                if str(backup_created.get(synth_id) or "") != created_key:
                    continue
                if _normalize_profile_name(
                    backup_profiles.get(synth_id) or ""
                ) != wanted_profile:
                    continue
                if expected_tail is not None:
                    # 备份缺省字段编码（''）与 DB 默认值（'0'）口径
                    # 不同：只在两侧都有值时逐字段比较
                    synth_tail = backup_tails.get(synth_id) or ()
                    mismatch = any(
                        want and have and want != have
                        for want, have in zip(
                            expected_tail, synth_tail
                        )
                    )
                    if mismatch:
                        continue
                if synth_fallback is None:
                    synth_fallback = synth_id
                if (
                    wanted_content is not None
                    and backup_contents.get(synth_id) == wanted_content
                ):
                    synth_match = synth_id
                    break
            if synth_match is not None:
                raw_id = synth_match
            elif synth_fallback is not None:
                raw_id = synth_fallback
        moved_key = (table, "__moved__")
        moved = state.setdefault(moved_key, set())
        matched = state.setdefault((table, "__matched__"), set())
        #: 本次导入每个本地候选被哪条备份原始主键认领：兄弟行自己
        #: 认领的恢复副本仍是那条备份记录的存活证据，不能被当成
        #: 「本轮已消化的行」跳过（第一百零三轮 [13]）
        claim_owner = state.setdefault((table, "__claim_owner__"), {})
        #: 本次导入已处理过的备份原始主键：同名同秒的另一条备份记录
        #: 若已处理完，其本地存活行不再被它占用
        seen_raw_ids = state.setdefault((table, "__seen_raw__"), set())
        if raw_id > 0:
            seen_raw_ids.add(raw_id)
        where = " AND ".join(f"{column} = ?" for column in columns)

        def _claim(candidate: int, mark_moved: bool = False) -> int:
            """认领一行：从所有身份组与漂移候选中排除，避免二次认领。"""
            if raw_id > 0:
                claim_owner[candidate] = raw_id
            if mark_moved:
                moved.add(candidate)
            matched.add(candidate)
            claimed.discard(candidate)
            for cached in list(state.values()):
                if (
                    isinstance(cached, set)
                    and cached is not moved
                    and cached is not matched
                    # __seen_raw__ 保存的是备份原始主键而非本地候选：
                    # 抹掉会让已处理的兄弟备份行被当成未处理
                    and cached is not seen_raw_ids
                ):
                    cached.discard(candidate)
            state[identity] = claimed
            return candidate

        def _cross_profile_twin_candidate() -> Optional[int]:
            """本行在原档案已无存活行时，可互换认领的跨档案同秒孪生。

            记录被 PATCH 改挂到另一档案后，本档案只剩另一条同秒
            孪生属主的存活行；若另一档案里恰有一条未编辑、内容与
            尾字段都与本行一致、且该档案自己的备份行内容相同的
            孪生行，两条孪生不可区分，可互相认领，避免在已改挂的
            记录旁复活一份旧档案副本（第八十九轮 [8]）。
            """
            if not (
                soft_where is not None
                and backup_profiles
                and backup_contents
                and created_at_value
            ):
                return None
            wanted_content = backup_contents.get(raw_id)
            if wanted_content is None:
                return None
            try:
                twin_rows = self._conn.execute(
                    f"SELECT id, profile FROM {table}"
                    " WHERE created_at = ?",
                    (str(created_at_value),),
                ).fetchall()
            except sqlite3.Error:
                return None
            own_profile = _normalize_profile_name(row_profile)
            twin_pool = []
            for row in twin_rows:
                candidate = int(row["id"])
                if candidate in moved or candidate in matched:
                    continue
                if (
                    pre_existing_ids is not None
                    and candidate not in pre_existing_ids
                ):
                    continue
                twin_profile = _normalize_profile_name(
                    str(row["profile"] or "")
                )
                if not twin_profile or twin_profile == own_profile:
                    continue
                if not (
                    _soft_matches(candidate)
                    and _tail_matches(candidate)
                    and _candidate_content_matches_backup(candidate)
                    and not _candidate_edited(candidate)
                ):
                    continue
                if not any(
                    other_id != raw_id
                    #: 属主备份行必须已经处理过：否则这条未编辑的
                    #: 孪生行还是它的存活版本，本行抢来会让属主
                    #: 记录永远无法恢复（第九十轮 [4]）
                    and other_id in seen_raw_ids
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) == twin_profile
                    and backup_contents.get(other_id) == wanted_content
                    and (
                        not backup_created
                        or backup_created.get(other_id)
                        == str(created_at_value)
                    )
                    for other_id in backup_profiles
                ):
                    continue
                if any(
                    other_id != raw_id
                    and other_id not in seen_raw_ids
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) == twin_profile
                    and backup_contents.get(other_id) == wanted_content
                    and (
                        not backup_created
                        or backup_created.get(other_id)
                        == str(created_at_value)
                    )
                    and backup_tails
                    and _tail_match_score(candidate, other_id)
                    == len(backup_tails.get(other_id) or [])
                    for other_id in backup_profiles
                ):
                    # 候选同时是同秒尚未处理的同档案兄弟备份行的
                    # 存活版本（内容+尾字段全等）：它属于那条记录，
                    # 本行不能互换认领（第九十五轮 [3]）
                    continue
                twin_pool.append(candidate)
            if len(twin_pool) == 1:
                return twin_pool[0]
            return None

        claimed = state.get(identity)
        if claimed is None:
            rows = self._conn.execute(
                f"SELECT id FROM {table} WHERE {where} ORDER BY id",
                values,
            ).fetchall()
            claimed = {int(row["id"]) for row in rows}
        group_seen_key = (table, "__seen__", identity, scope_profile)
        group_seen = int(state.get(group_seen_key, 0)) + 1
        state[group_seen_key] = group_seen
        # 本条是否为该身份组的最后一行备份记录：最后一行才允许认领
        # 同秒孪生组里剩下的唯一存活行
        is_group_last = group_seen >= max(backup_group_size, 1)
        local_size_key = (table, "__local_size__", identity, scope_profile)
        local_group_size = state.get(local_size_key)
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
            select_columns = "id, created_at, updated_at"
            if content_columns:
                select_columns += ", " + ", ".join(content_columns)
            try:
                rows = self._conn.execute(
                    f"SELECT {select_columns} FROM {table} WHERE id <= ?",
                    (max(backup_ids),),
                ).fetchall()
            except sqlite3.Error:
                rows = []
            if not rows:
                # 既有行全部大于备份主键上限（漂移/合并库）：区间为空
                # 不能当作「所有行都对得上」，只有正面证据才算同源
                result = False
            for row in rows:
                if not result:
                    break
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
                if content_columns and backup_contents:
                    expected_contents = backup_contents.get(local_id)
                    #: 未编辑（updated_at == created_at）的同主键行
                    #: 内容却与备份不符：它是同秒创建、恰好占用同一
                    #: 低位 id 的无关记录，主键空间不能视为同源，
                    #: 否则会被无条件认领并复制/丢失孪生（第八十九轮 [2]）。
                    #: updated_at 只有秒级精度：导出后同一秒内的编辑
                    #: 也会呈现 updated == created，此时按「不早于导出
                    #: 时间即视为已编辑」放行（与 _edited_after_export
                    #: 同口径），避免把刚 snooze/改过日期的行误判为无关行
                    if (
                        expected_contents is not None
                        and str(row["updated_at"] or "")
                        == str(row["created_at"] or "")
                        and not (
                            backup_exported_at
                            and str(row["updated_at"] or "")
                            >= str(backup_exported_at)
                        )
                        and self._db_content_fingerprint(
                            row, content_columns
                        ) != expected_contents
                    ):
                        result = False
                        break
            state[cache_key] = result
            return result

        if local_group_size is None:
            #: 组大小按「当前备份行档案」过滤：match_where 只有提醒等
            #: 表才有，其余表以行内档案为参照；否则同源改挂进来的
            #: 其它成员存活行会把本档案的同秒组提前判为已满（第七十八轮）
            reference_profile = scope_profile or (
                _normalize_profile_name(row_profile) if row_profile else ""
            )
            if reference_profile and claimed:
                placeholders = ",".join("?" for _ in claimed)
                try:
                    size_select = (
                        "id, profile, created_at, updated_at"
                    )
                    if content_columns:
                        size_select += ", " + ", ".join(content_columns)
                    size_rows = self._conn.execute(
                        f"SELECT {size_select}"
                        f" FROM {table}"
                        f" WHERE id IN ({placeholders})",
                        tuple(claimed),
                    ).fetchall()
                except sqlite3.Error:
                    size_rows = []
                local_group_size = 0
                for row in size_rows:
                    if _normalize_profile_name(
                        row["profile"] or ""
                    ) != reference_profile:
                        continue
                    row_id = int(row["id"])
                    if (
                        backup_profiles
                        and backup_created
                        and row_id in backup_profiles
                        and str(row["created_at"] or "")[:40]
                        == str(backup_created.get(row_id) or "")
                        and _normalize_profile_name(
                            backup_profiles.get(row_id) or ""
                        ) != reference_profile
                        #: 同源库（本地主键不可能指向别的记录）直接按
                        #: 主键+创建时间认定改挂行；只有主键空间可能
                        #: 漂移的库才要求「有编辑证据且内容吻合」，避免
                        #: 把本档案同秒孪生低估为缺行，也避免把改挂并
                        #: 编辑过内容的存活行误算作本档案行
                        #: （第九十一/九十二轮 [2]）
                        and (
                            _same_backup_origin()
                            or (
                                str(row["updated_at"] or "")
                                != str(row["created_at"] or "")
                                and (
                                    not backup_contents
                                    or not content_columns
                                    or self._db_content_fingerprint(
                                        row, content_columns
                                    )
                                    == backup_contents.get(row_id)
                                )
                            )
                        )
                    ):
                        # 同源备份中从其它档案改挂进来的行：它对应的
                        # 备份记录属于原档案，不能把当前档案的缺行抵消
                        continue
                    if (
                        backup_contents
                        and backup_profiles
                        and content_columns
                        and str(row["updated_at"] or "")
                        != str(row["created_at"] or "")
                    ):
                        try:
                            fp_row = self._conn.execute(
                                f"SELECT {', '.join(content_columns)}"
                                f" FROM {table} WHERE id = ?",
                                (row_id,),
                            ).fetchone()
                        except sqlite3.Error:
                            fp_row = None
                        if fp_row is not None:
                            fingerprint = self._db_content_fingerprint(
                                fp_row, content_columns
                            )
                            created_key = str(
                                row["created_at"] or ""
                            )[:40]
                            moved_copy = False
                            twin_of_reference = False
                            for other_id, other_contents in (
                                backup_contents.items()
                            ):
                                if other_contents != fingerprint:
                                    continue
                                if (
                                    backup_created
                                    and backup_created.get(other_id)
                                    != created_key
                                ):
                                    continue
                                other_profile = _normalize_profile_name(
                                    backup_profiles.get(other_id) or ""
                                )
                                if not other_profile:
                                    continue
                                if other_profile == reference_profile:
                                    # 内容也精确等于本档案的备份行：
                                    # 可能是本档案同内容的另一条记录，
                                    # 不能据此判定为改挂行
                                    twin_of_reference = True
                                    break
                                moved_copy = True
                            if moved_copy and not twin_of_reference:
                                continue
                    if (
                        backup_contents
                        and content_columns
                        and str(row["updated_at"] or "")
                        == str(row["created_at"] or "")
                    ):
                        # 未编辑且内容不匹配备份组任何一行的同秒无关行
                        # （多次恢复/合并遗留）不能抵消本组缺行：否则
                        # 被删记录会因组「已满」而永不补回
                        # （第一百零二轮 [4]）
                        try:
                            fp_row = self._conn.execute(
                                f"SELECT {', '.join(content_columns)}"
                                f" FROM {table} WHERE id = ?",
                                (row_id,),
                            ).fetchone()
                        except sqlite3.Error:
                            fp_row = None
                        if fp_row is not None:
                            fingerprint = self._db_content_fingerprint(
                                fp_row, content_columns
                            )
                            created_key = str(
                                row["created_at"] or ""
                            )[:40]
                            matches_backup = any(
                                other_contents == fingerprint
                                and (
                                    not backup_created
                                    or backup_created.get(other_id)
                                    == created_key
                                )
                                for other_id, other_contents in
                                backup_contents.items()
                            )
                            if not matches_backup:
                                continue
                    local_group_size += 1
            else:
                local_group_size = len(claimed)
            state[local_size_key] = local_group_size

        def _ambiguous() -> Optional[int]:
            """无法区分时按被删记录补回，绝不复制存活行旧版本。"""
            state[identity] = claimed
            inserted_key = (
                table, "__ambiguous__", identity, scope_profile
            )
            state[inserted_key] = (
                int(state.get(inserted_key, 0)) + 1
            )
            # 同一身份组的补插预算：锚点/软字段分支共享该计数，
            # 每个组最多补插缺失条数，补足后剩余备份行认领存活行
            budget_key = (
                table, "__anchor_inserted__", identity, scope_profile
            )
            state[budget_key] = int(state.get(budget_key, 0)) + 1
            if raw_id > 0 and backup_contents:
                expected_content = backup_contents.get(raw_id)
                if expected_content is not None:
                    # 记录本次补插的原始行内容：后续兄弟行判断「已解析」
                    # 时只能认可确实属于该兄弟的补插，不能把本行补插的
                    # 旧副本当成兄弟已恢复
                    ambiguous_key = (table, "__ambiguous_contents__")
                    ambiguous_map = state.get(ambiguous_key)
                    if not isinstance(ambiguous_map, dict):
                        ambiguous_map = {}
                        state[ambiguous_key] = ambiguous_map
                    ambiguous_map[raw_id] = expected_content
            return None

        def _group_rows_missing() -> bool:
            """组内是否仍有未补插的缺行：本次导入已补插的行抵消缺行。"""
            inserted_total = int(state.get(
                (table, "__anchor_inserted__", identity, scope_profile), 0
            ))
            return (
                local_group_size + inserted_total < backup_group_size
            )

        def _candidate_content_matches_profile_sibling(
            candidate: int, candidate_profile: str
        ) -> bool:
            """候选内容是否同时精确等于同秒同档案另一条备份记录。

            内容与兄弟备份行相同（如两条同日复诊、同到期提醒）时，
            即便候选被当前行认领，兄弟行的内容仍有存活行；反之
            （改挂行被改名成兄弟行的标题、内容原本不同）认领会
            让兄弟行的原内容永久缺失，必须按被删记录补插。
            """
            if (
                not backup_contents
                or not content_columns
                or not candidate_profile
            ):
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
            created_key = str(created_at_value or "")
            wanted = _normalize_profile_name(candidate_profile)
            for other_id, contents in backup_contents.items():
                if other_id == raw_id:
                    continue
                if contents != fingerprint:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if backup_profiles and _normalize_profile_name(
                    backup_profiles.get(other_id) or ""
                ) != wanted:
                    continue
                return True
            return False

        def _candidate_foreign_backup_row(candidate: int) -> bool:
            """候选是否其实是另一成员同秒备份行的改挂存活版本。

            同源库里本地 id 与备份主键一一对应：候选主键命中另一档案
            的备份行、且与当前行主键不同时，它属于那条记录（被改挂），
            不能被当前成员用「本地编辑过」认领，否则另一成员被删的
            记录丢失、改挂前的旧副本会在原成员名下复活。
            """
            if (
                not backup_ids
                or not backup_profiles
                or raw_id <= 0
                or candidate == raw_id
                or candidate not in backup_ids
            ):
                return False
            other_profile = backup_profiles.get(candidate)
            if other_profile is None:
                return False
            if _other_backup_row_survives(candidate, count_own_claims=True):
                # 主键对应的备份记录已在本地另有存活行：候选是当前
                # 行的改挂/漂移版本，可以认领；否则互换档案的两条
                # 同秒读数会被反复补插（第八十五轮）。认领者口径与
                # 主键撞号检查一致：被该备份记录本轮自己认领的恢复
                # 副本仍是它的存活行（第一百零四轮 [8]）
                return False
            current_profile = str(row_profile or "")
            if not current_profile and columns and columns[0] == "profile":
                current_profile = str(values[0])
            if not current_profile:
                return False
            if not _same_backup_origin():
                return False
            return (
                _normalize_profile_name(other_profile)
                != _normalize_profile_name(current_profile)
            )

        def _candidate_owns_unprocessed_backup_row(candidate: int) -> bool:
            """候选是否其实是另一条尚未处理的同源备份行的存活版本。

            同源库中本地 id 与备份主键一一对应：候选主键命中另一条
            尚未处理的备份行时，它可能是那条记录（改挂/改名后）的
            存活版本，不能先被当前行认领，否则那条记录只能补插旧副本。
            """
            if (
                not backup_ids
                or raw_id <= 0
                or candidate == raw_id
                or candidate not in backup_ids
                or candidate in seen_raw_ids
                or not _same_backup_origin()
            ):
                return False
            return True

        def _content_owned_by_other(candidate: int) -> bool:
            """候选内容是否精确等于备份里另一条记录（而非当前行）。

            软字段/锚点都可被编辑；当内容指纹命中同批次备份中的
            另一条记录时，该候选是那条记录的存活行，不能认领给本行，
            否则被删记录无法补回、另一条记录会被复制。
            """
            if not backup_contents or raw_id == 0:
                # 合成负键（无 id 备份行）同样有真实备份身份：
                # 不能按 raw_id<0 一刀切关掉内容归属判断
                # （第九十九轮 [8]）
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
            current_created = str(created_at_value or "")
            # 只把「尚无本地存活行」的另一条备份记录视为占用了该
            # 内容：若那条记录已另有存活行，候选更可能是本行被编辑
            # 后的版本（例如不同成员同一时间各录一条同名同剂量用药，
            # 其中一条被改剂量后恢复旧备份）
            return any(
                other_id != raw_id
                and contents == fingerprint
                and not _other_backup_row_survives(other_id)
                and (
                    not backup_created
                    or backup_created.get(other_id) == current_created
                )
                for other_id, contents in backup_contents.items()
            )

        def _local_holes_are_backup_ids() -> bool:
            """本地缺失的主键是否正好落在备份主键集合里。

            新库导入旧备份后自增 id 会重新编号：若备份主键范围内丢失
            的本地 id 不在备份集合中，说明 id 空间已经错位，本地
            id==备份主键的行可能其实是备份里另一条记录，不能据此
            自认领（否则被删记录丢失、旧副本复活）。
            """
            if not backup_ids:
                return True
            try:
                rows = self._conn.execute(
                    f"SELECT id FROM {table} WHERE id <= ?",
                    (max(backup_ids),),
                ).fetchall()
            except sqlite3.Error:
                return False
            local_ids = {int(row["id"]) for row in rows}
            # 「本地缺失的 id 是否都在备份主键集合里」用计数判断：
            # 备份 id 由文件提供，物化 set(range(1, max+1)) 会被
            # 伪造超大 id 放大成内存耗尽（第八十一轮）
            missing_count = int(max(backup_ids)) - len(local_ids)
            if missing_count <= 0:
                return True
            fillable = len(set(backup_ids) - local_ids)
            return missing_count <= fillable

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

        def _tail_matches(candidate: int) -> bool:
            """候选的非内容字段是否与本行一致（用于区分同内容孪生）。"""
            if tail_where is None:
                return False
            fragment, extra = tail_where
            try:
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE id = ? AND {fragment}",
                    (candidate, *extra),
                ).fetchone()
            except sqlite3.Error:
                return False
            return row is not None

        def _tail_match_score(
            candidate: int, target_raw_id: int
        ) -> int:
            """候选尾字段与某条备份记录的逐字段匹配数（-1 无法比较）。"""
            tail_columns = IMPORT_TAIL_COLUMNS.get(table)
            if not tail_columns or not backup_tails:
                return -1
            expected = backup_tails.get(target_raw_id)
            if expected is None:
                return -1
            try:
                row = self._conn.execute(
                    f"SELECT {', '.join(tail_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return -1
            if row is None:
                return -1
            local = self._db_content_fingerprint(row, tail_columns)
            return sum(
                1 for mine, want in zip(local, expected) if mine == want
            )

        def _tail_prefers_sibling(
            candidate: int, cross_profile: bool = False
        ) -> bool:
            """候选尾字段更像另一条同内容孪生备份行。

            「被删的是靠前那条、存活并编辑的是靠后那条」时，先处理行
            不能凭「存活行被编辑过」把属于兄弟行的候选认领走，否则被
            删记录的启停/停药等尾字段永久丢失、存活行被复制。
            cross_profile=True 时把改挂到其他成员的同秒孪生行也纳入
            比较：尾字段精确命中另一条备份记录的候选属于那条记录。
            """
            if not backup_tails or not backup_contents or tail_where is None:
                return False
            expected_content = backup_contents.get(raw_id)
            if expected_content is None:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            best_other = own_score
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    not cross_profile
                    and wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if backup_contents.get(other_id) != expected_content:
                    continue
                other_score = _tail_match_score(candidate, other_id)
                if other_score > best_other:
                    best_other = other_score
            return best_other > own_score

        def _tail_prefers_sibling_any_content(
            candidate: int, cross_profile: bool = False
        ) -> bool:
            """候选尾字段是否严格更像同秒任一兄弟备份行（不论内容）。

            存活行的内容列被本地编辑成与被删孪生相同后，尾字段
            （在用/停药、severity/note 等）仍会精确匹配它原本那条
            记录：此时把它按内容认领给被删行，会让被删记录的非内容
            字段永久丢失、并把存活行旧版本复制一份。
            """
            if not backup_tails or tail_where is None:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            best_other = own_score
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    not cross_profile
                    and wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                other_score = _tail_match_score(candidate, other_id)
                if other_score > best_other:
                    best_other = other_score
            return best_other > own_score

        def _tail_tied_with_sibling(
            candidate: int, cross_profile: bool = False
        ) -> bool:
            """候选尾字段与同内容孪生兄弟的匹配得分持平。

            得分与本行持平说明备注等尾字段被编辑后已无法判断候选属于
            哪条孪生：本轮若认领，被删记录会永久丢失（反之补插只会
            多一条可清理的旧副本）。严格高于兄弟行时（如「已停药」改
            成「已停药改备注」）仍可确认是本行存活版本。cross_profile
            把改挂到其他成员的同秒孪生纳入比较（发现5的跨档案场景）。
            """
            if not backup_tails or not backup_contents or tail_where is None:
                return False
            expected_content = backup_contents.get(raw_id)
            if expected_content is None:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            best_other = -1
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    not cross_profile
                    and wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if backup_contents.get(other_id) != expected_content:
                    continue
                other_score = _tail_match_score(candidate, other_id)
                if other_score > best_other:
                    best_other = other_score
            return best_other >= 0 and best_other == own_score

        def _tail_uniquely_matches(candidate: int) -> bool:
            """候选尾字段精确命中本行，且严格优于所有同秒兄弟行。

            内容列被编辑后（如剂量 50mg→75mg），尾字段（备注等）
            仍是归属证据：只有全部尾字段命中本行、且兄弟备份行的
            匹配数都更低时，才能确认候选是本行的存活版本；与兄弟
            打平则按被删记录补插（第九十九轮 [4]）。
            """
            if tail_where is None or not backup_tails:
                return False
            if not _tail_matches(candidate):
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if _tail_match_score(candidate, other_id) >= own_score:
                    return False
            return True

        def _moved_tail_beats_sibling(
            candidate: int, candidate_profile: str
        ) -> bool:
            """候选尾字段对当前行的匹配数是否严格高于同秒同档案兄弟。

            跨档案改挂行的尾字段（备注/完成状态等）常与本行相同；
            若候选新档案里还有一条同秒未处理的备份行，打平时候选
            也可能是那条被删记录的存活版本：宁可补插本行，不能吞掉
            被删成员记录（第一百轮 [4]）。
            """
            if tail_where is None or not backup_tails:
                return True
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return True
            created_key = str(created_at_value or "")
            wanted = _normalize_profile_name(candidate_profile)
            best_other = -1
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if backup_profiles and _normalize_profile_name(
                    backup_profiles.get(other_id) or ""
                ) != wanted:
                    continue
                if other_id in seen_raw_ids:
                    # 已处理的兄弟行不能构成归属歧义
                    continue
                other_score = _tail_match_score(candidate, other_id)
                if other_score > best_other:
                    best_other = other_score
            return best_other < 0 or own_score > best_other

        def _backup_row_present_for(
            other_id: int, candidate: int,
            require_pre_existing: bool = True,
        ) -> bool:
            """备份行是否已有自己的本地存货行（不含给定候选）。

            与 _other_backup_row_survives 的区别：本轮已被其它备份
            行认领的行同样算数（它仍是那条备份记录的存活行）；
            require_pre_existing=True 时只用「本次导入前已存在」排除
            补插出来的旧副本（第一百零一轮 [4]）。
            """
            expected = (backup_contents or {}).get(other_id)
            if expected is None or not content_columns:
                return False
            created_key = str(created_at_value or "")
            profile_key = (
                backup_profiles.get(other_id)
                if backup_profiles else None
            )
            try:
                rows = self._conn.execute(
                    f"SELECT id, profile, {', '.join(content_columns)}"
                    f" FROM {table} WHERE created_at = ? AND id != ?",
                    (created_key, candidate),
                ).fetchall()
            except sqlite3.Error:
                return False
            for row in rows:
                other_local = int(row["id"])
                if (
                    require_pre_existing
                    and pre_existing_ids is not None
                    and other_local not in pre_existing_ids
                ):
                    continue
                if (
                    profile_key is not None
                    and _normalize_profile_name(
                        str(row["profile"] or "")
                    ) != _normalize_profile_name(profile_key)
                ):
                    continue
                if self._db_content_fingerprint(
                    row, content_columns
                ) == expected:
                    return True
            return False

        def _tail_preferred_survive(candidate: int) -> bool:
            """尾字段严格偏向的同内容兄弟备份行是否都另有本地存活行。

            打平/偏向本身不足以证明候选属于兄弟行：兄弟备份行若都
            另有自己的本地存活行，候选就是本行被编辑的存活版本，
            不能按被删记录补插（第一百零一轮 [4]）。
            """
            if not backup_tails or not backup_contents:
                return False
            expected_content = backup_contents.get(raw_id)
            if expected_content is None:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            preferred = []
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if backup_contents.get(other_id) != expected_content:
                    continue
                if _tail_match_score(candidate, other_id) > own_score:
                    preferred.append(other_id)
            if not preferred:
                return False
            return all(
                _backup_row_present_for(other_id, candidate)
                for other_id in preferred
            )

        def _tail_preferred_any_survive(
            candidate: int, cross_profile: bool = False
        ) -> bool:
            """尾字段严格偏向的同秒任一兄弟备份行是否都另有本地存活行。"""
            if not backup_tails:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            preferred = []
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    not cross_profile
                    and wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if _tail_match_score(candidate, other_id) > own_score:
                    preferred.append(other_id)
            if not preferred:
                return False
            return all(
                _backup_row_present_for(other_id, candidate)
                for other_id in preferred
            )

        def _tail_tied_survive(
            candidate: int, cross_profile: bool = False
        ) -> bool:
            """尾字段得分持平的兄弟备份行是否都另有本地存活行。

            打平本身不足以判歧义：被持平的兄弟备份行若都另有自己
            的本地存活行，候选就是本行被编辑的存活版本，不能按
            被删记录补插（第一百零一轮 [9]）。
            """
            if not backup_tails or not backup_contents or tail_where is None:
                return False
            expected_content = backup_contents.get(raw_id)
            if expected_content is None:
                return False
            own_score = _tail_match_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            tied = []
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    not cross_profile
                    and wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if backup_contents.get(other_id) != expected_content:
                    continue
                if _tail_match_score(candidate, other_id) == own_score:
                    tied.append(other_id)
            if not tied:
                return False
            return all(
                _other_backup_row_survives(other_id)
                for other_id in tied
            )

        def _tied_sibling_survives(sole: int) -> bool:
            """被持平的同内容兄弟备份行是否都另有导入前已存在的存活行。

            与 _tail_tied_survive 的差别：这里用「本次导入前已存在且
            内容等于兄弟备份」的口径，排除本轮刚补插出来的旧副本，
            避免链式删除后的补插被误当兄弟行的独立存活证据
            （第一百零三轮 [7]）。
            """
            if not backup_tails or not backup_contents or tail_where is None:
                return False
            expected_content = backup_contents.get(raw_id)
            if expected_content is None:
                return False
            own_score = _tail_match_score(sole, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            tied = []
            for other_id in backup_tails:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if backup_contents.get(other_id) != expected_content:
                    continue
                if _tail_match_score(sole, other_id) == own_score:
                    tied.append(other_id)
            if not tied:
                return False
            return all(
                _backup_row_present_for(other_id, sole)
                for other_id in tied
            )

        def _candidate_same_profile(candidate: int) -> bool:
            """候选是否仍属于当前备份行的档案。"""
            if not row_profile:
                return False
            try:
                row = self._conn.execute(
                    f"SELECT profile FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return False
            if row is None:
                return False
            return _normalize_profile_name(
                str(row["profile"] or "")
            ) == _normalize_profile_name(row_profile)

        def _raw_signature_present(exclude: int) -> bool:
            """不含候选的行里是否已有与本行内容+尾字段完全一致的存活行。

            同档案同内容孪生尾字段打平时：本行原签名（例如另一条孪生
            副本的备注）若仍在本地，被编辑的候选就是本行版本；完全不
            在时本行必须按被删记录补插，否则尾字段永久丢失
            （第一百零二轮 [8]）。
            """
            if tail_where is None or not backup_tails or not content_columns:
                return False
            expected_content = (backup_contents or {}).get(raw_id)
            expected_tail = backup_tails.get(raw_id)
            if expected_content is None or expected_tail is None:
                return False
            tail_columns = IMPORT_TAIL_COLUMNS.get(table, ())
            if not tail_columns:
                return False
            created_key = str(created_at_value or "")
            try:
                rows = self._conn.execute(
                    f"SELECT id, {', '.join(content_columns)},"
                    f" {', '.join(tail_columns)} FROM {table}"
                    " WHERE created_at = ? AND id != ?",
                    (created_key, exclude),
                ).fetchall()
            except sqlite3.Error:
                return False
            for row in rows:
                if self._db_content_fingerprint(
                    row, content_columns
                ) != expected_content:
                    continue
                if self._db_content_fingerprint(
                    row, tail_columns
                ) == expected_tail:
                    return True
            return False

        def _soft_matches(candidate: int) -> bool:
            if soft_where is None:
                return True
            fragment, extra = soft_where
            try:
                row = self._conn.execute(
                    f"SELECT 1 FROM {table} WHERE id = ? AND {fragment}",
                    (candidate, *extra),
                ).fetchone()
            except sqlite3.Error:
                return False
            return row is not None

        def _candidate_content_matches_backup(candidate: int) -> bool:
            """候选内容列是否仍与当前备份行一致（编辑只发生在软字段）。"""
            if not backup_contents or not content_columns:
                return True
            expected = backup_contents.get(raw_id)
            if expected is None:
                return True
            try:
                row = self._conn.execute(
                    f"SELECT {', '.join(content_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return True
            if row is None:
                return True
            return self._db_content_fingerprint(
                row, content_columns
            ) == expected

        def _soft_or_edited(candidate: int) -> bool:
            # 主键撞号只是巧合时，软字段（标题/名称）必须命中；
            # 真正被本地编辑过的存活行看不到软字段，改用编辑证据
            return _soft_matches(candidate) or _candidate_edited(candidate)

        def _edited_after_export() -> bool:
            """本行是否在备份导出之后被编辑过。

            updated_at 只有秒级精度：导出与编辑落在同一秒时两者相等，
            仍视为在快照之后编辑。备份缺少导出时间（旧版或手工构造）
            时不采信，避免把另一个库中同主键同秒的无关记录认领为
            本备份行的旧版本。
            """
            if not backup_exported_at or raw_id <= 0:
                return False
            try:
                row = self._conn.execute(
                    f"SELECT updated_at FROM {table} WHERE id = ?",
                    (raw_id,),
                ).fetchone()
            except sqlite3.Error:
                return False
            if row is None:
                return False
            updated = str(row["updated_at"] or "")
            return bool(updated) and updated >= backup_exported_at

        def _candidate_owned_by_profile_sibling(
            candidate: int, candidate_profile: str
        ) -> bool:
            """候选是否就是备份里另一条同秒同档案记录的存活行。

            只有内容指纹也命中那条记录时才算（改挂到其他成员的记录
            内容通常与自己的备份行一致）；内容不同的同档案同秒记录
            只是主键漂移的巧合，不能排除。
            """
            if not (
                backup_profiles and backup_created and backup_contents
            ) or not content_columns:
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
            wanted = _normalize_profile_name(candidate_profile)
            created_key = str(created_at_value or "")
            for other_id, other_profile in backup_profiles.items():
                if other_id == raw_id or other_id <= 0:
                    continue
                if backup_created.get(other_id) != created_key:
                    continue
                if _normalize_profile_name(other_profile) != wanted:
                    continue
                if backup_contents.get(other_id) == fingerprint:
                    try:
                        covered_rows = self._conn.execute(
                            f"SELECT id, {', '.join(content_columns)}"
                            f" FROM {table} WHERE profile = ?"
                            " AND created_at = ? AND id != ?",
                            (candidate_profile, created_key, candidate),
                        ).fetchall()
                    except sqlite3.Error:
                        covered_rows = []
                    if any(
                        self._db_content_fingerprint(
                            covered_row, content_columns
                        ) == fingerprint
                        for covered_row in covered_rows
                    ):
                        # 兄弟备份行已有自己的同内容存活行：候选是
                        # 改挂+编辑的本行版本（第九十三轮 [9]）
                        continue
                    if _tail_matches(candidate) and not (
                        _tail_prefers_sibling(candidate)
                    ):
                        # 候选尾字段精确命中本行：它是本行的存活版本，
                        # 不能被当成兄弟行的存活行放弃认领
                        continue
                    return True
            return False

        import_identity_names = IMPORT_IDENTITY_NAME_COLUMNS.get(table, ())

        def _candidate_diff_hits_identity(candidate: int) -> bool:
            """候选内容与当前行的差异是否落在药名/标题/症状等标识列。"""
            if not import_identity_names:
                return False
            expected = (backup_contents or {}).get(raw_id)
            if expected is None or not content_columns:
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
            local = self._db_content_fingerprint(row, content_columns)
            for index, (mine, want) in enumerate(zip(local, expected)):
                if mine == want:
                    continue
                column = content_columns[index]
                expr = re.match(r"IFNULL\(\s*(\w+)", column)
                name = expr.group(1) if expr else column
                if name in import_identity_names:
                    return True
            return False

        def _candidate_diff_claimable(candidate: int) -> bool:
            """差异是否只落在同一记录的可编辑取值（时间或数值）上。"""
            expected = (backup_contents or {}).get(raw_id)
            if expected is None or not content_columns:
                return True
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
            local = self._db_content_fingerprint(row, content_columns)
            identity_names = IMPORT_IDENTITY_NAME_COLUMNS.get(table, ())
            kinds = set()
            for index, (mine, want) in enumerate(zip(local, expected)):
                if mine == want:
                    continue
                column = content_columns[index]
                expr = re.match(r"IFNULL\(\s*(\w+)", column)
                name = expr.group(1) if expr else column
                if name in identity_names:
                    return False
                if re.search(
                    r"(?:^|_)(?:at|date|time)$|date|time", name
                ):
                    kinds.add("temporal")
                elif name.startswith("value"):
                    kinds.add("value")
                else:
                    return False
            return len(kinds) <= 1

        def _candidate_diff_claimable_for_sibling(
            candidate: int,
        ) -> bool:
            """候选内容与某条同秒兄弟备份行仅差时间/数值列。

            组内有缺行时，候选既可能是本行被改期后的存活版本，也可能
            是兄弟行（同样被改期）的存活版本；两者不可区分时应补插
            被删记录，而不是认领兄弟行。
            """
            if not backup_contents or not content_columns:
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
            local = self._db_content_fingerprint(row, content_columns)
            created_key = str(created_at_value or "")
            group_fingerprints: Optional[set] = None

            def _sibling_resolved(
                expected: tuple, sibling_id: int
            ) -> bool:
                """兄弟行的内容是否已有本地存活行（前一条补插后即解析）。

                本次导入为消除歧义补插的旧副本只有在确实对应这条兄弟
                备份行时才算解析；否则另一行会把存活行的编辑版本认领
                走，自己的旧内容永不补插。
                """
                nonlocal group_fingerprints
                ambiguous_map = state.get(
                    (table, "__ambiguous_contents__")
                )
                if (
                    isinstance(ambiguous_map, dict)
                    and ambiguous_map.get(sibling_id) == expected
                ):
                    return True
                if group_fingerprints is None:
                    group_fingerprints = set()
                    try:
                        group_rows = self._conn.execute(
                            f"SELECT id, {', '.join(content_columns)}"
                            f" FROM {table} WHERE {where}",
                            values,
                        ).fetchall()
                    except sqlite3.Error:
                        group_rows = []
                    for group_row in group_rows:
                        if pre_existing_ids is not None and (
                            int(group_row["id"]) not in pre_existing_ids
                        ):
                            # 本次导入刚插入的行不是原有存活行
                            continue
                        group_fingerprints.add(
                            self._db_content_fingerprint(
                                group_row, content_columns
                            )
                        )
                return expected in group_fingerprints

            for other_id, expected in backup_contents.items():
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if scope_profile and backup_profiles:
                    other_profile = _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    )
                    if other_profile and other_profile != scope_profile:
                        # 另一成员的同秒记录不参与本档案的孪生归属
                        continue
                if local == expected:
                    continue
                if _sibling_resolved(expected, other_id):
                    # 兄弟已恢复仍要核对候选归属：候选内容明显更像
                    # 那条兄弟记录（严重度/备注等）时它属于兄弟行，
                    # 本行仍按被删记录补插
                    if _content_score(
                        candidate, other_id
                    ) > _content_score(candidate, raw_id):
                        return True
                    continue
                temporal = True
                for index, (mine, want) in enumerate(
                    zip(local, expected)
                ):
                    if mine == want:
                        continue
                    column = content_columns[index]
                    expr = re.match(r"IFNULL\(\s*(\w+)", column)
                    name = expr.group(1) if expr else column
                    if not re.search(
                        r"(?:^|_)(?:at|date|time)$|date|time", name
                    ):
                        temporal = False
                        break
                if temporal:
                    return True
            return False

        def _candidate_diff_is_temporal(candidate: int) -> bool:
            """候选内容与当前行的差异是否只落在时间类列上。

            时间列（recorded_at/due_at/scheduled_at…）是可编辑数据，
            只差时间说明候选是本行被改期后的存活版本；差异含药名/
            标题/症状等标识列时更可能是另一条同秒记录。
            """
            expected = (backup_contents or {}).get(raw_id)
            if expected is None or not content_columns:
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
            local = self._db_content_fingerprint(row, content_columns)
            for index, (mine, want) in enumerate(zip(local, expected)):
                if mine == want:
                    continue
                column = content_columns[index]
                expr = re.match(r"IFNULL\(\s*(\w+)", column)
                name = expr.group(1) if expr else column
                if not re.search(
                    r"(?:^|_)(?:at|date|time)$|date|time", name
                ):
                    return False
            return True

        def _candidate_attributable_to_profile_sibling(
            candidate: int, candidate_profile: str
        ) -> bool:
            """候选是否可能属于备份里同档案同秒的另一行（不论内容）。

            对方把内容编辑成与被删记录相同后，只按内容指纹相等判断
            会误把对方存活行认领给本行：只要备份里存在同档案、同
            创建时间的另一条记录、且那条记录在本地没有「内容仍与
            备份一致」的其它存活行，就不能把候选当作本行改挂的
            版本，否则被删记录不恢复、对方记录被复制。
            对方记录另有内容一致的存活行时（真正的改挂），候选仍是
            本行的存活版本，可以继续认领。
            """
            if not (backup_profiles and backup_created) or not candidate_profile:
                return False
            wanted = _normalize_profile_name(candidate_profile)
            created_key = str(created_at_value or "")
            for other_id, other_profile in backup_profiles.items():
                if other_id == raw_id or other_id == 0:
                    continue
                if backup_created.get(other_id) != created_key:
                    continue
                if _normalize_profile_name(other_profile) != wanted:
                    continue
                if other_id <= 0:
                    # 无 id 备份的兄弟行没有真实主键：该档案下若另有
                    # 「内容仍与备份一致或已被本地编辑」的存活行（不含
                    # 候选、不含本次导入刚插入的行），兄弟记录就是那条
                    # 行，候选仍属本行改挂/编辑版本；否则候选可能是
                    # 被删兄弟的存活版本（第一百轮 [4]、第一百零二轮 [5]）
                    expected = (backup_contents or {}).get(other_id)
                    select_cols = "id, created_at, updated_at"
                    if content_columns:
                        select_cols += ", " + ", ".join(content_columns)
                    try:
                        other_rows = self._conn.execute(
                            f"SELECT {select_cols} FROM {table}"
                            " WHERE profile = ? AND created_at = ?"
                            " AND id != ?",
                            (candidate_profile, created_key, candidate),
                        ).fetchall()
                    except sqlite3.Error:
                        other_rows = []
                    survivor = False
                    for other_row in other_rows:
                        other_local = int(other_row["id"])
                        if (
                            pre_existing_ids is not None
                            and other_local not in pre_existing_ids
                        ):
                            continue
                        if (
                            str(other_row["updated_at"] or "")
                            != str(other_row["created_at"] or "")
                        ):
                            survivor = True
                            break
                        if (
                            expected is not None
                            and content_columns
                            and self._db_content_fingerprint(
                                other_row, content_columns
                            ) == expected
                        ):
                            survivor = True
                            break
                    if survivor:
                        continue
                    return True
                expected = (backup_contents or {}).get(other_id)
                if expected is not None and content_columns:
                    try:
                        rows = self._conn.execute(
                            f"SELECT id, {', '.join(content_columns)}"
                            f" FROM {table} WHERE profile = ?"
                            " AND created_at = ? AND id != ?",
                            (candidate_profile, created_key, candidate),
                        ).fetchall()
                    except sqlite3.Error:
                        rows = []
                    sibling_count = 1
                    for other2, other2_profile in backup_profiles.items():
                        if other2 in (raw_id, other_id) or other2 <= 0:
                            continue
                        if backup_created.get(other2) != created_key:
                            continue
                        if _normalize_profile_name(
                            other2_profile
                        ) != wanted:
                            continue
                        if (backup_contents or {}).get(other2) == expected:
                            sibling_count += 1
                    if sum(
                        1 for row in rows
                        if self._db_content_fingerprint(
                            row, content_columns
                        ) == expected
                    ) >= sibling_count:
                        # 每个同指纹兄弟备份行都各有独立存活行：
                        # 候选确属当前行（例如成员改挂后内容未变）
                        continue
                return True
            return False

        def _backup_soft_shared_with_sibling() -> bool:
            """当前备份行的软字段（药名/标题）是否与同秒兄弟行相同。

            同名/同标题孪生时单凭软字段无法区分候选归属；软字段不同
            （如「吃药」与「复查」）时命中本行软字段的候选就是本行
            被编辑过的存活版本，不能按跨档案尾字段偏好判给兄弟。
            """
            if soft_where is None or not backup_softs:
                return False
            expected = backup_softs.get(raw_id)
            if expected is None:
                return False
            created_key = str(created_at_value or "")
            for other_id, values in backup_softs.items():
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if values == expected:
                    return True
            return False

        def _backup_content_shared_with_sibling() -> bool:
            """当前备份行的内容是否与同身份的另一备份行完全相同。

            仅内容完全相同的孪生（如两条同秒同内容的预约）才允许
            末行认领被编辑的存活行；内容不同的同秒记录（不同剂量/
            不同日期）各自代表独立数据，被删版本必须补回。
            """
            expected = (backup_contents or {}).get(raw_id)
            if expected is None:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            for other_id, contents in backup_contents.items():
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if contents == expected:
                    return True
            return False

        def _backup_content_shared_cross_profile() -> bool:
            """内容与同创建时间的另一备份行完全相同（忽略档案）。

            跨成员孪生（如妈妈/爸爸各一条同内容预约）在改挂+删除后，
            被改挂的存活行尾字段又被编辑得无法归属时，不能仅凭
            「唯一候选 + 被编辑过」认领：候选更可能是被删记录的版本
            被改挂过来，本行必须按被删记录补回，否则删除记录永久
            丢失、旧档案还会复活一条陈旧副本。
            """
            expected = (backup_contents or {}).get(raw_id)
            if expected is None:
                return False
            created_key = str(created_at_value or "")
            for other_id, contents in backup_contents.items():
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if contents == expected:
                    return True
            return False

        def _sole_may_own_later_sibling(candidate: int) -> bool:
            """唯一候选的内容是否与尚未处理的同身份备份行完全相同。

            相同说明候选可能是后面那行（仅非内容字段不同）的存活行，
            先处理行应补回被删记录；内容不同（跨档案改挂/不同内容
            孪生）则候选确属当前行，可以立即认领。
            """
            if not backup_contents or not content_columns:
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
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            for other_id, contents in backup_contents.items():
                if other_id == raw_id or other_id in seen_raw_ids:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                if contents == fingerprint:
                    return True
            return False

        def _same_profile_exact_candidate(exclude: int) -> Optional[int]:
            """同档案里软字段/内容/尾字段全部命中本行的候选。

            跨档案改挂分支认领前要先看它：本行可能另有正常的同档案
            存活行（重复导入时被上一次恢复补插），改挂候选其实是
            兄弟备份行的存活版本，抢先认领会把对方复制成两条。
            """
            if soft_where is None or not row_profile:
                return None
            created_key = str(created_at_value or "")
            if not created_key:
                return None
            fragment, extra = soft_where
            try:
                rows = self._conn.execute(
                    f"SELECT id FROM {table}"
                    f" WHERE created_at = ? AND profile = ?"
                    f" AND {fragment} AND id != ?",
                    (created_key, row_profile, *extra, exclude),
                ).fetchall()
            except sqlite3.Error:
                return None
            for row in rows:
                candidate = int(row["id"])
                if candidate in moved or candidate in matched:
                    continue
                if (
                    pre_existing_ids is not None
                    and candidate not in pre_existing_ids
                ):
                    continue
                if content_where and not (
                    _candidate_content_matches_backup(candidate)
                ):
                    continue
                if tail_where is not None and not _tail_matches(candidate):
                    continue
                return candidate
            return None

        def _candidate_soft_owned_by_other(
            candidate: int,
            cross_profile: bool = False,
            require_content: bool = False,
        ) -> bool:
            """候选软字段是否等于备份里同档案的另一条同秒记录。

            只比较与候选当前档案一致的备份行：候选更像是同档案里被
            改名/改挂的兄弟行存活版本；软字段只命中其它档案的记录时
            （如另一种药被本地改名成同名）不能据此否定本行认领。
            """
            soft_columns = IMPORT_SOFT_COLUMNS.get(table)
            if not soft_columns or not backup_softs:
                return False
            try:
                row = self._conn.execute(
                    f"SELECT profile, {', '.join(soft_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return False
            if row is None:
                return False
            fingerprint = self._db_content_fingerprint(
                row, soft_columns
            )
            content_fingerprint = None
            if require_content and content_columns:
                try:
                    content_row = self._conn.execute(
                        f"SELECT {', '.join(content_columns)}"
                        f" FROM {table} WHERE id = ?",
                        (candidate,),
                    ).fetchone()
                except sqlite3.Error:
                    content_row = None
                if content_row is None:
                    return False
                content_fingerprint = self._db_content_fingerprint(
                    content_row, content_columns
                )
            candidate_profile = _normalize_profile_name(
                str(row["profile"] or "")
            )
            created_key = str(created_at_value or "")
            return any(
                other_id != raw_id
                and values == fingerprint
                and (
                    not require_content
                    or content_fingerprint is None
                    or (backup_contents or {}).get(other_id)
                    == content_fingerprint
                )
                and (
                    not backup_created
                    or backup_created.get(other_id) == created_key
                )
                and (
                    cross_profile
                    or not backup_profiles
                    or _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) == candidate_profile
                )
                for other_id, values in backup_softs.items()
            )

        def _content_rows_missing_for_orphan() -> bool:
            """组已满时孤儿认领前：本行或任一同秒备份行的内容是否缺失。

            主键漂移下同秒孪生可能互相顶替：若本行内容在本地已无任何
            存活行，被编辑的行更可能是兄弟行的改名版本；若有兄弟行的
            内容已不存在，被编辑行也可能是它的改挂版本。两种情况都应
            补插被删记录（宁重复勿丢），不能把候选抢走。
            """
            if not backup_contents or not content_columns:
                return False
            created_key = str(created_at_value or "")
            try:
                rows = self._conn.execute(
                    f"SELECT id, {', '.join(content_columns)} FROM {table}"
                    " WHERE created_at = ?",
                    (created_key,),
                ).fetchall()
            except sqlite3.Error:
                return True
            fingerprints = [
                self._db_content_fingerprint(row, content_columns)
                for row in rows
            ]
            missing_ids = [
                backup_id
                for backup_id, expected in backup_contents.items()
                if (
                    not backup_created
                    or backup_created.get(backup_id) == created_key
                )
                and expected not in fingerprints
            ]
            if not missing_ids:
                return False
            if local_group_size < backup_group_size:
                # 组内确有缺行：被删记录先补回，不能把编辑行抢走
                return True
            # 组内行数已齐：缺内容也可能只是某条记录被改名/改剂量，
            # 其存活行仍在。只有同秒某条存活行的尾字段严格更符合该
            # 备份行（唯一归属）时才视为可解释；否则内容确实缺失，
            # 仍按被删记录补插（第八十八轮 [9]，兼容第六十六/七十
            # 四轮「改挂+编辑+删除」契约）
            if not backup_tails:
                return True
            for missing_id in missing_ids:
                explained = False
                for row in rows:
                    candidate = int(row["id"])
                    own_score = _tail_match_score(candidate, missing_id)
                    if own_score < 0:
                        continue
                    best_other = -1
                    for other_id in backup_tails:
                        if other_id == missing_id:
                            continue
                        if (
                            backup_created
                            and backup_created.get(other_id) != created_key
                        ):
                            continue
                        other_score = _tail_match_score(
                            candidate, other_id
                        )
                        if other_score > best_other:
                            best_other = other_score
                    if own_score > best_other:
                        explained = True
                        break
                if not explained:
                    return True
            return False

        def _sibling_content_missing_for_orphan() -> bool:
            """本行以外的同秒备份行内容是否在本地缺失。

            与 _content_rows_missing_for_orphan 的区别：本行自己的
            旧内容因编辑而缺失不算——那正是被编辑存活行的正常现象
            （第一百轮 [7]）；只有兄弟备份行的内容也缺失时，候选才
            可能是被删兄弟的存活版本，本行需按被删记录补插
            （第一百轮 [4]）。
            """
            if not backup_contents or not content_columns:
                return False
            created_key = str(created_at_value or "")
            try:
                rows = self._conn.execute(
                    f"SELECT id, {', '.join(content_columns)} FROM {table}"
                    " WHERE created_at = ?",
                    (created_key,),
                ).fetchall()
            except sqlite3.Error:
                return True
            fingerprints = {
                self._db_content_fingerprint(row, content_columns)
                for row in rows
            }
            for other_id, expected in backup_contents.items():
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if expected not in fingerprints:
                    return True
            return False

        def _orphan_edited_candidate() -> Optional[int]:
            """软字段被本地改名后与本行对应的唯一编辑候选。

            「rem1 被改名」这类场景：本行软字段值在本地已找不到，
            漂移候选只剩兄弟行的未编辑存活行；组内没有缺行时，唯一
            被编辑且软字段不匹配、尾字段与本行一致的候选才是本行的
            存活版本，不能把它当成兄弟行补插旧版本。无软字段的表
            （症状/指标）用尾字段精确命中来认领被改内容列的存活行。
            """
            if _content_rows_missing_for_orphan():
                return None
            if local_group_size < backup_group_size:
                return None
            pool = [
                candidate for candidate in claimed
                if candidate not in moved and candidate not in matched
            ]
            if soft_where is not None:
                # 本行仍有「软字段与尾字段都命中」的正常存活行时，
                # 必须走常规认领，不能被另一个改名行的编辑证据抢走
                for candidate in pool:
                    if (
                        candidate in pool
                        and _soft_matches(candidate)
                        and (tail_where is None or _tail_matches(candidate))
                    ):
                        return None
            orphan = []
            for candidate in pool:
                if not _candidate_edited(candidate):
                    continue
                if soft_where is not None and _soft_matches(candidate):
                    continue
                if _content_owned_by_other(candidate):
                    # 内容对应备份里另一条记录（如跨成员改挂后的行）：
                    # 它属于那条记录的存活版本，不能当本行的孤儿
                    continue
                if _candidate_soft_owned_by_other(
                    candidate, cross_profile=True, require_content=True
                ):
                    # 软字段精确等于另一条同秒备份记录（如被改挂提醒
                    # 的标题命中兄弟行）：候选属于那条记录，本行必须
                    # 按被删记录补插而不是抢走它。改挂会同时改档案，
                    # 必须跨档案比较，否则兄弟记录的改挂行会被当成
                    # 本行的改名孤儿（第八十三轮 [4]）
                    continue
                candidate_profile_row = self._conn.execute(
                    f"SELECT profile FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
                candidate_profile_key = (
                    _normalize_profile_name(
                        str(candidate_profile_row["profile"] or "")
                    )
                    if candidate_profile_row is not None else ""
                )
                if (
                    candidate_profile_key
                    and candidate_profile_key
                    != _normalize_profile_name(row_profile)
                    and backup_profiles
                    and backup_created
                    and any(
                        other_id not in seen_raw_ids
                        and _normalize_profile_name(
                            backup_profiles.get(other_id) or ""
                        ) == candidate_profile_key
                        and backup_created.get(other_id)
                        == str(created_at_value or "")
                        for other_id in backup_profiles
                    )
                ):
                    # 跨档案孤儿：该候选所属档案还有未处理的备份行，
                    # 它很可能是那条记录的存活版本（第九十一轮 [11]）
                    continue
                if _tail_prefers_sibling(candidate, cross_profile=True):
                    # 尾字段明显更像同秒孪生（如另一成员恰好在同名
                    # 提醒上完成/停药）：候选属于那条记录，不能被当
                    # 成本行改名后的孤儿抢走
                    continue
                if _tail_prefers_sibling_any_content(candidate):
                    # 尾字段严格命中同秒另一条备份记录（不限内容）：
                    # 候选是那条记录改名后的存活版本，应留给它认领；
                    # 否则本行抢走后那条记录只能补插重复行
                    # （第八十八轮 [9]）
                    continue
                # 组已满（本地没有缺行）时，尾字段（备注/状态）被一起
                # 编辑过的唯一存活行同样是本行的版本：仅凭尾字段不再
                # 命中本行不能否定它，否则重复导入会补插编辑前旧副本
                orphan.append(candidate)
            if len(orphan) == 1:
                return orphan[0]
            return None

        def _content_score(candidate: int, target_raw_id: int) -> int:
            """候选与某条备份记录的逐内容列匹配数（-1 无法比较）。"""
            if not backup_contents or not content_columns:
                return -1
            expected = backup_contents.get(target_raw_id)
            if expected is None:
                return -1
            try:
                row = self._conn.execute(
                    f"SELECT {', '.join(content_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return -1
            if row is None:
                return -1
            local = self._db_content_fingerprint(row, content_columns)
            return sum(
                1 for mine, want in zip(local, expected) if mine == want
            )

        def _content_prefers_sibling(candidate: int) -> bool:
            """候选的内容更接近同组另一条同秒备份行（而非本行）。

            同秒孪生的 created_at 全相同，「主键未漂移」判定在源库删过
            记录时形同虚设；候选若只在 note 等单个内容列上不同、且更
            像兄弟备份行，就不能凭 raw id + 编辑证据自认领。
            """
            own_score = _content_score(candidate, raw_id)
            if own_score < 0:
                return False
            created_key = str(created_at_value or "")
            wanted_profile = (
                _normalize_profile_name(row_profile)
                if row_profile else None
            )
            best_other = own_score
            for other_id in backup_contents or {}:
                if other_id == raw_id:
                    continue
                if (
                    backup_created
                    and backup_created.get(other_id) != created_key
                ):
                    continue
                if (
                    wanted_profile is not None
                    and backup_profiles
                    and _normalize_profile_name(
                        backup_profiles.get(other_id) or ""
                    ) != wanted_profile
                ):
                    continue
                other_score = _content_score(candidate, other_id)
                if other_score > best_other:
                    best_other = other_score
            return best_other > own_score

        def _other_backup_row_survives(
            candidate: int, count_own_claims: bool = False
        ) -> bool:
            """备份中 raw id == candidate 的那条记录是否另有本地存活行。

            本地自增 id 与备份原始主键数值相等可能只是巧合（恢复后
            早已漂移）：若那条备份记录（同档案、同创建时间）在本地
            确实另有存活行，本候选更可能是当前备份行被改挂/编辑后的
            版本；否则把候选判给那条备份记录，避免被删记录的恢复抢占
            另一位成员的存活行。
            """
            if not backup_profiles or not backup_created:
                return False
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
                if other_id in moved or other_id in matched:
                    # 默认口径：本轮已被任何备份行认领/改挂的行不算
                    # 存活证据（第九十四轮 [9]）。主键撞号检查需要
                    # 认领者口径：被检查记录自己认领的恢复副本仍是
                    # 它的存活行（第一百零三轮 [13]）
                    if not (
                        count_own_claims
                        and claim_owner.get(other_id) == candidate
                    ):
                        continue
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
                    if backup_contents and content_columns:
                        expected_content = backup_contents.get(candidate)
                        try:
                            fingerprint_row = self._conn.execute(
                                f"SELECT {', '.join(content_columns)}"
                                f" FROM {table} WHERE id = ?",
                                (other_id,),
                            ).fetchone()
                        except sqlite3.Error:
                            fingerprint_row = None
                        if (
                            expected_content is not None
                            and fingerprint_row is not None
                            and self._db_content_fingerprint(
                                fingerprint_row, content_columns
                            ) != expected_content
                        ):
                            # 该行内容与那条备份记录不符（被编辑过）：
                            # 不能据此把候选判给兄弟
                            continue
                    return True
            return False
        content_where = " AND ".join(
            f"{column} = ?" for column in content_columns
        )
        #: 自动复诊预约（备注带就诊标记）被改挂到其他档案时，原档案的
        #: 恢复副本必须补插：否则就诊链接被清空，再次导入又会把这行
        #: 挂回就诊，形成旧副本复活/重复提醒
        auto_followup_row = bool(
            table == "appointments"
            and tail_where is not None
            and "由就诊记录 #" in str(tail_where[1][1] or "")
        )

        def _auto_followup_moved_copy(candidate: int) -> bool:
            """候选是否是自动复诊预约改挂到其他档案后的存活副本。"""
            if not auto_followup_row or not columns or columns[0] != "profile":
                return False
            try:
                candidate_row = self._conn.execute(
                    f"SELECT profile FROM {table} WHERE id = ?",
                    (candidate,),
                ).fetchone()
            except sqlite3.Error:
                return False
            return bool(
                candidate_row is not None
                and _normalize_profile_name(
                    str(candidate_row["profile"] or "")
                )
                != _normalize_profile_name(str(values[0]))
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
                and _soft_or_edited(int(row["id"]))
            ):
                # 主键命中的内容相同孪生行可能属于同组另一条备份记录：
                # 尾字段更像兄弟行时不能认领，否则被删记录不恢复、
                # 存活记录被复制。跨档案也要比较：id 漂移时本地编号
                # 等于 raw_id 的行可能是另一成员改挂进本档案的存活行
                # （第七十八轮）
                claim_row = int(row["id"])
                if (
                    claim_row == raw_id
                    and _same_backup_origin()
                    # 同秒同内容孪生尾字段不命中本行时，主键相等只是
                    # 撞号：未编辑的同号行是兄弟备份记录的存活行，
                    # 不能凭同源自认领（第一百零四轮 [4]）；导出后
                    # 编辑过的同号行仍是本行被编辑的存活版本
                    and not (
                        backup_contents
                        and backup_tails
                        and tail_where is not None
                        and _backup_content_shared_with_sibling()
                        and not _candidate_edited(claim_row)
                        and not _tail_matches(claim_row)
                    )
                ):
                    claimable_row = True
                elif claim_row == raw_id:
                    # 漂移库里本地编号恰等于备份主键只是巧合：必须
                    # 有尾字段精确命中且软字段不属于兄弟行，才能把
                    # 它当本行存活版本（第八十七轮）
                    claimable_row = bool(
                        _tail_matches(claim_row)
                        and not _candidate_soft_owned_by_other(
                            claim_row, cross_profile=True
                        )
                    )
                else:
                    claimable_row = bool(
                        not _tail_prefers_sibling(
                            claim_row, cross_profile=True
                        ) and (
                            _soft_matches(claim_row)
                            or not _candidate_soft_owned_by_other(
                                claim_row, cross_profile=True,
                                require_content=True,
                            )
                        )
                    )
                if claimable_row:
                    # 尾字段平局时，软字段对不上本行、却命中同秒另一条
                    # 备份行（且内容也相同）的候选属于那条记录：本行按
                    # 被删记录补插；软字段命中的合法存活行（同标题孪生）
                    # 仍照常认领（第八十三/八十四轮）
                    return _claim(claim_row)
        if raw_id > 0 and content_where and created_at_value:
            # 记录被 PATCH 改挂到另一档案：主键 + 创建时间 + 内容仍能确认
            # 是同一条，恢复旧备份时不能在原档案名下重新插入
            raw_scope_clause = ""
            raw_scope_params: Tuple[Any, ...] = ()
            row = self._conn.execute(
                f"SELECT id, profile FROM {table}"
                f" WHERE id = ? AND created_at = ? AND {content_where}",
                (
                    raw_id, created_at_value, *content_values,
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
                if (
                    not expected_row_profile
                    and match_where is not None
                    and str(match_where[0]).strip().lower().startswith(
                        "profile"
                    )
                ):
                    expected_row_profile = str(match_where[1][0])
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
                if claimable and _candidate_owned_by_profile_sibling(
                    candidate, str(row["profile"] or "")
                ):
                    # 候选实为备份里另一条同档案同秒同内容记录的存活
                    # 行（普通编辑也会刷新 updated_at），不能当作本行
                    # 改挂吞掉，否则被删记录不恢复、对方记录被复制
                    claimable = False
                if claimable and not _soft_or_edited(candidate):
                    # 内容列相同但软字段不符且无编辑证据：更像备份里
                    # 另一条同秒记录，交给后续内容/软字段配对
                    claimable = False
                if claimable and _auto_followup_moved_copy(candidate):
                    # 自动复诊预约被改挂：原档案补回副本而不是吞掉
                    claimable = False
                if (
                    claimable
                    and not _soft_matches(candidate)
                    and _candidate_soft_owned_by_other(
                        candidate, cross_profile=True,
                        require_content=True,
                    )
                    and _tail_tied_with_sibling(
                        candidate, cross_profile=True
                    )
                ):
                    # 候选软字段（标题）精确等于同秒另一条备份记录：
                    # 它是那条记录改挂后的存活行（备注为空/相同的
                    # 孪生不能靠尾字段区分），本行按被删记录补回
                    # （第八十三轮 [4]）
                    claimable = False
                if claimable and _tail_prefers_sibling(
                    candidate, cross_profile=True
                ):
                    # 候选尾字段精确命中同秒同内容另一条备份行：它是
                    # 那条记录改挂后的存活版本，本行必须按被删记录补插
                    # （与 step1 同口径，第八十轮）
                    claimable = False
                if claimable and (
                    _same_profile_exact_candidate(candidate) is not None
                ):
                    # 本档案已有软/内容/尾字段全部命中本行的存活行：
                    # 跨档案候选属于另一成员的兄弟记录（被改名/改挂），
                    # 不能抢先把它认领给本行（第一百零一轮 [8]）
                    claimable = False
                if (
                    claimable
                    and candidate == raw_id
                    and not _same_backup_origin()
                    and not _tail_matches(candidate)
                ):
                    # 漂移库中本地编号恰等于备份主键只是巧合：
                    # 尾字段对不上时本行按被删记录补插（第八十七轮）
                    claimable = False
                if claimable:
                    # 同一候选行不能再被任何身份组（目标档案的内容配对
                    # 与兜底）重复认领，否则被删的同秒记录会被当成
                    # “已存在”
                    return _claim(candidate, mark_moved=True)
            if (
                soft_where is not None
                and created_at_value
                and backup_group_size <= 1
            ):
                # 改挂同时编辑了内容列（提醒 due/用药 dose/预约时间）：
                # 主键 + 创建时间 + 软字段 + 编辑证据仍能确认同一条，
                # 否则会在旧档案名下复活旧版本。同身份组有多条备份
                # 记录（同秒同名兄弟）时无法区分，交给软字段配对，
                # 避免把其中一条的存活行误判成另一条的改挂版本
                soft_fragment, soft_extra = soft_where
                soft_row = self._conn.execute(
                    f"SELECT id FROM {table}"
                    f" WHERE id = ? AND created_at = ? AND {soft_fragment}",
                    (raw_id, created_at_value, *soft_extra),
                ).fetchone()
                if soft_row is not None:
                    candidate = int(soft_row["id"])
                    candidate_profile_row = self._conn.execute(
                        f"SELECT profile FROM {table} WHERE id = ?",
                        (candidate,),
                    ).fetchone()
                    candidate_profile = (
                        str(candidate_profile_row["profile"] or "")
                        if candidate_profile_row is not None else ""
                    )
                    if (
                        (
                            pre_existing_ids is None
                            or candidate in pre_existing_ids
                        )
                        and candidate not in moved
                        and candidate not in matched
                        and _candidate_edited(candidate)
                        and not _candidate_owned_by_profile_sibling(
                            candidate, candidate_profile
                        )
                        and not _candidate_soft_owned_by_other(candidate)
                        and not _auto_followup_moved_copy(candidate)
                        and not (
                            _content_owned_by_other(candidate)
                            and not (
                                _same_backup_origin()
                                and candidate == raw_id
                            )
                        )
                        and not _tail_prefers_sibling(
                            candidate, cross_profile=True
                        )
                        and not (
                            candidate == raw_id
                            and not _same_backup_origin()
                            and not _tail_matches(candidate)
                        )
                    ):
                        # 候选软字段命中同档案另一条同秒备份记录时
                        # 留给那条记录认领，本行按漂移/内容配对
                        return _claim(candidate, mark_moved=True)

            # 跨库/合并恢复导致主键漂移后，改挂不能只靠主键相等识别：
            # 按「创建时间 + 内容」在导入前已存在的行中找唯一候选
            if pre_existing_ids:
                # 空集合时候选必然为空，跳过可让空库/换机恢复保持线性
                # 档案约束不能直接放进 SQL：改挂到另一位成员的记录
                # 档案已变化，直接过滤会看不到它。跨档案候选在下面
                # 的循环里要求编辑证据；非档案约束照常参与查询
                drift_clause = ""
                drift_params: Tuple[Any, ...] = ()
                profile_scoped = bool(
                    match_where is not None
                    and str(match_where[0]).strip().lower().startswith(
                        "profile"
                    )
                )
                if match_where is not None and not profile_scoped:
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
                if (
                    not row_profile
                    and match_where is not None
                    and str(match_where[0]).strip().lower().startswith(
                        "profile"
                    )
                ):
                    # 提醒等身份列不含档案：跨档案候选同样需要编辑
                    # 证据，未编辑的另一成员同秒同内容行不能被认领
                    row_profile = str(match_where[1][0])
                candidates = []
                for row in rows:
                    candidate = int(row["id"])
                    if candidate not in pre_existing_ids:
                        continue
                    if candidate in moved or candidate in matched:
                        continue
                    if (
                        candidate == raw_id
                        and not _same_backup_origin()
                        and not _tail_matches(candidate)
                    ):
                        # 漂移库中本地编号恰等于本备份主键只是巧合：
                        # 尾字段对不上时它是同秒另一条记录的存活行，
                        # 本行按被删记录补插（第八十七轮）
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
                            and not _other_backup_row_survives(
                                candidate, count_own_claims=True
                            )
                        ):
                            # 主键撞号只说明数字巧合：还要核对候选内容
                            # 是否真是那条备份记录的版本；内容其实是
                            # 本行被编辑后的版本（如本行改了备注）时
                            # 不能排除，否则删除的孪生会被重复补插
                            expected_candidate = (
                                backup_contents.get(candidate)
                                if backup_contents else None
                            )
                            candidate_matches_other = False
                            if (
                                expected_candidate is not None
                                and content_columns
                            ):
                                try:
                                    candidate_meta = self._conn.execute(
                                        f"SELECT {', '.join(content_columns)}"
                                        f" FROM {table} WHERE id = ?",
                                        (candidate,),
                                    ).fetchone()
                                except sqlite3.Error:
                                    candidate_meta = None
                                candidate_matches_other = bool(
                                    candidate_meta is not None
                                    and self._db_content_fingerprint(
                                        candidate_meta, content_columns
                                    )
                                    == expected_candidate
                                )
                            if (
                                candidate_matches_other
                                or _same_backup_origin()
                            ):
                                # 该本地行确实对应备份里的另一条同档案
                                # 同秒记录：不能被当作本行“改挂”的版本
                                continue
                    candidate_profile = str(row["profile"] or "")
                    if (
                        row_profile
                        and candidate_profile != row_profile
                    ):
                        # 自动复诊预约属于原档案的就诊记录：跨档案的
                        # 存活行只是被用户改挂的副本，不能据此吞掉原
                        # 档案的恢复副本，否则就诊链接被清空、再次导入
                        # 时又把这行挂回就诊（旧副本复活）
                        if _auto_followup_moved_copy(candidate):
                            continue
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
                        # 候选档案与备份里另一条同秒记录一致：它是那条
                        # 记录的存活行（即使内容已被改成与本行相同），
                        # 不能当作本行改挂，否则被删记录丢失、对方记录
                        # 被复制。这里只看跨档案候选，对方备份行必然会
                        # 被自己的原始记录认领，本行组内是否最后一条无关
                        if _candidate_attributable_to_profile_sibling(
                            candidate, candidate_profile
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
                    filtered_candidates = [
                        candidate for candidate in candidates
                        if candidate in soft_ids
                    ]
                    if (
                        not filtered_candidates
                        and tail_where is not None
                        and not _backup_content_shared_with_sibling()
                    ):
                        candidates = [
                            candidate for candidate in candidates
                            if not _candidate_soft_owned_by_other(
                                candidate, cross_profile=True,
                                require_content=True,
                            )
                        ]
                        placeholders = ",".join(
                            "?" for _ in candidates
                        )
                        # 软字段被本地编辑过（提醒改标题）：回退到满足
                        # 尾字段的漂移候选，避免把改编前旧版本再插一份。
                        # 同内容孪生（如两条同秒同到期日提醒）无法用
                        # 尾字段区分时必须保持歧义，不能吞掉兄弟行
                        tail_fragment, tail_extra = tail_where
                        tail_rows = self._conn.execute(
                            f"SELECT id FROM {table}"
                            f" WHERE id IN ({placeholders})"
                            f" AND {tail_fragment}",
                            (*candidates, *tail_extra),
                        ).fetchall() if candidates else []
                        tail_ids = {int(row["id"]) for row in tail_rows}
                        filtered_candidates = [
                            candidate for candidate in candidates
                            if candidate in tail_ids
                        ]
                    candidates = filtered_candidates
                if (
                    len(candidates) > 1
                    and soft_where is not None
                    and all(
                        _soft_matches(candidate)
                        for candidate in candidates
                    )
                    and (
                        tail_where is None
                        or all(
                            _tail_matches(candidate)
                            for candidate in candidates
                        )
                    )
                ):
                    # 多个候选在本行的软字段/内容/尾字段上完全等价：
                    # 优先未编辑的那条。被编辑过的同内容候选更可能
                    # 属于另一条同秒备份行（如被改挂到本档案的兄弟
                    # 记录），按 min(id) 认领会把它从原记录名下抢走，
                    # 导致原记录旧版本被插入成重复
                    fresh_candidates = [
                        candidate for candidate in candidates
                        if not _candidate_edited(candidate)
                    ]
                    if fresh_candidates:
                        candidates = fresh_candidates
                if len(candidates) > 1 and tail_where is not None:
                    # 同内容孪生（同秒两条相同症状/提醒）：尾字段精确
                    # 命中本行的唯一候选是本行的存活版本（可能已改挂
                    # 到其他成员）；其余候选的尾字段更像兄弟备份行时
                    # 交给那条记录认领。不先把改挂行挑出来的话，两条
                    # 候选互相抵消，本行会按「缺行」把旧版本补插成重复
                    own_tail = [
                        candidate for candidate in candidates
                        if _tail_matches(candidate)
                    ]
                    if len(own_tail) == 1:
                        others = [
                            candidate for candidate in candidates
                            if candidate != own_tail[0]
                        ]
                        if all(
                            _tail_prefers_sibling(candidate)
                            for candidate in others
                        ):
                            candidates = own_tail
                if (
                    not candidates
                    and local_group_size >= backup_group_size
                ):
                    orphan = _orphan_edited_candidate()
                    if orphan is not None:
                        # 软字段被本地编辑（改名提醒/改药名过敏）、组内
                        # 无缺行：唯一的编辑孤儿就是本行存活版本
                        return _claim(orphan, mark_moved=True)
                if len(candidates) == 1:
                    sole = min(candidates)
                    skip_sole = False
                    if (
                        not _tail_matches(sole)
                        and _backup_soft_shared_with_sibling()
                        and _tail_prefers_sibling(sole, cross_profile=True)
                        and not _tail_prefers_sibling(sole)
                        and not _tail_preferred_survive(sole)
                    ):
                        # 同名孪生（软字段相同）时软字段命中不能证明
                        # 候选属于本行：尾字段精确命中另一档案的备份
                        # 记录时它是那条记录改挂后的存活版本，本行按
                        # 被删记录补回（第八十一轮）。被偏向的兄弟
                        # 备份行若都另有本地存活行，候选仍是本行编辑
                        # 版本，不能按被删记录补插（第一百零一轮 [4]）
                        return _ambiguous()
                    if not _tail_matches(sole) and _tail_prefers_sibling(
                        sole
                    ):
                        # 候选尾字段更像同组另一条同内容孪生备份行
                        if local_group_size < backup_group_size:
                            tail_exact = [
                                candidate for candidate in claimed
                                if candidate not in moved
                                and candidate not in matched
                                and candidate != sole
                                and _tail_matches(candidate)
                                and _candidate_edited(candidate)
                                and (
                                    (
                                        candidate == raw_id
                                        and _same_backup_origin()
                                    )
                                    or not _candidate_soft_owned_by_other(
                                        candidate, cross_profile=True
                                    )
                                )
                                and not _content_owned_by_other(candidate)
                            ]
                            if len(tail_exact) == 1:
                                # 组内缺行是上一次恢复补插的行造成的：
                                # 本行改名后的存活行尾字段仍精确命中
                                # 本行，按它认领而不是补插旧版本
                                # （第八十三轮 [10] 幂等）
                                return _claim(
                                    tail_exact[0], mark_moved=True
                                )
                            # 组内确有缺行：本行被改挂+编辑的存活行
                            # 若在另一档案且尾字段仍命中本行，直接
                            # 认领，不能再补插改挂前旧副本
                            # （第九十三轮 [8]）
                            if (
                                _same_backup_origin()
                                and soft_where is not None
                                and created_at_value
                            ):
                                moved_pool = []
                                try:
                                    moved_rows = self._conn.execute(
                                        f"SELECT id FROM {table}"
                                        " WHERE created_at = ?",
                                        (str(created_at_value),),
                                    ).fetchall()
                                except sqlite3.Error:
                                    moved_rows = []
                                for moved_row in moved_rows:
                                    moved_id = int(moved_row["id"])
                                    if moved_id in moved or moved_id in matched:
                                        continue
                                    # 同源库里本地主键与备份一一
                                    # 对应：禁止认领主键命中另一条备份
                                    # 记录的存活行；删除后由导入补插的
                                    # 恢复副本（id 超出备份主键集合）
                                    # 凭编辑证据仍可认领，否则其改挂后
                                    # 第二次导入会复活旧副本
                                    # （第九十四轮 [3]/第九十五轮 [4]）
                                    if (
                                        moved_id != raw_id
                                        and moved_id in backup_ids
                                    ):
                                        continue
                                    if not _candidate_edited(moved_id):
                                        continue
                                    # 本行自己的主键行尾字段被本地编辑
                                    # 是合法情况：只对其它行的候选要求
                                    # 尾字段命中（第九十六轮 [7]）
                                    if (
                                        moved_id != raw_id
                                        and not _tail_matches(moved_id)
                                    ):
                                        continue
                                    if not _soft_matches(moved_id):
                                        continue
                                    if _content_owned_by_other(moved_id):
                                        continue
                                    profile_row = self._conn.execute(
                                        f"SELECT profile FROM {table}"
                                        " WHERE id = ?",
                                        (moved_id,),
                                    ).fetchone()
                                    if profile_row is None:
                                        continue
                                    if _normalize_profile_name(
                                        str(profile_row["profile"] or "")
                                    ) == _normalize_profile_name(
                                        row_profile
                                    ):
                                        continue
                                    moved_pool.append(moved_id)
                                if len(moved_pool) == 1:
                                    return _claim(
                                        moved_pool[0], mark_moved=True
                                    )
                            # 组内确有缺行：本行按被删记录补插
                            return _ambiguous()
                        # 组已满：候选属于兄弟行，跳过漂移认领，
                        # 交给后面的软字段/尾字段分支选对本行版本
                        skip_sole = True
                        orphan = _orphan_edited_candidate()
                        if orphan is not None:
                            return _claim(orphan, mark_moved=True)
                    if not skip_sole:
                        if (
                            not is_group_last
                            and backup_group_size > 1
                            and local_group_size < backup_group_size
                            and _sole_may_own_later_sibling(sole)
                        ):
                            # 同档案同秒的孪生组里，先处理行的唯一候选
                            # 可能是后面那行内容相同、仅非内容字段不同的
                            # 存活行：非内容字段与本行一致时它就是本行
                            # 的存活版本；存活行被编辑过（备注等尾字段已
                            # 变）时若尾字段更像同组另一条备份行，候选属于
                            # 兄弟行，必须先补回本行
                            if not _tail_matches(sole) and (
                                _tail_prefers_sibling(sole)
                                or not _candidate_edited(sole)
                                or (
                                    _tail_tied_with_sibling(
                                        sole, cross_profile=True
                                    )
                                    and not (
                                        sole == raw_id
                                        and _same_backup_origin()
                                    )
                                    and not _tail_tied_survive(
                                        sole, cross_profile=True
                                    )
                                )
                            ):
                                # 尾字段与兄弟行打平（备注被编辑得都
                                # 不像）：无法归属时宁重复勿丢；但本行
                                # 自身主键、同源的存活行就是本行被编辑
                                # 的版本，照常认领（第八十四轮）；被
                                # 持平的兄弟备份行都另有存活行时候选
                                # 仍属本行（第一百零一轮 [9]）
                                return _ambiguous()
                        if (
                            is_group_last
                            and tail_where is not None
                            and not _tail_matches(sole)
                            and (
                                _tail_prefers_sibling(sole)
                                or not _candidate_edited(sole)
                                # 同内容孪生分属不同档案（改挂后被删）：
                                # 尾字段被编辑得与本行、兄弟行都持平、
                                # 无法归属时必须按被删记录补回；尾字段
                                # 仍明显像本行（完成/停药等本地编辑）
                                # 时照常认领。仅当本地库可证明与备份同源
                                # （主键空间未漂移）且候选是兄弟行的主键
                                # 时成立：此时候选不可能是本行被改挂的行，
                                # 本行按被删记录补回；主键漂移的库里候选
                                # 主键无意义，普通编辑仍按内容认领（与
                                # 第 50/52 轮契约一致）
                                or (
                                    _same_backup_origin()
                                    and sole != raw_id
                                    and _backup_content_shared_cross_profile()
                                    and _tail_tied_with_sibling(
                                        sole, cross_profile=True
                                    )
                                )
                            )
                        ):
                            # 最后一行同样核对非内容字段：候选属于更早的
                            # 兄弟行时必须补插本行（停药/完成状态等）。
                            # 存活行被编辑过且尾字段不再指向兄弟行时仍按
                            # 本行认领
                            return _ambiguous()
                        if _content_owned_by_other(sole):
                            return _ambiguous()
                        if _candidate_foreign_backup_row(sole):
                            # 候选是另一成员同秒备份行改挂后的存活
                            # 版本：本行按被删记录补插
                            return _ambiguous()
                        if (
                            not _tail_matches(sole)
                            and _tail_prefers_sibling_any_content(sole)
                            and not _tail_preferred_any_survive(sole)
                        ):
                            # 候选内容是本行的，但尾字段明确属于同秒
                            # 另一条记录：按被删记录补回。被偏向的
                            # 兄弟备份行都另有本地存活行时不能据此
                            # 判歧义（第一百零一轮 [4]）
                            return _ambiguous()
                        if (
                            not _tail_matches(sole)
                            and _backup_content_shared_with_sibling()
                            and _candidate_same_profile(sole)
                            and _tail_tied_with_sibling(
                                sole, cross_profile=True
                            )
                            and not _raw_signature_present(sole)
                            and not (
                                local_group_size >= backup_group_size
                                and _tied_sibling_survives(sole)
                            )
                        ):
                            # 同档案同内容孪生尾字段打平，且本行原签名
                            # （内容+尾字段）在本地已完全缺失：候选只是
                            # 兄弟行的编辑版本，本行必须按被删记录补插，
                            # 否则尾字段永久丢失（第一百零二轮 [8]）。
                            # 被持平的兄弟备份行若已另有存活行，候选
                            # 只能是本行被编辑的存活版本，不能补插
                            # （第一百零三轮 [7]）；但组内确有缺行时
                            # 兄弟的存活行不能证明候选属于本行，仍按
                            # 被删记录补插（第一百零四轮 [3]）
                            return _ambiguous()
                        return _claim(sole, mark_moved=True)
        if soft_where is not None and created_at_value and pre_existing_ids:
            # 改挂到其他成员并编辑内容列、且主键漂移时，身份组里
            # 找不到存活行；created_at + 软字段（不限档案）+ 编辑
            # 证据仍能唯一锚定它，避免在旧成员名下复活旧版本
            fragment, extra = soft_where
            moved_rows = self._conn.execute(
                f"SELECT id, profile FROM {table} WHERE created_at = ?"
                f" AND {fragment}",
                (created_at_value, *extra),
            ).fetchall()
            moved_candidates = []
            for row in moved_rows:
                candidate = int(row["id"])
                if (
                    candidate not in pre_existing_ids
                    or candidate in moved
                    or candidate in matched
                ):
                    continue
                # 只处理跨档案改挂：同档案候选交给软字段/锚点分支，
                # 否则会把同秒不同内容的孪生编辑行误认领给本行
                candidate_profile = str(row["profile"] or "")
                if not row_profile or _normalize_profile_name(
                    candidate_profile
                ) == _normalize_profile_name(row_profile):
                    continue
                if not _candidate_edited(candidate):
                    continue
                if _auto_followup_moved_copy(candidate):
                    # 自动复诊预约的跨档案副本：原档案应补回自己的
                    # 恢复副本，不能把改挂行吞成本行版本
                    continue
                if _content_owned_by_other(candidate):
                    continue
                if (
                    (
                        (raw_id <= 0 and backup_group_size > 1)
                        or not backup_tails
                    )
                    and tail_where is not None
                    and not _tail_matches(candidate)
                    and _candidate_content_matches_backup(candidate)
                ):
                    # 无 id 备份没有跨行归属元数据：候选内容仍等于
                    # 本行、仅尾字段不符时，它可能是同秒另一条无 id
                    # 备份行的存活版本，不能认领；内容同时被编辑
                    # （改挂存活行）时尾字段不再可辨，允许认领
                    # （第九十五轮 [9]、第九十六轮 [2]）
                    continue
                if (
                    _candidate_owns_unprocessed_backup_row(candidate)
                    or _other_backup_row_survives(candidate)
                    or _tail_prefers_sibling(
                        candidate, cross_profile=True
                    )
                ) and not (
                    soft_where is not None
                    and _soft_matches(candidate)
                    and _tail_matches(candidate)
                    and _candidate_content_matches_profile_sibling(
                        candidate, candidate_profile
                    )
                ):
                    # 候选本地主键命中另一条尚未处理的备份行、或那条
                    # 备份记录已在本地另有存活行、或尾字段更像兄弟行：
                    # 候选属于那条记录，本行按被删记录补插
                    # （第八十七轮）
                    continue
                if _candidate_attributable_to_profile_sibling(
                    candidate, candidate_profile
                ) and not (
                    (
                        soft_where is not None
                        and _soft_matches(candidate)
                        and _tail_matches(candidate)
                        and _candidate_content_matches_profile_sibling(
                            candidate, candidate_profile
                        )
                    )
                    # 内容已被编辑、尾字段仍精确命中本行且不偏向
                    # 兄弟行：候选是本行改挂后的存活版本，即使兄弟
                    # 备份行被删除（第九十六轮 [8]）。内容等于本行
                    # 备份的候选是改名同化行，仍需按被删记录补回
                    # （第五十三轮口径）
                    or (
                        not _candidate_content_matches_backup(candidate)
                        and _tail_matches(candidate)
                        and not _tail_prefers_sibling(
                            candidate, cross_profile=True
                        )
                        # 尾字段打平不足以证明候选属于本行：同秒同档案
                        # 兄弟备份行仍未处理时，候选可能是被删兄弟的
                        # 存活版本，本行按被删记录补插（第一百轮 [4]）
                        and _moved_tail_beats_sibling(
                            candidate, candidate_profile
                        )
                    )
                    # 无 id 备份的改挂行内容+尾字段同时被编辑：尾
                    # 字段不偏向任何兄弟行时按本行存活版本认领，
                    # 否则被删记录只能靠复制旧副本恢复
                    # （第九十六轮 [2]）。内容仍等于本行备份的候选
                    # 不是「改挂+编辑」版本，不能因此认领
                    # （第一百零二轮 [5]）
                    or (
                        raw_id <= 0
                        and not _candidate_content_matches_backup(candidate)
                        and not _tail_matches(candidate)
                        and not _tail_prefers_sibling_any_content(
                            candidate, cross_profile=True
                        )
                    )
                ):
                    # 软字段与尾字段仍精确命中本行、且内容同时等于
                    # 同秒兄弟备份行（同日复诊/同到期提醒）时，认领
                    # 不会丢失兄弟行的任何信息，候选就是本行改挂后
                    # 的存活版本；尾字段被编辑过或会丢兄弟内容时仍按
                    # 被删记录补插（第八十三轮）
                    continue
                moved_candidates.append(candidate)
            if len(moved_candidates) == 1 and (
                _same_profile_exact_candidate(moved_candidates[0])
                is None
            ):
                # 同档案里若已有软/内容/尾字段全部命中的存活行，它
                # 才是本行的版本；改挂候选属于兄弟备份行，留到下面
                # 按正常配对认领，否则兄弟行会被复制成两条
                return _claim(moved_candidates[0], mark_moved=True)

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
            deferred_candidate: Optional[int] = None
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
                    if _tail_prefers_sibling(
                        candidate, cross_profile=True
                    ) and not _tail_matches(candidate):
                        # 同内容孪生：候选的尾字段（停药/完成等）更像
                        # 兄弟备份行（含被改挂到其它成员的）时留给那
                        # 条记录认领（第八十六轮）
                        continue
                    if not is_group_last:
                        candidate_profile_row = self._conn.execute(
                            f"SELECT profile FROM {table} WHERE id = ?",
                            (candidate,),
                        ).fetchone()
                        candidate_profile_value = (
                            str(candidate_profile_row["profile"] or "")
                            if candidate_profile_row is not None else ""
                        )
                        if _candidate_owned_by_profile_sibling(
                            candidate, candidate_profile_value
                        ):
                            # 缺行数已被前面的补插覆盖时，剩余候选
                            # 属于当前行；本地全部存活时同理
                            missing = max(
                                backup_group_size - local_group_size, 0
                            )
                            inserted = int(
                                state.get(
                                    (table, "__ambiguous__", identity,
                                     scope_profile), 0
                                )
                            )
                            if inserted < missing:
                                continue
                    if (
                        not _tail_matches(candidate)
                        and _tail_prefers_sibling_any_content(candidate)
                    ):
                        # 内容列被改成与本行相同、但尾字段明确属于
                        # 另一条同秒记录：不能在此直接认领
                        continue
                    if (
                        raw_id <= 0
                        and local_group_size < backup_group_size
                        and not _tail_matches(candidate)
                    ):
                        # 无 id 的备份行没有元数据可用：组内确有缺行、
                        # 候选尾字段又对不上时，先按本行补插完整数据，
                        # 存活行稍后自行认领，避免被删记录丢失；候选
                        # 被编辑过也不能作为归属证据（第八十五/八十七轮）
                        continue
                    if (
                        candidate == raw_id
                        and not _same_backup_origin()
                        and not _tail_matches(candidate)
                    ):
                        # 漂移库中本地编号恰等于备份主键只是巧合：
                        # 不能延后认领为“本行版本”（第八十七轮）
                        continue
                    if _tail_matches(candidate):
                        return _claim(candidate)
                    if deferred_candidate is None:
                        deferred_candidate = candidate
                    continue
            if deferred_candidate is not None and (
                raw_id > 0
                or (
                    local_group_size >= backup_group_size
                    and (
                        tail_where is None
                        or _tail_matches(deferred_candidate)
                        # 单行组（组内没有其它同秒备份行）尾字段被本地
                        # 编辑只是正常状态变化，不能因此补插旧副本；
                        # 未编辑的异尾行仍按被删记录补插
                        # （第九十七轮 [4]、第九十八轮 [9]）
                        or (
                            backup_group_size <= 1
                            and _candidate_edited(deferred_candidate)
                        )
                    )
                )
            ):
                # 无 id 行在组计数被改挂行抬高时也要核对尾字段，
                # 否则被删记录会被尾字段不符的候选顶替
                # （第九十六轮 [3]）
                return _claim(deferred_candidate)
        candidates = {
            c for c in claimed if c not in moved and c not in matched
        }
        if backup_ids:
            # 本地行若对应备份里的另一条同档案记录，不能拿来认领本行；
            # 改挂到其它档案的记录仍可参与漂移配对
            filtered_candidates = set()
            for candidate_idx in candidates:
                if candidate_idx == raw_id:
                    # 漂移库里本地行 id 恰等于本行原始主键只是巧合：
                    # 若可证明非同源、且软字段/内容都不命中本行，它是
                    # 备份里另一条同秒记录的改名存活行（如「诊断0」
                    # 被改成「诊断改」），不能当成本行存活版本吞掉，
                    # 否则那条被删记录（诊断1）永远无法恢复
                    if (
                        _same_backup_origin()
                        or _soft_matches(candidate_idx)
                        or _candidate_content_matches_backup(candidate_idx)
                    ):
                        filtered_candidates.add(candidate_idx)
                    continue
                if candidate_idx not in backup_ids:
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
                    f"SELECT profile, {', '.join(content_columns)}"
                    f" FROM {table} WHERE id = ?",
                    (candidate_idx,),
                ).fetchone()
                if candidate_meta is None or _normalize_profile_name(
                    candidate_meta["profile"] or ""
                ) != expected_profile:
                    filtered_candidates.add(candidate_idx)
                    continue
                expected_contents = (backup_contents or {}).get(
                    candidate_idx
                )
                if (
                    expected_contents is not None
                    and content_columns
                    and self._db_content_fingerprint(
                        candidate_meta, content_columns
                    ) != expected_contents
                ):
                    # 主键数值与另一条备份记录相同、但内容其实是当前行
                    # 被编辑后的存活版本：不能按撞号判给那条记录
                    filtered_candidates.add(candidate_idx)
            candidates = filtered_candidates
        if (
            raw_id > 0
            and raw_id in candidates
            and (pre_existing_ids is None or raw_id in pre_existing_ids)
            and (
                backup_group_size <= 1
                or local_group_size >= backup_group_size
                # 同源库中本地 id==备份主键且创建时间一致：即使组内
                # 确有缺行（另一条同秒记录被删），这一行也确定是本行
                # 的存活版本，编辑后应认领自身而不是补插旧副本
                or (
                    _same_backup_origin()
                    and _local_holes_are_backup_ids()
                    and (
                        _edited_after_export()
                        or _candidate_edited(raw_id)
                    )
                )
            )
            and (
                _soft_or_edited(raw_id)
                or (
                    _edited_after_export()
                    and (
                        _candidate_edited(raw_id)
                        # 同一秒内的软/尾字段编辑：内容列仍与备份一致，
                        # 同样能证明它就是这个备份行的本地版本
                        or _candidate_content_matches_backup(raw_id)
                    )
                )
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
                # 同源库主键不复用，尾字段被编辑得偏向兄弟行时该行
                # 仍是本行存活版本（第八十五轮）；本地确有缺行时
                # 仍走插入，优先补回被删记录
                if (
                    not (raw_id > 0 and _same_backup_origin())
                    and _tail_prefers_sibling(raw_id)
                    and not _tail_matches(raw_id)
                ):
                    # 本主键上的行其实是同组另一条孪生备份记录的
                    # 存活行：交给那条记录认领，本行按被删补插
                    pass
                else:
                    return _claim(raw_id)
        if not candidates:
            twin = _cross_profile_twin_candidate()
            if twin is not None:
                return _claim(twin, mark_moved=True)
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
                if (
                    backup_group_size > 1
                    and backup_anchor_size > 1
                    and local_group_size < backup_group_size
                ):
                    # 本行主键未漂移且已有编辑证据：该行就是本备份行
                    # 的存活版本，不应把编辑前的旧值再插一份
                    if (
                        raw_id in candidates
                        and _same_backup_origin()
                        and _candidate_edited(raw_id)
                        and not _content_prefers_sibling(raw_id)
                    ):
                        return _claim(raw_id)
                    if (
                        is_group_last
                        and len(candidates) == 1
                        and _candidate_content_matches_backup(
                            min(candidates)
                        )
                        and (
                            raw_id > 0
                            or (
                                soft_where is not None
                                and _soft_matches(min(candidates))
                            )
                        )
                        # 内容与同秒孪生完全相同、尾字段却对不上本行
                        # （备注也被编辑）：候选可能是另一条被删记录
                        # 的存活版本，认领它会让真正被删的孪生永久
                        # 丢失，必须先按被删记录补回；尾字段明显更像
                        # 本行（本地完成/停药编辑）时仍正常认领
                        and (
                            not _backup_content_shared_with_sibling()
                            or _tail_matches(min(candidates))
                            or not _tail_tied_with_sibling(
                                min(candidates)
                            )
                        )
                        and (
                            soft_where is None
                            or _soft_matches(min(candidates))
                            or _candidate_edited(min(candidates))
                        )
                    ):
                        sole = min(candidates)
                        if (
                            not _content_owned_by_other(sole)
                            and not _candidate_foreign_backup_row(sole)
                            and not (
                                _tail_prefers_sibling(sole)
                                and not _tail_matches(sole)
                            )
                            and not (
                                not _tail_matches(sole)
                                and _tail_prefers_sibling_any_content(sole)
                            )
                            and not (
                                not _tail_matches(sole)
                                and (
                                    _candidate_soft_owned_by_other(
                                        sole, cross_profile=True
                                    )
                                    or _tail_prefers_sibling(
                                        sole, cross_profile=True
                                    )
                                )
                            )
                        ):
                            return _claim(sole, mark_moved=True)
                    # 同身份组内多条备份行共享同一锚点且本地有缺行：
                    # 只补插缺失条数，补足后剩余备份行认领存活行，
                    # 避免 2 存活 + 1 缺失却插入 2 条
                    missing = max(
                        backup_group_size - local_group_size, 0
                    )
                    anchor_full = False
                    if fallback_where is not None:
                        anchor_fragment, anchor_extra = fallback_where
                        try:
                            anchor_count_row = self._conn.execute(
                                f"SELECT COUNT(*) AS n FROM {table}"
                                f" WHERE {where} AND {anchor_fragment}",
                                (*values, *anchor_extra),
                            ).fetchone()
                        except sqlite3.Error:
                            anchor_count_row = None
                        if anchor_count_row is not None:
                            anchor_local = int(
                                anchor_count_row["n"] or 0
                            )
                            anchor_full = (
                                anchor_local
                                >= int(backup_anchor_size or 1)
                            )
                    inserted_key = (
                        table, "__anchor_inserted__", identity,
                        scope_profile
                    )
                    inserted = int(state.get(inserted_key, 0))
                    if (
                        raw_id <= 0
                        and soft_where is not None
                        and backup_soft_size == 1
                    ):
                        edited_soft = [
                            candidate for candidate in claimed
                            if candidate not in moved
                            and candidate not in matched
                            and _soft_matches(candidate)
                            and _candidate_edited(candidate)
                            and not _candidate_soft_owned_by_other(
                                candidate, cross_profile=True,
                                require_content=True,
                            )
                        ]
                        if len(edited_soft) == 1:
                            # 无 id 备份行：软字段唯一命中的被编辑存活行
                            # 就是本行版本，不能因预算缺行补插旧副本
                            return _claim(
                                edited_soft[0], mark_moved=True
                            )
                    if inserted < missing and not anchor_full:
                        # 该锚点本地行数已够：预算不能被同组另一锚点
                        # 的缺行消耗后为已齐全的锚点再插一条重复读数
                        # （补插计数由 _ambiguous 统一维护）
                        return _ambiguous()
                    sole = min(candidates)
                    if (
                        _candidate_content_matches_backup(sole)
                        and not _content_owned_by_other(sole)
                        and not _content_prefers_sibling(sole)
                        and not (
                            not _tail_matches(sole)
                            and _tail_prefers_sibling_any_content(sole)
                        )
                        and not (
                            not _tail_matches(sole)
                            and (
                                _candidate_soft_owned_by_other(
                                    sole, cross_profile=True
                                )
                                or _tail_prefers_sibling(
                                    sole, cross_profile=True
                                )
                            )
                        )
                        and (
                            not _backup_content_shared_with_sibling()
                            or _tail_matches(sole)
                            or not _tail_tied_with_sibling(sole)
                        )
                        and not (
                            _tail_prefers_sibling(sole)
                            and not _tail_matches(sole)
                        )
                        and (
                            soft_where is None
                            or _soft_matches(sole)
                            or _candidate_edited(sole)
                        )
                    ):
                        return _claim(sole, mark_moved=True)
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
                    sole = min(candidates) if candidates else 0
                    tail_ok = bool(sole) and (
                        tail_where is None
                        or _tail_matches(sole)
                        or (
                            not _tail_prefers_sibling(sole)
                            # 兄弟内容被本地改成与本行相同时仍要比较
                            # 尾字段：候选明显属于兄弟行时不能认领
                            and not _tail_prefers_sibling_any_content(sole)
                        )
                    )
                    if (
                        len(candidates) == 1
                        and not _content_owned_by_other(sole)
                        # 候选原始主键命中另一成员的备份行：这是改挂
                        # 过来的存活版本，本行按被删记录补插
                        and not _candidate_foreign_backup_row(sole)
                        # 同内容孪生：候选尾字段精确命中同组另一条备份
                        # 行且不命中本行时，它是兄弟行的存活版本，本行
                        # 必须按原内容补插
                        and tail_ok
                        and (
                            backup_group_size <= 1
                            # 组内有缺行但候选只改了时间/数值：它是本行
                            # 被改期后的存活版本
                            or (
                                local_group_size < backup_group_size
                                and _candidate_diff_claimable(sole)
                                and not _candidate_diff_claimable_for_sibling(
                                    sole
                                )
                            )
                            # 组已满：重复导入时唯一被编辑的候选就是
                            # 本行的存活版本，不能补插旧版本
                            or (
                                local_group_size >= backup_group_size
                                and _candidate_edited(sole)
                                and _candidate_diff_claimable(sole)
                                and not _candidate_diff_claimable_for_sibling(
                                    sole
                                )
                            )
                        )
                        and (
                            _same_backup_origin()
                            or _candidate_edited(sole)
                        )
                    ):
                        return _claim(sole)
                    if (
                        len(candidates) > 1
                        and local_group_size >= backup_group_size
                        and raw_id > 0
                    ):
                        # 组内没有缺行、多条存活行都被编辑过：按内容与
                        # 尾字段相似度给本行挑唯一最像的候选，不能直接
                        # 补插编辑前旧值（会复活一条并不存在的旧读数）
                        scored = sorted(
                            (
                                (
                                    _content_score(candidate, raw_id),
                                    _tail_match_score(candidate, raw_id),
                                    -candidate,
                                )
                                for candidate in candidates
                            ),
                            reverse=True,
                        )
                        if scored[0][:2] > scored[1][:2]:
                            winner = -scored[0][2]
                            # 评分只是线索：胜者必须有本地编辑证据且
                            # 差异仅落在可编辑列，内容不能属于另一条
                            # 备份记录；否则按被删记录补插，不能把
                            # 另一来源未编辑的同秒行吞成本行版本
                            if (
                                _candidate_edited(winner)
                                and _candidate_diff_claimable(winner)
                                and not _content_owned_by_other(winner)
                            ):
                                return _claim(winner)
                    return _ambiguous()
        if not candidates:
            twin = _cross_profile_twin_candidate()
            if twin is not None:
                return _claim(twin, mark_moved=True)
            state[identity] = claimed
            return None
        if soft_where is not None and candidates:
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
            if len(soft) > 1:
                tail_exact = {
                    candidate for candidate in soft
                    if _tail_matches(candidate)
                }
                if len(tail_exact) == 1:
                    # 本地改名的存活行尾字段仍与当前备份行精确一致；
                    # 上一次恢复补插的旧副本尾字段已不符，不能让它
                    # 改变本次导入的分支（第八十三轮幂等）
                    soft = tail_exact
            if soft:
                # 软字段命中：备份组只有一行，或该软字段在备份组内
                # 唯一时才能认领；同秒同名孪生仍不可区分。本地存活
                # 行数已不少于备份行数时，说明每条备份行都有存活版本
                # （只是被编辑过），应继续认领而不是补回旧版本
                if (
                    backup_group_size <= 1
                    or backup_soft_size == 1
                    or local_group_size >= backup_group_size
                    # 唯一软字段命中且尾字段与本行一致：内容列被本地
                    # 编辑过的真孪生（当前行内容与兄弟备份行相同），
                    # 它就是本行存活版本，不能因为组未满就补插旧版本；
                    # 内容不同的同秒记录（不同剂量）仍各自补回
                    or (
                        len(soft) == 1
                        and _backup_content_shared_with_sibling()
                        and _tail_matches(min(soft))
                        # 候选内容差异必须可归属本行：时间/数值列，
                        # 或（第九十一轮 [10]）仅剂量等 PATCH 可编辑
                        # 内容列、尾字段精确命中且不是兄弟行
                        and (
                            _candidate_diff_claimable(min(soft))
                            or (
                                not _candidate_diff_hits_identity(
                                    min(soft)
                                )
                                and not _candidate_diff_claimable_for_sibling(
                                    min(soft)
                                )
                            )
                        )
                    )
                    # 无 id 备份行：候选内容与本行完全一致且该内容
                    # 不与兄弟备份行共享时，它就是本行的存活行；
                    # 组内缺行是被删/被改挂的其它记录，不该阻止
                    # 按内容配对（第九十九轮 [8]）
                    or (
                        raw_id <= 0
                        and len(soft) == 1
                        and not _backup_content_shared_with_sibling()
                        and _candidate_content_matches_backup(min(soft))
                    )
                ):
                    candidates = soft
                elif (
                    is_group_last
                    and len(soft) == 1
                    and (
                        _candidate_content_matches_backup(min(soft))
                        # 本地比备份少一行且只剩这一条软字段命中，
                        # 且当前行与兄弟内容完全相同（真孪生）：
                        # 它就是被编辑过内容列的存活行，旧版本不补插
                        or (
                            local_group_size == backup_group_size - 1
                            and _backup_content_shared_with_sibling()
                        )
                    )
                ):
                    # 组内最后一条备份行：剩余唯一软字段命中的候选
                    candidates = soft
                else:
                    # 组内补插预算已用尽（被删记录已补回）：剩余唯一
                    # 软字段命中且确被本地编辑过的候选就是本行存活版本，
                    # 不能再次补插编辑前旧副本
                    soft_inserted = int(state.get(
                        (table, "__anchor_inserted__", identity,
                         scope_profile), 0
                    ))
                    soft_missing = max(
                        backup_group_size - local_group_size, 0
                    )
                    soft_sole = min(soft) if len(soft) == 1 else 0
                    if (
                        soft_sole
                        and soft_inserted >= soft_missing
                        and _candidate_edited(soft_sole)
                        and _candidate_diff_claimable(soft_sole)
                        and not _content_owned_by_other(soft_sole)
                    ):
                        candidates = soft
                    else:
                        return _ambiguous()
            else:
                # 无 id 备份行没有内容元数据可用：即使内容列被编辑、
                # 候选不在内容配对集合里，软字段在组内唯一命中且被
                # 编辑过的存活行仍是本行版本（第八十六轮）
                soft_pool_found = False
                if raw_id <= 0 and backup_soft_size == 1:
                    claimed_pool = [
                        candidate for candidate in claimed
                        if candidate not in moved
                        and candidate not in matched
                        and _soft_matches(candidate)
                    ]
                    if len(claimed_pool) == 1:
                        candidates = {claimed_pool[0]}
                        ordered = sorted(candidates)
                        placeholders = ",".join(
                            "?" for _ in ordered
                        )
                        soft_pool_found = True
                # 备份组内存在多行同身份记录且本地确有缺行时，唯一
                # 候选可能是被删记录的孪生兄弟；本地全部存活（只是
                # 被改名/改标题）时仍按重复导入幂等处理。
                # 缺行判定要扣掉本次导入已补插的行：被删记录补回后，
                # 剩余备份行对应的是改名存活行，不能把旧版本再插一条
                # （第七十八轮）
                if (
                    not soft_pool_found
                    and backup_group_size > 1
                    and _group_rows_missing()
                ):
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
                        # 备份里的另一条记录。组内已无缺行（本地行数
                        # 够）时不能补插旧版本：内容列被编辑的候选
                        # 才是本行存活版本（它已不在漂移候选中）
                        if local_group_size >= backup_group_size:
                            edited = [
                                candidate for candidate in candidates
                                if _candidate_edited(candidate)
                                and not _soft_matches(candidate)
                            ]
                            tail_edited = [
                                candidate for candidate in edited
                                if _tail_matches(candidate)
                            ]
                            if tail_edited:
                                edited = tail_edited
                            if not edited:
                                return _ambiguous()
                            candidates = {min(edited)}
                        else:
                            return _ambiguous()
                    else:
                        candidates = fresh
                # 组内只剩唯一候选：软字段已被本地编辑（改名/改标题），
                # 它仍是这条备份行的存活版本，不能把旧版本再插一份
        if len(candidates) > 1:
            # 多候选里只保留对本行有证据的版本（软字段命中或确实被
            # 编辑过）。尾字段命中单独不算证据：同秒但标题不同的
            # 跨来源提醒不能被当成本行的存活版本吞掉
            preferred = {
                candidate for candidate in candidates
                if _soft_matches(candidate)
                or _candidate_edited(candidate)
            }
            if not preferred:
                return _ambiguous()
            tail_preferred = {
                candidate for candidate in preferred
                if _tail_matches(candidate)
            }
            if tail_preferred:
                # 多个软字段命中的同内容孪生：尾字段（停药/完成等）
                # 精确命中的那条才是本行的存活版本
                preferred = tail_preferred
            non_sibling = {
                candidate for candidate in preferred
                if not (
                    _tail_prefers_sibling(candidate)
                    and not _tail_matches(candidate)
                )
            }
            if non_sibling:
                preferred = non_sibling
            else:
                # 全部候选的尾字段都更像未处理的兄弟备份行：被改名的
                # 本行存活版本不在软字段候选里，先找编辑孤儿，否则
                # 按歧义补插，绝不 min() 任意认领兄弟行
                orphan = _orphan_edited_candidate()
                if orphan is not None:
                    return _claim(orphan, mark_moved=True)
                return _ambiguous()
            candidates = preferred
        if len(candidates) == 1:
            candidate = min(candidates)
            soft_owned_same = _candidate_soft_owned_by_other(candidate)
            if (
                _candidate_edited(candidate)
                and not _soft_matches(candidate)
                and (
                    soft_owned_same
                    # 跨档案命中兄弟行的软字段且尾字段也不符：候选更
                    # 可能是那条记录的改挂版本（如改挂后另一成员删除
                    # 同秒孪生），按被删记录补插；尾字段仍命中本行的
                    # 同档案改名（另一种药改成同名）继续正常认领
                    or (
                        not soft_owned_same
                        and tail_where is not None
                        and not _tail_matches(candidate)
                        and _candidate_soft_owned_by_other(
                            candidate, cross_profile=True
                        )
                    )
                    # 内容列已不属于本行、软字段又命中另一成员的备份
                    # 行：即使尾字段恰好与本行相同（note/状态为空），
                    # 也不能认领改挂行，被删记录必须补回
                    or (
                        not soft_owned_same
                        and _candidate_soft_owned_by_other(
                            candidate, cross_profile=True
                        )
                        and not _candidate_content_matches_backup(candidate)
                    )
                )
            ):
                return _ambiguous()
            if _content_owned_by_other(candidate):
                # 候选内容精确对应备份里的另一条记录：按被删记录补回
                return _ambiguous()
            if (
                content_where
                and not _candidate_content_matches_backup(candidate)
                and (
                    not _candidate_edited(candidate)
                    or (
                        _group_rows_missing()
                        and not _backup_content_shared_with_sibling()
                        and _candidate_diff_hits_identity(candidate)
                    )
                )
            ):
                # 内容列（如提醒到期日/症状时间）与备份行不同：没有
                # 编辑证据，或组内确有未补插的缺行且同秒记录内容不同
                # （编辑证据可能来自另一条记录）时，本行必须按被删补插
                return _ambiguous()
            soft_full = False
            if (
                local_group_size >= backup_group_size
                and soft_where is not None
            ):
                soft_fragment, soft_extra = soft_where
                soft_count_row = self._conn.execute(
                    f"SELECT COUNT(*) AS n FROM {table}"
                    f" WHERE {where} AND {soft_fragment}",
                    (*values, *soft_extra),
                ).fetchone()
                soft_full = int(
                    soft_count_row["n"] if soft_count_row else 0
                ) >= int(backup_soft_size or 1)
            if (
                not soft_full
                and not _tail_matches(candidate)
                and _tail_prefers_sibling_any_content(candidate)
            ):
                # 内容列被本地编辑成与本行相同时，尾字段仍可能明确
                # 属于另一条同秒记录（不同剂量/症状的孪生）；组内
                # 已无缺行时每条备份行都有存活版本，尾字段偏好只会
                # 制造重复（已停用计划复活/已完成提醒变待办）
                return _ambiguous()
            if (not soft_full) and (
                not _tail_matches(candidate)
            ) and (
                _tail_prefers_sibling(candidate)
                or (
                    not _soft_matches(candidate)
                    and _tail_prefers_sibling(candidate, cross_profile=True)
                )
            ):
                # 候选的非内容字段更像另一条同内容孪生备份行：交给
                # 那条记录认领，本行按被删记录补插（否则「恢复的已
                # 完成孪生被在用孪生吞掉」或反方向都可能发生）。
                # 跨档案维度仅在软字段也不命中本行时生效：标题命中
                # 说明候选是本行被编辑/完成后的存活版本
                return _ambiguous()
            if (
                local_group_size >= backup_group_size
                and tail_where is not None
                and not _candidate_edited(candidate)
            ):
                edited_tail = [
                    other for other in claimed
                    if other not in moved and other not in matched
                    and other != candidate
                    and _candidate_edited(other)
                    and _tail_matches(other)
                    and not _content_owned_by_other(other)
                    and not _candidate_soft_owned_by_other(
                        other, cross_profile=True, require_content=True
                    )
                ]
                if len(edited_tail) == 1:
                    # 组内已无缺行：本行的内容列（剂量/时间等）被
                    # 本地编辑过，被编辑存活行的尾字段仍命中本行，
                    # 它就是本行版本，不能补插编辑前旧副本
                    # （第九十轮 [8]）
                    return _claim(edited_tail[0], mark_moved=True)
            if (
                soft_where is not None
                and not _soft_matches(candidate)
                and not _candidate_edited(candidate)
            ):
                # 标题/名称对不上、又缺少编辑证据的同秒新建记录不是
                # 本行的存活版本：按被删记录补回，避免误认领后把
                # owner/复诊链接写到另一条记录上
                return _ambiguous()
            if not _same_backup_origin() and not _candidate_edited(
                candidate
            ):
                # 非同源且看不到任何编辑证据：不能凭撞号认领遗留行
                return _ambiguous()
        if len(candidates) > 1:
            filtered_multi = {
                candidate for candidate in candidates
                if not _content_owned_by_other(candidate)
                and not _candidate_soft_owned_by_other(
                    candidate, cross_profile=True
                )
            }
            if len(filtered_multi) == 1:
                candidates = filtered_multi
            elif local_group_size >= backup_group_size:
                # 组内已无缺行：所有备份行都应各认领一条存活行，
                # 不能再补插副本（重复导入保持幂等；第八十八轮 [8]）
                candidates = filtered_multi
            else:
                # 多条候选互相归属不明：不能 min() 任意认领，按被删
                # 记录补插（「宁重复勿丢」）
                return _ambiguous()
        if not candidates:
            if local_group_size >= backup_group_size and soft_where is not None:
                # 组内已无缺行：本行应认领组内仍未被占用、软字段
                # 命中的存活行（内容被编辑过），不能补插副本
                # （重复导入保持幂等；第八十八轮 [8]）
                pool = [
                    candidate for candidate in claimed
                    if candidate not in moved
                    and candidate not in matched
                    and _soft_matches(candidate)
                ]
                if pool:
                    return _claim(min(pool), mark_moved=True)
            return _ambiguous()
        sole = min(candidates)
        if _candidate_foreign_backup_row(sole):
            return _ambiguous()
        if (
            _candidate_owns_unprocessed_backup_row(sole)
            and not _candidate_content_matches_backup(sole)
        ):
            # 候选主键对应另一条尚未处理的备份行（改挂/改名后的存活
            # 版本）：本行按被删记录补插，把候选留给那条记录
            return _ambiguous()
        if (
            _content_rows_missing_for_orphan()
            and not _candidate_content_matches_backup(sole)
            and _candidate_foreign_backup_row(sole)
        ):
            # 组内仍有备份内容缺失、候选内容不属于本行，且候选原始
            # 主键指向另一成员的备份行：它是改挂过来的存活版本，
            # 本行按被删记录补插（宁重复勿丢）。同档案的改名/改剂量
            # 仍按本行存活版本认领。
            return _ambiguous()
        if (
            soft_where is not None
            and not _soft_matches(sole)
            and _candidate_soft_owned_by_other(
                sole, cross_profile=True, require_content=True
            )
        ):
            # 候选软字段（标题）对不上本行、却精确等于同秒另一条
            # 备份记录：它是那条记录改名/改挂后的存活版本，本行按
            # 被删记录补插，不能抢走兄弟行（第八十三轮 [4]）
            return _ambiguous()
        if (
            raw_id <= 0
            and (backup_group_size > 1 or not _candidate_edited(sole))
            and tail_where is not None
            and not _tail_matches(sole)
        ):
            # 无 id 备份行：候选尾字段对不上本行时无法确认归属，
            # 按被删记录补插（宁重复勿丢），避免被删记录被另一条
            # 记录的存活行顶替；没有尾字段的表（指标）不受影响
            # （第八十七轮、第九十六轮 [2][3]）
            return _ambiguous()
        if (
            soft_where is not None
            and raw_id <= 0
            and not _soft_matches(sole)
        ):
            # 无 id 备份行无法用内容确认归属：软字段对不上本行时按
            # 被删记录补插，不能把同秒另一条记录认领给本行
            # （第八十六轮）
            return _ambiguous()
        if (
            _candidate_soft_owned_by_other(sole, cross_profile=True)
            and (
                _tail_prefers_sibling(sole, cross_profile=True)
                # 被改挂到兄弟档案且内容（到期日等）同时被编辑时，
                # 同内容孪生比较不适用：直接按尾字段（备注等）跨
                # 档案比较，否则被删记录被兄弟行顶替永久丢失
                # （第九十四轮 [4]）
                or _tail_prefers_sibling_any_content(
                    sole, cross_profile=True
                )
            )
            # 被偏向的兄弟备份行都另有本地存活行时，候选是本行
            # 被编辑（停药/完成状态改成与兄弟相同）的存活版本，
            # 不能按被删记录补插（第一百零一轮 [4]）
            and not _tail_preferred_survive(sole)
            and not _tail_preferred_any_survive(
                sole, cross_profile=True
            )
        ):
            # 候选软字段命中同秒另一条备份行、尾字段也更像那条行
            # （被改挂到兄弟档案并编辑内容）：本行必须按被删记录
            # 补插，否则被删记录永久丢失、旧副本在原档案复活
            # （第八十六轮）
            return _ambiguous()
        if (
            sole == raw_id
            and not _same_backup_origin()
            and not _tail_matches(sole)
        ):
            # 漂移库中本地编号恰等于备份主键只是巧合：尾字段对不上
            # 时按被删记录补插（第八十七轮）
            return _ambiguous()
        if (
            backup_group_size > 1
            and content_where
            and not _candidate_content_matches_backup(sole)
            and _candidate_edited(sole)
            and _sibling_content_missing_for_orphan()
            and not _tail_uniquely_matches(sole)
            # 改名/改标题（身份列）按软字段归属认领；时间/数值列
            # 是同一记录的常规编辑（改期/改剂量后补正）——两类都
            # 不按被删记录补插
            and not _candidate_diff_hits_identity(sole)
            and not _candidate_diff_claimable(sole)
        ):
            # 候选内容与本行备份内容不同且被编辑过、组内仍有备份
            # 内容在本地缺失，尾字段也无法唯一归属本行：候选更可能
            # 是被删孪生兄弟的存活版本，本行必须按被删记录补插
            # （宁重复勿丢）；尾字段精确且唯一命中本行时仍按本行
            # 存活版本认领（第九十九轮 [4]）
            return _ambiguous()
        return _claim(sole)

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
        item: Dict[str, Any], columns: tuple, table: str = ""
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
            from_null_default = value is None
            if value is None:
                value = default
            if value is None:
                values.append("")
            elif isinstance(value, (int, float)) and not isinstance(
                value, bool
            ):
                values.append(f"{float(value):.10g}")
            else:
                text = " ".join(str(value).split())
                if table == "health_metrics" and name == "context":
                    # 与入库 _normalize_metric 的 context 归一化口径
                    # 一致（小写、无允许情境的指标置空），否则同一份
                    # 外部备份重复导入会复制读数（第九十九轮 [14]）
                    text = text.strip().lower()
                    metric_name = str(item.get("metric") or "").strip()
                    allowed = METRIC_CONTEXTS.get(metric_name, ())
                    if not allowed or (text and text not in allowed):
                        text = ""
                if from_null_default:
                    # IFNULL 默认值是字符串形式的数字（如 -1e308）：
                    # 只有它需要与 DB 侧 float 格式化口径统一；
                    # TEXT 列的自由文本（dose/note 等）保持原样
                    try:
                        text = f"{float(text):.10g}"
                    except ValueError:
                        pass
                canonical = _canonical_local_time(text)
                if canonical:
                    # 纯日期/时间串与入库规范化口径统一：否则同源
                    # 判定会被日期格式差异误伤（第九十七轮 [10]）
                    text = canonical
                values.append(text)
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
                text = " ".join(str(value).split())
                canonical = _canonical_local_time(text)
                if canonical:
                    text = canonical
                values.append(text)
        return tuple(values)

    @staticmethod
    def _tail_value_key(value: Any) -> str:
        """备份尾字段值的归一化：与 DB 侧内容指纹同口径（布尔→0/1）。"""
        if value is None:
            return ""
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return f"{float(value):.10g}"
        key = " ".join(str(value).split())
        canonical = _canonical_local_time(key)
        if canonical:
            # 与 DB 侧内容指纹的日期规范化口径一致：否则纯日期
            # 尾字段会让同源/尾字段比较误判（第九十七轮 [10]）
            return canonical
        return key

    @classmethod
    def _import_backup_meta(
        cls, raw_items: Any, limit: int, table: str = ""
    ) -> Dict[str, Any]:
        """备份原始主键 → （档案、创建时间、内容指纹、尾字段）映射。"""
        ids = set()
        profiles: Dict[int, str] = {}
        created: Dict[int, str] = {}
        contents: Dict[int, tuple] = {}
        tails: Dict[int, tuple] = {}
        columns = IMPORT_CONTENT_COLUMNS.get(table)
        tail_columns = IMPORT_TAIL_COLUMNS.get(table)
        soft_columns = IMPORT_SOFT_COLUMNS.get(table)
        softs: Dict[int, tuple] = {}
        if not isinstance(raw_items, list):
            return {
                "ids": ids, "profiles": profiles, "created": created,
                "contents": contents, "tails": tails, "softs": softs,
            }
        synth = 0
        for item in raw_items[:limit]:
            if not isinstance(item, dict):
                continue
            raw_id = _safe_db_id(item.get("id"))
            if raw_id <= 0:
                # 无 id 行（CSV/第三方导出）分配合成负键：只用于
                # 尾字段/内容的跨行归属比较，不代表任何可认领的
                # 本地主键（第九十六轮 [2][3]）
                synth -= 1
                raw_id = synth
            ids.add(raw_id)
            profiles[raw_id] = _normalize_profile_name(
                item.get("profile")
            )
            created[raw_id] = str(
                item.get("created_at") or ""
            ).strip()[:40]
            if columns:
                contents[raw_id] = cls._backup_content_fingerprint(
                    item, columns, table
                )
            if tail_columns:
                tails[raw_id] = tuple(
                    cls._tail_value_key(item.get(column))
                    for column in tail_columns
                )
            if soft_columns:
                softs[raw_id] = cls._backup_content_fingerprint(
                    item, soft_columns, table
                )
        return {
            "ids": ids, "profiles": profiles, "created": created,
            "contents": contents, "tails": tails, "softs": softs,
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

    def _remap_import_aliases(
        self, data: Dict[str, Any], exported_at: str = ""
    ) -> Dict[str, Any]:
        """恢复备份时把已改名档案的旧名称解析到当前名称。

        只跟随快照导出之后发生的改名：快照内已体现的改名不能再改写
        快照里仍存活的同名档案（可能是改名后重建的另一位成员）。
        """
        profile_fields = (
            "sessions", "metric_targets", "health_metrics", "symptoms",
            "medications", "medication_logs", "medication_history",
            "vaccinations", "appointments", "lab_results", "allergies",
            "care_items", "visit_records", "conditions", "reminders",
            "usage_events",
        )
        cache: Dict[str, str] = {}
        live_created: Dict[str, str] = {}
        raw_profiles = data.get("profiles")
        if isinstance(raw_profiles, list):
            for item in raw_profiles:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                profile_name = _normalize_profile_name(item.get("name"))
                created = str(item.get("created_at") or "")[:40]
                if profile_name and created > live_created.get(
                    profile_name, ""
                ):
                    live_created[profile_name] = created

        def mapped(value: Any) -> str:
            name = str(value or "")
            if not name:
                return name
            if name not in cache:
                try:
                    resolved = self.resolve_profile_alias(
                        name, after=exported_at
                    )
                    if (
                        exported_at
                        and resolved != name
                        and live_created.get(
                            _normalize_profile_name(name), ""
                        ) >= exported_at
                    ):
                        # 快照内该旧名仍由一位新成员存活（改名后重建）：
                        # 改名记录已体现在快照里，不能把新成员并过去
                        resolved = name
                    cache[name] = resolved or name
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

        # 备份携带的档案改名映射：只有「快照导出之后」发生的改名才能
        # 用于解析旧备份里的档案名。快照内已经体现的改名，其旧名字
        # 可能已被新主人复用（改名后又重建同名档案），不能再把快照
        # 里存活的同名档案改写到改名目标名下
        # 备份自带的 profile_renames 不写入本机改名历史（第七十九轮）：
        # 该表的记录会驱动 resolve_profile_alias，任何可伪造的映射都
        # 能把后续恢复/会话绑定串到其它成员名下（校验和也不覆盖它）。
        # 恢复改名前的备份时只跟随本机 rename_profile() 产生的真实
        # 历史，并且只跟随快照导出之后发生的改名（第八十轮）。
        backup_exported_at = str(data.get("exported_at") or "").strip()[:40]
        data = self._remap_import_aliases(data, backup_exported_at)

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
        # 非 dict 条目会被 _import_rows 过滤：在这里统一计入 invalid，
        # 否则畸形备份会被当成完整恢复（sessions 已在自身循环计数）
        for list_key in (
            "profiles", "health_metrics", "metric_targets", "symptoms",
            "medications", "medication_logs", "medication_history",
            "vaccinations", "appointments", "lab_results", "allergies",
            "care_items", "visit_records", "conditions", "reminders",
            "audit_log", "usage_events",
        ):
            items = data.get(list_key)
            if isinstance(items, list):
                invalid += sum(
                    1 for item in items[:IMPORT_LIST_LIMITS[list_key]]
                    if not isinstance(item, dict)
                )
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
                if (
                    session_id in (".", "..")
                    or "/" in session_id
                    or "\\" in session_id
                    or "%" in session_id
                    or any(ord(ch) < 32 for ch in session_id)
                ):
                    # 路径分隔符/百分号/控制字符让 /api/sessions/{id}
                    # 无法匹配（百分比编码的斜杠在 ASGI 层解码后变成
                    # 路径分隔符），导入的会话将无法打开、改名或逐条
                    # 删除；与档案导入的规则保持一致，直接判为非法
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
                raw_tags_value = raw.get("tags") or []
                tags = _normalize_tags(raw_tags_value)
                if (
                    DELETED_PROFILE_SESSION_TAG in raw_tags_value
                    and DELETED_PROFILE_SESSION_TAG not in tags
                ):
                    tags.append(DELETED_PROFILE_SESSION_TAG)
                try:
                    profile = _normalize_profile_name(
                        raw.get("profile"), strict=True
                    )
                except ValueError:
                    invalid += 1
                    continue

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
                    try:
                        # 与写入边界共用 strict 规则：路径分隔符会让
                        # /api/profiles/{name} 无法匹配，零宽字符还会让
                        # 该档案之后任何记录都写不进去（第七十三轮审查）
                        name = _normalize_profile_name(
                            raw.get("name"), strict=True
                        )
                    except ValueError:
                        invalid += 1
                        continue
                    if not name:
                        invalid += 1
                        continue
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
                # 组大小只统计能通过校验的行：永远无法导入的非法行
                # 不能把合法行误判成「本地缺行」而补插旧值
                metric_valid_rows = []
                for raw_item in raw_metrics[
                    : IMPORT_LIST_LIMITS["health_metrics"]
                ]:
                    if not isinstance(raw_item, dict):
                        continue
                    if not str(raw_item.get("measured_at") or "").strip():
                        # 空测量时间永远无法导入：不能抬高身份组大小
                        continue
                    try:
                        normalized_item = _normalize_metric(
                            raw_item.get("profile", ""),
                            raw_item.get("metric", ""),
                            raw_item.get("value1"),
                            raw_item.get("value2"),
                            raw_item.get("measured_at", ""),
                            raw_item.get("note", ""),
                            raw_item.get("context", ""),
                        )
                    except Exception:
                        normalized_item = None
                    if normalized_item is not None:
                        metric_valid_rows.append(raw_item)
                metric_group_sizes = self._identity_group_sizes(
                    metric_valid_rows,
                    IMPORT_LIST_LIMITS["health_metrics"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("metric") or "").strip(),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                metric_anchor_sizes = self._identity_group_sizes(
                    metric_valid_rows,
                    IMPORT_LIST_LIMITS["health_metrics"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("metric") or "").strip(),
                        str(item.get("created_at") or "").strip()[:40],
                        str(
                            _canonical_local_time(item.get("measured_at"))
                            or ""
                        ),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                metric_backup_meta = self._import_backup_meta(
                    metric_valid_rows, IMPORT_LIST_LIMITS["health_metrics"],
                    "health_metrics",
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
                    if not str(raw.get("measured_at") or "").strip():
                        # 空测量时间会被填成每次导入的当前时刻，
                        # 跨分钟重复导入同一备份产生重复读数：
                        # 与症状 CSV 对 recorded_at 的处理一致，判为非法
                        invalid += 1
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=metric_backup_meta["ids"],
                            backup_profiles=metric_backup_meta["profiles"],
                            backup_created=metric_backup_meta["created"],
                            backup_contents=metric_backup_meta["contents"],
                            backup_tails=metric_backup_meta["tails"],
                            backup_softs=metric_backup_meta["softs"],
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
                    count_gate = True
                    if created_key:
                        same_created = self._conn.execute(
                            "SELECT 1 FROM health_metrics"
                            " WHERE profile = ? AND metric = ?"
                            " AND measured_at = ? AND value1 = ?"
                            " AND IFNULL(value2, -1) = IFNULL(?, -1)"
                            " AND note = ? AND context = ?"
                            " AND created_at = ? LIMIT 1",
                            (
                                normalized["profile"],
                                normalized["metric"],
                                normalized["measured_at"],
                                normalized["value1"],
                                normalized["value2"],
                                normalized["note"],
                                normalized["context"],
                                created_key,
                            ),
                        ).fetchone()
                        if same_created is not None:
                            # 同源同秒的存活读数：孪生补插由身份匹配层
                            # 负责，不能按内容数量跳过
                            count_gate = False
                    if count_gate:
                        # 内容计数兜底：跨来源恢复（创建时间不同）按
                        # 内容数量判重，同一条读数不重复插入
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
                    try:
                        profile = _normalize_profile_name(
                            raw.get("profile"), strict=True
                        )
                    except ValueError:
                        invalid += 1
                        continue
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
                symptom_valid_rows = []
                for raw_item in raw_symptoms[
                    : IMPORT_LIST_LIMITS["symptoms"]
                ]:
                    if not isinstance(raw_item, dict):
                        continue
                    if not str(raw_item.get("recorded_at") or "").strip():
                        continue
                    try:
                        normalized_item = _normalize_symptom(
                            raw_item.get("profile", ""),
                            raw_item.get("symptom", ""),
                            raw_item.get("severity"),
                            raw_item.get("recorded_at", ""),
                            raw_item.get("note", ""),
                        )
                    except Exception:
                        normalized_item = None
                    if normalized_item is not None:
                        symptom_valid_rows.append(raw_item)
                symptom_group_sizes = self._identity_group_sizes(
                    symptom_valid_rows,
                    IMPORT_LIST_LIMITS["symptoms"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                symptom_anchor_sizes = self._identity_group_sizes(
                    symptom_valid_rows,
                    IMPORT_LIST_LIMITS["symptoms"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        str(
                            _canonical_local_time(item.get("recorded_at"))
                            or ""
                        ),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                symptom_backup_meta = self._import_backup_meta(
                    symptom_valid_rows,
                    IMPORT_LIST_LIMITS["symptoms"], "symptoms",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=symptom_backup_meta["ids"],
                            backup_profiles=symptom_backup_meta["profiles"],
                            backup_created=symptom_backup_meta["created"],
                            backup_contents=symptom_backup_meta["contents"],
                            backup_tails=symptom_backup_meta["tails"],
                            backup_softs=symptom_backup_meta["softs"],
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
                            tail_where=(
                                "severity = ? AND COALESCE(note, '') = ?",
                                (
                                    normalized["severity"],
                                    normalized["note"],
                                ),
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
                medication_valid_rows = _import_valid_rows(
                    raw_medications,
                    IMPORT_LIST_LIMITS["medications"],
                    lambda item: _normalize_medication(
                        item.get("profile", ""),
                        item.get("name", ""),
                        item.get("dose", ""),
                        item.get("frequency", ""),
                        item.get("note", ""),
                        item.get("active", True),
                        item.get("stock"),
                        item.get("stock_unit", ""),
                        item.get("low_stock_threshold", 0),
                        item.get("expiry_date", ""),
                        item.get("started_at", ""),
                        item.get("stopped_at", ""),
                        item.get("stop_reason", ""),
                    ),
                )
                medication_group_sizes = self._identity_group_sizes(
                    medication_valid_rows,
                    IMPORT_LIST_LIMITS["medications"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                medication_soft_sizes = self._identity_group_sizes(
                    medication_valid_rows,
                    IMPORT_LIST_LIMITS["medications"],
                    lambda item: (
                        _normalize_profile_name(item.get("profile")),
                        str(item.get("created_at") or "").strip()[:40],
                        " ".join(str(item.get("name") or "").split()),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                medication_backup_meta = self._import_backup_meta(
                    medication_valid_rows,
                    IMPORT_LIST_LIMITS["medications"], "medications",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=medication_backup_meta["ids"],
                            backup_profiles=medication_backup_meta["profiles"],
                            backup_created=medication_backup_meta["created"],
                            backup_contents=medication_backup_meta["contents"],
                            backup_tails=medication_backup_meta["tails"],
                            backup_softs=medication_backup_meta["softs"],
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
                            tail_where=(
                                "COALESCE(note, '') = ? AND active = ?"
                                " AND COALESCE(stopped_at, '') = ?"
                                " AND stop_reason = ?"
                                " AND IFNULL(stock, -1) = IFNULL(?, -1)"
                                " AND COALESCE(stock_unit, '') = ?"
                                " AND COALESCE(low_stock_threshold, 0) = ?"
                                " AND COALESCE(expiry_date, '') = ?",
                                (
                                    normalized["note"],
                                    normalized["active"],
                                    normalized["stopped_at"],
                                    normalized["stop_reason"],
                                    normalized["stock"],
                                    normalized["stock_unit"],
                                    normalized["low_stock_threshold"],
                                    normalized["expiry_date"],
                                ),
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
                               AND COALESCE(note, '') = ?
                               AND COALESCE(started_at, '') = ?
                               AND COALESCE(stopped_at, '') = ?
                               AND stop_reason = ? AND active = ?
                               AND IFNULL(stock, -1) = IFNULL(?, -1)
                               AND COALESCE(stock_unit, '') = ?
                               AND COALESCE(low_stock_threshold, 0) = ?
                               AND COALESCE(expiry_date, '') = ?
                            """,
                            (
                                normalized["profile"], normalized["name"],
                                normalized["dose"], normalized["frequency"],
                                normalized["note"],
                                normalized["started_at"],
                                normalized["stopped_at"],
                                normalized["stop_reason"],
                                normalized["active"],
                                normalized["stock"],
                                normalized["stock_unit"],
                                normalized["low_stock_threshold"],
                                normalized["expiry_date"],
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
                                stop_reason, resumed_at, created_at,
                                updated_at
                            ) VALUES (
                                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                                ?
                            )
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
                                str(raw.get("resumed_at") or "")[:40],
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
                        # 目标库主键漂移时实际命中行可能属于另一档案
                        # （改挂）：打卡/历史必须写到真实行档案名下
                        actual_profile = normalized["profile"]
                        actual_name = normalized["name"]
                        actual_row = self._conn.execute(
                            "SELECT profile, name FROM medications"
                            " WHERE id = ?",
                            (actual_id,),
                        ).fetchone()
                        if actual_row is not None:
                            actual_profile = str(actual_row["profile"] or "")
                            actual_name = str(actual_row["name"] or "")
                        medication_id_map[raw_id] = {
                            "id": actual_id,
                            "profile": actual_profile,
                            "name": actual_name,
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
                        try:
                            profile = _normalize_profile_name(
                                raw.get("profile"), strict=True
                            )
                        except ValueError:
                            invalid += 1
                            continue
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
                vaccination_valid_rows = _import_valid_rows(
                    raw_vaccinations,
                    IMPORT_LIST_LIMITS["vaccinations"],
                    lambda item: _normalize_vaccination(
                        item.get("profile") or "",
                        item.get("vaccine_name") or item.get("name") or "",
                        item.get("dose_number") or 1,
                        item.get("vaccinated_at") or "",
                        item.get("next_due") or "",
                        item.get("note") or "",
                    ),
                )
                vaccination_valid_rows = [
                    item for item in vaccination_valid_rows
                    if str(item.get("vaccinated_at") or "").strip()
                ]
                (
                    vaccination_group_sizes,
                    vaccination_soft_sizes,
                ) = self._identity_group_meta(
                    vaccination_valid_rows,
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
                    vaccination_valid_rows,
                    IMPORT_LIST_LIMITS["vaccinations"], "vaccinations",
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
                    if not str(raw.get("vaccinated_at") or "").strip():
                        # 空接种时间会被填成导入时刻，重复导入逐次复制
                        invalid += 1
                        continue
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=vaccination_backup_meta["ids"],
                            backup_profiles=vaccination_backup_meta["profiles"],
                            backup_created=vaccination_backup_meta["created"],
                            backup_contents=vaccination_backup_meta["contents"],
                            backup_tails=vaccination_backup_meta["tails"],
                            backup_softs=vaccination_backup_meta["softs"],
                            soft_where=(
                                "vaccine_name = ?",
                                (normalized["vaccine_name"],),
                            ),
                            tail_where=(
                                "COALESCE(next_due, '') = ?"
                                " AND COALESCE(note, '') = ?",
                                (
                                    normalized["next_due"],
                                    normalized["note"],
                                ),
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
            #: 本地预约 id → 备份里的原始 owner_visit_id
            appointment_owner_raw: Dict[int, int] = {}
            #: 本地预约 id → 备份里的原始备注（用于判断本地是否已改写）
            appointment_raw_notes: Dict[int, str] = {}
            appointment_raw_status: Dict[int, str] = {}
            appointment_raw_scheduled: Dict[int, str] = {}
            appointment_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_appointments, list):
                appointment_pre_ids = self._existing_ids_locked(
                    "appointments"
                )
                appointment_valid_rows = _import_valid_rows(
                    raw_appointments,
                    IMPORT_LIST_LIMITS["appointments"],
                    lambda item: _normalize_appointment(
                        item.get("profile") or "",
                        item.get("title") or "",
                        item.get("department") or "",
                        item.get("doctor") or "",
                        item.get("location") or "",
                        item.get("scheduled_at") or "",
                        item.get("status") or "upcoming",
                        item.get("note") or "",
                    ),
                )
                (
                    appointment_group_sizes,
                    appointment_soft_sizes,
                ) = self._identity_group_meta(
                    appointment_valid_rows,
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
                    appointment_valid_rows,
                    IMPORT_LIST_LIMITS["appointments"], "appointments",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=appointment_backup_meta["ids"],
                            backup_profiles=appointment_backup_meta["profiles"],
                            backup_created=appointment_backup_meta["created"],
                            backup_contents=appointment_backup_meta["contents"],
                            backup_tails=appointment_backup_meta["tails"],
                            backup_softs=appointment_backup_meta["softs"],
                            soft_where=(
                                "title = ?", (normalized["title"],)
                            ),
                            tail_where=(
                                "status = ? AND COALESCE(note, '') = ?",
                                (
                                    normalized["status"],
                                    normalized["note"],
                                ),
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
                    raw_owner_visit = _safe_db_id(
                        raw.get("owner_visit_id")
                    )
                    if appointment_id > 0 and raw_owner_visit:
                        appointment_owner_raw[int(appointment_id)] = (
                            raw_owner_visit
                        )
                    if appointment_id > 0:
                        appointment_raw_notes[int(appointment_id)] = str(
                            normalized["note"] or ""
                        )
                        appointment_raw_status[int(appointment_id)] = str(
                            normalized["status"] or "upcoming"
                        )
                        appointment_raw_scheduled[
                            int(appointment_id)
                        ] = str(normalized["scheduled_at"] or "")[:10]

            lab_results_imported = 0
            existing_lab_counts: Dict[tuple, int] = {}
            imported_lab_counts: Dict[tuple, int] = {}
            lab_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_lab_results, list):
                lab_pre_ids = self._existing_ids_locked("lab_results")
                lab_valid_rows = _import_valid_rows(
                    raw_lab_results,
                    IMPORT_LIST_LIMITS["lab_results"],
                    lambda item: _normalize_lab_result(
                        item.get("profile") or "",
                        item.get("panel") or "",
                        item.get("item") or "",
                        item.get("value"),
                        item.get("unit") or "",
                        item.get("ref_low"),
                        item.get("ref_high"),
                        item.get("tested_at") or "",
                        item.get("lab_name") or "",
                        item.get("note") or "",
                    ),
                )
                lab_valid_rows = [
                    item for item in lab_valid_rows
                    if str(item.get("tested_at") or "").strip()
                ]
                lab_group_sizes, lab_soft_sizes = self._identity_group_meta(
                    lab_valid_rows,
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
                    lab_valid_rows, IMPORT_LIST_LIMITS["lab_results"],
                    "lab_results",
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
                    if not str(raw.get("tested_at") or "").strip():
                        # 空检测时间会被填成每次导入的当前时刻，
                        # 跨分钟重复导入同一备份产生重复化验
                        invalid += 1
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=lab_backup_meta["ids"],
                            backup_profiles=lab_backup_meta["profiles"],
                            backup_created=lab_backup_meta["created"],
                            backup_contents=lab_backup_meta["contents"],
                            backup_tails=lab_backup_meta["tails"],
                            backup_softs=lab_backup_meta["softs"],
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
                allergy_valid_rows = _import_valid_rows(
                    raw_allergies,
                    IMPORT_LIST_LIMITS["allergies"],
                    lambda item: _normalize_allergy(
                        item.get("profile") or "",
                        item.get("substance") or "",
                        item.get("reaction") or "",
                        item.get("severity") or "moderate",
                        item.get("kind") or "drug",
                        item.get("active", True),
                        item.get("noted_at") or "",
                        item.get("note") or "",
                    ),
                )
                (
                    allergy_group_sizes,
                    allergy_soft_sizes,
                ) = self._identity_group_meta(
                    allergy_valid_rows,
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
                    allergy_valid_rows,
                    IMPORT_LIST_LIMITS["allergies"], "allergies",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=allergy_backup_meta["ids"],
                            backup_profiles=allergy_backup_meta["profiles"],
                            backup_created=allergy_backup_meta["created"],
                            backup_contents=allergy_backup_meta["contents"],
                            backup_tails=allergy_backup_meta["tails"],
                            backup_softs=allergy_backup_meta["softs"],
                            soft_where=(
                                "substance = ?",
                                (normalized["substance"],),
                            ),
                            tail_where=(
                                "COALESCE(reaction, '') = ?"
                                " AND severity = ? AND active = ?"
                                " AND COALESCE(note, '') = ?",
                                (
                                    normalized["reaction"],
                                    normalized["severity"],
                                    normalized["active"],
                                    normalized["note"],
                                ),
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
                               AND active = ? AND COALESCE(note, '') = ?
                            """,
                            (
                                normalized["profile"],
                                normalized["substance"],
                                normalized["kind"],
                                normalized["reaction"],
                                normalized["severity"],
                                normalized["noted_at"],
                                normalized["active"],
                                normalized["note"],
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
                care_valid_rows = _import_valid_rows(
                    raw_care_items,
                    IMPORT_LIST_LIMITS["care_items"],
                    lambda item: _normalize_care_item(
                        item.get("profile") or "",
                        item.get("title") or "",
                        item.get("interval_days") or 30,
                        item.get("last_done_at") or "",
                        item.get("next_due_at") or "",
                        item.get("note") or "",
                        item.get("active", True),
                    ),
                )
                care_group_sizes, care_soft_sizes = (
                    self._identity_group_meta(
                        care_valid_rows,
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
                    care_valid_rows,
                    IMPORT_LIST_LIMITS["care_items"], "care_items",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=care_backup_meta["ids"],
                            backup_profiles=care_backup_meta["profiles"],
                            backup_created=care_backup_meta["created"],
                            backup_contents=care_backup_meta["contents"],
                            backup_tails=care_backup_meta["tails"],
                            backup_softs=care_backup_meta["softs"],
                            soft_where=(
                                "title = ?", (normalized["title"],)
                            ),
                            tail_where=(
                                "COALESCE(last_done_at, '') = ?"
                                " AND COALESCE(next_due_at, '') = ?"
                                " AND active = ?",
                                (
                                    normalized["last_done_at"],
                                    normalized["next_due_at"],
                                    normalized["active"],
                                ),
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
            #: 备份原始就诊 id → 本地就诊 id（含判重命中的行），用于
            #: 重写预约备注里的「由就诊记录 #N 自动创建」标记
            visit_raw_local_map: Dict[int, int] = {}
            inserted_visit_ids: set = set()
            visit_identity_claims: Dict[tuple, set] = {}
            if isinstance(raw_visit_records, list):
                visit_pre_ids = self._existing_ids_locked("visit_records")
                visit_valid_rows = _import_valid_rows(
                    raw_visit_records,
                    IMPORT_LIST_LIMITS["visit_records"],
                    lambda item: _normalize_visit_record(
                        item.get("profile") or "",
                        item.get("appointment_id") or 0,
                        item.get("visit_date") or "",
                        item.get("department") or "",
                        item.get("doctor") or "",
                        item.get("facility") or "",
                        item.get("diagnosis") or "",
                        item.get("advice") or "",
                        item.get("prescription") or "",
                        item.get("next_followup_date") or "",
                        item.get("note") or "",
                    ),
                )
                visit_valid_rows = [
                    item for item in visit_valid_rows
                    if str(item.get("visit_date") or "").strip()
                ]
                visit_group_sizes, visit_soft_sizes = (
                    self._identity_group_meta(
                        visit_valid_rows,
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
                    visit_valid_rows, IMPORT_LIST_LIMITS["visit_records"],
                    "visit_records",
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
                    if not str(raw.get("visit_date") or "").strip():
                        # 空就诊日期按导入当天填充，跨天重复导入逐日新增
                        invalid += 1
                        continue
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
                    for _attr, _linked in (
                        ("appointment", mapped_appointment_id),
                        ("followup", mapped_followup_appointment_id),
                    ):
                        if not _linked:
                            continue
                        appt_owner = self._conn.execute(
                            "SELECT profile FROM appointments"
                            " WHERE id = ?",
                            (int(_linked),),
                        ).fetchone()
                        if (
                            appt_owner is None
                            or _normalize_profile_name(
                                appt_owner["profile"]
                            ) != normalized["profile"]
                        ):
                            if _attr == "appointment":
                                mapped_appointment_id = 0
                            else:
                                mapped_followup_appointment_id = 0
                    raw_followup_reminder = _safe_db_id(
                        raw.get("followup_reminder_id")
                    )
                    # 旧备份可能让多条就诊共享同一复诊预约：只有归属
                    # 就诊（备份 owner_visit_id 或备注 #N 标记）才写链接，
                    # 否则非归属就诊会抢占预约并覆盖 owner，归属就诊的
                    # 复诊安排丢失、再次安排复诊会重复建预约
                    if mapped_followup_appointment_id:
                        followup_owner_raw = int(
                            appointment_owner_raw.get(
                                int(mapped_followup_appointment_id), 0
                            ) or 0
                        )
                        if not followup_owner_raw:
                            marker = re.search(
                                r"由就诊记录 #(\d+) 自动创建",
                                str(appointment_raw_notes.get(
                                    int(mapped_followup_appointment_id),
                                    "",
                                )),
                            )
                            if marker is not None:
                                followup_owner_raw = _safe_db_id(
                                    marker.group(1)
                                )
                        if (
                            followup_owner_raw
                            and _safe_db_id(raw.get("id"))
                            != followup_owner_raw
                        ):
                            mapped_followup_appointment_id = 0
                            raw_followup_reminder = 0
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=visit_backup_meta["ids"],
                            backup_profiles=visit_backup_meta["profiles"],
                            backup_created=visit_backup_meta["created"],
                            backup_contents=visit_backup_meta["contents"],
                            backup_tails=visit_backup_meta["tails"],
                            backup_softs=visit_backup_meta["softs"],
                            soft_where=(
                                "visit_date = ? AND diagnosis = ?",
                                (
                                    normalized["visit_date"],
                                    normalized["diagnosis"],
                                ),
                            ),
                            tail_where=(
                                "COALESCE(facility, '') = ?"
                                " AND COALESCE(advice, '') = ?"
                                " AND COALESCE(prescription, '') = ?"
                                " AND COALESCE(next_followup_date, '') = ?",
                                (
                                    normalized["facility"],
                                    normalized["advice"],
                                    normalized["prescription"],
                                    normalized["next_followup_date"],
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
                        raw_visit_key = _safe_db_id(raw.get("id"))
                        if raw_visit_key and existing_visit_id:
                            visit_raw_local_map[raw_visit_key] = (
                                existing_visit_id
                            )
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
                            # 预约链接先留空：由 visit_link_updates 校验
                            # 通过后统一写回，未校验的临时链接会把共享
                            # 预约误判为“已被另一条就诊占用”（第七十九轮）
                            0,
                            0,
                            normalized["note"],
                            created_key or now,
                            str(raw.get("updated_at") or now)[:40],
                        ),
                    )
                    new_visit_id = int(cursor.lastrowid or 0)
                    if new_visit_id:
                        inserted_visit_ids.add(new_visit_id)
                    raw_visit_key = _safe_db_id(raw.get("id"))
                    if raw_visit_key and new_visit_id:
                        visit_raw_local_map[raw_visit_key] = new_visit_id
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
                condition_valid_rows = _import_valid_rows(
                    raw_conditions,
                    IMPORT_LIST_LIMITS["conditions"],
                    lambda item: _normalize_condition(
                        item.get("profile") or "",
                        item.get("name") or "",
                        item.get("icd10") or "",
                        item.get("diagnosed_at") or "",
                        item.get("status") or "active",
                        item.get("severity") or "moderate",
                        item.get("note") or "",
                    ),
                )
                (
                    condition_group_sizes,
                    condition_soft_sizes,
                ) = self._identity_group_meta(
                    condition_valid_rows,
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
                    condition_valid_rows,
                    IMPORT_LIST_LIMITS["conditions"], "conditions",
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=condition_backup_meta["ids"],
                            backup_profiles=condition_backup_meta["profiles"],
                            backup_created=condition_backup_meta["created"],
                            backup_contents=condition_backup_meta["contents"],
                            backup_tails=condition_backup_meta["tails"],
                            backup_softs=condition_backup_meta["softs"],
                            soft_where=(
                                "name = ?", (normalized["name"],)
                            ),
                            tail_where=(
                                "COALESCE(icd10, '') = ? AND status = ?"
                                " AND severity = ?",
                                (
                                    normalized["icd10"],
                                    normalized["status"],
                                    normalized["severity"],
                                ),
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

            target_appointment_ids = set(imported_appointment_ids) | set(
                raw_appointment_id_map.values()
            )
            if target_appointment_ids:
                # 只处理本次备份来源的预约：本地既有预约的 #N 是本地
                # 就诊 id，不能用备份主键映射改写或删除；即使备份里
                # 没有任何就诊映射到本地，也要清掉新插入预约上的
                # 悬空标记，避免之后误绑目标库同号就诊
                ordered_ids = sorted(target_appointment_ids)
                placeholders = ",".join("?" for _ in ordered_ids)
                # 不能按本地备注 LIKE 过滤：被删除就诊剥空的备注
                # 仍需要从备份原始 note 恢复标记
                marker_rows = self._conn.execute(
                    f"SELECT id, note, profile, status, scheduled_at,"
                    f" owner_visit_id FROM appointments"
                    f" WHERE id IN ({placeholders})",
                    tuple(ordered_ids),
                ).fetchall()
                marker_pattern = re.compile(
                    r"由就诊记录 #(\d+) 自动创建"
                )

                def _visit_linked_to_other(
                    local_visit: int, appointment_key: int
                ) -> bool:
                    """就诊当前是否已链接另一条待就诊复诊预约。"""
                    if not local_visit:
                        return False
                    linked_row = self._conn.execute(
                        "SELECT followup_appointment_id FROM visit_records"
                        " WHERE id = ?",
                        (int(local_visit),),
                    ).fetchone()
                    if linked_row is None:
                        return False
                    linked = int(
                        linked_row["followup_appointment_id"] or 0
                    )
                    return linked != 0 and linked != int(appointment_key)

                for marker_row in marker_rows:
                    appointment_key = int(marker_row["id"])
                    note_text = str(marker_row["note"] or "")
                    raw_note = appointment_raw_notes.get(appointment_key)
                    # 本地备注被删除就诊时剥空（""）：允许按备份映射恢复；
                    # 被改写/已重映射过的备注保持原样，避免把本地就诊 id
                    # 再当备份主键二次映射
                    restore_empty_note = (
                        note_text == ""
                        and appointment_key not in imported_appointment_ids
                    )
                    if restore_empty_note:
                        # 本地已把该预约降级为独立预约（改期早于就诊后
                        # owner/备注被清空）：仅当它仍是 upcoming 且日期
                        # 与本地就诊复诊日一致时才恢复标记，避免只写
                        # owner 不写链接的半挂接。已完成/已取消的预约
                        # 一律不恢复：再次导入不能复活被拒绝的旧标记
                        if str(marker_row["status"] or "") != "upcoming":
                            continue
                        if (
                            int(marker_row["owner_visit_id"] or 0) == 0
                        ):
                            scheduled_day = str(
                                marker_row["scheduled_at"] or ""
                            )[:10]
                            raw_marker = marker_pattern.search(
                                str(raw_note or "")
                            )
                            mapped_visit = (
                                visit_raw_local_map.get(
                                    _safe_db_id(raw_marker.group(1)), 0
                                ) if raw_marker is not None else 0
                            )
                            if _visit_linked_to_other(
                                mapped_visit, appointment_key
                            ):
                                # 就诊已链接另一条待就诊复诊预约：这条
                                # 预存预约是恢复的重复项，不能再把
                                # owner/标记抢回来，否则同一就诊出现
                                # 两条复诊预约与两条待办提醒
                                continue
                            visit_day = ""
                            if mapped_visit:
                                visit_row = self._conn.execute(
                                    "SELECT next_followup_date"
                                    " FROM visit_records WHERE id = ?",
                                    (int(mapped_visit),),
                                ).fetchone()
                                if visit_row is not None:
                                    visit_day = str(
                                        visit_row["next_followup_date"] or ""
                                    )[:10]
                            backup_status = appointment_raw_status.get(
                                appointment_key, "upcoming"
                            )
                            if (
                                not (
                                    backup_status == "upcoming"
                                    and mapped_visit
                                    in inserted_visit_ids
                                )
                                and (
                                    not mapped_visit
                                    or not scheduled_day
                                    or visit_day != scheduled_day
                                )
                            ):
                                continue
                    if raw_note is None:
                        continue
                    if (
                        "由就诊记录 #" not in note_text
                        and "由就诊记录 #" not in str(raw_note)
                    ):
                        continue
                    if note_text != raw_note and not restore_empty_note:
                        continue

                    def _remap_visit_marker(
                        match, appt_key=appointment_key,
                        keep_unmapped=(note_text != ""),
                    ):
                        raw_visit = _safe_db_id(match.group(1))
                        local_visit = visit_raw_local_map.get(raw_visit)
                        if not local_visit:
                            if appt_key in imported_appointment_ids:
                                # 本次新插入且映射不到就诊：陈旧标记
                                # 会误绑目标库同号就诊，直接移除
                                return ""
                            if not keep_unmapped:
                                # 备注已被剥空：悬空标记不能复活
                                return ""
                            return match.group(0)
                        owner_row = self._conn.execute(
                            "SELECT owner_visit_id FROM appointments"
                            " WHERE id = ?",
                            (int(appt_key),),
                        ).fetchone()
                        owner_visit = (
                            int(owner_row["owner_visit_id"] or 0)
                            if owner_row is not None else 0
                        )
                        if owner_visit and owner_visit != int(local_visit):
                            # 预约已归属另一条本地就诊（备份中的就诊
                            # 因内容编辑被补插成新记录）：保持原标记，
                            # 不能改指重复就诊，否则编辑/删除它会级联
                            # 删除原就诊的复诊预约与提醒
                            return match.group(0)
                        return f"由就诊记录 #{local_visit} 自动创建"

                    # 备注被剥空时从备份原始 note 重建标记；否则只在
                    # 本地备注仍与原始 note 相同时重映射
                    source_note = (
                        str(raw_note or "") if restore_empty_note else note_text
                    )
                    new_note = marker_pattern.sub(
                        _remap_visit_marker, source_note
                    ).strip()
                    if new_note != note_text:
                        self._conn.execute(
                            "UPDATE appointments SET note = ?,"
                            " updated_at = ? WHERE id = ?",
                            (new_note[:300], now, int(marker_row["id"])),
                        )
                raw_visit_days = {}
                if isinstance(raw_visit_records, list):
                    for raw_visit in raw_visit_records:
                        if not isinstance(raw_visit, dict):
                            continue
                        raw_visit_key = _safe_db_id(raw_visit.get("id"))
                        if raw_visit_key:
                            raw_visit_days[raw_visit_key] = str(
                                raw_visit.get("next_followup_date") or ""
                            )[:10]
                #: 同一备份就诊的多条待就诊自动预约：只恢复一条归属，
                #: 优先备份链接指向的那条（第九十八轮 [10]）
                preferred_owner_appointment: Dict[int, int] = {}
                for (
                    _link_visit, _link_appt, _link_rem,
                    _link_raw_visit, _link_new,
                ) in visit_link_updates:
                    raw_key = _safe_db_id(_link_raw_visit)
                    if raw_key and _link_appt:
                        preferred_owner_appointment.setdefault(
                            raw_key, int(_link_appt)
                        )
                claimed_owner_upcoming: Dict[int, set] = {}
                for appointment_key, raw_owner in (
                    appointment_owner_raw.items()
                ):
                    local_owner = visit_raw_local_map.get(raw_owner, 0)
                    appointment_row = self._conn.execute(
                        "SELECT profile, owner_visit_id, status,"
                        " scheduled_at FROM appointments"
                        " WHERE id = ?",
                        (appointment_key,),
                    ).fetchone()
                    if local_owner:
                        visit_profile = self._conn.execute(
                            "SELECT profile FROM visit_records"
                            " WHERE id = ?",
                            (local_owner,),
                        ).fetchone()
                        if (
                            visit_profile is None
                            or appointment_row is None
                            or _normalize_profile_name(
                                str(visit_profile["profile"] or "")
                            ) != _normalize_profile_name(
                                str(appointment_row["profile"] or "")
                            )
                        ):
                            local_owner = 0
                    if _visit_linked_to_other(local_owner, appointment_key):
                        # 同一就诊已有另一条待就诊复诊预约：恢复旧备份
                        # 不能把这条预存预约再挂回去
                        local_owner = 0
                    if (
                        local_owner
                        and appointment_row is not None
                        and appointment_raw_status.get(
                            appointment_key, "upcoming"
                        ) == "upcoming"
                    ):
                        raw_owner_key = _safe_db_id(raw_owner)
                        claimed = claimed_owner_upcoming.setdefault(
                            raw_owner_key, set()
                        )
                        preferred = preferred_owner_appointment.get(
                            raw_owner_key
                        )
                        if claimed or (
                            preferred
                            and int(appointment_key) != preferred
                        ):
                            # 同一就诊的第二条待就诊自动预约：不恢复
                            # 归属并剥掉备注标记，避免复诊完成后的
                            # 幽灵预约与到期待办（第九十八轮 [10]）
                            if appointment_key in imported_appointment_ids:
                                def _strip_this_owner(match):
                                    marker_id = _safe_db_id(match.group(1))
                                    if marker_id == int(local_owner) or (
                                        visit_raw_local_map.get(marker_id)
                                        == int(local_owner)
                                    ):
                                        # 备注标记可能已被重映射成本地
                                        # 就诊号，两种形态都要剥掉
                                        return ""
                                    return match.group(0)

                                note_row = self._conn.execute(
                                    "SELECT note FROM appointments"
                                    " WHERE id = ?",
                                    (int(appointment_key),),
                                ).fetchone()
                                cleaned = re.sub(
                                    r"由就诊记录 #(\d+) 自动创建",
                                    _strip_this_owner,
                                    str(note_row["note"] or "")
                                    if note_row is not None else "",
                                ).strip()[:300]
                                self._conn.execute(
                                    "UPDATE appointments SET"
                                    " owner_visit_id = 0, note = ?,"
                                    " updated_at = ? WHERE id = ?",
                                    (cleaned, now, int(appointment_key)),
                                )
                            local_owner = 0
                            continue
                        claimed.add(int(appointment_key))
                    existing_owner = (
                        int(appointment_row["owner_visit_id"] or 0)
                        if appointment_row is not None else 0
                    )
                    owner_ok = True
                    if local_owner and appointment_row is not None:
                        # 日期与就诊复诊日不一致：不得写回 owner/标记，
                        # 否则已改期的复诊预约会被旧备份重新挂回原
                        # 就诊，留下无链接的半挂接
                        owner_visit_row = self._conn.execute(
                            "SELECT next_followup_date"
                            " FROM visit_records WHERE id = ?",
                            (int(local_owner),),
                        ).fetchone()
                        owner_visit_day = (
                            str(owner_visit_row["next_followup_date"] or "")
                            [:10] if owner_visit_row is not None else ""
                        )
                        backup_status = appointment_raw_status.get(
                            appointment_key, "upcoming"
                        )
                        backup_owner_day = raw_visit_days.get(
                            _safe_db_id(raw_owner), ""
                        )
                        backup_owner_consistent = bool(
                            backup_owner_day
                            and backup_owner_day
                            == str(appointment_row["scheduled_at"] or "")[:10]
                        )
                        if (
                            not (
                                backup_status in ("cancelled", "completed")
                                and backup_owner_consistent
                                and local_owner not in inserted_visit_ids
                            )
                            and not (
                                local_owner in inserted_visit_ids
                                and backup_status in ("upcoming", "completed")
                            )
                            and owner_visit_day != str(
                                appointment_row["scheduled_at"] or ""
                            )[:10]
                        ):
                            # completed/cancelled 历史预约：备份自身一致
                            # （预约日 == 归属就诊复诊日）时保留归属
                            # 证据，供改回日期回挂与「恢复待就诊」校验；
                            # 备份本身已不一致的半挂接仍剥离（round55/57）
                            owner_ok = False
                    if not owner_ok:
                        if appointment_key in imported_appointment_ids:
                            # 本次新插入的预约：拒绝归属时同步剥掉备注
                            # 里的就诊标记，避免无链接的半挂接
                            def _strip_owner_marker(match):
                                raw_visit = _safe_db_id(match.group(1))
                                if visit_raw_local_map.get(raw_visit) == int(
                                    local_owner
                                ):
                                    return ""
                                return match.group(0)

                            note_row = self._conn.execute(
                                "SELECT note FROM appointments WHERE id = ?",
                                (int(appointment_key),),
                            ).fetchone()
                            cleaned_note = re.sub(
                                r"由就诊记录 #(\d+) 自动创建",
                                _strip_owner_marker,
                                str(note_row["note"] or "")
                                if note_row is not None else "",
                            ).strip()[:300]
                            self._conn.execute(
                                "UPDATE appointments SET owner_visit_id = 0,"
                                " note = ?, updated_at = ? WHERE id = ?",
                                (cleaned_note, now, int(appointment_key)),
                            )
                        continue
                    if (
                        appointment_key not in imported_appointment_ids
                        and existing_owner != 0
                        and existing_owner != local_owner
                    ):
                        # 本地存活的预约已有明确归属：恢复旧备份不能把它
                        # 改挂到另一条就诊（误认领时会造成日期错配）
                        continue
                    self._conn.execute(
                        "UPDATE appointments SET owner_visit_id = ?"
                        " WHERE id = ?",
                        (int(local_owner), int(appointment_key)),
                    )

            reminders_imported = 0
            imported_reminder_ids: set = set()
            reminder_links: List[Tuple[int, int]] = []
            raw_reminder_id_map: Dict[int, int] = {}
            existing_reminder_counts: Dict[tuple, int] = {}
            existing_reminder_done_counts: Dict[tuple, int] = {}
            imported_reminder_counts: Dict[tuple, int] = {}
            reminder_identity_claims: Dict[tuple, set] = {}
            pre_existing_reminder_ids = {
                int(row["id"])
                for row in self._conn.execute(
                    "SELECT id FROM reminders"
                ).fetchall()
            }
            #: 备份中同一预约+标题的未完成提醒条数：日期不同的合法多条
            #: 不能被「同预约+标题」兜底合并掉
            backup_reminder_title_counts: Dict[tuple, int] = {}
            if isinstance(raw_reminders, list):
                for raw_item in raw_reminders[
                    : IMPORT_LIST_LIMITS["reminders"]
                ]:
                    if not isinstance(raw_item, dict):
                        continue
                    if raw_item.get("done") in (1, True, "1"):
                        continue
                    raw_appt = _safe_db_id(raw_item.get("appointment_id"))
                    raw_title = " ".join(
                        str(raw_item.get("title") or "").split()
                    )[:MAX_REMINDER_TITLE_LENGTH]
                    if raw_appt > 0 and raw_title:
                        count_key = (raw_appt, raw_title)
                        backup_reminder_title_counts[count_key] = (
                            backup_reminder_title_counts.get(count_key, 0)
                            + 1
                        )
            def _reminder_import_valid(item):
                try:
                    _normalize_profile_name(
                        item.get("profile"), strict=True
                    )
                except ValueError:
                    return None
                title_text = " ".join(
                    str(item.get("title") or "").split()
                )
                if not title_text:
                    return None
                due_text = _normalize_reminder_due(item.get("due_at"))
                _normalize_reminder_advance(item.get("advance_days"))
                _validate_repeat_window(
                    due_text,
                    _normalize_reminder_repeat_days(
                        item.get("repeat_days")
                    ),
                    _normalize_reminder_repeat_until(
                        item.get("repeat_until")
                    ),
                )
                return {"title": title_text, "due": due_text}

            reminder_valid_rows = _import_valid_rows(
                raw_reminders,
                IMPORT_LIST_LIMITS["reminders"],
                _reminder_import_valid,
            )
            reminder_backup_meta = self._import_backup_meta(
                reminder_valid_rows,
                IMPORT_LIST_LIMITS["reminders"], "reminders",
            )
            if isinstance(raw_reminders, list):
                reminder_group_sizes = self._identity_group_sizes(
                    reminder_valid_rows,
                    IMPORT_LIST_LIMITS["reminders"],
                    lambda item: (
                        str(item.get("created_at") or "").strip()[:40],
                        _normalize_profile_name(item.get("profile")),
                    ) if str(item.get("created_at") or "").strip() else None,
                )
                reminder_soft_sizes = self._identity_group_sizes(
                    reminder_valid_rows,
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
                    try:
                        profile = _normalize_profile_name(
                            raw.get("profile"), strict=True
                        )
                    except ValueError:
                        invalid += 1
                        continue
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
                    if medication_id and _normalize_profile_name(
                        str(mapped_medication.get("profile") or "")
                    ) != profile:
                        # 跨成员药物不能被提醒挂接
                        medication_id = 0
                    medication_name = " ".join(
                        str(raw.get("medication_name") or "").split()
                    )[:MAX_MEDICATION_NAME_LENGTH]
                    med_title_hint = ""
                    if str(raw.get("title") or "").startswith("用药："):
                        med_title_hint = " ".join(
                            str(raw.get("title") or "")[
                                len("用药："):
                            ].split()
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
                                (
                                    medication_name
                                    and str(med_row["name"])
                                    == medication_name
                                )
                                or (
                                    med_title_hint
                                    and str(med_row["name"])
                                    == med_title_hint
                                )
                            )
                        ):
                            # 必须有点名的药名线索：跨安装同号药物不能
                            # 被当成备份里的那条，否则删除它会连带
                            # 删除这条提醒（第八十轮）
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
                    if vaccination_id:
                        vac_owner = self._conn.execute(
                            "SELECT profile FROM vaccinations WHERE id = ?",
                            (vaccination_id,),
                        ).fetchone()
                        if (
                            vac_owner is None
                            or _normalize_profile_name(vac_owner["profile"])
                            != profile
                        ):
                            vaccination_id = 0
                    if not vaccination_id and raw_vaccination_id > 0:
                        # CSV 只带疫苗 id：必须有名称或日期线索才关联，
                        # 跨安装同号疫苗不能被误挂（第八十轮）
                        vac_row = self._conn.execute(
                            "SELECT id, profile, vaccine_name, next_due"
                            " FROM vaccinations WHERE id = ?",
                            (raw_vaccination_id,),
                        ).fetchone()
                        if (
                            vac_row is not None
                            and _normalize_profile_name(vac_row["profile"])
                            == profile
                        ):
                            vac_name = str(vac_row["vaccine_name"] or "")
                            reminder_title = str(raw.get("title") or "")
                            reminder_note = str(raw.get("note") or "")
                            due_day = str(due_at or "")[:10]
                            next_due_day = str(
                                vac_row["next_due"] or ""
                            )[:10]
                            clue_ok = bool(
                                (
                                    vac_name
                                    and (
                                        vac_name in reminder_title
                                        or vac_name in reminder_note
                                    )
                                )
                                or (
                                    due_day
                                    and next_due_day
                                    and due_day == next_due_day
                                )
                            )
                            if clue_ok:
                                vaccination_id = int(vac_row["id"])
                    raw_appointment_id = _safe_db_id(
                        raw.get("appointment_id")
                    )
                    appointment_id = raw_appointment_id_map.get(
                        raw_appointment_id, 0
                    )
                    if appointment_id:
                        appt_owner = self._conn.execute(
                            "SELECT profile, status FROM appointments"
                            " WHERE id = ?",
                            (appointment_id,),
                        ).fetchone()
                        if (
                            appt_owner is None
                            or _normalize_profile_name(appt_owner["profile"])
                            != profile
                        ):
                            appointment_id = 0
                    if not appointment_id and raw_appointment_id > 0:
                        appt_row = self._conn.execute(
                            "SELECT id, profile, status, scheduled_at,"
                            " title FROM appointments WHERE id = ?",
                            (raw_appointment_id,),
                        ).fetchone()
                        if (
                            appt_row is not None
                            and str(appt_row["status"] or "")
                            == "upcoming"
                            and _normalize_profile_name(appt_row["profile"])
                            == profile
                        ):
                            # CSV 只带原始预约 ID（跨安装/ID 复用）：必须
                            # 有日期或标题线索与预约一致才挂接，否则
                            # 取消/删除同号无关预约会静默删除这条提醒
                            reminder_title = str(raw.get("title") or "")
                            reminder_note = str(raw.get("note") or "")
                            appt_title = str(appt_row["title"] or "")
                            appt_day = str(
                                appt_row["scheduled_at"] or ""
                            )[:10]
                            # 强线索：标题/备注与预约标题完全相等，或备注
                            # 里的「复诊 YYYY-MM-DD」与预约日精确一致。
                            # 只凭到期日接近会把提醒挂到同号无关预约
                            note_day = re.search(
                                r"复诊\s*(\d{4}-\d{2}-\d{2})",
                                reminder_note,
                            )
                            hint_ok = bool(
                                (
                                    appt_title
                                    and (
                                        reminder_title.strip()
                                        == appt_title.strip()
                                        or reminder_note.strip()
                                        == appt_title.strip()
                                    )
                                )
                                or (
                                    note_day
                                    and appt_day
                                    and note_day.group(1) == appt_day
                                )
                            )
                            if hint_ok:
                                appointment_id = int(appt_row["id"])
                    done = 1 if raw.get("done") in (1, True, "1") else 0
                    reminder_dropped = False
                    if appointment_id and not done:
                        appt_status = self._conn.execute(
                            "SELECT status FROM appointments WHERE id = ?",
                            (appointment_id,),
                        ).fetchone()
                        if (
                            appt_status is None
                            or str(appt_status["status"] or "")
                            != "upcoming"
                        ):
                            if appt_status is not None:
                                # 同档案预约已取消/完成：备份里的待办提醒
                                # 已过期，直接跳过；清 0 会把它降级成无主
                                # 待办并逃过最终的「非待就诊预约」清理
                                reminder_dropped = True
                            appointment_id = 0
                    if reminder_dropped:
                        continue
                    reminder_id = _safe_db_id(raw.get("id"))
                    raw_created = str(
                        raw.get("created_at") or ""
                    ).strip()[:40]
                    # 落库必须使用与身份匹配相同的去空白时间：带首尾
                    # 空白的 created_at 原样入库会让重复导入永远查不到
                    # 旧行，每次再插一条副本（第八十轮）
                    created_at = raw_created or now
                    updated_at = (
                        str(raw.get("updated_at") or "").strip()[:40]
                        or created_at
                    )
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
                            backup_exported_at=backup_exported_at,
                            backup_ids=reminder_backup_meta["ids"],
                            backup_profiles=reminder_backup_meta["profiles"],
                            backup_created=reminder_backup_meta["created"],
                            backup_contents=reminder_backup_meta["contents"],
                            backup_tails=reminder_backup_meta["tails"],
                            backup_softs=reminder_backup_meta["softs"],
                            match_where=("profile = ?", (profile,)),
                            soft_where=("title = ?", (title,)),
                            backup_group_size=reminder_group_sizes.get(
                                (raw_created, profile), 1
                            ),
                            backup_soft_size=reminder_soft_sizes.get(
                                (raw_created, profile, title), 1
                            ),
                            # 提醒没有独立锚点：同秒组大小即锚点组，
                            # 启用「主键未漂移+有编辑证据」自认领
                            backup_anchor_size=reminder_group_sizes.get(
                                (raw_created, profile), 1
                            ),
                            tail_where=(
                                "COALESCE(note, '') = ? AND advance_days = ?"
                                " AND repeat_days = ?"
                                " AND COALESCE(repeat_until, '') = ?"
                                " AND done = ?",
                                (
                                    note, advance_days, repeat_days,
                                    repeat_until, done,
                                ),
                            ),
                        )
                        if existing_reminder_id is not None:
                            if reminder_id > 0:
                                raw_reminder_id_map.setdefault(
                                    reminder_id, existing_reminder_id
                                )
                            continue
                    else:
                        # CSV 没有创建时间：按标题/档案/备注/到期日与
                        # 提醒设置计数判重（提前天数/重复规则不同是
                        # 两条合法提醒，不能合并）。done 不参与身份：
                        # 本地完成后重导入同一备份时同一条提醒已在库，
                        # 否则会把旧待办无限复制出来（第七十三轮审查）
                        base_identity = (
                            title, profile, note, session_id, due_at,
                            advance_days, repeat_days, repeat_until,
                            appointment_id, medication_id,
                            vaccination_id,
                        )
                        # 预约 ID 无法映射时 appointment_id 归 0：同一文件
                        # 里原本挂不同预约的两条提醒会变成完全相同。
                        # 用原始 ID 区分文件内出现次数，库内仍按 0 计数
                        occurrence_extra = (
                            0 if appointment_id else int(raw_appointment_id)
                        )
                        identity_key = base_identity + (occurrence_extra,)
                        base_for_count = base_identity
                        if done:
                            if (
                                base_for_count
                                not in existing_reminder_done_counts
                            ):
                                done_row = self._conn.execute(
                                    """
                                    SELECT COUNT(*) AS total FROM reminders
                                     WHERE title = ? AND profile = ?
                                       AND note = ? AND session_id = ?
                                       AND due_at = ? AND advance_days = ?
                                       AND repeat_days = ?
                                       AND COALESCE(repeat_until, '') = ?
                                       AND appointment_id = ?
                                       AND medication_id = ?
                                       AND vaccination_id = ?
                                       AND done = 1
                                    """,
                                    (
                                        title, profile, note, session_id,
                                        due_at, advance_days, repeat_days,
                                        repeat_until, appointment_id,
                                        medication_id, vaccination_id,
                                    ),
                                ).fetchone()
                                existing_reminder_done_counts[
                                    base_for_count
                                ] = int(done_row["total"] if done_row else 0)
                        elif base_for_count not in existing_reminder_counts:
                            count_row = self._conn.execute(
                                """
                                SELECT COUNT(*) AS total FROM reminders
                                 WHERE title = ? AND profile = ?
                                   AND note = ? AND session_id = ?
                                   AND due_at = ? AND advance_days = ?
                                   AND repeat_days = ?
                                   AND COALESCE(repeat_until, '') = ?
                                   AND appointment_id = ?
                                   AND medication_id = ?
                                   AND vaccination_id = ?
                                """,
                                (
                                    title, profile, note, session_id,
                                    due_at, advance_days, repeat_days,
                                    repeat_until, appointment_id,
                                    medication_id, vaccination_id,
                                ),
                            ).fetchone()
                            existing_reminder_counts[base_for_count] = int(
                                count_row["total"] if count_row else 0
                            )
                        # 文件内重复只按「身份+完成状态」去重：同一
                        # 备份里一条待办、一条已完成是两条合法提醒
                        seen = imported_reminder_counts.get(
                            (identity_key, done), 0
                        )
                        imported_reminder_counts[(identity_key, done)] = (
                            seen + 1
                        )
                        known = (
                            existing_reminder_done_counts.get(
                                base_for_count, 0
                            )
                            if done
                            else existing_reminder_counts[base_for_count]
                        )
                        # 同一文件内的重复行只导入一次；库中已有同身份
                        # 提醒（任何完成状态都算）时整组跳过，避免把
                        # 已完成提醒重新导入成新的待办副本
                        skip_existing = seen >= 1 or known > 0
                        if skip_existing:
                            if reminder_id > 0:
                                existing_row = self._conn.execute(
                                    """
                                    SELECT id FROM reminders
                                     WHERE title = ? AND profile = ?
                                       AND note = ? AND session_id = ?
                                       AND due_at = ? AND advance_days = ?
                                       AND repeat_days = ?
                                       AND COALESCE(repeat_until, '') = ?
                                       AND appointment_id = ?
                                       AND medication_id = ?
                                       AND vaccination_id = ?
                                     ORDER BY id LIMIT 1
                                    """,
                                    (
                                        title, profile, note, session_id,
                                        due_at, advance_days, repeat_days,
                                        repeat_until, appointment_id,
                                        medication_id, vaccination_id,
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
                        if existing_pending is None and (
                            backup_reminder_title_counts.get(
                                (raw_appointment_id, title), 0
                            ) <= 1
                        ):
                            # 预约已在本地改期并重建过提醒：旧备份提醒
                            # 日期不同，但同一预约+标题的待办不应重复。
                            # 备份里同一预约+标题有多条（日期不同的合法
                            # 多条提醒）时不能合并，否则会静默丢失
                            existing_pending = self._conn.execute(
                                "SELECT id FROM reminders"
                                " WHERE appointment_id = ? AND done = 0"
                                " AND profile = ? AND title = ?"
                                " ORDER BY id LIMIT 1",
                                (appointment_id, profile, title),
                            ).fetchone()
                        if existing_pending is not None:
                            pending_id = int(existing_pending["id"])
                            reusable = (
                                pending_id in pre_existing_reminder_ids
                                and pending_id not in imported_reminder_ids
                            )
                            if reusable and backup_reminder_title_counts.get(
                                (raw_appointment_id, title), 0
                            ) > 1:
                                same_row = self._conn.execute(
                                    "SELECT 1 FROM reminders WHERE id = ?"
                                    " AND note = ? AND session_id = ?"
                                    " AND advance_days = ? AND repeat_days = ?"
                                    " AND COALESCE(repeat_until, '') = ?",
                                    (
                                        pending_id, note, session_id,
                                        advance_days, repeat_days,
                                        repeat_until,
                                    ),
                                ).fetchone()
                                reusable = same_row is not None
                            if reusable:
                                if reminder_id > 0:
                                    raw_reminder_id_map.setdefault(
                                        reminder_id, pending_id,
                                    )
                                continue
                    if (
                        appointment_id
                        and appointment_id not in imported_appointment_ids
                    ):
                        appt_row = self._conn.execute(
                            "SELECT scheduled_at FROM appointments"
                            " WHERE id = ?",
                            (int(appointment_id),),
                        ).fetchone()
                        local_day = (
                            str(appt_row["scheduled_at"] or "")[:10]
                            if appt_row is not None else ""
                        )
                        # 优先用备份预约本身的计划日：提醒备注可能被
                        # 用户改写成不含日期，单看备注会漏判旧提醒
                        # （第八十七轮）
                        backup_day = appointment_raw_scheduled.get(
                            int(appointment_id), ""
                        )
                        marker = re.search(
                            r"复诊\s*(\d{4}-\d{2}-\d{2})",
                            str(note or ""),
                        )
                        if not backup_day and marker:
                            backup_day = marker.group(1)
                        if (
                            local_day
                            and backup_day
                            and backup_day != local_day
                        ):
                            # 本地预约已改期、旧提醒也已删除：备份里的
                            # 旧日期提醒不能原样挂回（会在错误日期触发）
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
            # 拒绝链接的脱钩提醒延后到全部链接写回后再判定是否仍
            # 有引用（共享就诊可能全部拒绝链接，第九十六轮 [9]）
            deferred_orphan_reminders: List[int] = []
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
                    "SELECT profile, followup_appointment_id,"
                    " followup_reminder_id, next_followup_date"
                    " FROM visit_records"
                    " WHERE id = ?",
                    (int(visit_id),),
                ).fetchone()
                if followup_reminder_id and visit_row is not None:
                    reminder_row = self._conn.execute(
                        "SELECT profile, done, appointment_id"
                        " FROM reminders WHERE id = ?",
                        (int(followup_reminder_id),),
                    ).fetchone()
                    reminder_owner = int(
                        reminder_row["appointment_id"] or 0
                    ) if reminder_row is not None else -1
                    owner_matches = (
                        reminder_owner == int(followup_appointment_id)
                        if followup_appointment_id else reminder_owner == 0
                    )
                    # reopen 降级后的待办提醒合法地脱钩（appointment_id=0）
                    # 但就诊仍以 followup_reminder_id 指向它：备份恢复
                    # 必须保留该链接，否则恢复后改复诊日期会留下幽灵提醒
                    if (
                        not owner_matches
                        and followup_appointment_id
                        and reminder_owner == 0
                        and reminder_row is not None
                        and not int(reminder_row["done"] or 0)
                    ):
                        owner_matches = True
                    if (
                        reminder_row is None
                        or int(reminder_row["done"] or 0)
                        or not owner_matches
                        or _normalize_profile_name(
                            reminder_row["profile"] or ""
                        ) != _normalize_profile_name(
                            visit_row["profile"] or ""
                        )
                    ):
                        # 已完成提醒、跨成员提醒、未挂在对应预约上或
                        # 已被清理：不能写回就诊链接，避免悬空/跨患者
                        # 引用或无主的重复待办被就诊认领
                        followup_reminder_id = 0
                appt_row = (
                    self._conn.execute(
                        "SELECT profile, status, scheduled_at,"
                        " owner_visit_id, note"
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
                    same_appt_profile = bool(
                        appt_row is not None
                        and _normalize_profile_name(
                            str(appt_row["profile"] or "")
                        ) == _normalize_profile_name(
                            str(visit_row["profile"] or "")
                            if visit_row is not None else ""
                        )
                    )
                    appt_status = (
                        str(appt_row["status"] or "")
                        if appt_row is not None else ""
                    )
                    if appt_status == "completed" and same_appt_profile:
                        # 已完成预约是就诊历史：实际就诊日期可以与计划复诊
                        # 日期不同，只要归属（owner/备注标记）指向该就诊
                        # 就恢复链接，避免已复诊被误报为逾期
                        marker_visit = 0
                        marker = re.search(
                            r"由就诊记录 #(\d+) 自动创建",
                            str(appt_row["note"] or ""),
                        )
                        if marker is not None:
                            marker_visit = visit_raw_local_map.get(
                                _safe_db_id(marker.group(1)), 0
                            )
                        same_date = bool(
                            visit_followup_date
                            and str(appt_row["scheduled_at"] or "")[:10]
                            == visit_followup_date[:10]
                        )
                        if visit_is_new:
                            appointment_ok = bool(
                                int(appt_row["owner_visit_id"] or 0)
                                == int(visit_id)
                                or marker_visit == int(visit_id)
                                or same_date
                            )
                        else:
                            # 已存在的就诊可能已被本地改期：旧备份里的
                            # 已完成预约不能重新挂回，否则逾期复诊提示
                            # 会被永久抑制；日期仍一致时才恢复链接
                            appointment_ok = same_date
                    else:
                        appointment_ok = bool(
                            same_appt_profile
                            and appt_status == "upcoming"
                            and visit_followup_date
                            and str(appt_row["scheduled_at"] or "")[:10]
                            == visit_followup_date[:10]
                        )
                    if followup_appointment_id and appointment_ok:
                        # 同一自动预约已被另一条就诊占用：旧备份里的共享
                        # 链接不能重新建立，否则编辑任一就诊会连带删除
                        other_link = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_appointment_id = ? AND id != ?"
                            " LIMIT 1",
                            (int(followup_appointment_id), int(visit_id)),
                        ).fetchone()
                        if other_link is not None:
                            appointment_ok = False
                    if (
                        followup_appointment_id
                        and not appointment_ok
                        and appt_status == "upcoming"
                        and int(followup_appointment_id)
                        in imported_appointment_ids
                    ):
                        # 本次新插入的预约链接被拒绝时，其归属/备注标记
                        # 不能继续指向该就诊，否则会留下 owner 与链接
                        # 不一致的孤儿自动预约；导入前已存在的预约保留
                        # 既有归属映射（第四十二轮约定）
                        note_row = self._conn.execute(
                            "SELECT note FROM appointments WHERE id = ?",
                            (int(followup_appointment_id),),
                        ).fetchone()
                        if note_row is not None:
                            def _strip_visit_marker(match):
                                raw_visit = _safe_db_id(match.group(1))
                                if visit_raw_local_map.get(raw_visit) == int(
                                    visit_id
                                ):
                                    return ""
                                return match.group(0)

                            cleaned_note = re.sub(
                                r"由就诊记录 #(\d+) 自动创建",
                                _strip_visit_marker,
                                str(note_row["note"] or ""),
                            ).strip()[:300]
                            self._conn.execute(
                                "UPDATE appointments SET"
                                " owner_visit_id = CASE WHEN owner_visit_id = ?"
                                " THEN 0 ELSE owner_visit_id END,"
                                " note = ?, updated_at = ? WHERE id = ?",
                                (
                                    int(visit_id), cleaned_note, now,
                                    int(followup_appointment_id),
                                ),
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
                            # 只清理本次导入新插入的提醒：映射可能命中
                            # 导入前已存在的待办，不能误删用户在用提醒。
                            # 提醒仍挂在被保留的 upcoming 预约上时保留，
                            # 只解除就诊链接（第九十四轮 [10]）；脱钩
                            # 提醒延后到全部链接写回后再判定引用，共享
                            # 就诊可能全部拒绝链接（第九十六轮 [9]）
                            attach_row = self._conn.execute(
                                "SELECT appointment_id"
                                " FROM reminders WHERE id = ?",
                                (int(followup_reminder_id),),
                            ).fetchone()
                            reminder_attached = bool(
                                attach_row is not None
                                and int(attach_row["appointment_id"] or 0)
                            )
                            if not reminder_attached:
                                deferred_orphan_reminders.append(
                                    int(followup_reminder_id)
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
                    if (
                        not apply_links
                        and local_link == 0
                        and followup_reminder_id
                        and followup_reminder_id in imported_reminder_ids
                    ):
                        # 首遍导入拒绝链接时已清理的脱钩提醒，第二次
                        # 导入重新插入后同样不能留下没有任何就诊
                        # 引用的 pending 幽灵待办（第九十四轮 [10]）
                        attach_row = self._conn.execute(
                            "SELECT appointment_id FROM reminders"
                            " WHERE id = ?",
                            (int(followup_reminder_id),),
                        ).fetchone()
                        detached = bool(
                            attach_row is not None
                            and not int(
                                attach_row["appointment_id"] or 0
                            )
                        )
                        if detached:
                            # 脱钩提醒延后到全部链接写回后再判断是否
                            # 还有引用：共享者可能全部拒绝链接
                            # （第九十六轮 [9]）
                            deferred_orphan_reminders.append(
                                int(followup_reminder_id)
                            )
                            followup_reminder_id = 0
                    if (
                        not apply_links
                        and followup_reminder_id
                        and followup_reminder_id in imported_reminder_ids
                        and local_link
                    ):
                        # 本地就诊已挂预约：备份的脱钩提醒不能落成
                        # 无主 pending；与本地该预约的待办合并，或按
                        # 本地预约的新日期接管为该预约的提醒
                        # （第八十八/八十九轮）
                        other_ref = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_reminder_id = ?"
                            " AND id != ? LIMIT 1",
                            (int(followup_reminder_id), int(visit_id)),
                        ).fetchone()
                        appt_row = self._conn.execute(
                            "SELECT status, scheduled_at, department,"
                            " doctor FROM appointments WHERE id = ?",
                            (int(local_link),),
                        ).fetchone()
                        tombstone_row = self._conn.execute(
                            "SELECT 1 FROM app_state WHERE key = ?",
                            (
                                "reminder_deleted_"
                                f"{int(local_link)}",
                            ),
                        ).fetchone()
                        appt_usable = bool(
                            appt_row is not None
                            and str(appt_row["status"] or "")
                            == "upcoming"
                            and tombstone_row is None
                        )
                        if other_ref is None:
                            local_pending = self._conn.execute(
                                "SELECT id FROM reminders"
                                " WHERE appointment_id = ? AND done = 0"
                                " ORDER BY id LIMIT 1",
                                (int(local_link),),
                            ).fetchone()
                            if local_pending is not None or not appt_usable:
                                # 本地已有该预约的待办，或预约已取消/
                                # 已完成/被用户墓碑 opt-out：备份里的
                                # 旧日期提醒按重复项清理，不能复活
                                self._conn.execute(
                                    "DELETE FROM reminders WHERE id = ?"
                                    " AND done = 0 AND appointment_id = 0",
                                    (int(followup_reminder_id),),
                                )
                            else:
                                # 按本地预约的新日期重算提醒日期与
                                # 文案：不能原样挂回备份里的旧日期
                                appt_day = str(
                                    appt_row["scheduled_at"] or ""
                                )[:10]
                                due_day = ""
                                try:
                                    due_day = (
                                        datetime.strptime(
                                            appt_day, "%Y-%m-%d"
                                        ).date() - timedelta(days=1)
                                    ).strftime("%Y-%m-%d")
                                except ValueError:
                                    due_day = ""
                                if due_day and str(now)[:10] > due_day:
                                    due_day = str(now)[:10]
                                adopt_note = " ".join(
                                    part for part in (
                                        f"复诊 {appt_day}" if appt_day else "",
                                        str(
                                            appt_row["department"] or ""
                                        ),
                                        str(appt_row["doctor"] or ""),
                                    ) if part
                                )[:300]
                                # 覆盖就诊原链接前先清理其旧脱钩提醒，
                                # 避免制造无引用孤儿（仍被其它就诊
                                # 引用时保留）
                                old_link = (
                                    int(visit_row["followup_reminder_id"]
                                        or 0)
                                    if visit_row is not None else 0
                                )
                                if (
                                    old_link
                                    and old_link
                                    != int(followup_reminder_id)
                                ):
                                    old_other = self._conn.execute(
                                        "SELECT 1 FROM visit_records"
                                        " WHERE followup_reminder_id = ?"
                                        " AND id != ? LIMIT 1",
                                        (old_link, int(visit_id)),
                                    ).fetchone()
                                    if old_other is None:
                                        self._conn.execute(
                                            "DELETE FROM reminders"
                                            " WHERE id = ? AND done = 0"
                                            " AND appointment_id = 0",
                                            (old_link,),
                                        )
                                        self._conn.execute(
                                            "UPDATE visit_records"
                                            " SET followup_reminder_id = 0"
                                            " WHERE followup_reminder_id = ?",
                                            (old_link,),
                                        )
                                self._conn.execute(
                                    "UPDATE reminders SET appointment_id = ?,"
                                    " due_at = COALESCE(NULLIF(?, ''),"
                                    " due_at), note = COALESCE(NULLIF(?, ''),"
                                    " note), updated_at = ?"
                                    " WHERE id = ? AND done = 0"
                                    " AND appointment_id = 0",
                                    (
                                        int(local_link), due_day,
                                        adopt_note, now,
                                        int(followup_reminder_id),
                                    ),
                                )
                                self._conn.execute(
                                    "UPDATE visit_records"
                                    " SET followup_reminder_id = ?"
                                    " WHERE id = ?",
                                    (
                                        int(followup_reminder_id),
                                        int(visit_id),
                                    ),
                                )
                if apply_links:
                    restored_reminder = followup_reminder_id
                    old_reminder = 0
                    if visit_row is not None:
                        old_reminder = int(
                            visit_row["followup_reminder_id"] or 0
                        )
                    old_row = None
                    if old_reminder:
                        old_row = self._conn.execute(
                            "SELECT profile, done, appointment_id"
                            " FROM reminders WHERE id = ?",
                            (old_reminder,),
                        ).fetchone()
                    old_usable = bool(
                        old_row is not None
                        and visit_row is not None
                        and not int(old_row["done"] or 0)
                        and int(old_row["appointment_id"] or 0) == 0
                        and _normalize_profile_name(
                            old_row["profile"] or ""
                        ) == _normalize_profile_name(
                            visit_row["profile"] or ""
                        )
                    )
                    if old_usable and old_reminder != restored_reminder:
                        if restored_reminder:
                            # 目标提醒已存在：合并删除旧脱钩待办
                            # （仍被其它就诊引用时保留）
                            other_ref = self._conn.execute(
                                "SELECT 1 FROM visit_records"
                                " WHERE followup_reminder_id = ?"
                                " AND id != ? LIMIT 1",
                                (old_reminder, int(visit_id)),
                            ).fetchone()
                            if other_ref is None:
                                self._conn.execute(
                                    "DELETE FROM reminders WHERE id = ?"
                                    " AND done = 0 AND appointment_id = 0",
                                    (old_reminder,),
                                )
                        else:
                            target_pending = None
                            if followup_appointment_id:
                                target_pending = self._conn.execute(
                                    "SELECT id FROM reminders"
                                    " WHERE appointment_id = ? AND done = 0"
                                    " ORDER BY id LIMIT 1",
                                    (int(followup_appointment_id),),
                                ).fetchone()
                            if target_pending is not None:
                                restored_reminder = int(
                                    target_pending["id"]
                                )
                                other_ref = self._conn.execute(
                                    "SELECT 1 FROM visit_records"
                                    " WHERE followup_reminder_id = ?"
                                    " AND id != ? LIMIT 1",
                                    (old_reminder, int(visit_id)),
                                ).fetchone()
                                if other_ref is None:
                                    self._conn.execute(
                                        "DELETE FROM reminders"
                                        " WHERE id = ? AND done = 0"
                                        " AND appointment_id = 0",
                                        (old_reminder,),
                                    )
                            else:
                                # 目标预约没有提醒：把旧脱钩待办挂回
                                # 该预约，避免覆盖成无引用待办
                                if followup_appointment_id:
                                    self._conn.execute(
                                        "UPDATE reminders"
                                        " SET appointment_id = ?,"
                                        " updated_at = ?"
                                        " WHERE id = ? AND done = 0"
                                        " AND appointment_id = 0",
                                        (
                                            int(followup_appointment_id),
                                            now, old_reminder,
                                        ),
                                    )
                                restored_reminder = old_reminder
                    elif not restored_reminder and old_usable:
                        restored_reminder = old_reminder
                    self._conn.execute(
                        """
                        UPDATE visit_records
                           SET followup_appointment_id = ?,
                               followup_reminder_id = ?
                         WHERE id = ?
                        """,
                        (
                            followup_appointment_id,
                            restored_reminder,
                            visit_id,
                        ),
                    )
                if followup_appointment_id and raw_visit_id:
                    # 归属标记里是旧库就诊 id：无论链接是否写回都要
                    # 重映射备注，否则陈旧 #N 会让恢复/改期挂到错误的
                    # 就诊并产生重复预约
                    appointment_row = self._conn.execute(
                        "SELECT note, owner_visit_id FROM appointments"
                        " WHERE id = ?",
                        (int(followup_appointment_id),),
                    ).fetchone()
                    if appointment_row is not None:
                        owner_visit = int(
                            appointment_row["owner_visit_id"] or 0
                        )
                        if owner_visit and owner_visit != int(visit_id):
                            # 预约已归属另一条（本地未编辑的）就诊：
                            # 不能把备注标记改指到本次补插的重复就诊，
                            # 否则编辑/删除该就诊会级联删除原预约与
                            # 提醒
                            note_text = ""
                        else:
                            note_text = str(
                                appointment_row["note"] or ""
                            )
                        old_marker = (
                            f"由就诊记录 #{int(raw_visit_id)} 自动创建"
                        )
                        new_marker = (
                            f"由就诊记录 #{int(visit_id)} 自动创建"
                        )
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
                deleted = self._conn.execute(
                    "DELETE FROM appointments WHERE id = ?"
                    " AND status = 'upcoming'"
                    " AND id NOT IN (SELECT followup_appointment_id"
                    " FROM visit_records"
                    " WHERE followup_appointment_id != 0)"
                    # 有就诊从该预约补记（appointment_id 引用）时保留：
                    # 删除会留下悬空的就诊来源并删掉其待办提醒
                    " AND id NOT IN (SELECT appointment_id"
                    " FROM visit_records WHERE appointment_id != 0)",
                    (stale_id,),
                )
                if deleted.rowcount:
                    # 预约仍被本次新插入的就诊引用时不能删它的提醒：
                    # 就诊的提前提醒会凭空消失
                    doomed_reminders = [
                        int(row["id"]) for row in self._conn.execute(
                            "SELECT id FROM reminders"
                            " WHERE done = 0 AND appointment_id = ?",
                            (stale_id,),
                        ).fetchall()
                    ]
                    self._conn.execute(
                        "DELETE FROM reminders WHERE done = 0"
                        " AND appointment_id = ?",
                        (stale_id,),
                    )
                    # 已完成提醒是历史：不能继续指向已删除的预约
                    self._conn.execute(
                        "UPDATE reminders SET appointment_id = 0,"
                        " updated_at = ? WHERE done = 1"
                        " AND appointment_id = ?",
                        (now, stale_id),
                    )
                    if doomed_reminders:
                        doomed_ph = ",".join(
                            "?" for _ in doomed_reminders
                        )
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_reminder_id = 0"
                            " WHERE followup_reminder_id IN"
                            f" ({doomed_ph})",
                            doomed_reminders,
                        )

            # 备份恢复不能把提醒/复诊链接重新挂到已取消/已完成的预约：
            # 已取消/已完成预约都不应再有待办提醒（与 update_appointment
            # 的运行时行为一致）；复诊链接只在预约已取消或悬空时清空，
            # 已完成的预约是就诊历史，清链接会让已按时复诊重新报逾期
            if deferred_orphan_reminders:
                # 全部就诊链接写回后仍无任何引用的脱钩提醒按无主
                # pending 清理；仍被引用的保留（第九十六轮 [9]）
                for deferred_id in set(deferred_orphan_reminders):
                    deferred_ref = self._conn.execute(
                        "SELECT 1 FROM visit_records"
                        " WHERE followup_reminder_id = ? LIMIT 1",
                        (int(deferred_id),),
                    ).fetchone()
                    if deferred_ref is None:
                        self._conn.execute(
                            "DELETE FROM reminders WHERE id = ?"
                            " AND done = 0 AND appointment_id = 0",
                            (int(deferred_id),),
                        )
            doomed_pending_rows = self._conn.execute(
                "SELECT id FROM reminders WHERE done = 0"
                " AND appointment_id != 0 AND appointment_id NOT IN ("
                "SELECT id FROM appointments WHERE status = 'upcoming')"
            ).fetchall()
            self._conn.execute(
                "DELETE FROM reminders WHERE done = 0"
                " AND appointment_id != 0 AND appointment_id NOT IN ("
                "SELECT id FROM appointments WHERE status = 'upcoming')"
            )
            if doomed_pending_rows:
                doomed_pending = [
                    int(row["id"]) for row in doomed_pending_rows
                ]
                doomed_ph = ",".join("?" for _ in doomed_pending)
                self._conn.execute(
                    "UPDATE visit_records SET followup_reminder_id = 0"
                    f" WHERE followup_reminder_id IN ({doomed_ph})",
                    doomed_pending,
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

            # 就诊仍链接已完成预约时：脱钩 pending 提醒失去意义，
            # 不复诊就不该再提醒；保留预约链接作为历史，只清提醒
            # 链接并按需删除无引用提醒（第九十八轮 [11]）
            completed_linked_rows = self._conn.execute(
                "SELECT v.id, v.followup_reminder_id FROM visit_records v"
                " WHERE v.followup_appointment_id != 0 AND EXISTS ("
                "SELECT 1 FROM appointments a WHERE a.id ="
                " v.followup_appointment_id AND a.status = 'completed')"
            ).fetchall()
            for linked_row in completed_linked_rows:
                linked_reminder = int(
                    linked_row["followup_reminder_id"] or 0
                )
                if not linked_reminder:
                    continue
                self._clean_unlinked_detached_reminder(
                    linked_reminder, int(linked_row["id"])
                )
                self._conn.execute(
                    "UPDATE visit_records SET followup_reminder_id = 0"
                    " WHERE id = ?",
                    (int(linked_row["id"]),),
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
                    try:
                        profile = _normalize_profile_name(
                            raw.get("profile"), strict=True
                        )
                    except ValueError:
                        invalid += 1
                        continue
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
            row = self._conn.execute(
                "SELECT tags FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if row is None:
                return None
            raw_current = str(row["tags"] or "")
            if (
                DELETED_PROFILE_SESSION_TAG in raw_current
                and DELETED_PROFILE_SESSION_TAG not in normalized
            ):
                # 删除档案的内部标记不能被用户编辑标签清掉，否则
                # 已删除成员的会诊会重新被统计为「未指定」
                normalized.append(DELETED_PROFILE_SESSION_TAG)
            self._conn.execute(
                "UPDATE sessions SET tags = ? WHERE id = ?",
                (json.dumps(normalized, ensure_ascii=False), session_id),
            )
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
        try:
            name = _normalize_profile_name(name, strict=True)
        except ValueError:
            return None
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
            # 改名历史里的旧姓名同样属于隐私：沿改名链删除相关行，
            # 避免删除档案后旧姓名仍随导出/备份离开本机（第八十轮）
            chain_names = {name}
            frontier = [name]
            for _ in range(20):
                if not frontier:
                    break
                placeholders = ",".join("?" for _ in frontier)
                links = self._conn.execute(
                    "SELECT old_name, new_name FROM profile_renames"
                    f" WHERE old_name IN ({placeholders})"
                    f"    OR new_name IN ({placeholders})",
                    (*frontier, *frontier),
                ).fetchall()
                frontier = []
                for link in links:
                    for candidate in (
                        str(link["old_name"] or ""),
                        str(link["new_name"] or ""),
                    ):
                        key = _normalize_profile_name(candidate)
                        if key and key not in chain_names:
                            chain_names.add(key)
                            frontier.append(key)
            for chain_name in chain_names:
                self._conn.execute(
                    "DELETE FROM profile_renames WHERE old_name = ?"
                    " OR new_name = ?",
                    (chain_name, chain_name),
                )
            # 旧会话不再绑定已删除档案：否则继续提问会写回同名命名空间，
            # 重建同名档案后召回已删除患者的病史。打上标记让家庭总览/
            # 报表不再把该成员的会诊算到「未指定」名下
            rows = self._conn.execute(
                "SELECT id, tags FROM sessions WHERE profile = ?", (name,)
            ).fetchall()
            for row in rows:
                tags = _parse_tags(row["tags"])
                if DELETED_PROFILE_SESSION_TAG not in tags:
                    tags.append(DELETED_PROFILE_SESSION_TAG)
                self._conn.execute(
                    "UPDATE sessions SET profile = '', tags = ? WHERE id = ?",
                    (json.dumps(tags, ensure_ascii=False), row["id"]),
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
        # 与写入边界同一规则：含不可打印字符（零宽连接符等）的新名字
        # 会让该档案此后所有写入被 strict 规范化拒绝（第七十三轮审查）
        new_name = _normalize_profile_name(new_name, strict=True)

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
        day_count = None
        latest_daily_entry = None
        if metric in DAILY_ACCUMULATING_METRICS and value1 is not None:
            # 每日累计指标按条均值的「较上期」会被记录频次误导
            value1["prev_avg"] = None
            if profile is not None:
                # 平均/最低/最高同样按自然日合计：同日 4000+5000 不能
                # 显示成「平均 4500」而当日累计 9000；「全部」范围
                # （days=0）也按天统计
                daily_series = self.metric_daily_series(
                    profile, metric, days
                )
                daily_totals = [
                    float(entry["total"]) for entry in daily_series
                ]
                if daily_totals:
                    value1["avg"] = round(
                        sum(daily_totals) / len(daily_totals), 1
                    )
                    value1["min"] = round(min(daily_totals), 1)
                    value1["max"] = round(max(daily_totals), 1)
                    day_count = len(daily_totals)
                    daily_latest = max(
                        daily_series,
                        key=lambda entry: str(entry.get("day") or ""),
                    )
                    latest_daily_entry = {
                        **(dict(latest_row) if latest_row else {}),
                        "value1": float(daily_latest["total"]),
                        "value2": None,
                        "measured_at": str(daily_latest.get("day") or ""),
                    }
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
            "latest": (
                latest_daily_entry
                or (dict(latest_row) if latest_row else None)
            ),
            "day_count": day_count,
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
                "SELECT next_due, profile, vaccine_name, dose_number"
                " FROM vaccinations WHERE id = ?",
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
            # 档案变化/疫苗改名/剂次变化时同步提醒的档案与自动标题
            profile_changed = bool(
                previous is not None
                and _normalize_profile_name(
                    str(previous["profile"] or "")
                ) != _normalize_profile_name(normalized["profile"])
            )
            old_titles = (
                {
                    f"接种：{str(previous['vaccine_name'] or '') or '疫苗'}"
                    f"（第{int(previous['dose_number'] or 1) + 1}剂）"[:120],
                    # 兼容第七十四轮之前按已接种剂次生成的旧标题
                    f"接种：{str(previous['vaccine_name'] or '') or '疫苗'}"
                    f"（第{int(previous['dose_number'] or 1)}剂）"[:120],
                }
                if previous is not None else set()
            )
            new_title = (
                f"接种：{normalized['vaccine_name'] or '疫苗'}"
                f"（第{int(normalized['dose_number'] or 1) + 1}剂）"[:120]
            )
            old_title = (
                next(iter(old_titles)) if old_titles else ""
            )
            if profile_changed or old_title != new_title:
                reminder_rows = self._conn.execute(
                    "SELECT id, title FROM reminders"
                    " WHERE done = 0 AND vaccination_id = ?",
                    (int(vaccination_id),),
                ).fetchall()
                for reminder_row in reminder_rows:
                    updates = ["updated_at = ?"]
                    update_params: List[Any] = [now]
                    if profile_changed:
                        updates.append("profile = ?")
                        update_params.append(normalized["profile"])
                    if (
                        old_title != new_title
                        and str(reminder_row["title"] or "") in old_titles
                    ):
                        updates.append("title = ?")
                        update_params.append(new_title)
                    update_params.append(int(reminder_row["id"]))
                    self._conn.execute(
                        f"UPDATE reminders SET {', '.join(updates)}"
                        " WHERE id = ?",
                        update_params,
                    )
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
            vaccination = self._conn.execute(
                "SELECT profile FROM vaccinations WHERE id = ?",
                (int(vaccination_id),),
            ).fetchone()
            if vaccination is None:
                return False
            self._conn.execute(
                "DELETE FROM vaccinations WHERE id = ?",
                (int(vaccination_id),),
            )
            self._conn.execute(
                "DELETE FROM reminders WHERE done = 0"
                " AND vaccination_id = ?",
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
        self,
        note: Any,
        owner_visit_id: Any,
        profile: str = "",
        scheduled_at: str = "",
        check_date: bool = True,
    ) -> int:
        """解析预约归属就诊：持久 owner 优先，悬空时回退到备注标记。

        备注可被用户编辑、导入时也可能残留旧库 id；标记只在就诊真实
        存在、档案一致且（恢复/改期场景）日期与预约一致时才采用，
        避免改期静默覆盖无关就诊的复诊日期。
        """
        owner = _safe_db_id(owner_visit_id)
        marker = re.search(
            r"由就诊记录 #(\d+) 自动创建", str(note or "")
        )
        marker_id = (
            _safe_db_id(marker.group(1)) if marker is not None else 0
        )
        scheduled_day = str(scheduled_at or "")[:10]

        def _visit_row(visit_id: int):
            if not visit_id:
                return None
            row = self._conn.execute(
                "SELECT profile, next_followup_date"
                " FROM visit_records WHERE id = ?",
                (visit_id,),
            ).fetchone()
            if row is None:
                return None
            if profile and _normalize_profile_name(
                str(row["profile"] or "")
            ) != profile:
                return None
            return row

        owner_row = _visit_row(owner)
        if owner_row is not None:
            return owner
        marker_row = _visit_row(marker_id)
        if marker_row is None:
            return 0
        if check_date and scheduled_day:
            visit_day = str(marker_row["next_followup_date"] or "")[:10]
            if visit_day and visit_day != scheduled_day:
                # 标记指向的就诊复诊日期与本次预约不一致：
                # 不能据此改写该就诊（第四十三轮的改期约束）
                return 0
        return marker_id

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
                    normalized["scheduled_at"],
                    # 恢复校验必须先解析归属再做日期/重复校验：
                    # 改日期恢复不能被日期一致性提前放行
                    check_date=False,
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
                        # 导入恢复常留下「owner/备注指向本就诊、但链接
                        # 未写回」的待就诊自动预约：只校验链接会漏掉
                        # 它们，恢复后同一就诊出现两条复诊预约与两条
                        # 待办提醒（第九十九轮 [9]）
                        marker = (
                            f"由就诊记录 #{int(restore_visit_id)}"
                            " 自动创建"
                        )
                        owned_upcoming = self._conn.execute(
                            "SELECT 1 FROM appointments"
                            " WHERE status = 'upcoming' AND id != ?"
                            "   AND profile = ?"
                            "   AND (owner_visit_id = ?"
                            "        OR (owner_visit_id = 0 AND note LIKE ?))"
                            " LIMIT 1",
                            (
                                int(appointment_id),
                                linked_visit["profile"],
                                int(restore_visit_id),
                                f"%{marker}%",
                            ),
                        ).fetchone()
                        if owned_upcoming is not None:
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
            # 预约改档案时提醒跟随；档案未变时不刷新 updated_at，
            # 否则已完成提醒的时间戳会晚于删除墓碑，改期重建会把
            # 用户已删除的提醒复活。已完成提醒是历史：只迁移档案，
            # 不刷新 updated_at（第一百轮 [8]）
            self._conn.execute(
                "UPDATE reminders SET profile = ?, updated_at = ?"
                " WHERE appointment_id = ? AND profile != ? AND done = 0",
                (
                    normalized["profile"], now, int(appointment_id),
                    normalized["profile"],
                ),
            )
            self._conn.execute(
                "UPDATE reminders SET profile = ?"
                " WHERE appointment_id = ? AND profile != ? AND done = 1",
                (
                    normalized["profile"], int(appointment_id),
                    normalized["profile"],
                ),
            )
            profile_changed = (
                _normalize_profile_name(str(previous["profile"] or ""))
                != normalized["profile"]
            )
            if profile_changed:
                # 另一位成员的就诊记录不能继续把这条预约当「已安排
                # 复诊」：清掉不匹配档案的链接与归属，避免删除/改期
                # 该就诊时连带操作别人的预约
                other_links = self._conn.execute(
                    "SELECT id, profile, followup_reminder_id"
                    " FROM visit_records"
                    " WHERE followup_appointment_id = ?"
                    "   AND profile != ?",
                    (int(appointment_id), normalized["profile"]),
                ).fetchall()
                for other_link in other_links:
                    keep_reminder = 0
                    linked_reminder = int(
                        other_link["followup_reminder_id"] or 0
                    )
                    if linked_reminder:
                        linked_row = self._conn.execute(
                            "SELECT profile, done, appointment_id"
                            " FROM reminders WHERE id = ?",
                            (linked_reminder,),
                        ).fetchone()
                        if (
                            linked_row is not None
                            and not int(linked_row["done"] or 0)
                            and int(linked_row["appointment_id"] or 0) == 0
                            and _normalize_profile_name(
                                linked_row["profile"] or ""
                            ) == _normalize_profile_name(
                                other_link["profile"] or ""
                            )
                        ):
                            # 合法的「仅提醒链接」状态：只清预约链接，
                            # 保留脱钩待办交给后续安排复诊接管，避免
                            # 无引用 pending 与重复提醒（第八十六轮）
                            keep_reminder = linked_reminder
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET followup_appointment_id = 0,"
                        " followup_reminder_id = ?, updated_at = ?"
                        " WHERE id = ?",
                        (keep_reminder, now, int(other_link["id"])),
                    )
                self._conn.execute(
                    "UPDATE appointments SET owner_visit_id = 0"
                    " WHERE id = ? AND owner_visit_id != 0 AND"
                    " owner_visit_id NOT IN ("
                    "SELECT id FROM visit_records WHERE profile = ?)",
                    (int(appointment_id), normalized["profile"]),
                )
                # 备注里的「由就诊记录 #N 自动创建」标记若指向其他
                # 成员（或已不存在）的就诊，一并剥离：否则该就诊之后
                # 改/删档案时会按标记把这条预约再抢走、甚至级联删除
                # 新成员的预约与提醒
                note_text = str(normalized["note"] or "")
                marker_hits = list(
                    re.finditer(r"由就诊记录 #(\d+) 自动创建", note_text)
                )
                if marker_hits:
                    cleaned_note = note_text
                    for match in marker_hits:
                        marker_visit = _safe_db_id(match.group(1))
                        marker_owner = (
                            self._conn.execute(
                                "SELECT profile FROM visit_records"
                                " WHERE id = ?",
                                (marker_visit,),
                            ).fetchone()
                            if marker_visit else None
                        )
                        if marker_owner is None or (
                            _normalize_profile_name(
                                str(marker_owner["profile"] or "")
                            )
                            != normalized["profile"]
                        ):
                            cleaned_note = cleaned_note.replace(
                                match.group(0), ""
                            )
                    cleaned_note = " ".join(cleaned_note.split())[:300]
                    if cleaned_note != note_text:
                        self._conn.execute(
                            "UPDATE appointments SET note = ? WHERE id = ?",
                            (cleaned_note, int(appointment_id)),
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
            # 过期自动复诊预约不能恢复：必须在任何写操作（含提醒
            # 接管的内层提交）之前失败，否则接口返回 400 但预约已
            # 被改为待就诊、提醒被复活（第八十四轮）
            _was_upcoming_early = (
                str(previous["status"] or "") == "upcoming"
            )
            if (
                normalized["status"] == "upcoming"
                and not _was_upcoming_early
                and reminder_due
            ):
                _auto_followup_early = bool(
                    int(previous["owner_visit_id"] or 0)
                ) or "由就诊记录 #" in str(previous["note"] or "")
                _scheduled_day_early = str(
                    normalized["scheduled_at"] or ""
                )[:10]
                if (
                    _auto_followup_early
                    and _scheduled_day_early
                    and _scheduled_day_early < str(now)[:10]
                ):
                    raise ValueError("预约日期已过期，请先修改日期")

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

            def _schedule_key(value: Any) -> str:
                text = str(value or "").strip()
                if not text:
                    return ""
                return _canonical_local_time(text) or text

            schedule_date_changed = (
                _schedule_key(previous["scheduled_at"])[:10]
                != _schedule_key(normalized["scheduled_at"])[:10]
            )
            schedule_changed = (
                schedule_date_changed
                or str(previous["status"] or "") != normalized["status"]
            )
            if normalized["status"] != "upcoming":
                # 仍被其它就诊引用的共享提醒只脱钩、不删除，否则
                # 会删掉别人唯一的复诊待办并留下悬空链接
                # （第九十轮 [14]）
                pending_rows = self._conn.execute(
                    "SELECT id FROM reminders"
                    " WHERE done = 0 AND appointment_id = ?",
                    (int(appointment_id),),
                ).fetchall()
                for pending_row in pending_rows:
                    pending_id = int(pending_row["id"] or 0)
                    if not pending_id:
                        continue
                    shared_ref = self._conn.execute(
                        "SELECT 1 FROM visit_records"
                        " WHERE followup_reminder_id = ?"
                        " AND followup_appointment_id != ?"
                        " LIMIT 1",
                        (pending_id, int(appointment_id)),
                    ).fetchone()
                    if shared_ref is not None:
                        self._conn.execute(
                            "UPDATE reminders SET appointment_id = 0,"
                            " updated_at = ? WHERE id = ?",
                            (now, pending_id),
                        )
                    else:
                        self._conn.execute(
                            "DELETE FROM reminders WHERE id = ?",
                            (pending_id,),
                        )
                # 已完成提醒不再指向已取消/已完成的预约，避免被重新
                # 打开后复活成悬空待办
                self._conn.execute(
                    "UPDATE reminders SET appointment_id = 0, updated_at = ?"
                    " WHERE done = 1 AND appointment_id = ?",
                    (now, int(appointment_id)),
                )
                # 就诊仍链接的脱钩待办（reopen 降级后 appointment_id=0）
                # 随取消/完成一并删除，否则预约取消后仍留下幽灵提醒
                linked_rows = self._conn.execute(
                    "SELECT DISTINCT followup_reminder_id FROM visit_records"
                    " WHERE followup_appointment_id = ?"
                    " AND followup_reminder_id != 0",
                    (int(appointment_id),),
                ).fetchall()
                for link_row in linked_rows:
                    linked_id = int(link_row["followup_reminder_id"] or 0)
                    if linked_id:
                        other_ref = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_reminder_id = ?"
                            " AND followup_appointment_id != ?"
                            " LIMIT 1",
                            (linked_id, int(appointment_id)),
                        ).fetchone()
                        if other_ref is not None:
                            # 其它就诊（挂别的预约）仍引用这条共享
                            # 脱钩提醒：保留提醒，只解除本预约就诊的
                            # 链接，否则会删掉别人唯一的复诊提醒并
                            # 留下悬空 followup_reminder_id
                            # （第八十九轮 [11]）
                            continue
                        self._conn.execute(
                            "DELETE FROM reminders WHERE id = ? AND done = 0"
                            " AND appointment_id = 0",
                            (linked_id,),
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
                    # 同一预约上可能挂着多条日期不同的 pending 提醒
                    # （导入层把它当合法数据）：按预约日期变化的天数
                    # 整体平移，保留各自的独立日期，不能统一钉到
                    # 新日期前一天（第九十四轮 [15]）
                    old_day = _schedule_key(
                        previous["scheduled_at"]
                    )[:10]
                    new_day = _schedule_key(
                        normalized["scheduled_at"]
                    )[:10]
                    day_delta = None
                    if old_day and new_day:
                        try:
                            day_delta = (
                                datetime.fromisoformat(new_day).date()
                                - datetime.fromisoformat(old_day).date()
                            ).days
                        except ValueError:
                            day_delta = None
                    pending_due_rows = self._conn.execute(
                        "SELECT id, due_at FROM reminders"
                        " WHERE done = 0 AND appointment_id = ?",
                        (int(appointment_id),),
                    ).fetchall()
                    updated_count = 0
                    for due_row in pending_due_rows:
                        shifted_due = ""
                        if day_delta:
                            raw_due = str(due_row["due_at"] or "")
                            try:
                                due_day = datetime.fromisoformat(
                                    raw_due[:10]
                                ).date()
                                shifted_due = (
                                    due_day + timedelta(days=day_delta)
                                ).strftime("%Y-%m-%d")
                            except (ValueError, OverflowError):
                                shifted_due = ""
                        if shifted_due and shifted_due < str(now)[:10]:
                            # 平移结果不能早于今天：与其它提醒日期
                            # 计算路径的 max(…, 今天) 钳制一致
                            # （第九十五轮 [17]）
                            shifted_due = reminder_due
                        due_cursor = self._conn.execute(
                            "UPDATE reminders SET due_at = ?,"
                            " updated_at = ? WHERE id = ?",
                            (
                                shifted_due or reminder_due, now,
                                int(due_row["id"]),
                            ),
                        )
                        updated_count += int(due_cursor.rowcount or 0)
                    # 就诊仍链接的脱钩待办同样随改期更新日期
                    detached_updated = 0
                    linked_rows = self._conn.execute(
                        "SELECT DISTINCT followup_reminder_id"
                        " FROM visit_records"
                        " WHERE followup_appointment_id = ?"
                        " AND followup_reminder_id != 0",
                        (int(appointment_id),),
                    ).fetchall()
                    for link_row in linked_rows:
                        linked_id = int(
                            link_row["followup_reminder_id"] or 0
                        )
                        if not linked_id:
                            continue
                        shared_detached = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_reminder_id = ?"
                            " AND followup_appointment_id != ? LIMIT 1",
                            (linked_id, int(appointment_id)),
                        ).fetchone()
                        if shared_detached is not None:
                            # 共享脱钩提醒被其它就诊使用：改期不能把它
                            # 改到本预约的日期（第九十二轮 [10]）
                            continue
                        appointment_pending = self._conn.execute(
                            "SELECT id FROM reminders"
                            " WHERE appointment_id = ? AND done = 0"
                            " ORDER BY due_at ASC, id ASC LIMIT 1",
                            (int(appointment_id),),
                        ).fetchone()
                        if appointment_pending is not None and int(
                            appointment_pending["id"]
                        ) != linked_id:
                            # 预约已有自己的待办：合并删除旧的脱钩提醒
                            # 并把就诊链接改指预约待办，避免同一次复诊
                            # 两条 pending 且就诊挂旧日期
                            # （第一百零二轮 [15]）
                            self._conn.execute(
                                "DELETE FROM reminders WHERE id = ?"
                                " AND done = 0 AND appointment_id = 0",
                                (linked_id,),
                            )
                            pending_note_row = self._conn.execute(
                                "SELECT note FROM reminders WHERE id = ?",
                                (int(appointment_pending["id"]),),
                            ).fetchone()
                            pending_note = (
                                str(pending_note_row["note"] or "")
                                if pending_note_row is not None else ""
                            )
                            merged_note = re.sub(
                                r"^复诊\s*\d{4}-\d{2}-\d{2}",
                                f"复诊 {str(normalized['scheduled_at'] or '')[:10]}",
                                pending_note,
                            )
                            if not merged_note.strip():
                                merged_note = (
                                    "复诊 "
                                    + str(
                                        normalized["scheduled_at"] or ""
                                    )[:10]
                                )
                            self._conn.execute(
                                "UPDATE reminders SET note = ?,"
                                " updated_at = ? WHERE id = ?",
                                (
                                    merged_note, now,
                                    int(appointment_pending["id"]),
                                ),
                            )
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_reminder_id = ?,"
                                " updated_at = ?"
                                " WHERE followup_reminder_id = ?",
                                (
                                    int(appointment_pending["id"]), now,
                                    linked_id,
                                ),
                            )
                            detached_updated += 1
                            continue
                        linked_note = self._conn.execute(
                            "SELECT note FROM reminders WHERE id = ?"
                            " AND done = 0 AND appointment_id = 0",
                            (linked_id,),
                        ).fetchone()
                        note_text = (
                            str(linked_note["note"] or "")
                            if linked_note is not None else ""
                        )
                        appointment_day = (
                            str(normalized["scheduled_at"] or "")[:10]
                            or str(reminder_due)[:10]
                        )
                        refreshed_note = re.sub(
                            r"^复诊\s*\d{4}-\d{2}-\d{2}",
                            f"复诊 {appointment_day}",
                            note_text,
                        )
                        cursor = self._conn.execute(
                            "UPDATE reminders SET due_at = ?, note = ?,"
                            " updated_at = ?"
                            " WHERE id = ? AND done = 0"
                            " AND appointment_id = 0",
                            (reminder_due, refreshed_note, now, linked_id),
                        )
                        detached_updated += int(cursor.rowcount or 0)
                    if updated_count == 0 and not detached_updated:
                        # 该预约唯一的提醒已被勾选完成：改期后必须重建
                        # 待办，否则复诊前没有任何提醒。从未创建、已被
                        # 用户删除的提醒不能因改期复活：优先按就诊上的
                        # 提醒链接判断（删除时链接已被清空）
                        link_row = self._conn.execute(
                            "SELECT followup_reminder_id FROM visit_records"
                            " WHERE followup_appointment_id = ? LIMIT 1",
                            (int(appointment_id),),
                        ).fetchone()
                        linked_reminder_id = (
                            int(link_row["followup_reminder_id"] or 0)
                            if link_row is not None else 0
                        )
                        rebuild = False
                        if linked_reminder_id:
                            linked_alive = self._conn.execute(
                                "SELECT done FROM reminders WHERE id = ?",
                                (linked_reminder_id,),
                            ).fetchone()
                            rebuild = bool(
                                linked_alive is not None
                                and int(linked_alive["done"] or 1) == 1
                            )
                        if not rebuild:
                            # 没有就诊链接的普通预约，或就诊链接仍在但
                            # 提醒链接已被删除清空（用户用『提醒』按钮
                            # 重新建过）：以是否有已完成提醒判断。就诊
                            # 链接存在且从未重建过提醒时，用户主动删除
                            # 后不能再复活；重新建过并完成则改期必须
                            # 重建待办，否则复诊前没有任何提醒
                            had_completed = self._conn.execute(
                                "SELECT 1 FROM reminders"
                                " WHERE appointment_id = ? AND done = 1"
                                " LIMIT 1",
                                (int(appointment_id),),
                            ).fetchone()
                            rebuild = had_completed is not None
                        if not rebuild:
                            # 已完成提醒被「清理已完成」删除后：自动
                            # 复诊预约仍存在、就诊仍安排着，只是没有
                            # 任何待办提醒——没有 opt-out 墓碑时必须
                            # 重建，否则「已安排复诊」却永远不会提醒
                            # （第八十二轮）
                            auto_owned = bool(
                                int(previous["owner_visit_id"] or 0)
                                or re.search(
                                    r"由就诊记录 #\d+ 自动创建",
                                    str(previous["note"] or ""),
                                )
                            )
                            pending_row = self._conn.execute(
                                "SELECT 1 FROM reminders"
                                " WHERE appointment_id = ? AND done = 0"
                                " LIMIT 1",
                                (int(appointment_id),),
                            ).fetchone()
                            if auto_owned and pending_row is None:
                                try:
                                    auto_marker = self.get_app_state(
                                        "reminder_deleted_"
                                        f"{int(appointment_id)}"
                                    )
                                except sqlite3.Error:
                                    auto_marker = ""
                                if not auto_marker:
                                    rebuild = True
                        if rebuild:
                            # 用户主动删除过该预约的提醒（重建后又删）：
                            # 删除墓碑不早于任何已完成提醒时不再复活。
                            # 该闸门必须覆盖「就诊仍链接到已完成提醒」
                            # 分支，「提醒」按钮创建的提醒不会回写
                            # 就诊链接，旧链接不能绕过墓碑
                            try:
                                marker = self.get_app_state(
                                    "reminder_deleted_"
                                    f"{int(appointment_id)}"
                                )
                            except sqlite3.Error:
                                marker = ""
                            if marker:
                                latest_done = self._conn.execute(
                                    "SELECT MAX(updated_at) AS latest"
                                    " FROM reminders"
                                    " WHERE appointment_id = ?"
                                    " AND done = 1",
                                    (int(appointment_id),),
                                ).fetchone()
                                latest_text = (
                                    str(latest_done["latest"] or "")
                                    if latest_done is not None else ""
                                )
                                if (
                                    not latest_text
                                    or latest_text <= marker
                                ):
                                    rebuild = False
                        adopted_id = 0
                        if rebuild:
                            # 导入的「仅提醒链接」脱钩待办：恢复/改期时
                            # 优先接管就诊仍链接的那条提醒，不能再建
                            # 一条同名提醒（删就诊后旧提醒变幽灵）
                            # （第八十三轮 [16]）
                            adopt_visit = self._appointment_owner_visit(
                                previous["note"],
                                previous["owner_visit_id"],
                                normalized["profile"],
                                normalized["scheduled_at"],
                                check_date=False,
                            )
                            if adopt_visit:
                                adopt_row = self._conn.execute(
                                    "SELECT followup_reminder_id,"
                                    " followup_appointment_id"
                                    " FROM visit_records WHERE id = ?",
                                    (int(adopt_visit),),
                                ).fetchone()
                                adopt_reminder = (
                                    int(adopt_row["followup_reminder_id"] or 0)
                                    if adopt_row is not None else 0
                                )
                                # 就诊已链接另一条待就诊预约时，这条
                                # 提醒属于那条复诊计划：本预约改期不能
                                # 把它劫持过来（否则取消原预约后幽灵
                                # 提醒继续触发，第一百零四轮 [10]）
                                adopt_linked = (
                                    int(adopt_row["followup_appointment_id"] or 0)
                                    if adopt_row is not None else 0
                                )
                                if (
                                    adopt_reminder
                                    and reminder_due
                                    and adopt_linked
                                    in (0, int(appointment_id))
                                ):
                                    adopt_day = str(
                                        normalized["scheduled_at"] or ""
                                    )[:10]
                                    adopt_note = " ".join(
                                        part for part in (
                                            f"复诊 {adopt_day}",
                                            normalized["department"],
                                            normalized["doctor"],
                                        ) if part
                                    )[:300]
                                    if adopt_note and (
                                        self.adopt_orphan_followup_reminder(
                                            adopt_reminder,
                                            int(appointment_id),
                                            reminder_due,
                                            adopt_note,
                                            normalized["profile"],
                                            _in_transaction=True,
                                            owner_visit_id=adopt_visit,
                                        )
                                    ):
                                        adopted_id = adopt_reminder
                        rebuilt_id = (
                            adopted_id
                            or (_create_reminder() if rebuild else 0)
                        )
                        if rebuilt_id:
                            # 系统刚按用户意愿重建提醒：删除墓碑必须
                            # 撤销，否则取消→恢复待就诊时又被墓碑拦住
                            self._conn.execute(
                                "DELETE FROM app_state WHERE key = ?",
                                (f"reminder_deleted_{int(appointment_id)}",),
                            )
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
                            if adopted_id and adopt_visit:
                                # 归属就诊仅靠 owner/备注标记关联（fa=0）：
                                # 接管成功后一并写回预约链接，避免
                                # followup_scheduled 仍为 false
                                # （第九十二轮 [13]）
                                self._conn.execute(
                                    "UPDATE visit_records"
                                    " SET followup_appointment_id = ?,"
                                    " updated_at = ?"
                                    " WHERE id = ?"
                                    " AND followup_appointment_id = 0",
                                    (
                                        int(appointment_id), now,
                                        int(adopt_visit),
                                    ),
                                )
                else:
                    self._conn.execute(
                        "DELETE FROM reminders "
                        "WHERE done = 0 AND appointment_id = ?",
                        (int(appointment_id),),
                    )
            if (
                normalized["status"] in ("cancelled", "completed")
                and str(previous["status"] or "") == "upcoming"
            ):
                # 导入遗留的「owner/备注归属但链接未写回」就诊（fa=0）
                # 仍可能挂着脱钩复诊提醒：取消/完成后必须一并清理，
                # 否则提醒按原日期继续触发（第一百零二轮 [9]）
                owner_visit = self._appointment_owner_visit(
                    previous["note"],
                    previous["owner_visit_id"],
                    normalized["profile"],
                    normalized["scheduled_at"],
                    check_date=False,
                )
                if owner_visit:
                    owner_row = self._conn.execute(
                        "SELECT followup_reminder_id, profile"
                        " FROM visit_records WHERE id = ?",
                        (int(owner_visit),),
                    ).fetchone()
                    if owner_row is not None and (
                        _normalize_profile_name(
                            owner_row["profile"] or ""
                        )
                        == _normalize_profile_name(
                            normalized["profile"]
                        )
                    ):
                        owner_reminder = int(
                            owner_row["followup_reminder_id"] or 0
                        )
                        if owner_reminder:
                            self._clean_unlinked_detached_reminder(
                                owner_reminder, int(owner_visit)
                            )
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_reminder_id = 0"
                                " WHERE id = ?"
                                " AND followup_reminder_id = ?",
                                (int(owner_visit), owner_reminder),
                            )
                # 预约取消/完成后原复诊提醒已脱钩或删除：就诊上的
                # 提醒链接必须清空，避免之后重开提醒再被误删
                self._conn.execute(
                    "UPDATE visit_records SET followup_reminder_id = 0"
                    " WHERE followup_appointment_id = ?",
                    (int(appointment_id),),
                )
            if (
                normalized["status"] == "completed"
                and str(previous["status"] or "") in (
                    "cancelled", "upcoming"
                )
            ):
                # 取消/待就诊标记「已完成」：该预约仍是这次复诊的
                # 实际就诊记录，日期与就诊一致且就诊无其它待就诊
                # 链接时重新回挂，避免逾期复诊提示与重复安排
                # （upcoming→completed 的导入遗留半挂接见第一百零
                # 一轮 [10]）
                owner_visit_id = self._appointment_owner_visit(
                    previous["note"],
                    previous["owner_visit_id"],
                    normalized["profile"],
                    normalized["scheduled_at"],
                )
                if owner_visit_id:
                    visit_row = self._conn.execute(
                        "SELECT next_followup_date,"
                        " followup_appointment_id, followup_reminder_id"
                        " FROM visit_records WHERE id = ?",
                        (int(owner_visit_id),),
                    ).fetchone()
                    if visit_row is not None:
                        visit_day = str(
                            visit_row["next_followup_date"] or ""
                        )[:10]
                        other_link = int(
                            visit_row["followup_appointment_id"] or 0
                        )
                        if (
                            visit_day
                            and visit_day == str(
                                normalized["scheduled_at"] or ""
                            )[:10]
                            and other_link in (0, int(appointment_id))
                        ):
                            # 就诊仍链接的脱钩待办随「已完成」一并清理：
                            # 回挂会清零提醒链接，之后删就诊/删预约都
                            # 级联不到它，会留下永久幽灵提醒
                            # （第八十四轮）
                            stale_reminder = int(
                                visit_row["followup_reminder_id"] or 0
                            )
                            if stale_reminder:
                                stale_row = self._conn.execute(
                                    "SELECT done, appointment_id, profile"
                                    " FROM reminders WHERE id = ?",
                                    (stale_reminder,),
                                ).fetchone()
                                if (
                                    stale_row is not None
                                    and not int(stale_row["done"] or 0)
                                    and int(
                                        stale_row["appointment_id"] or 0
                                    ) == 0
                                    and _normalize_profile_name(
                                        stale_row["profile"] or ""
                                    ) == _normalize_profile_name(
                                        normalized["profile"]
                                    )
                                ):
                                    shared_stale = self._conn.execute(
                                        "SELECT 1 FROM visit_records"
                                        " WHERE followup_reminder_id = ?"
                                        " AND id != ? LIMIT 1",
                                        (
                                            stale_reminder,
                                            int(owner_visit_id),
                                        ),
                                    ).fetchone()
                                    if shared_stale is None:
                                        self._conn.execute(
                                            "DELETE FROM reminders"
                                            " WHERE id = ? AND done = 0"
                                            " AND appointment_id = 0",
                                            (stale_reminder,),
                                        )
                                        self._conn.execute(
                                            "UPDATE visit_records"
                                            " SET followup_reminder_id = 0"
                                            " WHERE followup_reminder_id = ?",
                                            (stale_reminder,),
                                        )
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_appointment_id = ?,"
                                " followup_reminder_id = 0,"
                                " updated_at = ? WHERE id = ?",
                                (
                                    int(appointment_id), now,
                                    int(owner_visit_id),
                                ),
                            )
            # 取消/完成后恢复待就诊：待办提醒在取消时已被删除，按当前
            # 预约字段重建一条（手改文案无法找回，但至少不会漏提醒）
            was_upcoming = str(previous["status"] or "") == "upcoming"
            if (
                normalized["status"] == "upcoming"
                and not was_upcoming
                and reminder_due
            ):
                auto_followup = bool(
                    int(previous["owner_visit_id"] or 0)
                ) or "由就诊记录 #" in str(previous["note"] or "")
                scheduled_day = str(
                    normalized["scheduled_at"] or ""
                )[:10]
                today_day = str(now)[:10]
                if (
                    auto_followup
                    and scheduled_day
                    and scheduled_day < today_day
                ):
                    # 兜底：早期校验已覆盖该分支（第八十四轮）
                    raise ValueError("预约日期已过期，请先修改日期")
                existing = self._conn.execute(
                    "SELECT id FROM reminders WHERE done = 0"
                    " AND appointment_id = ? LIMIT 1",
                    (int(appointment_id),),
                ).fetchone()
                restored_reminder_id = (
                    int(existing["id"]) if existing is not None else 0
                )
                if existing is None:
                    deleted_marker = ""
                    try:
                        deleted_marker = str(
                            self.get_app_state(
                                "reminder_deleted_"
                                f"{int(appointment_id)}"
                            ) or ""
                        )
                    except sqlite3.Error:
                        deleted_marker = ""
                    visit_owner = self._appointment_owner_visit(
                        previous["note"],
                        previous["owner_visit_id"],
                        normalized["profile"],
                        normalized["scheduled_at"],
                        check_date=False,
                    )
                    if deleted_marker:
                        # 用户主动删除过该预约的提醒：恢复待就诊不能
                        # 绕过墓碑复活，只恢复预约本身；就诊仍链接的
                        # 脱钩待办随墓碑一并清理，避免无引用幽灵
                        # （第八十八轮）
                        restored_reminder_id = 0
                        if visit_owner:
                            linked_row = self._conn.execute(
                                "SELECT followup_reminder_id"
                                " FROM visit_records WHERE id = ?",
                                (int(visit_owner),),
                            ).fetchone()
                            tomb_reminder = (
                                int(linked_row["followup_reminder_id"] or 0)
                                if linked_row is not None else 0
                            )
                            if tomb_reminder:
                                tomb_row = self._conn.execute(
                                    "SELECT profile, done, appointment_id"
                                    " FROM reminders WHERE id = ?",
                                    (tomb_reminder,),
                                ).fetchone()
                                if (
                                    tomb_row is not None
                                    and not int(tomb_row["done"] or 0)
                                    and int(
                                        tomb_row["appointment_id"] or 0
                                    ) == 0
                                    and _normalize_profile_name(
                                        tomb_row["profile"] or ""
                                    ) == _normalize_profile_name(
                                        normalized["profile"]
                                    )
                                ):
                                    shared_tomb = self._conn.execute(
                                        "SELECT 1 FROM visit_records"
                                        " WHERE followup_reminder_id = ?"
                                        " AND id != ? LIMIT 1",
                                        (
                                            tomb_reminder,
                                            int(visit_owner),
                                        ),
                                    ).fetchone()
                                    if shared_tomb is None:
                                        # 仅本就诊引用时才随墓碑删除；
                                        # 仍被其它就诊共享时保留，只让
                                        # 墓碑对本预约生效（第九十一轮 [5]）
                                        self._conn.execute(
                                            "DELETE FROM reminders"
                                            " WHERE id = ? AND done = 0"
                                            " AND appointment_id = 0",
                                            (tomb_reminder,),
                                        )
                                        self._conn.execute(
                                            "UPDATE visit_records"
                                            " SET followup_reminder_id = 0"
                                            " WHERE followup_reminder_id = ?",
                                            (tomb_reminder,),
                                        )
                    else:
                        restored_reminder_id = 0
                        # 导入的「仅提醒链接」脱钩待办：接管它，否则
                        # 会再建一条同名提醒、删就诊后留下幽灵
                        # （第八十三轮）
                        orphan_id = 0
                        if visit_owner:
                            linked_row = self._conn.execute(
                                "SELECT followup_reminder_id"
                                " FROM visit_records WHERE id = ?",
                                (int(visit_owner),),
                            ).fetchone()
                            orphan_id = (
                                int(linked_row["followup_reminder_id"] or 0)
                                if linked_row is not None else 0
                            )
                        if orphan_id and scheduled_day:
                            try:
                                due_date = datetime.strptime(
                                    scheduled_day, "%Y-%m-%d"
                                ).date() - timedelta(days=1)
                                today_date = datetime.strptime(
                                    today_day, "%Y-%m-%d"
                                ).date()
                                if due_date < today_date:
                                    due_date = today_date
                                adopt_note = " ".join(
                                    part for part in (
                                        f"复诊 {scheduled_day}",
                                        normalized.get("department") or "",
                                        normalized.get("doctor") or "",
                                    ) if part
                                )[:300]
                            except ValueError:
                                adopt_note = ""
                            if adopt_note and (
                                self.adopt_orphan_followup_reminder(
                                    orphan_id,
                                    int(appointment_id),
                                    due_date.strftime("%Y-%m-%d"),
                                    adopt_note,
                                    normalized["profile"],
                                    _in_transaction=True,
                                    owner_visit_id=visit_owner,
                                )
                            ):
                                restored_reminder_id = orphan_id
                        if not restored_reminder_id:
                            restored_reminder_id = _create_reminder()
                if normalized["status"] == "upcoming":
                    # 恢复待就诊后把就诊记录的复诊链接写回：否则逾期
                    # 复诊提示会被抑制、再次安排复诊会产生重复预约。
                    # 提醒因用户删除墓碑而不重建时同样要写回
                    # （followup_reminder_id=0），链接与归属必须一致
                    visit_target = self._appointment_owner_visit(
                        previous["note"],
                        previous["owner_visit_id"],
                        normalized["profile"],
                        normalized["scheduled_at"],
                    )
                    if not visit_target:
                        linked = self._conn.execute(
                            "SELECT id FROM visit_records"
                            " WHERE followup_appointment_id = ?",
                            (int(appointment_id),),
                        ).fetchone()
                        if linked is not None:
                            visit_target = int(linked["id"])
                    if not visit_target:
                        # 归属就诊已改到别的档案：清除跨档案 owner/备注
                        # 标记，让恢复出的预约成为独立预约，避免再次
                        # 安排复诊时出现重复预约与提醒
                        owner_prev = _safe_db_id(previous["owner_visit_id"])
                        if owner_prev:
                            owner_row = self._conn.execute(
                                "SELECT profile FROM visit_records"
                                " WHERE id = ?",
                                (owner_prev,),
                            ).fetchone()
                            if owner_row is not None and (
                                _normalize_profile_name(
                                    owner_row["profile"] or ""
                                )
                                != _normalize_profile_name(
                                    normalized["profile"]
                                )
                            ):
                                self._conn.execute(
                                    "UPDATE appointments SET owner_visit_id"
                                    " = 0, note = ?, updated_at = ?"
                                    " WHERE id = ?",
                                    (
                                        re.sub(
                                            r"由就诊记录 #\d+ 自动创建",
                                            "",
                                            str(normalized["note"] or ""),
                                        ).strip()[:300],
                                        now,
                                        int(appointment_id),
                                    ),
                                )
                    if visit_target:
                        # 复诊日期在取消期间被改过时不能把链接挂回旧日期
                        # 的预约，否则后续“安排复诊”会误报已安排
                        link_cursor = self._conn.execute(
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
                        if link_cursor.rowcount == 0:
                            # 就诊复诊日期为空/已变更：链接没有写回，
                            # 不能继续保留自动归属与备注标记。备注以
                            # 本次提交为基底：同一 PATCH 里的新备注
                            # 不能被旧备注覆盖
                            self._conn.execute(
                                "UPDATE appointments"
                                " SET owner_visit_id = 0, note = ?,"
                                " updated_at = ? WHERE id = ?",
                                (
                                    re.sub(
                                        r"由就诊记录 #\d+ 自动创建",
                                        "",
                                        str(normalized["note"] or ""),
                                    ).strip()[:300],
                                    now,
                                    int(appointment_id),
                                ),
                            )
            # 已完成预约改期（就诊日期先改、再更正预约日期的顺序）：
            # 新日期与归属就诊的复诊日期一致时把链接回挂，否则
            # followup_scheduled 永久为假、逾期提示无法自愈（第八十轮）
            if (
                normalized["status"] == "completed"
                and str(previous["status"] or "") == "completed"
                and normalized["scheduled_at"]
                and str(previous["scheduled_at"] or "")
                != normalized["scheduled_at"]
            ):
                completed_visit = self._appointment_owner_visit(
                    previous["note"],
                    previous["owner_visit_id"],
                    normalized["profile"],
                    normalized["scheduled_at"],
                    check_date=False,
                )
                if completed_visit:
                    completed_day = str(
                        normalized["scheduled_at"] or ""
                    )[:10]
                    completed_link = self._conn.execute(
                        "SELECT next_followup_date,"
                        " followup_appointment_id,"
                        " followup_reminder_id FROM visit_records"
                        " WHERE id = ?",
                        (completed_visit,),
                    ).fetchone()
                    completed_linked = (
                        int(completed_link["followup_appointment_id"] or 0)
                        if completed_link is not None else 0
                    )
                    other_link = self._conn.execute(
                        "SELECT 1 FROM visit_records"
                        " WHERE followup_appointment_id = ? AND id != ?"
                        " LIMIT 1",
                        (int(appointment_id), completed_visit),
                    ).fetchone()
                    if (
                        completed_link is not None
                        and completed_day
                        and str(
                            completed_link["next_followup_date"] or ""
                        )[:10] == completed_day
                        and completed_linked in (0, int(appointment_id))
                        and other_link is None
                    ):
                        # 回挂会清零提醒链接：就诊原先链接的脱钩提醒
                        # 若再无其它引用，必须一并清理，否则成为永久
                        # 幽灵待办（第九十七轮 [11]）
                        stale_reminder = int(
                            completed_link["followup_reminder_id"] or 0
                        )
                        if stale_reminder:
                            self._clean_unlinked_detached_reminder(
                                stale_reminder, completed_visit
                            )
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = ?,"
                            " followup_reminder_id = 0 WHERE id = ?",
                            (int(appointment_id), completed_visit),
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
                    normalized["scheduled_at"],
                    # 只有备注标记、owner/链接未写回的自动预约：改期
                    # 后的日期与就诊复诊日不同也要解析出归属，否则
                    # 就诊日期不跟随，之后「安排复诊」会把改期拨回
                    check_date=False,
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
                    # 就诊已链接另一条待就诊预约时，本预约的改期不能
                    # 静默改写该就诊的复诊日期（否则产生错配链接）
                    link_row = self._conn.execute(
                        "SELECT followup_appointment_id, visit_date,"
                        " next_followup_date FROM visit_records"
                        " WHERE id = ?",
                        (visit_target,),
                    ).fetchone()
                    linked_existing = (
                        int(link_row["followup_appointment_id"] or 0)
                        if link_row is not None else 0
                    )
                    visit_day = str(
                        link_row["visit_date"] or ""
                    )[:10] if link_row is not None else ""
                    scheduled_day = str(
                        normalized["scheduled_at"] or ""
                    )[:10]
                    if linked_existing not in (0, int(appointment_id)):
                        visit_target = 0
                    elif (
                        visit_day and scheduled_day
                        and scheduled_day < visit_day
                    ) or (
                        scheduled_day and scheduled_day < str(now)[:10]
                    ):
                        # 复诊日期早于就诊日期或已过期：不再写回，
                        # 并把预约降级为独立预约（避免挂回后抑制
                        # 逾期复诊提示、又不在待就诊列表）
                        visit_target = 0
                        self._conn.execute(
                            "UPDATE appointments SET owner_visit_id = 0,"
                            " note = ?, updated_at = ? WHERE id = ?",
                            (
                                re.sub(
                                    r"由就诊记录 #\d+ 自动创建",
                                    "",
                                    str(normalized["note"] or ""),
                                ).strip()[:300],
                                now,
                                int(appointment_id),
                            ),
                        )
                        # 就诊上的复诊链接必须一起切断：否则“已安排
                        # 复诊”状态会永久抑制逾期复诊提示，之后改就诊
                        # 复诊日期还会凭残留链接删掉这条独立预约
                        downgraded_rows = self._conn.execute(
                            "SELECT id, followup_reminder_id"
                            " FROM visit_records"
                            " WHERE followup_appointment_id = ?",
                            (int(appointment_id),),
                        ).fetchall()
                        self._conn.execute(
                            "UPDATE visit_records SET"
                            " followup_appointment_id = 0,"
                            " followup_reminder_id = 0, updated_at = ?"
                            " WHERE followup_appointment_id = ?",
                            (now, int(appointment_id)),
                        )
                        for downgraded in downgraded_rows:
                            self._clean_unlinked_detached_reminder(
                                int(
                                    downgraded["followup_reminder_id"] or 0
                                ),
                                int(downgraded["id"]),
                            )
                shared_visits = [
                    int(row["id"]) for row in self._conn.execute(
                        "SELECT id FROM visit_records"
                        " WHERE followup_appointment_id = ?",
                        (int(appointment_id),),
                    ).fetchall()
                ]
                if visit_target and visit_target not in shared_visits:
                    shared_visits.append(visit_target)
                synced_visits = 0
                for shared_visit in shared_visits:
                    shared_row = self._conn.execute(
                        "SELECT profile, visit_date, next_followup_date"
                        " FROM visit_records WHERE id = ?",
                        (shared_visit,),
                    ).fetchone()
                    if shared_row is None:
                        continue
                    shared_day = str(
                        normalized["scheduled_at"] or ""
                    )[:10]
                    shared_visit_day = str(
                        shared_row["visit_date"] or ""
                    )[:10]
                    same_shared_profile = (
                        _normalize_profile_name(
                            str(shared_row["profile"] or "")
                        ) == _normalize_profile_name(
                            normalized["profile"]
                        )
                    )
                    if (
                        not same_shared_profile
                        or (
                            shared_visit_day
                            and shared_day
                            and shared_day < shared_visit_day
                        )
                        or (shared_day and shared_day < str(now)[:10])
                    ):
                        # 共享链接的其它就诊：档案/日期不符时只解除
                        # 该就诊自己的链接，不能把它留在「已安排复诊」
                        # 却不上报逾期的悬空状态
                        shared_link_row = self._conn.execute(
                            "SELECT followup_reminder_id"
                            " FROM visit_records WHERE id = ?",
                            (shared_visit,),
                        ).fetchone()
                        shared_unlinked_reminder = (
                            int(
                                shared_link_row["followup_reminder_id"] or 0
                            )
                            if shared_link_row is not None else 0
                        )
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = 0,"
                            " followup_reminder_id = 0, updated_at = ?"
                            " WHERE id = ?",
                            (now, shared_visit),
                        )
                        self._clean_unlinked_detached_reminder(
                            shared_unlinked_reminder, shared_visit
                        )
                        continue
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET next_followup_date = ?, updated_at = ?"
                        " WHERE id = ? AND profile = ?"
                        " AND next_followup_date != ?",
                        (
                            shared_day,
                            now,
                            shared_visit,
                            normalized["profile"],
                            shared_day,
                        ),
                    )
                    pending_row = self._conn.execute(
                        "SELECT id FROM reminders"
                        " WHERE appointment_id = ? AND done = 0"
                        " ORDER BY due_at ASC, id ASC LIMIT 1",
                        (int(appointment_id),),
                    ).fetchone()
                    # owner/备注归属但链接未写回的遗留自动预约：改期
                    # 同步时一并回写链接，否则就诊卡一直显示「未安排
                    # 复诊」、健康提示重复提醒（第一百轮 [9]）
                    pending_reminder_id = (
                        int(pending_row["id"]) if pending_row else 0
                    )
                    old_link_row = self._conn.execute(
                        "SELECT followup_reminder_id FROM visit_records"
                        " WHERE id = ? AND followup_appointment_id = 0",
                        (shared_visit,),
                    ).fetchone()
                    stale_link = (
                        int(old_link_row["followup_reminder_id"] or 0)
                        if old_link_row is not None else 0
                    )
                    target_reminder = pending_reminder_id
                    if stale_link and stale_link != pending_reminder_id:
                        stale_meta = self._conn.execute(
                            "SELECT done, appointment_id, note"
                            " FROM reminders WHERE id = ?",
                            (stale_link,),
                        ).fetchone()
                        stale_shared = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_reminder_id = ? AND id != ?"
                            " LIMIT 1",
                            (stale_link, shared_visit),
                        ).fetchone()
                        if (
                            stale_meta is not None
                            and not int(stale_meta["done"] or 0)
                            and int(stale_meta["appointment_id"] or 0) == 0
                            and stale_shared is None
                        ):
                            if pending_reminder_id:
                                # 预约已有自己的待办：合并删除旧的
                                # 脱钩提醒，避免同一次复诊两条
                                # pending 且就诊挂旧日期那条
                                # （第一百零一轮 [12]）
                                self._conn.execute(
                                    "DELETE FROM reminders WHERE id = ?"
                                    " AND done = 0 AND appointment_id = 0",
                                    (stale_link,),
                                )
                            else:
                                refreshed_note = re.sub(
                                    r"^复诊\s*\d{4}-\d{2}-\d{2}",
                                    f"复诊 {shared_day}",
                                    str(stale_meta["note"] or ""),
                                )
                                try:
                                    stale_due = (
                                        datetime.fromisoformat(shared_day)
                                        .date()
                                        - timedelta(days=1)
                                    ).strftime("%Y-%m-%d")
                                except (ValueError, OverflowError):
                                    stale_due = ""
                                self._conn.execute(
                                    "UPDATE reminders SET due_at = ?,"
                                    " note = ?, updated_at = ?"
                                    " WHERE id = ? AND done = 0"
                                    " AND appointment_id = 0",
                                    (
                                        stale_due or shared_day,
                                        refreshed_note,
                                        now,
                                        stale_link,
                                    ),
                                )
                                target_reminder = stale_link
                        else:
                            target_reminder = stale_link
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET followup_appointment_id = ?,"
                        " followup_reminder_id = ?, updated_at = ?"
                        " WHERE id = ? AND profile = ?"
                        " AND followup_appointment_id = 0",
                        (
                            int(appointment_id),
                            target_reminder,
                            now,
                            shared_visit,
                            normalized["profile"],
                        ),
                    )
                    synced_visits += 1
                if not synced_visits:
                    # 所有共享就诊都无法同步：清除预约归属与备注，
                    # 避免留下指向已失效复诊安排的半挂接
                    self._conn.execute(
                        "UPDATE appointments SET owner_visit_id = 0,"
                        " note = ?, updated_at = ? WHERE id = ?",
                        (
                            re.sub(
                                r"由就诊记录 #\d+ 自动创建",
                                "",
                                str(normalized["note"] or ""),
                            ).strip()[:300],
                            now,
                            int(appointment_id),
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
            appt_row = self._conn.execute(
                "SELECT profile, scheduled_at, note, owner_visit_id"
                " FROM appointments WHERE id = ?",
                (int(appointment_id),),
            ).fetchone()
            cursor = self._conn.execute(
                "DELETE FROM appointments WHERE id = ?",
                (int(appointment_id),),
            )
            if cursor.rowcount == 0:
                return False
            # 预约提醒随预约删除，避免遗留的待办在之后显示逾期；
            # 仍被其它就诊引用的共享提醒只脱钩（第九十轮 [14]）
            pending_rows = self._conn.execute(
                "SELECT id FROM reminders"
                " WHERE done = 0 AND appointment_id = ?",
                (int(appointment_id),),
            ).fetchall()
            for pending_row in pending_rows:
                pending_id = int(pending_row["id"] or 0)
                if not pending_id:
                    continue
                shared_ref = self._conn.execute(
                    "SELECT 1 FROM visit_records"
                    " WHERE followup_reminder_id = ?"
                    " AND followup_appointment_id != ?"
                    " LIMIT 1",
                    (pending_id, int(appointment_id)),
                ).fetchone()
                if shared_ref is not None:
                    self._conn.execute(
                        "UPDATE reminders SET appointment_id = 0,"
                        " updated_at = ? WHERE id = ?",
                        (self._now(), pending_id),
                    )
                else:
                    self._conn.execute(
                        "DELETE FROM reminders WHERE id = ?",
                        (pending_id,),
                    )
            # 就诊仍链接的脱钩待办（reopen 降级）同样随预约删除，
            # 否则删除后留下无主幽灵提醒
            linked_rows = self._conn.execute(
                "SELECT DISTINCT followup_reminder_id FROM visit_records"
                " WHERE followup_appointment_id = ?"
                " AND followup_reminder_id != 0",
                (int(appointment_id),),
            ).fetchall()
            for link_row in linked_rows:
                linked_id = int(link_row["followup_reminder_id"] or 0)
                if linked_id:
                    other_ref = self._conn.execute(
                        "SELECT 1 FROM visit_records"
                        " WHERE followup_reminder_id = ?"
                        " AND followup_appointment_id != ?"
                        " LIMIT 1",
                        (linked_id, int(appointment_id)),
                    ).fetchone()
                    if other_ref is not None:
                        # 其它就诊仍引用该共享脱钩提醒：保留提醒，
                        # 只清本预约就诊的链接（第八十九轮 [11]）
                        continue
                    self._conn.execute(
                        "DELETE FROM reminders WHERE id = ? AND done = 0"
                        " AND appointment_id = 0",
                        (linked_id,),
                    )
            if appt_row is not None:
                # 约定归属但链接未写回的就诊（fa=0）同样要清理它挂着
                # 的脱钩复诊提醒：预约删除后不能留下按原日期触发的
                # 幽灵提醒（第一百零二轮 [9]）
                owner_visit = self._appointment_owner_visit(
                    appt_row["note"],
                    appt_row["owner_visit_id"],
                    str(appt_row["profile"] or ""),
                    str(appt_row["scheduled_at"] or ""),
                    check_date=False,
                )
                if owner_visit:
                    owner_row = self._conn.execute(
                        "SELECT followup_reminder_id, profile"
                        " FROM visit_records WHERE id = ?",
                        (int(owner_visit),),
                    ).fetchone()
                    if owner_row is not None and (
                        _normalize_profile_name(
                            owner_row["profile"] or ""
                        )
                        == _normalize_profile_name(
                            str(appt_row["profile"] or "")
                        )
                    ):
                        owner_reminder = int(
                            owner_row["followup_reminder_id"] or 0
                        )
                        if owner_reminder:
                            self._clean_unlinked_detached_reminder(
                                owner_reminder, int(owner_visit)
                            )
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_reminder_id = 0"
                                " WHERE id = ?"
                                " AND followup_reminder_id = ?",
                                (int(owner_visit), owner_reminder),
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
        # 未提供预约时提醒段落按独立提醒处理（owner 校验不生效）
        auto_created = False
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
            marker_owner = (
                int(row["owner_visit_id"] or 0)
                if row is not None else 0
            )
            auto_created = same_profile and (
                marker_owner in (0, int(visit_id))
            ) and (
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
                if linked is not None:
                    if marker_owner == int(visit_id):
                        auto_created = True
                    elif marker_owner == 0:
                        # 旧版无归属预约：没有其它就诊链接时才把本就诊
                        # 的链接当作自动创建证据；多条就诊共享时只
                        # 解除本就诊链接，不能删掉对方仍在用的预约
                        other_link = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE followup_appointment_id = ? AND id != ?"
                            " LIMIT 1",
                            (appointment, int(visit_id)),
                        ).fetchone()
                        if other_link is None:
                            auto_created = True
                        else:
                            self._conn.execute(
                                "UPDATE visit_records"
                                " SET followup_appointment_id = 0"
                                " WHERE id = ? AND followup_appointment_id = ?",
                                (int(visit_id), appointment),
                            )
                    else:
                        # 旧版本遗留的共享链接：预约 owner 属于另一条
                        # 就诊，本次只解除本就诊自己的链接，绝不能把
                        # 对方的待就诊预约与提醒一起删掉
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = 0"
                            " WHERE id = ? AND followup_appointment_id = ?",
                            (int(visit_id), appointment),
                        )
            # 只有仍未就诊的自动复诊预约才删除；已完成/已取消的是就诊
            # 历史，不能因为修改上一次就诊而复诊日期就消失
            if auto_created and keep_date and (
                str(row["scheduled_at"] or "")[:10] == keep_date
            ):
                # 日期与新复诊日期一致：无论待就诊还是已完成都保留，
                # completed 是按时复诊的历史，清链接会重新报逾期
                if str(row["status"] or "") == "completed":
                    visit_link = self._conn.execute(
                        "SELECT followup_reminder_id FROM visit_records"
                        " WHERE id = ? AND followup_appointment_id = ?",
                        (int(visit_id), appointment),
                    ).fetchone()
                    if visit_link is not None:
                        linked_reminder = int(
                            visit_link["followup_reminder_id"] or 0
                        )
                        if linked_reminder:
                            # 复诊已完成：旧日期脱钩提醒不再提醒，
                            # 无其它引用时删除（第九十八轮 [11]）
                            self._clean_unlinked_detached_reminder(
                                linked_reminder, int(visit_id)
                            )
                            self._conn.execute(
                                "UPDATE visit_records SET"
                                " followup_reminder_id = 0 WHERE id = ?",
                                (int(visit_id),),
                            )
                return True
            if auto_created and str(row["status"] or "") == "upcoming":
                doomed_reminders = [
                    int(pending["id"])
                    for pending in self._conn.execute(
                        "SELECT id FROM reminders"
                        " WHERE done = 0 AND appointment_id = ?",
                        (appointment,),
                    ).fetchall()
                ]
                self._conn.execute(
                    "DELETE FROM appointments WHERE id = ?",
                    (appointment,),
                )
                self._conn.execute(
                    "DELETE FROM reminders"
                    " WHERE done = 0 AND appointment_id = ?",
                    (appointment,),
                )
                if doomed_reminders:
                    # 其它就诊遗留指向这些提醒的复诊链接同样解除，
                    # 否则提醒已删而链接仍指向它，复诊会被永久抑制
                    placeholders = ",".join(
                        "?" for _ in doomed_reminders
                    )
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET followup_reminder_id = 0"
                        f" WHERE followup_reminder_id IN ({placeholders})",
                        tuple(doomed_reminders),
                    )
                # 已完成提醒是历史：不能继续指向已删除的预约，否则
                # 跨设备导入时会被按本地同号预约兜底挂接
                self._conn.execute(
                    "UPDATE reminders SET appointment_id = 0, updated_at = ?"
                    " WHERE done = 1 AND appointment_id = ?",
                    (self._now(), appointment),
                )
                # 从该预约补记的就诊记录不能留下悬空引用
                self._conn.execute(
                    "UPDATE visit_records SET appointment_id = 0"
                    " WHERE appointment_id = ?",
                    (appointment,),
                )
            if row is None or auto_created:
                # 只有本次确实把该预约判为自动复诊（或它已不存在、
                # 清理悬空链接）时才清链接：标记指向本就诊但 owner
                # 属于另一条就诊时，不能把真正归属者的「已安排复诊」
                # 一起清掉
                self._conn.execute(
                    "UPDATE visit_records SET followup_appointment_id = 0"
                    " WHERE followup_appointment_id = ?",
                    (appointment,),
                )
        reminder = _safe_db_id(reminder_id)
        if reminder:
            visit_profile = self._conn.execute(
                "SELECT profile FROM visit_records WHERE id = ?",
                (int(visit_id),),
            ).fetchone()
            reminder_row = self._conn.execute(
                "SELECT profile, appointment_id FROM reminders WHERE id = ?",
                (reminder,),
            ).fetchone()
            same_owner = bool(
                visit_profile is None
                or reminder_row is None
                or _normalize_profile_name(
                    reminder_row["profile"] or ""
                ) == _normalize_profile_name(
                    visit_profile["profile"] or ""
                )
            )
            reminder_appointment = (
                int(reminder_row["appointment_id"] or 0)
                if reminder_row is not None else -1
            )
            still_linked = (
                reminder_appointment == 0
                and self._conn.execute(
                    "SELECT 1 FROM visit_records WHERE id = ?"
                    " AND followup_reminder_id = ?",
                    (int(visit_id), reminder),
                ).fetchone() is not None
            )
            # 遗留共享链接（预约 owner 属于另一条就诊）时，提醒
            # 同样不属于本次就诊：不能删除归属就诊唯一的一条待办
            ownership_ok = bool(auto_created or not appointment)
            if (
                same_owner
                and (
                    reminder_appointment == -1
                    or (
                        ownership_ok
                        and (
                            reminder_appointment
                            == (int(appointment) or 0)
                            or still_linked
                        )
                    )
                    # 遗留共享链接下，本就诊仍是这条脱钩待办提醒的
                    # 唯一引用：预约归属不影响提醒清理，否则解除
                    # 链接后它会变成无引用的幽灵提醒（第九十九轮 [10]）
                    or (reminder_appointment == 0 and still_linked)
                )
            ):
                # 已完成的提醒是历史记录，不能随复诊改期删除；
                # 已移交/脱钩的独立提醒也不能被就诊级联删除。
                # 但就诊仍把该提醒当作自己的复诊提醒时（完成→重开
                # 后脱钩），它必须随改期删除，否则留下过期幽灵提醒
                shared_row = self._conn.execute(
                    "SELECT 1 FROM visit_records"
                    " WHERE followup_reminder_id = ? AND id != ? LIMIT 1",
                    (reminder, int(visit_id)),
                ).fetchone()
                if shared_row is not None:
                    # 其它就诊仍引用这条脱钩提醒：保留提醒，不能随
                    # 本就诊改期/删除而删掉别人的复诊待办
                    # （第九十轮 [9]）
                    return False
                cursor = self._conn.execute(
                    "DELETE FROM reminders WHERE id = ? AND done = 0",
                    (reminder,),
                )
                if cursor.rowcount:
                    # 提醒已删：其它就诊指向它的复诊链接不能悬空，
                    # 否则复诊会被永久抑制
                    self._conn.execute(
                        "UPDATE visit_records"
                        " SET followup_reminder_id = 0"
                        " WHERE followup_reminder_id = ?",
                        (reminder,),
                    )
        return False

    def _adopt_followup_reminder_for_kept_appointment(
        self,
        visit_id: int,
        reminder_id: Any,
        appointment_id: int,
        normalized: Dict[str, Any],
        old_appointment_id: Any = 0,
    ) -> None:
        """把就诊仍链接的待办提醒接管到被保留的自动预约上。

        复诊日期改到与该预约同日时预约被保留，但主
        `_drop_visit_followup_locked` 仍会把就诊链接的脱钩提醒当旧
        复诊安排删除，回写又只能取预约自带的提醒，导致
        「已安排复诊」却没有待办提醒（第一百零三轮 [8]）。
        提醒仍挂在就诊旧链接预约上时（正常排程状态），该预约会被
        主 _drop 连同 pending 提醒一起删除：先脱钩再接管
        （第一百零四轮 [9]）。
        调用方须已持有写锁与事务（update_visit_record 内）。
        """
        reminder = _safe_db_id(reminder_id)
        appointment = _safe_db_id(appointment_id)
        if not reminder or not appointment:
            return
        reminder_row = self._conn.execute(
            "SELECT done, appointment_id FROM reminders WHERE id = ?",
            (int(reminder),),
        ).fetchone()
        if reminder_row is None or int(reminder_row["done"] or 0):
            return
        attached = int(reminder_row["appointment_id"] or 0)
        if attached:
            # 只处理仍挂在「就诊旧链接预约」上的提醒，且该预约确实
            # 会被主 _drop 删除（upcoming、归属本就诊、日期不是
            # 保留日期）；其它情况提醒保持原样，不能挪走别人的待办
            old_appointment = _safe_db_id(old_appointment_id)
            keep_day = str(
                normalized.get("next_followup_date") or ""
            )[:10]
            if not old_appointment or attached != int(old_appointment):
                return
            old_row = self._conn.execute(
                "SELECT status, scheduled_at, owner_visit_id"
                " FROM appointments WHERE id = ?",
                (int(old_appointment),),
            ).fetchone()
            if (
                old_row is None
                or str(old_row["status"] or "") != "upcoming"
                or int(old_row["owner_visit_id"] or 0)
                not in (0, int(visit_id))
                or str(old_row["scheduled_at"] or "")[:10] == keep_day
            ):
                return
            self._conn.execute(
                "UPDATE reminders SET appointment_id = 0, updated_at = ?"
                " WHERE id = ? AND done = 0 AND appointment_id = ?",
                (self._now(), int(reminder), int(attached)),
            )
        existing_pending = self._conn.execute(
            "SELECT 1 FROM reminders WHERE done = 0 AND appointment_id = ?"
            " LIMIT 1",
            (int(appointment),),
        ).fetchone()
        if existing_pending is not None:
            # 预约本身已有待办提醒：回写会用它，不能再接管一条
            # 造成同一预约两条 pending（第一百零三轮 [8]）
            return
        appointment_row = self._conn.execute(
            "SELECT scheduled_at FROM appointments WHERE id = ?",
            (int(appointment),),
        ).fetchone()
        if appointment_row is None:
            return
        adopt_day = str(appointment_row["scheduled_at"] or "")[:10]
        adopt_due = ""
        try:
            adopt_date = datetime.strptime(adopt_day, "%Y-%m-%d").date()
        except ValueError:
            adopt_date = None
        if adopt_date is not None:
            try:
                adopt_due = max(
                    adopt_date - timedelta(days=1),
                    datetime.now().date(),
                ).strftime("%Y-%m-%d")
            except OverflowError:
                adopt_due = ""
        if not adopt_due:
            return
        adopt_note = " ".join(
            part for part in (
                f"复诊 {adopt_day}",
                normalized.get("department") or "",
                normalized.get("doctor") or "",
            ) if part
        )[:MAX_REMINDER_NOTE_LENGTH]
        if not adopt_note:
            return
        self.adopt_orphan_followup_reminder(
            reminder,
            int(appointment),
            adopt_due,
            adopt_note,
            str(normalized.get("profile") or ""),
            _in_transaction=True,
            owner_visit_id=int(visit_id),
        )

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
                    owner_row = self._conn.execute(
                        "SELECT owner_visit_id FROM appointments"
                        " WHERE id = ?",
                        (linked_appointment,),
                    ).fetchone()
                    owner_visit = (
                        int(owner_row["owner_visit_id"] or 0)
                        if owner_row is not None else 0
                    )
                    if owner_visit in (0, int(visit_id)):
                        # 旧版本可能有多条就诊共享该复诊预约：迁移前
                        # 先解除其它就诊的链接，避免它们继续指向新
                        # 档案的预约、抑制逾期复诊提示；对方独占的
                        # 脱钩待办提醒同时清理（第一百轮 [13]）
                        cleared_links = self._conn.execute(
                            "SELECT id, followup_reminder_id"
                            " FROM visit_records"
                            " WHERE followup_appointment_id = ?"
                            " AND id != ?",
                            (linked_appointment, int(visit_id)),
                        ).fetchall()
                        for cleared_link in cleared_links:
                            cleared_reminder = int(
                                cleared_link["followup_reminder_id"] or 0
                            )
                            if cleared_reminder:
                                self._clean_unlinked_detached_reminder(
                                    cleared_reminder,
                                    int(cleared_link["id"]),
                                )
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = 0,"
                            " followup_reminder_id = 0, updated_at = ?"
                            " WHERE followup_appointment_id = ?"
                            " AND id != ?",
                            (now, linked_appointment, int(visit_id)),
                        )
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
                            " updated_at = ? WHERE appointment_id = ?"
                            " AND done = 0",
                            (
                                normalized["profile"], now,
                                linked_appointment,
                            ),
                        )
                        # 已完成提醒只迁档案不刷新 updated_at：否则会
                        # 晚于删除墓碑、改期重建复活已删提醒（第一百轮 [8]）
                        self._conn.execute(
                            "UPDATE reminders SET profile = ?"
                            " WHERE appointment_id = ? AND done = 1",
                            (
                                normalized["profile"],
                                linked_appointment,
                            ),
                        )
                    else:
                        # 遗留共享链接：预约属于另一条就诊，只解除
                        # 本就诊的链接，不能把别人的预约/提醒迁档案
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = 0"
                            " WHERE id = ? AND followup_appointment_id = ?",
                            (int(visit_id), linked_appointment),
                        )
                if linked_reminder:
                    reminder_meta = self._conn.execute(
                        "SELECT appointment_id FROM reminders"
                        " WHERE id = ?",
                        (linked_reminder,),
                    ).fetchone()
                    reminder_appointment = (
                        int(reminder_meta["appointment_id"] or 0)
                        if reminder_meta is not None else 0
                    )
                    reminder_owner_ok = True
                    if reminder_appointment:
                        meta_row = self._conn.execute(
                            "SELECT owner_visit_id FROM appointments"
                            " WHERE id = ?",
                            (reminder_appointment,),
                        ).fetchone()
                        meta_owner = (
                            int(meta_row["owner_visit_id"] or 0)
                            if meta_row is not None else 0
                        )
                        reminder_owner_ok = meta_owner in (
                            0, int(visit_id)
                        )
                    shared_linked = self._conn.execute(
                        "SELECT 1 FROM visit_records"
                        " WHERE followup_reminder_id = ? AND id != ? LIMIT 1",
                        (linked_reminder, int(visit_id)),
                    ).fetchone() is not None
                    if reminder_owner_ok and not shared_linked:
                        # 已完成提醒只迁档案不刷新 updated_at：否则会
                        # 晚于删除墓碑、改期重建复活已删提醒
                        # （第一百零轮 [8]、第一百零三轮 [9]）
                        self._conn.execute(
                            "UPDATE reminders SET profile = ?,"
                            " updated_at = CASE WHEN done = 0 THEN ?"
                            " ELSE updated_at END WHERE id = ?",
                            (normalized["profile"], now, linked_reminder),
                        )
                    else:
                        # 共享脱钩提醒不能随单个就诊改档案：只解除本
                        # 就诊链接，由之后的「安排复诊」为迁移后的
                        # 就诊新建专属提醒（第九十一轮 [12]）
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_reminder_id = 0"
                            " WHERE id = ? AND followup_reminder_id = ?",
                            (int(visit_id), linked_reminder),
                        )
                # owner/备注标记指向本就诊但没写回链接的 upcoming 自动
                # 预约同样跟随改档案：否则旧档案保留别人的待办，新档案
                # 安排复诊时看不到它而再建一条，形成跨档案重复复诊
                marker_text = f"由就诊记录 #{int(visit_id)} 自动创建"
                old_profile = str(old_row["profile"] or "")
                # 只迁移 owner 为本就诊（或 owner=0 的标记型）自动
                # 预约：预约被改到其他成员后 owner 已清 0、备注标记
                # 可能残留，不能因为标记指向本就诊就把别人的预约再
                # 搬一次；owner 属于另一条就诊时同样不能迁移（与
                # _drop_visit_followup_locked / _schedule_followup_sync
                # 的 owner 校验口径一致）
                self._conn.execute(
                    "UPDATE appointments SET profile = ?, updated_at = ?"
                    " WHERE status = 'upcoming' AND profile = ?"
                    "   AND (owner_visit_id = ?"
                    "        OR (owner_visit_id = 0 AND note LIKE ?))",
                    (
                        normalized["profile"], now, old_profile,
                        int(visit_id), f"%{marker_text}%",
                    ),
                )
                # 提醒子查询与预约迁移保持同一集合：导入恢复留下的
                # 「只有备注标记、owner=0」的自动预约及其待办提醒
                # 一起跟随改档案，避免旧成员名下残留别人的复诊待办。
                # 已完成提醒只迁档案不刷新 updated_at，避免击穿删除
                # 墓碑（第一百轮 [8]）
                self._conn.execute(
                    "UPDATE reminders SET profile = ?, updated_at = ?"
                    " WHERE appointment_id IN (SELECT id FROM appointments"
                    "   WHERE status = 'upcoming' AND profile = ?"
                    "     AND (owner_visit_id = ?"
                    "          OR (owner_visit_id = 0 AND note LIKE ?)))"
                    " AND done = 0",
                    (
                        normalized["profile"], now, normalized["profile"],
                        int(visit_id), f"%{marker_text}%",
                    ),
                )
                self._conn.execute(
                    "UPDATE reminders SET profile = ?"
                    " WHERE appointment_id IN (SELECT id FROM appointments"
                    "   WHERE status = 'upcoming' AND profile = ?"
                    "     AND (owner_visit_id = ?"
                    "          OR (owner_visit_id = 0 AND note LIKE ?)))"
                    " AND done = 1",
                    (
                        normalized["profile"], normalized["profile"],
                        int(visit_id), f"%{marker_text}%",
                    ),
                )
                # 已取消/已完成等未迁移的预约仍可能带着本就诊的
                # owner 与「#N 自动创建」标记：就诊已换档案后必须
                # 剥离，否则旧档案长期显示指向他人就诊的残留归属
                self._conn.execute(
                    "UPDATE appointments SET owner_visit_id = 0,"
                    " note = TRIM(REPLACE(note, ?, '')), updated_at = ?"
                    " WHERE profile != ?"
                    "   AND (owner_visit_id = ?"
                    "        OR (owner_visit_id = 0 AND note LIKE ?))",
                    (
                        marker_text, now, normalized["profile"],
                        int(visit_id), f"%{marker_text}%",
                    ),
                )
            # 复诊日期变化后撤销旧的自动预约/提醒：只清关联编号会留下
            # 幽灵预约和仍会到期的旧提醒，再次安排时两套并存
            if (
                old_row is not None
                and str(old_row["next_followup_date"] or "")
                != normalized["next_followup_date"]
            ):
                # 导入恢复可能只回填了 owner/备注标记而没有写回链接：
                # 这些 upcoming 自动预约与待办提醒同样属于旧复诊安排，
                # 必须随复诊日期变化一起清理（日期等于新日期的保留）
                linked_id = _safe_db_id(
                    old_row["followup_appointment_id"]
                )
                marker_text = f"由就诊记录 #{int(visit_id)} 自动创建"
                kept_extra_ids: List[int] = []
                for extra in self._conn.execute(
                    "SELECT id FROM appointments"
                    " WHERE status = 'upcoming' AND (owner_visit_id = ?"
                    "        OR note LIKE ?)",
                    (int(visit_id), f"%{marker_text}%"),
                ).fetchall():
                    extra_id = int(extra["id"])
                    if extra_id == linked_id:
                        continue
                    if self._drop_visit_followup_locked(
                        int(visit_id),
                        extra_id,
                        0,
                        keep_date=normalized["next_followup_date"],
                    ):
                        kept_extra_ids.append(extra_id)
                if kept_extra_ids:
                    # 与新复诊日期同日的遗留自动预约被保留时，就诊仍
                    # 链接的脱钩待办提醒必须接管到该预约：主 _drop 会
                    # 把它当旧复诊安排删除，而回写只能取预约自带的
                    # 提醒，最终「已安排复诊」却没有待办提醒
                    # （第一百零三轮 [8]）
                    self._adopt_followup_reminder_for_kept_appointment(
                        int(visit_id),
                        old_row["followup_reminder_id"],
                        kept_extra_ids[0],
                        normalized,
                        old_appointment_id=old_row["followup_appointment_id"],
                    )
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
                if kept_extra_ids:
                    # 被保留下来的同日期自动预约回写为就诊链接：否则
                    # followup_scheduled 误报「未安排复诊」
                    # （第九十八轮 [15]）
                    extra_row = self._conn.execute(
                        "SELECT id FROM appointments WHERE id = ?"
                        " AND status = 'upcoming' AND profile = ?",
                        (
                            int(kept_extra_ids[0]),
                            normalized["profile"],
                        ),
                    ).fetchone()
                    if extra_row is not None:
                        extra_reminder = self._conn.execute(
                            "SELECT id FROM reminders WHERE done = 0"
                            " AND appointment_id = ? ORDER BY id LIMIT 1",
                            (int(extra_row["id"]),),
                        ).fetchone()
                        self._conn.execute(
                            "UPDATE visit_records SET"
                            " followup_appointment_id = ?,"
                            " followup_reminder_id = ? WHERE id = ?",
                            (
                                int(extra_row["id"]),
                                int(extra_reminder["id"])
                                if extra_reminder is not None else 0,
                                int(visit_id),
                            ),
                        )
                # 已完成（completed）自动预约是就诊历史：改回复诊日期
                # 或把日期更正为已完成预约的计划日时链接同样回挂，
                # 否则 followup_scheduled 永久为假、逾期提示无法自愈
                new_followup_date = str(
                    normalized["next_followup_date"] or ""
                )[:10]
                current_link = self._conn.execute(
                    "SELECT followup_appointment_id FROM visit_records"
                    " WHERE id = ?",
                    (int(visit_id),),
                ).fetchone()
                if new_followup_date and (
                    current_link is None
                    or int(current_link["followup_appointment_id"] or 0) == 0
                ):
                    restored = self._conn.execute(
                        "SELECT id FROM appointments"
                        " WHERE status = 'completed'"
                        "   AND profile = ?"
                        "   AND substr(scheduled_at, 1, 10) = ?"
                        "   AND (owner_visit_id = ? OR note LIKE ?)"
                        "   AND id NOT IN (SELECT followup_appointment_id"
                        "     FROM visit_records"
                        "     WHERE followup_appointment_id != 0"
                        "       AND id != ?)"
                        " ORDER BY id LIMIT 1",
                        (
                            normalized["profile"], new_followup_date,
                            int(visit_id), f"%{marker_text}%",
                            int(visit_id),
                        ),
                    ).fetchone()
                    if restored is not None:
                        self._conn.execute(
                            "UPDATE visit_records"
                            " SET followup_appointment_id = ?,"
                            " followup_reminder_id = 0 WHERE id = ?",
                            (int(restored["id"]), int(visit_id)),
                        )
        return self.get_visit_record(visit_id)

    def adopt_visit_appointment(
        self, visit_id: int, appointment_id: Any
    ) -> bool:
        """把无归属的待就诊预约接管为本就诊的复诊安排。

        只在安排复诊时对「唯一同档案同标题同日期且无 owner/标记」的
        候选调用：恢复旧备份留下的独立预约不应再生成第二条。
        """
        appointment = _safe_db_id(appointment_id)
        if not appointment:
            return False
        marker = f"由就诊记录 #{int(visit_id)} 自动创建"
        now = self._now()
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT note FROM appointments"
                " WHERE id = ? AND status = 'upcoming'",
                (appointment,),
            ).fetchone()
            if row is None:
                return False
            note = str(row["note"] or "")
            if marker not in note:
                note = (note + " " + marker).strip()[:300]
            self._conn.execute(
                "UPDATE appointments SET owner_visit_id = ?,"
                " note = ?, updated_at = ? WHERE id = ?",
                (int(visit_id), note, now, appointment),
            )
            return True

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

            def _release_other_links(owner_id: int) -> None:
                """解除其它就诊指向该预约的悬空链接并清理脱钩提醒。

                只清链接会让对方仍挂着的 pending 脱钩提醒失去全部
                引用，按旧日期继续触发（第九十九轮 [10]）。
                """
                cleared_rows = self._conn.execute(
                    "SELECT id, followup_reminder_id FROM visit_records"
                    " WHERE followup_appointment_id = ? AND id != ?",
                    (appointment, owner_id),
                ).fetchall()
                for cleared_row in cleared_rows:
                    other_reminder = int(
                        cleared_row["followup_reminder_id"] or 0
                    )
                    if other_reminder:
                        self._clean_unlinked_detached_reminder(
                            other_reminder, int(cleared_row["id"])
                        )
                self._conn.execute(
                    "UPDATE visit_records"
                    " SET followup_appointment_id = 0,"
                    " followup_reminder_id = 0"
                    " WHERE followup_appointment_id = ?"
                    " AND id != ?",
                    (appointment, owner_id),
                )

            if appointment:
                appointment_row = self._conn.execute(
                    "SELECT profile, status, scheduled_at, owner_visit_id"
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
                owner_visit = int(appointment_row["owner_visit_id"] or 0)
                if owner_visit and owner_visit != int(visit_id):
                    # 预约归属另一条就诊：不能把同一条复诊预约和
                    # 提醒共享给两条就诊
                    return False
                other_link = self._conn.execute(
                    "SELECT 1 FROM visit_records"
                    " WHERE followup_appointment_id = ? AND id != ?"
                    " LIMIT 1",
                    (appointment, int(visit_id)),
                ).fetchone()
                if other_link is not None:
                    if owner_visit == int(visit_id):
                        # 旧版本遗留的共享链接：预约归本就诊所有时，
                        # 解除其他就诊指向它的悬空链接（预约归本就诊，
                        # 对方仍挂的脱钩提醒一并清理），否则「安排复诊」
                        # 每次都会因共享校验失败误报 404
                        _release_other_links(int(visit_id))
                    elif owner_visit == 0:
                        # 旧版无归属共享预约：本就诊已链接它时按归属
                        # 就诊处理（幂等），并解除其它就诊的悬空链接
                        current_link = self._conn.execute(
                            "SELECT 1 FROM visit_records"
                            " WHERE id = ? AND followup_appointment_id = ?",
                            (int(visit_id), appointment),
                        ).fetchone()
                        if current_link is None:
                            return False
                        _release_other_links(int(visit_id))
                        self._conn.execute(
                            "UPDATE appointments SET owner_visit_id = ?"
                            " WHERE id = ?",
                            (int(visit_id), appointment),
                        )
                    else:
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
                linked_id = _safe_db_id(row["followup_appointment_id"])
                marker_text = f"由就诊记录 #{int(visit_id)} 自动创建"
                # 导入恢复可能只回填了 owner/备注标记而没有写回链接：
                # 这些 upcoming 自动预约同样是本就诊创建的，必须一并
                # 撤销，否则待办提醒会照常到期触发
                extra_ids: List[int] = []
                for extra in self._conn.execute(
                    "SELECT id, owner_visit_id, note FROM appointments"
                    " WHERE status = 'upcoming' AND (owner_visit_id = ?"
                    "        OR note LIKE ?)",
                    (int(visit_id), f"%{marker_text}%"),
                ).fetchall():
                    candidate_id = int(extra["id"])
                    if candidate_id == linked_id:
                        continue
                    if (
                        int(extra["owner_visit_id"] or 0)
                        == int(visit_id)
                        or marker_text in str(extra["note"] or "")
                    ):
                        extra_ids.append(candidate_id)
                for extra_id in extra_ids:
                    self._drop_visit_followup_locked(
                        int(visit_id), extra_id, 0
                    )
                self._drop_visit_followup_locked(
                    int(visit_id),
                    row["followup_appointment_id"],
                    row["followup_reminder_id"],
                )
            # 幸存的 cancelled/completed 自动预约不能保留指向已删
            # 就诊的悬空归属（否则恢复旧预约会绕过重复校验）
            self._conn.execute(
                "UPDATE appointments SET owner_visit_id = 0"
                " WHERE owner_visit_id = ?",
                (int(visit_id),),
            )
            # 备注标记同样是归属证据：删除就诊时一并剥离，避免导入/
            # 迁移把目标库同号就诊误当作归属
            marker_text = f"由就诊记录 #{int(visit_id)} 自动创建"
            marker_rows = self._conn.execute(
                "SELECT id, note FROM appointments"
                " WHERE note LIKE '%由就诊记录 #%'"
            ).fetchall()
            for marker_row in marker_rows:
                note_text = str(marker_row["note"] or "")
                if marker_text in note_text:
                    self._conn.execute(
                        "UPDATE appointments SET note = ? WHERE id = ?",
                        (
                            note_text.replace(marker_text, "").strip()[:300],
                            int(marker_row["id"]),
                        ),
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
                       started_at, stopped_at, stop_reason, resumed_at,
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
                              AND (
                                  medications.resumed_at = ''
                                  OR l.taken_at >= medications.resumed_at
                              )
                       ) AS week_count,
                       (
                           SELECT COUNT(*) FROM medication_logs l
                            WHERE l.medication_id = medications.id
                              AND l.skipped = 0
                              AND l.taken_at >= ? AND l.taken_at < ?
                              AND (
                                  medications.resumed_at = ''
                                  OR l.taken_at >= medications.resumed_at
                              )
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
                    bool(row["active"]),
                ),
                "expected_range_doses": expected_doses_for_period(
                    row["frequency"],
                    row["started_at"] or row["created_at"],
                    now,
                    days,
                    row["stopped_at"] or "",
                    bool(row["active"]),
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
                       started_at, stopped_at, stop_reason, resumed_at
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
            # 普通编辑沿用重启标记：只有 停用→启用 才重新写入
            normalized["resumed_at"] = str(
                previous["resumed_at"] or ""
            )
            if (
                bool(int(normalized["active"] or 0))
                and not int(previous["active"] or 0)
            ):
                # 重新启用：旧停药日期/原因不再适用，否则用药编辑
                # 表单原样回传的停药日会把在用药物从依从统计里整段
                # 排除（第七十三轮审查）
                normalized["stopped_at"] = ""
                normalized["stop_reason"] = ""
                normalized["resumed_at"] = now
                # 停用区间不应计入应服次数：只要发生 停用→启用
                # （包括列表按钮只发送 active 的场景），当前疗程就
                # 从今天重新起算（旧记录保留在 medication_history）
                normalized["started_at"] = now[:10]
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
                       resumed_at = ?, updated_at = ?
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
                    str(normalized.get("resumed_at") or ""),
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
                 WHERE done = 0 AND medication_id = ? AND profile = ?
                """,
                (medication_id, str(medication["profile"] or "")),
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
        try:
            normalized_profile = _normalize_profile_name(
                profile, strict=True
            )
        except ValueError:
            return None
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

    def count_attachments_by_ref(
        self, kind: str, profile: Optional[str] = None
    ) -> Dict[int, int]:
        """按关联记录统计附件数量，仅返回已关联的附件。

        profile 非 None 时与 attachment_kind_stats 同口径只统计该
        档案的附件，避免档案视图带出其他成员的记录 id（第八十二轮）。
        """
        kind_key = str(kind or "").strip().lower()
        if kind_key not in ATTACHMENT_KINDS:
            return {}
        profile_clause = ""
        params: List[Any] = [kind_key]
        if profile is not None:
            profile_clause = " AND profile = ?"
            params.append(_normalize_profile_name(profile))
        with self._lock:
            rows = self._conn.execute(
                f"""
                SELECT ref_id, COUNT(*) AS total
                  FROM attachments
                 WHERE kind = ? AND ref_id > 0{profile_clause}
                 GROUP BY ref_id
                """,
                tuple(params),
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

        try:
            # 与写入边界同一 strict 规则：'../evil' 或零宽仿冒名会随
            # list_used_profile_names 进入档案下拉/家庭档案导出
            profile = _normalize_profile_name(profile, strict=True)
        except ValueError:
            return None
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

    def redact_audit_details(
        self,
        actions: List[str],
        prefix: str,
        replacement: str,
    ) -> int:
        """把 detail 以 prefix 开头的审计行整体替换为不可识别内容。

        旧版本把 profile.rename 的新档案名写在 detail 里：删除该档案
        时仅按 target 前缀清理擦不到这行，姓名会继续出现在审计、
        审计 CSV 与 JSON 备份中。
        """
        actions = [str(action) for action in actions if str(action)]
        prefix = str(prefix or "")
        if not actions or not prefix:
            return 0
        placeholders = ",".join("?" for _ in actions)
        with self._write_and_checkpoint():
            cursor = self._conn.execute(
                f"""
                UPDATE audit_log
                   SET target = ?, detail = ?
                 WHERE action IN ({placeholders})
                   AND (detail = ? OR substr(detail, 1, ?) = ?)
                """,
                (
                    str(replacement), str(replacement),
                    *actions, prefix, len(prefix), prefix,
                ),
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
            if appointment_key:
                # 与 add_reminder 一致：重新建立该预约的提醒即撤销
                # 「不再提醒」墓碑，避免取消/恢复待就诊时提醒消失
                self._conn.execute(
                    "DELETE FROM app_state WHERE key = ?",
                    (f"reminder_deleted_{int(appointment_key)}",),
                )
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
        profile = _normalize_profile_name(profile, strict=True)
        advance_days = _normalize_reminder_advance(advance_days)
        repeat_days = _normalize_reminder_repeat_days(repeat_days)
        repeat_until = _normalize_reminder_repeat_until(repeat_until)
        _validate_repeat_window(due_at, repeat_days, repeat_until)
        medication_id = _safe_db_id(medication_id)
        vaccination_id = _safe_db_id(vaccination_id)
        appointment_id = _safe_db_id(appointment_id)
        now = self._now()
        with self._lock, self._conn:
            # 关联记录必须存在且属于同一档案；待办提醒只能挂待就诊
            # 预约，避免通过公开接口创建会被级联静默删除的提醒
            for _table, _row_id, _require_upcoming in (
                ("medications", medication_id, False),
                ("vaccinations", vaccination_id, False),
                ("appointments", appointment_id, True),
            ):
                if not _row_id:
                    continue
                if _table == "appointments":
                    owner_row = self._conn.execute(
                        "SELECT profile, status FROM appointments"
                        " WHERE id = ?",
                        (int(_row_id),),
                    ).fetchone()
                else:
                    owner_row = self._conn.execute(
                        f"SELECT profile FROM {_table} WHERE id = ?",
                        (int(_row_id),),
                    ).fetchone()
                valid = bool(
                    owner_row is not None
                    and _normalize_profile_name(owner_row["profile"])
                    == profile
                    and (
                        not _require_upcoming
                        or str(owner_row["status"] or "") == "upcoming"
                    )
                )
                if not valid:
                    if _table == "medications":
                        medication_id = 0
                    elif _table == "vaccinations":
                        vaccination_id = 0
                    else:
                        appointment_id = 0
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
            if appointment_id:
                # 用户重新为该预约创建提醒：撤销「不再提醒」墓碑，
                # 否则取消/恢复待就诊会误以为用户仍不想收到提醒
                self._conn.execute(
                    "DELETE FROM app_state WHERE key = ?",
                    (f"reminder_deleted_{int(appointment_id)}",),
                )
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
            reminder_row = self._conn.execute(
                "SELECT appointment_id FROM reminders WHERE id = ?",
                (reminder_id,),
            ).fetchone()
            linked_appointment = (
                int(reminder_row["appointment_id"] or 0)
                if reminder_row is not None else 0
            )
            if linked_appointment:
                duplicate_pending = self._conn.execute(
                    "SELECT 1 FROM reminders"
                    " WHERE appointment_id = ? AND done = 0 AND id != ?"
                    " LIMIT 1",
                    (linked_appointment, reminder_id),
                ).fetchone()
                if duplicate_pending is not None:
                    # 该预约已有新的待办（改期重建）：旧提醒恢复为独立
                    # 提醒，避免同一预约出现两条（其中一条日期过期）
                    self._conn.execute(
                        "UPDATE reminders SET appointment_id = 0"
                        " WHERE id = ?",
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

    def delete_reminder(
        self, reminder_id: int, record_tombstone: bool = True
    ) -> bool:
        """删除提醒；record_tombstone=False 用于系统去重/回滚清理。

        只有用户主动删除待办才应写成「不再提醒」墓碑；系统合并重复
        提醒或回滚新建提醒时写墓碑，会让之后取消→恢复待就诊时永远
        不再重建提醒（第八十九轮 [9]）。
        """
        with self._write_and_checkpoint():
            doomed_row = self._conn.execute(
                "SELECT appointment_id, done FROM reminders WHERE id = ?",
                (int(reminder_id),),
            ).fetchone()
            cursor = self._conn.execute(
                "DELETE FROM reminders WHERE id = ?", (int(reminder_id),)
            )
            if cursor.rowcount == 0:
                return False
            if (
                record_tombstone
                and doomed_row is not None
                and not int(doomed_row["done"] or 0)
            ):
                # 用户主动删除「待办」提醒：记录墓碑，之后改期不能凭
                # 旧的已完成提醒把它复活。删除已完成的历史提醒（清理
                # 已完成/单条删除历史）不是 opt-out，不写墓碑
                # （第八十二轮）。脱钩待办（appointment_id=0）按其
                # 仍被哪些就诊链接，为对应预约写墓碑（第八十八轮）
                marker_targets = set()
                doomed_appointment = int(
                    doomed_row["appointment_id"] or 0
                )
                if doomed_appointment:
                    marker_targets.add(doomed_appointment)
                else:
                    linked_targets = self._conn.execute(
                        "SELECT DISTINCT followup_appointment_id"
                        " FROM visit_records WHERE followup_reminder_id = ?"
                        " AND followup_appointment_id != 0",
                        (int(reminder_id),),
                    ).fetchall()
                    marker_targets.update(
                        int(row["followup_appointment_id"] or 0)
                        for row in linked_targets
                        if int(row["followup_appointment_id"] or 0)
                    )
                    # 链接未写回的半挂接就诊（fa=0）：按 owner/备注
                    # 归属反查其待就诊自动预约，同样写墓碑，否则改期
                    # 会重建用户刚删除的提醒（第一百零二轮 [10]）
                    unlinked_visits = self._conn.execute(
                        "SELECT id, profile FROM visit_records"
                        " WHERE followup_reminder_id = ?"
                        " AND followup_appointment_id = 0",
                        (int(reminder_id),),
                    ).fetchall()
                    for visit_row in unlinked_visits:
                        visit_id = int(visit_row["id"])
                        marker_text = (
                            f"由就诊记录 #{visit_id} 自动创建"
                        )
                        owned = self._conn.execute(
                            "SELECT id FROM appointments"
                            " WHERE status = 'upcoming' AND profile = ?"
                            "   AND (owner_visit_id = ?"
                            "        OR (owner_visit_id = 0"
                            "            AND note LIKE ?))",
                            (
                                str(visit_row["profile"] or ""),
                                visit_id,
                                f"%{marker_text}%",
                            ),
                        ).fetchall()
                        marker_targets.update(
                            int(row["id"]) for row in owned
                        )
                try:
                    for target_id in marker_targets:
                        self.set_app_state(
                            f"reminder_deleted_{int(target_id)}",
                            self._now(),
                        )
                except sqlite3.Error:
                    pass
            # 用户主动删除提醒后，就诊上的复诊提醒链接一并解除，
            # 下次改期不能凭残留链接把提醒复活
            self._conn.execute(
                "UPDATE visit_records SET followup_reminder_id = 0"
                " WHERE followup_reminder_id = ?",
                (int(reminder_id),),
            )
            return True

    def _clean_unlinked_detached_reminder(
        self, reminder_id: Any, visit_id: Any
    ) -> None:
        """解除就诊链接后清理不再被任何就诊引用的脱钩复诊提醒。

        改期把自动预约降级为独立预约时，就诊原先挂着的脱钩提醒
        会失去全部引用，必须在同一事务里删除，避免幽灵待办在
        错误日期继续触发（第九十三轮 [10]）。调用方持有写锁。
        """
        reminder = _safe_db_id(reminder_id)
        if not reminder:
            return
        row = self._conn.execute(
            "SELECT done, appointment_id FROM reminders WHERE id = ?",
            (int(reminder),),
        ).fetchone()
        if (
            row is None
            or int(row["done"] or 0)
            or int(row["appointment_id"] or 0) != 0
        ):
            return
        other = self._conn.execute(
            "SELECT 1 FROM visit_records WHERE followup_reminder_id = ?"
            " AND id != ? LIMIT 1",
            (int(reminder), int(_safe_db_id(visit_id) or 0)),
        ).fetchone()
        if other is not None:
            # 仍被其它就诊共享：保留提醒，只解除本就诊链接
            return
        self._conn.execute(
            "DELETE FROM reminders WHERE id = ? AND done = 0"
            " AND appointment_id = 0",
            (int(reminder),),
        )
        self._conn.execute(
            "UPDATE visit_records SET followup_reminder_id = 0"
            " WHERE followup_reminder_id = ?",
            (int(reminder),),
        )

    def release_completed_followup_reminder(
        self, visit_id: Any, reminder_id: Any
    ) -> None:
        """复诊已完成：本就诊的脱钩待办提醒不再触发（第九十九轮 [15]）。

        只处理仍挂在本就诊链接上、done=0 且 appointment_id=0 的
        提醒：无其它就诊引用时删除，共享时保留并解除本就诊链接。
        已完成的提醒是历史记录，保持不动。
        """
        visit = int(_safe_db_id(visit_id) or 0)
        reminder = _safe_db_id(reminder_id)
        if not visit or not reminder:
            return
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT done, appointment_id FROM reminders WHERE id = ?",
                (int(reminder),),
            ).fetchone()
            if (
                row is None
                or int(row["done"] or 0)
                or int(row["appointment_id"] or 0) != 0
            ):
                return
            self._clean_unlinked_detached_reminder(reminder, visit)
            self._conn.execute(
                "UPDATE visit_records SET followup_reminder_id = 0"
                " WHERE id = ? AND followup_reminder_id = ?",
                (visit, int(reminder)),
            )

    def reminder_shared_by_other_visit(
        self, reminder_id: Any, visit_id: Any
    ) -> bool:
        """提醒是否仍被另一条就诊引用（共享脱钩提醒保护）。"""
        reminder = _safe_db_id(reminder_id)
        if not reminder:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM visit_records WHERE followup_reminder_id = ?"
                " AND id != ? LIMIT 1",
                (int(reminder), int(_safe_db_id(visit_id) or 0)),
            ).fetchone()
        return row is not None

    def delete_reminders_bulk(
        self, reminder_ids: Any, only_done: bool = False
    ) -> int:
        """批量删除提醒，返回实际删除条数。"""
        return len(
            self.delete_reminders_bulk_with_ids(reminder_ids, only_done)
        )

    def delete_reminders_bulk_with_ids(
        self, reminder_ids: Any, only_done: bool = False
    ) -> List[int]:
        """批量删除提醒并按实际删除返回 id（供审计标题清理）。

        only_done=True 供「清理已完成」使用：在 SQL 层只删除已完成
        行，其它标签页/设备期间恢复（reopen）的提醒不会被误删。
        """
        values = sorted({
            parsed for parsed in (
                _safe_db_id(value) for value in (reminder_ids or [])
            ) if parsed
        })
        if not values:
            return []
        placeholders = ",".join("?" for _ in values)
        condition = " AND done = 1" if only_done else ""
        with self._write_and_checkpoint():
            doomed_rows = self._conn.execute(
                f"SELECT id, appointment_id, done FROM reminders"
                f" WHERE id IN ({placeholders}){condition}",
                values,
            ).fetchall()
            doomed = [int(row["id"]) for row in doomed_rows]
            cursor = self._conn.execute(
                f"DELETE FROM reminders"
                f" WHERE id IN ({placeholders}){condition}",
                values,
            )
            if doomed:
                doomed_placeholders = ",".join("?" for _ in doomed)
                self._conn.execute(
                    "UPDATE visit_records SET followup_reminder_id = 0"
                    f" WHERE followup_reminder_id IN ({doomed_placeholders})",
                    doomed,
                )
                for doomed_row in doomed_rows:
                    appointment_ref = int(
                        doomed_row["appointment_id"] or 0
                    )
                    if not appointment_ref:
                        continue
                    if int(doomed_row["done"] or 0):
                        # 清理已完成的历史提醒不是「不再提醒」：
                        # 不写墓碑，改期仍应重建待办（第八十二轮）
                        continue
                    try:
                        self.set_app_state(
                            f"reminder_deleted_{appointment_ref}",
                            self._now(),
                        )
                    except sqlite3.Error:
                        pass
            return doomed

    def adopt_orphan_followup_reminder(
        self,
        reminder_id: Any,
        appointment_id: Any,
        due_at: str,
        note: str,
        profile: str,
        _in_transaction: bool = False,
        owner_visit_id: Any = 0,
    ) -> bool:
        """把就诊仍链接的脱钩待办接管为指定预约的复诊提醒。

        「仅提醒链接」的导入恢复会留下 appointment_id=0 的待办：
        再次安排复诊时接管它，不能再建一条同名提醒（第八十轮）。
        """
        reminder = _safe_db_id(reminder_id)
        appointment = _safe_db_id(appointment_id)
        if not reminder or not appointment:
            return False
        due_at = _normalize_reminder_due(due_at)
        note = " ".join(str(note or "").split())[:MAX_REMINDER_NOTE_LENGTH]
        now = self._now()

        def _apply() -> bool:
            row = self._conn.execute(
                "SELECT profile, done, appointment_id FROM reminders"
                " WHERE id = ?",
                (int(reminder),),
            ).fetchone()
            if (
                row is None
                or int(row["done"] or 0)
                or int(row["appointment_id"] or 0) != 0
                or _normalize_profile_name(row["profile"] or "")
                != _normalize_profile_name(profile)
            ):
                return False
            if owner_visit_id:
                shared_row = self._conn.execute(
                    "SELECT 1 FROM visit_records"
                    " WHERE followup_reminder_id = ? AND id != ? LIMIT 1",
                    (int(reminder), int(owner_visit_id)),
                ).fetchone()
                if shared_row is not None:
                    # 提醒被其它就诊共享：接管会让它们失去待办，
                    # 由调用方新建一条预约专用提醒（第九十轮 [10]）
                    return False
            cursor = self._conn.execute(
                "UPDATE reminders SET appointment_id = ?, due_at = ?,"
                " note = ?, updated_at = ?"
                " WHERE id = ? AND done = 0 AND appointment_id = 0",
                (int(appointment), due_at, note, now, int(reminder)),
            )
            return int(cursor.rowcount or 0) > 0

        if _in_transaction:
            # 调用方（update_appointment）已持有写锁与事务：内层再进
            # Connection 上下文会提前提交外层，失败回滚不完整
            return _apply()
        with self._lock, self._conn:
            return _apply()

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
            params.append(
                _normalize_profile_name(profile, strict=True)
            )

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
                "SELECT due_at, repeat_days, repeat_until, profile "
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
            result = {**dict(row), "done": bool(row["done"])}
            profile_changed = bool(
                profile is not None
                and current is not None
                and _normalize_profile_name(str(profile))
                != _normalize_profile_name(str(current["profile"] or ""))
            )
            if profile_changed:
                # 提醒移交其他成员后，原就诊上的提醒链接必须解除：
                # 之后编辑/删除该就诊不能把别人的待办一起删掉
                self._conn.execute(
                    "UPDATE visit_records SET followup_reminder_id = 0"
                    " WHERE followup_reminder_id = ?",
                    (int(reminder_id),),
                )
            if profile is not None and int(result["appointment_id"] or 0):
                appointment_row = self._conn.execute(
                    "SELECT profile FROM appointments WHERE id = ?",
                    (int(result["appointment_id"]),),
                ).fetchone()
                if appointment_row is None or _normalize_profile_name(
                    str(appointment_row["profile"] or "")
                ) != _normalize_profile_name(str(result["profile"] or "")):
                    # 提醒改到另一位成员名下后不能再挂在原成员的预约上：
                    # 降级为独立提醒并清掉就诊的提醒链接，避免预约
                    # 取消/完成时连带删除别人的提醒或出现重复待办
                    self._conn.execute(
                        "UPDATE reminders SET appointment_id = 0"
                        " WHERE id = ?",
                        (int(reminder_id),),
                    )
                    self._conn.execute(
                        "UPDATE visit_records SET followup_reminder_id = 0"
                        " WHERE followup_reminder_id = ?",
                        (int(reminder_id),),
                    )
                    result["appointment_id"] = 0
            if profile is not None:
                for link_key, link_table, link_column in (
                    ("medication_id", "medications", "id"),
                    ("vaccination_id", "vaccinations", "id"),
                ):
                    linked_id = int(result.get(link_key) or 0)
                    if not linked_id:
                        continue
                    linked_row = self._conn.execute(
                        f"SELECT profile FROM {link_table}"
                        f" WHERE {link_column} = ?",
                        (linked_id,),
                    ).fetchone()
                    if linked_row is not None and _normalize_profile_name(
                        str(linked_row["profile"] or "")
                    ) == _normalize_profile_name(str(result["profile"] or "")):
                        continue
                    # 提醒改到其他成员名下后不能继续挂在原成员的药物/
                    # 疫苗上：清空链接，避免删除药物时连带删除
                    self._conn.execute(
                        f"UPDATE reminders SET {link_key} = 0 WHERE id = ?",
                        (int(reminder_id),),
                    )
                    result[link_key] = 0
        return result

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
