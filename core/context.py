"""结构化问诊上下文的格式化工具。"""
from __future__ import annotations

from typing import Any, Dict

#: 用户填写字段的展示标签
CONTEXT_LABELS = {
    "age": "年龄",
    "sex": "性别",
    "duration": "症状持续时间",
    "history": "既往病史",
    "medications": "正在使用的药物",
    "allergies": "过敏史",
    "recent_metrics": "近期健康指标",
    "pending_reminders": "待办随访提醒",
    "recent_symptoms": "近期症状",
    "active_medications": "用药清单",
}

#: 由记忆系统注入的内部字段，不写入患者的公开上下文
INTERNAL_CONTEXT_KEYS = {"recent_history", "historical_cases"}

#: 防止用户输入过长挤占提示词
MAX_CONTEXT_LENGTH = 2000


def _stringify(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        return "；".join(_stringify(item) for item in value)
    if isinstance(value, dict):
        return "；".join(f"{key}={_stringify(item)}" for key, item in value.items())
    return str(value).strip()


def format_context(context: Any, max_length: int = MAX_CONTEXT_LENGTH) -> str:
    """把上下文字典格式化为提示词里的「背景信息」段落。

    已知字段使用中文标签；未知字段保留原键名，方便 API 调用方
    传入自定义信息。空值会被忽略。
    """
    if not isinstance(context, dict):
        return ""

    lines = []
    for key, label in CONTEXT_LABELS.items():
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        text = _stringify(value)
        if text:
            lines.append(f"{label}：{text}")

    for key, value in context.items():
        if key in CONTEXT_LABELS or key in INTERNAL_CONTEXT_KEYS:
            continue
        if value in (None, "", [], {}):
            continue
        text = _stringify(value)
        if text:
            lines.append(f"{key}：{text}")

    return "\n".join(lines)[:max_length]


def public_context(context: Any) -> Dict[str, Any]:
    """去掉记忆系统注入的内部字段，只保留用户提供的上下文。"""
    if not isinstance(context, dict):
        return {}
    return {
        key: value
        for key, value in context.items()
        if key not in INTERNAL_CONTEXT_KEYS and value not in (None, "", [], {})
    }
