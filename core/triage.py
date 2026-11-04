"""急症红旗（red flag）预检。

在调用 LLM 之前做一次确定性的关键词筛查：命中胸痛、呼吸困难、意识改变、
卒中样表现等急症信号时，立即提示用户拨打 120 或前往急诊，不必等待模型回复。

这不是诊断，也不替代医生判断；它只是给高风险描述提供更快的安全出口。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.context import medication_context_text

#: (匹配短语, 展示标签)。同一个标签只保留一次。
RED_FLAG_PHRASES = (
    ("胸痛", "胸痛"),
    ("心前区痛", "胸痛"),
    ("胸口压榨", "胸部压榨感"),
    ("呼吸困难", "呼吸困难"),
    ("喘不上气", "呼吸困难"),
    ("喘不过气", "呼吸困难"),
    ("窒息", "窒息感"),
    ("意识模糊", "意识改变"),
    ("意识不清", "意识改变"),
    ("昏迷", "意识丧失"),
    ("晕厥", "晕厥"),
    ("昏倒", "晕厥"),
    ("抽搐", "抽搐"),
    ("惊厥", "抽搐"),
    ("言语不清", "言语不清"),
    ("说话不清", "言语不清"),
    ("口角歪斜", "口角歪斜"),
    ("嘴歪", "口角歪斜"),
    ("一侧肢体无力", "单侧肢体无力"),
    ("单侧无力", "单侧肢体无力"),
    ("半身不遂", "单侧肢体无力"),
    ("偏瘫", "单侧肢体无力"),
    ("剧烈头痛", "剧烈头痛"),
    ("爆炸样头痛", "剧烈头痛"),
    ("突然视力", "突发视力异常"),
    ("视力骤降", "突发视力异常"),
    ("失明", "视力丧失"),
    ("大量出血", "大量出血"),
    ("大出血", "大量出血"),
    ("呕血", "呕血"),
    ("吐血", "呕血"),
    ("咯血", "咯血"),
    ("黑便", "消化道出血征象"),
    ("血便", "消化道出血征象"),
    ("高热不退", "持续高热"),
    ("高烧不退", "持续高热"),
    ("颈部僵硬", "颈项强直"),
    ("颈项强直", "颈项强直"),
    ("过敏性休克", "过敏性休克"),
    ("喉头水肿", "气道梗阻风险"),
    ("服毒", "中毒/服药过量"),
    ("农药中毒", "中毒/服药过量"),
    ("药物过量", "中毒/服药过量"),
    ("自杀", "自伤风险"),
    ("自伤", "自伤风险"),
    ("无尿", "无尿/少尿"),
    ("没有小便", "无尿/少尿"),
    ("尿量明显减少", "无尿/少尿"),
    ("少尿", "无尿/少尿"),
    ("糖尿病酮症", "糖尿病急症"),
    ("酮症酸中毒", "糖尿病急症"),
    ("高渗性昏迷", "糖尿病急症"),
    ("产后出血", "孕产期出血"),
    ("孕期出血", "孕产期出血"),
    ("怀孕出血", "孕产期出血"),
    ("阴道大量出血", "大量阴道出血"),
)

#: 短语前 4 个字符内出现这些词时视为否定描述（"没有胸痛""无呼吸困难"）
NEGATIONS = ("没有", "无", "不", "未", "否认")

#: 出现这些词时，同一句里的「出血」按孕产期出血处理
PREGNANCY_TERMS = ("怀孕", "孕期", "妊娠", "产妇", "产后")

#: 分句标点：否定判断只在同一分句内进行，避免上一句的「不」误伤下一句
CLAUSE_DELIMITERS = "，。；！？、,.!?;\n"


def _clause_prefix(text: str, start: int, lookback: int = 6) -> str:
    """返回短语之前、同一分句内的有限长度前缀。"""
    boundary = start
    while boundary > 0 and text[boundary - 1] not in CLAUSE_DELIMITERS:
        boundary -= 1
    return text[max(boundary, start - lookback):start]


def _positive_mention(text: str, phrase: str) -> bool:
    """短语是否在同一分句内以非否定形式出现。"""
    start = text.find(phrase)
    while start != -1:
        prefix = _clause_prefix(text, start)
        if not any(negation in prefix for negation in NEGATIONS):
            return True
        start = text.find(phrase, start + len(phrase))
    return False

EMERGENCY_MESSAGE = (
    "描述中包含急症警示信号，请立即拨打 120 或前往急诊科，"
    "不要等待在线回复。若已出现意识不清、呼吸困难加重等情况，请立即呼救。"
)

#: 需要结合上下文判断的高危情况
FEVER_TERMS = ("发热", "发烧", "高烧", "低烧", "烧到")
TEMPERATURE_PATTERN = re.compile(r"体温\s*(?:3[89](?:\.\d+)?|4[0-2](?:\.\d+)?)")
IMMUNOSUPPRESSION_TERMS = (
    "化疗", "放疗", "免疫抑制", "器官移植", "骨髓移植", "造血干细胞移植",
    "艾滋病", "hiv", "长期激素", "长期使用激素", "甲氨蝶呤", "环孢素",
    "他克莫司", "吗替麦考酚酯", "生物制剂",
)
ANTICOAGULANT_TERMS = (
    "华法林", "利伐沙班", "阿哌沙班", "达比加群", "艾多沙班",
    "肝素", "低分子肝素", "抗凝",
)
HEAD_TRAUMA_TERMS = (
    "摔倒", "跌倒", "摔伤", "滑倒", "头部外伤", "头部受伤",
    "撞到头部", "头部撞击", "车祸", "头部被撞",
)

_AGE_YEARS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:岁|周岁|years?|yrs?|y)", re.I)
_AGE_MONTHS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:个?月|months?|mos?)", re.I)
_AGE_DAYS = re.compile(r"(\d+(?:\.\d+)?)\s*(?:天|日|days?)", re.I)
_AGE_BARE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*$")


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        return "；".join(f"{key}={_flatten(item)}" for key, item in value.items())
    return str(value).strip()


def _parse_age_months(age: Any) -> Optional[float]:
    """把常见年龄写法换算成月龄；无法识别时返回 None。"""
    text = _flatten(age).lower()
    if not text:
        return None
    if "新生儿" in text:
        return 0.0

    years = _AGE_YEARS.search(text)
    months = _AGE_MONTHS.search(text)
    days = _AGE_DAYS.search(text)
    total = 0.0
    if years:
        total += float(years.group(1)) * 12
    if months:
        total += float(months.group(1))
    if not years and not months and days:
        total += float(days.group(1)) / 30.44
    if not years and not months and not days:
        bare = _AGE_BARE.match(text)
        if bare:
            total = float(bare.group(1)) * 12
    return total if (years or months or days or _AGE_BARE.match(text)) else None


def _has_fever(text: str) -> bool:
    for term in FEVER_TERMS:
        start = text.find(term)
        while start != -1:
            prefix = _clause_prefix(text, start)
            if not any(negation in prefix for negation in NEGATIONS):
                return True
            start = text.find(term, start + len(term))

    for match in TEMPERATURE_PATTERN.finditer(text):
        prefix = _clause_prefix(text, match.start())
        if not any(negation in prefix for negation in NEGATIONS):
            return True
    return False


def _context_flags(text: str, context: Any) -> List[Tuple[str, str]]:
    """结合年龄、免疫状态与抗凝用药补充高危提示。"""
    if not isinstance(context, dict):
        return []

    flags: List[Tuple[str, str]] = []
    age_months = _parse_age_months(context.get("age"))
    if age_months is not None and age_months < 3 and _has_fever(text):
        flags.append((
            "3 个月以下婴儿发热",
            "3 个月以下婴儿出现发热需要尽快由儿科或急诊医生评估，"
            "请不要自行用药，立即前往医院。",
        ))

    medical_text = " ".join(
        part for part in (
            _flatten(context.get("history")).lower(),
            medication_context_text(context).lower(),
        ) if part
    )
    if (
        any(term in medical_text for term in IMMUNOSUPPRESSION_TERMS)
        and _has_fever(text)
    ):
        flags.append((
            "免疫功能低下伴发热",
            "免疫功能低下的患者出现发热可能是严重感染的表现，"
            "请尽快联系主诊医生或前往急诊，不要自行服用退烧药掩盖病情。",
        ))

    if (
        any(term in medical_text for term in ANTICOAGULANT_TERMS)
        and any(term in text for term in HEAD_TRAUMA_TERMS)
    ):
        flags.append((
            "抗凝治疗期间头部外伤",
            "正在使用抗凝药物时发生头部外伤，颅内出血风险更高，"
            "请立即前往急诊评估，即使当时没有明显不适。",
        ))

    return flags


def detect_red_flags(text: str, context: Any = None) -> Dict[str, Any]:
    """筛查文本中的急症红旗。

    Args:
        text: 用户主诉
        context: 可选的结构化补充信息；用于年龄、免疫状态与抗凝用药的
            高危规则（例如 3 个月以下婴儿发热）。

    Returns:
        {
            "level": "emergency" | "none",
            "matches": [展示标签...],
            "message": 提示语（无命中时为空字符串）
        }
    """
    text = (text or "").strip()
    if not text:
        return {"level": "none", "matches": [], "message": ""}

    matches: List[str] = []
    for phrase, label in RED_FLAG_PHRASES:
        start = text.find(phrase)
        while start != -1:
            prefix = _clause_prefix(text, start)
            if not any(negation in prefix for negation in NEGATIONS):
                if label not in matches:
                    matches.append(label)
                break
            start = text.find(phrase, start + len(phrase))

    if (
        any(term in text for term in PREGNANCY_TERMS)
        and _positive_mention(text, "出血")
        and "孕产期出血" not in matches
    ):
        matches.append("孕产期出血")

    context_flags = _context_flags(text, context)
    for label, _ in context_flags:
        if label not in matches:
            matches.append(label)

    if not matches:
        return {"level": "none", "matches": [], "message": ""}

    message_parts = [message for _, message in context_flags]
    message_parts.append(EMERGENCY_MESSAGE)
    return {
        "level": "emergency",
        "matches": matches,
        "message": " ".join(message_parts),
    }
