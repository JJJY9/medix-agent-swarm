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
    ("意识丧失", "意识丧失"),
    ("不省人事", "意识丧失"),
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
    ("咳血", "咯血"),
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

#: 否定词与短语之间允许的填充词；普通否定与「排除/除外」双重否定共用
_NEGATION_FILLERS = (
    "出现|伴有|伴随|伴|感|有|见|见到|明显|任何|明确|发生|"
    "诉说|诉|特殊|显著|疑似|表现|是|过|曾经|曾|为|存在|得了|会|"
    "可能|一定|肯定|真的|确实"
)

#: 否定结构必须紧贴短语，且排除「无诱因」「不能缓解」「不舒服」这类
#: 正常修饰语，否则真实的急症描述会被误判成否定
_NEGATION_PATTERN = re.compile(
    r"(?:没有|没|无|未|否认|不是|并非|非|排除|不考虑|不(?!过))"
    rf"(?:{_NEGATION_FILLERS})*"
    r"(?:的)?\s*$"
)
#: A 不 A 疑问（是不是/有没有/是否…）表示不确定，按阳性处理：
#: 「不知道是不是胸痛」不是否认胸痛，不能静默漏报
_UNCERTAIN_QUESTION_RE = re.compile(
    r"(?:是不是|有没有|要不要|会不会|能不能|可不可以|是否|有无)"
)
#: 「不是没有/并非没有」是双重否定，实际为阳性
_DOUBLE_NEGATION_SUFFIX_PATTERN = re.compile(
    r"(?:不是|并非|并非说|不能说|不代表|又不是)\s*(?:没有|没|无)\s*$"
)
#: 「差点没昏倒」是“几乎发生”，不是否定
_ALMOST_PATTERN = re.compile(
    r"(?:差点|险些|几乎|差一点|差些)\s*(?:没有|没)?\s*$"
)
#: 「从未有过的胸痛」「前所未有的呼吸困难」是“从未如此”的最高级
#: 描述，实际为阳性，不能被否定填充词误判
_SUPERLATIVE_NOVELTY_RE = re.compile(
    r"(?:从未有过|从来没有过|从没有过|未曾有过|前所未有|"
    r"从未出现过|从未像这样|第一次出现)[^，。；]{0,2}的\s*$"
)

#: 「不排除/不能排除 X」表示 X 仍有可能，不能当成否定
_DOUBLE_NEGATION_SUFFIXES = (
    "不排除", "不能排除", "无法排除", "未排除",
    "不除外", "不能除外", "无法除外", "未除外",
)
#: 「排除/除外」前还有否定词时同样是双重否定（"尚未完全排除""暂不除外"）
_RULED_OUT_SUFFIX_PATTERN = re.compile(
    r"(?:"
    r"(?:尚|还|暂|暂时|并|绝|从|也)?"
    r"(?:不|未|没|无)"
    r"(?:能|可以|可|有|法)?"
    r"(?:完全|彻底|明确|确切|轻易|地)*"
    r"|难以|不容易|不好"
    r")$"
)
#: 「排除/除外」及其后常见完成态/系词/填充词；双重否定判断需先定位这段。
#: 与 _NEGATION_PATTERN 共用填充词，「不排除有明显胸痛/有过晕厥」也能命中
_RULED_OUT_TAIL = re.compile(
    r"(?:排除|除外)(?:了|掉|啦)?\s*"
    rf"(?:(?:{_NEGATION_FILLERS})\s*)*"
    r"(?:的)?\s*$"
)

#: 出现这些词时，同一句里的「出血」按孕产期出血处理
PREGNANCY_TERMS = (
    "怀孕", "孕期", "妊娠", "产妇", "产后", "孕妇", "孕产妇",
    "早孕", "中孕", "晚孕", "孕早期", "孕中期", "孕晚期",
    "产前", "待产", "临产",
    # 明确的妊娠相关表述：宫外孕/孕吐/产检等出现出血时同样是
    # 产科急症高风险场景
    "宫外孕", "异位妊娠", "孕吐", "孕检", "产检", "葡萄胎",
    "难免流产", "先兆流产", "胎停", "胎停育", "稽留流产",
)
#: 「孕12周」「孕 28 周」「孕4个月」等写法（排除避孕/备孕等）
PREGNANCY_WEEK_PATTERN = re.compile(
    r"(?<![避备不待])(?:怀)?孕\s*[0-9一二两三四五六七八九十]+\s*"
    # 「孕12+周」（12 周多）、「孕12-14周」（筛查孕周范围）、「孕20w」
    r"(?:[+＋]\s*\d*)?"
    r"(?:\s*[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"\s*(?:周|[wW]|个?月|天)"
)

#: 询问/怀疑怀孕时同样属于需要按孕产期评估的情况
PREGNANCY_UNCERTAIN_PATTERN = re.compile(
    r"(?:有没有|是不是|有无|怀没)\s*怀孕"
    r"|(?:不确定|不知道|不清楚)(?:是否|是不是|有没有)?\s*怀孕"
)
PREGNANCY_UNCERTAIN_TERMS = (
    "有没有怀孕", "是否怀孕", "有无怀孕", "不知道有没有怀孕",
    "不确定是否怀孕", "可能怀孕", "怀疑怀孕", "会不会怀孕",
)

#: 孕产期出血只认与妊娠相关的出血部位，牙龈/鼻/皮肤出血不能算
PREGNANCY_BLEEDING_TERMS = (
    "阴道出血", "阴道流血", "阴道大量出血", "产道出血",
    "下体出血", "见红", "早孕出血", "孕早期出血", "孕中期出血",
    "孕晚期出血", "产前出血", "孕妇出血",
)
#: 量词插入「阴道」与「出血」之间（阴道少量出血/阴道有点流血）仍属
#: 孕产期出血；牙龈/鼻/皮肤出血没有这些部位词，不会命中。妊娠词分支
#: 允许跨逗号并覆盖「孕8周」「产后/产妇」等常见简写
_PREGNANCY_BLEEDING_RE = re.compile(
    r"(?:阴道|产道|下体)"
    r"[^，。；,;！!？?\n]{0,4}(?:出血|流血|见红)"
    r"|(?:孕妇|产妇|孕产妇|怀孕|孕期|产前|产后|早孕|孕[早中晚]期"
    r"|宫外孕|异位妊娠|孕吐|孕检|产检|葡萄胎|先兆流产|难免流产"
    r"|胎停育?|稽留流产"
    r"|孕\s*[0-9一二两三四五六七八九十]+"
    r"\s*(?:[+＋]\s*\d*)?"
    r"(?:\s*[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"\s*(?:周|[wW]|个?月|天))"
    r"[^。；;！!？?\n]{0,8}(?:出血|流血|见红)"
    r"|(?:出血|流血)[^，。；,;！!？?\n]{0,2}(?:阴道|产道)"
    r"|见红"
)

#: 口语出血表述：「阴道流了一点血」「有少量血」「内裤上有点血」
#: 「擦到血」「血丝」；排除血常规/血压/血糖等非出血词
_COLLOQUIAL_BLEEDING_RE = re.compile(
    r"(?:阴道|产道|下体|下面|下身|内裤上?|护垫|卫生巾上?|纸巾上?|擦拭?)"
    r"[^，。；,;！!？?\n]{0,6}"
    r"血(?!常规|压|糖|脂|型|沉|氧|液|管|检|培养|友|蛋白|小板|栓|肿)"
    r"|(?:有一点|有一些|有点|有些|少量|少许|带有?|擦到|看到|见到|"
    r"流了?|出了?|有)"
    r"[^，。；,;！!？?\n]{0,3}"
    r"血(?!常规|压|糖|脂|型|沉|氧|液|管|检|培养|友|蛋白|小板|栓|肿)"
    r"|血丝"
)


