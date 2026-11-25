"""用药安全提示（确定性规则）。

在模型回答之前，根据主诉、正在使用的药物和过敏史做一次规则筛查：
命中明确的重复用药、药物相互作用或过敏交叉风险时，给出可读提示。

规则只覆盖说明书/指南中常见的明确风险，不提供剂量调整或停药建议，
也不能替代医生或药师的判断。宁可少报，也不制造无依据的恐慌。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Tuple

from core.context import medication_context_text
#: 药名 → 组内规范名。别名用于匹配主诉和用药记录里的不同写法。
DRUG_GROUPS: Dict[str, Tuple[Tuple[str, Tuple[str, ...]], ...]] = {
    "warfarin": (
        ("华法林", ("华法林", "warfarin")),
    ),
    "nsaids": (
        ("阿司匹林", ("阿司匹林", "aspirin")),
        ("布洛芬", ("布洛芬", "ibuprofen")),
        ("双氯芬酸", ("双氯芬酸", "diclofenac")),
        ("萘普生", ("萘普生", "naproxen")),
        ("洛索洛芬", ("洛索洛芬", "loxoprofen")),
        ("美洛昔康", ("美洛昔康", "meloxicam")),
        ("塞来昔布", ("塞来昔布", "celecoxib")),
        ("吲哚美辛", ("吲哚美辛", "indomethacin")),
    ),
    "inr_raisers": (
        ("阿奇霉素", ("阿奇霉素", "azithromycin")),
        ("克拉霉素", ("克拉霉素", "clarithromycin")),
        ("红霉素", ("红霉素", "erythromycin")),
        ("氟康唑", ("氟康唑", "fluconazole")),
        ("伊曲康唑", ("伊曲康唑", "itraconazole")),
        ("伏立康唑", ("伏立康唑", "voriconazole")),
        ("甲硝唑", ("甲硝唑", "metronidazole")),
        ("复方磺胺甲噁唑", ("复方磺胺甲噁唑", "复方新诺明", "磺胺甲噁唑", "sulfamethoxazole")),
    ),
    "raas_blockers": (
        ("卡托普利", ("卡托普利", "captopril")),
        ("依那普利", ("依那普利", "enalapril")),
        ("贝那普利", ("贝那普利", "benazepril")),
        ("培哚普利", ("培哚普利", "perindopril")),
        ("雷米普利", ("雷米普利", "ramipril")),
        ("赖诺普利", ("赖诺普利", "lisinopril")),
        ("氯沙坦", ("氯沙坦", "losartan")),
        ("缬沙坦", ("缬沙坦", "valsartan")),
        ("厄贝沙坦", ("厄贝沙坦", "irbesartan")),
        ("替米沙坦", ("替米沙坦", "telmisartan")),
        ("坎地沙坦", ("坎地沙坦", "candesartan")),
        ("奥美沙坦", ("奥美沙坦", "olmesartan")),
    ),
    "potassium_sparing": (
        ("螺内酯", ("螺内酯", "spironolactone")),
        ("氨苯蝶啶", ("氨苯蝶啶", "triamterene")),
        ("阿米洛利", ("阿米洛利", "amiloride")),
        ("依普利酮", ("依普利酮", "eplerenone")),
        ("非奈利酮", ("非奈利酮", "finerenone")),
    ),
    "nitrates": (
        ("硝酸甘油", ("硝酸甘油", "nitroglycerin")),
        ("硝酸异山梨酯", ("硝酸异山梨酯", "isosorbide")),
        ("单硝酸异山梨酯", ("单硝酸异山梨酯", "isosorbide mononitrate")),
    ),
    "pde5": (
        ("西地那非", ("西地那非", "sildenafil")),
        ("他达拉非", ("他达拉非", "tadalafil")),
        ("伐地那非", ("伐地那非", "vardenafil")),
    ),
    "digoxin": (
        ("地高辛", ("地高辛", "digoxin")),
    ),
    "digoxin_toxicity": (
        ("胺碘酮", ("胺碘酮", "amiodarone")),
        ("维拉帕米", ("维拉帕米", "verapamil")),
    ),
    "amiodarone": (
        ("胺碘酮", ("胺碘酮", "amiodarone")),
    ),
    "fluoroquinolones": (
        ("左氧氟沙星", ("左氧氟沙星", "levofloxacin")),
        ("环丙沙星", ("环丙沙星", "ciprofloxacin")),
    ),
    "clopidogrel": (
        ("氯吡格雷", ("氯吡格雷", "clopidogrel")),
    ),
    "omeprazole": (
        ("奥美拉唑", ("奥美拉唑", "omeprazole")),
        ("埃索美拉唑", ("埃索美拉唑", "esomeprazole")),
    ),
    "metformin": (
        ("二甲双胍", ("二甲双胍", "metformin")),
    ),
    "contrast": (
        ("含碘造影剂", (
            "含碘造影剂", "碘造影", "造影剂", "增强ct", "增强 ct",
            "iodinated contrast",
        )),
    ),
    "cephalosporins": (
        ("头孢类抗菌药", ("头孢", "cephalosporin", "cef")),
    ),
    "acetaminophen": (
        ("对乙酰氨基酚", ("对乙酰氨基酚", "扑热息痛", "泰诺", "必理通", "散利痛", "paracetamol", "acetaminophen")),
    ),
    "corticosteroids": (
        ("泼尼松", ("泼尼松", "强的松", "prednisone")),
        ("地塞米松", ("地塞米松", "dexamethasone")),
        ("甲泼尼龙", ("甲泼尼龙", "methylprednisolone")),
        ("氢化可的松", ("氢化可的松", "hydrocortisone")),
        ("倍他米松", ("倍他米松", "betamethasone")),
    ),
    "statins": (
        ("阿托伐他汀", ("阿托伐他汀", "atorvastatin")),
        ("辛伐他汀", ("辛伐他汀", "simvastatin")),
        ("洛伐他汀", ("洛伐他汀", "lovastatin")),
        ("瑞舒伐他汀", ("瑞舒伐他汀", "rosuvastatin")),
        ("普伐他汀", ("普伐他汀", "pravastatin")),
        ("氟伐他汀", ("氟伐他汀", "fluvastatin")),
        ("匹伐他汀", ("匹伐他汀", "pitavastatin")),
    ),
    #: 主要经 CYP3A4 代谢的他汀；瑞舒伐他汀不在此列
    "cyp3a4_statins": (
        ("阿托伐他汀", ("阿托伐他汀", "atorvastatin")),
        ("辛伐他汀", ("辛伐他汀", "simvastatin")),
        ("洛伐他汀", ("洛伐他汀", "lovastatin")),
    ),
    "cyp3a4_inhibitors": (
        ("克拉霉素", ("克拉霉素", "clarithromycin")),
        ("红霉素", ("红霉素", "erythromycin")),
        ("伊曲康唑", ("伊曲康唑", "itraconazole")),
        ("伏立康唑", ("伏立康唑", "voriconazole")),
    ),
    "grapefruit": (
        ("西柚/葡萄柚", ("西柚", "葡萄柚", "grapefruit")),
    ),
    "levothyroxine": (
        ("左甲状腺素", ("左甲状腺素", "优甲乐", "levothyroxine")),
    ),
    "calcium": (
        ("钙剂", ("钙片", "碳酸钙", "葡萄糖酸钙", "枸橼酸钙", "calcium")),
    ),
    "iron": (
        ("铁剂", ("铁剂", "硫酸亚铁", "琥珀酸亚铁", "富马酸亚铁", "多糖铁", "iron")),
    ),
    "fluoroquinolones": (
        ("左氧氟沙星", ("左氧氟沙星", "levofloxacin")),
        ("莫西沙星", ("莫西沙星", "moxifloxacin")),
        ("环丙沙星", ("环丙沙星", "ciprofloxacin")),
        ("氧氟沙星", ("氧氟沙星", "ofloxacin")),
    ),
    "tetracyclines": (
        ("四环素", ("四环素", "tetracycline")),
        ("多西环素", ("多西环素", "doxycycline")),
        ("米诺环素", ("米诺环素", "minocycline")),
    ),
    "minerals": (
        ("钙剂", (
            "钙片", "碳酸钙", "葡萄糖酸钙", "枸橼酸钙",
            "calcium", "calcium carbonate", "calcium citrate",
        )),
        ("铁剂", (
            "铁剂", "硫酸亚铁", "琥珀酸亚铁", "富马酸亚铁", "多糖铁",
            "iron", "ferrous sulfate", "ferrous fumarate",
        )),
        ("铝碳酸镁", ("铝碳酸镁", "aluminum", "aluminium")),
        ("氢氧化铝", ("氢氧化铝", "aluminum hydroxide")),
    ),
    "ssris": (
        ("舍曲林", ("舍曲林", "sertraline")),
        ("帕罗西汀", ("帕罗西汀", "paroxetine")),
        ("氟西汀", ("氟西汀", "fluoxetine")),
        ("西酞普兰", ("西酞普兰", "citalopram")),
        ("艾司西酞普兰", ("艾司西酞普兰", "escitalopram")),
    ),
    "methotrexate": (
        ("甲氨蝶呤", ("甲氨蝶呤", "methotrexate")),
    ),
    "allopurinol": (
        ("别嘌醇", ("别嘌醇", "allopurinol")),
    ),
    "azathioprine": (
        ("硫唑嘌呤", ("硫唑嘌呤", "azathioprine")),
    ),
    "opioids": (
        ("吗啡", ("吗啡", "morphine")),
        ("羟考酮", ("羟考酮", "oxycodone")),
        ("可待因", ("可待因", "codeine")),
        ("曲马多", ("曲马多", "tramadol")),
    ),
    "benzodiazepines": (
        ("地西泮", ("地西泮", "安定", "diazepam")),
        ("阿普唑仑", ("阿普唑仑", "alprazolam")),
        ("劳拉西泮", ("劳拉西泮", "lorazepam")),
        ("氯硝西泮", ("氯硝西泮", "clonazepam")),
        ("艾司唑仑", ("艾司唑仑", "estazolam")),
        ("咪达唑仑", ("咪达唑仑", "midazolam")),
    ),
    "sulfonylureas": (
        ("格列本脲", ("格列本脲", "glibenclamide")),
        ("格列美脲", ("格列美脲", "glimepiride")),
        ("格列齐特", ("格列齐特", "gliclazide")),
    ),
    "insulin": (
        ("胰岛素", ("胰岛素", "insulin")),
    ),
    "lithium": (
        ("碳酸锂", ("碳酸锂", "锂盐", "lithium")),
    ),
    "thiazides": (
        ("氢氯噻嗪", ("氢氯噻嗪", "hydrochlorothiazide")),
        ("吲达帕胺", ("吲达帕胺", "indapamide")),
    ),
    "alcohol": (
        ("酒精", ("喝酒", "饮酒", "喝了酒", "啤酒", "白酒", "红酒", "药酒",
                  "alcohol", "beer", "wine")),
    ),
}

#: 明确的相互作用：两组药物同时出现时提示。
INTERACTION_RULES: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "华法林与非甾体抗炎药",
        "warfarin",
        "nsaids",
        "两者合用会明显增加出血风险，尤其是消化道出血。请让开药医生或药师确认是否需要调整用药或加用保护措施。",
    ),
    (
        "华法林与部分抗感染药",
        "warfarin",
        "inr_raisers",
        "大环内酯类、唑类抗真菌药、甲硝唑或磺胺类药物可能增强华法林作用，合用期间通常需要监测凝血指标。",
    ),
    (
        "降压药与保钾利尿剂",
        "raas_blockers",
        "potassium_sparing",
        "ACEI/ARB 类降压药与螺内酯等保钾利尿剂合用可能升高血钾，肾功能不全者风险更高，请医生评估并监测血钾。",
    ),
    (
        "硝酸酯与 PDE5 抑制剂",
        "nitrates",
        "pde5",
        "硝酸酯类药物与西地那非、他达拉非等合用可能引起严重低血压，属于需要避免的常见组合，请立即咨询医生或药师。",
    ),
    (
        "地高辛与胺碘酮/维拉帕米",
        "digoxin",
        "digoxin_toxicity",
        "合用可能升高地高辛血药浓度，增加中毒风险（恶心、心悸、视力异常等），通常需要监测血药浓度并调整剂量。",
    ),
    (
        "华法林与胺碘酮",
        "warfarin",
        "amiodarone",
        "胺碘酮会抑制华法林代谢，可能显著升高 INR 并增加出血风险；合用或调整剂量时请医生监测凝血指标。",
    ),
    (
        "华法林与氟喹诺酮类",
        "warfarin",
        "fluoroquinolones",
        "左氧氟沙星、环丙沙星等氟喹诺酮类可能增强华法林作用，合用期间请医生监测 INR 和出血迹象。",
    ),
    (
        "氯吡格雷与奥美拉唑",
        "clopidogrel",
        "omeprazole",
        "奥美拉唑/埃索美拉唑可能降低氯吡格雷的抗血小板作用；如需要胃黏膜保护，请让医生选择其他质子泵抑制剂。",
    ),
    (
        "二甲双胍与含碘造影剂",
        "metformin",
        "contrast",
        "使用含碘造影剂前后通常需要暂停二甲双胍并复查肾功能，请提前告知影像科和开药医生。",
    ),
    (
        "非甾体抗炎药与糖皮质激素",
        "nsaids",
        "corticosteroids",
        "两者合用会增加胃肠道溃疡和出血风险，请医生评估是否需要胃黏膜保护。",
    ),
    (
        "他汀与部分抗感染药",
        "cyp3a4_statins",
        "cyp3a4_inhibitors",
        "克拉霉素、红霉素、伊曲康唑等强效 CYP3A4 抑制剂会升高部分他汀的血药浓度，增加肌痛和横纹肌溶解风险；请让医生评估是否换药或暂停。",
    ),
    (
        "他汀与西柚",
        "cyp3a4_statins",
        "grapefruit",
        "西柚/葡萄柚会抑制 CYP3A4，可能升高部分他汀的血药浓度，增加肌肉损伤风险；服药期间请避免大量饮用西柚汁。",
    ),
    (
        "左甲状腺素与钙/铁/铝剂",
        "levothyroxine",
        "minerals",
        "钙剂、铁剂和含铝抗酸药会影响左甲状腺素吸收；通常需要间隔至少 4 小时服用，具体请遵医嘱。",
    ),
    (
        "喹诺酮类与矿物质",
        "fluoroquinolones",
        "minerals",
        "钙、铁、镁、铝会与喹诺酮类抗菌药结合，降低吸收；两者通常需要间隔 2 小时以上，请按说明书或医嘱服用。",
    ),
    (
        "四环素类与矿物质",
        "tetracyclines",
        "minerals",
        "钙、铁、镁、铝会降低四环素类抗菌药的吸收；两者通常需要间隔 2 小时以上，请按说明书或医嘱服用。",
    ),
    (
        "SSRI 与非甾体抗炎药",
        "ssris",
        "nsaids",
        "SSRI 类抗抑郁药与阿司匹林/布洛芬等合用会增加消化道出血风险，尤其是老年人；请医生评估是否需要胃黏膜保护。",
    ),
    (
        "甲氨蝶呤与非甾体抗炎药",
        "methotrexate",
        "nsaids",
        "非甾体抗炎药可能减少甲氨蝶呤排泄，增加骨髓抑制和肝肾毒性风险；合用需要医生密切监测。",
    ),
    (
        "甲氨蝶呤与磺胺类",
        "methotrexate",
        "sulfonamides",
        "磺胺类药物可能增强甲氨蝶呤毒性，属于需要谨慎避免的组合；请让开药医生确认。",
    ),
    (
        "别嘌醇与硫唑嘌呤",
        "allopurinol",
        "azathioprine",
        "别嘌醇会显著升高硫唑嘌呤活性代谢物，可能导致严重骨髓抑制；通常需要避免合用或大幅减量并由专科医生管理。",
    ),
    (
        "阿片类与苯二氮䓬类",
        "opioids",
        "benzodiazepines",
        "阿片类镇痛药与地西泮等苯二氮䓬类合用会加重镇静和呼吸抑制，属于高风险组合；请让医生评估并避免自行加量。",
    ),
    (
        "磺脲类降糖药与酒精",
        "sulfonylureas",
        "alcohol",
        "酒精可能增强磺脲类降糖药的作用，引起低血糖；用药期间请限制饮酒并注意监测血糖。",
    ),
    (
        "胰岛素与酒精",
        "insulin",
        "alcohol",
        "酒精可能掩盖低血糖症状并增加低血糖风险；用药期间请限制饮酒，并随身携带含糖食品。",
    ),
    (
        "降压药与非甾体抗炎药",
        "raas_blockers",
        "nsaids",
        "ACEI/ARB 类降压药与阿司匹林/布洛芬等合用可能削弱降压效果并增加肾功能损害风险，尤其在脱水或老年人中；请医生评估。",
    ),
    (
        "锂盐与非甾体抗炎药",
        "lithium",
        "nsaids",
        "非甾体抗炎药可能升高锂盐血药浓度，增加锂中毒风险；合用需要监测血锂浓度。",
    ),
    (
        "锂盐与 ACEI/ARB",
        "lithium",
        "raas_blockers",
        "ACEI/ARB 类降压药可能升高锂盐血药浓度，增加锂中毒风险；请医生评估并监测。",
    ),
    (
        "锂盐与噻嗪类利尿剂",
        "lithium",
        "thiazides",
        "噻嗪类利尿剂会减少锂的肾脏排泄，可能引起锂中毒；"
        "合用或调整剂量时请医生监测血锂浓度和中毒症状。",
    ),
)

ALCOHOL_TERMS = (
    "喝酒", "喝了酒", "喝了点酒", "喝点酒", "少量饮酒", "饮酒",
    "啤酒", "白酒", "红酒", "药酒",
    "alcohol", "beer", "wine",
)

#: 紧贴酒类词前的否定/戒断表达（「不喝酒」「不喝啤酒」「已经戒酒」）
_ALCOHOL_NEGATIONS = re.compile(
    r"(?:不|没|没有|无|未|不再|已经?戒|戒了|忌|从不|别|勿|禁止)"
    r"(?:再|怎么|太|会|能|想|敢|喝|饮)?\s*$"
)


#: 「不得不喝白酒」「不能不喝红酒」表示实际会喝，不是戒酒
_ALCOHOL_DOUBLE_NEGATIONS = re.compile(
    r"(?:不得不|不能不|忍不住|没法不|无法不)(?:喝|饮)?\s*$"
)
#: A 不 A 疑问（「能不能喝酒」「可不可以喝」）不是否定，按饮酒语境处理
_ALCOHOL_UNCERTAIN_QUESTION = re.compile(
    r"(?:能|可)不能(?:喝|饮)?\s*$|可不可以(?:喝|饮)?\s*$|"
    r"要不要(?:喝|饮)?\s*$|会不会(?:喝|饮)?\s*$|"
    r"是否可以(?:喝|饮)?\s*$|是否(?:喝|饮)?\s*$|敢不敢(?:喝|饮)?\s*$"
)

#: 「不喝酒就难受/睡不着」是在描述饮酒依赖，仍视为饮酒语境
_ALCOHOL_DEPENDENCE_WORDS = (
    "难受", "睡不着", "头痛", "失眠", "焦虑", "手抖", "心慌",
)


def _has_alcohol_use(text: str) -> bool:
    """文本是否提到当前饮酒；否定或戒酒表述不算。"""
    for term in ALCOHOL_TERMS:
        if term.isascii():
            # 英文按单词边界匹配，避免 wine 命中 swine
            pattern = re.compile(
                rf"(?<![a-z0-9_]){re.escape(term)}s?(?![a-z0-9_])"
            )
            matches = [match.start() for match in pattern.finditer(text)]
        else:
            matches = []
            start = text.find(term)
            while start != -1:
                matches.append(start)
                start = text.find(term, start + len(term))
        for position in matches:
            prefix = text[max(0, position - 8):position]
            if _ALCOHOL_DOUBLE_NEGATIONS.search(prefix):
                return True
            if _ALCOHOL_UNCERTAIN_QUESTION.search(prefix):
                # 「能不能喝酒」是疑问，不是「不能喝酒」
                return True
            if not _ALCOHOL_NEGATIONS.search(prefix):
                return True
            suffix = text[position + len(term):position + len(term) + 8]
            if "就" in suffix and any(
                word in suffix for word in _ALCOHOL_DEPENDENCE_WORDS
            ):
                return True
    return False

#: 过敏史 → (展示名, 过敏词, 需要警惕的药物组)
ALLERGY_RULES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "青霉素类",
        (
            "青霉素", "阿莫西林", "氨苄西林", "哌拉西林",
            "penicillin", "amoxicillin", "ampicillin", "piperacillin",
        ),
        ("penicillins", "cephalosporins"),
    ),
    (
        "头孢类",
        ("头孢", "cephalosporin"),
        ("cephalosporins",),
    ),
    (
        "磺胺类",
        ("磺胺", "复方新诺明", "sulfa", "sulfonamide"),
        ("sulfonamides",),
    ),
    (
        "非甾体抗炎药",
        (
            "非甾体抗炎药", "非甾体类抗炎药", "非甾体消炎药",
            "阿司匹林", "布洛芬", "双氯芬酸", "萘普生",
            "nsaid", "nsaids",
            "aspirin", "ibuprofen", "diclofenac",
        ),
        ("nsaids",),
    ),
)

#: 青霉素/头孢需要单独的组，避免与 inr_raisers 中的磺胺混在一起。
DRUG_GROUPS["penicillins"] = (
    ("青霉素", ("青霉素", "penicillin")),
    ("阿莫西林", ("阿莫西林", "amoxicillin")),
    ("氨苄西林", ("氨苄西林", "ampicillin")),
    ("哌拉西林", ("哌拉西林", "piperacillin")),
)
DRUG_GROUPS["sulfonamides"] = (
    ("复方磺胺甲噁唑", ("复方磺胺甲噁唑", "复方新诺明", "磺胺甲噁唑", "sulfamethoxazole")),
    ("磺胺类", ("磺胺", "sulfonamide", "sulfonamides")),
)
DRUG_GROUPS["loop_diuretics"] = (
    ("呋塞米", ("呋塞米", "速尿", "furosemide")),
    ("托拉塞米", ("托拉塞米", "torasemide")),
    ("布美他尼", ("布美他尼", "bumetanide")),
)
#: 慢病相关药物组（供慢病-用药规则复用；不参与药物相互作用判断）
DRUG_GROUPS["beta_blockers_nonselective"] = (
    ("普萘洛尔", ("普萘洛尔", "心得安", "propranolol")),
    ("卡维地洛", ("卡维地洛", "carvedilol")),
    ("拉贝洛尔", ("拉贝洛尔", "labetalol")),
    ("噻吗洛尔", ("噻吗洛尔", "timolol")),
    ("纳多洛尔", ("纳多洛尔", "nadolol")),
    ("索他洛尔", ("索他洛尔", "sotalol")),
)
DRUG_GROUPS["anticholinergics"] = (
    ("阿托品", ("阿托品", "atropine")),
    ("山莨菪碱", ("山莨菪碱", "654-2", "anisodamine")),
    ("东莨菪碱", ("东莨菪碱", "scopolamine")),
    ("奥昔布宁", ("奥昔布宁", "oxybutynin")),
    ("托特罗定", ("托特罗定", "tolterodine")),
    ("索利那新", ("索利那新", "solifenacin")),
    ("苯海索", ("苯海索", "trihexyphenidyl")),
    ("苯海拉明", ("苯海拉明", "diphenhydramine")),
    ("氯苯那敏", ("氯苯那敏", "扑尔敏", "chlorphenamine", "chlorpheniramine")),
    ("异丙嗪", ("异丙嗪", "promethazine")),
    ("颠茄", ("颠茄", "belladonna")),
    ("阿米替林", ("阿米替林", "amitriptyline")),
    ("多塞平", ("多塞平", "doxepin")),
)
DRUG_GROUPS["anticoagulants_antiplatelets"] = (
    ("华法林", ("华法林", "warfarin")),
    ("阿司匹林", ("阿司匹林", "aspirin")),
    ("氯吡格雷", ("氯吡格雷", "clopidogrel")),
    ("替格瑞洛", ("替格瑞洛", "ticagrelor")),
    ("利伐沙班", ("利伐沙班", "rivaroxaban")),
    ("阿哌沙班", ("阿哌沙班", "apixaban")),
    ("艾多沙班", ("艾多沙班", "edoxaban")),
    ("达比加群", ("达比加群", "dabigatran")),
    ("肝素", ("肝素", "heparin")),
    ("依诺肝素", ("依诺肝素", "enoxaparin")),
)

#: 同时使用两种不同 NSAID 属于重复用药。
DUPLICATE_GROUPS: Tuple[Tuple[str, str, str, str], ...] = (
    (
        "重复使用非甾体抗炎药",
        "nsaids",
        "同时使用两种不同的非甾体抗炎药（如布洛芬与双氯芬酸）不会增强疗效，反而会增加胃肠道出血和肾损伤风险。请只保留一种，并先咨询医生或药师。",
        "warning",
    ),
)

MAX_ALERTS = 5


def _flatten(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        return "；".join(_flatten(item) for item in value)
    if isinstance(value, dict):
        return "；".join(f"{key}={_flatten(item)}" for key, item in value.items())
    return str(value).strip()


_MEDICATION_VERBS = (
    "服用", "口服", "吃", "用", "注射", "打", "含服", "滴", "喷", "喝", "饮",
)
#: 本身也是普通词语的药名，短别名需要用药语境才认定
_AMBIGUOUS_SHORT_ALIASES = {"安定"}
_NEGATED_USE_PREFIX = re.compile(
    r"(?:没有|没吃|没喝|没在吃|没再吃|未用|未服|未|否认|并非|无需|"
    # 「要不要/用不用/需不需要/吃不吃」是 A-not-A 问句：不能把后半
    # 截当成否定用药，否则相互作用/过敏提示被静默跳过（第七十九轮）
    r"(?<!用)不用|(?<!要)不要|(?<!需)不需要|不必|无|(?<!吃)不吃|"
    r"(?<!服)不服用|不再吃|不在吃|没吃了?"
    # 带谓词/完成体的否定：「没用过/没做过化疗/没有感染过HIV」
    r"|没(?:有)?(?:再|在)?(?:吃|服用|口服|服|用|喝|注射|打|做|接受|"
    r"进行|感染|得)(?:过|了)?)"
    r"[^，。；,.!?！？\n]{0,4}$"
)
#: 双重否定表示仍在用药：「不得不吃华法林/不能不吃华法林」
_USE_DOUBLE_NEGATION = re.compile(
    r"(?:不得不|不能不|没法不|无法不|忍不住|不是不|并非不|"
    r"(?:不是|并非|并不是)(?:没|没有|未))"
    r"(?:再|继续)?(?:吃|服用|服|用|口服|注射|打|喝|饮)?\s*$"
)
#: 英文双重否定：「can't stop taking warfarin」
_USE_DOUBLE_NEGATION_EN = re.compile(
    r"(?:can'?t|cannot|can not|won'?t|will not|unable to)\s+"
    r"(?:stop|quit|cease)\s+(?:taking|using)\s*$",
    re.IGNORECASE,
)
#: 单次漏服（今天没吃/漏服了一次）仍属当前用药，不是未用药
_DOSE_MISSED_PREFIX = re.compile(
    r"(?:今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|前天|"
    r"前晚|刚才|这次|这一顿|这顿|"
    r"[0-9一二两三四五六七八九十]+(?:天|日)来?|几天|数天|两三天|"
    r"[0-9一二两三四五六七八九十]+周来?|几周)"
    r"[^，。；,.!?！？\n]{0,6}"
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|没服|未服|没有服|"
    r"没口服|漏服用?|忘(?:记)?服用?|少吃)"
)
#: 宾语前置的近期漏服（没吃华法林三天了/未服用华法林两周）：时长
#: 在药名之后同样按当前用药处理（第九十四轮 [7]）。中间片段是
#: 漏服宾语，用于核对漏的是不是本药（第九十五轮 [16]）
_DOSE_MISSED_OBJECT_FIRST = re.compile(
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|没服|未服|没有服|"
    r"没口服|漏服用?|忘(?:记)?服用?)"
    r"([^，。；,.!?！？\n]{0,12}?)"
    r"[0-9一二两三四五六七八九十]+(?:天|日|周)"
)


_DOSE_MISSED_TIME_OBJ_AFTER = re.compile(
    r"(?:今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|"
    r"昨夜|前天|前晚|刚才|这次|这一顿|这顿|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?|几天|数天|两三天|几周)"
    r"[^，。；,.!?！？\n]{0,8}?"
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|"
    r"没服|未服|漏服用?)"
    r"\s*([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})"
)
_DOSE_MISSED_TIME_OBJ_BEFORE = re.compile(
    r"([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*(?:也|都|还|仍)?\s*"
    r"(?:今天|今早|今晨|早上|中午|晚上|今晚|昨天|"
    r"昨晚|昨夜|前天|前晚|刚才|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?)"
    r"[^，。；,.!?！？\n]{0,4}?"
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|漏服用?)"
)

#: 纯代词/副词宾语（「我已经」「都」）：整项只是主语或助词而不是
#: 药名；不能对整项做裸子串过滤，否则他汀/他克莫司/他达拉非会被
#: 「他」误删（第九十九轮 [2][5]）
_DOSE_OBJ_PURE_STOP_RE = re.compile(
    r"^(?:(?:我们|我|你|他|她|本人|已经|已|还|也|都|仍|刚|才|就|又|再)"
    r"\s*)+$"
)


def _missed_dose_obj_items(raw: str) -> list:
    """漏服宾语的药名项：转折词取末项，并列词逐项拆分。

    「华法林和二甲双胍」→[华法林, 二甲双胍]：任一命中本药都算
    本药漏服（第九十八轮 [4]）。只有整项都是代词/副词（「我已经」）
    才丢弃；不能做裸子串过滤，否则含「他」的药名（他汀/他克莫司/
    他达拉非）会被整项误删（第九十九轮 [2][5]）。
    """
    raw = str(raw or "").strip()
    if any(word in raw for word in ("但", "而")):
        raw = re.split(r"[但而]", raw)[-1]
    items = []
    for piece in re.split(r"[和及与或]", raw):
        obj = re.sub(
            r"^[了啦啊呀但而和及与或的着]+|[了啦啊呀但而和及与或的着]+$",
            "",
            piece.strip(),
        )
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


def _clean_missed_dose_obj(raw: str) -> str:
    items = _missed_dose_obj_items(raw)
    return items[0] if items else ""


#: 时间词在主语之前的漏服（今天华法林没吃）
_DOSE_MISSED_TIME_LEAD = re.compile(
    r"(?:今天|今早|今晨|早上|中午|晚上|今晚|昨天|昨晚|昨夜|前天|前晚|"
    r"刚才|"
    r"[0-9一二两三四五六七八九十]+天来?|"
    r"[0-9一二两三四五六七八九十]+周来?)"
    r"\s*([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*"
    r"(?:也|都|还|仍)?\s*"
    r"(?:没吃|未吃|没有吃|没服用|未服用|没有服用|漏服用?)"
)


def _missed_dose_time_names_term(text: str, alias_lower: str) -> bool:
    """时间前置漏服的对象是否本药（今天没吃华法林/华法林今天没吃）。"""
    text = str(text or "")

    def names(alias: str, items) -> bool:
        return any(
            alias in item.lower() or item.lower() in alias
            for item in items
        )

    # 宾语在时间词之前（华法林今天没吃）：该形态独占判定，
    # 非本药时不回退去绑定句尾他药（第九十八轮 [4]）
    for match in _DOSE_MISSED_TIME_OBJ_BEFORE.finditer(text):
        items = _missed_dose_obj_items(match.group(1))
        if not items:
            continue
        return names(alias_lower, items)
    # 宾语在动词之后（今天没吃华法林和二甲双胍）
    for match in _DOSE_MISSED_TIME_OBJ_AFTER.finditer(text):
        items = _missed_dose_obj_items(match.group(1))
        if not items:
            continue
        return names(alias_lower, items)
    # 时间词在主语之前（今天华法林没吃）
    for match in _DOSE_MISSED_TIME_LEAD.finditer(text):
        items = _missed_dose_obj_items(match.group(1))
        if items and names(alias_lower, items):
            return True
    return False


def _missed_dose_names_term(text: str, alias_lower: str) -> bool:
    """宾语前置漏服的对象是否就是本药（阿司匹林没吃三天了→否）。"""
    text = str(text or "")
    alias_lower = str(alias_lower or "").lower()
    if not alias_lower:
        return False
    for match in _DOSE_MISSED_OBJECT_FIRST.finditer(text):
        obj = (match.group(1) or "").strip().lower()
        if obj and (alias_lower in obj or obj in alias_lower):
            return True
        if obj:
            continue
        # 「没吃三天了」：宾语在动词之前（华法林没吃三天了）
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
        ).strip().lower()
        if obj and (alias_lower in obj or obj in alias_lower):
            return True
    return _missed_dose_time_names_term(text, alias_lower)
#: 依从性限定词：「没有按时吃/未规律服用」仍是当前用药，不是未用药
_ADHERENCE_USE_PREFIX = re.compile(
    r"(?:按时|规律|按医嘱|遵医嘱|每天|天天|坚持|准时|定点|按顿|按需)"
)
#: 「没有停用」「不要停药」表示仍在用药，不能当作未用药
_NEGATED_STOP_PREFIX = re.compile(
    r"(?:没有|没|未|无|不要|不用|不必|不需要|无需|避免|并非"
    r"|不能|不可|不宜|不让|不应|不准|别"
    # 医嘱式否定：「不擅自/请勿/切勿/不得停用华法林」= 仍在用药
    r"|不擅自|不自行|不随意|不轻易|不随便|不推荐|不主张"
    r"|请勿|切勿|切忌|不得(?!不)|不允许|禁止)"
    # 裸「停」也要覆盖：「没有停华法林」「不要停」都是仍在用药
    r"[^，。；,.!?！？\n]{0,4}(?:停用|停药|停服|停止服用|不再服用|停过|间断|中断|停)\s*$"
)
#: 明确停药/停服的表述才算当前未用药
_STOPPED_USE_PREFIX = re.compile(
    r"(?:已经|已|刚刚|刚|最近|早就|现已|目前)?\s*"
    r"(?:停用了|停用|停药|停服|停止服用|不再服用|停了|停掉|已停)\s*$"
)
#: 药名之后的停药表述：「华法林已经停用两个月了」
#: 只允许时间表达式出现在药名与停药动词之间，避免「准备停药/头痛停了」
_STOPPED_USE_SUFFIX = re.compile(
    r"^(?:前段时间|前阵子|前一阵|上个月|上周|上上个月|昨天|前天|"
    r"去年|前年|上年|"
    r"前几天|前几周|前几个月|前几年|最近|目前|现在|"
    r"[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|天|周|年)"
    r"(?:多)?(?:前|以来)?)?"
    r"\s*(?:我|本人|现在|目前)?\s*"
    r"(?:已经|已|刚刚|刚|早就|就|才|后来|之后)*\s*"
    r"(?:停用了|停用(?!时间|时候|日子|那天|没|不)"
    r"|停药(?!时间|时候|日子|那天|没|不)"
    r"|停服(?!时间|时候|日子|那天|没|不)"
    r"|停止服用(?!时间|时候|日子|那天|没|不)|不再服用|停了"
    # 「停的时间不长/停药没多久/停药不长时间」不是已停药：近期
    # 停药仍按当前用药检查（第九十四/九十五轮）
    r"|停的(?!时间|时候|日子|那天|话)|停掉"
    r"|已停药(?!时间|时候|日子|那天|没|不)"
    r"|已停用(?!时间|时候|日子|那天|没|不)"
    r"|已停服(?!时间|时候|日子|那天|没|不)"
    r"|已停止(?!时间|时候|日子|那天|没|不)"
    r"|已停(?!用|药|服|止|时间|时候|日子|那天)"
    # 药名后的完成体否定同样是停药：「华法林我不吃了」
    r"|不吃了|不再吃了|不用吃了"
    # 药名后置的否定：「阿司匹林我没在吃/我没有在吃/没吃过」
    r"|没在吃|没在服用|没在吃药|不在吃|未在服用|未服用"
    r"|没有在吃|没有在服用|没有吃|没有服用|没吃过(?!量)"
    r"|没有用过|没用过|未服用过)"
    # 「还没吃完/没吃够」表示仍在服用，不是停药
    r"(?![完够满])"
    r"(?!\s*(?:可以吗|行吗|好吗|行不行|可以么|是否|吗|不行|会怎样))"
)
#: 药名之后的既往用药：「阿司匹林以前吃过」「阿司匹林之前用过」。
#: 必须带明确的既往时间词：裸「吃过了」是今天已服药（依从），不是停药
_PAST_USE_SUFFIX = re.compile(
    r"^(?:\s*(?:我|本人))?\s*(?:以前|之前|曾经|曾|既往|过去)\s*"
    r"(?:吃过|服用过|服过|用过)(?!量)"
)
#: 「想/打算/准备停用华法林」「打算不吃华法林」是意图，当前仍在用药
_INTENT_STOP_PREFIX = re.compile(
    r"(?:想|打算|准备|计划|考虑|要不要|是否|让|叫我|建议|嘱咐|叮嘱|要求|医嘱"
    # 情态疑问「能/可以/可否停用X吗」是意向询问，当前仍在用药：
    # 与药名后语序的 _INTENT_STOP_SUFFIX 对齐（第一百轮 [3]）
    r"|能|能否|能不能|可以|可否|该不该|需不需要|应不应该)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:停用|停药|停服|停了|停掉|不吃了|不吃|不再吃|不用吃了|不用吃|"
    r"不服|不喝|不服用|不再服用|不用服用|不再服|不口服|"
    r"不做化疗|不化疗|不再化疗|停止化疗|暂停化疗)\s*$"
)
#: 「准备停药/能停药吗」是意向或疑问，不能当作已停药
_INTENT_STOP_SUFFIX = re.compile(
    r"^[^，。；,.!?！？\n]{0,4}"
    r"(?:准备|打算|计划|想要|想|考虑|能|能否|能不能|可以|可否|"
    r"是否|要不要|该不该|何时|什么时候)\s*"
    r"(?:停用|停药|停服|停了|停掉|不吃了|不吃|不再吃|不用吃了|"
    r"不服用|不再服用|不用服用|不再服|"
    r"不做化疗|不化疗|不再化疗)"
)
#: 药名之前带时长的停药表述：「我已经停用两个月的华法林」
_STOPPED_USE_PREFIX_LONG = re.compile(
    r"(?:已经|已|早就|刚刚|刚)?\s*(?:停用|停药|停服|停止服用|不再服用|停了)"
    r"[^，。；,.!?！？]{0,6}?(?:月|天|周|年)(?:了|多)?\s*的?\s*$"
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
_LONG_STOP_SELF_REFERENCES = (
    "这个药", "该药", "此药", "这种药", "那种药", "它", "这个", "该",
)
#: 药物类别词 → 同类药名线索（只有类别一致才算自指）
_LONG_STOP_CATEGORY_HINTS = {
    "抗凝药": (
        "warfarin", "heparin", "rivaroxaban", "dabigatran",
        "apixaban", "edoxaban", "enoxaparin", "clopidogrel",
        "aspirin", "抗凝", "华法林", "肝素", "利伐沙班", "达比加群",
        "阿哌沙班", "依诺肝素", "氯吡格雷", "阿司匹林",
    ),
    "抗凝药物": (
        "warfarin", "heparin", "rivaroxaban", "dabigatran",
        "apixaban", "edoxaban", "enoxaparin", "clopidogrel",
        "aspirin", "抗凝", "华法林", "肝素", "利伐沙班", "达比加群",
        "阿哌沙班", "依诺肝素", "氯吡格雷", "阿司匹林",
    ),
    "化疗药": (
        "methotrexate", "cisplatin", "fluorouracil", "paclitaxel",
        "cyclophosphamide", "doxorubicin", "化疗", "甲氨蝶呤", "顺铂",
        "氟尿嘧啶", "紫杉醇", "环磷酰胺", "多柔比星",
    ),
    "化疗药物": (
        "methotrexate", "cisplatin", "fluorouracil", "paclitaxel",
        "cyclophosphamide", "doxorubicin", "化疗", "甲氨蝶呤", "顺铂",
        "氟尿嘧啶", "紫杉醇", "环磷酰胺", "多柔比星",
    ),
    "免疫抑制剂": (
        "methotrexate", "cyclosporine", "tacrolimus", "azathioprine",
        "免疫抑制", "甲氨蝶呤", "环孢素", "他克莫司", "硫唑嘌呤",
    ),
    "激素": (
        "prednisone", "prednisolone", "dexamethasone",
        "methylprednisolone", "hydrocortisone", "激素", "泼尼松",
        "强的松", "地塞米松", "甲泼尼龙", "氢化可的松", "糖皮质激素",
    ),
    "糖皮质激素": (
        "prednisone", "prednisolone", "dexamethasone",
        "methylprednisolone", "hydrocortisone", "激素", "泼尼松",
        "强的松", "地塞米松", "甲泼尼龙", "氢化可的松", "糖皮质激素",
    ),
}


def _normalize_long_stop_token(token: str) -> str:
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


def _long_stop_token_is_determiner(token: str) -> bool:
    """宾语是否只是辅助词/代词（没有点名任何药名）。"""
    token = str(token or "").strip()
    changed = True
    while changed and token:
        changed = False
        if token in _LONG_STOP_SELF_REFERENCES:
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
    return not token or token in _LONG_STOP_SELF_REFERENCES


def _segment_ends_with_drug_name(segment: str) -> bool:
    """分句是否以药名结尾（「阿司匹林，停了半年」）。"""
    tail = re.sub(
        r"[（(【「『][^）)】」』]{0,20}[）)】」』]\s*$", "",
        str(segment or "").strip(),
    ).rstrip("，,、;； \t")
    if not tail:
        return False
    tail = tail[-16:]
    for group_items in DRUG_GROUPS.values():
        for _canonical, aliases in group_items:
            if any(
                tail.endswith(alias.lower()) for alias in aliases
            ):
                return True
    return False


#: 当前药自身的剂型后缀（「阿司匹林肠溶片」的「肠溶片」）：窗口从
#: 药名之后开始，首段残留的剂型词不是第二种药（第八十四轮）
_DRUG_FORM_SUFFIX_RE = re.compile(
    r"^(?:肠溶|缓释|控释|分散|咀嚼|泡腾|软|硬)?"
    r"(?:片剂|胶囊剂|胶囊|颗粒|片|丸|散|口服液|滴剂|喷雾剂|贴剂|"
    r"注射液|混悬液|糖浆)"
)


def _window_mentions_other_drug(prefix: str, alias_lower: str) -> bool:
    """停药动词前的文本是否点名了另一种药（不是当前药）。"""
    segments = re.split(
        r"[，。；;,.!?！？\n]",
        normalize_brand_drug_names(str(prefix or "")),
    )
    for index, segment in enumerate(segments):
        lowered = segment.lower()
        if not lowered.strip():
            continue
        if index == 0:
            # 首段紧接当前药别名：剥掉本身的剂型词再判断
            stripped = _DRUG_FORM_SUFFIX_RE.sub("", segment.strip())
            if not stripped:
                continue
            segment = stripped
            lowered = segment.lower()
        if clause_mentions_drug_clause(segment):
            return True
        for group_items in DRUG_GROUPS.values():
            for _canonical, aliases in group_items:
                for alias in aliases:
                    alias_l = str(alias).lower()
                    if len(alias_l) < 2 or alias_l in alias_lower:
                        continue
                    if alias_l in lowered:
                        return True
    return False


def _long_stop_applies_to_alias(window: str, alias_lower: str) -> bool:
    """长窗口里的远期停药是否指向当前药（而不是另一种药）。

    「华法林没吃完，阿司匹林停药一年了」：窗口扫到后一分句的停药，
    但停药动词前点名的是阿司匹林，不能据此把华法林判为已停用。
    """
    window = window or ""
    alias_lower = str(alias_lower or "").lower()
    for match in _LONG_STOP_CONTEXT.finditer(window):
        after_span = window[match.start(): match.end()]
        after = _STOP_VERB_AFTER_OBJECT_RE.search(after_span)
        if after is not None:
            after_raw = after.group(1).strip()
            if not _long_stop_token_is_determiner(after_raw):
                after_token = _normalize_long_stop_token(after_raw)
                if (
                    len(after_token) >= 2
                    and not _STOP_TIME_OBJECT_RE.match(after_token)
                    and after_token not in _LONG_STOP_SELF_REFERENCES
                ):
                    if (
                        after_token in alias_lower
                        or alias_lower in after_token
                    ):
                        # 「停用华法林一年了」：停药宾语就是本药
                        return True
                    # 「停用阿司匹林一年了」：停药属于宾语点名的
                    # 另一种药，不能把当前药判为已停用（第九十九轮 [1]）
                    continue
        segments = re.split(
            r"[，。；;,.!?！？\n]", window[: match.start()]
        )
        before = segments[-1]
        if not before.strip() and len(segments) >= 2:
            # 「阿司匹林，停了半年」：药名在逗号前的分句；
            # 「大小便正常，已经停药」里的普通描述不是药名
            if _segment_ends_with_drug_name(segments[-2]):
                before = segments[-2]
        # 药名后带规格括号（阿司匹林（肠溶片）停了）：剥离括号再取药名
        before = re.sub(
            r"[（(【「『][^）)】」』]{0,20}[）)】」』]\s*$", "", before
        )
        before = before.rstrip("，,、;； \t")
        token_match = re.search(r"([一-龥A-Za-z0-9]{2,10})\s*$", before)
        raw_token = token_match.group(1).strip() if token_match else ""
        if _long_stop_token_is_determiner(raw_token) or re.match(
            r"(?:没有|没|未)(?:有)?\s*(?:吃|喝|服|用)", raw_token
        ):
            # 「已经停药半年」「没吃完，已经停药」：没有点名其他药。
            # 但「华法林没吃完，二甲双胍吃了两年，停了一年多了」的
            # 停药属于中间那条药名分句，不能归因到当前药（第八十二轮）
            if _window_mentions_other_drug(
                window[: match.start()], alias_lower
            ):
                continue
            return True
        token = _normalize_long_stop_token(raw_token)
        if len(token) < 2:
            # 无药名宾语：就是当前药
            return True
        if token in _LONG_STOP_SELF_REFERENCES:
            return True
        matched_reference = False
        for reference in ("这个药", "该药", "此药", "这种药", "那种药"):
            if reference in token:
                head = token.split(reference)[0].strip()
                matched_reference = True
                if (
                    not head
                    or head in alias_lower
                    or alias_lower in head
                ):
                    return True
                break
        if matched_reference:
            continue
        hints = _LONG_STOP_CATEGORY_HINTS.get(token)
        if hints is not None:
            # 「激素停了半年」只有当前药确属激素类才等价于本药
            if any(hint in alias_lower for hint in hints):
                return True
            continue
        if token in alias_lower or alias_lower in token:
            return True
        continue
    return False


#: 明确远期停药（月/年）：即使同句还提到「没吃完」也按已停用
_LONG_STOP_CONTEXT = re.compile(
    r"(?:已经|已|早就|早|都|快)?\s*"
    r"(?:停用|停药|停了|停的|停服|停止服用|"
    # 裸「停」只认紧跟时长的形态（停三年了/停两个多月）：
    # 「停经两个月」是月经史，不是停药（第九十四轮 [6]）
    r"停(?=\s*[0-9一二两三四五六七八九十半几]+(?:个)?(?:多)?(?:年|月))"
    r"|不吃了?)"
    r"[^，。；,.!?！？\n]{0,6}?"
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月)|半年|多月|多年|"
    r"去年|前年|上年|很久|好久)"
    r"|(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月)|半年|多月|多年|"
    r"去年|前年|上年|很久|好久)"
    r"\s*(?:前|以来)?[^，。；,.!?！？\n]{0,4}"
    r"(?:停用|停药|停了|停的|停服|停止服用)"
)
#: 药名之后的「不能停药/未停药」= 仍在服用
_NEGATED_STOP_SUFFIX = re.compile(
    r"^[^，。；,.!?！？\n]{0,4}(?:不能|不可|不宜|不让|不应|不要|别|"
    r"未|没有|没)\s*(?:停用|停药|停服|停止服用)"
)
#: 停药动词后直接跟药名的语序（停用阿司匹林一年了/停掉了布洛芬）：
#: 宾语是另一种药时，这次停药不能归因到当前药（第九十九轮 [1]）
_STOP_VERB_AFTER_OBJECT_RE = re.compile(
    r"(?:停用|停药|停了|停掉|停掉了|停服|停止服用|不吃了?)"
    r"\s*(?:了)?\s*"
    r"([一-龥A-Za-z]{2,8}?)"
    r"(?=\s*(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:多)?"
    r"(?:年|月|天|周|日|星期)|半年|大半年|多月|多年|很久|好久|"
    r"了|，|。|；|$))"
)
#: 停药动词后的纯时长宾语（停用一年了/停了两个月了）：不是药名
_STOP_TIME_OBJECT_RE = re.compile(
    r"^(?:"
    r"[0-9一二两三四五六七八九十半几]+(?:个)?(?:多)?"
    r"(?:年|月|天|周|日|星期)"
    r"|半年|大半年|多月|多年|很久|好久"
    r")(?:多|了|左右)?$"
    r"|^[了前以来]$"
)
#: 既往用药：「我以前吃阿司匹林」「以前吃过华法林」「十年前用过」。
#: 数字时长分支必须带完成体（过/了），「两年前开始吃」仍是当前用药
_PAST_USE_PREFIX = re.compile(
    r"(?:以前|之前|曾经|曾|既往|过去)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:服用|口服|吃|用|注射|打|含服|滴|喷|喝|饮)"
    r"(?:过|了|过几次|过几次了)?\s*(?:的)?\s*$"
    r"|[0-9一二两三四五六七八九十半几]+\s*(?:天|周|个月|月|年)前"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:服用|口服|吃|用|注射|打|含服|滴|喷|喝|饮)"
    r"(?:过|了|过几次|过几次了)\s*(?:的)?\s*$"
)
#: 既往用药后的剂量延续（「以前吃2mg，现在吃5mg/一片」）：
#: 仍是当前用药，不能按既往史跳过药名
_DOSE_CHANGE_CONTINUATION = re.compile(
    r"(?:现在|目前|如今)(?:每天|每日|仍|还)?\s*"
    r"(?:(?:改吃|改成|改为|改服|换吃|换成|换为|换服|调到|调成|加到|增加到|"
    r"减到)\s*)?"
    r"(?:(?:每天|每日|一天|一日|每晚|每早)(?:一次|1次)?\s*"
    r"(?:[，,；;]\s*每次)?\s*)?(?:(?:吃|服用|口服|用)\s*)?"
    r"(?:[0-9一二两三四五六七八九十半]+(?:\.[0-9]+)?"
    r"\s*(?:mg|毫克|g|克|片|粒|单位|iu|ug)|一片半?|半片|一片)"
)
def _mentions_other_drug_alias(text: str, alias_lower: str) -> bool:
    """文本是否点名了另一种药（用于剂量延续语的归属判断）。"""
    lowered = normalize_brand_drug_names(str(text or "")).lower()
    alias_lower = str(alias_lower or "").lower()
    for group_items in DRUG_GROUPS.values():
        for _canonical, aliases in group_items:
            for alias in aliases:
                alias_text = str(alias).lower()
                if len(alias_text) < 2 or alias_text in alias_lower:
                    continue
                if alias_text in lowered:
                    return True
    generic = GENERIC_DRUG_NAME_RE.search(lowered)
    if (
        generic is not None
        and generic.group(0) not in alias_lower
        and alias_lower not in generic.group(0)
    ):
        return True
    return False


def _dose_continuation_names_other(text: str, alias_lower: str) -> bool:
    """剂量延续语的直接宾语是否是另一种药。

    「现在每天吃一片阿司匹林」的剂量属于阿司匹林，不能据此把
    华法林判为仍在用；「现在吃一片，能吃布洛芬吗」中的布洛芬是
    提问对象，不算剂量宾语。
    """
    match = _DOSE_CHANGE_CONTINUATION.search(str(text or ""))
    if match is None:
        return False
    tail = re.split(
        r"[，。；,.!?！？\n]", str(text)[match.end():], maxsplit=1
    )[0][:16]
    return _mentions_other_drug_alias(tail, alias_lower)


#: 未来停药意图（明天/下周停用）当前仍在用药
_FUTURE_STOP_PREFIX = re.compile(
    r"(?:明天|后天|下周|下个?月|下星期|过两天|过几天|以后|将来|"
    r"月底|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周|个月|月|年)\s*后|"
    r"等[^，。；,.!?！？\n]{0,10}(?:就|再))"
    r"[^，。；,.!?！？\n]{0,10}"
    r"(?:停用|停药|停服|停了|停掉|不吃了?|不再吃|不用吃(?:了)?|"
    r"不吃|不服|不喝|不服用|不再服用|不用服用)\s*$"
)
#: 未来停药的任意语序（「下周就不吃华法林了」「华法林下周就不吃了」）：
#: 停药计划落实之前仍按当前用药处理
_FUTURE_STOP_CONTEXT = re.compile(
    r"(?:明天|后天|下周|下个?月|下星期|明年|月底|年底|"
    r"过[0-9一二两三四五六七八九十]+(?:天|周|个月|月|年)|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周|个月|月|年)\s*(?:后|以后)|"
    r"等[^，。；,.!?！？\n]{0,12}(?:就|再))"
    r"[^，。；,.!?！？\n]{0,6}"
    r"(?:停用|停药|停服|停了|停掉|不吃|不再吃|不用吃|不服|不喝|"
    r"不服用|不再服用|不用服用)"
)
#: 近期停药（1–2 周内）仍有抗凝作用：按当前用药处理
_RECENT_STOP_PREFIX = re.compile(
    r"(?:刚刚?|今天|昨天|前天|前几天|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前)"
    r"[^，。；,.!?！？\n]{0,4}(?:已|刚|才)?\s*"
    r"(?:停用|停药|停了|停掉|停服|停止服用)\s*$"
)
_RECENT_STOP_SUFFIX = re.compile(
    r"^\s*(?:停用|停药|停了|停掉|停服|停止服用)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:今天|昨天|前天|几天|几天前|一周|"
    r"[0-9一二两三四五六七八九十]+\s*天|"
    r"(?:1|[一])\s*周(?:半)?)"
)
#: 近期「开始不吃…了」仍按当前用药保守处理（停药 1–2 天内 INR 偏高）
_RECENT_NO_EAT_CONTEXT = re.compile(
    r"(?:今天|昨天|前天|刚才|刚刚|今早|昨晚|凌晨|前几天|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前|"
    r"[0-9一二两三四五六七八九十]+\s*天)"
    r"[^，。；,.!?！？\n]{0,6}?(?:开始|就|才|已经?)?\s*"
    r"(?:不吃|不吃了|不再吃|不再服用)"
)
#: 近期停药的任意语序（药名前/后的文本拼接后判断）：
#: 「停了华法林三天了」「华法林昨天停了」「刚把华法林停了」
_RECENT_STOP_CONTEXT = re.compile(
    r"(?:刚刚?|今天|昨天|前天|前几天|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前?)"
    r"[^，。；,.!?！？\n]{0,4}(?:停用|停药|停了|停掉|停服|停止服用)"
    r"|(?:停用|停药|停了|停掉|停服|停止服用)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:今天|昨天|前天|几天|一周|"
    r"[0-9一二两三四五六七八九十]+\s*天|"
    r"(?:1|[一])\s*周(?:半)?)"
)
_RECENT_STOP_PREFIX_EN = re.compile(
    r"(?:just|recently|yesterday|today)\s+"
    r"(?:stopped|discontinued|quit|ceased)\s*$",
    re.IGNORECASE,
)
_RECENT_STOP_SUFFIX_EN = re.compile(
    r"^\s*(?:was\s+|has\s+been\s+|had\s+been\s+)?"
    r"(?:stopped|discontinued|quit|ceased)\s+"
    r"(?:(?:\d+\s*)?days?\s+ago"
    r"|(?:1|a|an|one)\s*weeks?\s+ago"
    r"|yesterday|today)",
    re.IGNORECASE,
)
#: 英文否定停药：「I have not stopped warfarin」「never stopped」
#: 「I am not off warfarin」都表示仍在用药
_NEGATED_STOP_PREFIX_EN = re.compile(
    r"(?:not|never|n't|no\s+longer)\s+"
    r"(?:yet\s+|ever\s+|really\s+)?"
    r"(?:stopped|discontinued|quit|ceased)\b"
    r"|(?:not|never)\s+off\b",
    re.IGNORECASE,
)
#: 英文药名前的停药表述：「I stopped warfarin two years ago」
_STOPPED_USE_PREFIX_EN = re.compile(
    r"(?:i\s+|he\s+|she\s+|they\s+|patient\s+)?"
    r"(?:have\s+|had\s+|has\s+)?"
    r"(?:stopped|discontinued|quit|ceased)\s+"
    r"(?:taking\s+|using\s+)?$"
    r"|(?:i\s+am\s+|i'm\s+)?off\s+$",
    re.IGNORECASE,
)
#: 英文近期停药（天/周内）仍有抗凝作用：按当前用药处理
_RECENT_STOP_CONTEXT_EN = re.compile(
    r"(?:stopped|discontinued|quit|ceased)\s+(?:\w+\s+){0,2}"
    r"(?:(?:\d+|a|an|one|two|few)\s+days?\s+ago"
    r"|(?:1|a|an|one)\s*weeks?\s+ago)"
    r"|(?:stopped|discontinued|quit|ceased)\s+(?:\w+\s+){0,2}"
    r"(?:yesterday|today)",
    re.IGNORECASE,
)
#: 英文「off X for N days/until yesterday」：近期停药仍有抗凝作用
_RECENT_STOP_OFF_EN = re.compile(
    r"\boff\s+(?:of\s+)?(?:[\w'-]+\s+){0,3}(?:for\s+)?"
    r"(?:(?:\d+|one|two|three|four|five|a\s+few|several)"
    r"\s+(?:days?|weeks?)|yesterday|today)",
    re.IGNORECASE,
)
#: 英文单次漏服（today/yesterday + didn't take）仍属当前用药
_DOSE_MISSED_PREFIX_EN = re.compile(
    r"(?:today|yesterday|last\s+night|this\s+morning|tonight)"
    r"[^,.;!?\n]{0,24}"
    r"(?:didn't|did\s+not|haven't|have\s+not|missed)"
    r"\s+(?:take|taking|use|using|my\s+dose)"
    r"|(?:didn't|did\s+not|haven't|have\s+not|missed)"
    r"\s+(?:take|taking|use|using|my\s+dose)"
    r"[^,.;!?\n]{0,24}(?:today|yesterday|last\s+night|this\s+morning)",
    re.IGNORECASE,
)
#: 英文明确未用药：「I do not take warfarin」
_NEGATED_USE_PREFIX_EN = re.compile(
    r"(?:do not|don't|didn't|does not|not)\s+(?:take|taking|use|using)"
    r"\s*$|no longer (?:taking|on)\s*$",
    re.IGNORECASE,
)
#: 持续用药：「过去半年每天吃」「既往长期服用」不是既往用药
_CONTINUOUS_USE_PREFIX = re.compile(
    r"(?:过去|之前|以前|近|最近|这|既往|曾经?|现在|目前|还在|仍在|仍|继续)"
    r"[^，。；,.!?！？\n]{0,6}"
    r"(?:每天|每日|天天|长期|一直|持续|规律|常年|多年|半年|"
    r"几天|几日|几周|数天|数周|近期|这几天|"
    r"一周|两周|[一二两三四五六七八九十0-9]+(?:天|周|个月))"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:服用|口服|吃|用|注射|打|喝|饮)\s*$"
)
_STOPPED_USE_SUFFIX_EN = re.compile(
    r"^\s*(?:has been |had been |was |is |been )?"
    r"(?:stopped(?!\s*working)|discontinued|no longer taking)",
    re.IGNORECASE,
)
#: 药名之后跨分句出现的续用/重启表述：「停了半年，最近又吃上了」；
#: 续用分句点名另一种药（还在吃阿司匹林）不算本药重启（第八十九轮）
_RESTART_USE_SUFFIX = re.compile(
    r"^[^。；;！!？?\n]{0,24}"
    r"(?:又吃上|又用上|又服上|又在吃|又在服用|又在用|又重新吃|"
    r"又重新服用|重新开始吃|重新开始服用|恢复吃了|恢复用药|"
    r"恢复吃药|恢复服药|接着吃|接着服|接着用|又吃起来了|又用起来了|"
    r"重新吃|重新服用|重新用|恢复服用|恢复吃"
    r"|又开始吃|又开始服|再次服用|继续服用|继续吃|还在吃|还在服用"
    r"|仍在吃|仍在服用|仍然服用|还在用|仍在用|继续用|还用|仍用"
    r"|一直在用|仍在使用|继续使用|还在使用"
    r"|还吃|还服用|仍吃|没停过|没间断过|未停过|从未停过|从没停过"
    r"|一直没停|吃到现在|用到现在|吃到今天|用到今天"
    # 口语「又吃了/又服用了」同样是重启；排除吃饭/吃东西（含量词）
    r"|又吃了(?!饭|菜|东西|水果|早餐|午餐|晚餐|零食|面包|鸡蛋|汤|粥|面|"
    r"零|点|些|口|(?:两|几|一|半)[口块碗])"
    r"|又服用了|又用了(?!别))"
)
#: 宾语前置的续用：「阿司匹林还在吃」。词表与 triage 的
#: _RESTART_OBJECT_BEFORE_RE 保持同一来源（第九十五/九十六轮）
_RESTART_OBJECT_BEFORE_RE = re.compile(
    r"([一-龥A-Za-z][一-龥A-Za-z0-9]{1,9})\s*(?:也|还|仍|都)?\s*"
    r"(?:还在吃|还在服用|仍在吃|仍在服用|仍然服用|继续吃|继续服用|"
    r"还在用|仍在用|继续用|还用|仍用|仍在使用|一直在用|"
    r"继续使用|还在使用|还吃|还服用|仍吃|接着吃|接着服|接着用|"
    r"仍然用)"
)


def _restart_names_other_medication(
    follow: str, alias_lower: str
) -> bool:
    """续用分句是否在说另一种药（还在吃阿司匹林/阿司匹林还在吃）。

    续用线索必须指回本药；换用其它药不能把已停用的药复活成当前
    用药（第八十九轮）。
    """
    text = str(follow or "")
    match = _RESTART_USE_SUFFIX.search(text)
    if match is not None:
        tail = re.split(
            r"[，。；,.!?！？\n]", text[match.end():], maxsplit=1
        )[0][:12]
        tail = re.sub(
            r"^[0-9一二两三四五六七八九十半几]+\s*"
            r"(?:片|粒|丸|袋|包|支|瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)\s*",
            "",
            tail,
            flags=re.IGNORECASE,
        )
        tail = re.sub(r"(?:着|呢|啊|呀|吧|了|啦)+$", "", tail).strip()
        if tail and _mentions_other_drug_alias(tail, alias_lower):
            return True
    for before in _RESTART_OBJECT_BEFORE_RE.finditer(text):
        obj = re.sub(
            r"(?:这|那|个|种|些|药)+$", "", before.group(1)
        ).strip()
        if len(obj) < 2:
            continue
        if _mentions_other_drug_alias(obj, alias_lower):
            return True
    return False


#: 英文续用宾语：「still on aspirin」点名他药时不算本药重启
_ENGLISH_STILL_OBJ_RE = re.compile(
    r"\bstill\s+(?:take|taking|use|using|on)\s+"
    r"(?:(it|the\s+same|this)\b|"
    r"(?:(?:my|his|her|their|the)\s+)?([A-Za-z][A-Za-z\-]{2,})\b)",
    re.IGNORECASE,
)


def _english_restart_names_other(
    follow: str, alias_lower: str
) -> bool:
    """英文续用分句是否点名另一种药（still on aspirin）。"""
    text = str(follow or "")
    for match in _ENGLISH_STILL_OBJ_RE.finditer(text):
        if (match.group(1) or "").strip():
            continue
        obj = (match.group(2) or "").strip()
        if not obj:
            continue
        if _mentions_other_drug_alias(obj, alias_lower):
            return True
    return False


_RESTART_USE_SUFFIX_EN = re.compile(
    r"\b(?:restarted|started (?:it )?again|taking (?:it )?again"
    # still take/use/using it 与 still taking/on 同为续用
    # （第九十八轮 [2]）
    r"|back on (?:it )?(?:again)?|still\s+(?:taking|on|take|use|using)"
    r"|continuing (?:it|to take)"
    # kept/continued/continue (to) take/use it：代词宾语续用
    # （第九十四轮 [2]）
    r"|(?:kept|keeps?|continued|continues?|have continued"
    r"|has continued|have kept|has kept)\s+(?:on\s+)?"
    r"(?:taking|to take|using|to use)?\s*(?:it|the same|this))\b",
    re.IGNORECASE,
)
#: 持续用药形态：「以前吃过，现在每天吃一片」仍是当前用药；
#: 宾语是另一种药（每天吃阿司匹林）时不算本药续用
_CONTINUOUS_USE_AFTER_RE = re.compile(
    r"(?:每天|每日|天天|一直|持续|规律|长期|常年)"
    r"(?:都|也|还|在|坚持)?(?:吃|服用|口服|服|用)"
    r"(?!饭|菜|水果|东西|零食|早餐|午餐|晚餐|汤|粥|面)"
    r"\s*([一-龥A-Za-z0-9]{0,12})"
)


def _continuous_restart_matches(follow: str, alias_lower: str) -> bool:
    """持续用药分句是否指当前药（空/数量/同药宾语）。"""
    for match in _CONTINUOUS_USE_AFTER_RE.finditer(follow or ""):
        obj = re.split(
            r"[，。；,.!?！？\n和及与、]", match.group(1) or ""
        )[0].strip()
        # 先剥离数量+单位：「每天吃一片阿司匹林」是另一种药，
        # 「每天吃一片」才是本药的剂量续用
        obj = re.sub(
            r"^[0-9一二两三四五六七八九十半几]+\s*"
            r"(?:片|粒|丸|袋|包|支|瓶|滴|毫升|ml|mg|毫克|克|g|单位|iu)\s*",
            "",
            obj,
            flags=re.IGNORECASE,
        )
        # 时间补语/语气助词不是药名宾语
        obj = re.sub(
            r"(?:到现在|到今天|至今|到现在为止|以来|为止|一直)$", "", obj
        ).strip()
        obj = re.sub(r"(?:着|呢|啊|呀|吧|了|啦)+$", "", obj).strip()
        if not obj:
            return True
        if re.match(r"^[0-9一二两三四五六七八九十半几]+", obj):
            return True
        obj_lower = obj.lower()
        if alias_lower and (
            alias_lower in obj_lower or obj_lower in alias_lower
        ):
            return True
    return False
_PAST_USE_PREFIX_EN = re.compile(
    r"(?:used to|previously|formerly)\s+(?:take|taking|use|using)?\s*$",
    re.IGNORECASE,
)


def _clause_bounds(text: str, start: int, end: int) -> tuple:
    """药名所在分句的起止位置。"""
    left = 0
    for index in range(start - 1, -1, -1):
        if text[index] in "，。；,.!?！？\n":
            left = index + 1
            break
    right = len(text)
    for index in range(end, len(text)):
        if text[index] in "，。；,.!?！？\n":
            right = index
            break
    return left, right


def _alias_matches(text: str, alias: str) -> bool:
    """匹配药名：英文按单词边界、否定语境与短中文别名按用药语境过滤。"""
    alias_lower = alias.lower()
    ascii_alias = alias_lower.isascii() and all(
        char.isalnum() or char in " -" for char in alias_lower
    )
    if ascii_alias:
        for match in re.finditer(
            rf"(?<![a-z0-9_]){re.escape(alias_lower)}s?(?![a-z0-9_])",
            text,
        ):
            left, right = _clause_bounds(text, match.start(), match.end())
            prefix = text[left:match.start()]
            suffix = text[match.end():right]
            long_suffix = text[match.end():right + 25]
            # 英文停药分句常以逗号紧邻药名：25 字窗口会截断
            # "stopped taking it yesterday"，英文模式单独用更宽窗口
            far_suffix = text[match.end():right + 40]
            # 英文续用宾语（"ago, but I am still on aspirin"）可能在
            # 逗号后更深：判定与归属用固定 90 字窗口，避免截断成
            # "aspir" 导致他药被判回本药（第九十一轮 [8]）
            en_suffix = text[match.end():right + 90]
            if _NEGATED_STOP_PREFIX.search(prefix):
                # 「没有停用」= 仍在服用
                return True
            if (
                _USE_DOUBLE_NEGATION.search(prefix)
                or _USE_DOUBLE_NEGATION_EN.search(prefix)
                or _DOSE_MISSED_PREFIX_EN.search(prefix + suffix)
                # 中文时间/停药表述作用于整段文本，与药名语言无关：
                # 「我今天没吃 warfarin」「下周就不吃 warfarin 了」
                or (
                    _DOSE_MISSED_PREFIX.search(prefix + suffix)
                    and _missed_dose_names_term(
                        text[left:right], alias_lower
                    )
                )
                # 「没吃 warfarin 三天了」：时长在药名之后同样是漏服
                # （第九十四轮 [7]）；漏服宾语必须是本药
                # （第九十五轮 [16]）
                or _missed_dose_names_term(text[left:right], alias_lower)
                or _FUTURE_STOP_PREFIX.search(prefix)
                or _FUTURE_STOP_CONTEXT.search(prefix + suffix)
                or _NEGATED_STOP_PREFIX_EN.search(prefix)
                or _INTENT_STOP_PREFIX.search(prefix)
                or _INTENT_STOP_SUFFIX.search(suffix)
                or _NEGATED_STOP_SUFFIX.search(suffix)
                or _CONTINUOUS_USE_PREFIX.search(prefix)
                or (
                    _RESTART_USE_SUFFIX.search(long_suffix)
                    and not _restart_names_other_medication(
                        long_suffix, alias_lower
                    )
                )
                or (
                    _continuous_restart_matches(long_suffix, alias_lower)
                    and not _restart_names_other_medication(
                        long_suffix, alias_lower
                    )
                )
                or (
                    _RESTART_USE_SUFFIX_EN.search(en_suffix)
                    and not _english_restart_names_other(
                        en_suffix, alias_lower
                    )
                )
                or _ADHERENCE_USE_PREFIX.search(prefix)
                and _NEGATED_USE_PREFIX.search(prefix)
                or _RECENT_STOP_PREFIX.search(prefix)
                or _RECENT_STOP_PREFIX_EN.search(prefix)
                or _RECENT_STOP_SUFFIX.search(suffix)
                or _RECENT_STOP_SUFFIX_EN.search(suffix)
                or _RECENT_STOP_CONTEXT.search(prefix + suffix)
                or _RECENT_NO_EAT_CONTEXT.search(prefix + suffix)
                or _RECENT_STOP_CONTEXT_EN.search(prefix + suffix)
                or _RECENT_STOP_OFF_EN.search(prefix + suffix)
                # 英文跨分句近期停药：I used to take warfarin,
                # stopped yesterday 与中文「之前吃…昨天停药」同义
                or _RECENT_STOP_CONTEXT_EN.search(far_suffix)
                or _RECENT_STOP_OFF_EN.search(far_suffix)
            ):
                return True
            if (
                _long_stop_applies_to_alias(
                    prefix + text[match.end():right + 120],
                    alias_lower,
                )
                or _STOPPED_USE_PREFIX.search(prefix)
                or _STOPPED_USE_PREFIX_LONG.search(prefix)
                or _STOPPED_USE_SUFFIX.search(suffix)
                or _PAST_USE_SUFFIX.search(suffix)
                or _STOPPED_USE_SUFFIX_EN.search(suffix)
                or _STOPPED_USE_PREFIX_EN.search(prefix)
                or _NEGATED_USE_PREFIX.search(prefix)
                or _PAST_USE_PREFIX.search(prefix)
                or _PAST_USE_PREFIX_EN.search(prefix)
                or _NEGATED_USE_PREFIX_EN.search(prefix)
            ):
                continue
            return True
        return False

    start = text.find(alias_lower)
    while start != -1:
        end = start + len(alias_lower)
        left, right = _clause_bounds(text, start, end)
        prefix = text[left:start]
        suffix = text[end:right]
        # 续用/重启常出现在下一个分句（「停了半年，最近又吃上了」）：
        # 允许向后多看一段，句号仍截断
        long_suffix = text[end:right + 25]
        if (
            _USE_DOUBLE_NEGATION.search(prefix)
            or (
                _DOSE_MISSED_PREFIX.search(prefix + suffix)
                and _missed_dose_names_term(
                    text[left:right], alias_lower
                )
            )
            # 「没吃华法林三天了」：时长在药名之后同样是漏服
            # （第九十四轮 [7]）；漏服宾语必须是本药
            # （第九十五轮 [16]）
            or _missed_dose_names_term(text[left:right], alias_lower)
            or _FUTURE_STOP_PREFIX.search(prefix)
            or _FUTURE_STOP_CONTEXT.search(prefix + suffix)
            or _NEGATED_STOP_PREFIX.search(prefix)
            or _NEGATED_STOP_SUFFIX.search(suffix)
            or _INTENT_STOP_SUFFIX.search(suffix)
            or _INTENT_STOP_PREFIX.search(prefix)
            or _CONTINUOUS_USE_PREFIX.search(prefix)
            or (
                _RESTART_USE_SUFFIX.search(long_suffix)
                and not _restart_names_other_medication(
                    long_suffix, alias_lower
                )
            )
            or (
                _continuous_restart_matches(long_suffix, alias_lower)
                and not _restart_names_other_medication(
                    long_suffix, alias_lower
                )
            )
            or _RECENT_STOP_PREFIX.search(prefix)
            or _RECENT_STOP_SUFFIX.search(suffix)
            or _RECENT_STOP_CONTEXT.search(prefix + suffix)
            or _RECENT_NO_EAT_CONTEXT.search(prefix + suffix)
            # 「我之前吃华法林，昨天停药了」：近期停药仍有抗凝作用，
            # 跨分句绑定到药名
            or _RECENT_STOP_CONTEXT.search(long_suffix)
            or _RECENT_NO_EAT_CONTEXT.search(long_suffix)
            or (
                _ADHERENCE_USE_PREFIX.search(prefix)
                and _NEGATED_USE_PREFIX.search(prefix)
            )
        ):
            return True
        if (
            # 「没吃完，……，已经停药一年了」：远期停药覆盖未吃完的
            # 续用；停药分句可能隔着多段生活描述，按更长的后缀窗口
            # 扫描，但停药必须指向当前药（他药停药不能误伤）
            _long_stop_applies_to_alias(
                text[end:right + 120], alias_lower
            )
            or _STOPPED_USE_PREFIX.search(prefix)
            or _STOPPED_USE_PREFIX_LONG.search(prefix)
            or _STOPPED_USE_SUFFIX.search(suffix)
            or _PAST_USE_SUFFIX.search(suffix)
            or _NEGATED_USE_PREFIX.search(prefix)
            or (
                _PAST_USE_PREFIX.search(prefix)
                and not (
                    (
                        _DOSE_CHANGE_CONTINUATION.search(prefix)
                        or _DOSE_CHANGE_CONTINUATION.search(suffix)
                        or _DOSE_CHANGE_CONTINUATION.search(long_suffix)
                    )
                    and not (
                        _dose_continuation_names_other(prefix, alias_lower)
                        or _dose_continuation_names_other(
                            suffix, alias_lower
                        )
                        or _dose_continuation_names_other(
                            long_suffix, alias_lower
                        )
                    )
                )
            )
        ):
            start = text.find(alias_lower, end)
            continue

        if alias_lower in _AMBIGUOUS_SHORT_ALIASES:
            gate_prefix = text[max(0, start - 4):start]
            suffix = text[end:end + 3]
            usable = (
                any(verb in gate_prefix for verb in _MEDICATION_VERBS)
                or (start == 0 and not text[end:].strip())
                or suffix.startswith((
                    "片", "粒", "胶囊", "针", "注射", "滴", "喷", "剂",
                ))
                or gate_prefix.rstrip().endswith((
                    "、", "，", ",", "（", "(", "和", "与", "及", "+",
                ))
                or suffix.startswith(("、", "，", ",", "）", ")", "和", "与", "及"))
            )
            if not usable:
                start = text.find(alias_lower, end)
                continue

        return True
    return False


def _term_matches(text: str, term: str) -> bool:
    """过敏词等短词匹配；英文要求单词边界，避免 sulfa 命中 sulfate。"""
    lowered = term.lower()
    if not lowered:
        return False
    if lowered.isascii() and lowered.replace(" ", "").isalnum():
        return bool(re.search(
            rf"(?<![a-z0-9_]){re.escape(lowered)}s?(?![a-z0-9_])", text
        ))
    return lowered in text


#: 局部用糖皮质激素不进入全身相互作用判断
_TOPICAL_STEROID_MARKERS = (
    "滴眼", "眼液", "滴耳", "乳膏", "软膏", "外用", "喷雾",
    "洗剂", "凝胶", "贴剂",
)


def _find_drugs(text: str, group: str) -> List[str]:
    """返回文本中命中的组内规范药名（去重、保持定义顺序）。"""
    text = normalize_brand_drug_names(text)
    if group == "alcohol":
        # 酒类是行为描述而非药名：必须排除「不喝酒」「已戒酒」
        return ["酒精"] if _has_alcohol_use(text) else []
    if group == "corticosteroids":
        # 逐条药物过滤：氢化可的松乳膏/滴眼液不算全身用药。
        # 只跳过局部剂型所在的短语，不能把同段里的口服激素一起屏蔽
        found: List[str] = []
        for canonical, aliases in DRUG_GROUPS.get(group, ()):
            # 和/与/及 也要切分：同一短语里的口服激素不能被局部剂型
            # 标记一起跳过（「氢化可的松乳膏和泼尼松」）
            for chunk in re.split(
                r"(?:以及|[；;，,\n、和与及])", text
            ):
                if any(
                    marker in chunk
                    for marker in _TOPICAL_STEROID_MARKERS
                ):
                    continue
                if any(_alias_matches(chunk, alias) for alias in aliases):
                    found.append(canonical)
                    break
        return found
    found: List[str] = []
    for canonical, aliases in DRUG_GROUPS.get(group, ()):
        if any(_alias_matches(text, alias) for alias in aliases):
            if canonical not in found:
                found.append(canonical)
    return found


def find_drugs(text: Any, group: str) -> List[str]:
    """按药物组匹配文本中的药名，供化验联动等其他规则复用。"""
    return _find_drugs(
        normalize_brand_drug_names(_flatten(text).lower()), group
    )


#: 只从「对X过敏」「X过敏史」这类明确句式提取过敏原
_ALLERGY_STATEMENT_PATTERNS = (
    # 标签式过敏记录：过敏原：X / 过敏药物：X / 我过敏的药是X。
    # 必须带冒号或「是/为」，避免把「过敏原检测」「抗过敏药物」当成过敏史
    re.compile(
        r"(?<!抗)(?:我的|我|本人|患者)?\s*"
        r"(?:过敏原|过敏药物|过敏的药(?:物)?)\s*"
        r"(?:[：:]|[是为])\s*"
        r"([^\s，。；、,.!?！？：:]{1,20})"
    ),
    re.compile(r"对[^，。；,.!?！？]{1,20}?过敏"),
    re.compile(r"[^，。；,.!?！？]{1,20}?过敏史"),
    re.compile(r"过敏史\s*[：:]\s*([^\n，。；]{1,30})"),
    # 「我青霉素过敏」这类不带「对」的陈述；捕获组排除否定表达
    re.compile(
        r"(?:^|[，。；,.!?！？\n])\s*(?:我|本人|患者)?\s*"
        r"([^，。；,.!?！？\n]{1,20}?)\s*(?<!抗)过敏(?!史|的药|的药物)"
    ),
    # 英文陈述：「I'm allergic to penicillin」「penicillin allergy」
    # 「Allergy to penicillin」「penicillin allergic」；捕获只取药名
    # token，遇到 and/but/can/i 等词或标点即止，不能连带吞掉后面的
    # 在用药物（否则华法林等相互作用提示被静默抹掉，第八十一轮）
    re.compile(
        r"allergic\s+to\s+([a-z][a-z0-9\-]*(?:\s+[a-z][a-z0-9\-]*){0,2}?)"
        r"(?=\s*(?:[,.;!?\n，。；！？]|and\b|but\b|can\b|could\b|should\b|"
        r"may\b|i\b|we\b|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"\ballerg(?:y|ies)\s+to\s+([a-z][a-z0-9\-]*"
        r"(?:\s+[a-z][a-z0-9\-]*){0,2}?)"
        r"(?=\s*(?:[,.;!?\n，。；！？]|and\b|but\b|can\b|could\b|should\b|"
        r"may\b|i\b|we\b|$))",
        re.IGNORECASE,
    ),
    re.compile(
        r"([a-z][a-z0-9\-]{0,20}?)\s+allerg(?:y|ies)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?!i\b|am\b|is\b|are\b|was\b|were\b|be\b|not\b|no\b"
        r"|never\b)([a-z][a-z0-9\-]{2,20})\s+allergic\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\ballerg(?:y|ies)\s*[:：]\s*([^\n,.;!?]{1,30})",
        re.IGNORECASE,
    ),
)

#: 检测结果不是过敏原（「阿莫西林阴性」）；阳性仍按过敏处理
_ALLERGY_RESULT_WORDS = ("阴性", "待查", "正常", "未查")

#: 句式前出现否定词时跳过（「我没有阿司匹林过敏」）
_ALLERGY_NEGATION_PATTERN = re.compile(
    r"(?:没有|无|未|否认|不|并非)[^，。；,.!?！？]{0,6}$"
)

#: 捕获内容本身带否定词时也跳过（「没有阿司匹林过敏」）
_ALLERGY_NEGATION_WORDS = ("没有", "无", "未", "否认", "不", "并非")


def _allergy_mentions_in_question(question: str) -> str:
    """提取问题中的过敏陈述，忽略否定表述。"""
    parts: List[str] = []
    for pattern in _ALLERGY_STATEMENT_PATTERNS:
        for match in pattern.finditer(question):
            prefix = question[max(0, match.start() - 12):match.start()]
            if _ALLERGY_NEGATION_PATTERN.search(prefix):
                continue
            if re.search(r"\b(?:not|no|never|non)\s*$", prefix, re.I):
                # 「not allergic to penicillin」「no known allergies」
                continue
            captured = match.group(1) if match.groups() else match.group(0)
            if any(word in captured for word in _ALLERGY_NEGATION_WORDS):
                continue
            if any(word in captured for word in _ALLERGY_RESULT_WORDS):
                continue
            # 结果词可能跟在捕获组之后（「青霉素过敏史阴性」
            # 「青霉素过敏原阴性」），捕获组里只有药名
            after = question[match.end():match.end() + 8]
            if any(word in after for word in _ALLERGY_RESULT_WORDS):
                continue
            parts.append(match.group(0))
    return " ".join(parts)


def _positive_allergy_field(value: Any) -> str:
    """清理结构化过敏史中的否定表述（“否认青霉素过敏”）。"""
    clauses = re.split(
        r"[，。；,.!?！？\n]", _flatten(value)
    )
    kept = []
    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue
        if (
            re.search(r"(?:没有|否认|无|未|并非)[^，。；]{0,8}过敏", clause)
            or "不过敏" in clause
        ):
            continue
        # 「过敏原：阿莫西林阴性」是阴性结果；但「磺胺过敏（未查）」
        # 已明确写了过敏，不能因为括号里的检查状态整段丢弃
        if (
            any(word in clause for word in _ALLERGY_RESULT_WORDS)
            and not re.search(r"过敏(?!原)", clause)
        ):
            continue
        kept.append(clause)
    return " ".join(kept).lower()


def _strip_allergy_statements(text: str) -> str:
    """从药物文本中移除过敏陈述，避免把过敏药当成正在服用。"""
    for pattern in _ALLERGY_STATEMENT_PATTERNS:
        text = pattern.sub(" ", text)
    return text


def _allergy_text(context: Dict[str, Any], question: str) -> str:
    parts = [
        _positive_allergy_field(context.get("allergies")),
        _allergy_mentions_in_question(question),
    ]
    return " ".join(part for part in parts if part).lower()


def _dedupe(alerts: List[Dict[str, str]]) -> List[Dict[str, str]]:
    seen = set()
    result = []
    for alert in alerts:
        title = alert["title"]
        if title in seen:
            continue
        seen.add(title)
        result.append(alert)
        if len(result) >= MAX_ALERTS:
            break
    return result


from core.medication_text import (
    GENERIC_DRUG_NAME_RE,
    clause_mentions_drug_clause,
    normalize_brand_drug_names,
)

#: 家属称谓：家人的服药/停药/过敏不能算到患者本人头上
_FAMILY_MEDICATION_MARKERS = (
    "父亲", "母亲", "父母", "家人", "家族", "爷爷", "奶奶", "外公", "外婆",
    "丈夫", "妻子", "配偶", "哥哥", "姐姐", "弟弟", "妹妹", "儿子", "女儿",
    "爸爸", "妈妈", "爸", "妈", "老公", "老婆", "爱人", "老伴",
    "岳父", "岳母", "公公", "婆婆", "女婿", "儿媳", "姥姥", "姥爷",
    # 主语位置的家属表述（成人档案下不把家属用药算到患者头上）
    "家里人", "我家孩子", "我家宝宝", "我家娃", "我家老人",
    "我孩子", "我宝宝", "我娃",
    # 英文家属表述
    "my father", "my mother", "my dad", "my mom", "my mum",
    "my husband", "my wife", "my son", "my daughter", "my brother",
    "my sister", "my grandfather", "my grandmother", "my grandma",
    "my grandpa", "my parents", "my family",
)
#: 患儿档案下这些称谓指患者本人（与 triage 的家属口径一致）
_CHILD_SELF_MARKERS = (
    "我儿子", "我女儿", "孩子", "宝宝", "患儿", "小朋友", "我娃",
)
#: 紧随家属用药分句的停药/续用分句仍属于该家属
_FAMILY_STOP_FOLLOW_RE = re.compile(
    r"^(?:"
    # 「两年前停了」「去年就停药了」：时间前置语序同样属于家属
    r"(?:[0-9一二两三四五六七八九十半几]+(?:个)?(?:年|月|天|周)"
    r"(?:多)?(?:前|以来)?|去年|前年|上个月|上周|多年前|好?多年|"
    r"最近|当时|那时)\s*(?:就|才|已经|已|早就)?\s*"
    r"|已经?|早就|现在|目前|之前|以前|刚刚?)?\s*"
    r"(?:停药|停用|停了|停服|停止服用|不吃了?|又吃|重新吃|重新服用)"
)
#: 家属转述患者本人用药（我爸让我吃/我妈建议我吃/my father told me to take）
_FAMILY_RELAY_RE = re.compile(
    r"(?:让|叫|劝|说|建议|叮嘱|嘱咐|要求|提醒|吩咐|交代)"
    r".{0,6}?我(.+)"
    r"|(?:told|asked|advised|reminded|wants?|wanted)"
    r"\s+(?:me|us)\s+to\s+(.+)",
    re.I,
)
_FAMILY_MARKER_TAIL = (
    "爸", "妈", "父亲", "母亲", "儿子", "女儿", "老公", "老婆",
    "爱人", "爷爷", "奶奶", "外公", "外婆", "岳父", "岳母",
    "公公", "婆婆", "哥", "姐", "弟", "妹",
)
#: 「我」后紧跟家属称谓时是定语（我爸爸/我妈），不是患者主语
_FAMILY_AFTER_I = (
    r"(?:的)?(?:"
    r"家(?:里)?(?:的)?(?:孩子|宝宝|娃|老人|人)|"
    r"爸|妈|爹|娘|爷|奶|外公|外婆|姥爷|姥姥|老公|老婆|"
    r"爱人|丈夫|妻子|儿子|女儿|儿|女|哥|姐|弟|妹|叔|姨|舅|姑|"
    r"孩子|宝宝|家人|父亲|母亲)"
)
def _patient_continuation_follows(
    clauses: list, index: int, clause: str
) -> bool:
    """家属分句后 1-2 句是否为患者本人的同一药物续用/过敏。

    与分诊共用 continuation_same_as_clause：宾语必须指向前一分句
    的同一药物/过敏原（「我也在吃维生素C」「我也有一只猫」不算）。
    """
    from core.triage import continuation_same_as_clause

    for follow in clauses[index + 1:index + 3]:
        follow = str(follow or "").strip()
        if follow and continuation_same_as_clause(follow, clause):
            return True
    return False


def _patient_self_mentioned(clause: str) -> bool:
    """分句是否确在描述患者本人的用药/过敏（而非提问框架）。

    「我想问一下我爸吃华法林」「我能吃布洛芬吗」里的「我」只是提问者，
    不能把家属用药算到患者头上；「我和我爸都吃」「我和我爸一起吃」
    「我、我爸都对青霉素过敏」才是患者本人的描述。
    """
    lowered_clause = clause.lower()
    family_first = bool(
        re.search(
            r"(?:和|跟|与|同|、)\s*我(?!"
            + _FAMILY_AFTER_I +
            r")",
            clause,
        )
        and any(cue in clause for cue in ("都", "也", "均", "皆", "一起"))
        and any(
            marker in lowered_clause
            for marker in _FAMILY_MEDICATION_MARKERS
        )
    )
    parallel_subject = bool(
        re.search(
            r"我(?:、|和|跟|与|同)[^，。；;?？]{0,12}"
            r"(?:都|也|均|皆|一起|每天|一直|正在|在)",
            clause,
        )
        or re.search(
            r"(?:都|也|均|皆)[^，。；;?？]{0,8}我", clause
        )
        or family_first
        or re.search(
            r"\b(?:my father|my mother|my dad|my mom|my mum|"
            r"my husband|my wife|my son|my daughter|my brother|"
            r"my sister|my grandfather|my grandmother|my grandma|"
            r"my grandpa|my parents|my family)\s+and\s+i\b",
            lowered_clause,
        )
    )
    if parallel_subject:
        return True
    allergy = re.search(
        r"我(?:自己|本人)?[^，。；;?？]{0,12}(?:对)?"
        r"[^，。；;?？]{0,8}过敏",
        clause,
    )
    if allergy:
        segment = allergy.group(0)
        # 「我想问一下我爸爸…过敏」是提问框架：中间的家属用药/过敏
        # 不属于患者本人
        questioning = any(
            word in segment for word in ("问", "想", "咨询", "了解")
        )
        # 「我爸爸/我妈…过敏」里的「我」是家属定语，不是患者本人；
        # 并列主语（我和我爸都对…过敏）已在上面的分支保留
        family_subject = re.match(
            r"我(?:自己|本人)?" + _FAMILY_AFTER_I,
            segment,
        )
        if not questioning and not family_subject:
            return True
    for match in re.finditer(
        r"我(?:和|跟|与|也|都|每天|一直|最近|现在|正在|在|还在|要|得|"
        r"需要)?[^，。；;?？]{0,8}?(?:吃|服用|服|用|停用|停药|停了)",
        clause,
    ):
        span = clause[match.start():match.end()]
        between = clause[match.start() + 1:match.end()]
        tail = clause[match.end():match.end() + 10]
        if any(word in between for word in ("问", "想", "咨询", "了解")):
            continue
        if re.search(r"(?:吗|么|呢|？|\?)", tail):
            continue
        family_inside = any(
            marker in between or marker in span
            for marker in _FAMILY_MEDICATION_MARKERS
        )
        # 并列主语必须包含独立的患者「我」：我爸和我妈都吃华法林
        # 不能因为「和…都」被当成患者本人用药
        if family_inside and not parallel_subject:
            continue
        return True
    english = re.sub(
        r"\b(?:can|should|may|could|would|do|does|did|shall|will)\s+i\b",
        "",
        clause.lower(),
    )
    if re.search(
        r"(?:^|[\s,;])i\s*'?(?:m|ve)\b"
        r"|(?:^|[\s,;])i\s+(?:am|have|had|was|were|on|take|took|"
        r"use|using|taking)\b",
        english,
    ):
        return True
    return False


def _child_patient_context(context: Any) -> bool:
    """患儿档案（或未填年龄）下「我儿子/我女儿」就是患者本人。"""
    try:
        from core.triage import _context_child_patient

        return bool(_context_child_patient(context))
    except Exception:
        return True


def _patient_medication_text(text: str, context: Any = None) -> str:
    """去掉家属本人的用药/停药/过敏分句，只保留患者本人描述。

    「我每天吃华法林，我爸以前也吃华法林，已经停药两年了，我能吃
    布洛芬吗」：家属分句与其后的停药分句不能让患者的华法林被判为
    已停用；「我爸让我吃华法林」「我妈建议我吃华法林」保留患者自己
    的用药；「我和我爸都吃华法林」与患儿档案下的「我儿子在吃」同样
    算患者本人。
    """
    clauses = re.split(r"[，,。；;！!？?\n]", str(text or ""))
    child_patient = _child_patient_context(context)
    kept: List[str] = []
    drop_next = False
    for index, clause in enumerate(clauses):
        current = clause.strip()
        if not current:
            continue
        if drop_next and _FAMILY_STOP_FOLLOW_RE.match(current):
            drop_next = False
            continue
        drop_next = False
        lowered = current.lower()
        if any(
            marker in lowered for marker in _FAMILY_MEDICATION_MARKERS
        ):
            if child_patient and any(
                marker in current for marker in _CHILD_SELF_MARKERS
            ):
                # 患儿档案：孩子就是患者本人，不能当家属过滤
                kept.append(current)
                continue
            if _patient_self_mentioned(current):
                # 并列主语（我和我爸都吃华法林）：患者本人的用药与
                # 过敏描述仍在分句里，保守保留整句
                kept.append(current)
                continue
            narration = _FAMILY_RELAY_RE.search(current)
            if narration is not None:
                captured = next(
                    (
                        group for group in narration.groups()
                        if group and group.strip()
                    ),
                    "",
                ).strip()
                if captured and not any(
                    captured.startswith(marker)
                    for marker in _FAMILY_MARKER_TAIL
                ) and not captured.lower().startswith("my "):
                    kept.append(captured)
                    continue
            if _patient_continuation_follows(clauses, index, current):
                # 「我爸吃华法林，我也在吃」：后一分句是患者本人的
                # 同一药物/过敏原续用，不能把家属分句整句丢弃
                kept.append(current)
                continue
            drop_next = True
            continue
        kept.append(current)
    return "，".join(kept)


def check_medication_safety(
    question: str,
    context: Any = None,
) -> Dict[str, Any]:
    """根据主诉、用药和过敏史生成用药安全提示。

    Returns:
        {
            "level": "warning" | "info" | "none",
            "alerts": [{"severity", "title", "detail"}, ...]
        }
    """
    question = normalize_brand_drug_names(_flatten(question).lower())
    question = _patient_medication_text(question, context)
    context = context if isinstance(context, dict) else {}
    medications = normalize_brand_drug_names(
        medication_context_text(context).lower()
    )
    allergies = normalize_brand_drug_names(
        _allergy_text(context, question)
    )

    # 药物相互作用的判断只使用主诉与当前用药，避免把过敏史里的药名当成正在服用。
    drug_text = " ".join(
        part for part in (
            _strip_allergy_statements(question), medications
        ) if part
    )

    alerts: List[Dict[str, str]] = []

    # 过敏史优先保留：即使同时命中多种相互作用，也不应被 MAX_ALERTS 截掉
    for group_label, allergy_terms, drug_groups in ALLERGY_RULES:
        if not any(_term_matches(allergies, term) for term in allergy_terms):
            continue
        for group in drug_groups:
            drugs = _find_drugs(drug_text, group)
            if not drugs:
                continue
            alerts.append({
                "severity": "warning",
                "title": f"{group_label}过敏史与{'、'.join(drugs)}",
                "detail": (
                    f"记录到「{group_label}」过敏史，本次描述或用药中出现"
                    f"「{'、'.join(drugs)}」。相关药物可能存在交叉过敏，"
                    "请勿自行服用，先让医生或药师确认。"
                ),
            })
            break

    for title, group_a, group_b, detail in INTERACTION_RULES:
        drugs_a = _find_drugs(drug_text, group_a)
        drugs_b = _find_drugs(drug_text, group_b)
        if not drugs_a or not drugs_b:
            continue
        pair = "、".join(dict.fromkeys([*drugs_a, *drugs_b]))
        alerts.append({
            "severity": "warning",
            "title": title,
            "detail": f"检测到同时涉及：{pair}。{detail}",
        })

    for title, group, detail, severity in DUPLICATE_GROUPS:
        drugs = _find_drugs(drug_text, group)
        if len(drugs) < 2:
            continue
        alerts.append({
            "severity": severity,
            "title": title,
            "detail": f"检测到：{'、'.join(drugs)}。{detail}",
        })

    if _find_drugs(drug_text, "cephalosporins") and _has_alcohol_use(
        drug_text
    ):
        alerts.append({
            "severity": "info",
            "title": "头孢类与酒精",
            "detail": "部分头孢类药物与酒精同用可能出现双硫仑样反应。用药期间及停药后数天内建议避免饮酒，具体请咨询医生或药师。",
        })

    if _find_drugs(drug_text, "acetaminophen") and _has_alcohol_use(
        drug_text
    ):
        alerts.append({
            "severity": "warning",
            "title": "对乙酰氨基酚与酒精",
            "detail": "酒精会增加对乙酰氨基酚的肝毒性风险。服药期间避免饮酒，也不要与其他含对乙酰氨基酚的复方感冒药重复使用。",
        })

    alerts = _dedupe(alerts)
    level = "none"
    if any(alert["severity"] == "warning" for alert in alerts):
        level = "warning"
    elif alerts:
        level = "info"

    return {"level": level, "alerts": alerts}
