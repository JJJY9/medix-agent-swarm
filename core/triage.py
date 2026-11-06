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
    ("胸口痛", "胸痛"),
    ("胸口疼", "胸痛"),
    ("心口痛", "胸痛"),
    ("心口疼", "胸痛"),
    ("心前区痛", "胸痛"),
    ("心前区疼痛", "胸痛"),
    ("胸口压榨", "胸部压榨感"),
    ("胸部压榨", "胸部压榨感"),
    ("胸骨后压榨", "胸部压榨感"),
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

#: 部分短语是常见词的子串（如「无尿痛」「减少尿蛋白」），匹配后需再校验
_PHRASE_GUARDS = {
    "无尿": re.compile(
        r"无尿(?!痛|急|频|路|道|意|失禁|常规|培养|检查|感染|液|袋|管|酮|糖|蛋|红|白)"
    ),
    "少尿": re.compile(r"(?<!减)少尿"),
}

#: 短语前 4 个字符内出现这些词时视为否定描述（"没有胸痛""无呼吸困难"）
NEGATIONS = ("没有", "没", "无", "不", "未", "否认")

#: 否定结构必须紧贴短语，且排除「无诱因」「不能缓解」「不舒服」这类
#: 正常修饰语，否则真实的急症描述会被误判成否定
_NEGATION_PATTERN = re.compile(
    r"(?:没有|没|无|未|否认|不是|并非|非|排除|不考虑|不(?!过))"
    r"(?:出现|伴有|伴随|伴|感|有|见|见到|明显|任何|明确|发生|"
    r"诉说|诉|特殊|显著|疑似|表现|是|过|曾经|曾)*"
    r"(?:的)?\s*$"
)
#: 「差点没昏倒」是“几乎发生”，不是否定
_ALMOST_PATTERN = re.compile(
    r"(?:差点|险些|几乎|差一点|差些)\s*(?:没有|没)?\s*$"
)

#: 出现这些词时，同一句里的「出血」按孕产期出血处理
PREGNANCY_TERMS = ("怀孕", "孕期", "妊娠", "产妇", "产后")

#: 孕产期出血只认与妊娠相关的出血部位，牙龈/鼻/皮肤出血不能算
PREGNANCY_BLEEDING_TERMS = (
    "阴道出血", "阴道流血", "阴道大量出血", "产道出血",
    "下体出血", "见红",
)


def _is_negated(prefix: str) -> bool:
    """前缀是否以否定结构结束（"没有"、"未见"、"无"…）。"""
    if _ALMOST_PATTERN.search(prefix):
        return False
    return bool(_NEGATION_PATTERN.search(prefix))

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
        if not _is_negated(prefix):
            return True
        start = text.find(phrase, start + len(phrase))
    return False


def has_positive_mention(text: str, phrase: str) -> bool:
    """公开的否定感知匹配：短语是否以患者当前状态出现。

    供风险评估等 Skill 复用，避免「没有胸痛」「不严重」被当作阳性症状。
    """
    return _positive_mention(text, phrase)

#: 回答中出现这些词可能构成就医建议
VISIT_ADVICE_TERMS = ("就医", "就诊", "看医生", "急诊", "医院", "救护车")
#: 紧邻就医建议前的否定表达（"不建议您现在立刻去医院"）
_VISIT_NEGATIONS = (
    "不建议", "不推荐", "不主张", "无需", "不用", "不必", "不需要",
    "没有必要", "并非", "不是", "暂不", "先不", "不急着", "不必要",
    "不应", "不应该", "不要", "别", "不宜",
    "可以不去", "没必要", "用不着", "无需去", "不必去", "不用去",
    "不去", "先不去", "暂时不去", "目前不去", "不打算去",
)
_VISIT_CLAUSE_PATTERN = re.compile(r"[。；;！!？?，,\n]")
_QUESTION_MARKERS = ("是否", "要不要", "需不需要", "该不该", "用不用")
#: 就医建议通常带有这些祈使/建议词；仅有「医院」等名词不够
_VISIT_ADVICE_CUES = (
    "建议", "推荐", "请", "需要", "需", "必须", "务必",
    "尽快", "立即", "及时", "马上", "最好", "可以", "前往",
    "应", "应该", "应当", "尽早", "必要时",
)
#: 紧贴就医建议前的祈使动词
_VISIT_ENDING_CUES = ("去", "到", "送", "再")
#: 描述既往检查/就诊经历的分句，不是对当前患者的就医建议
_VISIT_DESCRIPTIVE_MARKERS = (
    "看到", "听到", "发现", "检查", "记录", "结果",
    "之前", "既往", "曾经", "到过", "去过", "住过", "做过",
)
#: “120”只有出现在呼救语境中才算就医建议，避免血压 120/80 误判
_CALL_120_PATTERN = re.compile(
    r"(?:拨打|打|叫|呼叫|致电)\s*(?:急救电话|急救中心|救护车)?\s*120"
    r"|120\s*(?:急救|救护车)"
)
#: 科普性描述里的红旗词不表示该患者正处于急症
EDUCATIONAL_MARKERS = (
    "可导致", "可能导致", "可引起", "多见于", "多发生于", "好发于",
    "常见于", "属于常见", "属常见", "典型表现", "临床表现",
)


def clinical_statement_text(text: str) -> str:
    """去掉科普性分句，只保留描述患者当前情况的文本。"""
    text = (text or "").strip()
    if not text:
        return ""
    clauses = re.split(r"[。；;！!？?\n]", text)
    kept = [
        clause for clause in clauses
        if clause and not any(marker in clause for marker in EDUCATIONAL_MARKERS)
    ]
    return "。".join(kept)