def _has_pregnancy_bleeding(text: str) -> bool:
    for match in _PREGNANCY_BLEEDING_RE.finditer(text):
        span = match.group(0)
        prefix = _clause_prefix(text, match.start())
        # 匹配内部含紧邻出血词的否定（阴道没有出血/阴道未见出血/
        # 阴道不出血）或明确是牙龈/鼻/皮肤出血时不算孕产期出血。
        # 只认出血词之前的否定：「不规则/无痛性/非常」等修饰是阳性
        bleed_body = re.sub(
            r"(?:出血|流血|见红)\s*$", "", span
        )
        internal_body = re.sub(
            r"(?:阴道|产道|下体)\s*$", "", bleed_body
        )
        if re.search(
            r"(?:没有|没|无|未|不|否认|非)"
            r"(?:(?:明显|任何|见|见到|出现|发现)\s*)*$",
            internal_body,
        ) is not None:
            continue
        # 部位明确的匹配（阴道出血/产道出血）只看匹配片段本身：
        # 前置枚举里的「牙龈出血、」不能吞掉后面的阴道出血
        location_match = span.startswith(
            ("阴道", "产道", "下体")
        ) or span.endswith(("阴道", "产道"))
        gum_words = _GUM_BLEEDING_WORDS
        if location_match:
            gum_hit = any(word in span for word in gum_words)
        else:
            # 非部位匹配只看顿号/逗号后的最后一个枚举项：前面的
            # 「牙龈出血、」不能吞掉后面的孕产期「见红」
            prefix_tail = re.split(
                r"[，。；;,.!?！？、\n]", prefix
            )[-1]
            gum_hit = any(
                word in span or word in prefix_tail[-6:]
                for word in gum_words
            )
        if gum_hit:
            continue
        if not _is_negated(prefix):
            return True
    location_terms = (
        "阴道出血", "阴道流血", "阴道大量出血", "产道出血", "下体出血",
    )
    for term in PREGNANCY_BLEEDING_TERMS:
        start = text.find(term)
        while start != -1:
            prefix = _clause_prefix(text, start)
            if term in location_terms:
                gum_hit = any(
                    word in term for word in _GUM_BLEEDING_WORDS
                )
            else:
                prefix_tail = re.split(
                    r"[，。；;,.!?！？、\n]", prefix
                )[-1]
                gum_hit = any(
                    word in prefix_tail[-6:]
                    for word in (
                        "牙龈", "鼻腔", "鼻子", "鼻", "耳", "皮肤", "痔",
                    )
                )
            if not gum_hit and not _is_negated(prefix):
                return True
            start = text.find(term, start + len(term))
    if _colloquial_bleeding_positive(text):
        return True
    return False


_GUM_BLEEDING_WORDS = (
    "牙龈", "鼻腔", "鼻子", "鼻", "耳", "皮肤", "痔", "口腔",
    # 口腔动作：刷牙/漱口/吐口水带血同样是牙龈或口腔黏膜出血
    "刷牙", "牙刷", "漱口", "吐口水", "剔牙", "牙缝", "泡沫带血",
)


def _generic_bleeding_positive(text: str) -> bool:
    """孕产妇主诉里任何未否定的出血/流血/见红（牙鼻皮肤除外）。

    妊娠状态可能只在档案病史里，主诉只说「今天出血了」：此时不能
    要求出血前必须出现妊娠词。
    """
    for term in ("出血", "流血", "见红"):
        start = text.find(term)
        while start != -1:
            prefix = _clause_prefix(text, start)

            def _bleeding_negated(value: str) -> bool:
                return (
                    re.search(
                        r"(?:没有|没|无|未|不|否认|非)"
                        r"(?:(?:明显|任何|见|见到|出现|发现)\s*)*$",
                        value,
                    ) is not None
                    or _is_negated(value)
                )

            location_stripped = re.sub(
                r"(?:阴道|产道|下体)\s*$", "", prefix
            )
            if (
                any(word in prefix[-6:] for word in _GUM_BLEEDING_WORDS)
                or _bleeding_negated(prefix)
                or (
                    location_stripped != prefix
                    and _bleeding_negated(location_stripped)
                )
            ):
                start = text.find(term, start + len(term))
                continue
            return True
    if _colloquial_bleeding_positive(text):
        return True
    return False


def _colloquial_bleeding_positive(text: str) -> bool:
    """口语出血表述（流了一点血/有少量血/血丝等）是否阳性。"""
    for match in _COLLOQUIAL_BLEEDING_RE.finditer(text):
        span = match.group(0)
        prefix = _clause_prefix(text, match.start())
        body = re.sub(r"血丝?$", "", span)
        # 模式只在「血」前停住：先剥离残留的出血动词（出/流/见/了），
        # 才能看见紧贴其前的否定（阴道没有出血/阴道没有流血）
        body = re.sub(r"(?:出|流|见|了)+$", "", body)
        body = re.sub(
            r"(?:阴道|产道|下体|下面|下身|内裤上?|护垫|卫生巾上?|"
            r"纸巾上?|擦拭?)\s*$",
            "", body,
        )
        if re.search(
            r"(?:没有|没|无|未|不|否认|非)"
            r"(?:(?:明显|任何|见|见到|出现|发现|一点|一些|少量)\s*)*$",
            body,
        ) is not None:
            continue
        if any(
            word in span or word in prefix[-6:]
            for word in _GUM_BLEEDING_WORDS
        ):
            continue
        if not _is_negated(prefix):
            return True
    return False


#: 病史里的既往产科史（不再代表当前妊娠）。注意不要包含
#: 「已控制/妊娠期糖尿病」这类与当前妊娠并存的诊断/治疗状态
_HISTORY_PAST_OBSTETRIC = re.compile(
    r"(?:曾经|以前|去年|前年|多年前|既往|已流产|已结束|"
    r"产后\s*[0-9一二两三四五六七八九十]+\s*年|产后抑郁)"
)


def _current_history_text(history: str) -> str:
    """过滤掉病史中的既往产科记录，只保留可能代表当前妊娠的分句。"""
    kept = []
    for clause in re.split(r"[，。；;,.!?！？\n]", history):
        if _HISTORY_PAST_OBSTETRIC.search(clause):
            continue
        kept.append(clause)
    return "；".join(kept)


def _is_negated(prefix: str, suffix: str = "") -> bool:
    """前缀是否以否定结构结束（"没有"、"未见"、"无"…）。

    suffix 为短语之后、同一分句内的有限文本：枚举否定后面若跟着
    「加重/伴大汗/放射/不缓解」等阳性修饰（「无发热、咳嗽、胸痛加重」），
    说明最后一项其实是阳性描述，不能整组按否定处理。
    """
    if _ALMOST_PATTERN.search(prefix):
        return False
    stripped = prefix.rstrip()
    if _SUPERLATIVE_NOVELTY_RE.search(stripped):
        # 「从未有过的胸痛」是新发阳性描述，不是否定
        return False
    if _UNCERTAIN_QUESTION_RE.search(stripped):
        # 「不知道是不是胸痛」「有没有胸痛」「是否呼吸困难」是疑似，
        # 与妊娠不确定的既有口径一致：按阳性处理，不能漏报
        return False
    if _DOUBLE_NEGATION_SUFFIX_PATTERN.search(stripped):
        # 「不是没有胸痛」「并非没有呼吸困难」实际是阳性
        return False
    if any(
        stripped.endswith(term) for term in _DOUBLE_NEGATION_SUFFIXES
    ):
        # 「不排除酮症酸中毒」是阳性警示，不是否定
        return False
    ruled_out = _RULED_OUT_TAIL.search(stripped)
    if ruled_out:
        stem = stripped[:ruled_out.start()].rstrip()
        if _RULED_OUT_SUFFIX_PATTERN.search(stem):
            # 「尚未完全排除是…」「暂时不除外为…」同样表示仍有可能
            return False
        # 「医生排除了酮症酸中毒」是已完成排除，按否定处理
        return True
    if re.search(
        r"(?:不|未|没|尚未|暂未|不能|无法|难以|别)"
        r"[^，。；,.!?！？\n]{0,4}(?:排除|除外)",
        stripped,
    ):
        # 「未排除发热和胸痛」：枚举项里的双重否定同样是阳性，
        # 不能按枚举否定吞掉后续症状
        return False
    if _NEGATION_PATTERN.search(prefix):
        return True
    enum_negated = _enum_negated(prefix)
    if enum_negated and suffix and _post_modifier_positive(suffix):
        return False
    return enum_negated

#: 分句标点：否定判断只在同一分句内进行，避免上一句的「不」误伤下一句
CLAUSE_DELIMITERS = "，。；！？、,.!?;\n"
#: 前缀回溯允许跨过顿号：否定常以枚举形式覆盖整组症状
#: （「无发热、咳嗽、胸痛」），顿号不应截断否定范围
_PREFIX_DELIMITERS = CLAUSE_DELIMITERS.replace("、", "")

