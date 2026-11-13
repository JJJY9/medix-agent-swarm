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

#: 安全关键字段：优先渲染并保证预算。既往病史等自由文本再长，
#: 也不能把过敏史、用药清单、近期指标和待办提醒挤出提示词。
PRIORITY_CONTEXT_KEYS = (
    "allergies",
    "active_medications",
    "medications",
    "recent_metrics",
    "pending_reminders",
)

#: 单个字段超出预算时的截断标记
_TRUNCATED_MARKER = "…（已截断）"


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
        role = str(item.get("role"))
        role_label = {
            "user": "用户",
            "assistant": "助手",
            "system": "历史摘要",
            "tool": "工具结果",
        }.get(role, "助手")
        lines.append(f"{role_label}：{content}")
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


def _truncate_field(text: str, cap: int) -> str:
    """按字段截断并标注，避免单个字段挤占整段提示词。"""
    if cap <= 0:
        return ""
    if len(text) <= cap:
        return text
    if cap <= len(_TRUNCATED_MARKER) + 8:
        return text[:cap]
    return text[: cap - len(_TRUNCATED_MARKER)].rstrip() + _TRUNCATED_MARKER


def format_context(
    context: Any, max_length: int = MAX_CONTEXT_LENGTH,
    include_internal: bool = False,
) -> str:
    """把上下文字典格式化为提示词里的「背景信息」段落。

    已知字段使用中文标签；未知字段保留原键名，方便 API 调用方
    传入自定义信息。空值会被忽略。include_internal=True 时额外
    渲染短期记忆与相似历史案例（Worker 需要多轮上下文）。

    渲染顺序按安全价值排序：过敏史/用药清单/近期指标/待办提醒先
    渲染并均分预算，其余字段与既往病史等自由文本使用剩余预算。
    既往病史很长时会被单独截断，而不是把安全字段整段挤出去。
    """
    if not isinstance(context, dict):
        return ""

    history_text = ""
    if include_internal:
        history_text = _format_recent_history(context.get("recent_history"))
        cases_text = _format_historical_cases(
            context.get("historical_cases")
        )
    else:
        cases_text = ""

    header = f"{INTERNAL_CONTEXT_LABELS['recent_history']}：\n"
    reserve = 0
    if history_text:
        # 最近对话放在最后并预留预算，避免长背景把最新追问整段截掉
        reserve = min(
            len(header) + len(history_text),
            max(200, max_length // 3),
        )
    soft_limit = max(0, max_length - reserve - (1 if reserve else 0))

    present_priority = [
        key for key in PRIORITY_CONTEXT_KEYS
        if _stringify(context.get(key))
    ]
    other_known = [
        key for key in CONTEXT_LABELS
        if key not in PRIORITY_CONTEXT_KEYS
        and _stringify(context.get(key))
    ]
    unknown_keys = [
        key for key, value in context.items()
        if key not in CONTEXT_LABELS
        and key not in INTERNAL_CONTEXT_KEYS
        and _stringify(value)
    ]

    lines = []
    used = 0

    def add_line(label: str, text: str, force: bool = False) -> bool:
        nonlocal used
        if not text:
            return False
        separator = 1 if lines else 0
        # 行格式固定为「标签：文本」，精确计算本行还能放多少字符
        allowed = soft_limit - used - separator - len(label) - 1
        if allowed < 4:
            return False
        if len(text) > allowed:
            if not force:
                return False
            text = _truncate_field(text, allowed)
        lines.append(f"{label}：{text}")
        used += separator + len(label) + 1 + len(text)
        return True

    # 1) 安全关键字段：均分预算，保证每个非空字段都出现在提示词里
    if present_priority:
        secondary_exist = bool(other_known or unknown_keys or cases_text)
        share = int(soft_limit * 0.6) if secondary_exist else soft_limit
        labels_size = sum(
            len(CONTEXT_LABELS[key]) + 1 for key in present_priority
        )
        count = len(present_priority)
        budget = share - labels_size - (count - 1)
        per_field = max(20, budget // max(1, count))
        for key in present_priority:
            add_line(
                CONTEXT_LABELS[key],
                _truncate_field(_stringify(context.get(key)), per_field),
                force=True,
            )

    # 2) 其余已知字段（含既往病史）：使用剩余预算，逐个字段截断
    secondary = list(other_known)
    if cases_text:
        secondary.append("historical_cases")
    remaining = soft_limit - used
    if secondary and remaining > 60:
        labels_size = sum(
            len(CONTEXT_LABELS.get(key)
                or INTERNAL_CONTEXT_LABELS.get(key, key)) + 1
            for key in secondary
        )
        # 每行一个分隔换行 + 标签与冒号，先扣掉再均分
        budget = remaining - labels_size - len(secondary)
        per_field = max(20, budget // len(secondary))
        for key in other_known:
            add_line(
                CONTEXT_LABELS[key],
                _truncate_field(_stringify(context.get(key)), per_field),
            )
        if cases_text:
            add_line(
                INTERNAL_CONTEXT_LABELS["historical_cases"],
                _truncate_field(
                    cases_text, max(per_field, 80)
                ),
            )

    # 3) 未知自定义字段：只在还有余量时渲染
    remaining = soft_limit - used
    if unknown_keys and remaining > 100:
        budget = remaining - len(unknown_keys) - 2 * len(unknown_keys)
        per_field = max(20, budget // len(unknown_keys))
        for key in unknown_keys:
            add_line(
                str(key),
                _truncate_field(_stringify(context.get(key)), per_field),
            )

    body = "\n".join(lines)
    if history_text:
        available = (
            max_length - len(body) - (1 if body else 0) - len(header)
        )
        if available > 0:
            snippet = history_text[-available:]
            if len(history_text) > available:
                # 从下一条完整消息开始，避免截断半句话
                newline = snippet.find("\n")
                if newline >= 0:
                    snippet = snippet[newline + 1:]
            body = (body + "\n" if body else "") + header + snippet

    return body[:max_length]


def public_context(context: Any) -> Dict[str, Any]:
    """去掉记忆系统注入的内部字段，只保留用户提供的上下文。"""
    if not isinstance(context, dict):
        return {}
    return {
        key: value
        for key, value in context.items()
        if key not in INTERNAL_CONTEXT_KEYS and value not in (None, "", [], {})
    }