def has_visit_recommendation(text: str) -> bool:
    """输出中是否包含非否定的就医/急诊建议。

    按分句和就近否定判断；问句（“是否需要去医院？”）不算建议，
    单独的“120”只有在拨打急救电话的语境中才算。
    """
    text = (text or "").strip()
    if not text:
        return False

    for sentence in _VISIT_CLAUSE_PATTERN.split(text):
        if not sentence:
            continue
        for term in VISIT_ADVICE_TERMS:
            start = sentence.find(term)
            while start != -1:
                prefix = sentence[:start]
                negated = any(
                    negation in prefix for negation in _VISIT_NEGATIONS
                )
                questioning = any(
                    marker in prefix for marker in _QUESTION_MARKERS
                ) or sentence.rstrip().endswith(("吗", "呢"))
                descriptive = (
                    (
                        any(
                            marker in prefix
                            for marker in _VISIT_DESCRIPTIVE_MARKERS
                        )
                        or (
                            not prefix.strip()
                            and any(
                                marker in sentence
                                for marker in _VISIT_DESCRIPTIVE_MARKERS
                            )
                        )
                    )
                    and not any(
                        cue in prefix for cue in _VISIT_ADVICE_CUES
                    )
                )
                advised = (
                    not prefix.strip()
                    or any(cue in prefix for cue in _VISIT_ADVICE_CUES)
                    or prefix.rstrip().endswith(_VISIT_ENDING_CUES)
                )
                if (
                    not negated
                    and not questioning
                    and advised
                    and not descriptive
                ):
                    return True
                start = sentence.find(term, start + len(term))
    return bool(_CALL_120_PATTERN.search(text))

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
#: 纯跌倒本身不等于头部外伤；只有确实撞到/碰到头才按头部外伤处理
HEAD_TRAUMA_TERMS = (
    "头部外伤", "头部受伤", "头部撞击", "头部被撞", "头部着地",
    "头部撞到", "撞到头部", "撞到头", "碰到头部", "碰到头",
    "磕到头部", "磕到头", "头碰到", "头撞到", "头磕到", "头着地",
    "摔到头", "后脑勺着地", "后脑着地", "后脑勺撞", "后脑勺磕",
    "后脑勺碰", "磕破头", "磕破了头", "撞破头", "撞破了头",
    "脑袋磕", "脑袋撞", "脑袋碰", "头磕破", "头撞破",
)

#: 家族史/他人病史不能当作患者本人的当前状态
_FAMILY_HISTORY_MARKERS = (
    "父亲", "母亲", "父母", "家人", "家族", "爷爷", "奶奶", "外公", "外婆",
    "丈夫", "妻子", "配偶", "哥哥", "姐姐", "弟弟", "妹妹", "儿子", "女儿",
)

#: 已经结束、停用、否认或仅为过敏的病史/用药不属于当前高危状态
_CONTEXT_INACTIVE_MARKERS = (
    "已结束", "结束后", "已停", "停用", "停药", "停了", "已完成",
    "不再", "未使用", "没有使用", "未服用", "没有服用", "未吃", "没有吃",
    "否认", "未曾", "从未", "过敏",
)

#: 「未停用/不能停药」表示仍在用药，不能按“已停用”过滤
_NEGATED_STOP_PATTERN = re.compile(
    r"(?:未|没|没有|不能|不可|不宜|不让|不应|不要|别|无需|不必|并非)"
    r"[^，。；]{0,4}(?:停用|停药|停服|停了|停止服用)"
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
            if not _is_negated(prefix):
                return True
            start = text.find(term, start + len(term))

    for match in TEMPERATURE_PATTERN.finditer(text):
        prefix = _clause_prefix(text, match.start())
        if not _is_negated(prefix):
            return True
    return False


def _active_medical_text(context: Dict[str, Any]) -> str:
    """拼接当前仍生效的病史与用药。

    按分句过滤家族史、他人病史、否定表述、过敏史以及已结束/已停用的
    治疗，避免把「父亲化疗」「未使用抗凝药」当作患者本人的高危状态。
    """
    raw = " ".join(
        part for part in (
            _flatten(context.get("history")).lower(),
            medication_context_text(context).lower(),
        ) if part
    )
    clauses = re.split(r"[，。；;,.!?！？\n]", raw)
    kept = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if any(marker in clause for marker in _FAMILY_HISTORY_MARKERS):
            continue
        if _NEGATED_STOP_PATTERN.search(clause):
            # 「未停用华法林」是仍在用药，不能被“停用”关键字过滤掉
            kept.append(clause)
            continue
        if any(marker in clause for marker in _CONTEXT_INACTIVE_MARKERS):
            continue
        kept.append(clause)
    return " ".join(kept)


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

    medical_text = _active_medical_text(context)
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
        and any(_positive_mention(text, term) for term in HEAD_TRAUMA_TERMS)
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
        guard = _PHRASE_GUARDS.get(phrase)
        start = text.find(phrase)
        while start != -1:
            if guard is not None and not guard.match(text, start):
                start = text.find(phrase, start + len(phrase))
                continue
            prefix = _clause_prefix(text, start)
            if not _is_negated(prefix):
                if label not in matches:
                    matches.append(label)
                break
            start = text.find(phrase, start + len(phrase))

    if (
        any(term in text for term in PREGNANCY_TERMS)
        and any(
            _positive_mention(text, term)
            for term in PREGNANCY_BLEEDING_TERMS
        )
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