#: 枚举否定：「没有发烧和胸痛」「无发热、咳嗽及胸痛」「否认胸痛和呼吸困难」。
#: 只允许否定词 + 短症状项 + 枚举连接词，最后一项可以以连接词收尾
#: （短语本身就是最后一项）。含谓词/转折词（突发、加重、但…）时不匹配，
#: 避免把「无诱因、突发胸痛」误判为否定。
#: 只用完整否定词：裸「不」常出现在「说话不清/不适」等形容词里
_ENUM_NEGATION_PATTERN = re.compile(
    r"(?:没有|没|无|未|否认)"
    r"(?:出现|伴有|伴随|感|有|见|见到|任何|明显|明确|特殊|显著)?"
    r"[^，。；！？,.!?;\n但|]{1,8}?"
    r"(?:(?:、|和|及|与|以及|或|或者)"
    r"[^，。；！？,.!?;\n但|]{0,8}?)*"
    r"(?:、|和|及|与|以及|或|或者)?\s*$"
)
#: 枚举项里出现这些词时按阳性处理（症状修饰/转折/加重）
_ENUM_POSITIVE_CUES = (
    "突发", "剧烈", "加重", "持续", "缓解", "出现", "但", "却",
    "越来越", "不能", "无法", "伴有", "伴随", "放射", "撕裂",
    "压榨", "难忍", "明显", "反复",
)


def _clause_prefix(text: str, start: int, lookback: int = 24) -> str:
    """返回短语之前、同一分句内的有限长度前缀。

    窗口需容纳「尚未能完全排除」这类较长的双重否定结构；顿号不截断
    前缀，让枚举否定（「无发热、咳嗽、胸痛」）能看到整组症状。
    """
    boundary = start
    while boundary > 0 and text[boundary - 1] not in _PREFIX_DELIMITERS:
        boundary -= 1
    return text[max(boundary, start - lookback):start]


#: 短语之后的阳性修饰：枚举否定不能吞掉「胸痛加重/伴大汗/放射…」
_POST_POSITIVE_CUES = (
    "加重", "加剧", "恶化", "难忍", "剧痛", "大汗", "出汗",
    "放射", "越来越", "不缓解", "不能缓解", "持续不缓解",
)


def _clause_suffix(text: str, start: int, lookahead: int = 12) -> str:
    """返回短语之后、同一分句内的有限长度文本（顿号即截断）。"""
    boundary = start
    while (
        boundary < len(text)
        and text[boundary] not in CLAUSE_DELIMITERS
    ):
        boundary += 1
    return text[start:min(boundary, start + lookahead)]


def _post_modifier_positive(suffix: str) -> bool:
    return any(cue in suffix for cue in _POST_POSITIVE_CUES)


def _enum_negated(prefix: str) -> bool:
    """否定词是否以枚举形式覆盖到前缀末尾（含「和/、/及」连接）。"""
    match = _ENUM_NEGATION_PATTERN.search(prefix)
    if not match:
        return False
    span = match.group(0)
    # 必须存在显式枚举连接词，否则交给 _NEGATION_PATTERN 判断，
    # 避免把「无明显诱因的」这类普通修饰当成枚举否定
    if not any(sep in span for sep in ('、', '和', '及', '与', '以及', '或', '或者')):
        return False
    # 否定词后的第一项必须是常见症状/体征词：「全身无力、」「没有
    # 力气、」「无诱因、」「未婚、」里的无/没/未是词的一部分，不能
    # 让它否定后面的真实症状
    neg_word = re.match(r"(?:没有|没|无|未|否认)", span)
    body = span[neg_word.end():] if neg_word else span
    body = re.sub(
        r"^(?:出现|伴有|伴随|感|有|见|见到|任何|明显|明确|特殊|显著)",
        "",
        body,
    )
    first_item = re.split(r"、|和|及|与|以及|或|或者", body, maxsplit=1)[0]
    if not any(term in first_item for term in _NEGATED_SYMPTOM_TERMS):
        return False
    # 阳性线索只在剥离否定词与填充词后的枚举正文上判断：
    # 「没有出现胸痛和呼吸困难」里紧跟否定词的「出现」不是阳性线索
    return not any(cue in body for cue in _ENUM_POSITIVE_CUES)


def _positive_mention(text: str, phrase: str) -> bool:
    """短语是否在同一分句内以非否定形式出现。"""
    start = text.find(phrase)
    while start != -1:
        prefix = _clause_prefix(text, start)
        suffix = _clause_suffix(text, start + len(phrase))
        if not _is_negated(prefix, suffix):
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
#: 就医短语前的否定窗口：否定词出现在前 12 字内，且其后到短语之间
#: 不能有建议提示词（"没有明显诱因也应去医院"是建议）。
#: 长词优先匹配，覆盖「没能及时/没来得及/没办法/没人送我去」等自然语序
_VISIT_NEGATION_WORD = re.compile(
    r"(?:没来得及|没有及时|没能及时|未能及时|没有能|没有办法|没办法"
    r"|没时间|没有时间|来不及|尚未|还没|并未|未曾|从未|并无|无法|不能"
    r"|没|未|无)"
)
#: 「没严重到必须/未到需要……的程度」式阈值否定（含拨打 120 的劝阻）
_VISIT_NOT_SEVERE_PATTERN = re.compile(
    r"(?:没有|没|未)(?:有)?(?:严重|厉害)?到"
    r"(?:必须|需要|要|非得|一定|该|得)?"
    r"(?:去|到|送|再|赶|带|叫|打|拨)?\s*$"
)
#: 否定词之后出现这些词说明是在给建议，不是否定描述
_VISIT_CUE_IN_TAIL = re.compile(
    r"(?:建议|推荐|请|需要|需|必须|务必|尽快|立即|马上|及时|最好|可以"
    r"|前往|应(?:该|当)?|尽早|必要时|赶快)"
)

#: 就医词之后的后置否定：「医院就别去了」「医院不用去了」「急诊别去了」。
#: 句首就是「医院/急诊」时前缀为空，只有检查后缀才能识别劝阻。
_VISIT_POST_NEGATION = re.compile(
    r"^\s*(?:就|还是|也|都|最好|先|暂时|以后再)?\s*"
    r"(?:别|不用|不必|不需要|没必要|无需|用不着|不要|不能|不可以)"
    r"\s*(?:再|先|急着|着急)?\s*(?:去|到|跑|折腾)?\s*(?:了)?"
)
#: 「去医院也没用/医院去了也白去」式劝阻
_VISIT_USELESS_NEGATION = re.compile(
    r"^\s*(?:去|到|再)?\s*(?:了)?\s*(?:也|都)?\s*"
    r"(?:没用|没有用|没意义|没有意义|白去|白搭|来不及)"
)


def _visit_suffix_negated(sentence: str, end: int) -> bool:
    """就医词之后是否紧跟劝阻语（且其后没有再给出建议）。"""
    tail = sentence[end:end + 16]
    match = None
    for pattern in (_VISIT_POST_NEGATION, _VISIT_USELESS_NEGATION):
        found = pattern.search(tail)
        if found and (match is None or found.end() > match.end()):
            match = found
    if match is None:
        return False
    rest = sentence[end + match.end():end + match.end() + 12]
    return not _VISIT_CUE_IN_TAIL.search(rest)


def _visit_prefix_negated(prefix: str) -> bool:
    """就医短语前的分句是否是否定描述（就医建议与 120 呼救共用）。"""
    tail = prefix.rstrip()[-12:]
    if _VISIT_NOT_SEVERE_PATTERN.search(tail):
        return True
    match = _VISIT_NEGATION_WORD.search(tail)
    if not match:
        return False
    between = tail[match.end():]
    return not _VISIT_CUE_IN_TAIL.search(between)
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
    # 定义/科普句式：解释疾病是什么，不等于患者当前处于该状态
    "是指", "是一种", "称为", "也叫", "定义为", "表现为",
    "典型症状", "主要表现", "常见症状", "症状包括",
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


