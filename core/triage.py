"""急症红旗（red flag）预检。

在调用 LLM 之前做一次确定性的关键词筛查：命中胸痛、呼吸困难、意识改变、
卒中样表现等急症信号时，立即提示用户拨打 120 或前往急诊，不必等待模型回复。

这不是诊断，也不替代医生判断；它只是给高风险描述提供更快的安全出口。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from core.context import medication_context_text
from core.medication_safety import (
    DRUG_GROUPS as _DRUG_GROUPS,
    _AMBIGUOUS_SHORT_ALIASES,
)
from core.medication_text import (
    BRAND_DRUG_NAME_RE as _BRAND_DRUG_NAME_RE,
    GENERIC_DRUG_NAME_RE as _GENERIC_DRUG_NAME_RE,
    MED_CATEGORY_RE as _MED_CATEGORY_RE,
    clause_mentions_drug_clause,
)

#: (匹配短语, 展示标签)。同一个标签只保留一次。
RED_FLAG_PHRASES = (
    ("胸痛", "胸痛"),
    ("胸疼", "胸痛"),
    ("胸口痛", "胸痛"),
    ("胸口疼", "胸痛"),
    ("心口痛", "胸痛"),
    ("心口疼", "胸痛"),
    ("心前区痛", "胸痛"),
    ("心前区疼痛", "胸痛"),
    # 常见口语/书面同义写法：直接子串无法由「胸痛」覆盖
    ("胸部疼痛", "胸痛"),
    ("胸部疼", "胸痛"),
    ("胸部痛", "胸痛"),
    ("胸骨后疼痛", "胸痛"),
    ("胸前区疼痛", "胸痛"),
    ("胸前区痛", "胸痛"),
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
    "再|再次|再发|出现|伴有|伴随|伴|感|有|见|见到|明显|任何|明确|发生|"
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
#: A 不 A 疑问（撞没撞到头/碰没碰到）：同属不确定，按阳性处理，
#: 不能被前缀末尾的裸「没」判成否认（第一百零二轮 [2]）
_A_NOT_A_PREFIX_RE = re.compile(
    r"(?:撞|磕|碰|砸|摔|打|吃|服|用|喝|吸|停)(?:没|不)$"
)
#: 「不是没有/并非没有」是双重否定，实际为阳性
_DOUBLE_NEGATION_SUFFIX_PATTERN = re.compile(
    r"(?:不是|并非|并非说|并不是说?|不能说|不代表|又不是)"
    r"(?:说|真的|完全|彻底|根本|任何|明显|绝对|会|可能|是)?\s*"
    r"(?:没有|没|无)"
    rf"(?:{_NEGATION_FILLERS})*\s*$"
)
#: 「差点没昏倒」是“几乎发生”，不是否定。
#: 近失词与句尾之间允许口语副词串（差点就/差点就要/差一点点就/
#: 几乎快要），否则「差点就要撞到头」会被当作真实撞击（第九十九
#: 轮 [17]、第一百轮 [11]）
_ALMOST_PATTERN = re.compile(
    r"(?:差点|险些|几乎|差一点|差些)\s*(?:就|都|要|快|点){0,3}\s*"
    r"(?:没有|没)?\s*$"
)
#: 「从未有过的胸痛」「前所未有的呼吸困难」是“从未如此”的最高级
#: 描述，实际为阳性，不能被否定填充词误判
_SUPERLATIVE_NOVELTY_RE = re.compile(
    r"(?:从未有过|从来没有过|从没有过|未曾有过|前所未有|"
    r"从未出现过|从未像这样|第一次出现|"
    # 「从来没出现过的胸痛」「好多年没出现的胸痛又来了」同为
    # 新发/复发阳性描述，不能被否定填充词吞掉（第八十轮）
    r"从(?:来)?没(?:有)?(?:再)?出现(?:过)?|"
    r"没(?:有)?(?:再)?出现(?:过)?|"
    r"(?:好)?多年(?:没(?:有)?|未见)(?:再)?(?:出现(?:过)?)?|"
    r"(?:好)?久没(?:有)?(?:再)?出现(?:过)?|"
    # 「以前没有过的胸痛」「之前没有过的呼吸困难」同为从未有过
    r"(?:以前|之前|过去|以往|此前|从(?:来)?)?没(?:有)?(?:再)?过)"
    r"[^，。；]{0,2}的\s*$"
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
#: 短语之后的分句末否定：「胸痛没有了」「发烧、咳嗽、胸痛都没有」。
#: 否定词在短语之后同样覆盖前面的枚举症状（第九十九轮 [7]）；
#: 「不是没有了/并非没有」是双重否定，按阳性处理。否定词尾与前置
#: 否定填充词对齐：没有出现/未出现/没有再发作同样是否认
#: （第一百零三轮 [6]）
_TRAILING_NEGATION_RE = re.compile(
    r"^[、，]?(?P<pre>[^，。；！？,.!?;\n]{0,40}?)?"
    r"(?P<group>都|也|全都|全|均)?"
    r"(?:已经|早已)?"
    r"(?:没有|没了|未|不(?:再)?有|没|无)"
    r"(?:再|再次)?"
    r"(?:出现|发生|发作|复发)?"
    r"(?:了)?\s*$"
)


def _trailing_negation_covers(tail: str) -> bool:
    """tail 是否以「（都）没有/没了」收尾（短语后的后置否定）。

    短语与否定词之间还有其它内容时，必须带群否定标记（都/也/
    全/均）才算整组否定，且中间不能有阳性修饰：「胸痛很厉害、
    呼吸困难没有」是胸痛阳性+否认呼吸困难（第一百轮 [5]）。
    短语后紧跟顿号时分隔的是其它枚举项，其中的修饰词不否决本
    短语的否定（「胸痛、出汗都没有」）（第一百零一轮 [5]）。
    """
    raw = str(tail or "")
    enum_tail = raw.lstrip().startswith(("、", "，"))
    trailing = _TRAILING_NEGATION_RE.match(raw)
    if trailing is None:
        return False
    pre = (trailing.group("pre") or "").rstrip()
    if re.search(r"(?:不是|并非|并不|不)$", pre):
        return False
    if pre:
        # 群否定标记（都/也/全/均）是整组否定的必要条件：
        # 「说话不清、手脚麻木没有」只否认手脚麻木
        if not trailing.group("group"):
            return False
        # 短语后紧跟顿号时分隔的是其它枚举项，其中的修饰词不否决
        # 本短语的否定（「胸痛、出汗都没有」）（第一百零一轮 [5]）
        if not enum_tail and any(
            cue in pre for cue in _TRAILING_NEGATION_POSITIVE_CUES
        ):
            return False
    return True


def _trailing_negation_in_clause(
    text: str, end: int, prefix: str = ""
) -> bool:
    """短语之后、同一分句（顿号不截断）是否以整组后置否定收尾。

    「发烧、咳嗽、胸痛都没有」的最后一项才带否定后缀，前面的枚举
    症状同样按整组否定处理（第九十九轮 [7]）。阳性修饰只看短语
    自己的修饰窗口（最后一个顿号之后）：短语自身带阳性修饰
    （持续胸痛、呼吸困难都没有）时不按否定处理（第一百轮 [5]）；
    其它枚举项里的出汗/厉害不否决本短语（第一百零一轮 [5]）。
    """
    if prefix:
        own_prefix = re.split(r"[、，,]", prefix)[-1]
        if any(
            cue in own_prefix for cue in _TRAILING_NEGATION_POSITIVE_CUES
        ):
            return False
    boundary = end
    limit = len(text)
    while boundary < limit and text[boundary] not in "，。；！？,.!?;\n":
        boundary += 1
    return _trailing_negation_covers(text[end:boundary])


def _is_negated_with_trailing(prefix: str, suffix: str) -> bool:
    """_is_negated 加后置「（已经）没有了/没了」否定（发热词专用）。

    孕产期出血路径不能用后置否定（出血已停仍需排查宫外孕），但
    「发烧没有了」是明确的当前无发热（第一百轮 [1] 的取舍只应
    作用于出血路径，第一百零一轮 [6]）。
    """
    if _trailing_negation_covers(suffix):
        return True
    return _is_negated(prefix, suffix)

#: 胸痛族的插入修饰变体：胸口有点疼/胸前区隐隐作痛/心口一阵一阵疼。
#: 精确子串表无法覆盖部位词与疼/痛之间的副词，单独用受控正则兜底
#: （第一百零一轮 [1]）
_CHEST_PAIN_VARIANT_RE = re.compile(
    r"(?:胸腔|胸口|胸前区|心前区|心口|胸部|胸骨后|胸骨|胸)"
    r"(?:有点|有些|有一点|一点点|一丝|轻微|隐隐|隐|中间|中部|"
    # 中心性/正中部位写法与 X性/X样 后缀（胸部正中疼痛/胸骨中段疼
    # 痛/心前区压榨性疼痛/心口处疼/胸口压迫感）（第一百零三轮
    # [1][2]）
    r"正中|正中间|中央|正中央|中段|中下段|中线|处|位置|上方|"
    # 方向限定：胸部正中偏左/胸骨中段偏右疼痛同样是中心性胸痛
    # （第一百零四轮 [5]）
    r"偏左|偏右|偏上|偏下|左侧|右侧|左边|右边|"
    r"压迫|压着|烧灼|绞窄|紧缩|"
    r"一阵一阵|一阵阵|一阵|持续性|阵发性|针刺样|针刺|针扎|"
    r"刀割样|刀割|撕裂样|撕裂|绞着|扯着|放射|特别|明显|一直|"
    r"持续|总是?|老是|经常|突然|剧烈|剧|很|好|较|稍微|略|发|"
    r"胀|闷|钝|刺|绞|压榨|揪着|作|地|得|一样|似的|性|样|的)*"
    # 压迫感/压榨感/紧缩感等无「疼/痛」的缺血等价主诉；
    # 「的/一样」修饰（胸口一阵阵的疼/撕裂样的疼痛）同样命中
    # （第一百零七轮 [2]）
    r"(?:疼|痛|压迫感|压榨感|紧缩感)"
)

#: 胸部压榨感/闷痛的修饰变体（胸口像压了块石头）不在此处理：
#: 只兜底「部位+修饰+疼/痛」，避免与其它规则混淆
#: 出现这些词时，同一句里的「出血」按孕产期出血处理
PREGNANCY_TERMS = (
    "怀孕", "孕期", "妊娠", "产妇", "产后", "孕妇", "孕产妇",
    "早孕", "中孕", "晚孕", "孕早期", "孕中期", "孕晚期",
    "产前", "待产", "临产", "预产期", "足月",
    # 明确的妊娠相关表述：宫外孕/孕吐/产检等出现出血时同样是
    # 产科急症高风险场景
    "宫外孕", "异位妊娠", "孕吐", "孕检", "产检", "葡萄胎",
    "难免流产", "先兆流产", "胎停", "胎停育", "稽留流产",
    # IVF/人工授精语境：移植后出血同样是妊娠未确认状态，宫外孕
    # 风险按孕产期出血评估（第一百零三轮 [4]）；「试管婴儿移植」
    # 是同一场景的常见写法人称（第一百零四轮 [6]）
    "试管移植", "胚胎移植", "囊胚移植", "冻胚移植", "鲜胚移植",
    "胚胎植入", "囊胚植入", "人工授精", "试管婴儿移植",
)
#: 「剖宫产术后N天/剖宫产术后（无天数）/刚做完剖宫产」都是近期产科
#: 状态；「剖宫产5年了/剖宫产术后5年」是既往史，不算当前妊娠
_CAESAREAN_RECENT_RE = re.compile(
    r"(?:剖宫产|剖腹产)(?:术后)?\s*"
    r"(?:(?:[0-9一二两三四五六七八九十半几]+|几)\s*(?:天|周)|"
    r"(?:[1-9]|1[01]|[一二两三四五六七八九十])\s*个?月)"
    r"|(?:剖宫产|剖腹产)\s*(?:手术)?\s*"
    r"(?:之后|以后|术后|后|刚做完|做完|刚手术)"
    r"(?!\s*(?:(?:已|已经|都|快|近|将近|已有?|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年))"
    r"|(?:刚|刚刚|最近|前两天|前几天)\s*(?:做完|做了)?\s*(?:剖宫产|剖腹产)"
    # 时间前置语序：「今天/昨天/这周/半个月前/一个多月前/两周半前 +
    # 剖宫产」同样是近期产科状态（第一百零四轮 [2]、第一百零五轮
    # [4]）；年单位与 ≥12 个月属既往史，由 _PAST_OBSTETRIC_YEARS 剥离
    r"|(?:今天|昨天|昨晚|前天|大前天|这周|本周|上周|上星期|上上星期|"
    r"上(?:个)?月|这(?:个)?月|本(?:个)?月|"
    r"(?:[0-9一二两三四五六七八九十百半几]+|几)\s*(?:多|来|半)?\s*"
    r"(?:天|日|周|星期)(?:半)?\s*前|"
    r"(?<![0-9一二两三四五六七八九十])"
    r"(?:十[一]?|[一二两三四五六七八九半]|1[01]|[1-9])\s*个?\s*"
    r"(?:多|来|半)?\s*月(?:\s*多)?\s*前)\s*"
    r"(?:做(?:了)?|做的)?\s*(?:剖宫产|剖腹产)"
)
#: 顺产/自然分娩/月子/生完孩子等近期产后表述：产后出血风险仍高；
#: 「顺产5年」等明确年数属于既往史，不算当前孕产状态。
#: 年数判断用函数逐个匹配触发词，避免可选后缀回溯绕过负向前瞻。
_POSTPARTUM_TRIGGER_RE = re.compile(
    r"(?:顺产|自然分娩|生完(?:孩子|宝宝|娃)?|刚生完|生了(?:孩子|宝宝)?|"
    r"(?:刚|刚刚)(?:生产|分娩)(?:完|了)?|"
    r"(?:生产|分娩)(?:的)?(?:时候|时(?!间)|完|了|后)|"
    r"(?:剖宫产|剖腹产)(?:手术)?(?:时|的时候)|"
    r"生(?:孩子|宝宝|娃)(?:的)?(?:时候|时(?!间))|"
    r"(?<![花陌医卫学究发出产人女男再养])生(?:的)?(?:时候|时(?!间))|"
    r"生下(?:孩子|宝宝|娃)?|"
    # 月子期间/出月子/生孩子后 等常见近期产后表述（第九十五轮 [2]）
    r"坐月子|月子里|月子(?:期间|期|中)|"
    r"出(?:了)?月子|刚出(?:了)?月子|"
    r"生(?:孩子|宝宝|娃)(?:以)?后|"
    r"产褥期)"
)
_POSTPARTUM_YEARS_AFTER_RE = re.compile(
    r"^\s*(?:(?:已|已经|都|快|近|将近|已有?|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年|"
    r"(?:1[2-9]|[2-9][0-9])\s*个?月|十二\s*个?月|"
    r"十[二三四五六七八九]\s*个?月|"
    r"[二三四五六七八九]十[一二三四五六七八九]?\s*个?月)"
)


#: 触发词之前明确年数/去年（「3年前分娩时/去年生孩子的时候」）：
#: 属于既往产科史，不是当前孕产状态
_PAST_YEARS_BEFORE_TRIGGER_RE = re.compile(
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年前|去年|前年|"
    r"多年前|以前|之前)[^，。；]{0,8}$"
)


def _postpartum_recent_positive(text: str) -> bool:
    """是否为近期产后表述（明确年数视为既往史）。"""
    for match in _POSTPARTUM_TRIGGER_RE.finditer(text):
        prefix = text[:match.start()]
        if _PAST_YEARS_BEFORE_TRIGGER_RE.search(prefix):
            continue
        tail = text[match.end():match.end() + 12]
        if _POSTPARTUM_YEARS_AFTER_RE.search(tail):
            continue
        return True
    return False
#: 近期停经/月经推迟：妊娠可疑证据（「停经45天，阴道出血」是异位
#: 妊娠/先兆流产的典型组合）；绝经/停经多年不是妊娠（第八十四轮）
_AMENORRHEA_BASE_RE = re.compile(
    r"停经|闭经|月经推迟|月经推后|月经没来|月经未至|没来月经|"
    r"大姨妈推迟|大姨妈没来|例假推迟|例假没来|"
    r"经期推迟|经期推后|经期没来|经期未至|"
    r"生理期推迟|生理期推后|生理期没来|生理期未至|"
    # 数量词插入的语序：月经两个月没来/例假迟了十天/月经晚了十天；
    # 经期/生理期是同一停经语义的标准说法（第一百零六轮 [2]、
    # 第一百零七轮 [7]）
    r"(?:月经|例假|大姨妈|经期|生理期)[^，。；;,.!?！？\n]{0,6}?"
    r"(?:没(?:有)?来|未至|推迟|推后|晚了|迟了)"
)
_AMENORRHEA_YEARS_RE = re.compile(
    r"^\s*(?:已|已经|都|快|近|将近|有|已有|长达)?\s*"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?|好?多)\s*年"
)
_AMENORRHEA_NEGATED_RE = re.compile(
    r"(?:未|没有?|无|不是|否认|排除)\s*$|"
    r"史\s*[:：]?\s*(?:无|阴性|-)|"
    r"没有?怀孕|未怀孕|排除怀孕|不是怀孕"
)
_AMENORRHEA_PAST_RE = re.compile(
    r"(?:去年|前年|以前|之前|曾经|过去|多年前)"
)


def _recent_amenorrhea_positive(text: str) -> bool:
    """是否为近期的停经/月经推迟（否定/既往/绝经/未孕除外）。"""
    for match in _AMENORRHEA_BASE_RE.finditer(text):
        clause_start = max(
            text.rfind(ch, 0, match.start())
            for ch in "，。；;,.!?！？\n"
        ) + 1
        ends = [
            pos for pos in (
                text.find(ch, match.end())
                for ch in "，。；;,.!?！？\n"
            ) if pos != -1
        ]
        clause_end = min(ends) if ends else len(text)
        before = text[clause_start:match.start()]
        after = text[match.end():clause_end]
        if _AMENORRHEA_NEGATED_RE.search(before):
            continue
        if _AMENORRHEA_NEGATED_RE.search(
            text[clause_start:min(len(text), clause_end + 12)]
        ):
            continue
        if _AMENORRHEA_PAST_RE.search(before[-8:]):
            continue
        tail = text[match.end():match.end() + 10]
        if _AMENORRHEA_YEARS_RE.match(tail):
            continue
        prefix = text[max(0, match.start() - 4):match.start()]
        if re.search(r"绝经|更年期", prefix):
            continue
        return True
    return False


#: 其它明确的妊娠证据：验孕阳性、自然流产/流产了；血/尿 HCG 阳性、
#: 「两道杠/一深一浅」等口语写法；「自然流产史」是既往史，不算当前妊娠
_PREGNANCY_EVIDENCE_RE = re.compile(
    r"(?:验孕(?:棒|试纸)?(?:呈|显示)?"
    r"(?:阳性|弱阳性|浅阳性|两条杠|两道杠|双杠|一深一浅)|"
    r"(?:血|血清|尿|β-?|b-?)?\s*hcg\s*(?:呈|显示|为)?\s*(?:阳性|升高)|"
    r"(?:自然|不全|完全)流产(?!史)|流产了|"
    r"(?:药流|人流|引产)(?:后|完|了)|刚流产|"
    r"(?<!史)流产(?!史)|"
    r"怀(?:上)?(?:二胎|二宝|第[二三四]胎))",
    re.IGNORECASE,
)
#: 「孕12周」「孕 28 周」「孕4个月」等写法（排除避孕/备孕等）；
#: 「孕周12 / 孕周：12+3」反向写法同样算，且单位可省略
#: （第一百一十一轮 [6]）
PREGNANCY_WEEK_PATTERN = re.compile(
    r"(?<![避备不待])(?:"
    # 反向写法：孕周[：]12+3
    r"(?:怀)?孕周\s*[:：]?\s*[0-9一二两三四五六七八九十]+\s*"
    r"(?:[+＋]\s*[0-9]+\s*(?:周|[wW])?"
    r"|(?:[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"(?:周|[wW]|个[来多]?月|个半月|月|天)?)"
    r"|(?:怀)?孕\s*[0-9一二两三四五六七八九十]+\s*"
    r"(?:"
    # 产检简写「孕12+3」（=孕12周+3天）：+N 后天数可省略单位
    # （第一百零九轮 [2]）
    r"[+＋]\s*[0-9]+\s*(?:周|[wW])?"
    # 「孕12+周」（12 周多）、「孕12-14周」（筛查孕周范围）、
    # 「孕20w」「孕4个月」
    r"|[+＋]?\s*(?:[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"\s*(?:周|[wW]|个[来多]?月|个半月|月|天)"
    r")"
    r")"
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
    "下体出血", "下面出血", "下面流血", "下身出血", "下身流血",
    "见红", "早孕出血", "孕早期出血", "孕中期出血",
    "孕晚期出血", "产前出血", "孕妇出血",
    # 与 RED_FLAG_PHRASES 对齐：这些短语以「是什么病」出现时同样
    # 不能被定义问句过滤
    "孕期出血", "怀孕出血", "产后出血",
)
#: 量词插入「阴道」与「出血」之间（阴道少量出血/阴道有点流血）仍属
#: 孕产期出血；牙龈/鼻/皮肤出血没有这些部位词，不会命中。妊娠词分支
#: 允许跨逗号并覆盖「孕8周」「产后/产妇」等常见简写
_PREGNANCY_BLEEDING_RE = re.compile(
    # 内裤/护垫/卫生巾上的血迹是明确的出血部位（第一百一十五轮 [17]）
    r"(?:阴道|产道|下体|下面|下身|内裤上?|护垫上?|卫生巾上?)"
    r"[^，。；,;！!？?\n]{0,4}(?:出血|流血|见红|见(?:到)?血)"
    r"|(?:孕妇|产妇|孕产妇|怀孕|孕期|产前|产后|早孕|孕[早中晚]期"
    r"|预产期|足月"
    r"|宫外孕|异位妊娠|孕吐|孕检|产检|葡萄胎|先兆流产|难免流产"
    r"|胎停育?|稽留流产"
    r"|孕周\s*[:：]?\s*[0-9一二两三四五六七八九十]+"
    # 反向写法「孕周12/孕周：12+3」单位可省略（第一百一十一轮 [6]）
    r"\s*(?:[+＋]\s*[0-9]+\s*(?:周|[wW])?"
    r"|(?:[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"(?:周|[wW]|个[来多]?月|个半月|月|天)?)"
    r"|孕\s*[0-9一二两三四五六七八九十]+"
    # 简写「孕12+3，出血」与「孕12+周」「孕12-14周」同口径
    # （第一百零九轮 [2]）
    r"\s*(?:[+＋]\s*[0-9]+\s*(?:周|[wW])?"
    r"|[+＋]?\s*(?:[-~～—至到]\s*[0-9一二两三四五六七八九十]+\s*)?"
    r"\s*(?:周|[wW]|个[来多]?月|个半月|月|天)))"
    r"[^。；;！!？?\n]{0,8}(?:出血|流血|见红|见(?:到)?血(?:了|啦))"
    r"|(?:出血|流血)[^，。；,;！!？?\n]{0,2}(?:阴道|产道|下面|下身)"
    # 裸「见血」必须带「了/啦」：一针见血/见血封喉/见血就晕不是
    # 出血描述（第一百一十五轮 [17]）
    r"|见红|见(?:到)?血(?:了|啦)"
)

#: 口语出血表述：「阴道流了一点血」「有少量血」「内裤上有点血」
#: 「擦到血」「血丝」；排除血常规/血压/血糖等非出血词
_COLLOQUIAL_BLEEDING_RE = re.compile(
    r"(?:阴道|产道|下体|下面|下身|内裤上?|护垫|卫生巾上?|纸巾上?|擦拭?)"
    r"[^，。；,;！!？?\n]{0,6}"
    r"(?<![贫缺])"
    r"血(?!常规|压|糖|脂|型|沉|氧|液|管|检|培养|友|蛋白|小板|栓|肿|红蛋白|红素|细胞|尿)"
    r"|(?:有一点|有一些|有点|有些|少量|少许|带有?|擦到|看到|见到|"
    r"流了?|出了?|有)"
    r"[^，。；,;！!？?\n]{0,3}"
    r"(?<![贫缺])"
    r"血(?!常规|压|糖|脂|型|沉|氧|液|管|检|培养|友|蛋白|小板|栓|肿|红蛋白|红素|细胞|尿)"
    r"|血丝"
)


def _has_pregnancy_bleeding(text: str) -> bool:
    for match in _PREGNANCY_BLEEDING_RE.finditer(text):
        span = match.group(0)
        prefix = _clause_prefix(text, match.start())
        if _bleeding_jianxue_idiom(text, match.start(), span):
            # 见血封喉/见血就晕/一针见血不是孕产期出血
            # （第一百一十五轮 [17]）
            continue
        # 匹配内部含紧邻出血词的否定（阴道没有出血/阴道未见出血/
        # 阴道不出血）或明确是牙龈/鼻/皮肤出血时不算孕产期出血。
        # 只认出血词之前的否定：「不规则/无痛性/非常」等修饰是阳性
        bleed_body = re.sub(
            r"(?:出血|流血|见红|见(?:到)?血)\s*$", "", span
        )
        internal_body = re.sub(
            r"(?:阴道|产道|下体|下面|下身)\s*$", "", bleed_body
        )
        uncertainty_window = (
            prefix[-10:]
            + span
            + text[match.end(): match.end() + 6]
        )
        if (
            _uncertain_bleeding_window(uncertainty_window)
            and "不再" not in internal_body
        ):
            # 「不确定/不知道有没有出血」「不确定下面有没有出血」
            # 「下面有没有出血不确定」都是待排查的出血主诉
            # （第一百一十七轮 [3]、第一百一十八轮 [1]）
            return True
        if (
            re.search(
                # 「不是/并不是阴道出血」：否认以「是」收尾，单独的
                # 「不」匹配不完整，必须显式包含（第九十四轮 [8]）
                r"(?:没有|没|无|未|不是|不|否认|非)"
                r"(?:(?:再|再次|明显|任何|见|见到|出现|发现)\s*)*$",
                internal_body,
            )
            is not None
            # 「否认腹痛及阴道出血/无腹痛、阴道出血」：枚举否定覆盖
            # 到出血部位（第一百一十六轮 [6]）
            or _enum_negated(internal_body)
        ):
            # 出血词后紧贴的「了」说明曾经出血、现已停止（妊娠
            # 语境仍需排查宫外孕/先兆流产，第一百轮 [1] 口径、
            # 第一百一十二轮 [2]）；后续分句的「了」（做了B超/
            # 太累了）不算证据；「不再流血了」按 round55 契约仍为
            # 否认（第一百一十三轮 [4][5]、第一百一十四轮 [5][12]）
            tail_jump = text[match.end(): match.end() + 2]
            if (
                re.match(r"[，,、]?\s{0,2}(?:了|啦)", tail_jump)
                and "不再" not in internal_body
            ):
                return True
            continue
        # 部位明确的匹配（阴道出血/产道出血）只看匹配片段本身：
        # 前置枚举里的「牙龈出血、」不能吞掉后面的阴道出血
        location_match = span.startswith(
            ("阴道", "产道", "下体", "下面", "下身")
        ) or span.endswith(("阴道", "产道", "下面", "下身"))
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
        if _non_pregnancy_bleeding_site(span, prefix):
            continue
        if not (
            _is_negated(prefix, _clause_suffix(text, match.end()))
            or _enum_negated(prefix)
        ):
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
            if not gum_hit and not _is_negated(
                prefix, _clause_suffix(text, start + len(term))
            ):
                return True
            start = text.find(term, start + len(term))
    if _colloquial_bleeding_positive(text):
        return True
    return False


#: 非妊娠部位的出血：便/尿/痰/咳/呕等即使带孕周也不能按孕产期
#: 出血处理（大便出血、尿里有血、痰中带血）
_NON_PREG_BLEEDING_WORDS = (
    "大便", "便中", "便血", "解便", "肛门", "直肠", "痔疮",
    "小便", "尿液", "尿里", "尿血", "血尿", "排尿",
    "痰中", "痰里", "咳血", "咯血", "呕血", "吐血",
    "尿中", "咳出", "咯出", "呕出", "呕吐物",
    "出血热", "出血性疾病", "伤口", "术后", "鼻腔", "牙龈",
)


def _non_pregnancy_bleeding_site(span: str, prefix: str) -> bool:
    """匹配片段/前缀是否明确指向非妊娠部位。"""
    head = span[:4]
    tail = prefix[-6:]
    if any(
        location in head or location in tail[-4:]
        for location in ("阴道", "产道", "下体", "见红", "内裤", "护垫")
    ):
        return False
    obstetric = any(
        word in span or word in tail
        for word in (
            "剖宫产", "剖腹产", "产妇", "产后", "孕妇", "孕期", "产褥",
        )
    )
    if obstetric:
        # 会阴/伤口出血（产后常在便溺时发现）仍按孕产期出血处理
        if any(
            word in span or word in tail
            for word in ("伤口", "会阴", "缝线", "缝针")
        ):
            return False
        # 便/尿/痰等只有紧邻出血词时才是「非妊娠部位出血」；
        # 「产后小便时会阴伤口出血」里的「小便时」只是时机
        combined = tail + span
        for word in _NON_PREG_BLEEDING_WORDS:
            if word in ("术后", "伤口"):
                continue
            for match in re.finditer(re.escape(word), combined):
                after = combined[match.end(): match.end() + 4]
                if re.match(
                    r"[^，。；]{0,2}(?:出血|流血|见红|血)", after
                ):
                    return True
        return False
    return any(
        word in span or word in tail
        for word in _NON_PREG_BLEEDING_WORDS
    )


_GUM_BLEEDING_WORDS = (
    "牙龈", "鼻腔", "鼻子", "鼻", "耳", "皮肤", "痔", "口腔",
    # 牙科/口唇出血同样不是孕产期出血（第九十二轮 [8]）；
    # 「唇」不用裸单字，避免「阴唇」被误豁免（第九十三轮 [5]）
    "牙齿", "牙", "嘴唇", "口唇", "唇部", "唇周", "舌头", "舌",
    "口腔黏膜",
    # 口腔动作：刷牙/漱口/吐口水带血同样是牙龈或口腔黏膜出血
    "刷牙", "牙刷", "漱口", "吐口水", "剔牙", "牙缝", "泡沫带血",
)


def _uncertain_bleeding_window(window: str) -> bool:
    """窗口内的不确定问句是否真的指向出血。

    「不确定有没有出血」是待排查；「不知道有没有怀孕，现在没有
    出血」里不确定的是怀孕，出血被明确否认；「不确定是不是羊水
    阴道没有出血」同理（第一百一十九轮 [5][11]）。
    """
    sample = str(window or "")
    tokens = re.search(
        r"不确定|不知道|不清楚|没法确定|无法确定|说不好|有没有|是不是|"
        r"会不会|是否",
        sample,
    )
    if tokens is None:
        return False
    blood = re.search(r"出血|流血|见红|血", sample)
    if blood is None:
        return False
    before_blood = sample[: blood.start()][-6:]
    before_blood = re.sub(
        r"(?:见|见到|出现|发现)\s*$", "", before_blood
    )
    return re.search(
        # 「有没有」里的「没有」不是否定
        r"(?<!有)(?:没有|没|无|未|不|否认|非)\s*$", before_blood
    ) is None


def _bleeding_jianxue_idiom(text: str, start: int, term: str) -> bool:
    """「一针见血/见血封喉/见血就晕」不是出血描述。

    只在无出血部位、且后接晕血类反应时判定（第一百一十五轮 [17]、
    第一百一十六轮 [4]）：「下面/内裤上见血」「见血我就赶紧去了
    医院」「见血都两天了」都是真实出血。
    """
    if "见" not in term or "血" not in term:
        return False
    if "出血" in term or "流血" in term or "见红" in term:
        return False
    if any(
        location in term
        for location in (
            "阴道", "产道", "下体", "下面", "下身", "内裤", "护垫",
            "卫生巾", "上厕所", "擦拭", "擦到",
        )
    ):
        return False
    prefix = text[max(0, start - 4): start]
    suffix = text[start + len(term): start + len(term) + 10]
    if prefix.endswith("一针"):
        return True
    if suffix.startswith("封喉"):
        return True
    return re.match(
        r"\s*(?:我|他|她|人|都|也|会|就|便){0,2}\s*"
        r"(?:就|便)?\s*(?:会|我|他|她|有点|有些)?\s*"
        r"(?:头晕|头昏|晕倒|晕血|晕|害怕|紧张|心慌|发慌|恶心|想吐|"
        r"吐|发抖|发软|腿软|眼花|冒冷汗|出汗)",
        suffix,
    ) is not None


def _generic_bleeding_positive(text: str) -> bool:
    """孕产妇主诉里任何未否定的出血/流血/见红（牙鼻皮肤除外）。

    妊娠状态可能只在档案病史里，主诉只说「今天出血了」：此时不能
    要求出血前必须出现妊娠词。
    """
    for term in ("出血", "流血", "见红", "见血"):
        start = text.find(term)
        while start != -1:
            prefix = _clause_prefix(text, start)

            def _bleeding_negated(value: str, suffix: str = "") -> bool:
                return (
                    re.search(
                        r"(?:没有|没|无|未|不|否认|非)"
                        r"(?:(?:明显|任何|见|见到|出现|发现)\s*)*$",
                        value,
                    ) is not None
                    or _is_negated(value, suffix)
                )

            location_stripped = re.sub(
                r"(?:阴道|产道|下体)\s*$", "", prefix
            )
            if (
                any(word in prefix[-6:] for word in _GUM_BLEEDING_WORDS)
                or _non_pregnancy_bleeding_site(term, prefix)
                or _bleeding_jianxue_idiom(text, start, term)
                or _bleeding_negated(
                    prefix, _clause_suffix(text, start + len(term))
                )
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
        if _bleeding_jianxue_idiom(text, match.start(), span):
            # 「抽血时见到血我就紧张」不是出血描述
            # （第一百一十五轮 [17]）
            continue
        prefix = _clause_prefix(text, match.start())
        if _uncertain_bleeding_window(
            prefix[-10:]
            + span
            + text[match.end(): match.end() + 6]
        ):
            # 「不确定内裤上有没有血」（第一百一十八轮 [1]）
            return True
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
            r"(?:(?:再|再次|明显|任何|见|见到|出现|发现|一点|"
            r"一些|少量)\s*)*$",
            body,
        ) is not None:
            continue
        if any(
            word in span or word in prefix[-6:]
            for word in _GUM_BLEEDING_WORDS
        ):
            continue
        if _non_pregnancy_bleeding_site(span, prefix):
            continue
        if not _is_negated(prefix, _clause_suffix(text, match.end())):
            return True
    return False


#: 主诉中带明确年数的产后/流产后表述：已不是当前妊娠，不能据此
#: 把阴道出血判为孕产期出血（「产后2年，阴道出血」）
_PAST_OBSTETRIC_YEARS = re.compile(
    r"(?:产后|流产后|胎停后?|顺产后|引产后|"
    r"剖宫产(?:手术)?(?:术后|之后|以后|后)?|"
    r"剖腹产(?:手术)?(?:术后|之后|以后|后)?)\s*"
    r"(?:(?:已|已经|都|快|近|将近|差不多|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年|"
    r"(?:1[2-9]|[2-9][0-9])\s*个?月|十二\s*个?月|"
    r"十[二三四五六七八九]\s*个?月|"
    r"[二三四五六七八九]十[一二三四五六七八九]?\s*个?月)"
    # 「三年前流产过」「两年前胎停过一次」「N年前人流」
    r"|(?:[0-9一二两三四五六七八九十半几]+(?:多|几)?|好?多)"
    r"\s*年前[^，。；]{0,6}?(?:流产|胎停|人流|药流|引产)(?:过|了)?"
    # 「人流后一年」「药流后两年」
    r"|(?:流产|人流|药流|引产|胎停)(?:后|完|了|过)?\s*"
    r"(?:(?:已|已经|都|快|近|将近|差不多|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年)"
    # 「去年/前年/以前/曾经流产过」
    r"|(?:去年|前年|上年|以前|之前|曾经|既往)[^，。；]{0,6}?"
    r"(?:流产|胎停|人流|药流|引产)(?:过|了)?"
    # 「3年前分娩时/去年生孩子的时候」
    r"|(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年前|去年|前年|"
    r"多年前|以前|之前)[^，。；]{0,8}?"
    r"(?:分娩|生产|剖宫产|剖腹产|生孩子)"
)
#: 病史里的既往产科史（不再代表当前妊娠）。注意不要包含
#: 「已控制/妊娠期糖尿病」这类与当前妊娠并存的诊断/治疗状态
_HISTORY_PAST_OBSTETRIC = re.compile(
    r"(?:曾经|以前|去年|前年|多年前|既往|已流产|已结束|"
    # 产后/流产后/顺产后/引产后/胎停后的多年表述同样是既往史，
    # 与主诉路径的 _PAST_OBSTETRIC_YEARS 同口径
    r"(?:产后|流产后|顺产后|引产后|胎停后)\s*"
    r"(?:(?:已|已经|都|快|近|将近|差不多|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年|"
    r"(?:1[2-9]|[2-9][0-9])\s*个?月|十二\s*个?月)|"
    r"产后抑郁|"
    r"剖宫产(?:手术)?(?:术后|之后|以后|后)?\s*"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年|"
    r"(?:1[2-9]|[2-9][0-9])\s*个?月|十二\s*个?月)|"
    r"剖腹产(?:手术)?(?:术后|之后|以后|后)?\s*"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年|"
    r"(?:1[2-9]|[2-9][0-9])\s*个?月|十二\s*个?月)"
    r"|(?:[0-9一二两三四五六七八九十半几]+(?:多|几)?|好?多)"
    r"\s*年前[^，。；]{0,6}?(?:流产|胎停|人流|药流|引产)(?:过|了)?"
    r"|(?:流产|人流|药流|引产|胎停)(?:后|完|了|过)?\s*"
    r"(?:(?:已|已经|都|快|近|将近|差不多|已?满(?:了)?)\s*)?"
    r"(?:[0-9一二两三四五六七八九十]+(?:多|几)?\s*年|好?多年))"
)


def _current_history_text(history: str) -> str:
    """过滤掉病史中的既往产科记录，只保留可能代表当前妊娠的分句。"""
    kept = []
    for clause in re.split(r"[，。；;,.!?！？\n]", history):
        if _HISTORY_PAST_OBSTETRIC.search(clause):
            # 同一分句可能并存当前妊娠（「剖宫产术后3年现在怀孕8周」
            # 「产后两年又怀孕了」）：只剥离具体的既往产科短语，不能
            # 整句丢弃，否则同句的当前妊娠证据会一起消失。
            # 「曾经怀孕/去年孕早期出血/已流产」等泛化既往表述没有
            # 可剥离的具体短语（residue 不变），仍整句丢弃，避免把
            # 既往孕史当成当前妊娠
            residue = _PAST_OBSTETRIC_YEARS.sub("", clause)
            if residue == clause:
                continue
            residue = residue.strip(" ，,；;、")
            if residue:
                kept.append(residue)
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
    if _A_NOT_A_PREFIX_RE.search(stripped):
        # 「不确定撞没撞到头」：A 不 A 疑问同样按不确定阳性处理
        # （第一百零二轮 [2]）
        return False
    double_negation = _DOUBLE_NEGATION_SUFFIX_PATTERN.search(stripped)
    if double_negation is not None:
        # 转述否定「你不是说没有胸痛吗」：不是+说+没有+疑问语气
        # 是引用他人的否认，不是阳性（第九十七轮 [17]）
        if re.search(
            r"(?:不是|并非|并不是)说",
            double_negation.group(0),
        ) and re.match(r"[吗么嘛]", suffix.lstrip()):
            return True
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
    # 「这不是胸痛吗/我不是胸痛吗」是反问而非否认：否定词后紧跟
    # 句尾疑问语气词时按阳性处理（第九十五轮 [1]）。否定词与短语
    # 之间允许填充词（不是有/不是明显的），转述框架（说不是/称不是）
    # 仍是否认（第九十六轮 [4]）
    rhetorical = re.search(
        rf"(?:不是|并非|并不是)(?:{_NEGATION_FILLERS})*(?:的)?\s*$",
        stripped,
    )
    if rhetorical is not None and re.match(r"[吗么嘛]", suffix.lstrip()):
        if re.search(
            r"(?:说|称|表示|认为|告诉|问)\s*$",
            stripped[: rhetorical.start()],
        ) is None:
            return False
    # 后置否定（「胸痛没有了」「发烧、咳嗽、胸痛都没有」）只作用于
    # 红旗症状主循环（_trailing_negation_in_clause）：孕产期出血等
    # 独立路径不能用「出血已经没有了」当作否认——早期妊娠出血已停
    # 仍需排查宫外孕/先兆流产（第一百轮 [1]）
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
    r"[^，。；！？,.!?;\n但|只就仅]{1,8}?"
    r"(?:(?:、|和|及|与|或者|以及|或)"
    r"[^，。；！？,.!?;\n但|只就仅]{0,8}?)*"
    r"(?:、|和|及|与|或者|以及|或)?\s*$"
)
#: 枚举项里出现这些词时按阳性处理（症状修饰/转折/加重）
_ENUM_POSITIVE_CUES = (
    "突发", "剧烈", "加重", "持续", "缓解", "出现", "但", "却",
    "越来越", "不能", "无法", "伴有", "伴随", "放射", "撕裂",
    # 「咳嗽并有/还有/同时有胸痛」：并列引出的是新增阳性症状
    # （第一百一十一轮 [2]）
    "并有", "还有", "同时有", "并伴有", "并出现",
    "压榨", "难忍", "明显", "反复",
    # 转折词后的症状是真实主诉（没有呕吐、只是胸痛）
    "只是", "就是", "仅仅", "只为", "仅为", "不过",
    "冷汗", "冒冷汗", "偶发", "偶尔", "偶感", "偶见", "间或",
)


def _clause_prefix(text: str, start: int, lookback: int = 64) -> str:
    """返回短语之前、同一分句内的有限长度前缀。

    窗口需容纳「尚未能完全排除」这类较长的双重否定结构；顿号不截断
    前缀，让枚举否定（「无发热、咳嗽、胸痛」）能看到整组症状。
    """
    boundary = start
    while boundary > 0 and text[boundary - 1] not in _PREFIX_DELIMITERS:
        boundary -= 1
    return text[max(boundary, start - lookback):start]


#: 短语之后的阳性修饰：枚举否定不能吞掉「胸痛加重/伴大汗/放射…」。
#: 持续时间同样是阳性证据：「无发热、胸痛已持续2小时」是真实胸痛
_POST_POSITIVE_CUES = (
    "加重", "加剧", "恶化", "难忍", "剧痛", "大汗", "出汗",
    "冷汗", "冒冷汗", "虚汗",
    "放射", "越来越", "不缓解", "不能缓解", "持续不缓解",
    "没缓解", "没有缓解", "无缓解", "未缓解", "未见缓解",
    "没好转", "没有好转", "无好转", "未见好转",
    # 常见程度修饰同样是阳性证据（无发热、胸痛剧烈/得厉害）
    "剧烈", "明显", "严重", "厉害", "得很", "得厉害", "难忍",
    # 偶发/阵发性描述是真实发作而不是否认
    "偶发", "偶尔", "偶感", "偶见", "间或", "间发", "阵发",
    "好几次", "多次", "反复",
)
#: 后置否定旁若出现这些持续/程度修饰，说明前面的症状是阳性主诉
#: （「持续胸痛、呼吸困难都没有」）（第一百轮 [5]）
_TRAILING_NEGATION_POSITIVE_CUES = tuple(_POST_POSITIVE_CUES) + (
    "持续", "一直", "反复", "频繁", "经常", "总是", "老是", "越来越",
)
#: 患者本人当前病程线索：科普引用分支专用，不复用为枚举否定服务的
#: 偶发/冷汗等阳性词（「文章说偶尔胸痛没事」不是本人病程）
_CURRENT_COURSE_CUES = (
    "加重", "加剧", "恶化", "难忍", "剧痛", "大汗", "出汗",
    "冷汗", "冒冷汗", "虚汗",
    "放射", "越来越", "不缓解", "不能缓解", "持续不缓解",
    "剧烈", "明显", "严重", "厉害", "得很", "得厉害",
    # 「现在还疼/还在痛/仍然疼/还喘」是当前持续病程而非定义
    # （第一百零八轮 [2]）
    "还疼", "还在疼", "还在痛", "还痛", "仍然疼", "仍然痛", "仍疼",
    "一直疼", "一直痛", "还喘", "还在喘", "还在发烧", "还发烧",
    "还烧", "还在出血",
    # 未带固定严重度词的口语当前症状（我有点疼/现在还在抽/还有血）
    # （第一百零九轮 [6]）
    "有点疼", "有点痛", "还有点疼", "还有点痛", "还是疼", "还是痛",
    "现在疼", "现在痛", "还在抽", "现在还在抽", "还有血", "有点血",
    "还有点血",
    # 口语严重度/躯体反应（疼得受不了/很疼/疼得直冒汗）同样是
    # 当前病程证据（第一百一十二轮 [1]）
    "受不了", "难以忍受", "很疼", "很痛", "特别疼", "特别痛",
    "好疼", "好痛", "太疼", "疼死了", "疼得直冒汗", "直冒汗",
    "疼得打滚", "很难受",
)
#: 强度出汗的常见语序：出汗得厉害（补语）/汗出得厉害（倒装）/
#: 出了好多汗、浑身是汗、汗很多（量化）。紧贴形态由第九十八轮
#: [1] 引入，补语与量化形态是同一语义的漏改（第九十九轮 [3][13]）
_SWEAT_INTENSITY_RE = re.compile(
    r"出汗\s*(?:得|的|到)?\s*"
    r"(?:特别|很|非常|明显|厉害|严重|不止|大量|较多|好多|很多|挺多)"
    r"|汗\s*(?:出|流|冒)?\s*(?:得|的)?\s*"
    r"(?:特别|很|非常|明显|厉害|严重|多)"
    r"|(?:出了?|流了?|冒了?)\s*"
    r"(?:好多|很多|大量|不少|一身|满头|满身)(?:的)?汗"
    r"|(?:浑身|全身|满头|一身)(?:都|全)?(?:是|的)?汗"
    r"|汗如雨下|大汗淋漓"
)


def _sweat_intensity_positive(text: str) -> bool:
    """文本是否含未被否定的强度出汗（「没有出很多汗」不算）。

    匹配位置前同一分句若紧邻否定词（没有/没/未/无/不再），该次
    匹配是患者的否认表述，不能当作阳性证据（第一百轮 [12]）。
    """
    text = str(text or "")
    for match in _SWEAT_INTENSITY_RE.finditer(text):
        before = text[max(0, match.start() - 8):match.start()]
        clause_tail = re.split(r"[，。；！？,.!?;\n]", before)[-1]
        if re.search(
            r"(?:没有|没|未|无|不再|不)[^，。；！？,.!?;\n]{0,3}$",
            clause_tail,
        ):
            continue
        return True
    return False
_POST_DURATION_RE = re.compile(
    r"(?:已|已经|有)?\s*(?:持续|发作|反复)?\s*"
    r"[0-9一二两三四五六七八九十半几]+\s*"
    r"(?:个)?(?:多|来)?\s*"
    r"(?:分钟|小时|天|日|周|个?月|年)"
)
#: 出血量/发作次数也是阳性证据（无发热、咯血约50毫升/咯血一口）
_POST_QUANTITY_RE = re.compile(
    r"(?:约|大约|差不多)?\s*[0-9一二两三四五六七八九十半几]+"
    r"(?:\.\d+)?\s*(?:毫升|ml|mL|口|次|阵)"
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


#: 静态程度描述：被否定时说明症状被否认（胸痛不明显）
_STATIC_DEGREE_CUES = (
    "剧烈", "明显", "严重", "厉害", "得很", "得厉害", "难忍",
)


def _post_modifier_positive(suffix: str) -> bool:
    # 「胸痛不明显/不剧烈」：静态程度词前的否定说明症状被否认；
    # 但「胸痛没加重/未恶化」只是否认变化，胸痛本身仍是主诉，
    # 变化类线索（加重/恶化/放射/大汗…）不受否定语缀影响
    if suffix.lstrip().startswith(
        ("伴", "且", "并有", "还有", "同时有", "并出现", "并伴有")
    ):
        # 「无发热、胸痛伴冷汗」「咳嗽并有胸痛」：伴随/并列成分是新增
        # 阳性描述（第一百一十一轮 [2]）
        return True
    for cue in _POST_POSITIVE_CUES:
        for match in re.finditer(re.escape(cue), suffix):
            start = match.start()
            if (
                cue in _STATIC_DEGREE_CUES
                and start > 0
                and suffix[start - 1] in "不没无未"
            ):
                continue
            return True
    if _POST_DURATION_RE.search(suffix) is not None:
        return True
    return _POST_QUANTITY_RE.search(suffix) is not None


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
    # 显式肯定项（有/偶有/时有/少量/有痰…）或急性修饰（突然/突发）
    # 会终止否定范围：「无发热、有咯血」「无发热、突然胸痛」不是
    # 在否认咯血/胸痛；裸症状枚举（无发热、咳嗽、胸痛）仍整体否定
    items = re.split(r"或者|以及|或|和|及|与|、", body)
    for item in items[1:]:
        text_item = item.strip()
        if not text_item:
            continue
        if text_item.startswith(
            ("有", "偶有", "时有", "稍有", "少量", "有痰", "出现",
             "伴有", "伴随", "偶发", "偶尔", "偶感", "偶见", "间或",
             "间发", "阵发", "反复", "多次")
        ):
            return False
        if text_item.endswith(("伴", "并", "且")):
            # 「无发热、咳嗽伴胸痛」：伴发成分是新的阳性描述
            return False
        if any(
            cue in text_item
            for cue in ("突然", "突发", "偶发", "偶尔", "偶感", "偶见",
                        "间或", "阵发", "反复发作", "好几次")
        ):
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
    "婴儿", "幼儿", "儿童", "新生儿", "小儿",
    "老婆", "老公", "爱人", "对象", "丈夫", "妻子", "配偶", "老伴",
    "哥哥", "姐姐", "弟弟", "妹妹", "兄弟", "姐妹",
    "阿姨", "叔叔", "舅舅", "姑姑", "婶婶", "侄子", "外甥",
    "孙子", "孙女",
    # 常见亲属/口语称谓（第九十三轮 [2]）
    "岳父", "岳母", "公公", "婆婆", "女婿", "儿媳", "闺女",
    "老爷子", "老太太", "娃",
    # 媳妇/丈母娘/老丈人/病人/家属同样是患者主语或家属主语
    # （第一百一十五轮 [5]）
    "媳妇", "媳妇儿", "丈母娘", "老丈人", "岳丈", "病人", "家属",
    "亲人",
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
    # 第一百一十五轮 [5]：定义问句前的患者/家属称谓
    "媳妇", "媳妇儿", "丈母娘", "老丈人", "岳丈", "病人", "家属",
    "亲人",
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
    r"^([^，,；;。！？!?\n]*)(?:是什么感觉|什么感觉|是什么样的感觉|"
    r"是什么样感觉|什么样的感觉|是啥感觉|是啥样的感觉|啥感觉|"
    r"是什么样的|是什么样子|是什么症状|有什么症状|会有什么感觉|"
    r"是什么|啥是|是什么意思|什么意思|"
    r"是指什么|指什么|是啥|"
    r"是什么病的?(?:症状|表现|征兆|前兆|早期症状|早期表现)|"
    r"是什么(?:病|疾病)(?:引起|导致)的?|"
    r"是啥病|是什么病|是什么疾病|是啥疾病)"
    r"\s*[呢呀啊吧嘛吗]?\s*[？?]?$"
)
#: 疑问词前置的定义问句：「什么病会引起胸痛」「哪些疾病会导致咯血」
#: 是在问科普，不是患者主诉（第八十九轮）；「什么病会胸痛/什么情况
#: 下会咯血」情态词直接接红旗症状同样是科普提问（第一百零六轮 [7]）
_DEFINITION_LEAD_Q_RE = re.compile(
    r"^(?:请问)?(?:什么病|什么疾病|哪些病|哪些疾病|何种疾病|什么原因|"
    r"什么情况|哪些原因|哪类疾病|哪类病)"
    r"[^，。；]{0,10}?(?:会|能|可以|可能|容易)?\s*"
    r"(?:引起|引发|导致|造成|诱发|出现)"
    r"|^(?:请问)?(?:什么病|什么疾病|哪些病|哪些疾病|何种疾病|"
    r"什么原因|什么情况|哪些原因|哪类疾病|哪类病)"
    r"[^，。；]{0,10}?(?:会|能|可以|可能|容易)\s*"
    r"(?=(?:胸痛|胸疼|胸口|心口|心前区|胸部|胸骨|咯血|咳血|抽搐|惊厥|"
    r"呼吸困难|喘不上气|晕厥|昏迷|呕血|吐血|黑便|血便|剧烈头痛))"
)
#: 症状在前的科普问句：「X见于什么病」「X的原因有哪些」
_DEFINITION_MID_RE = re.compile(
    r"(?:见于|可见于|常见于)\s*(?:哪些|什么|何种)"
    r"|的(?:原因|病因|诱因)\s*(?:有哪些|是什么|是啥)"
)

#: 口语化「提问前缀」：去掉后按剩余内容重新判断，避免「我想知道什么是
#: 咯血」因含「我」被当成患者主诉而触发急症横幅
_EDUCATIONAL_PREFIX_RE = re.compile(
    r"^(?:请问(?:一下)?|我想知道|我想问下|我想问问|我想问个问题|"
    # 「问个问题：什么是咯血」「我想问个问题」：长框架必须先匹配，
    # 否则被「我想问」截断留下「个问题」（第一百零九轮 [7]）
    r"问个问题|有个问题|我想问(?:一下)?|"
    r"想问(?:问|一下|个问题)?|问下|"
    # 「帮我查一下什么是咯血」：帮查类框架同样是科普提问；叠词
    # （帮我看看/查查）与「帮忙」同口径（第一百零九轮 [7]、
    # 第一百一十轮 [15]）
    r"(?:帮我|帮忙)(?:查|看|搜|找)(?:查|看|搜|找)?(?:一下|下)?|"
    r"帮我了解(?:一下|下)?|"
    r"咨询(?:一下)?|想咨询(?:一下)?|"
    r"我想了解(?:一下|下)?|想了解(?:一下)?|了解一下|听说|我听说|据说)"
)
#: 媒体词作为症状发生的时间语境（看视频时胸痛/看了个视频后胸痛）：
#: 不是被引用的科普内容，不能按科普丢弃（第八十三轮）
_EDUCATION_MEDIA_TIME_CONTEXT_RE = re.compile(
    r"(?:看|刷|听|读|浏览)(?:了|过|着)?"
    r"(?:[0-9一二两三四五六七八九十]+)?"
    r"(?:个|段|篇|条|集|一会儿|会儿)?\s*"
    r"(?:短视频|小视频|视频|文章|新闻|帖子|百科|电视|报纸|讲座|广播)"
    r"[^，。；]{0,6}?(?:的时候|时|以后|之后|后|就|便|即|看得|看着)"
    r"|(?:一边|边)(?:看|刷|听)[^，。；]{0,6}一边"
)

#: 发热话题结构（「关于发烧的文章」「发烧的护理」）：被引用的
#: 内容标题，不是患者本人发热（第八十八轮）
_FEVER_TOPIC_RE = re.compile(
    r"(?:关于|讲|介绍|怎么处理|怎么办|如何|科普)[^，。；]{0,12}?"
    r"(?:发烧|发热)"
    r"|(?:发烧|发热)[^，。；]{0,6}?的(?:护理|处理|文章|视频|科普|"
    r"新闻|帖子|百科|内容|问题)"
    r"|(?:发烧|发热)(?:护理|处理|文章|视频|科普|新闻|帖子|百科|内容|"
    r"问题)"
)

#: 媒体名词之后才出现症状：真实伴随症状（「看视频胸痛」）而非
#: 内容标题（「讲抽搐的视频」「关于胸痛的文章」）
def _media_then_symptom(clause: str) -> bool:
    noun = re.search(
        r"(?:短视频|小视频|视频|文章|新闻|帖子|百科|电视|报纸|讲座|广播)",
        str(clause or ""),
    )
    if noun is None:
        return False
    rest = str(clause)[noun.end():]
    return _is_red_flag_text(rest) or any(
        term in rest for term in _COLLOQUIAL_SYMPTOM_WORDS
    )


#: 传闻式语句整句按科普处理，不构成患者本人当前主诉
_HEARSAY_PREFIX_RE = re.compile(r"^(?:听说|我听说|据说)")
#: 病程/数量线索：定义问句前缀含这些词时按真实主诉保留
_COURSE_MARKER_RE = re.compile(
    # 「十分钟/一个小时/十几分钟/三个月」：中文数字+可选个/来/多
    # +分钟等短时程单位，与阿拉伯数字写法同口径（急性胸痛/抽搐
    # 在一小时内最需要急诊，不能因写法不同整句丢弃）
    r"[0-9一二两三四五六七八九十半几]+\s*"
    r"(?:个[来多]?|来|多|余)?\s*"
    r"(?:分钟|小时|钟头|天|日|周|星期|个月|月|年)"
)
#: 定义问句前缀里出现这些场景/程度修饰时按真实主诉保留
#: （「活动后胸痛是什么病」「胸痛越来越重是什么病」「一侧肢体
#: 无力是什么病」都在描述患者的当前症状组合，不是纯术语询问）
_DEFINITION_KEEP_CUES = (
    "活动后", "活动时", "活动", "劳累", "走路", "行走", "爬楼",
    # 运动/搬抬等劳力场景同样是真实主诉（运动后胸痛是什么病，
    # 第一百轮 [6]）
    "运动", "跑步", "打球", "健身", "游泳", "锻炼", "上楼", "爬坡",
    "搬东西", "搬重物", "干活", "体力活动", "劳作",
    "夜间", "夜里", "半夜", "睡前", "晨起", "早上",
    # 晚上/白天/深夜/清晨同样是真实发作时段（第一百一十七轮 [2]）
    "晚上", "白天", "深夜", "清晨",
    "深呼吸", "呼吸时", "按压", "触碰",
    "一侧", "单侧", "半边", "偏侧", "肢体",
    "加剧", "越来越", "加重",
)


_COURSE_MARKERS = (
    "持续", "反复", "最近", "今天", "昨天", "前天", "这几天", "刚才",
    "经常", "常常", "时常", "总是", "老是", "一直", "长期", "多年",
    "很久", "好久", "很长时间", "好几天", "数天", "数周", "数月",
    "近来", "这段时间",
    # 时段词同样是近期病程线索（昨晚/今早/凌晨胸痛是什么病）
    "昨晚", "昨夜", "今晚", "今早", "今晨", "凌晨", "早晨", "早上",
    "上午", "中午", "下午", "傍晚", "刚刚", "上周",
    # 晚上/白天/深夜/清晨与频次词同样是真实病程线索
    # （第一百一十七轮 [2]）
    "晚上", "白天", "深夜", "清晨", "每天", "每日", "天天", "每晚",
    "频繁", "时不时", "间断",
    # 发作/频次形态同样是真实病程线索（胸痛一阵一阵的是什么病）
    "一阵一阵", "一阵阵", "一阵子", "一段时间", "时好时坏",
    "反反复复", "好几次", "好几回", "好多次",
)


def _is_red_flag_text(text: str) -> bool:
    if any(term in text for term, _ in RED_FLAG_PHRASES):
        return True
    if _has_head_trauma(text):
        return True
    # 孕产期出血同样是确定性急症信号（阴道出血/见红）
    return _has_pregnancy_bleeding(text)


def _definition_multi_symptom(prefix: str) -> bool:
    """定义问句前缀是否含≥2 个不同症状词（无标点并写）。"""
    tokens = set()
    for phrase, _label in RED_FLAG_PHRASES:
        tokens.add(phrase)
    tokens.update(FEVER_TERMS)
    tokens.update(_NEGATED_SYMPTOM_TERMS)
    tokens.update(_COLLOQUIAL_SYMPTOM_WORDS)
    tokens.update(("白沫", "口唇发紫", "口唇青紫", "发绀", "吐白沫"))
    matched: list = []
    for token in sorted(
        (t for t in tokens if len(t) >= 2 and t in prefix),
        key=len,
        reverse=True,
    ):
        if all(token not in chosen for chosen in matched):
            matched.append(token)
            if len(matched) >= 2:
                return True
    return False


#: 第三人称代词主语（他抽搐是什么病/请解释一下她胸痛）：代词在
#: 分句开头且后接症状时是在描述真实主诉；「他们/其他/他汀/吉他」
#: 等不是人称主语
_THIRD_PERSON_SUBJECT_RE = re.compile(
    r"^\s*(?:一下)?\s*(?:他|她)(?!们|汀|俩|乡|人|处|杀|自己)"
)


def _third_person_symptom_subject(text: str) -> bool:
    """分句是否以「他/她 + 症状」描述第三方（含患者本人）主诉。"""
    if _THIRD_PERSON_SUBJECT_RE.match(text) is None:
        return False
    return _is_red_flag_text(text) or any(
        term in text for term in _COLLOQUIAL_SYMPTOM_WORDS
    )


_URGENT_HELP_RE = re.compile(
    r"(?:怎么办|怎么办啊|怎么处理|如何处理|该怎[么样]办|我该怎么办|"
    r"要不要紧|要紧吗|严重吗|严不严重|严重不严重|危险吗|"
    r"有没有事|会不会有事|"
    # A-not-A 求助形式可省略疑问尾；「需要/要去/要/应该+医院」
    # 的肯定形式必须带疑问标记，避免把「我明天要去医院体检」这类
    # 陈述当作求助（第一百一十轮 [3]、第一百一十一轮 [14]）
    r"(?:要不要|用不用|需不需要|该不该)"
    r"\s*(?:马上|立刻|立即|赶紧|尽快|现在就|现在|就)?\s*"
    r"(?:去|到|送|上|叫|打)?\s*"
    r"(?:医院|急诊|看(?:一下)?医生|看大夫|医生|救护车|急救|120)"
    r"(?:看看|检查|就诊|瞧一瞧)?\s*[吗嘛呢]?|"
    # 紧迫副词（马上/赶紧…）+ 医院：自述需要立即就医，可省略疑问
    # 标记（第一百一十二轮 [5]）
    r"(?:需要|要|应该)\s*(?:马上|立刻|立即|赶紧|尽快|现在就)\s*"
    r"(?:去|到)?\s*(?:医院|急诊)(?:看看|检查|就诊|瞧一瞧)?|"
    # 一般肯定式：疑问标记可以在同分句稍后出现（检查一下吗/
    # 去医院么），但计划陈述「明天要去医院体检」不算求助
    # （第一百一十一轮 [14]、第一百一十二轮 [3]）
    r"(?:需要|要去|要|应该)"
    r"\s*(?:马上|立刻|立即|赶紧|尽快|现在就|现在|就)?\s*"
    r"(?:去|到)?\s*(?:医院|急诊)(?:看看|检查|就诊|瞧一瞧)?"
    r"(?=[^，。；,.!?！？\n]{0,12}(?:吗(?!丁啉|啡)|嘛|呢)"
    r"|[^，。；,.!?！？\n]{0,12}(?<![什怎那这多要甚])么\s*$)|"
    # 常见求助载体：看医生/看大夫/送医院/叫救护车/打120
    # （第一百一十五轮 [1]）
    r"(?:需要|要|应该|得)\s*(?:去|到|送|上|叫|打)?\s*"
    r"(?:看(?:一下)?医生|看大夫|医生|救护车|急救|120)"
    r"|怎么缓解|怎么治疗)"
)


def _pregnancy_term_positive(text: str, term: str) -> bool:
    """妊娠词是否以当前（非既往）语义出现；预产期也可能是往年。"""
    if not _positive_mention(text, term):
        return False
    if term in ("预产期", "足月"):
        if re.search(
            rf"{term}[^，。；]{{0,6}}(?:去年|前年|多年前)|"
            rf"(?:去年|前年|多年前)[^，。；]{{0,4}}{term}",
            text,
        ):
            return False
    return True


def _clean_complaint_clause(
    clause: str, patient_context_seen: bool = False,
    help_follows: bool = False, course_follows: bool = False,
    symptom_context_seen: bool = False,
) -> str:
    """清洗单个分句：有患者语境的保留；纯定义问句丢弃。

    patient_context_seen 表示前面已有明确患者主语的症状分句
    （「宝宝发烧，抽搐是什么病」）：此时后置定义问句的前缀是
    患者本人的新症状，不能当成纯定义丢弃。
    """
    educational = _EDUCATIONAL_PREFIX_RE.match(clause)
    if educational:
        # 前缀后的冒号/逗号一并剥掉：「问个问题：什么是咯血」
        # （第一百零九轮 [7]）
        rest = clause[educational.end():].strip(" ，,、：:")
        if not rest:
            return ""
        if _HEARSAY_PREFIX_RE.match(clause):
            # 「听说我爸今天胸痛得厉害」是家属急症：剥掉传闻前缀后
            # 仍按主体/红旗判断；「听说咯血是肺癌的症状」才是科普
            if (
                any(
                    marker in rest
                    for marker in _FAMILY_HISTORY_MARKERS
                )
                and _is_red_flag_text(rest)
            ) or _has_patient_subject(rest) or _course_context_clause(rest):
                return _clean_complaint_clause(
                    rest, patient_context_seen, help_follows,
                    course_follows, symptom_context_seen,
                )
            return ""
        # 去掉「请问/我想知道」后按剩余内容重新判断：「什么是咯血」仍是
        # 纯定义问句，而「胸痛两天了是什么病」仍按病程主诉保留
        return _clean_complaint_clause(
            rest, patient_context_seen, help_follows,
            course_follows, symptom_context_seen,
        )
    definition_lead = _DEFINITION_PREFIX_RE.match(clause)
    if definition_lead:
        # 句首定义引导词（什么是/什么叫/请解释一下…）：剥掉前缀后
        # 若剩余内容含患者主体或病程线索，按真实主诉继续判断
        rest = clause[definition_lead.end():].strip(" ，,、")
        if not rest:
            return ""
        if (
            _has_patient_subject(rest)
            # 第三人称代词主语 + 症状：描述的是真实患者主诉
            or _third_person_symptom_subject(rest)
            # 孕产期出血无论是否有主语都是真实急症描述
            or any(term in rest for term in PREGNANCY_BLEEDING_TERMS)
            # 家属语境 + 红旗（请解释一下我爸胸痛是什么原因）；
            # 称谓表与 keep_context 同源：外甥/孙子/叔叔/老爷子等
            # 同样按真实患者语境保留（第一百零六轮 [15]）
            or (
                any(
                    marker in rest
                    for marker in (
                        *_FAMILY_HISTORY_MARKERS,
                        *_PATIENT_SUBJECT_MARKERS,
                        "老爷子", "老太太", "闺女", "娃", "老人",
                    )
                )
                and _is_red_flag_text(rest)
            )
            # 急性求助/突发修饰/多症状/头外伤：即使没有主语也是
            # 真实主诉描述（解释一下抽搐怎么办、突发胸痛是什么病、
            # 抽搐吐白沫是什么病）；纯术语问句（介绍一下呼吸困难、
            # 什么是持续性胸痛）仍按科普丢弃
            or re.search(
                r"(?:怎么办|怎么处理|如何处理|该怎[么样]办|"
                r"要不要紧|严重吗)", rest
            ) is not None
            or any(
                word in rest
                for word in (
                    # 只保留修饰词：「什么是抽搐/昏迷」是纯术语查询，
                    # 必须靠「怎么办」或多症状等线索才按主诉保留
                    "突发", "突然", "剧烈", "加重", "无法", "不能",
                    "大汗", "冷汗",
                )
            )
            or _definition_multi_symptom(rest)
            or _has_head_trauma(rest)
            or (help_follows and _is_red_flag_text(rest))
            # 「什么是抽搐？已经抽了五分钟了」：后置分句的病程/严重度
            # 线索说明这是在描述真实主诉（与后缀定义分支同口径，
            # 第一百零五轮 [1]）
            or (course_follows and _is_red_flag_text(rest))
        ):
            return _clean_complaint_clause(
                rest, patient_context_seen, help_follows,
                course_follows, symptom_context_seen,
            )
        # 纯术语解释（介绍一下呼吸困难/什么是咯血）仍按科普丢弃
        return ""
    blocker_clause = str(clause).replace("说明书", "")
    if any(word in blocker_clause for word in _EDUCATION_BLOCKERS_STRONG):
        # 引用科普/文章/网上内容的叙述不是患者当前主诉：忽略
        # 「我在网上看到胸痛要重视」这类分句，避免把科普当急症；
        # 同一分句仍明确描述当前病程（今天/持续/越来越重）或
        # 家属急症时保留
        # 纯引述内容（网上说/文章讲…）没有患者主语时，其中的
        # 时长/频次/程度线索属于被引用的科普内容（网上说胸痛持续
        # 十分钟要就医），不能据此保留分句
        quote_lead = _EDUCATION_QUOTE_LEAD_RE.search(clause)
        # 「我+网上看到/刷到+说」里的「我」是读者（我在网上看到说
        # 胸痛很严重），不能当作患者主语；照教程吃药等本人动作仍算
        # 患者语境（第九十六轮 [5]）
        quote_reader_only = False
        if quote_lead is not None:
            before_quote = clause[: quote_lead.start()]
            after_quote = clause[quote_lead.end():]
            # 「我」在引述词之前且引述词后没有本人动作（吃药/跳操/运动）
            # 时只是读者；「我照网上教程吃药后呼吸困难」这类本人动作
            # 仍按患者语境处理（第九十六轮 [5]）
            patient_action_after = re.search(
                r"(?:吃了?|服药|服用|吃药|喝了?|跳操|运动|锻炼|"
                r"跑步?|走路?|干活|搬东西|洗澡|工作|上班)",
                after_quote,
            ) is not None
            if (
                re.match(r"\s*(?:我|本人)[^，。；]{0,8}$", before_quote)
                is not None
                and not patient_action_after
            ):
                quote_reader_only = True
        quote_subject_region = (
            clause[quote_lead.end():]
            if quote_reader_only else clause
        )
        quote_only = bool(
            quote_lead is not None
            and not _has_patient_subject(quote_subject_region)
            and _EDUCATION_CURRENT_RE.search(clause) is None
            # 省略主语的「这几天胸痛在网上查了」同样是患者主诉
            and not _patient_course_before_quote(clause)
        )
        current_course = (not quote_only) and (
            # 「网上说胸痛持续十分钟」里的时长属于引用内容；只有
            # 明确「我今天/我现在胸痛」才是本人当前病程
            any(word in clause for word in _CURRENT_COURSE_CUES)
            # 「我这几天胸痛在网上查了」：症状出现在引用词之前且带
            # 患者病程线索时，症状本身不是被引用的科普内容
            or _patient_course_before_quote(clause)
            # 「网上买的药吃了胸痛/跟着视频跳操后胸痛」：本人服药的
            # 动作线索说明这不是科普引用；但纯引述内容里的动作词
            # （网上说吃完药后胸痛）没有患者主语，不能据此保留
            or (
                any(cue in clause for cue in _EDUCATION_CURRENT_CUES)
                and (
                    _has_patient_subject(clause)
                    or _EDUCATION_CURRENT_RE.search(clause) is not None
                    or _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
                )
            )
            # 「网上看到胸痛要重视我现在胸痛」：引用之后同分句仍在
            # 描述本人当前症状
            or _EDUCATION_CURRENT_RE.search(clause) is not None
            # 「网上说胸痛要重视我胸口痛」：引述词后出现患者主语
            # 与红旗症状（第一百一十九轮 [1]）
            or _patient_course_after_quote(clause)
            # 「看视频时/看了个视频后胸痛」：媒体词只是症状发生的
            # 时间语境，不是科普引用（第八十三轮）
            or (
                _EDUCATION_MEDIA_TIME_CONTEXT_RE.search(clause)
                is not None
                and _is_red_flag_text(clause)
                and _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
            )
            # 「我昨天看视频胸痛」「最近刷视频胸痛」「我看视频胸痛
            # 已经两天了」：媒体词后没有连接词，但时间/病程线索表明
            # 是本人症状而非科普引用（第八十五轮）
            or (
                _is_red_flag_text(clause)
                and _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
                and _media_then_symptom(clause)
                and (
                    _COURSE_MARKER_RE.search(clause) is not None
                    or any(
                        cue in clause
                        for cue in (
                            "昨天", "昨晚", "今天", "最近", "这两天",
                            "这几天", "刚才", "刚刚", "现在",
                        )
                    )
                )
            )
            # 「宝宝发烧38.5度看了文章」：发热不在红旗表，但婴儿/
            # 免疫抑制伴发热规则依赖它，未被否定的发热必须保留
            # （第八十七轮）
            or (
                _has_fever(clause)
                and _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
                and _FEVER_TOPIC_RE.search(clause) is None
            )
            # 「网上查了胸痛怎么办」：检索词之后是患者本人的红旗
            # 症状（胸痛/阴道出血/头外伤），不能当科普丢弃；引述
            # 引导词（网上说/文章讲）仍按科普处理（第九十七轮 [2]）
            or (
                _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
                and _EDUCATION_TOPIC_REFERENCE_RE.search(clause) is None
                and (
                    _is_red_flag_text(clause)
                    or _has_pregnancy_bleeding(clause)
                    or _has_head_trauma(clause)
                )
            )
        )
        family_redflag = any(
            marker in clause for marker in _FAMILY_HISTORY_MARKERS
        ) and _is_red_flag_text(clause)
        mentions_symptom = _is_red_flag_text(clause) or any(
            term in clause for term in _COLLOQUIAL_SYMPTOM_WORDS
        )
        if not (current_course or family_redflag) and mentions_symptom:
            # 只丢弃把症状写进科普/文章引用的分句；「我在网上查了
            # 资料」这类无症状的铺垫保持原样
            return ""
    lead_q = _DEFINITION_LEAD_Q_RE.match(clause)
    mid_q = _DEFINITION_MID_RE.search(clause)
    if (
        (lead_q is not None or mid_q is not None)
        and not _has_patient_subject(clause)
        and not _course_context_clause(clause)
        and not _has_head_trauma(clause)
    ):
        if mid_q is not None:
            stripped = clause[: mid_q.start()].strip(" ，,、的")
        else:
            stripped = clause[lead_q.end():].strip(" ，,、的")
        if stripped == clause:
            stripped = ""
        # 剥掉问句框架后仍带病程/严重度（胸痛持续两小时了见于什么病、
        # 胸痛剧烈见于什么病）：问句里的症状是真实主诉，不能因整句
        # 超过 _course_context_clause 的 10 字门槛被当成纯科普丢弃
        # （第九十四轮 [1]）
        symptom_course = bool(stripped) and (
            _COURSE_MARKER_RE.search(stripped) is not None
            or any(marker in stripped for marker in _COURSE_MARKERS)
            or _POST_DURATION_RE.search(stripped) is not None
            or any(cue in stripped for cue in _POST_POSITIVE_CUES)
        )
        # 「什么病会引起胸痛」「胸痛见于什么病」等科普提问默认整句
        # 丢弃；但分句含求助语、后续分句有求助/病程线索、或家属+
        # 红旗时，它是在描述真实主诉：剥掉问句框架保留症状，不能
        # 把胸痛/咯血急症一并吞掉（第九十轮 [1]）
        keep_context = bool(
            _URGENT_HELP_RE.search(clause)
            or help_follows
            or course_follows
            or symptom_course
            or (
                any(
                    marker in clause
                    for marker in _FAMILY_HISTORY_MARKERS
                )
                and _is_red_flag_text(clause)
            )
            # 家属/患儿称谓与 _FAMILY_HISTORY_MARKERS 不同步：
            # 孙子/外甥/叔叔/阿姨/老爷子/老太太/闺女/娃 等同样是
            # 真实患者语境（「孙子抽搐见于什么病」）（第一百零五轮 [7]）
            or (
                any(
                    marker in clause
                    for marker in _PATIENT_SUBJECT_MARKERS
                    + ("老爷子", "老太太", "闺女", "娃", "老人")
                )
                and _is_red_flag_text(clause)
            )
            # 「我爸咳嗽三天了，咯血见于什么病」：前文已有症状级
            # 语境，后置定义问句里的红旗是新症状；仅有主体/生活
            # 叙述（我正在准备考试）不算（第九十二/九十三轮 [14]）
            or (symptom_context_seen and _is_red_flag_text(clause))
        )
        if not keep_context:
            return ""
        if stripped:
            return _clean_complaint_clause(
                stripped, patient_context_seen, help_follows,
                course_follows,
            )
        return ""
    if any(marker in clause for marker in _PATIENT_CONTEXT_MARKERS):
        if not any(
            word in clause for word in _EDUCATION_BLOCKERS_STRONG
        ):
            return clause
    if any(marker in clause for marker in EDUCATIONAL_MARKERS):
        # 「表现为/症状包括」既可能是科普也可能是真实主诉：含红旗
        # 且不是定义问句时必须保留，否则 ACS/呼吸困难会静默漏报
        if (
            (
                _is_red_flag_text(clause)
                and not _DEFINITION_PREFIX_RE.match(clause)
                and not _DEFINITION_SUFFIX_RE.match(clause)
                and not _FEATURE_QUESTION_RE.search(clause)
            )
            # 「主要表现是发烧39度」「症状包括发烧38.5度」：发热不在
            # 红旗表，但婴儿/免疫抑制伴发热规则依赖它，未被否定的
            # 发热必须保留（第八十九轮，与引述分支同口径）
            or (
                _has_fever(clause)
                and _EDUCATION_QUOTE_LEAD_RE.search(clause) is None
                and _FEVER_TOPIC_RE.search(clause) is None
            )
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
        experiential_definition = re.search(
            r"(?:是什么感觉|什么感觉|是什么样的感觉|是什么样感觉|"
            r"什么样的感觉|是啥感觉|是啥样的感觉|啥感觉|"
            r"是什么样的|是什么样子|是什么症状|"
            r"有什么症状|会有什么感觉)\s*[呢呀啊吧嘛吗]?\s*[？?]?\s*$",
            clause,
        ) is not None
        if any(word in prefix for word in _EDUCATION_BLOCKERS_STRONG):
            media_time = bool(
                _EDUCATION_MEDIA_TIME_CONTEXT_RE.search(prefix)
                and _is_red_flag_text(prefix)
                and _EDUCATION_QUOTE_LEAD_RE.search(prefix) is None
            )
            if media_time:
                # 「看视频时胸痛是什么病」：媒体词是症状发生的时间
                # 语境，不是被引用的科普内容（第九十二轮 [7]）
                return prefix
            # 「在网上看到胸痛是什么病」引用的是科普内容，不是主诉
            return ""
        # 「X是什么病」：X 是带病程的症状描述时按真实主诉保留，
        # 只有单一术语的纯定义问句才整句丢弃
        if prefix and (
            # 问候语里的「早上/晚上」不是患者病程（早上好胸痛是什么病）
            not _is_greeting_clause(prefix)
            and (
                _COURSE_MARKER_RE.search(prefix) is not None
                or any(marker in prefix for marker in _COURSE_MARKERS)
            )
            # 多症状枚举、客观测量值、头外伤或急性修饰都是真实主诉，
            # 不能因为带「是什么病」就整句丢弃；「伴/伴随/合并」
            # 与顿号/和/及/与同属多症状连接词
            or re.search(
                r"[、和及与]|伴|合并|并发", prefix
            ) is not None
            or re.search(r"\d", prefix) is not None
            # 发作频次（抽搐了两次/咳嗽好几回）是真实病程而非术语解释
            or re.search(
                r"(?:[0-9一二两三四五六七八九十半几]+|好|多|数)"
                r"\s*(?:次|阵|回)",
                prefix,
            ) is not None
            or _has_head_trauma(prefix)
            or _URGENT_HELP_RE.search(prefix) is not None
            or (help_follows and _is_red_flag_text(prefix))
            # 「胸痛是什么病啊，痛得我直冒冷汗」：后置分句的严重度/
            # 病程线索说明前一句是真实主诉（第九十四轮 [1]）
            or (course_follows and _is_red_flag_text(prefix))
            or (not experiential_definition and any(
                word in prefix
                for word in (
                    "突发", "突然", "剧烈", "加重", "无法", "不能",
                    "大量", "持续", "反复",
                    # 典型急症修饰：放射/压榨/冷汗等同样说明是真实主诉
                    "放射", "压榨", "撕裂", "大汗", "冷汗", "冒汗",
                    "难忍", "濒死", "躺不平", "不能平卧", "端坐呼吸",
                    "闷痛", "绞痛", "刀割", "难以忍受", "受不了",
                    "像石头", "像刀割", "针扎", "压着", "翻白眼",
                    "左侧", "右侧", "左边", "右边", "左胸", "右胸",
                    "左肩", "右肩", "胸前区",
                )
            ))
            # 枚举/后置阳性线索（越来越/加剧/恶化…）与场景修饰
            # （活动后/夜间/走路时…）同样说明是在描述真实主诉
            or any(word in prefix for word in _POST_POSITIVE_CUES)
            or any(word in prefix for word in _DEFINITION_KEEP_CUES)
            # 无标点并写的多症状（「发热胸痛」「抽搐吐白沫」）是真实
            # 主诉，不是单一术语解释
            or _definition_multi_symptom(prefix)
            # 第三人称代词主语的症状（「他抽搐是什么病」）同样不是
            # 纯术语解释
            or _third_person_symptom_subject(prefix)
            # 前文已明确患者本人（「宝宝发烧，抽搐是什么病」）：
            # 单一红旗症状也按真实主诉保留
            or (
                patient_context_seen
                and (
                    _is_red_flag_text(prefix)
                    or _colloquial_bleeding_positive(prefix)
                )
            )
            # 孕产期出血无论是否有前文患者语境都按真实主诉保留：
            # 「阴道出血是什么病」在孕期档案下同样是急症描述；
            # 量词插入（阴道有点出血/孕早期少量出血）同样命中
            or any(term in prefix for term in PREGNANCY_BLEEDING_TERMS)
            or _PREGNANCY_BLEEDING_RE.search(prefix) is not None
            or _colloquial_bleeding_positive(prefix)
            # 无「我」字的亲属/孕产主诉（岳父胸痛是什么病 / 怀孕了
            # 抽搐是什么病）同样是真实急症描述（第九十三轮 [2]）
            or (
                any(
                    marker in prefix
                    for marker in _FAMILY_HISTORY_MARKERS
                )
                and _is_red_flag_text(prefix)
            )
            # 患者/家属主语（病人/家属/亲人）+ 单一红旗术语同样是
            # 真实主诉（第一百一十五轮 [5]）
            or (
                any(
                    marker in prefix
                    for marker in _PATIENT_SUBJECT_MARKERS
                )
                and _is_red_flag_text(prefix)
            )
            or any(term in prefix for term in PREGNANCY_TERMS)
        ):
            # 带病程/时间线索或多症状的定义问句通常是在描述当前症状
            return prefix
        return ""
    if any(
        word in clause for word in _EDUCATION_BLOCKERS_STRONG
    ) and re.search(
        r"(?:是什么|啥是|是什么意思|什么叫|啥叫|何谓|何为)", clause
    ):
        # 「在网上看到胸痛是什么病的文章」：引用科普标题，不是主诉
        return ""
    return clause


#: 科普/寒暄式铺垫：即使带时间词也不建立患者语境
_COURSE_CONTEXT_BLOCKERS = (
    "查", "资料", "学习", "了解", "问问", "问一下", "问下", "咨询",
    "搜索", "网上", "听说", "据说", "请问", "科普", "看了", "文章",
    "视频", "新闻", "百科", "帖子",
    # 寒暄/致谢分句不能为后面的定义问句背书
    "你好", "您好", "谢谢", "好的", "在吗", "麻烦", "打扰",
    # 与症状无关的生活叙述（最近在准备考试/这两天在减肥）：即使带
    # 时间词也不能把纯定义问句变成患者主诉（第九十二轮 [4]）
    "减肥", "考试", "工作", "加班", "门诊", "聚会", "出差", "旅游",
    "搬家", "开会", "上课", "作业", "婚礼", "装修", "面试", "聚餐",
    "旅行", "度假", "排练", "比赛", "年会", "约会", "相亲",
    # 「早上好」里的时段词是问候，不是患者病程
    "早上好", "早安", "晚上好", "晚上好", "午安", "晚安", "哈喽",
)

def _is_greeting_clause(text: str) -> bool:
    return any(
        word in str(text or "")
        for word in (
            "早上好", "早安", "晚上好", "午安", "晚安", "哈喽",
            "你好", "您好",
        )
    )


#: 明确的科普/检索语境：即使分句含「我/今天」也不按患者主诉保留
_EDUCATION_BLOCKERS_STRONG = (
    "网上", "查资料", "查了资料", "科普", "文章", "视频", "新闻",
    "帖子", "百科", "听说", "据说", "搜了", "搜索",
    # 电视/报道/报纸/讲座同样是引述来源，但只拦截引述短语：
    # 「看电视时胸痛」是症状时间语境，不能整句丢弃；「说明书上」
    # 也不是科普引用（第八十七/八十八轮）
    "电视上", "电视里", "电视说", "电视里说", "报道", "报纸上",
    "报纸说", "书上说", "书上写", "书上讲", "讲座里", "讲座中",
    "讲座上说", "讲座说", "广播里", "广播说", "收音机里", "收音机说",
)
#: 引用词之后仍属患者本人的动作/时间线索（网购药、跟视频运动等）
_EDUCATION_CURRENT_CUES = (
    "买了", "买的", "吃完", "吃了", "服用", "喝了", "跳操", "运动",
    "锻炼", "跟着", "教程", "现在", "刚刚", "刚才", "突然", "这时",
)
#: 引述引导词：其后的动作词属于被引用的科普内容，不是本人行为
#: （「网上说吃完药后胸痛要马上就医」「文章讲运动后胸痛」）
#: 「关于胸痛的文章/讲抽搐的视频」：主题引用不是患者主诉
_EDUCATION_TOPIC_REFERENCE_RE = re.compile(
    r"(?:关于|有关|讲|介绍|题为|名为)"
    r"[^，。；,.!?！？\n]{0,12}"
    r"(?:文章|视频|新闻|帖子|百科|科普|教程)"
    r"|(?:文章|视频|新闻|帖子|百科|科普|教程)"
    r"[^，。；,.!?！？\n]{0,6}"
    r"(?:里|上|中)?(?:说|讲|写|介绍|提到|看到)"
    # 「胸痛的文章/孕期出血的视频」：所有格主题引用
    # （第九十八轮 [14]）
    r"|[^，。；,.!?！？\n]{0,12}的(?:相关|有关)?"
    r"(?:文章|视频|新闻|帖子|百科|科普|教程)"
    # 「看到新闻里胸痛猝死的案例」「刷到电视上胸痛急救的科普」：
    # 引用动词在前、媒体词后接案例/科普（第九十八轮 [7]）
    r"|(?:看到|看了|刷到|刷了|读了|读到|听说|发现)"
    r"[^，。；,.!?！？\n]{0,12}"
    r"(?:新闻|电视|报道|报纸|广播|文章|视频|帖子|科普|百科)"
    r"[^，。；,.!?！？\n]{0,20}"
    r"(?:案例|科普|急救|报道|说法|研究|事故|新闻|文章|视频)"
    r"|(?:新闻|电视|报道|报纸|广播|文章|视频|帖子|科普|百科)"
    r"[^，。；,.!?！？\n]{0,20}的?案例"
)
_EDUCATION_QUOTE_LEAD_RE = re.compile(
    r"(?:网上|文章|视频|新闻|帖子|百科|教程|科普|讲座|报道|书上|"
    r"电视|广播|收音机|报纸|听说|据说)"
    r"[^，。；]{0,8}?"
    r"(?:说|讲|写|提到|指出|介绍|称|教|报道|表示|认为|"
    r"看到|刷到|见到|听说)"
)
#: 引用之后同分句「我+症状」：真实主诉被连写在同一分句里
_EDUCATION_CURRENT_RE = re.compile(
    # 「我」后的定语（我的/这个/这种）与副词可叠用：
    # 网上说我的胸痛可能是心绞痛（第一百一十一轮 [1]）
    r"我(?:(?:现在|刚刚|刚|也|正|又|今天|昨天|最近|这两天|"
    r"的|这个|这种|那个|那种)\s*){0,2}"
    r"(?:有点|有一点|有些|有|稍微|偶尔)?\s*"
    r"(?:胸痛|胸疼|头痛|头晕|呼吸困难|"
    r"咯血|咳血|抽搐|晕厥|昏迷|便血|黑便|"
    r"喘|疼|痛|发热|发烧|恶心|呕吐|心慌|胸闷)"
)
#: 引用词之前的症状与病程（「我这几天胸痛在网上查了」）：症状本身
#: 不是被引用的科普内容，不能因为同分句出现「网上/文章」就丢弃
_EDUCATION_SYMPTOM_WORDS = (
    "胸痛", "胸疼", "头晕", "头痛", "呼吸困难", "发热", "发烧",
    "咳嗽", "咯血", "恶心", "呕吐", "心慌", "胸闷", "抽搐", "疼", "痛",
)


_PATIENT_SELF_TOKEN_RE = re.compile(
    r"我(?!的?(?:爸|妈|父母|父|母|爷|奶|外公|外婆|姥姥|姥爷|哥|姐|弟|妹|"
    r"儿|女|爱人|对象|丈夫|妻子|配偶|老公|老婆|家人|亲|"
    r"家|孩|宝|娃|岳父|岳母|公公|婆婆|女婿|儿媳|孙子|孙女|"
    r"外孙|外甥|侄|叔|伯|姑|姨|舅|婶|嫂|闺女|老爷子|老太太|"
    r"爹|娘|丈母娘|老丈人|岳丈|公婆|媳妇|病人|家属))"
    r"|本人"
)


def _patient_course_after_quote(clause: str) -> bool:
    """引述词之后是否出现患者本人的红旗症状描述。

    「网上说胸痛要重视我胸口痛」：引述词后紧跟患者主语和红旗
    症状时，同句是在描述本人当前情况，不能按科普引用整句丢弃
    （第一百一十九轮 [1]）。
    """
    match = _EDUCATION_QUOTE_LEAD_RE.search(clause)
    if match is None:
        return False
    tail = clause[match.end():]
    self_match = _PATIENT_SELF_TOKEN_RE.search(tail)
    if self_match is None:
        return False
    after = tail[self_match.end():]
    return (
        _is_red_flag_text(after)
        or _has_pregnancy_bleeding(after)
        or _has_head_trauma(after)
    )


def _patient_course_before_quote(clause: str) -> bool:
    positions = [
        clause.find(word)
        for word in _EDUCATION_BLOCKERS_STRONG
        if word in clause
    ]
    if not positions:
        return False
    prefix = clause[:min(positions)]
    if (
        not any(word in prefix for word in _EDUCATION_SYMPTOM_WORDS)
        and not _is_red_flag_text(prefix)
    ):
        return False
    # 引用词之前已是完整红旗描述（含复合/伴随症状），且没有
    # 「什么是/介绍」类术语引导：直接按本人主诉保留，不能被未
    # 列入白名单的伴随症状（气短/左臂麻木）挡回。只看患者主语
    # 不够：主语也得带着症状出现在引用词之前才算本人主诉
    if _is_red_flag_text(prefix) and not (
        _DEFINITION_PREFIX_RE.search(prefix) is not None
        or re.search(
            r"什么|啥|介绍|定义|解释|概念|科普|关于|有关|一篇|讲|写|主题",
            prefix,
        )
    ):
        return True
    # 引述词之前已有实测体温（发烧38.5度看了文章说要去医院）：读数
    # 是患者本人的客观病程，引述动词只是补充说明，不能整句丢弃
    # （第一百零二轮 [3]）
    if re.search(
        r"[0-9]+(?:\.[0-9]+)?\s*(?:度|°C|℃)", prefix
    ) and any(
        word in prefix for word in ("发烧", "发热", "低烧", "高烧", "体温")
    ):
        return True
    topic_quote = re.search(r"关于|有关|一篇|讲|写|主题", prefix)
    topic_is_about_symptom = bool(
        topic_quote
        and not _is_red_flag_text(prefix[:topic_quote.start()])
        and not any(
            word in prefix[:topic_quote.start()]
            for word in _EDUCATION_SYMPTOM_WORDS
        )
    )
    if (
        not topic_is_about_symptom
        and (
            _COURSE_MARKER_RE.search(prefix) is not None
            or any(marker in prefix for marker in _COURSE_MARKERS)
            or any(word in prefix for word in _CURRENT_COURSE_CUES)
            or any(word in prefix for word in _POST_POSITIVE_CUES)
            or _EDUCATION_CURRENT_RE.search(prefix) is not None
        )
    ):
        return True
    # 「胸痛剧烈网上查了」「咯血网上查了」：引用词之前只有症状词与
    # 程度修饰（没有「什么是/介绍」等术语引导），症状本身仍是本人
    # 主诉，不能因为没写时间词就当成科普引用丢弃（第七十三轮审查）
    residue = prefix.strip(" ，,、。！？!?…—-~～")
    for word in sorted(
        _EDUCATION_SYMPTOM_WORDS
        + _CURRENT_COURSE_CUES
        + _POST_POSITIVE_CUES,
        key=len,
        reverse=True,
    ):
        residue = residue.replace(word, "")
    residue = re.sub(
        r"[的了着过是在有又也都很更最太我本人他她它们伴并且和与及无没未否认]",
        "",
        residue,
    )
    return not residue


#: 以否定开头的分句（没有胸痛/无发热/否认咯血）不建立患者语境
_NEGATED_CONTEXT_LEAD = re.compile(
    r"^\s*(?:今天|昨天|最近|目前|现在|我|本人)?\s*"
    r"(?:没有|没再|未见|未出现|否认|并非|不是|无(?!力|精神|食欲|胃口))"
)
#: 口语症状词：已保留分句需含症状样证据才建立患者语境，
#: 寒暄/称呼（早上好、医生、嗯嗯…）不能为后置定义问句背书
_COLLOQUIAL_SYMPTOM_WORDS = (
    "发麻", "麻木", "闷痛", "酸痛", "胀痛", "刺痛", "绞痛", "隐痛",
    "乏力", "没劲", "无力", "头晕", "头疼", "头痛", "恶心", "呕吐",
    "咳嗽", "气喘", "胸闷", "心慌", "腹痛", "肚子疼", "腹泻", "便秘",
    "发烧", "发热", "抽搐", "咯血", "出血", "皮疹", "水肿", "疼", "痛",
)


#: 患者状态词：术后/肿瘤治疗/慢病等分句同样说明后置定义问句在
#: 描述患者本人（「刚做完手术，抽搐是什么病」）
_PATIENT_STATE_WORDS = (
    "手术", "术后", "化疗", "放疗", "靶向", "免疫治疗", "住院", "出院",
    "糖尿病", "高血压", "肾病", "透析", "免疫抑制", "激素", "冠心病",
    "心脏病", "慢阻肺", "脑梗", "中风", "肿瘤", "癌", "怀孕", "孕期",
    "产后", "哺乳", "透析", "肝硬化", "移植",
)


def _symptom_context_clause(clause: str) -> bool:
    """已保留分句是否含症状样证据（用于建立患者语境）。"""
    text = str(clause or "")
    if not text:
        return False
    return (
        _is_red_flag_text(text)
        or _colloquial_bleeding_positive(text)
        or any(term in text for term in _NEGATED_SYMPTOM_TERMS)
        or any(word in text for word in _COLLOQUIAL_SYMPTOM_WORDS)
        or any(word in text for word in _PATIENT_STATE_WORDS)
    )


#: 严格病程时长（不含「几天」等模糊生活叙述）：定义问句的后续
#: 分句只有明确时长/频次才算患者病程线索（第九十一轮 [4]）
_STRICT_COURSE_DURATION_RE = re.compile(
    r"[0-9一二两三四五六七八九十半]+\s*"
    r"(?:个[来多]?|来|多|余)?\s*"
    r"(?:分钟|小时|钟头|天|日|周|星期|个月|月|年)"
)


def _course_context_clause(clause: str) -> bool:
    """纯病程/时间铺垫分句（最近/两天了/反复发作）建立患者语境。"""
    text = str(clause or "").strip()
    if not text or len(text) > 10:
        return False
    if _NEGATED_CONTEXT_LEAD.match(text):
        # 「今天没有胸痛」是否定铺垫，不是患者当前主诉语境
        return False
    if any(word in text for word in _COURSE_CONTEXT_BLOCKERS):
        return False
    return (
        any(marker in text for marker in _COURSE_MARKERS)
        or _COURSE_MARKER_RE.search(text) is not None
    )


#: 「还疼/还在痛/还喘」这类当前持续描述本身就是患者证据
_CONTINUOUS_SYMPTOM_CUES = (
    "还疼", "还在疼", "还在痛", "还痛", "仍然疼", "仍然痛", "仍疼",
    "一直疼", "一直痛", "还喘", "还在喘", "还在发烧", "还发烧",
    "还烧", "还在出血",
    "有点疼", "有点痛", "还有点疼", "还有点痛", "还是疼", "还是痛",
    "现在疼", "现在痛", "还在抽", "现在还在抽", "还有血", "有点血",
    "还有点血",
    # 口语严重度/躯体反应（疼得受不了/很疼/疼得直冒汗）同样是
    # 当前病程证据（第一百一十二轮 [1]）
    "受不了", "难以忍受", "很疼", "很痛", "特别疼", "特别痛",
    "好疼", "好痛", "太疼", "疼死了", "疼得直冒汗", "直冒汗",
    "疼得打滚", "很难受",
)


def _normalize_menstrual_nickname(text: str) -> str:
    """「大姨妈+期间/前后/刚走…」等月经线索归一为「月经」。

    避免家属称谓「妈」把整句当家属语境丢弃；只在明确的月经
    线索下归一（第一百一十三/一百一十四轮）。
    """
    return re.sub(
        r"大姨妈(?=[^，。；;,.!?！？\n]{0,8}?(?:期间|前后|那几天|刚走|"
        r"刚完|这几天|没(?:有)?来|未至|推迟|推后|晚了|迟了|来了))",
        "月经",
        str(text or ""),
    )


def _severity_follow_clause(text_follow: str) -> bool:
    """后续分句是否带患者本人当前病程/严重度证据。

    引述（网上说很严重/朋友说很厉害）与疑问句（会恶化吗）不升级
    定义问句；客观体征（冷汗/大汗/放射/不缓解）与生活叙述里的
    具体症状（工作的时候疼得厉害）仍是患者证据
    （第九十五轮 [6][8]、第九十六轮 [1]）。
    """
    hard_sign = any(
        sign in text_follow
        for sign in (
            "冷汗", "冒冷汗", "大汗", "虚汗",
            "放射", "不缓解", "不能缓解", "持续不缓解",
            "难忍", "剧痛", "濒死", "躺不平", "端坐呼吸",
        )
    ) or (
        # 「出汗特别厉害」这类强度形态是真实病程；裸「出汗」
        # （天气热容易出汗/没有出汗）仍不作证据
        # （第九十七轮 [14]、第九十八轮 [1]）；否定式强度
        # 出汗（没有出很多汗）同样不升级（第一百轮 [12]）
        _sweat_intensity_positive(text_follow)
    )
    return bool(
        (
            any(cue in text_follow for cue in _POST_POSITIVE_CUES)
            or any(
                cue in text_follow
                for cue in _CURRENT_COURSE_CUES
            )
            # 「出了好多汗/浑身是汗」不含字面「出汗」cue，
            # 但强度出汗本身就是患者证据（第九十九轮 [3]）
            or _sweat_intensity_positive(text_follow)
        )
        and not any(
            word in text_follow
            for word in _EDUCATION_BLOCKERS_STRONG
        )
        and _EDUCATION_QUOTE_LEAD_RE.search(text_follow) is None
        and re.search(r"[吗么嘛]\s*[？?]?\s*$", text_follow)
        is None
        and (
            hard_sign
            or "我" in text_follow
            or _is_red_flag_text(text_follow)
            or any(
                term in text_follow
                for term in _COLLOQUIAL_SYMPTOM_WORDS
            )
            # 「现在还疼/还喘」这类持续描述不需要额外症状词
            # （第一百零八轮 [2]）
            or any(
                cue in text_follow
                for cue in _CONTINUOUS_SYMPTOM_CUES
            )
        )
    )


def patient_complaint_text(text: str) -> str:
    """提取「患者主诉」部分：去掉定义/科普型问句，保留症状描述。

    “什么是咯血？”没有患者语境，不能当作急症主诉；“咯血是什么？”
    同理；而“我父亲最近咯血是什么原因？”含患者语境，必须保留并触发
    红旗。按分句处理（含逗号/顿号）：含患者语境或症状描述的分句保留，
    只有纯定义问句才整句丢弃，避免“孩子发烧了，医生说是一种病毒感染”
    这类真实主诉被整段删除。
    """
    text = _normalize_menstrual_nickname(str(text or "").strip())
    if not text:
        return ""
    # 顿号不是分句边界：detect_red_flags 的枚举否定（「无发热、
    # 咳嗽、胸痛」）依赖整组顿号在同一分句内，不能在此拆开重拼
    clauses = re.split(r"[。；;！!？?\n，,]", text)
    kept = []
    context_seen = False
    symptom_context_seen = False
    for position, clause in enumerate(clauses):
        # 求助/病程线索可能隔着一两个中间分句（「胸痛是什么病，
        # 现在还疼，需要去医院吗」）：扫描全部后续分句而不是只看
        # 紧邻一句（第一百零八轮 [2]）。家属主语分句与其紧接的
        # 无主语句（我爸咳嗽，需要去医院吗）不算患者证据
        # （第一百零九轮 [14]）
        follow_clauses = [
            follow.strip() for follow in clauses[position + 1:]
            if follow.strip()
        ]
        patient_follows = []
        for idx, text_follow in enumerate(follow_clauses):
            if _PATIENT_FAMILY_SUBJECT_RE.search(text_follow):
                continue
            if idx > 0 and (
                _PATIENT_FAMILY_SUBJECT_RE.search(
                    follow_clauses[idx - 1]
                )
                and "我" not in text_follow
                and "本人" not in text_follow
            ):
                # 只有「家属的非急性症状描述」（我爸咳嗽）才把紧接的
                # 无主语句视为延续家属话题；家属只是旁观担心
                # （我女儿很担心）或家属本身是急症（我儿子抽了五分钟）
                # 时，求助续句仍算患者本人语境（第一百一十轮 [2][5]）
                prev_clause = follow_clauses[idx - 1]
                prev_symptomatic = any(
                    term in prev_clause
                    for term in _COLLOQUIAL_SYMPTOM_WORDS
                ) or _is_red_flag_text(prev_clause)
                if prev_symptomatic and not _is_red_flag_text(
                    prev_clause
                ):
                    continue
            patient_follows.append(text_follow)
        help_follows = any(
            _URGENT_HELP_RE.search(text_follow) is not None
            for text_follow in patient_follows
        )
        course_follows = any(
            _URGENT_HELP_RE.search(text_follow) is not None
            or _course_context_clause(text_follow)
            for text_follow in patient_follows
        )
        # 「胸痛是什么病啊，痛得我直冒冷汗」：后续带严重度/当前
        # 病程线索的分句说明前一句是真实主诉而非纯定义问句
        # （第九十四轮 [1]）
        severity_follows = any(
            _severity_follow_clause(text_follow)
            for text_follow in patient_follows
        )
        cleaned = _clean_complaint_clause(
            clause.strip(), context_seen, help_follows,
            course_follows or severity_follows,
            symptom_context_seen,
        )
        if cleaned:
            kept.append(cleaned)
            if _symptom_context_clause(clause) or any(
                _positive_mention(clause, phrase)
                for phrase, _label in RED_FLAG_PHRASES
            ):
                symptom_context_seen = True
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
            pregnancy_seen = (
                any(term in clause for term in PREGNANCY_TERMS)
                or PREGNANCY_WEEK_PATTERN.search(clause) is not None
            )
            if (
                subject_seen or positive_flag or symptom_seen
                or pregnancy_seen
            ):
                # 已有患者本人的症状/妊娠分句：后续定义问句按新症状
                # 保留。「今天想问问」「没有胸痛」这类铺垫/否定分句
                # 不算语境；「我在网上查了资料」里的「我」是检索者
                if (
                    positive_flag
                    or symptom_seen
                    or pregnancy_seen
                    or (
                        not any(
                            word in clause
                            for word in _EDUCATION_BLOCKERS_STRONG
                        )
                        and _NEGATED_CONTEXT_LEAD.match(clause) is None
                    )
                ):
                    context_seen = True
            elif _course_context_clause(clause):
                # 纯病程/时间铺垫（「最近」「两天了」）同样说明后置
                # 定义问句在描述患者本人；查资料/问问等科普铺垫除外
                context_seen = True
            elif (
                len(clause.strip()) >= 2
                and not any(
                    word in clause for word in _EDUCATION_BLOCKERS_STRONG
                )
                and not any(
                    word in clause for word in _COURSE_CONTEXT_BLOCKERS
                )
                and _NEGATED_CONTEXT_LEAD.match(clause) is None
                and _symptom_context_clause(clause)
            ):
                # 词表外的口语症状（「手脚发麻」「胸口闷痛」）同样是
                # 真实主诉：已保留的普通分句也建立患者语境，
                # 「手脚发麻，说话不清是什么病」不能只留下前半句。
                # 「没有胸痛，抽搐是什么病」这类否定铺垫除外
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
        # 既往史/系统回顾里的慢病枚举同样可以否定后续症状
        # （第一百一十七轮 [7]）
        "高血压", "糖尿病", "冠心病", "脑梗", "肝病", "肾病",
        "胃病", "哮喘", "结核",
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
    # 常见免疫抑制/长期激素药名（结构化用药清单里写的是药名而不是
    # 「激素」二字时同样要触发免疫抑制伴发热规则，第八十六轮）
    "硫唑嘌呤", "环磷酰胺", "来氟米特", "阿达木单抗", "英夫利西单抗",
    "依那西普", "托法替布",
    # 英文表述（输入在 _active_medical_text 中已转小写）
    "chemotherapy", "chemo", "immunosuppression",
    "immunosuppressed", "organ transplant", "stem cell transplant",
    "methotrexate", "cyclosporine", "tacrolimus", "mycophenolate",
    "azathioprine", "cyclophosphamide", "leflunomide", "adalimumab",
    "biologic",
) + tuple(
    str(alias).lower()
    for _group in ("corticosteroids", "azathioprine")
    for _canonical, _aliases in _DRUG_GROUPS.get(_group, ())
    for alias in _aliases
)
ANTICOAGULANT_TERMS = (
    "华法林", "利伐沙班", "阿哌沙班", "达比加群", "艾多沙班",
    "肝素", "低分子肝素", "抗凝",
    # 英文药名（输入在 _active_medical_text 中已转小写）
    "warfarin", "rivaroxaban", "apixaban", "dabigatran",
    "edoxaban", "heparin", "enoxaparin", "anticoagulant",
    # 国内常用 DOAC 商品名（拜瑞妥/泰毕全/艾乐妥/里先安）
    "拜瑞妥", "泰毕全", "艾乐妥", "里先安",
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
    "头上磕了个包", "头皮血肿", "头部血肿", "头皮下血肿",
    "头流血", "头上流血", "头出血", "头上出血",
    # 部位词+出血/缝针同样是头部外伤证据（第一百零八轮 [1]）
    "后脑勺流血", "后脑勺出血", "后脑勺渗血", "后脑勺缝",
    "头顶流血", "头顶出血", "头顶渗血", "头顶缝",
    "太阳穴流血", "太阳穴出血", "太阳穴缝",
    "脑门流血", "脑门出血", "额头流血", "额头出血", "额头缝",
    "前额流血", "前额出血", "枕部流血", "枕部出血",
    "脑袋流血", "脑袋出血",
    # 头皮破裂/流血同样是头部外伤（第一百零一轮 [3]）
    "头皮破", "头皮流血", "头皮出血", "头皮裂", "头皮擦破",
    "头皮摔破", "头皮摔裂",
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
    # 中间插入物体（头被门撞了）与「额头/头顶+磕撞」的常见语序
    "脑袋被", "额头被", "头顶被", "前额被", "后脑勺被",
    "枕部被", "额头磕", "额头撞", "额头碰", "头顶磕", "头顶撞",
    "头顶碰", "前额磕", "前额撞", "前额碰", "磕到了脑袋",
    "撞到了脑袋", "碰到了脑袋", "磕着脑袋", "撞着脑袋",
    "头磕了个包", "脑袋磕了个包",
    # 标准写法与「砸伤/撞伤」语序（含中间插入物体由正则覆盖）
    "头外伤", "砸到头", "砸到头部", "头部砸到", "磕伤头部",
    "撞伤头部", "撞伤了头", "砸伤了头", "头砸伤", "头撞伤",
    # 跌倒/摔伤语序（后脑勺摔到地上/摔到后脑勺/头摔破）
    "后脑勺摔", "摔到后脑勺", "摔了后脑勺", "头摔破", "摔破头",
    "头摔伤", "摔伤了头", "后脑勺着地", "头着地",
    "头先着地", "额头先着地", "头先撞", "头先磕", "头朝下着地",
    "头先碰", "额头先撞", "额头先磕",
)

#: 头部外伤的复合语序：部位词 + 撞击/外伤动词（可含「被…撞」），
#: 以及反向的「撞到/砸到/磕到头」。排除「脚趾头/枕头/骨头/馒头」
#: 等以「头」结尾的非头部名词，避免裸子串误报。
_HEAD_NON_HEAD_PREFIX = (
    "趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐"
)
_HEAD_TRAUMA_RE = re.compile(
    r"(?:(?<![趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐])头(?:部)?|脑袋|额头|前额|头顶|"
    r"后脑勺|枕部|脑门|脑壳|太阳穴)"
    # 口语方位/副词：后脑勺上磕了个包、头不小心撞到墙、
    # 头一下子撞到门框（部位与撞击动词之间可插入修饰）。
    # 揣测词（好像/可能/似乎/估计/多半/应该是）按阳性处理：
    # 「头好像撞到了」是可能的头部撞击，抗凝状态需急诊评估
    # （第一百轮 [2]）
    r"(?:上|部|后面|后面上)?"
    # 插入修饰可叠用：头也还在撞到/都在流血（第一百一十轮 [6]）
    r"(?:(?:一不小心|不小心|一下子|突然|直接|先|重重地?|猛地|猛一?下|"
    r"当时|今天|昨天|前几天|好像|似乎|可能|估计|多半|应该是?|怕是|"
    # 常见副词/系词插入：头也撞到了/头都撞出血/头真的撞到/还在流血
    # （第一百零九轮 [1][4]）
    r"还在|一直在|一直|不停地|不停|仍然|仍|也|都|真的|确实|是|"
    r"一下|又|还|在|"
    r"疑似|不确定|不知道)\s*){0,3}"
    r"(?:外伤|受伤|撞伤|砸伤|磕伤|摔伤|摔破|撞|磕|碰|砸|摔|"
    r"伤|着地|破|"
    # 部位词直接跟伤口/出血动词同样是头部外伤：后脑勺流血了/
    # 头顶出血/后脑勺缝了三针（第一百零八轮 [1]）；持续出血与
    # 量化出血（还在流血/流了点血/出了点血/流了一点血）同口径
    # （第一百零九轮 [4]、第一百一十轮 [6]）
    r"流了?(?:一?点|一?些|几滴|不少|很多|好多)?\s*血(?:不止)?|"
    r"出了?(?:一?点|一?些|不少|很多|好多)?\s*血|"
    r"出血(?:不止)?|渗血|"
    r"缝(?:了)?\s*[0-9一二两三四五六七八九十]{0,3}\s*针|缝针|缝了?几针|"
    r"(?:受到?|遭到?)[^，。；,.!?！？\n]{0,4}撞击|"
    r"被[^，。；,.!?！？\n]{0,6}(?:撞|磕|碰|砸|打|夹))"
    r"|头皮(?:上)?(?:磕破|碰破|撞破|磕|撞|碰|破|流血|出血|裂|擦破)"
    r"|(?:撞|磕|碰|砸|打|摔)(?:在|到)(?:了)?"
    r"(?:我|他|她|患者|宝宝|孩子|本人)?(?:的)?"
    r"(?:(?<![趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐])头(?:部)?|脑袋|额头|前额|"
    r"头顶|后脑勺|枕部|脑门|脑壳|太阳穴)"
    r"|(?<![趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐])"
    r"(?:头(?:部)?|脑袋|额头|前额|头顶|后脑勺|枕部|脑门|脑壳|太阳穴)"
    r"在[^，。；,.!?！？\n]{0,6}上?(?:撞|磕|碰|砸)"
    r"|(?:撞|磕|碰|砸|摔)(?:到|了|着|伤|破)?了?"
    r"(?:(?<![趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐])头(?:部)?|脑袋|额头|前额|"
    r"头顶|后脑勺|枕部)"
)


#: 头部起包/血肿的语序（「头上起了个大包」没有撞击动词，但同样
#: 是头部外伤证据）；裸「头」部位同样适用（头起了个大包，
#: 第一百一十轮 [8]），仍排除 脚趾头/枕头/骨头/乳头/鼻头/龟头
#: 等非头部名词（第一百一十二轮 [15]）
_HEAD_SWELLING_RE = re.compile(
    r"(?:(?<![趾指骨枕馒拳舌木砖石车脚乳鼻龟眉肩膝腕踝奶榔斧锄镐])头(?:上|部)?|"
    r"头皮|脑袋上?|额头|头顶|后脑勺)"
    r"(?:(?:起了?|肿了?|鼓了?)(?:个|一个)?(?:大)?(?:包|肿包|血肿)"
    r"|有(?:个|一个)?(?:包|肿包|血肿))"
    r"|(?:头皮|头部|头皮下)血肿"
)


def _has_head_trauma(text: str) -> bool:
    for term in HEAD_TRAUMA_TERMS:
        start = text.find(term)
        while start != -1:
            if not (
                term.startswith("头")
                and start > 0
                and text[start - 1] in _HEAD_NON_HEAD_PREFIX
            ):
                return True
            start = text.find(term, start + len(term))
    return (
        _HEAD_TRAUMA_RE.search(text) is not None
        or _HEAD_SWELLING_RE.search(text) is not None
    )


#: 患者在家属用药分句之后的续用表述。必须核对宾语：只有同一
#: 药物/过敏原、无宾语或代词宾语才算续用，「我也在吃维生素C」
#: 「我也有一只猫」「我也有高血压」不能把家属的药名算到患者头上
_CONTINUATION_USE_RE = re.compile(
    r"我[^，。；;?？]{0,6}?"
    r"(?:也|同样|都|跟他(?:们)?(?:一样|一起)?|跟她(?:们)?(?:一样|一起)?|"
    r"和他(?:们)?(?:一样|一起)?|和她(?:们)?(?:一样|一起)?|"
    r"跟他们一样|跟她们一样)"
    r"[^，。；;?？]{0,8}?(?:在|还|一直)?\s*"
    r"(?:吃|服用|服|用)([^，。；;?？]{0,16})"
)
_CONTINUATION_ALLERGY_RE = re.compile(
    r"我[^，。；;?？]{0,6}?"
    r"(?:也|同样|都|跟他(?:们)?(?:一样|一起)?|跟她(?:们)?(?:一样|一起)?|"
    r"和他(?:们)?(?:一样|一起)?|和她(?:们)?(?:一样|一起)?|"
    r"跟他们一样|跟她们一样)"
    r"[^，。；;?？]{0,12}?(?:对)?([^，。；;?？]{0,12})过敏"
)
#: 跨分句否定：「我也没在吃/我最近不吃/我也没服用」不是患者续用
_CONTINUATION_NEGATED_RE = re.compile(
    r"(?:没|没有|未|不再|没再|别|不)\s*"
    r"(?:敢|想|愿|愿意|能|会|要)?\s*(?:在|再|继续)?\s*"
    r"(?:吃|服用|服|用|过敏)"
)
_CONTINUATION_SAME_RE = re.compile(
    r"我(?:也|跟他|跟她|跟他们|和他|和她|和他们)?一样"
    r"|我也是(?:[，。；;?？]|$)"
)
_PRONOUN_OBJECTS = (
    "这个药", "这种药", "那个药", "那种药", "一样的药",
    "同一种药", "同样的药", "相同的药", "同一种", "同样的", "相同的",
    "这个", "那个", "它",
)


def continuation_object_matches(obj: str, clause: str) -> bool:
    """续用宾语是否指向前一分句中的同一药物/过敏原。"""
    obj = str(obj or "").strip(" 　")
    # 「我也在吃了/我也在吃药」：剥离助词与数量词后无宾语或只剩
    # 泛指名词，都是对前药的指代
    obj = re.sub(r"(?:了|啦|着|过|呢|啊|呀|吧|的)+$", "", obj)
    obj = re.sub(r"^[一两二三四五六七八九十几半]+[片粒袋包支瓶]", "", obj)
    obj = obj.strip(" 　")
    if (
        not obj
        or obj in ("药", "药物", "这种", "那种", "这些", "那些")
        or any(word in obj for word in _PRONOUN_OBJECTS)
    ):
        return True
    tokens = {obj[i:i + 2] for i in range(len(obj) - 1)}
    return any(token in clause for token in tokens if len(token) == 2)


def continuation_same_as_clause(follow: str, clause: str) -> bool:
    """续用分句是否描述患者本人的同一药物/过敏原。"""
    if _CONTINUATION_NEGATED_RE.search(follow):
        return False
    # 「我妈也吃/我岳母也在吃/我家孩子也吃」：家属做主语，
    # 不是患者本人的续用。复用 _FAMILY_HISTORY_MARKERS，避免漏称谓
    if _PATIENT_FAMILY_SUBJECT_RE.match(follow):
        return False
    if _CONTINUATION_SAME_RE.search(follow):
        return True
    for pattern in (_CONTINUATION_USE_RE, _CONTINUATION_ALLERGY_RE):
        match = pattern.search(follow)
        if match and continuation_object_matches(match.group(1), clause):
            return True
    return False


def _patient_continuation_ahead(
    clauses: list, index: int, clause: str
) -> bool:
    """家属分句之后 1-2 句是否为患者本人的同一药物续用/过敏。"""
    for follow in clauses[index + 1:index + 3]:
        follow = str(follow or "").strip()
        if not follow:
            continue
        if continuation_same_as_clause(follow, clause):
            return True
    return False


#: 家族史/他人病史不能当作患者本人的当前状态
_FAMILY_HISTORY_MARKERS = (
    "父亲", "母亲", "父母", "家人", "家族", "爷爷", "奶奶", "外公", "外婆",
    "丈夫", "妻子", "配偶", "哥哥", "姐姐", "弟弟", "妹妹", "儿子", "女儿",
    # 口语称谓：家人服药不能算患者本人抗凝
    "爸爸", "妈妈", "爸", "妈", "老公", "老婆", "爱人", "老伴",
    "岳父", "岳母", "公公", "婆婆", "女婿", "儿媳", "姥姥", "姥爷",
    "闺女",
    # 裸家属称谓与 triage 用药口径保持一致（第一百一十五轮 [12]）：
    # 家属的药不能算患者本人抗凝/免疫抑制
    "媳妇", "媳妇儿", "丈母娘", "老丈人", "岳丈", "公婆",
    "孩子", "小孩", "娃", "大宝", "二宝", "爹", "娘",
    "叔叔", "阿姨", "舅舅", "姑姑",
    "家里老人", "家中老人", "家里长辈",
    # 主语位置的家属表述（成人档案下不把家属用药算到患者头上）
    "家里人", "我家孩子", "我家宝宝", "我家娃", "我家老人",
    "我孩子", "我宝宝", "我娃",
)

#: 「我爸也吃/我岳母也在吃/我家孩子也吃」——家属做主语
_FAMILY_SUBJECT_NAMES = "|".join(
    re.escape(marker)
    for marker in sorted(_FAMILY_HISTORY_MARKERS, key=len, reverse=True)
)
_PATIENT_FAMILY_SUBJECT_RE = re.compile(
    r"我(?:的)?(?:"
    r"家(?:里)?(?:的)?(?:人|孩子|宝宝|娃|老人|"
    + _FAMILY_SUBJECT_NAMES
    + r")|"
    + _FAMILY_SUBJECT_NAMES
    + r")"
)

#: 已经结束、停用、否认或仅为过敏的病史/用药不属于当前高危状态
_CONTEXT_INACTIVE_MARKERS = (
    "已结束", "结束后", "已停", "停用", "停药", "停了", "停的", "已完成",
    # 停服/停止服用与停用同义，远期停药同样退出当前用药状态
    # （第一百零三轮 [5]）
    "停服", "停止服用",
    "不再", "未使用", "没有使用", "未服用", "没有服用", "未吃", "没有吃",
    "没吃", "不吃", "不吃了", "不再吃", "不用吃", "无需吃", "没再吃", "停掉", "停掉了",
    "否认", "未曾", "从未", "过敏史", "过敏原", "过敏反应",
)

#: 「不吃辛辣/不吃菠菜」是饮食限制，不影响同分句药名的在用状态
_DIET_RESTRICTION_RE = re.compile(
    r"不吃(?:辛辣|辣|海鲜|菠菜|柚子|葡萄柚|生冷|油腻|刺激性?|"
    r"高[钾磷]|含[钾磷]|酒|咖啡|浓茶|牛奶|绿叶菜|豆制品|动物内脏)"
)

#: 双重否定表示仍在用药：「不得不吃华法林/不能不吃华法林」
_TRIAGE_DOUBLE_NEGATION = re.compile(
    r"(?:不得不|不能不|没法不|无法不|忍不住|不是不|并非不|"
    r"(?:不是|并非|并不是)(?:没|没有|未))"
    r"(?:再|继续)?(?:吃|服用|服|用|口服|注射|打|喝|饮)?"
)
#: 「药还没吃完/没有吃完」只是说明剩余药量：仍在用药
_FINISHED_DOSE_RE = re.compile(
    r"(?:还|仍|尚未|没有|没|未)?\s*没(?:有)?\s*吃(?:完|够|满)|"
    r"没(?:有)?\s*(?:服用|服)完"
)
#: 单次漏服（今天没吃/漏服了一次/N 天没吃）仍处于用药/抗凝状态
_DOSE_MISSED_PATTERN = re.compile(
    r"(?:今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|前天|"
    r"前晚|刚才|这次|这一顿|这顿|"
    r"[0-9一二两三四五六七八九十]+(?:天|日)来?|几天|数天|两三天|"
    r"[0-9一二两三四五六七八九十]+周来?|几周)"
    r"[^，。；,.!?！？\n]{0,8}"
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|没服|未服|没有服|"
    r"没口服|漏服用?|忘(?:记)?服用?|少吃)"
)
#: 宾语前置的近期漏服（没吃华法林三天了/未服用华法林两周）：时长
#: 落在否定动词之后，仍是「仍在用药、抗凝状态成立」（第九十四轮 [7]）。
#: 中间片段是漏服宾语，用于核对漏的到底是不是本行药（第九十五轮 [16]）
_DOSE_MISSED_OBJECT_FIRST_RE = re.compile(
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|没服|未服|没有服|"
    r"没口服|漏服用?|忘(?:记)?服用?)"
    r"([^，。；,.!?！？\n]{0,12}?)"
    r"[0-9一二两三四五六七八九十]+(?:天|日|周)"
)


def _dose_missed_object_first(text: str) -> str:
    """漏服动词之后的宾语片段（没吃华法林三天了→华法林）。"""
    match = _DOSE_MISSED_OBJECT_FIRST_RE.search(str(text or ""))
    if match is None:
        return ""
    return (match.group(1) or "").strip()


def _dose_missed_object_before(text: str) -> str:
    """漏服动词之前的主体（阿司匹林没吃三天了→阿司匹林）。"""
    text = str(text or "")
    for match in _DOSE_MISSED_OBJECT_FIRST_RE.finditer(text):
        last = re.split(r"[，。；、但而]", text[: match.start()])[-1]
        last = re.sub(
            r"(?:已经|已|都|快|近|将近|有|已有)?\s*"
            r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:多)?"
            r"(?:天|日|周|个月|月|年))(?:了|多)?$",
            "",
            last,
        ).strip()
        last = re.sub(r"(?:了|啦|啊|呀)$", "", last).strip()
        candidate = re.search(
            r"([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})$", last
        )
        if candidate is None:
            continue
        obj = re.sub(
            r"^(?:也|都|还|仍|和|及|与|但|而)+", "",
            candidate.group(1),
        ).strip()
        if len(obj) >= 2:
            return obj
    return ""


def _missed_dose_names_high_risk_drug(text: str) -> bool:
    """宾语前置漏服点名的是否抗凝/免疫抑制等高风险药。"""
    obj = (
        _dose_missed_object_first(text)
        or _dose_missed_object_before(text)
    )
    if not obj:
        return True
    return (
        any(term in obj for term in ANTICOAGULANT_TERMS)
        or any(term in obj for term in IMMUNOSUPPRESSION_TERMS)
    )


def _missed_dose_names_term(text: str, term: str) -> bool:
    """宾语前置漏服的对象是否就是 term 这个药。"""
    obj = (
        _dose_missed_object_first(text)
        or _dose_missed_object_before(text)
    )
    if not obj or not term:
        return False
    return term in obj or obj in term

#: 时间前置漏服、宾語在动词之后（今天没吃华法林）
_DOSE_MISSED_TIME_OBJ_AFTER_RE = re.compile(
    r"(?P<time>今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|"
    r"前天|前晚|刚才|这次|这一顿|这顿|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?|几天|数天|两三天|几周)"
    r"[^，。；,.!?！？\n]{0,8}?"
    r"(?P<verb>没吃|未吃|没有吃|没服用|未服用|没有服用|没服|未服|漏服用?)"
    r"\s*(?P<obj>[一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})"
)
#: 时间前置漏服、宾語在时间词之前（华法林今天没吃）
_DOSE_MISSED_TIME_OBJ_BEFORE_RE = re.compile(
    r"(?P<obj>[一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*(?:也|都|还|仍)?\s*"
    r"(?P<time>今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|"
    r"前天|前晚|刚才|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?)"
    r"[^，。；,.!?！？\n]{0,4}?"
    r"(?P<verb>没吃|未吃|没有吃|没服用|未服用|没有服用|漏服用?)"
)


#: 漏服宾语的首尾语气/连接词：宾语两侧的词不是药名
_DOSE_OBJ_EDGE_RE = re.compile(r"^[了啦啊呀但而和及与或的着\s]+|[了啦啊呀但而和及与或的着\s]+$")
#: 纯代词/副词宾语（「我已经」「都」）：整项只是主语或助词
#: 而不是药名。不能做裸子串过滤：他汀/他克莫司/他达拉非等药名
#: 本身含「他」（第九十九轮 [2][5]）
_DOSE_OBJ_PURE_STOP_RE = re.compile(
    r"^(?:(?:我们|我|你|他|她|本人|已经|已|还|也|都|仍|刚|才|就|又|再)"
    r"\s*)+$"
)


def _clean_missed_dose_obj(raw: str) -> str:
    items = _missed_dose_obj_items(raw)
    return items[0] if items else ""


def _missed_dose_obj_items(raw: str) -> list:
    """漏服宾语的药名项：转折词取末项，并列词逐项拆分。

    「停用两年了但利伐沙班」→[利伐沙班]；「华法林和二甲双胍」
    →[华法林, 二甲双胍]（第九十八轮 [5]）。
    """
    raw = str(raw or "").strip()
    if any(word in raw for word in ("但", "而")):
        raw = re.split(r"[但而]", raw)[-1]
    items = []
    for piece in re.split(r"[和及与或]", raw):
        obj = _DOSE_OBJ_EDGE_RE.sub("", piece.strip())
        if len(obj) < 2:
            continue
        if _DOSE_OBJ_PURE_STOP_RE.match(obj):
            continue
        if any(
            word in obj
            for word in (
                "停用", "停药", "停了", "停的", "没吃", "未吃", "漏服",
            )
        ):
            continue
        items.append(obj)
    return items


#: 时间词在主语之前的漏服（今天华法林没吃）
_DOSE_MISSED_TIME_LEAD_RE = re.compile(
    r"(?P<time>今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|"
    r"前天|前晚|刚才|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?)"
    r"\s*(?P<obj>[一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*"
    r"(?:也|都|还|仍)?\s*"
    r"(?P<verb>没吃|未吃|没有吃|没服用|未服用|没有服用|漏服用?)"
)


#: 纯时间词宾语：「今天早上/昨天晚上」被拆分出的「今天」不是药名，
#: 也不是低风险药，不能据此把整句按低风险漏服丢弃（第九十九轮 [6]）
_MISSED_DOSE_TIME_TOKEN_RE = re.compile(
    r"^(?:今天|今早|今晨|今日|昨天|昨晚|昨夜|前天|前晚|刚才|刚刚|"
    r"现在|最近|这几天|这两天|那天|这天|早上|上午|中午|下午|晚上|"
    r"今晚|晚|早|这次|这一顿|这顿|"
    r"[0-9一二两三四五六七八九十]+天来?|几天|数天|两三天|"
    r"[0-9一二两三四五六七八九十]+周来?|几周)$"
)


def _missed_dose_time_first_fragment(text: str) -> str:
    """时间前置漏服的高风险药片段（华法林今天没吃→华法林今天没吃）。

    宾语在时间词之前的形态优先（「二甲双胍今天没吃」指向二甲双胍）；
    该形态指向非高风险药时整句不保留，避免把句尾他药误绑给漏服。
    """
    text = str(text or "")

    def high_risk(obj: str) -> bool:
        return any(
            term in obj for term in ANTICOAGULANT_TERMS
        ) or any(term in obj for term in IMMUNOSUPPRESSION_TERMS)

    for match in _DOSE_MISSED_TIME_OBJ_BEFORE_RE.finditer(text):
        items = _missed_dose_obj_items(match.group("obj"))
        if not items:
            continue
        for obj in items:
            if high_risk(obj):
                return f"{obj}{match.group('time')}{match.group('verb')}"
        if all(_MISSED_DOSE_TIME_TOKEN_RE.match(obj) for obj in items):
            # 宾语只是时间词拆分（「今天早上」→今天+早上）：继续
            # 尝试宾语在动词后的形态，不能整句丢弃（第九十九轮 [6]）
            continue
        # 宾语在时间词之前且全是非高风险药：不保留本句，
        # 避免把句尾他药误绑给漏服（第九十八轮 [5]）
        return ""
    for match in _DOSE_MISSED_TIME_OBJ_AFTER_RE.finditer(text):
        items = _missed_dose_obj_items(match.group("obj"))
        if not items:
            continue
        for obj in items:
            if high_risk(obj):
                return f"{obj}{match.group('time')}{match.group('verb')}"
        return ""
    for match in _DOSE_MISSED_TIME_LEAD_RE.finditer(text):
        items = _missed_dose_obj_items(match.group("obj"))
        for obj in items:
            if high_risk(obj):
                return f"{obj}{match.group('time')}{match.group('verb')}"
    return ""


def _missed_dose_high_risk_fragment(text: str) -> str:
    """同分句有远期停药时，只保留漏服高风险药的片段。

    「甲氨蝶呤停用两年了但华法林没吃三天了」→「华法林没吃三天」：
    漏服对象是高风险药时保留其用药状态，同时剔除同句远期停药的
    他药；漏服对象不是高风险药时返回空串（整句丢弃）。时间前置
    形态（今天没吃华法林/华法林今天没吃）同样按对象归属
    （第九十七轮 [1][7]）。
    """
    text = str(text or "")
    match = _DOSE_MISSED_OBJECT_FIRST_RE.search(text)
    if match is None:
        return _missed_dose_time_first_fragment(text)
    obj = (match.group(1) or "").strip()
    if not obj:
        obj = _dose_missed_object_before(text)
    if not obj:
        return _missed_dose_time_first_fragment(text)
    if not (
        any(term in obj for term in ANTICOAGULANT_TERMS)
        or any(term in obj for term in IMMUNOSUPPRESSION_TERMS)
    ):
        return ""
    tail = text[match.start(): match.end()]
    if obj in tail:
        tail = tail.replace(obj, "", 1)
    return f"{obj}{tail}".strip(" ，,、但而")
#: 未来停药计划（明天就停/下周就不吃了/等这盒吃完就停）：当前仍在用药
_FUTURE_STOP_PATTERN = re.compile(
    r"(?:明天|后天|下周|下个?月|下星期|明年|月底|年底|"
    r"过[0-9一二两三四五六七八九十]+(?:天|周|个月|月|年)|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周|个月|月|年)\s*(?:后|以后)|"
    r"等[^，。；]{0,12}(?:就|再))"
    r"[^，。；]{0,6}"
    r"(?:停用|停药|停服|停了|停掉|停止服用|不吃|不再吃|不用吃|不服|"
    r"不喝|不服用|不再服用|不用服用|不再服|不口服|"
    r"不做化疗|不化疗|不再化疗|停止化疗|暂停化疗)"
)
#: 「未停用/不能停药」表示仍在用药，不能按“已停用”过滤
_NEGATED_STOP_PATTERN = re.compile(
    r"(?:未|没|没有|不能|不可|不宜|不让|不应|不要|别|无需|不必|"
    r"并非|不想|不打算|不准备)"
    r"[^，。；]{0,4}(?:停用|停药|停服|停了|停止服用|停过|间断|中断)"
)
#: 「打算/准备停用华法林」「打算不吃华法林」都是意图，当前仍在用药
_STOP_INTENT_PATTERN = re.compile(
    r"(?:想|打算|准备|计划|考虑|要不要|是否)"
    r"[^，。；]{0,6}"
    r"(?:停用|停药|停服|停了|停止服用|不吃|不再吃|不用吃|不服|不喝|"
    r"不服用|不再服用|不用服用|不再服|不口服|"
    r"不做化疗|不化疗|不再化疗|停止化疗|暂停化疗)"
)
#: 「华法林停用可以吗」是询问，不能按已停药处理
_STOP_QUESTION_PATTERN = re.compile(
    r"(?:停用|停药|停服|停了)[^，。；]{0,4}"
    r"(?:可以吗|行吗|好吗|行不行|可以么|是否|吗)"
)
#: 换药/续用线索：同一分句里「停用旧药改用新药」仍是当前用药
_SWITCH_MEDICATION_PATTERN = re.compile(
    # 只保留真正的换药表述；续用词（还在吃/仍在服用…）交由
    # _restart_names_other_medication 归属判定（第九十三轮 [13]）
    r"(?:改用|换成|改吃|换用|改为|重新吃|又吃上|恢复服用)"
)
#: 近期停药仍有抗凝作用：「停用两天/昨天停药/刚停用」按当前用药处理
_RECENT_STOP_PATTERN = re.compile(
    r"(?:停用|停药|停了|停掉|停掉了)[^，。；;,.!?！？\n]{0,3}"
    r"(?:今天|昨天|前天|[0-9一二两三四五六七八九十]+天|几天|"
    r"一周|两周)"
    r"|(?:刚刚?|今天|昨天|前天|前几天|前几周|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前)"
    r"[^，。；;,.!?！？\n]{0,4}(?:已|刚|才)?\s*"
    r"(?:停用|停药|停了|停掉|停掉了|停服|停止服用)"
)
#: 近期「开始不吃…了」同样是近期停药（停药 1–2 天内 INR 仍偏高）：
#: 「昨天开始不吃华法林了」不能整句丢弃。裸「不吃了」仍按已停处理
_RECENT_NO_EAT_PATTERN = re.compile(
    r"(?:今天|昨天|前天|刚才|刚刚|今早|昨晚|凌晨|前几天|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前|"
    r"[0-9一二两三四五六七八九十]+\s*天)"
    r"[^，。；;,.!?！？\n]{0,6}?(?:开始|就|才|已经?)?\s*"
    r"(?:不吃|不吃了|不再吃|不再服用)"
)
#: 明确远期停药（月/年量级）才按已结束处理；裸「停用/停了」保守
#: 视为仍有抗凝作用（停药 1–2 天内 INR 仍可能偏高）
_LONG_STOP_PATTERN = re.compile(
    r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|"
    # 裸「停」：紧跟时长（停三年了）或「停+药名+时长」（停华法林
    # 两年了）（第一百零五轮 [8]）；「停经两个月」是月经史，
    # 「停车/停机/停工」等非药物宾语也不是停药（第九十四轮 [6]）
    r"停(?!(?:经|机|工|课|业|车|电|水|职|赛|战|学|产|航|手|留|靠|放|歇)))"
    r"[^，。；;,.!?！？\n]{0,6}"
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|年)|多月|多年|"
    r"半年|去年|前年|上年|上个月|上月|很久|好久)"
    r"|(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|年)|半年|多年|"
    r"去年|前年|上年|上个月|上月|很久|好久)"
    r"\s*(?:前|以来)?[^，。；;,.!?！？\n]{0,4}"
    r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用)"
    # 英文停药表述（warfarin was stopped 2 years ago / no longer
    # taking / discontinued ... for years）
    r"|(?:stopped|discontinued|ceased|quit|been\s+off)\s+"
    r"(?:taking\s+|using\s+)?"
    r"[^,.;!?\n]{0,24}?"
    r"(?:(?:[0-9]+|a|one|two|three|four|five|several|many|few)"
    r"\s+(?:years?|months?))\s*"
    r"(?:ago|for)?"
    r"|(?:not\s+(?:on|taking)|no\s+longer\s+(?:taking|on))"
)
#: 英文远期停药分句（warfarin was stopped 2 years ago）与重启线索
_ENGLISH_STOP_RE = re.compile(
    r"\b(?:stopped|discontinued|ceased|quit|"
    r"no\s+longer\s+(?:taking|on)|not\s+(?:on|taking))\b",
    re.IGNORECASE,
)
#: 英文已知抗凝/免疫抑制药名（用于停药/续用的宾语归属判定）
_ENGLISH_KNOWN_DRUG_TERMS = tuple(
    term.lower()
    for term in ANTICOAGULANT_TERMS + IMMUNOSUPPRESSION_TERMS
    if term.isascii()
)
#: 「have been off warfarin for two years」：been off 必须指向已知
#: 药名；「been off work/alcohol」不是停药。限定词（off my warfarin）
#: 同样接受（第九十/九十一轮）
_ENGLISH_BEEN_OFF_RE = re.compile(
    r"\b(?:been|have\s+been|has\s+been|had\s+been)\s+off\s+"
    r"(?:of\s+)?(?:(?:my|his|her|their|the)\s+)?"
    r"([A-Za-z][A-Za-z\-]{2,})\b",
    re.IGNORECASE,
)


def _english_known_drug(obj: str) -> bool:
    obj = str(obj or "").lower()
    if not obj:
        return False
    return obj in _ENGLISH_KNOWN_DRUG_TERMS or any(
        obj in term for term in _ENGLISH_KNOWN_DRUG_TERMS
    )


def _english_been_off_drug(text: str) -> bool:
    for match in _ENGLISH_BEEN_OFF_RE.finditer(str(text or "")):
        if _english_known_drug(match.group(1)):
            return True
    return False


#: stopped/discontinued/ceased/quit 的宾语必须是已知药名才算远期
#: 停药：戒酒/戒烟（stopped drinking alcohol）不能抹掉在用药物
#: （第九十一轮 [6]）
_ENGLISH_STOP_ACTIVE_RE = re.compile(
    r"\b(?:stopped|discontinued|ceased|quit)\s+"
    r"(?:(?:drinking|smoking|using|taking|the|my|his|her|their)\s+)*"
    r"([A-Za-z][A-Za-z\-]{2,})\b",
    re.IGNORECASE,
)
_ENGLISH_STOP_PASSIVE_RE = re.compile(
    r"\b([A-Za-z][A-Za-z\-]{2,})\s+(?:(?:was|is|are|has\s+been|"
    r"have\s+been|had\s+been|got)\s+)?"
    r"(?:stopped(?!\s+working)|discontinued|ceased|quit)\b",
    re.IGNORECASE,
)
_ENGLISH_NO_LONGER_RE = re.compile(
    r"\b(?:no\s+longer\s+(?:taking|on)|not\s+(?:on|taking))\b",
    re.IGNORECASE,
)


def _english_stop_names_drug(text: str) -> bool:
    text = str(text or "")
    for pattern in (
        _ENGLISH_STOP_ACTIVE_RE, _ENGLISH_STOP_PASSIVE_RE,
    ):
        for match in pattern.finditer(text):
            if _english_known_drug(match.group(1)):
                return True
    return False
_ENGLISH_RESTART_RE = re.compile(
    r"\b(?:restarted|restarting|started\s+again|back\s+on|"
    r"resumed|currently\s+taking|"
    r"(?:take|taking|started|using)\s+it\s+again|on\s+it\s+again|"
    # now I am using it again / currently using：进行式续用
    # （第一百零二轮 [13]）
    r"now\s+(?:i\s*(?:'m|am)\s+)?(?:using|taking)\b|"
    r"currently\s+using|"
    # kept/continued/continue (to) take/use it：代词宾语续用
    # （第九十四轮 [2]）
    r"(?:kept|keeps?|kept\s+on|continued|continues?|continuing|"
    r"have\s+continued|has\s+continued|have\s+kept|has\s+kept)\s+"
    r"(?:on\s+)?(?:taking|to\s+take|using|to\s+use)?\s*"
    r"(?:it|the\s+same|this)\b"
    # I am taking it now / now I take it daily / started taking it
    # last month：代词宾语的一般现在时/进行式续用
    # （第一百一十六轮 [7][15]）
    r"|i\s*(?:'m|am)\s+(?:taking|using)\s+it\s+now"
    r"|now\s+(?:i\s+)?(?:take|use)\s+it\b"
    r"|started\s+(?:taking|using)\s+it\s+(?:last|this|in|at|on|since)"
    # I take it now / I have been taking it since last year
    # （第一百一十七轮 [13]）
    r"|i\s+(?:take|use)\s+it\s+(?:now|daily|every\s+day)"
    r"|i\s*(?:'m|am)\s+on\s+it\s+now"
    r"|(?:i\s+)?(?:'ve|have)\s+been\s+(?:taking|using)\s+it)\b",
    re.IGNORECASE,
)
#: 英文停药必须有明确时长才算远期停药（not on/not taking 裸分支不算）
_ENGLISH_STOP_DURATION_RE = re.compile(
    r"\b(?:[0-9]+|a|one|two|three|several)\s+(?:years?|months?)\b"
    r"|\bago\b",
    re.IGNORECASE,
)
#: 「除本药外没吃别的」：不是停用本药。裸 daily/every day 可能
#: 描述别的药或运动，不能取消远期停药（第八十八轮）
_ENGLISH_STOP_CONTINUATION_RE = re.compile(
    r"\b(?:besides|other\s+than|except|anything\s+else)\b",
    re.IGNORECASE,
)
#: 「still taking/on X」：代词宾语直接算续用；具名宾语必须在本
#: 分句前文出现过（warfarin was stopped … still taking warfarin），
#: 换用其它药（amlodipine/aspirin/methotrexate/泼尼松）不能复活
#: 本药（第九十轮 [3][17]）
_ENGLISH_STILL_RE = re.compile(
    r"\bstill\s+(?:take|taking|use|using|on)\s+"
    r"(?:(it|the\s+same|this)\b|"
    r"(?:(?:my|his|her|their|the)\s+)?([A-Za-z][A-Za-z\-]{2,})\b)",
    re.IGNORECASE,
)


def _english_still_names_self(text: str) -> bool:
    """「still taking/on X」的宾语是否指回本分句正在讨论的药。"""
    text = str(text or "")
    for match in _ENGLISH_STILL_RE.finditer(text):
        if (match.group(1) or "").strip():
            return True
        obj = (match.group(2) or "").strip()
        if not obj:
            continue
        earlier = text[: match.start()].lower()
        if re.search(
            rf"(?<![a-z]){re.escape(obj.lower())}(?![a-z])",
            earlier,
        ):
            return True
    return False


#: 「…and I take warfarin / I am on warfarin」：同一药名复述=续用
#: （第九十一轮 [7]）
_ENGLISH_RESUME_OBJ_RE = re.compile(
    r"\b(?:take|taking|use|using|am\s+on)\s+"
    r"(?:(?:my|the|his|her|their)\s+)?([A-Za-z][A-Za-z\-]{2,})\b",
    re.IGNORECASE,
)


def _english_resumes_named_drug(text: str) -> bool:
    """无 still 的同药复述（…and I take warfarin）是否指回前文的药。"""
    text = str(text or "")
    for match in _ENGLISH_RESUME_OBJ_RE.finditer(text):
        obj = (match.group(1) or "").strip().lower()
        if not _english_known_drug(obj):
            continue
        earlier = text[: match.start()].lower()
        if re.search(
            rf"(?<![a-z]){re.escape(obj)}(?![a-z])", earlier
        ):
            return True
    return False


def _english_stop_continuation(text: str) -> bool:
    text = str(text or "")
    return (
        bool(_ENGLISH_STOP_CONTINUATION_RE.search(text))
        or _english_still_names_self(text)
        or _english_resumes_named_drug(text)
    )


def _english_still_known_drug_fragment(text: str) -> str:
    """提取停药分句里「still taking/on <已知药名>」的药名片段。

    远期停药短语只否定被停的药；同分句点名另一种仍在服用的抗凝/
    免疫抑制药时，该药必须进入当前用药文本（第九十一轮 [1][2]）。
    """
    text = str(text or "")
    parts = []
    for match in _ENGLISH_STILL_RE.finditer(text):
        if (match.group(1) or "").strip():
            continue
        obj = (match.group(2) or "").strip()
        if _english_known_drug(obj):
            parts.append(f"still taking {obj}")
    return "；".join(parts)
#: 英文否定停药（has not been stopped / never discontinued）
_ENGLISH_NEGATED_STOP_RE = re.compile(
    r"\b(?:not|never|n't)\s+(?:been\s+|have\s+|has\s+|had\s+|"
    r"yet\s+)?(?:stopped|discontinued|ceased|quit|off)\b",
    re.IGNORECASE,
)
#: 明确结束的长期治疗时间线（化疗结束五年/放疗完成多年）：与远期
#: 停药一样，可以否定同段消息里的「没吃完」
_LONG_ENDED_TREATMENT_RE = re.compile(
    # 必须点名化疗/放疗等肿瘤治疗：阑尾/骨科手术或牙科治疗「结束
    # 三个月」与当前抗凝/免疫抑制语境无关，不能据此丢弃「没吃完」
    r"(?:(?:化疗|放疗|靶向治疗|免疫治疗|抗肿瘤治疗)"
    r"[^，。；]{0,8}?(?:结束|完成|做完|停止)|"
    r"(?:finished|completed|done\s+with)\s+(?:the\s+)?"
    r"(?:chemo|chemotherapy|radiotherapy)[^,.;!?\n]{0,16}?|"
    r"(?:结束|完成|做完|停止)[^，。；]{0,8}?"
    r"(?:化疗|放疗|靶向治疗|免疫治疗|抗肿瘤治疗))"
    r"[^，。；]{0,6}?"
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月)|半年|多年)"
)


#: 停药宾语前后的辅助词（剥离后再判断是否点名了他药）
_LONG_STOP_AUX_SUFFIXES = (
    "已经", "已", "早就", "现在", "之前", "以前", "目前", "都",
)
_LONG_STOP_AUX_PREFIXES = (
    "已经", "已", "早就", "都", "快", "目前", "现在", "之前", "以前",
    "医生", "医嘱", "自行", "我们", "我", "他", "她", "本人",
    "让", "叫", "劝",
)
#: 指代当前药物的宾语
_STOP_SELF_REFERENCES = (
    "这个药", "该药", "此药", "这种药", "那种药", "它", "这个", "该",
)
#: 停药动词后的纯时长/时间宾语（停用一年了/去年停了/上个月停了）：
#: 不是药名，也不能判成「另一种药」（第一百零一轮 [7]）
_STOP_TIME_OBJECT_RE = re.compile(
    r"^(?:"
    r"[0-9一二两三四五六七八九十半几]+(?:个)?(?:多)?"
    r"(?:年|月|天|周|日|星期)"
    r"|半年|大半年|多月|多年|很久|好久"
    r"|去年|前年|上年|上一年|上个月|上月|上周|上上个月|"
    r"前几个月|前几年|近几年|近年|年初|年底"
    r")(?:多|了|左右)?$"
    r"|^[了前以来]$"
)
#: 药物类别词 → 当前语境中的同类药名线索（类别一致才算自指）
_STOP_CATEGORY_HINTS = {
    "抗凝药": (
        "华法林", "肝素", "利伐沙班", "达比加群", "阿哌沙班",
        "依诺肝素", "氯吡格雷", "阿司匹林", "抗凝",
    ),
    "抗凝药物": (
        "华法林", "肝素", "利伐沙班", "达比加群", "阿哌沙班",
        "依诺肝素", "氯吡格雷", "阿司匹林", "抗凝",
    ),
    "化疗药": (
        "甲氨蝶呤", "顺铂", "氟尿嘧啶", "紫杉醇", "环磷酰胺",
        "多柔比星", "化疗",
    ),
    "化疗药物": (
        "甲氨蝶呤", "顺铂", "氟尿嘧啶", "紫杉醇", "环磷酰胺",
        "多柔比星", "化疗",
    ),
    "免疫抑制剂": (
        "甲氨蝶呤", "环孢素", "他克莫司", "硫唑嘌呤", "免疫抑制",
    ),
    "激素": (
        "泼尼松", "强的松", "地塞米松", "甲泼尼龙", "氢化可的松",
        "激素", "糖皮质激素",
    ),
    "糖皮质激素": (
        "泼尼松", "强的松", "地塞米松", "甲泼尼龙", "氢化可的松",
        "激素", "糖皮质激素",
    ),
}


#: 停药分句前一分句若以这些药名结尾，归属判定要把两段合看
_OTHER_STOP_DRUG_TAILS = tuple(ANTICOAGULANT_TERMS) + tuple(
    IMMUNOSUPPRESSION_TERMS
) + (
    "阿司匹林", "布洛芬", "双氯芬酸", "萘普生", "对乙酰氨基酚",
    "氯吡格雷", "他汀", "激素", "抗凝药", "化疗药", "免疫抑制剂",
)


def _normalize_stop_token(token: str) -> str:
    """剥离停药宾语前后的时间/辅助词，保留真正的药名。"""
    token = str(token or "").strip()
    changed = True
    while changed and token:
        changed = False
        for word in _LONG_STOP_AUX_SUFFIXES:
            if token.endswith(word) and len(token) - len(word) >= 2:
                token = token[: -len(word)]
                changed = True
        for word in _LONG_STOP_AUX_PREFIXES:
            if token.startswith(word) and len(token) - len(word) >= 2:
                token = token[len(word):]
                changed = True
    return token.strip()


def _stop_token_is_determiner(token: str) -> bool:
    """宾语是否只是辅助词/代词（没有点名任何药名）。"""
    token = str(token or "").strip()
    changed = True
    while changed and token:
        changed = False
        if token in _STOP_SELF_REFERENCES:
            return True
        for word in _LONG_STOP_AUX_SUFFIXES:
            if token == word:
                return True
            if token.endswith(word) and len(token) > len(word):
                token = token[: -len(word)]
                changed = True
        for word in _LONG_STOP_AUX_PREFIXES:
            if token == word:
                return True
            if token.startswith(word) and len(token) > len(word):
                token = token[len(word):]
                changed = True
    return not token or token in _STOP_SELF_REFERENCES


def _stop_clause_names_other_drug(
    clause: str, current_clause: str
) -> bool:
    """远期停药分句是否点名了另一种药（而非当前分句的药）。"""
    clause = re.sub(
        r"[，,](?=\s*(?:已经?|已|早就)?\s*"
        r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|不吃了?))",
        "",
        str(clause or ""),
    )
    match = re.search(
        r"([一-龥A-Za-z0-9]{2,10})\s*"
        r"(?:[（(【「『][^）)】」』]{0,20}[）)】」』]\s*)?"
        r"(?:已经?|已)?\s*"
        r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|不吃了?)",
        clause,
    )
    if match is None:
        # 「阿司匹林（已停用两年）」：停药动词在括号内，宾语在括号前
        # （第一百零七轮 [1][6]）
        match = re.search(
            r"([一-龥A-Za-z0-9]{2,10})\s*[（(][^）)]{0,20}"
            r"(?:已停用|已停药|已停服|停用|停药|停了|停的|停服|"
            r"停止服用|不吃了)[^）)]{0,20}[）)]",
            clause,
        )
    if match is None:
        # 「停用阿司匹林一年了」：药名在停药动词之后的语序同样
        # 点名了另一种药，不能归因到前一分句的药（第九十九轮 [1]）
        after = re.search(
            r"(?:已经?|已|早就|早|都|快)?\s*"
            r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|不吃了?)"
            r"\s*(?:了)?\s*"
            r"([一-龥A-Za-z]{2,8}?)"
            r"(?=\s*(?:[0-9一二两三四五六七八九十半几]+(?:个)?"
            r"(?:多)?(?:年|月|天|周|日|星期)|半年(?:多)?|大半年|多月|"
            r"多年|很久|好久|了|，|。|；|$))",
            clause,
        )
        if after is None:
            return False
        raw_token = after.group(1).strip()
        if _stop_token_is_determiner(raw_token):
            return False
        token = _normalize_stop_token(raw_token)
        if _STOP_TIME_OBJECT_RE.match(token or raw_token):
            return False
    else:
        raw_token = match.group(1).strip()
        token = ""
        if not _stop_token_is_determiner(raw_token):
            token = _normalize_stop_token(raw_token)
            if _STOP_TIME_OBJECT_RE.match(token or raw_token):
                # 「去年停了/上个月停了」：时间词不是药名宾语
                token = ""
        if not token:
            # 前置词只是时间/辅助词（去年停的阿司匹林）：真正的药名
            # 宾语在停药动词之后（第一百零七轮 [1]）
            after = re.search(
                r"(?:已经?|已|早就|早|都|快)?\s*"
                r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|"
                r"不吃了?)"
                r"\s*(?:了)?\s*"
                r"([一-龥A-Za-z]{2,8}?)"
                r"(?=\s*(?:[0-9一二两三四五六七八九十半几]+(?:个)?"
                r"(?:多)?(?:年|月|天|周|日|星期)|半年(?:多)?|大半年|"
                r"多月|多年|很久|好久|了|，|。|；|$))",
                clause,
            )
            if after is not None:
                after_raw = after.group(1).strip()
                if not _stop_token_is_determiner(after_raw):
                    after_token = _normalize_stop_token(after_raw)
                    if len(after_token) >= 2 and not (
                        _STOP_TIME_OBJECT_RE.match(
                            after_token or after_raw
                        )
                    ):
                        token = after_token
    if len(token) < 2:
        return False
    if token in _STOP_SELF_REFERENCES:
        return False
    # 「华法林这个药停了半年」：指代词前的药名与当前分句一致才算自指；
    # 「阿司匹林这个药停了半年」点名的是另一种药
    for reference in ("这个药", "该药", "此药", "这种药", "那种药"):
        if reference in token:
            head = token.split(reference)[0].strip()
            return bool(head) and head not in current_clause
    hints = _STOP_CATEGORY_HINTS.get(token)
    if hints is not None:
        # 「激素停了半年」只有当前语境确属激素类才等价于本药
        return not any(hint in current_clause for hint in hints)
    return token not in current_clause


#: 见 core/medication_text.py：分诊与用药安全共用同一套药名/类别词；
#: medication_safety 的 DRUG_GROUPS 还维护着大量非后缀通用名
#: （呋塞米/格列美脲/螺内酯等），停药归因同样要认出来（第八十五轮）
_DRUG_GROUP_ALIASES = tuple(sorted(
    {
        str(alias).lower()
        for group, items in _DRUG_GROUPS.items()
        if group not in ("grapefruit", "contrast", "alcohol")
        for _canonical, aliases in items
        for alias in aliases
        if len(str(alias)) >= 2
        and str(alias).lower() not in _AMBIGUOUS_SHORT_ALIASES
    },
    key=len,
    reverse=True,
))


def _names_known_group_drug(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(alias in lowered for alias in _DRUG_GROUP_ALIASES)


def _clause_names_other_drug(clause: str) -> bool:
    """分句是否点名/描述了一条用药（停药归因的「最近药名分句」）。

    药名后缀/类别词来自 core/medication_text 共享定义；不再使用
    「用药动作+任意宾语」判定（「吃药不规律/打了疫苗/吃了两年」
    不是另一条药，第八十三轮）。
    """
    text = str(clause or "")
    return bool(
        any(term in text for term in _OTHER_STOP_DRUG_TAILS)
        or clause_mentions_drug_clause(text)
        or _names_known_group_drug(text)
    )


#: 明确表示当前仍在用药/仍在治疗：远期停药不能抹掉这些分句
_LONG_STOP_CURRENT_USE_RE = re.compile(
    r"(?:现在|目前|当前|正在|仍在|还在)"
    r"[^，。；;?？]{0,8}?"
    r"(?:吃|服用|服|用|注射|打|化疗|放疗|抗凝|免疫抑制|激素)"
    r"|(?:化疗|放疗|抗凝|激素治疗|免疫抑制治疗)(?:中|期间)"
    r"|(?:刚|刚刚)(?:做完|结束)?(?:化疗|放疗)"
)
#: 结构化用药清单行（name=华法林；active=True）：权威在用数据，
#: 不按自由文本里的远期停药整行抹掉
_STRUCTURED_MED_LINE_RE = re.compile(r"(?:name|active|status)\s*=", re.I)


def _long_stop_targets_clause(clause: str) -> bool:
    """紧邻远期停药的前一分句是否就是被停的药。

    「华法林吃了三年，停了一年多了」：停药主体是华法林，药名不能
    继续算作当前用药。但「怀孕39周」「正在化疗」「现在吃利伐沙班」
    以及结构化用药清单行不属于停药主体，必须保留（第七十八轮）。
    """
    if not (
        any(term in clause for term in _OTHER_STOP_DRUG_TAILS)
        or _GENERIC_DRUG_NAME_RE.search(clause)
        or _MED_CATEGORY_RE.search(clause)
        or _BRAND_DRUG_NAME_RE.search(clause)
        or _names_known_group_drug(clause)
    ):
        return False
    if _STRUCTURED_MED_LINE_RE.search(clause):
        return False
    if _LONG_STOP_CURRENT_USE_RE.search(clause):
        return False
    return True


def _ended_treatment_targets_clause(clause: str) -> bool:
    """「化疗结束N年」是否归因到本分句的药名（化疗/免疫抑制类）。

    结束化疗/放疗只说明该类治疗结束，不能把同段无关的抗凝药等
    在用药物一起抹掉（第七十九轮）。
    """
    if not _long_stop_targets_clause(clause):
        return False
    hints = tuple(_STOP_CATEGORY_HINTS.get("化疗药", ())) + tuple(
        _STOP_CATEGORY_HINTS.get("免疫抑制剂", ())
    )
    return (
        any(term in clause for term in IMMUNOSUPPRESSION_TERMS)
        or any(hint in clause for hint in hints)
    )


def _clause_ends_with_drug_name(clause: str) -> bool:
    """分句是否以药名结尾（「阿司匹林」「阿司匹林（肠溶片）」）。"""
    tail = re.sub(
        r"[（(【「『][^）)】」』]{0,20}[）)】」』]\s*$", "",
        str(clause or "").strip(),
    ).rstrip("，,、;； \t")
    if not tail:
        return False
    tail = tail[-16:]
    return any(
        tail.endswith(term)
        for term in _OTHER_STOP_DRUG_TAILS
    )


def _intervening_other_drug_clause(
    clauses: List[str], start: int, stop: int
) -> bool:
    """停药分句前（start 与 stop 之间）是否还有另一条药名/用药分句。"""
    for pos in range(start + 1, stop):
        candidate = clauses[pos].strip()
        if not candidate:
            continue
        if _clause_names_other_drug(candidate):
            return True
    return False


def _long_stop_near(clauses: List[str], index: int) -> bool:
    """同段消息是否另有明确远期停药/结束治疗证据。

    「华法林没吃完，最近睡眠不好，胃口也差，已经停药半年了」
    「甲氨蝶呤没吃完，……，化疗结束五年了」：远期停用说明当前
    并不在用药，「没吃完」只是回顾剩余药量，不能据此恢复抗凝/
    免疫抑制语境；即使停药分句离得较远也必须生效（按整段消息
    扫描，家属用药分句不参与判断）。
    """
    current_clause = clauses[index] if 0 <= index < len(clauses) else ""
    for pos, clause in enumerate(clauses):
        if any(
            marker in clause for marker in _FAMILY_HISTORY_MARKERS
        ):
            continue
        if _LONG_STOP_PATTERN.search(clause):
            if pos > index and _intervening_other_drug_clause(
                clauses, index, pos
            ):
                # 「华法林没吃完，二甲双胍吃了两年，停了一年多了」/
                # 「…我爸吃阿司匹林，停了一年多了」：停药更可能指中间
                # 那条药名分句，不能抹掉本药的抗凝语境（第八十一轮）
                continue
            # 「阿司匹林，停了半年」：药名与停药动词落在相邻分句时，
            # 归属判定要把前一分句一起算上，否则会被当成本药停药
            attribution_clause = clause
            if (
                pos > 0
                and re.match(
                    r"^\s*(?:已经?|已|早就)?\s*"
                    r"(?:停用|停药|停了|停掉|停掉了|停服|停止服用|不吃了?)",
                    clause,
                )
                and not any(
                    marker in clauses[pos - 1]
                    for marker in _FAMILY_HISTORY_MARKERS
                )
                and _clause_ends_with_drug_name(clauses[pos - 1])
            ):
                attribution_clause = clauses[pos - 1] + "，" + clause
            if _stop_clause_names_other_drug(
                attribution_clause, current_clause
            ):
                # 「华法林没吃完，阿司匹林停药一年了」：另一种药的
                # 远期停用不能抹掉本药的抗凝语境
                continue
            return True
        if _LONG_ENDED_TREATMENT_RE.search(clause):
            # 结束化疗/放疗只能否定化疗/免疫抑制类在用药物：
            # 「华法林没吃完，化疗结束5年了」的抗凝状态仍成立
            if _ended_treatment_targets_clause(current_clause):
                return True
    return False
#: 停药动词：只有真正的停药表述才做「无时长保守保留」
_STOP_VERB_PATTERN = re.compile(
    r"(?:停用|停药|停了|停的|停掉|停掉了|停服|停止服用|已停|停过|间断|中断|"
    r"停(?=[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月)))"
)
#: 时长前的裸「停」（停三年了）：同样进入停药分句判定
#: 裸「停」+（药名）+ 月/年时长（停三年了/停华法林两年了）：
#: 「停经/停车/停机」等非药物宾语不算（第一百零五轮 [8]）
_BARE_STOP_DURATION_RE = re.compile(
    r"停(?!(?:经|机|工|课|业|车|电|水|职|赛|战|学|产|航|手|留|靠|放|歇))"
    r"[^，。；;,.!?！？\n]{0,6}"
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月))"
)
#: 停药后又重新服用（可跨一个分句）：抗凝状态仍然成立
_RESTART_TEXT_RE = re.compile(
    r"(?:又吃上|又用上|又服上|又在吃|又在服用|又在用|又重新吃|"
    r"又重新服用|重新开始吃|重新开始服用|恢复吃了|恢复用药|"
    r"恢复吃药|恢复服药|接着吃|接着服|接着用|又吃起来了|又用起来了|"
    r"重新吃|重新服用|重新用|恢复服用|恢复吃"
    r"|又开始吃|又开始服|再次服用|继续服用|继续吃|还在吃|还在服用"
    r"|仍在吃|仍在服用|仍然服用|还在用|仍在用|继续用|还用|仍用"
    r"|仍然用|一直在用|仍在使用|继续使用|还在使用"
    r"|还吃|还服用|仍吃|没停过|没间断过|未停过|从未停过|从没停过"
    r"|一直没停|吃到现在|用到现在|吃到今天|用到今天"
    # 进行式续用：「停了半年，现在在吃/目前在用/正在服用」（第一百
    # 零一轮 [2]）；必须带「在/正在」，避免「现在吃5mg阿司匹林」
    # 这类换药后的剂量延续被当成原药续用（round88）；排除吃饭/菜。
    # 「现在吃着/目前用着」持续体同样是续用（第一百零二轮 [7][13]）
    r"|(?:现在|目前|当前)在(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|用)"
    r"|正在(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|用)"
    r"|(?:现在|目前|当前)(?:吃着|用着|服用着|口服着)"
    # 口语「又吃了/又服用了」同样是重启；排除吃饭/吃东西
    r"|又吃了(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])"
    r"|又服用了|又用了(?!别)"
    # 「最近在吃/近来一直服用/现在吃上了」同样是续用
    # （第一百零四轮 [1]）；「吃上」分支必须在前，否则
    # 「最近吃上了」会被「最近吃」先吃掉后半句。「一直在」
    # 与 近期/这几天/这些天 等词形同样收录（第一百零五轮 [5]）
    r"|(?:现在|最近|目前|当前)(?:吃上|用上|服上)(?:了)?"
    r"|(?:最近|近来|近期|这段时间|这阵子|这几天|这些天|前段时间|前阵子)"
    r"(?:一直在|在|一直)?(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|服|用)"
    # 「停了半年，现在每天吃」「上个月开始吃了」同样是续用
    # （第一百一十五轮 [2]）
    r"|(?:现在|目前|最近|近来|近期|当前|这段时间|这阵子|这几天|"
    r"这些天)(?:都|也|还|在)?(?:每天|每日|天天|长期|一直|持续|"
    r"规律|常年)(?:都|也|还|在|坚持|按医嘱|遵医嘱|按时)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面)"
    r"|服用|口服|用)"
    r"|(?:上个月|上月|去年|今年|后来|出院后|术后|那之后|之后|"
    r"现在|目前|最近)开始"
    r"(?:吃(?!饭|菜|东西|水果|零食|早餐|午餐|晚餐|汤|粥|面)"
    r"|服用|口服)(?:了)?"
    # 「我以前吃华法林，现在也吃/也在吃/目前也吃着」（第一百一十七
    # 轮 [1]）：宾语归属仍由 _restart_names_other_medication 校验
    r"|(?:现在|目前|当前|如今)(?:也|都|又)?(?:在|还)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面)"
    r"|服用|口服|用)(?:着|呢)?"
    # 「孩子出生后吃上了」「生完孩子后又吃上了」：产后时间从句后
    # 省略主语的续用（第一百一十八轮 [5]）
    r"|(?:出生后|出生以后|生完|生了|生下|满月|产后)"
    r"[^，。；不没未别]{0,6}"
    r"(?:又|也|才|就)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"奶|粉)|服用|口服|用)(?:上|着)?(?:了)?)"
)
#: 续用线索的宾语（还在吃阿司匹林）：换药后不能复活既往药
_RESTART_OBJECT_RE = re.compile(
    r"(?:又吃上|又用上|又服上|又在吃|又在服用|又在用|又重新吃|"
    r"又重新服用|重新开始吃|重新开始服用|恢复吃了|恢复用药|"
    r"恢复吃药|恢复服药|接着吃|接着服|接着用|又吃起来了|又用起来了|"
    r"重新吃|重新服用|重新用|恢复服用|恢复吃"
    r"|又开始吃|又开始服|再次服用|继续服用|继续吃|还在吃|还在服用"
    r"|仍在吃|仍在服用|仍然服用|还在用|仍在用|继续用|还用|仍用"
    r"|仍然用|一直在用|仍在使用|继续使用|还在使用"
    r"|还吃|还服用|仍吃|没停过|没间断过|未停过|从未停过|从没停过"
    r"|一直没停|吃到现在|用到现在|吃到今天|用到今天"
    r"|又吃了(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])"
    r"|又服用了|又用了(?!别)"
    # 进行式续用：「现在在吃阿司匹林」「目前在用中药调理」：宾语
    # 用于他药归属，换药后不能复活既往药（第一百零二轮 [7][13]）
    r"|(?:现在|目前|当前)在(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|用)"
    r"|正在(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|用)"
    r"|(?:现在|最近|目前|当前)(?:吃着|用着|服用着|口服着)"
    # 最近在吃/现在吃上了：宾语同样参与他药归属（第一百零四轮 [1]）；
    # 「吃上」分支必须在前，否则宾语会从「上了…」开始截取
    r"|(?:现在|最近|目前|当前)(?:吃上|用上|服上)"
    r"|(?:最近|近来|近期|这段时间|这阵子|这几天|这些天|前段时间|前阵子)"
    r"(?:一直在|在|一直)?(?:"
    r"吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])|服用|口服|服|用)"
    r"|(?:现在|目前|当前|如今)(?:也|都|又)?(?:在|还)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面)"
    r"|服用|口服|用)(?:着|呢)?"
    r"|(?:出生后|出生以后|生完|生了|生下|满月|产后)"
    r"[^，。；不没未别]{0,6}(?:又|也|才|就)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"奶|粉)|服用|口服|用)(?:上|着)?(?:了)?"
    r"|(?:每天|每日|天天|一直|持续|规律|长期|常年)"
    r"(?:都|也|还|在|坚持)?"
    r"(?:吃|服用|口服|服|用)"
    r"(?!饭|菜|水果|东西|零食|早餐|午餐|晚餐|汤|粥|面))"
    r"(?:了)?\s*([一-龥A-Za-z0-9]{1,10})"
)
#: 代词/泛称宾语（还在吃这个/那种药）仍指同一药物
_RESTART_OBJECT_IGNORE = (
    "这个", "那个", "这种", "那种", "这些", "那些", "同样", "原来",
    "之前", "它", "药",
)
#: 剥掉尾部「药/药物/药片」后的代词宾语：这药/该药/此药/一样的药
#: 同样指本药（第一百零六轮 [13]，与 medication_safety 口径一致）
_RESTART_SELF_PRONOUNS = (
    "这", "该", "此", "一样", "相同", "同一种", "同样", "原来", "之前",
    "它", "药",
)
#: 宾语前置的续用：「阿司匹林还在吃」
_RESTART_OBJECT_BEFORE_RE = re.compile(
    r"([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*(?:也|还|仍|都)?\s*"
    r"(?:还在吃|还在服用|仍在吃|仍在服用|仍然服用|继续吃|继续服用|"
    # 词表与 _RESTART_TEXT_RE / medication_safety 保持一致
    # （第九十五轮 [14]、第九十六轮 [14]）
    r"还在用|仍在用|继续用|还用|仍用|仍在使用|一直在用|"
    r"继续使用|还在使用|还吃|还服用|仍吃|接着吃|接着服|接着用|"
    r"仍然用)"
)
#: 分句里的用药宾语（以前吃华法林）
_MED_OBJECT_IN_CLAUSE = re.compile(
    r"(?:吃|服用|口服|服|用|注射|打)(?:过|了)?\s*([一-龥A-Za-z0-9]{1,10})"
)


def _restart_names_other_medication(follow: str, clause: str) -> bool:
    """续用分句是否在说另一种药（「还在吃阿司匹林」）。

    续用线索必须与既往分句的药名一致；换用其它药、家属用药都不能
    把已停用的药复活成当前用药。无宾语或代词宾语（还在吃这个）保持
    原来的续用语义。
    """
    match = _RESTART_OBJECT_RE.search(follow)
    if match is None:
        # 宾语前置：「阿司匹林还在吃」说的是另一种药，不能复活
        # 既往分句的药（第八十九轮）。只有宾语确为已知药名时才
        # 算他药：时间/频率副词（现在还在吃/一直还在用）不是药名
        before = _RESTART_OBJECT_BEFORE_RE.search(follow)
        if before is None:
            return False
        obj = re.split(
            r"[，。；,.!?！？\n和及与、]", before.group(1)
        )[0]
        obj = re.sub(
            r"(?:这|那|个|种|些|药)+$", "", obj
        ).strip()
        if len(obj) < 2:
            return False
        drug_terms = [
            term for term in _OTHER_STOP_DRUG_TAILS if term in obj
        ]
        generic = _GENERIC_DRUG_NAME_RE.search(obj)
        if not drug_terms and generic is None:
            return False
        past = _MED_OBJECT_IN_CLAUSE.search(clause)
        past_obj = past.group(1) if past else ""
        if past_obj and (past_obj in obj or obj in past_obj):
            return False
        return True
    obj = re.split(r"[，。；,.!?！？\n和及与、]", match.group(1))[0]
    # 先剥离数量+单位再看宾语：「每天吃一片阿司匹林」是换药，
    # 「又吃了两片」才是同一药物的剂量续用
    obj = re.sub(
        r"^[0-9一二两三四五六七八九十半几]+\s*"
        r"(?:片|粒|丸|袋|包|支|瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)\s*",
        "",
        obj,
        flags=re.IGNORECASE,
    )
    # 时间补语/语气助词不是药名宾语：「一直吃到现在/现在还吃着呢」
    obj = re.sub(
        r"(?:到现在|到今天|至今|到现在为止|以来|为止|一直)$", "", obj
    ).strip()
    obj = re.sub(r"(?:着|呢|啊|呀|吧|了|啦)+$", "", obj).strip()
    if not obj or len(obj) < 2:
        return False
    if any(obj.startswith(word) for word in _RESTART_OBJECT_IGNORE):
        return False
    # 剥掉尾部「药/药物/药片」后仍为代词：这药/该药/此药/一样的药
    # 指的还是本药（第一百零六轮 [13]）
    pronoun = re.sub(r"(?:的?药[物片品剂]?)+$", "", obj).strip()
    if pronoun in _RESTART_SELF_PRONOUNS:
        return False
    past = _MED_OBJECT_IN_CLAUSE.search(clause)
    past_obj = past.group(1) if past else ""
    if past_obj and (past_obj in obj or obj in past_obj):
        return False
    return True


def _restart_object_non_medication(follow: str, clause: str) -> bool:
    """续用线索的显式宾语是否明显不是药物（在吃电脑/火锅/中药调理）。

    这种句子不是本药续用：被停药仍是停用，不能因为「在用X」而
    把已停用药物复活（第一百零二轮 [7][13]）。
    """
    match = _RESTART_OBJECT_RE.search(str(follow or ""))
    if match is None:
        return False
    obj = re.split(r"[，。；,.!?！？\n和及与、]", match.group(1))[0]
    obj = re.sub(
        r"^[0-9一二两三四五六七八九十半几]+\s*"
        r"(?:片|粒|丸|袋|包|支|瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)\s*",
        "",
        obj,
        flags=re.IGNORECASE,
    )
    obj = re.sub(r"(?:着|呢|啊|呀|吧|了|啦)+$", "", obj).strip()
    if obj in ("素", "斋") or obj.startswith(
        ("药膳", "素食", "食疗", "素菜")
    ):
        # 「现在用药膳调理/现在也吃素」不是药物续用
        # （第一百一十八轮 [15]）
        return True
    if obj in (
        "到现在", "到今天", "至今", "到现在为止", "以来", "为止", "一直"
    ) or obj.endswith(("到现在", "到今天", "至今", "以来", "为止")):
        # 时间补语不是药物宾语（一直吃到现在）
        return False
    if not obj or len(obj) < 2:
        return False
    if any(obj.startswith(word) for word in _RESTART_OBJECT_IGNORE):
        return False
    # 这药/该药/此药/一样的药是自指代词，不是非药物对象（第一百零六轮 [13]）
    pronoun = re.sub(r"(?:的?药[物片品剂]?)+$", "", obj).strip()
    if pronoun in _RESTART_SELF_PRONOUNS:
        return False
    past = _MED_OBJECT_IN_CLAUSE.search(clause)
    past_obj = past.group(1) if past else ""
    if past_obj and (past_obj in obj or obj in past_obj):
        return False
    if any(term in obj for term in _OTHER_STOP_DRUG_TAILS):
        return False
    if _GENERIC_DRUG_NAME_RE.search(obj) is not None:
        return False
    if re.fullmatch(
        r"(?:[0-9一二两三四五六七八九十半几]+\s*(?:个)?\s*"
        r"(?:多)?\s*(?:天|日|周|星期|个月|月|年)"
        r"|半年|几天|一段时间|一阵子|长期|好久|很久)"
        r"(?:的疗程|疗程|左右)?",
        obj,
    ):
        # 疗程/时长不是另一种药（继续吃两周）
        return False
    return True


#: 已结束的肿瘤治疗（化疗/放疗）：结束多年不再是当前免疫抑制。
#: 第二分支必须带完成体（过/了），且间隔里不能出现「开始/至今/一直」：
#: 「3个月前开始化疗」是进行中的治疗，不能按已结束处理
_CHEMO_ENDED_RE = re.compile(
    r"(?:finished|completed|done\s+with)\s+(?:the\s+)?"
    r"(?:chemo|chemotherapy|radiotherapy|radiation)[^,.;!?\n]{0,12}|"
    r"(?:化疗|放疗|靶向治疗|免疫治疗)(?:已经?|已)?\s*"
    r"(?:结束|完成|做完|停止)(?:了|后)?"
    r"|[0-9一二两三四五六七八九十半几]+\s*(?:年|个月)\s*前"
    r"(?:(?!开始|至今|一直)[^，。；]){0,6}?"
    r"(?:化疗|放疗)(?:过|了)"
    # 「五年前做过/结束/做完化疗」：完成动词在名词之前
    r"|[0-9一二两三四五六七八九十半几]+\s*(?:年|个月)\s*前"
    r"(?:(?!开始|至今|一直)[^，。；]){0,4}?"
    r"(?:做过|接受过|进行过|打完|做完|完成|结束|停止|停用)(?:了|过)?"
    r"[^，。；]{0,2}?(?:化疗|放疗)"
)
#: 刚结束不久的化疗（结束才一周/结束后一个月）：免疫抑制仍可能存在
_CHEMO_RECENT_END_RE = re.compile(
    r"(?:结束|完成|做完)(?:才|刚|不到|后|已经?有?|快|将近|差不多|约|大概)?\s*"
    # 「结束后第5天」「结束后5-14天」「结束后十来天」「快两周」
    # 「好几周」：出院宣教常用（第九十八轮 [6]）
    r"(?:第\s*)?(?:好几|[0-9一二两三四五六七八九十半几]+"
    r"(?:\s*[-~～—至到]\s*[0-9一二两三四五六七八九十]+)?|几)\s*"
    r"(?:来|多)?\s*(?:天|日|周|星期|个?月)"
)
#: 时间词写在完成动词之前（上周化疗结束/昨天化疗结束/两周前化疗结束）
#: 英文近期结束：finished/completed + yesterday/last week/N days ago
#: 仍按近期处理（免疫抑制窗口内），不能剥离化疗词
#: （第九十七轮 [3]）
_CHEMO_RECENT_END_EN_RE = re.compile(
    r"\b(?:just|recently)\s+(?:finished|completed)\b"
    r"|\b(?:finished|completed|done\s+with)\s+(?:the\s+)?"
    r"(?:chemo(?:therapy)?|radiotherapy|radiation)\b"
    r"[^,.;!?\n]{0,20}?"
    r"\b(?:today|yesterday|last\s+(?:night|week|month)|this\s+week|"
    r"(?:[0-9]+|one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|several|many|a\s+few|a\s+couple(?:\s+of)?)\s+"
    r"(?:days?|weeks?|months?)\s+ago)\b",
    re.IGNORECASE,
)
_CHEMO_RECENT_END_LEAD_RE = re.compile(
    r"(?:今天|昨天|前天|上周|本周|这周|最近|前几天|刚|刚刚"
    r"|[一二两三四五六1-6]\s*(?:天|周|个月)\s*前)"
    r"[^，。；]{0,6}?(?:化疗|放疗|靶向治疗|免疫治疗)(?:已经?|已)?\s*"
    r"(?:结束|完成|做完|停止)"
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
#: 「以前吃过，现在每天吃/目前一直服用」：药名之后的持续用药表述。
#: 吃饭类宾语排除；明确的其它药名由 _restart_names_other_medication 处理
_CONTINUATION_AFTER_PAST = re.compile(
    r"(?:每天|每日|天天|一直|持续|规律|长期|常年)"
    r"(?:都|也|还|在|坚持|按医嘱|遵医嘱|按时)?"
    r"(?:吃|服用|口服|服|用)"
    r"(?!饭|菜|水果|东西|零食|早餐|午餐|晚餐|汤|粥|面)"
    # 「以前吃2mg，现在吃5mg/一片/改吃5mg/调到5mg」：剂量变更
    # 同样是当前续用；变更词本身可充当动词（第八十八轮）
    r"|(?:现在|目前|当前|如今)(?:也|都|又)?(?:在|还)?"
    r"(?:吃(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面)"
    r"|服用|口服|用)(?:着|呢)?"
    r"|(?:现在|目前|如今)(?:每天|每日|仍|还)?\s*"
    r"(?:(?:改吃|改成|改为|改服|换吃|换成|换为|换服|调到|调成|加到|增加到|"
    r"减到)\s*)?"
    r"(?:(?:每天|每日|一天|一日|每晚|每早)(?:一次|1次)?\s*"
    r"(?:[，,；;]\s*每次)?\s*)?(?:(?:吃|服用|口服|用)\s*)?"
    r"(?:[0-9一二两三四五六七八九十半]+(?:\.[0-9]+)?"
    r"\s*(?:mg|毫克|g|克|片|粒|单位|iu|ug)|一片半?|半片|一片)"
)

def _chinese_restart_known_drug_fragment(text: str) -> str:
    """提取续用分句里点名的已知药名（还在用华法林）。

    远期停药短语只否定被停的药；同一分句里「还在用/继续吃 <另一种
    已知药>」时，该药名要保留为当前用药（第九十二轮 [3]）。
    """
    text = str(text or "")
    parts = []
    for match in _RESTART_OBJECT_RE.finditer(text):
        obj = re.split(
            r"[，。；,.!?！？\n和及与、]", match.group(1) or ""
        )[0].strip()
        obj = re.sub(
            r"^[0-9一二两三四五六七八九十半几]+\s*"
            r"(?:片|粒|丸|袋|包|支|瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)\s*",
            "",
            obj,
        )
        obj = re.sub(r"(?:着|呢|啊|呀|吧|了|啦)+$", "", obj).strip()
        if not obj:
            continue
        tail_terms = [
            term for term in _OTHER_STOP_DRUG_TAILS if term in obj
        ]
        generic = _GENERIC_DRUG_NAME_RE.search(obj)
        if tail_terms or generic is not None:
            parts.append(obj)
    return "；".join(parts)


def _continuation_names_other_drug(
    text: str, current_clause: str
) -> bool:
    """剂量延续语（现在吃5mg…）的宾语是否是另一种药。

    延续语直接指向另一药（「现在吃5mg阿司匹林」）时不能据此把
    本分句的既往药判为仍在用；宾语是本分句的药名（「现在吃5mg
    华法林」）时仍算续用。
    """
    text = str(text or "")
    current = str(current_clause or "")
    for match in _CONTINUATION_AFTER_PAST.finditer(text):
        tail = re.split(
            r"[，。；,.!?！？\n]", text[match.end():], maxsplit=1
        )[0][:16]
        if not tail.strip():
            continue
        tail_terms = [
            term for term in _OTHER_STOP_DRUG_TAILS if term in tail
        ]
        if tail_terms and not any(term in current for term in tail_terms):
            return True
        generic = _GENERIC_DRUG_NAME_RE.search(tail)
        if generic and generic.group(0) not in current:
            return True
    return False


#: 「HIV 阴性」「艾滋病抗体阴性」是阴性结果，不代表当前免疫抑制
_NEGATIVE_TEST_SUFFIX = re.compile(
    r"\s*(?:抗体|抗原|检测|筛查|检查|结果)?\s*(?:为|呈|是)?\s*"
    r"(?:阴性|[（(]\s*[-－—]\s*[）)])"
)


_MED_USE_NEGATION_RE = re.compile(
    r"(?:没|没有|未|不|无需|不用)(?:再|在)?"
    r"(?:吃|服用|服|用|喝|注射|打|做|接受|进行|感染|得)(?:过|了)?"
    r"\s*$"
)
_MED_USE_STOP_SUFFIX_RE = re.compile(
    r"^[^，。；]{0,3}(?:我|本人)?(?:已经|已|刚|才)?"
    r"(?:不吃了|不再吃了|不用吃了"
    # 药名后置的否定：「华法林我没在吃/我没有在吃/没吃过」
    r"|没在吃|没在服用|没在吃药|不在吃|未在服用|未服用"
    r"|没有在吃|没有在服用|没有吃|没有服用|没吃过(?!量)"
    r"|没有用过|没用过|未服用过)"
    # 「还没吃完/没吃够」表示仍在服用，不是停药
    r"(?![完够满])"
)
#: 药名之后的既往用药：「华法林以前吃过」
_MED_USE_PAST_SUFFIX_RE = re.compile(
    r"^[^，。；]{0,3}(?:以前|之前|曾经|曾|既往|过去)\s*"
    r"(?:吃过|服用过|服过|用过)(?!量)"
)


def _active_term_mention(text: str, term: str) -> bool:
    """术语是否以非否定形式出现（"无化疗史""HIV阴性"不算当前状态）。"""
    start = text.find(term)
    while start != -1:
        prefix = _clause_prefix(text, start)
        suffix = text[start + len(term):start + len(term) + 16]
        # 单次漏服（我今天没吃 X）、未来停药计划（下周就不吃 X 了）
        # 与意图停药（打算不吃 X/准备不再化疗）仍处于用药状态：
        # 不能被「没吃/不吃/不再」当作否定
        window = text[max(0, start - 12):start + len(term) + 4]
        # 「华法林以前吃过，现在还在吃/每天吃」：药名之后的续用/持续
        # 用药分句说明仍是当前用药，不能被「以前吃过」判为既往
        continuation_window = text[start:start + len(term) + 40]
        continuation = bool(
            _RESTART_TEXT_RE.search(continuation_window)
            or _CONTINUATION_AFTER_PAST.search(continuation_window)
            # 「华法林，昨天停药了」：近期停药仍有抗凝作用
            or _RECENT_STOP_PATTERN.search(continuation_window)
            or _RECENT_NO_EAT_PATTERN.search(continuation_window)
        )
        if continuation and _restart_names_other_medication(
            continuation_window, text[max(0, start - 30):start]
        ):
            # 续用线索指向另一种药（以前吃华法林，现在每天吃阿司匹林）
            continuation = False
        continued_use = (
            _DOSE_MISSED_PATTERN.search(prefix) is not None
            # 「华法林今天没有吃」：漏服时间词写在药名之后同样是
            # 单次漏服，当前抗凝状态仍成立
            or _DOSE_MISSED_PATTERN.search(suffix) is not None
            # 「没吃华法林三天了」：漏服时长写在药名之后同样是
            # 单次漏服，当前抗凝状态仍成立（第九十四轮 [7]）；
            # 漏服宾语必须是本药，别药漏服不能复活本药
            # （第九十五轮 [16]）
            or _missed_dose_names_term(prefix + term + suffix, term)
            # 「昨天开始不吃华法林了」：近期停药仍按当前抗凝状态处理
            or _RECENT_NO_EAT_PATTERN.search(prefix) is not None
            or _RECENT_NO_EAT_PATTERN.search(suffix) is not None
            or _RECENT_NO_EAT_PATTERN.search(window) is not None
            or _FUTURE_STOP_PATTERN.search(prefix) is not None
            or _FUTURE_STOP_PATTERN.search(suffix) is not None
            or _FUTURE_STOP_PATTERN.search(window) is not None
            or _STOP_INTENT_PATTERN.search(window) is not None
            or continuation
        )
        negated = (
            not _TRIAGE_DOUBLE_NEGATION.search(prefix)
            and not continued_use
            and (
                _is_negated(prefix, suffix)
                or bool(
                    _NEGATIVE_TEST_SUFFIX.match(
                        text, start + len(term)
                    )
                )
                or _MED_USE_NEGATION_RE.search(prefix) is not None
                or _MED_USE_STOP_SUFFIX_RE.search(suffix) is not None
                or _MED_USE_PAST_SUFFIX_RE.search(suffix) is not None
            )
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
            if stripped.endswith(
                ("不发", "没发", "没有发", "未发",
                 "也没有发", "也没有再发")
            ):
                # 「没有发烧，也没有发高烧」：同一分句里对其它发热词
                # 的否认同样生效
                start = text.find(term, start + len(term))
                continue
            if term == "烧了" and (
                stripped.endswith("退")
                or stripped.endswith(("火", "燃烧"))
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
            after = text[start + len(term):start + len(term) + 12]
            negative_prefix = (
                r"[^，。；！？\n]{0,4}?"
                r"(?:不到|没到|未到|没有?到|不足|未达|差一点|差点)"
            )
            duration_after = re.match(
                negative_prefix
                + r"\s*[0-9一二两三四五六七八九十]+\s*"
                r"(?:天|日|小时|周|个?月|年)",
                after,
            )
            if not duration_after:
                low_threshold = re.match(
                    negative_prefix
                    + r"\s*(?:3[0-7](?:\.\d+)?|38(?:\.0+)?)"
                    r"\s*(?:度|℃)?",
                    after,
                )
                if low_threshold:
                    # 「体温/发烧不到38度」：读数低于发热界才算无发热；
                    # 「没到39度/不到40度」仍可能是 38.x 的真实发热，
                    # 「不到一天/48小时」是病程时长（第八十三轮）
                    start = text.find(term, start + len(term))
                    continue
            if not _is_negated_with_trailing(
                prefix, _clause_suffix(text, start + len(term))
            ) and not _trailing_negation_in_clause(
                # 「发烧、咳嗽、胸痛都没有」：顿号枚举的整组后置否定
                # 同样覆盖发热词，与红旗短语路径同口径
                # （第一百零八轮 [6]）
                text, start + len(term), prefix,
            ):
                return True
            start = text.find(term, start + len(term))

    for match in TEMPERATURE_PATTERN.finditer(text):
        # 显式摄氏温度分支从数字开始；有发热语境的分支由上下文保证
        if match.group(0)[:1].isdigit():
            window = text[max(0, match.start() - 8):match.end() + 8]
            if any(word in window for word in _NON_BODY_TEMPERATURE_WORDS):
                continue
            recent = text[max(0, match.start() - 6):match.start()]
            if re.search(
                r"(?:不到|没到|未到|没有?到|不足|未达|差一点|差点)\s*$",
                recent,
            ):
                # 「没有到38度」：数字前紧跟阈值否定；读数本身 ≥38
                # 时（「没到39度」）仍是真实发热，不能跳过
                try:
                    reading_value = float(re.match(
                        r"[0-9]+(?:\.[0-9]+)?", match.group(0)
                    ).group(0))
                except (AttributeError, ValueError):
                    reading_value = 0.0
                if reading_value <= 38:
                    continue
        else:
            keyword = re.match(
                r"(?:体温|发烧|发热|高烧|低烧|烧到)", match.group(0)
            )
            bridge = match.group(0)[keyword.end():] if keyword else ""
            if re.search(
                r"(?:不到|没到|未到|没有?到|不足|未达|不是|差一点|差点)",
                bridge,
            ):
                # 「体温不到38度」：读数低于发热阈值才跳过；界限
                # ≥38（「没到39度」）仍按发热处理（第八十三轮）
                bridge_values = re.findall(
                    r"[0-9]+(?:\.[0-9]+)?", bridge
                )
                if not bridge_values or float(bridge_values[0]) <= 38:
                    continue
        prefix = _clause_prefix(text, match.start(), lookback=8)
        if not _is_negated_with_trailing(
            prefix, _clause_suffix(text, match.end())
        ):
            return True
    return False


def _active_medical_text(
    context: Dict[str, Any], child_patient: bool = False
) -> str:
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
    # 「大姨妈」含家属称谓子串「妈」，会被家属分支整句丢弃；它是
    # 月经的口语说法，归一后再做停经/孕产判断（第一百零六轮 [2]）。
    # 只在后接月经线索时归一：「大姨妈在化疗/在吃华法林」是家属，
    # 必须保留原词让家属分句过滤生效（第一百零七轮 [16]）
    raw = re.sub(
        r"大姨妈(?=[^，。；;,.!?！？\n]{0,8}?"
        r"(?:期间|前后|那几天|刚走|刚完|这几天|"
        r"没(?:有)?来|未至|推迟|推后|晚了|迟了|来了))",
        "月经",
        raw,
    )
    # 小数剂量（2.5mg）里的句点不是分句边界：切断后剂量续用
    # 识别不到单位，会把当前用药误判为既往（第八十九轮）
    clauses = re.split(r"[，。；;,!?！？\n]|(?<!\d)\.|\.(?!\d)", raw)
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
            elif _has_patient_subject(clause) and not (
                not child_patient
                and any(
                    term in clause
                    for term in (
                        "孩子", "小孩", "宝宝", "娃", "大宝", "二宝",
                    )
                )
                and "我" not in clause
                and not any(
                    term in clause
                    for term in ("本人", "患者", "患儿")
                )
            ):
                # 「我和我爸都吃华法林/都在化疗」：患者是并列主语，
                # 不能因为家属称谓整句丢弃；成人档案下「孩子在吃
                # 华法林」是家属，患儿档案由上方 child_patient 兜底
                # （第一百一十五轮 [12]）
                pass
            elif child_patient and any(
                term in clause for term in (
                    "儿子", "女儿", "我儿", "我女", "孩子", "宝宝", "娃",
                    "闺女",
                )
            ):
                # 患儿档案下家长口述「我儿子正在化疗/在吃华法林」：
                # 儿子/女儿指的就是当前患者本人
                pass
            elif _patient_continuation_ahead(clauses, index, clause):
                # 「我爸吃华法林，我也在吃」：后一分句患者本人续用
                # 同一药物/过敏原，家属分句里的药名不能整句丢弃
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
        if re.search(r"(?<!抗)过敏", clause):
            # 「肝素过敏/对华法林过敏」是过敏陈述，不是正在用药；
            # 只剥离过敏短语本身：同一分句里的其它状态（华法林每天
            # 一片/化疗期间/怀孕中期）继续参与判断，不能整句丢弃
            residue = re.sub(
                r"对[^，。；]{0,10}?(?:过敏|过敏史|过敏原|过敏反应)",
                "",
                clause,
            )
            if residue == clause:
                residue = re.sub(
                    r"[^，。；]{0,6}?(?:过敏|过敏史|过敏原|过敏反应)",
                    "",
                    clause,
                )
            residue = residue.strip(" ，,；")
            if not residue:
                continue
            clause = residue
        if _CHEMO_ENDED_RE.search(clause) and not _CURRENT_MEDICATION_CUE.search(
            clause
        ) and not re.search(
            r"(?:刚|刚刚|最近|这两天|这几天)\s*(?:结束|完成|做完)", clause
        ) and not _CHEMO_RECENT_END_RE.search(clause) and not (
            _CHEMO_RECENT_END_LEAD_RE.search(clause)
        ) and not _CHEMO_RECENT_END_EN_RE.search(clause):
            # 「化疗结束5年了/化疗已经结束了」不是当前免疫抑制；
            # 只剥离结束短语，同分句的其它当前状态（每天吃华法林/
            # 怀孕/长期激素）继续参与判断
            residue = _CHEMO_ENDED_RE.sub("", clause).strip(" ，,；")
            if not residue:
                continue
            clause = residue
        # 续用线索可能落在下一个分句（「华法林停了半年，最近又吃上了」）
        # 家属用药或换用其它药物的分句不能复活本句的既往药：
        # 「以前吃华法林，我爸还在吃华法林」「…现在还在吃阿司匹林」
        restart_parts = []
        # 「停了一年多，后来医生说可以吃，现在又吃上了」：续用
        # 可能隔着一两个原因/事件分句（第一百一十六轮 [2]）
        for follow in clauses[index + 1:index + 5]:
            follow = follow.strip()
            if not follow:
                continue
            if any(
                marker in follow
                for marker in _FAMILY_HISTORY_MARKERS
            ):
                explicit_family = re.search(
                    r"我(?:的)?(?:家(?:里)?(?:的)?)?(?:"
                    + _FAMILY_SUBJECT_NAMES
                    + r")",
                    follow,
                ) is not None
                postpartum_clause = re.search(
                    r"(?:生完|生了|生下|出生以后|出生后|满月|产后)",
                    follow,
                ) is not None
                if explicit_family or not (
                    postpartum_clause
                    or re.search(r"我|本人|患者|患儿", follow)
                ):
                    # 显式家属主语（我妈目前还在吃）跳过；「孩子出生后
                    # 吃上了」「生完孩子后又吃上了」是患者产后续用
                    # （第一百一十七轮 [6]、第一百一十八轮 [5][7]）
                    continue
            if _restart_names_other_medication(follow, clause):
                continue
            if _restart_object_non_medication(follow, clause):
                # 「现在用药膳调理」「现在也吃素」不是本药续用
                # （第一百一十八轮 [15]）
                continue
            restart_parts.append(follow)
        restart_window = "；".join(restart_parts)[:40]
        english_stopped = bool(
            _ENGLISH_NO_LONGER_RE.search(clause) is not None
            or _english_been_off_drug(clause)
            or (
                _ENGLISH_STOP_ACTIVE_RE.search(clause) is not None
                and _english_stop_names_drug(clause)
            )
            or (
                _ENGLISH_STOP_PASSIVE_RE.search(clause) is not None
                and _english_stop_names_drug(clause)
            )
        )
        if (
            english_stopped
            and _LONG_STOP_PATTERN.search(clause)
            and _ENGLISH_STOP_DURATION_RE.search(clause)
            and not _ENGLISH_RESTART_RE.search(clause)
            and not _ENGLISH_RESTART_RE.search(restart_window)
            and not _english_stop_continuation(clause)
            # 跨分句「…stopped, still taking it」：续用分句没有药名，
            # 但停药分句必须保留（第九十三轮 [1]）
            and not _english_stop_continuation(restart_window)
            and not _ENGLISH_NEGATED_STOP_RE.search(clause)
        ):
            # 英文远期停药（warfarin was stopped 2 years ago）与
            # 中文口径一致：要求明确时长，且「除华法林外没吃别的」
            # 「没有停用」仍按当前用药（第八十六/八十七轮）。停药
            # 只作用于被停的药：同分句「still taking 另一种在用药」
            # 的药名必须保留（第九十一轮 [1][2]）
            preserved = _english_still_known_drug_fragment(clause)
            if preserved:
                kept.append(preserved)
            continue
        if (
            _NEGATED_STOP_PATTERN.search(clause)
            or _STOP_INTENT_PATTERN.search(clause)
            or _STOP_QUESTION_PATTERN.search(clause)
            or _SWITCH_MEDICATION_PATTERN.search(clause)
            or _RECENT_STOP_PATTERN.search(clause)
            or _RECENT_NO_EAT_PATTERN.search(clause)
        ):
            # 「未停用华法林」「打算停用华法林」「停用旧药改用新药」
            # 「昨天停药」都表示当前仍有用药/抗凝作用，不能整句丢弃
            kept.append(clause)
            continue
        if _TRIAGE_DOUBLE_NEGATION.search(clause):
            # 「不得不吃/不能不吃华法林」= 仍在用药，不能被
            # 「不吃」子串当作停药
            kept.append(clause)
            continue
        dose_missed = bool(
            _DOSE_MISSED_PATTERN.search(clause)
            or _DOSE_MISSED_OBJECT_FIRST_RE.search(clause)
        )
        if dose_missed and _LONG_STOP_PATTERN.search(clause):
            # 同分句同时有远期停药与漏服：只有漏服对象是高风险药
            # 才保留，并且只保留漏服片段，不能把同句远期停药的
            # 他药一起复活（第九十五轮 [16]、第九十六轮 [6][11]）
            fragment = _missed_dose_high_risk_fragment(clause)
            if fragment:
                kept.append(fragment)
            continue
        if dose_missed:
            # 「华法林今天没吃」「没吃华法林三天了」是漏服：仍处于
            # 抗凝状态，不能按「没吃」整句丢弃
            kept.append(clause)
            continue
        if _FUTURE_STOP_PATTERN.search(clause):
            # 「下周就不吃华法林了」是未来的停药计划，当前仍在用药
            kept.append(clause)
            continue
        if any(
            marker in clause
            for marker in _CONTEXT_INACTIVE_MARKERS
        ) or _BARE_STOP_DURATION_RE.search(clause):
            diet_match = re.search(
                r"(?:不吃|没吃|没有吃|未吃|不再吃|没再吃)"
                r"([^，。；,.!?！？\n]{0,10})",
                clause,
            )
            diet_object = (
                diet_match.group(1).strip() if diet_match else ""
            )
            if (
                diet_object
                and not diet_object.startswith(
                    ("了", "啦", "啊", "呀", "完", "够", "满")
                )
                and not any(
                    word in diet_object
                    for word in (
                        "这个药", "这种药", "那种药", "那个药", "它",
                        *ANTICOAGULANT_TERMS, *IMMUNOSUPPRESSION_TERMS,
                    )
                )
                and not _STOP_VERB_PATTERN.search(clause)
            ):
                # 「服用华法林期间不吃青菜」：否定的宾语是食物，
                # 不是药物本身，同句药名仍是当前用药。用「宾语里
                # 没有已知药名」判定，避免食物白名单永远漏项；
                # 同时剥离饮食限制短语，避免「华法林…没有吃青菜」
                # 被药名级否定规则当成停用
                cleaned_diet = (
                    clause[:diet_match.start()]
                    + clause[diet_match.end():]
                ).strip(" ，,；")
                if cleaned_diet:
                    kept.append(cleaned_diet)
                continue
            if _FINISHED_DOSE_RE.search(clause) and not (
                _long_stop_near(clauses, index)
                and not (
                    _RESTART_TEXT_RE.search(clause)
                    or _RESTART_TEXT_RE.search(restart_window)
                )
            ):
                # 「华法林还没吃完」说明仍在用药，不是停药；但同段
                # 消息已明确远期停药/结束治疗（停药半年、化疗结束
                # 五年）时，「没吃完」只是回顾药量，不能恢复用药语境；
                # 明确的续用表述（现在又在吃）仍优先保留
                kept.append(clause)
                continue
            if (
                _RESTART_TEXT_RE.search(clause)
                or _RESTART_TEXT_RE.search(restart_window)
                or _CHEMO_RECENT_END_RE.search(clause)
                or _CHEMO_RECENT_END_LEAD_RE.search(clause)
            ):
                # 「停了半年，最近又吃上了」：跨分句续用线索；
                # 「化疗结束后一周」：结束后仍处免疫抑制窗口。
                # 续用指向另一种已知药时只保留该药名，不能把整个
                # 停药分句（含被停药）当成当前用药（第九十二轮 [3]）
                if _restart_names_other_medication(
                    clause, clause
                ) or _restart_names_other_medication(
                    restart_window, clause
                ):
                    if _restart_object_non_medication(
                        clause, clause
                    ) or _restart_object_non_medication(
                        restart_window, clause
                    ):
                        # 明确在吃非药物对象（电脑/火锅/中药调理）：
                        # 不是本药续用，被停药仍是停用
                        # （第一百零二轮 [7][13]）
                        continue
                    fragment = _chinese_restart_known_drug_fragment(
                        clause + "；" + restart_window
                    )
                    if not fragment:
                        # 宾语前置（甲氨蝶呤还在服用）：补充提取；
                        # 仍无法归属时保留原分句，宁可误保留被停药
                        # 名也不能丢掉仍在服用的药物（第九十三轮 [4]）
                        before_text = clause + "；" + restart_window
                        for before in _RESTART_OBJECT_BEFORE_RE.finditer(
                            before_text
                        ):
                            obj = re.sub(
                                r"(?:这|那|个|种|些|药)+$", "",
                                before.group(1),
                            ).strip()
                            if not obj:
                                continue
                            if (
                                any(
                                    term in obj
                                    for term in _OTHER_STOP_DRUG_TAILS
                                )
                                or _GENERIC_DRUG_NAME_RE.search(obj)
                                is not None
                            ):
                                fragment = obj
                                break
                    if fragment:
                        kept.append(fragment)
                    else:
                        kept.append(clause)
                    continue
                kept.append(clause)
                continue
            # 数字与单位之间可能有空格（已停用 2 个月）：比对前去掉，
            # 避免同一语义因空格写法不同而整句丢弃（第一百零六轮 [1]）
            stop_clause_text = re.sub(
                r"(?<=[0-9])\s+(?=[个年月天周日])", "", clause
            )
            if (
                _STOP_VERB_PATTERN.search(clause)
                and not _LONG_STOP_PATTERN.search(stop_clause_text)
            ):
                # 没有明确远期时长的停药保守按仍有抗凝作用处理
                kept.append(clause)
            elif _LONG_STOP_PATTERN.search(stop_clause_text):
                # 同一分句里既有在用药物又有远期停药标注
                # （华法林 3mg 每日一次、阿司匹林（已停用两年））：
                # 只剔除被标注停药的分项，保留其余在用药物
                # （第一百零六轮 [1]）。
                # 「A、B都停了两年」是共享停药：动词作用于整个列表，
                # 首个药名不能按在用保留（第一百零七轮 [17]）
                shared_stop = re.search(
                    # 群标记必须带枚举/总括词（都/也/均/全都/都已…）：
                    # 裸「已停用」只是括号内单药标注，不是列表共享
                    r"(?:都|也|均|全都|都已|均已|也都)(?:已经)?\s*"
                    r"(?:停用了|停用|停药|停服|停止了|停掉了|停了|"
                    r"不吃了|不再吃了|不再服用了?|停止服用了?|"
                    r"停止使用了?)",
                    clause,
                )
                if shared_stop and re.search(
                    r"(?:还在|仍在|没停|未停|尚未|不能停)", clause
                ):
                    shared_stop = None
                if shared_stop is None:
                    residual_parts = []
                    pieces = [
                        piece.strip()
                        for piece in re.split(
                            r"[、,，和与及跟/＋]|\s+", clause
                        )
                    ]
                    bare_time_stop = re.compile(
                        r"^(?:[0-9一二两三四五六七八九十半几]+\s*"
                        r"(?:个)?\s*(?:多|半|来)?\s*"
                        r"(?:天|日|周|星期|个月|月|年)|半年|大半年|"
                        r"多年|好几年|好久|很久|数月|几个?月|"
                        r"一段时间|去年|前年|上个月|上周|最近)"
                        r"\s*(?:前|以来)?\s*(?:已|已经|都|早就)?\s*"
                        r"(?:停用了|停用|停药|停的|停了|停服|"
                        r"停止服用|不吃了)"
                    )
                    for piece_index, item in enumerate(pieces):
                        if not item:
                            continue
                        piece_stop = re.search(
                            r"[（(][^）)]{0,24}(?:已停用|已停药|已停服|"
                            r"停用|停药|停了|停的|停服|停止服用|不吃了)",
                            item,
                        )
                        if piece_stop is not None:
                            leading = item[: piece_stop.start()].strip()
                            if not leading or re.match(
                                r"^[0-9一二两三四五六七八九十半几]+"
                                r"(?:\.[0-9]+)?\s*(?:片|粒|丸|袋|包|支|"
                                r"瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)?$",
                                leading,
                                re.IGNORECASE,
                            ):
                                # 「华法林 3mg（已停用两年）」：标注挂在
                                # 剂量片段上，剔除前一药名
                                # （第一百一十九轮 [4]）
                                if residual_parts:
                                    residual_parts.pop()
                                continue
                        if (
                            _STOP_VERB_PATTERN.search(item)
                            or _LONG_STOP_PATTERN.search(item)
                        ):
                            continue
                        following = (
                            pieces[piece_index + 1]
                            if piece_index + 1 < len(pieces) else ""
                        )
                        if following and bare_time_stop.match(following):
                            # 「阿司匹林 两个月前已停用」：空格后的
                            # 时长+停药归属于前一项药名，一起剔除
                            continue
                        # 只有确含药名证据的分项才保留
                        # （「和/与/空格」连接与剂量片段拆分后的
                        # 碎词不进入在用药物文本）（第一百零七轮 [6]）
                        if not (
                            any(
                                term in item
                                for term in _OTHER_STOP_DRUG_TAILS
                            )
                            or _GENERIC_DRUG_NAME_RE.search(item)
                            or _MED_CATEGORY_RE.search(item)
                            or _BRAND_DRUG_NAME_RE.search(item)
                            or _names_known_group_drug(item)
                        ):
                            continue
                        residual_parts.append(item)
                    if residual_parts:
                        kept.append("、".join(residual_parts))
            continue
        if (
            _PAST_MEDICATION_PATTERN.search(clause)
            and not _CONTINUOUS_MEDICATION_PATTERN.search(clause)
            and not _CURRENT_MEDICATION_CUE.search(clause)
        ):
            # 「以前吃华法林」是既往用药；含近期时长、「现在还在吃」
            # 或后续分句出现续用/持续用药线索（现在每天吃）时仍属
            # 当前用药
            follow_stop_window = "；".join(
                candidate.strip()
                for candidate in clauses[index + 1:index + 3]
                if candidate.strip()
                and not any(
                    marker in candidate
                    for marker in _FAMILY_HISTORY_MARKERS
                )
            )[:60]
            if (
                _RESTART_TEXT_RE.search(clause)
                or _RESTART_TEXT_RE.search(restart_window)
                or (
                    _CONTINUATION_AFTER_PAST.search(clause)
                    and not _continuation_names_other_drug(
                        clause, clause
                    )
                )
                or (
                    _CONTINUATION_AFTER_PAST.search(restart_window)
                    and not _continuation_names_other_drug(
                        restart_window, clause
                    )
                )
                # 「我之前吃华法林，昨天停药了」：近期停药仍处抗凝状态，
                # 保留药名分句供 _active_term_mention 关联
                or _RECENT_STOP_PATTERN.search(follow_stop_window)
                or _RECENT_NO_EAT_PATTERN.search(follow_stop_window)
            ):
                kept.append(clause)
            continue
        next_stop_index = 0
        for pos in range(index + 1, len(clauses)):
            candidate_clause = clauses[pos].strip()
            if not candidate_clause:
                continue
            if (
                _LONG_STOP_PATTERN.search(candidate_clause)
                or _LONG_ENDED_TREATMENT_RE.search(candidate_clause)
            ):
                next_stop_index = pos
                break
            if _clause_names_other_drug(candidate_clause):
                # 中间还有另一条用药分句（含家属的药分句/类别词/
                # 「奥美拉唑吃了两年」式动作宾语）：远期停药更可能指
                # 它，本分句必须先于那条记录处理。裸动词（胃不舒服/
                # 打了点滴/用着不舒服）不算（第八十一/八十二轮）
                break
        if next_stop_index:
            stop_clause = clauses[next_stop_index].strip()
            if _LONG_STOP_PATTERN.search(stop_clause):
                attributable = _long_stop_targets_clause(clause)
            else:
                # 「化疗结束5年了」只能归因到化疗/免疫抑制类药名，
                # 华法林等无关在用药物必须保留（第七十九轮）
                attributable = _ended_treatment_targets_clause(clause)
            if (
                attributable
                and not _stop_clause_names_other_drug(
                    stop_clause, clause
                )
                and not _RESTART_TEXT_RE.search(clause)
                and not _RESTART_TEXT_RE.search(restart_window)
            ):
                # 「华法林吃了三年，去年胃出血，停了一年多了」：本句
                # 是停药分句之前最近的一条药名分句时，药名不能继续
                # 算作当前用药。只看最近一条药名分句，避免把孕期/
                # 免疫状态/结构化在用药物等无关分句删掉（第七十八/
                # 七十九轮）
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
    "小孩", "儿童", "新生儿", "小儿",
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


def _context_child_patient(context: Any) -> bool:
    """当前档案是否是儿童/婴幼儿（家属称谓在此语境下指患者本人）。

    档案未填年龄时按未知处理：宁可把「我儿子发烧」算到患者头上
    触发安全网，也不能在患儿档案下静默丢弃急症描述；明确成年
    （≥18 岁）的档案仍保持「我爸/我妈」等家属分句的排除口径。
    """
    if not isinstance(context, dict):
        return True
    months = _parse_age_months(context.get("age"))
    return months is None or months < 216


def _patient_only_text(
    text: str, child_patient: bool = False
) -> str:
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
                # 「我陪我妈去化疗」：不是患者本人的治疗；但同一分句
                # 若在描述患者本人的发热/症状（我照顾我爸时发烧），
                # 不能整句丢弃
                if not (
                    _has_fever(clause)
                    or TEMPERATURE_PATTERN.search(clause) is not None
                    or any(
                        term in clause for term in _NEGATED_SYMPTOM_TERMS
                    )
                ):
                    continue
            elif _has_patient_subject(clause):
                # 「我妈说宝宝发烧」「我和我爸都发烧」：句中有明确的
                # 患者主体，不能因为家属称谓整句丢弃
                pass
            elif child_patient and any(
                term in clause for term in (
                    "儿子", "女儿", "我儿", "我女", "孩子", "宝宝", "娃",
                    "闺女",
                )
            ):
                # 患儿档案下家长口述「我儿子今天发烧」：儿子/女儿
                # 指的就是当前患者本人
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
            if subject in ("孩子", "宝宝", "娃", "老人") and re.search(
                r"(?:我|他|她)(?:的)?(?:家(?:里)?(?:的)?)?\s*$",
                before,
            ):
                # 「我家孩子/我家宝宝」是家属，不是档案患者本人
                start = clause.find(subject, start + len(subject))
                continue
            return True
    # 裸「我」不能是家属称谓的一部分（我爸/我妈/我儿子…）；
    # 「的」同样要检查（我的父亲/我的妈妈）：父亲等词在「的」之后
    # 时仍是家属，整句不能按患者本人用药（第一百零四轮 [7]）。
    # 称谓表与 _FAMILY_HISTORY_MARKERS/_PATIENT_SUBJECT_MARKERS 对齐：
    # 岳父/岳母/公公/婆婆/孙子/外甥/闺女等同样是家属（第一百零五轮 [6]）
    return re.search(
        r"我(?!的?(?:爸|妈|父母|父|母|爷|奶|外公|外婆|姥姥|姥爷|哥|姐|弟|妹|"
        r"儿|女|爱人|对象|丈夫|妻子|配偶|老公|老婆|家人|亲|"
        r"家|孩|宝|娃|岳父|岳母|公公|婆婆|女婿|儿媳|孙子|孙女|"
        r"外孙|外甥|侄|叔|伯|姑|姨|舅|婶|嫂|闺女|老爷子|老太太|"
        r"爹|娘|丈母娘|老丈人|岳丈|公婆|媳妇|病人|家属))",
        clause,
    ) is not None


def _patient_pattern_mention(
    text: str, pattern, child_patient: bool = False,
    negate_almost: bool = False,
) -> bool:
    """正则版本的患者本人匹配：分句过滤家属叙述后按正则判断。"""
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
                pass
            elif child_patient and any(
                term in clause for term in (
                    "儿子", "女儿", "我儿", "我女", "孩子", "宝宝", "娃",
                    "闺女",
                )
            ):
                # 患儿档案下家长口述「我儿子头被门撞了」指患者本人
                pass
            else:
                continue
        for match in pattern.finditer(clause):
            prefix = _clause_prefix(clause, match.start())
            suffix = _clause_suffix(clause, match.end())
            if negate_almost and _ALMOST_PATTERN.search(prefix):
                # 「差点撞到头」= 撞击未发生（第九十七轮 [8]）
                continue
            if not _is_negated(prefix, suffix):
                return True
    return False


def _head_trauma_negated(prefix: str, suffix: str = "") -> bool:
    """头部外伤否定：差点/险些/几乎撞到头=撞击未发生（第九十七轮 [8]）。"""
    if _ALMOST_PATTERN.search(prefix):
        return True
    return _is_negated(prefix, suffix)


def _patient_head_trauma_mention(
    text: str, child_patient: bool = False
) -> bool:
    """患者本人的头部外伤：词表按词边界（排除脚趾头/手指头）与否定过滤。"""
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
                pass
            elif child_patient and any(
                term in clause for term in (
                    "儿子", "女儿", "我儿", "我女", "孩子", "宝宝", "娃",
                    "闺女",
                )
            ):
                pass
            else:
                continue
        swell = _HEAD_SWELLING_RE.search(clause)
        if swell is not None:
            prefix = _clause_prefix(clause, swell.start())
            suffix = _clause_suffix(clause, swell.end())
            if not _head_trauma_negated(prefix, suffix):
                return True
        for term in HEAD_TRAUMA_TERMS:
            start = clause.find(term)
            while start != -1:
                if not (
                    term.startswith("头")
                    and start > 0
                    and clause[start - 1] in _HEAD_NON_HEAD_PREFIX
                ):
                    prefix = _clause_prefix(clause, start)
                    suffix = _clause_suffix(
                        clause, start + len(term)
                    )
                    if not _head_trauma_negated(prefix, suffix):
                        return True
                start = clause.find(term, start + len(term))
    return _patient_pattern_mention(
        text, _HEAD_TRAUMA_RE, child_patient, True
    )


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
    child_patient = _context_child_patient(context)
    patient_text = _patient_only_text(text, child_patient)
    age_months = _parse_age_months(context.get("age"))
    if age_months is not None and age_months < 3 and _has_fever(patient_text):
        flags.append((
            "3 个月以下婴儿发热",
            "3 个月以下婴儿出现发热需要尽快由儿科或急诊医生评估，"
            "请不要自行用药，立即前往医院。",
        ))

    medical_text = _active_medical_text(context, child_patient)
    # 用户可能只在本次主诉里自述化疗/抗凝（档案/用药清单未录入）：
    # 免疫抑制与抗凝判断都并入主诉文本，过滤规则与结构化文本一致
    complaint_medical = _active_medical_text(
        {"history": text}, child_patient
    )
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
        and _patient_head_trauma_mention(text, child_patient)
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
            phrase_end = start + len(phrase)
            suffix = _clause_suffix(text, phrase_end)
            if not _is_negated(
                prefix, suffix
            ) and not _trailing_negation_in_clause(
                text, phrase_end, prefix
            ):
                if label not in matches:
                    matches.append(label)
                break
            start = text.find(phrase, start + len(phrase))

    # 胸痛族的插入修饰变体：「胸疼」「胸口有点疼」「胸前区隐隐作痛」
    # 「心口一阵一阵疼」：部位词与疼/痛之间的副词让精确子串失配，
    # 用受控正则兜底并复用同一套否定判定（第一百零一轮 [1]）
    if "胸痛" not in matches:
        for variant in _CHEST_PAIN_VARIANT_RE.finditer(text):
            prefix = _clause_prefix(text, variant.start())
            variant_end = variant.end()
            suffix = _clause_suffix(text, variant_end)
            if not _is_negated(
                prefix, suffix
            ) and not _trailing_negation_in_clause(
                text, variant_end, prefix
            ):
                matches.append("胸痛")
                break

    # 孕期状态可能只写在档案病史里（本次主诉只描述出血）：
    # 复用 _active_medical_text 的分句过滤把患者本人的现症/妊娠
    # 表述并入判定，家族史/否认表述会被过滤掉
    history_text = ""
    if isinstance(context, dict):
        try:
            history_text = _current_history_text(
                _active_medical_text(
                    context, _context_child_patient(context)
                )
            )
        except Exception:
            history_text = ""
    history_text = re.sub(
        r"妊娠期(?:糖尿病|高血压|肝内胆汁淤积症?|贫血|甲减|甲亢"
        r"|血小板减少)",
        "",
        history_text,
    )
    pregnancy_text = _PAST_OBSTETRIC_YEARS.sub("；", text)
    if history_text and history_text not in text:
        pregnancy_text = f"{pregnancy_text}；{history_text}"
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
            _pregnancy_term_positive(pregnancy_text, term)
            for term in PREGNANCY_TERMS
        )
        or _PREGNANCY_EVIDENCE_RE.search(pregnancy_text) is not None
        or _recent_amenorrhea_positive(pregnancy_text)
        or _CAESAREAN_RECENT_RE.search(pregnancy_text) is not None
        or _postpartum_recent_positive(pregnancy_text)
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
