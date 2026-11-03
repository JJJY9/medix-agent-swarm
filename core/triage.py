"""急症红旗（red flag）预检。

在调用 LLM 之前做一次确定性的关键词筛查：命中胸痛、呼吸困难、意识改变、
卒中样表现等急症信号时，立即提示用户拨打 120 或前往急诊，不必等待模型回复。

这不是诊断，也不替代医生判断；它只是给高风险描述提供更快的安全出口。
"""
from __future__ import annotations

from typing import Any, Dict, List

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
)

#: 短语前 4 个字符内出现这些词时视为否定描述（"没有胸痛""无呼吸困难"）
NEGATIONS = ("没有", "无", "不", "未", "否认")

#: 分句标点：否定判断只在同一分句内进行，避免上一句的「不」误伤下一句
CLAUSE_DELIMITERS = "，。；！？、,.!?;\n"


def _clause_prefix(text: str, start: int, lookback: int = 6) -> str:
    """返回短语之前、同一分句内的有限长度前缀。"""
    boundary = start
    while boundary > 0 and text[boundary - 1] not in CLAUSE_DELIMITERS:
        boundary -= 1
    return text[max(boundary, start - lookback):start]

EMERGENCY_MESSAGE = (
    "描述中包含急症警示信号，请立即拨打 120 或前往急诊科，"
    "不要等待在线回复。若已出现意识不清、呼吸困难加重等情况，请立即呼救。"
)


def detect_red_flags(text: str) -> Dict[str, Any]:
    """筛查文本中的急症红旗。

    Args:
        text: 用户主诉

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

    if not matches:
        return {"level": "none", "matches": [], "message": ""}

    return {
        "level": "emergency",
        "matches": matches,
        "message": EMERGENCY_MESSAGE,
    }