#: 出现这些词说明在描述本人/家人当前情况，不能当科普问句过滤
_PATIENT_CONTEXT_MARKERS = (
    "我", "本人", "孩子", "儿子", "女儿", "父亲", "母亲", "爸", "妈",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷", "家人", "老人",
    "宝宝", "大宝", "二宝", "小孩", "患儿", "患者", "病患",
    "老婆", "老公", "爱人", "对象", "丈夫", "妻子", "配偶", "老伴",
    "哥哥", "姐姐", "弟弟", "妹妹", "兄弟", "姐妹",
    "阿姨", "叔叔", "舅舅", "姑姑", "婶婶", "侄子", "外甥",
    "孙子", "孙女",
    "最近", "这两天", "今天", "昨天", "前天", "持续", "伴有",
    "突然", "出现", "发作", "测得", "体温", "吃了", "服了", "正在吃",
    "现在", "目前", "这几天",
)
#: 真正的患者主语（不含「今天/最近/持续」等时间副词）：
#: 只有这些词或正向红旗症状才把后置定义问句当作新症状保留
_PATIENT_SUBJECT_MARKERS = (
    "我", "本人", "孩子", "儿子", "女儿", "父亲", "母亲", "爸", "妈",
    "爷爷", "奶奶", "外公", "外婆", "姥姥", "姥爷", "家人", "老人",
    "宝宝", "大宝", "二宝", "小孩", "患儿", "患者", "病患",
    "老婆", "老公", "爱人", "对象", "丈夫", "妻子", "配偶", "老伴",
    "哥哥", "姐姐", "弟弟", "妹妹", "兄弟", "姐妹",
    "阿姨", "叔叔", "舅舅", "姑姑", "婶婶", "侄子", "外甥",
    "孙子", "孙女",
)

#: 纯定义引导词开头的分句：整句丢弃（什么是X / 请解释X）
_DEFINITION_PREFIX_RE = re.compile(
    r"^(?:什么是|啥是|什么叫|啥叫|何谓|何为|请解释|解释一下|介绍一下|讲讲)"
)
#: 特征/信息型问句：X有哪些症状（前缀是症状时保留）
_FEATURE_QUESTION_RE = re.compile(
    r"(?:有哪些(?:症状|表现|原因|危害)|有什么(?:症状|表现|危害))"
)
#: 「X是什么原因/怎么回事」只去掉疑问尾巴，保留症状描述
_CAUSE_TAIL_RE = re.compile(
    r"是?(?:什么原因|怎么回事|咋回事)"
    r"(?:造成的|引起的|导致的)?\s*[呢呀啊吧嘛吗]?\s*[？?]?$"
)
#: 「X是什么」纯定义问句（含「是什么病/是指什么/是啥」等变体）
#: 第一组是定义尾巴之前的描述，用于保留「发烧、咳嗽三天了是什么病」
_DEFINITION_SUFFIX_RE = re.compile(
    r"^([^，,；;。！？!?\n]*)(?:是什么|啥是|是什么意思|什么意思|"
    r"是指什么|指什么|是啥|是什么病|是啥病|是什么疾病|是啥疾病)"
    r"\s*[呢呀啊吧嘛吗]?\s*[？?]?$"
)
#: 口语化「提问前缀」：去掉后按剩余内容重新判断，避免「我想知道什么是
#: 咯血」因含「我」被当成患者主诉而触发急症横幅
_EDUCATIONAL_PREFIX_RE = re.compile(
    r"^(?:请问(?:一下)?|我想知道|我想问(?:一下)?|想问(?:一下)?|"
    r"想了解(?:一下)?|了解一下|听说|我听说|据说)"
)
#: 传闻式语句整句按科普处理，不构成患者本人当前主诉
_HEARSAY_PREFIX_RE = re.compile(r"^(?:听说|我听说|据说)")
#: 病程/数量线索：定义问句前缀含这些词时按真实主诉保留
_COURSE_MARKER_RE = re.compile(
    r"[0-9一二两三四五六七八九十半几]+\s*(?:天|日|周|个月|月|小时|年)"
)
#: 定义问句前缀里出现这些场景/程度修饰时按真实主诉保留
#: （「活动后胸痛是什么病」「胸痛越来越重是什么病」「一侧肢体
#: 无力是什么病」都在描述患者的当前症状组合，不是纯术语询问）
_DEFINITION_KEEP_CUES = (
    "活动后", "活动时", "活动", "劳累", "走路", "行走", "爬楼",
    "夜间", "夜里", "半夜", "睡前", "晨起", "早上",
    "深呼吸", "呼吸时", "按压", "触碰",
    "一侧", "单侧", "半边", "偏侧", "肢体",
    "加剧", "越来越", "加重",
)


_COURSE_MARKERS = (
    "持续", "反复", "最近", "今天", "昨天", "前天", "这几天", "刚才",
    "经常", "常常", "时常", "总是", "老是", "一直", "长期", "多年",
    "很久", "好久", "很长时间", "好几天", "数天", "数周", "数月",
    "近来", "这段时间",
)


def _is_red_flag_text(text: str) -> bool:
    if any(term in text for term, _ in RED_FLAG_PHRASES):
        return True
    if any(term in text for term in HEAD_TRAUMA_TERMS):
        return True
    # 孕产期出血同样是确定性急症信号（阴道出血/见红）
    return _has_pregnancy_bleeding(text)


def _clean_complaint_clause(
    clause: str, patient_context_seen: bool = False
) -> str:
    """清洗单个分句：有患者语境的保留；纯定义问句丢弃。

    patient_context_seen 表示前面已有明确患者主语的症状分句
    （「宝宝发烧，抽搐是什么病」）：此时后置定义问句的前缀是
    患者本人的新症状，不能当成纯定义丢弃。
    """
    educational = _EDUCATIONAL_PREFIX_RE.match(clause)
    if educational:
        if _HEARSAY_PREFIX_RE.match(clause):
            return ""
        rest = clause[educational.end():].strip(" ，,、")
        if not rest:
            return ""
        # 去掉「请问/我想知道」后按剩余内容重新判断：「什么是咯血」仍是
        # 纯定义问句，而「胸痛两天了是什么病」仍按病程主诉保留
        return _clean_complaint_clause(rest, patient_context_seen)
    definition_lead = _DEFINITION_PREFIX_RE.match(clause)
    if definition_lead:
        # 句首定义引导词（什么是/什么叫/请解释一下…）：剥掉前缀后
        # 若剩余内容含患者主体或病程线索，按真实主诉继续判断
        rest = clause[definition_lead.end():].strip(" ，,、")
        if not rest:
            return ""
        if _has_patient_subject(rest):
            # 只有明确的患者主体（我/宝宝/孩子…）才说明在描述本人；
            # 「什么是持续性胸痛」仍是术语解释
            return _clean_complaint_clause(rest, patient_context_seen)
        # 纯术语解释（介绍一下呼吸困难/什么是咯血）仍按科普丢弃
        return ""
    if any(marker in clause for marker in _PATIENT_CONTEXT_MARKERS):
        return clause
    if any(marker in clause for marker in EDUCATIONAL_MARKERS):
        # 「表现为/症状包括」既可能是科普也可能是真实主诉：含红旗
        # 且不是定义问句时必须保留，否则 ACS/呼吸困难会静默漏报
        if (
            _is_red_flag_text(clause)
            and not _DEFINITION_PREFIX_RE.match(clause)
            and not _DEFINITION_SUFFIX_RE.match(clause)
            and not _FEATURE_QUESTION_RE.search(clause)
        ):
            return clause
        return ""
    feature = _FEATURE_QUESTION_RE.search(clause)
    if feature:
        prefix = clause[: feature.start()].strip(" ，,、")
        return prefix if _is_red_flag_text(prefix) else ""
    cause = _CAUSE_TAIL_RE.search(clause)
    if cause:
        return clause[: cause.start()].strip(" ，,、").rstrip("是").strip()
    definition = _DEFINITION_SUFFIX_RE.match(clause)
    if definition:
        prefix = definition.group(1).strip(" ，,、")
        # 「X是什么病」：X 是带病程的症状描述时按真实主诉保留，
        # 只有单一术语的纯定义问句才整句丢弃
        if prefix and (
            _COURSE_MARKER_RE.search(prefix) is not None
            or any(marker in prefix for marker in _COURSE_MARKERS)
            # 多症状枚举、客观测量值、头外伤或急性修饰都是真实主诉，
            # 不能因为带「是什么病」就整句丢弃；「伴/伴随/合并」
            # 与顿号/和/及/与同属多症状连接词
            or re.search(
                r"[、和及与]|伴|合并|并发", prefix
            ) is not None
            or re.search(r"\d", prefix) is not None
            or any(term in prefix for term in HEAD_TRAUMA_TERMS)
            or any(
                word in prefix
                for word in (
                    "突发", "突然", "剧烈", "加重", "无法", "不能",
                    "大量", "持续", "反复",
                    # 典型急症修饰：放射/压榨/冷汗等同样说明是真实主诉
                    "放射", "压榨", "撕裂", "大汗", "冷汗", "冒汗",
                    "难忍", "濒死", "躺不平", "不能平卧", "端坐呼吸",
                    "闷痛", "绞痛", "刀割",
                )
            )
            # 枚举/后置阳性线索（越来越/加剧/恶化…）与场景修饰
            # （活动后/夜间/走路时…）同样说明是在描述真实主诉
            or any(word in prefix for word in _POST_POSITIVE_CUES)
            or any(word in prefix for word in _DEFINITION_KEEP_CUES)
            # 前文已明确患者本人（「宝宝发烧，抽搐是什么病」）：
            # 单一红旗症状也按真实主诉保留
            or (patient_context_seen and _is_red_flag_text(prefix))
            # 孕产期出血无论是否有前文患者语境都按真实主诉保留：
            # 「阴道出血是什么病」在孕期档案下同样是急症描述
            or any(term in prefix for term in PREGNANCY_BLEEDING_TERMS)
        ):
            # 带病程/时间线索或多症状的定义问句通常是在描述当前症状
            return prefix
        return ""
    return clause


