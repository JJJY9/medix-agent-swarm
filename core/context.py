"""结构化问诊上下文的格式化工具。"""
from __future__ import annotations

from typing import Any, Dict

#: 用户填写字段的展示标签
CONTEXT_LABELS = {
    "age": "年龄",
    "sex": "性别",
    "blood_type": "血型",
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
INTERNAL_CONTEXT_KEYS = {"recent_history", "historical_cases", "memory_scope"}

#: 内部记忆字段在提示词里的展示标签（仅 include_internal=True 时渲染）
INTERNAL_CONTEXT_LABELS = {
    "recent_history": "最近对话",
    "historical_cases": "相似历史案例",
}

#: 防止用户输入过长挤占提示词
MAX_CONTEXT_LENGTH = 2000


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        parts = [_stringify(item) for item in value]
        return "；".join(part for part in parts if part)
    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            text = _stringify(item)
            if text:
                parts.append(f"{key}={text}")
        return "；".join(parts)
    return str(value).strip()


def medication_context_text(context: Any) -> str:
    """合并自由文本药物与结构化用药清单，供安全与分诊规则使用。"""
    if not isinstance(context, dict):
        return ""
    parts = []
    for key in ("medications", "active_medications"):
        value = context.get(key)
        if value in (None, "", [], {}):
            continue
        text = _stringify(value)
        if text:
            parts.append(text)
    return "；".join(parts)


def _format_recent_history(value: Any) -> str:
    lines = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        role = "用户" if str(item.get("role")) == "user" else "助手"
        lines.append(f"{role}：{content}")
    return "\n".join(lines)


def _format_historical_cases(value: Any) -> str:
    lines = []
    for index, item in enumerate(
        value if isinstance(value, list) else [], 1
    ):
        if not isinstance(item, dict):
            continue
        summary = str(item.get("summary") or "").strip()
        if not summary:
            continue
        score = item.get("score")
        suffix = f"（相关度 {score}）" if score is not None else ""
        lines.append(f"案例{index}{suffix}：{summary}")
    return "\n".join(lines)


def format_context(
    context: Any, max_length: int = MAX_CONTEXT_LENGTH,
    include_internal: bool = False,
) -> str:
    """把上下文字典格式化为提示词里的「背景信息」段落。

    已知字段使用中文标签；未知字段保留原键名，方便 API 调用方
    传入自定义信息。空值会被忽略。include_internal=True 时额外
    渲染短期记忆与相似历史案例（Worker 需要多轮上下文）。
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

    if include_internal:
        history_text = _format_recent_history(context.get("recent_history"))
        if history_text:
            lines.append(
                f"{INTERNAL_CONTEXT_LABELS['recent_history']}：\n"
                f"{history_text}"
            )
        cases_text = _format_historical_cases(
            context.get("historical_cases")
        )
        if cases_text:
            lines.append(
                f"{INTERNAL_CONTEXT_LABELS['historical_cases']}：\n"
                f"{cases_text}"
            )

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