def patient_complaint_text(text: str) -> str:
    """提取「患者主诉」部分：去掉定义/科普型问句，保留症状描述。

    “什么是咯血？”没有患者语境，不能当作急症主诉；“咯血是什么？”
    同理；而“我父亲最近咯血是什么原因？”含患者语境，必须保留并触发
    红旗。按分句处理（含逗号/顿号）：含患者语境或症状描述的分句保留，
    只有纯定义问句才整句丢弃，避免“孩子发烧了，医生说是一种病毒感染”
    这类真实主诉被整段删除。
    """
    text = str(text or "").strip()
    if not text:
        return ""
    # 顿号不是分句边界：detect_red_flags 的枚举否定（「无发热、
    # 咳嗽、胸痛」）依赖整组顿号在同一分句内，不能在此拆开重拼
    clauses = re.split(r"[。；;！!？?\n，,]", text)
    kept = []
    context_seen = False
    for clause in clauses:
        cleaned = _clean_complaint_clause(
            clause.strip(), context_seen
        )
        if cleaned:
            kept.append(cleaned)
            subject_seen = any(
                marker in clause for marker in _PATIENT_SUBJECT_MARKERS
            )
            positive_flag = any(
                _positive_mention(clause, phrase)
                for phrase, _label in RED_FLAG_PHRASES
            )
            # 「发烧两天，抽搐是什么病」：没有主语但有真实症状的分句
            # 同样说明后置定义问句在描述患者本人
            symptom_seen = any(
                _positive_mention(clause, term)
                for term in _NEGATED_SYMPTOM_TERMS
            )
            if subject_seen or positive_flag or symptom_seen:
                # 已有患者本人的症状分句：后续定义问句按新症状保留。
                # 「今天想问问」「没有胸痛」这类铺垫/否定分句不算语境
                context_seen = True
    return "，".join(kept)


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
                negated = (
                    any(
                        negation in prefix for negation in _VISIT_NEGATIONS
                    )
                    or _visit_prefix_negated(prefix)
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
                if _visit_suffix_negated(sentence, start + len(term)):
                    # 「医院就别去了」：后缀是否定，不能算已建议就医
                    start = sentence.find(term, start + len(term))
                    continue
                if (
                    not negated
                    and not questioning
                    and advised
                    and not descriptive
                ):
                    return True
                start = sentence.find(term, start + len(term))
        for call in _CALL_120_PATTERN.finditer(sentence):
            prefix = sentence[:call.start()]
            tail = prefix.rstrip()[-10:]
            negated = (
                any(negation in tail for negation in _VISIT_NEGATIONS)
                or _visit_prefix_negated(prefix)
            )
            questioning = any(
                marker in prefix for marker in _QUESTION_MARKERS
            ) or sentence.rstrip().endswith(("吗", "呢"))
            if not negated and not questioning:
                return True
    return False

EMERGENCY_MESSAGE = (
    "描述中包含急症警示信号，请立即拨打 120 或前往急诊科，"
    "不要等待在线回复。若已出现意识不清、呼吸困难加重等情况，请立即呼救。"
)

#: 需要结合上下文判断的高危情况
FEVER_TERMS = (
    "发热", "发烧", "高烧", "低烧", "烧到", "高热", "烧了", "体温高",
)
TEMPERATURE_PATTERN = re.compile(
    # 有发热语境时接受「体温 39」「发烧 38.5 度」等写法
    r"(?:体温|发烧|发热|高烧|低烧|烧到)[^，。；！？\n]{0,8}"
    r"(?:3[89](?:\.\d+)?|4[0-2](?:\.\d+)?)\s*"
    r"(?:°\s*[cC]|℃|摄氏度|度)?"
    # 明确摄氏单位时可直接出现（仍需排除气温/水温等）；
    # 「38度5」「38.5度」等口语写法同样按体温处理
    r"|(?:3[89](?:\.\d+)?|4[0-2](?:\.\d+)?)\s*"
    r"(?:°\s*[cC]|℃|摄氏度|度)(?:[0-9](?![0-9]))?"
)
#: 枚举否定的首个症状项词表：否定词后必须先出现常见症状，避免把
#: 「全身无力、胸痛」「没有力气、胸痛」「无诱因、胸痛」「未婚、胸痛」
#: 里的词内「无/没/未」当成否定词覆盖真实症状（见 _enum_negated）
_NEGATED_SYMPTOM_TERMS = tuple(
    {phrase for phrase, _label in RED_FLAG_PHRASES}
    | set(FEVER_TERMS)
    | {
        "咳嗽", "咳痰", "胸闷", "气喘", "气促", "呼吸困难", "头痛",
        "头晕", "恶心", "呕吐", "腹痛", "腹泻", "便秘", "乏力",
        "食欲", "出汗", "盗汗", "心悸", "心慌", "水肿", "皮疹",
        "瘙痒", "尿频", "尿急", "尿痛", "血尿", "便血", "黑便",
        "失眠", "麻木", "抽搐", "晕厥", "咽痛", "鼻塞", "流涕",
        "关节痛", "腰痛", "背痛", "胸痛", "咯血", "呕血", "出血",
        "视物模糊", "言语不清", "意识不清",
        "喉咙痛", "嗓子痛", "寒战", "畏寒", "怕冷", "发冷", "发热感",
        "流鼻涕", "鼻涕", "打喷嚏", "嗜睡", "耳鸣", "口苦", "口干",
        "肌肉酸痛", "酸痛", "腹胀", "反酸", "烧心", "嗳气", "便溏",
        "食欲不振", "精神差", "犯困",
    }
)
IMMUNOSUPPRESSION_TERMS = (
    "化疗", "放疗", "免疫抑制", "器官移植", "骨髓移植", "造血干细胞移植",
    "艾滋病", "hiv", "长期激素", "长期使用激素", "甲氨蝶呤", "环孢素",
    "他克莫司", "吗替麦考酚酯", "生物制剂",
    # 英文表述（输入在 _active_medical_text 中已转小写）
    "chemotherapy", "chemo", "immunosuppression",
    "immunosuppressed", "organ transplant", "stem cell transplant",
    "methotrexate", "cyclosporine", "tacrolimus", "mycophenolate",
    "biologic",
)
ANTICOAGULANT_TERMS = (
    "华法林", "利伐沙班", "阿哌沙班", "达比加群", "艾多沙班",
    "肝素", "低分子肝素", "抗凝",
    # 英文药名（输入在 _active_medical_text 中已转小写）
    "warfarin", "rivaroxaban", "apixaban", "dabigatran",
    "edoxaban", "heparin", "enoxaparin", "anticoagulant",
)
#: 纯跌倒本身不等于头部外伤；只有确实撞到/碰到头才按头部外伤处理
HEAD_TRAUMA_TERMS = (
    "头部外伤", "头部受伤", "头部撞击", "头部被撞", "头部着地",
    "头部撞到", "撞到头部", "撞到头", "碰到头部", "碰到头",
    "磕到头部", "磕到头", "头碰到", "头撞到", "头磕到", "头着地",
    "摔到头", "后脑勺着地", "后脑着地", "后脑勺撞", "后脑勺磕",
    "后脑勺碰", "磕破头", "磕破了头", "撞破头", "撞破了头",
    "脑袋磕", "脑袋撞", "脑袋碰", "头磕破", "头撞破",
    # 反向语序：磕了头 / 头上撞了个包 / 头被撞了一下
    "磕了头", "撞了头", "碰了头", "磕了脑袋", "撞了脑袋", "碰了脑袋",
    "磕到脑袋", "撞到脑袋", "碰到脑袋", "磕了一下头", "头磕了一下",
    "头撞了一下", "头碰了一下", "头被撞", "头被磕", "头被碰",
    "头上撞", "头上磕", "头上碰", "头撞了个包", "头上撞了个包",
    "头上磕了个包",
    "撞了一下头", "撞了下头", "撞到了头", "磕到了头", "碰了一下头",
    "磕了下头", "头部撞了一下", "头部被撞", "撞了一下脑袋",
    "脑袋被撞", "脑袋被磕", "脑袋被碰",
    # 头部+动词的组合变体（头部碰到/磕了后脑勺/脑袋着地等）
    "头部碰到", "头部磕到", "磕了后脑勺", "撞了后脑勺",
    "磕到后脑勺", "撞到后脑勺", "碰到后脑勺",
    "磕到了后脑勺", "撞到了后脑勺", "碰到了后脑勺",
    "后脑勺被磕", "后脑勺被撞", "后脑勺被碰",
    "后脑勺先着地", "后脑勺直接着地", "后脑勺重重着地",
    "碰到了头", "碰到了脑袋",
    "碰了后脑勺", "脑袋着地", "头顶磕到", "头顶撞到", "头受伤",
    "枕部撞到", "枕部磕到", "额头撞到", "前额撞到", "额头磕到",
)

#: 家族史/他人病史不能当作患者本人的当前状态
_FAMILY_HISTORY_MARKERS = (
    "父亲", "母亲", "父母", "家人", "家族", "爷爷", "奶奶", "外公", "外婆",
    "丈夫", "妻子", "配偶", "哥哥", "姐姐", "弟弟", "妹妹", "儿子", "女儿",
    # 口语称谓：家人服药不能算患者本人抗凝
    "爸爸", "妈妈", "爸", "妈", "老公", "老婆", "爱人", "老伴",
    "岳父", "岳母", "公公", "婆婆", "女婿", "儿媳", "姥姥", "姥爷",
)

#: 已经结束、停用、否认或仅为过敏的病史/用药不属于当前高危状态
_CONTEXT_INACTIVE_MARKERS = (
    "已结束", "结束后", "已停", "停用", "停药", "停了", "已完成",
    "不再", "未使用", "没有使用", "未服用", "没有服用", "未吃", "没有吃",
    "没吃", "不吃", "不吃了", "不再吃", "不用吃", "无需吃", "没再吃",
    "否认", "未曾", "从未", "过敏",
)

#: 「未停用/不能停药」表示仍在用药，不能按“已停用”过滤
_NEGATED_STOP_PATTERN = re.compile(
    r"(?:未|没|没有|不能|不可|不宜|不让|不应|不要|别|无需|不必|"
    r"并非|不想|不打算|不准备)"
    r"[^，。；]{0,4}(?:停用|停药|停服|停了|停止服用)"
)
#: 「打算/准备停用华法林」是意图，当前仍在用药
_STOP_INTENT_PATTERN = re.compile(
    r"(?:想|打算|准备|计划|考虑|要不要|是否)"
    r"[^，。；]{0,6}(?:停用|停药|停服|停了|停止服用)"
)
#: 「华法林停用可以吗」是询问，不能按已停药处理
_STOP_QUESTION_PATTERN = re.compile(
    r"(?:停用|停药|停服|停了)[^，。；]{0,4}"
    r"(?:可以吗|行吗|好吗|行不行|可以么|是否|吗)"
)
#: 换药/续用线索：同一分句里「停用旧药改用新药」仍是当前用药
_SWITCH_MEDICATION_PATTERN = re.compile(
    r"(?:改用|换成|改吃|换用|改为|重新吃|又吃上|恢复服用|"
    r"还在吃|仍在服用|继续服用|仍然服用)"
)
#: 近期停药仍有抗凝作用：「停用两天/昨天停药/刚停用」按当前用药处理
_RECENT_STOP_PATTERN = re.compile(
    r"(?:停用|停药|停了)[^，。；;,.!?！？\n]{0,3}"
    r"(?:今天|昨天|前天|[0-9一二两三四五六七八九十]+天|几天|"
    r"一周|两周)"
    r"|(?:刚刚?|今天|昨天|前天|前几天|前几周|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前)"
    r"[^，。；;,.!?！？\n]{0,4}(?:已|刚|才)?\s*"
    r"(?:停用|停药|停了|停服|停止服用)"
)
#: 明确远期停药（月/年量级）才按已结束处理；裸「停用/停了」保守
#: 视为仍有抗凝作用（停药 1–2 天内 INR 仍可能偏高）
_LONG_STOP_PATTERN = re.compile(
    r"(?:停用|停药|停了|停服|停止服用)"
    r"[^，。；;,.!?！？\n]{0,6}"
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|年)|多月|多年|半年)"
    r"|(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|年)|半年|多年)"
    r"\s*(?:前|以来)?[^，。；;,.!?！？\n]{0,4}"
    r"(?:停用|停药|停了|停服|停止服用)"
)
#: 停药动词：只有真正的停药表述才做「无时长保守保留」
_STOP_VERB_PATTERN = re.compile(r"(?:停用|停药|停了|停服|停止服用|已停)")
#: 停药后又重新服用（可跨一个分句）：抗凝状态仍然成立
_RESTART_TEXT_RE = re.compile(
    r"(?:又吃上|又用上|又服上|重新吃|重新服用|重新用|恢复服用|恢复吃"
    r"|又开始吃|又开始服|再次服用|继续服用|继续吃|还在吃|还在服用"
    r"|仍在吃|仍在服用|仍然服用)"
)
#: 既往用药（不含每天/长期等持续线索时）不是当前用药
_PAST_MEDICATION_PATTERN = re.compile(
    r"(?:以前|之前|曾经|曾|既往|过去)"
    r"[^，。；]{0,8}(?:服用|口服|吃|用|注射|打)"
    # 「十年前吃过/几年前服用过」：完成体语缀才按既往处理，
    # 「两年前开始吃」仍是当前用药
    r"|[0-9一二两三四五六七八九十半几]+\s*(?:天|周|个月|月|年)前"
    r"[^，。；]{0,8}(?:服用|口服|吃|用|注射|打)(?:过|了)"
)
_CONTINUOUS_MEDICATION_PATTERN = re.compile(
    r"(?:每天|每日|天天|长期|一直|持续|规律|常年|多年|这半年|近半年|"
    r"几天|几日|几周|数天|数周|近期|这几天|最近|"
    r"一周|两周|[一二两三四五六七八九十0-9]+(?:天|周|个月)来?)"
)
#: 同一分句出现这些词时仍属当前用药（即使前半句提到以前）
_CURRENT_MEDICATION_CUE = re.compile(
    r"(?:现在|目前|还在|仍在|仍|继续|改为|换成)"
)

#: 「HIV 阴性」「艾滋病抗体阴性」是阴性结果，不代表当前免疫抑制
_NEGATIVE_TEST_SUFFIX = re.compile(
    r"\s*(?:抗体|抗原|检测|筛查|检查|结果)?\s*(?:为|呈|是)?\s*"
    r"(?:阴性|[（(]\s*[-－—]\s*[）)])"
)


_MED_USE_NEGATION_RE = re.compile(
    r"(?:没|没有|未|不|无需|不用)(?:再|在)?(?:吃|服用|服|用|喝|注射|打)"
    r"\s*$"
)
_MED_USE_STOP_SUFFIX_RE = re.compile(
    r"^[^，。；]{0,3}(?:我|本人)?(?:已经|已|刚|才)?"
    r"(?:不吃了|不再吃了|不用吃了)"
)


def _active_term_mention(text: str, term: str) -> bool:
    """术语是否以非否定形式出现（"无化疗史""HIV阴性"不算当前状态）。"""
    start = text.find(term)
    while start != -1:
        prefix = _clause_prefix(text, start)
        suffix = text[start + len(term):start + len(term) + 16]
        negated = (
            _is_negated(prefix)
            or bool(_NEGATIVE_TEST_SUFFIX.match(text, start + len(term)))
            or _MED_USE_NEGATION_RE.search(prefix) is not None
            or _MED_USE_STOP_SUFFIX_RE.search(suffix) is not None
        )
        if not negated:
            return True
        start = text.find(term, start + len(term))
    return False

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


#: 「气温/水温/酒精度 38 度」不是体温
_NON_BODY_TEMPERATURE_WORDS = (
    "气温", "水温", "室温", "油温", "酒精度", "环境温度",
    "室外温度", "天气温度", "洗澡水", "洗澡", "房间", "空调",
    "白酒", "啤酒", "红酒", "喝酒",
)


def _has_fever(text: str) -> bool:
    for term in FEVER_TERMS:
        start = text.find(term)
        while start != -1:
            prefix = _clause_prefix(text, start)
            stripped = prefix.rstrip()
            if term == "烧了" and (
                stripped.endswith("退")
                or stripped.endswith(("火", "燃烧"))
                or stripped.endswith(
                    ("不发", "没发", "没有发", "未发", "再发")
                )
            ):
                # 「已经退烧了」「不发烧了」不是发热
                start = text.find(term, start + len(term))
                continue
            if term == "高热":
                following = text[start + len(term):start + len(term) + 1]
                if following in ("量", "能", "卡", "食", "饮", "效", "烹"):
                    # 「高热量/高热能」不是发热
                    start = text.find(term, start + len(term))
                    continue
            if not _is_negated(prefix):
                return True
            start = text.find(term, start + len(term))

    for match in TEMPERATURE_PATTERN.finditer(text):
        # 显式摄氏温度分支从数字开始；有发热语境的分支由上下文保证
        if match.group(0)[:1].isdigit():
            window = text[max(0, match.start() - 8):match.end() + 8]
            if any(word in window for word in _NON_BODY_TEMPERATURE_WORDS):
                continue
        prefix = _clause_prefix(text, match.start(), lookback=8)
        if not _is_negated(prefix):
            return True
    return False


def _active_medical_text(context: Dict[str, Any]) -> str:
    """拼接当前仍生效的病史与用药。

    按分句过滤家族史、他人病史、否定表述、过敏史以及已结束/已停用的
    治疗，避免把「父亲化疗」「未使用抗凝药」当作患者本人的高危状态。
    """
    raw = "；".join(
        part for part in (
            _flatten(context.get("history")).lower(),
            medication_context_text(context).lower(),
        ) if part
    )
    clauses = re.split(r"[，。；;,.!?！？\n]", raw)
    kept = []
    for index, clause in enumerate(clauses):
        clause = clause.strip()
        if not clause:
            continue
        if any(marker in clause for marker in _FAMILY_HISTORY_MARKERS):
            # 病史提取不做宾语剥离：「陪我妈化疗/照顾我爸吃药」不是
            # 患者本人在用药
            stripped = _strip_family_narration(
                clause, allow_object=False
            ).strip()
            if stripped and not any(
                marker in stripped for marker in _FAMILY_HISTORY_MARKERS
            ):
                clause = stripped
            elif _FAMILY_CARE_RE.search(clause):
                # 「我陪我妈去化疗/我照顾我爸吃药」：治疗属于家属
                continue
            elif _has_patient_subject(clause):
                # 「我和我爸都吃华法林/都在化疗」：患者是并列主语，
                # 不能因为家属称谓整句丢弃
                pass
            else:
                continue
            # 家属宾语剥离后的「买/拿/取/配 + 药名」是替家人购药，
            # 不能当作患者本人在用
            if re.match(
                r"^\s*(?:去?药[店房])?(?:买|购买|拿|取|配|代买|帮买|购)",
                clause,
            ):
                continue
        # 续用线索可能落在下一个分句（「华法林停了半年，最近又吃上了」）
        restart_window = "；".join(clauses[index + 1:index + 3])[:40]
        if (
            _NEGATED_STOP_PATTERN.search(clause)
            or _STOP_INTENT_PATTERN.search(clause)
            or _STOP_QUESTION_PATTERN.search(clause)
            or _SWITCH_MEDICATION_PATTERN.search(clause)
            or _RECENT_STOP_PATTERN.search(clause)
        ):
            # 「未停用华法林」「打算停用华法林」「停用旧药改用新药」
            # 「昨天停药」都表示当前仍有用药/抗凝作用，不能整句丢弃
            kept.append(clause)
            continue
        if any(marker in clause for marker in _CONTEXT_INACTIVE_MARKERS):
            if (
                _RESTART_TEXT_RE.search(clause)
                or _RESTART_TEXT_RE.search(restart_window)
            ):
                # 「停了半年，最近又吃上了」：跨分句续用线索
                kept.append(clause)
                continue
            if (
                _STOP_VERB_PATTERN.search(clause)
                and not _LONG_STOP_PATTERN.search(clause)
            ):
                # 没有明确远期时长的停药保守按仍有抗凝作用处理
                kept.append(clause)
            continue
            continue
        if (
            _PAST_MEDICATION_PATTERN.search(clause)
            and not _CONTINUOUS_MEDICATION_PATTERN.search(clause)
            and not _CURRENT_MEDICATION_CUE.search(clause)
        ):
            # 「以前吃华法林」是既往用药；含近期时长或「现在还在吃」
            # 的分句仍属当前用药
            continue
        kept.append(clause)
    # 用分句标点拼接：术语匹配的前缀不能跨过分句边界，
    # 否则上一个分句结尾的「无」会否定下一段的真实病史
    return "；".join(kept)


#: 家属只是叙述者/提醒者时的框架（「我妈说我头撞到门框」
#: 「我爸让我吃华法林」）：剥离后仍是患者本人的表述
_FAMILY_NARRATION_RE = re.compile(
    r"(?:他|她)?\s*(?:刚才|刚刚|当时|今天|昨天|在旁)?\s*"
    r"(?:说|告诉|讲|提醒|让|叫|喊|看到|看见|发现|扶着?|带着?|"
    r"陪着?|送|抱|背)"
)
#: 家属作宾语的动作（「我扶我爸起来时头撞到」「照顾我妈时摔到头」
#: 「给我爸拿药时头撞到柜子」）：患者是动作主语，家属只是宾语
_FAMILY_OBJECT_RE = re.compile(
    r"(?:照顾|搀扶|扶着?|带着?|陪着?|送|背|抱|帮|给|替|喂|接|"
    r"看望|看看|叫醒|抱起|搀着?)\s*(?:了|着)?\s*(?:我|本人|自己)?\s*$"
)
#: 叙述框架后的患者主体（家属转述患者/宝宝的症状）
_PATIENT_SUBJECT_SAFE = (
    "本人", "宝宝", "孩子", "婴儿", "幼儿", "患儿", "患者",
    "小孩", "儿童",
)


def _strip_family_narration(
    clause: str, allow_object: bool = True
) -> str:
    """家属称谓只是叙述者时剥离框架，保留患者本人的表述。

    「我妈说我今天头撞到门框」→「今天头撞到门框」；
    「我爸让我吃华法林」→「吃华法林」。家属本人作主语
    （「我爸吃华法林」「我爸撞到头」）时原样返回，由调用方过滤。
    """
    if not any(marker in clause for marker in _FAMILY_HISTORY_MARKERS):
        return clause
    for marker in _FAMILY_HISTORY_MARKERS:
        start = clause.find(marker)
        while start != -1:
            tail = clause[start + len(marker):]
            verb = _FAMILY_NARRATION_RE.match(tail)
            if verb:
                rest = tail[verb.end():]
                pos = rest.find("我")
                if pos != -1:
                    remainder = rest[pos + 1:].lstrip("，,、 的")
                    if remainder:
                        return remainder
                # 家属转述的宾语是宝宝/孩子/患者本人
                for subject in _PATIENT_SUBJECT_SAFE:
                    pos = rest.find(subject)
                    if pos != -1:
                        remainder = rest[pos:].lstrip("，,、 的")
                        if remainder:
                            return remainder
            prefix = clause[:start]
            if allow_object and _FAMILY_OBJECT_RE.search(prefix):
                remainder = clause[start + len(marker):]
                # 宾语结构后若出现「他/她」另一主体，不能算到患者头上
                if remainder and "他" not in remainder and "她" not in remainder:
                    return remainder.lstrip("，,、 的")
            start = clause.find(marker, start + len(marker))
    return clause


def _patient_only_text(text: str) -> str:
    """拼接只属于患者本人的分句（家属作主语的分句丢弃）。"""
    kept = []
    # 小数点不是分句边界：「38.5度」不能被拆成「38」「5度」，
    # 否则体温规则看不到完整读数
    for clause in re.split(
        r"[，。；;！!？?\n]|,(?![0-9])|[.](?![0-9])|(?<![0-9])[.]",
        str(text or ""),
    ):
        clause = clause.strip()
        if not clause:
            continue
        if any(marker in clause for marker in _FAMILY_HISTORY_MARKERS):
            stripped = _strip_family_narration(
                clause, allow_object=False
            ).strip()
            if stripped and not any(
                marker in stripped for marker in _FAMILY_HISTORY_MARKERS
            ):
                clause = stripped
            elif _FAMILY_CARE_RE.search(clause):
                # 「我陪我妈去化疗」：不是患者本人的治疗
                continue
            elif _has_patient_subject(clause):
                # 「我妈说宝宝发烧」「我和我爸都发烧」：句中有明确的
                # 患者主体，不能因为家属称谓整句丢弃
                pass
            else:
                continue
        kept.append(clause)
    return "；".join(kept)


_FAMILY_POSSESSIVE_RE = re.compile(
    r"(?:爸|妈|父亲|母亲|老公|老婆|爱人|老伴|爷爷|奶奶|外公|外婆|"
    r"姥姥|姥爷|哥哥|姐姐|弟弟|妹妹|儿子|女儿|家人|亲属)"
    r"[^，。；]{0,4}(?:是|作为|当|这个|那个|一位|一名)"
    r"[^，。；]{0,6}\s*$"
)
#: 「陪/照顾/送/喂家属」结构：治疗属于家属，不能算患者本人
_FAMILY_CARE_RE = re.compile(
    r"(?:陪|送|照顾|搀扶|扶着?|带着?|喂|帮|替|给)(?:着|了)?"
    r"(?:我|他|她)?"
    r"(?:爸|妈|父亲|母亲|老公|老婆|爱人|老伴|爷爷|奶奶|外公|外婆|"
    r"姥姥|姥爷|哥哥|姐姐|弟弟|妹妹|儿子|女儿|家人|亲属)"
)


def _has_patient_subject(clause: str) -> bool:
    """分句里是否有不依附家属称谓的患者主体。"""
    for subject in _PATIENT_SUBJECT_SAFE:
        start = clause.find(subject)
        while start != -1:
            before = clause[:start]
            if _FAMILY_POSSESSIVE_RE.search(before):
                # 「我爸是化疗患者」：患者一词修饰的是家属
                start = clause.find(subject, start + len(subject))
                continue
            return True
    # 裸「我」不能是家属称谓的一部分（我爸/我妈/我儿子…）
    return re.search(
        r"我(?!爸|妈|父母|父|母|爷|奶|外公|外婆|姥姥|姥爷|哥|姐|弟|妹|"
        r"儿|女|爱人|对象|丈夫|妻子|配偶|老公|老婆|家人|亲)",
        clause,
    ) is not None


def _patient_term_mention(text: str, term: str) -> bool:
    """只匹配患者本人的表述：同一分句出现家属称谓时跳过。

    避免「我吃华法林，我爸撞到头」把家人的外伤拼到患者身上。
    """
    for clause in re.split(r"[，。；;,.!?！？\n]", text):
        if not clause:
            continue
        if any(marker in clause for marker in _FAMILY_HISTORY_MARKERS):
            stripped = _strip_family_narration(clause)
            if stripped and not any(
                marker in stripped for marker in _FAMILY_HISTORY_MARKERS
            ):
                clause = stripped
            elif _has_patient_subject(clause):
                # 「我和我爸都撞到头」：患者是并列主语
                pass
            else:
                continue
        if _positive_mention(clause, term):
            return True
    return False


def _context_flags(text: str, context: Any) -> List[Tuple[str, str]]:
    """结合年龄、免疫状态与抗凝用药补充高危提示。"""
    if not isinstance(context, dict):
        return []

    flags: List[Tuple[str, str]] = []
    patient_text = _patient_only_text(text)
    age_months = _parse_age_months(context.get("age"))
    if age_months is not None and age_months < 3 and _has_fever(patient_text):
        flags.append((
            "3 个月以下婴儿发热",
            "3 个月以下婴儿出现发热需要尽快由儿科或急诊医生评估，"
            "请不要自行用药，立即前往医院。",
        ))

    medical_text = _active_medical_text(context)
    # 用户可能只在本次主诉里自述化疗/抗凝（档案/用药清单未录入）：
    # 免疫抑制与抗凝判断都并入主诉文本，过滤规则与结构化文本一致
    complaint_medical = _active_medical_text({"history": text})
    active_text = medical_text
    if complaint_medical:
        active_text = f"{medical_text}；{complaint_medical}"
    if (
        any(
            _active_term_mention(active_text, term)
            for term in IMMUNOSUPPRESSION_TERMS
        )
        and _has_fever(patient_text)
    ):
        flags.append((
            "免疫功能低下伴发热",
            "免疫功能低下的患者出现发热可能是严重感染的表现，"
            "请尽快联系主诊医生或前往急诊，不要自行服用退烧药掩盖病情。",
        ))

    anticoag_text = active_text
    if (
        any(
            _active_term_mention(anticoag_text, term)
            for term in ANTICOAGULANT_TERMS
        )
        and any(
            _patient_term_mention(text, term)
            for term in HEAD_TRAUMA_TERMS
        )
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
            suffix = _clause_suffix(text, start + len(phrase))
            if not _is_negated(prefix, suffix):
                if label not in matches:
                    matches.append(label)
                break
            start = text.find(phrase, start + len(phrase))

    # 孕期状态可能只写在档案病史里（本次主诉只描述出血）：
    # 复用 _active_medical_text 的分句过滤把患者本人的现症/妊娠
    # 表述并入判定，家族史/否认表述会被过滤掉
    history_text = ""
    if isinstance(context, dict):
        try:
            history_text = _current_history_text(
                _active_medical_text(context)
            )
        except Exception:
            history_text = ""
    history_text = re.sub(
        r"妊娠期(?:糖尿病|高血压|肝内胆汁淤积症?|贫血|甲减|甲亢"
        r"|血小板减少)",
        "",
        history_text,
    )
    pregnancy_text = text
    if history_text and history_text not in text:
        pregnancy_text = f"{text}；{history_text}"
    pregnancy_weeks = False
    for match in PREGNANCY_WEEK_PATTERN.finditer(pregnancy_text):
        if not _is_negated(
            _clause_prefix(pregnancy_text, match.start())
        ):
            pregnancy_weeks = True
            break
    pregnancy_mentioned = (
        PREGNANCY_UNCERTAIN_PATTERN.search(pregnancy_text) is not None
        or pregnancy_weeks
        or any(
            _positive_mention(pregnancy_text, term)
            for term in PREGNANCY_UNCERTAIN_TERMS
        )
        or any(
            _positive_mention(pregnancy_text, term)
            for term in PREGNANCY_TERMS
        )
    )
    if (
        pregnancy_mentioned
        and (
            _has_pregnancy_bleeding(text)
            or _generic_bleeding_positive(text)
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
