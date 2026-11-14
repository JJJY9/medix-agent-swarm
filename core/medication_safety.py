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
    r"(?:没有|没吃|没喝|没在吃|没再吃|未用|未服|未|否认|并非|无需|不用|"
    r"不要|避免|不需要|不必|无|不吃|不服用|不再吃|不在吃|没吃了?)"
    r"[^，。；,.!?！？\n]{0,4}$"
)
#: 依从性限定词：「没有按时吃/未规律服用」仍是当前用药，不是未用药
_ADHERENCE_USE_PREFIX = re.compile(
    r"(?:按时|规律|按医嘱|遵医嘱|每天|天天|坚持|准时|定点|按顿|按需)"
)
#: 「没有停用」「不要停药」表示仍在用药，不能当作未用药
_NEGATED_STOP_PREFIX = re.compile(
    r"(?:没有|没|未|无|不要|不用|不必|不需要|无需|避免|并非"
    r"|不能|不可|不宜|不让|不应|不准|别)"
    r"[^，。；,.!?！？\n]{0,4}(?:停用|停药|停服|停止服用|不再服用)\s*$"
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
    r"前几天|前几周|前几个月|前几年|最近|目前|现在|"
    r"[0-9一二两三四五六七八九十半几]+(?:个)?(?:月|天|周|年)"
    r"(?:多)?(?:前|以来)?)?"
    r"\s*(?:我|本人|现在|目前)?\s*"
    r"(?:已经|已|刚刚|刚|早就|就|才|后来|之后)*\s*"
    r"(?:停用了|停用|停药|停服|停止服用|不再服用|停了|停掉|已停)"
    r"(?!\s*(?:可以吗|行吗|好吗|行不行|可以么|是否|吗))"
)
#: 「想/打算/准备停用华法林」是意图，当前仍在用药
_INTENT_STOP_PREFIX = re.compile(
    r"(?:想|打算|准备|计划|考虑|要不要|是否|让|叫我|建议|嘱咐|叮嘱|要求|医嘱)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:停用|停药|停服|停了|停掉)\s*$"
)
#: 「准备停药/能停药吗」是意向或疑问，不能当作已停药
_INTENT_STOP_SUFFIX = re.compile(
    r"^[^，。；,.!?！？\n]{0,4}"
    r"(?:准备|打算|计划|想要|想|考虑|能|能否|能不能|可以|可否|"
    r"是否|要不要|该不该|何时|什么时候)\s*"
    r"(?:停用|停药|停服|停了|停掉)"
)
#: 药名之前带时长的停药表述：「我已经停用两个月的华法林」
_STOPPED_USE_PREFIX_LONG = re.compile(
    r"(?:已经|已|早就|刚刚|刚)?\s*(?:停用|停药|停服|停止服用|不再服用|停了)"
    r"[^，。；,.!?！？]{0,6}?(?:月|天|周|年)(?:了|多)?\s*的?\s*$"
)
#: 药名之后的「不能停药/未停药」= 仍在服用
_NEGATED_STOP_SUFFIX = re.compile(
    r"^[^，。；,.!?！？\n]{0,4}(?:不能|不可|不宜|不让|不应|不要|别|"
    r"未|没有|没)\s*(?:停用|停药|停服|停止服用)"
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
#: 近期停药（1–2 周内）仍有抗凝作用：按当前用药处理
_RECENT_STOP_PREFIX = re.compile(
    r"(?:刚刚?|今天|昨天|前天|前几天|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前)"
    r"[^，。；,.!?！？\n]{0,4}(?:已|刚|才)?\s*"
    r"(?:停用|停药|停了|停服|停止服用)\s*$"
)
_RECENT_STOP_SUFFIX = re.compile(
    r"^\s*(?:停用|停药|停了|停服|停止服用)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:今天|昨天|前天|几天|几天前|一周|"
    r"[0-9一二两三四五六七八九十]+\s*天)"
)
#: 近期停药的任意语序（药名前/后的文本拼接后判断）：
#: 「停了华法林三天了」「华法林昨天停了」「刚把华法林停了」
_RECENT_STOP_CONTEXT = re.compile(
    r"(?:刚刚?|今天|昨天|前天|前几天|几天|几周|"
    r"[0-9一二两三四五六七八九十]+\s*(?:天|周)前?)"
    r"[^，。；,.!?！？\n]{0,4}(?:停用|停药|停了|停服|停止服用)"
    r"|(?:停用|停药|停了|停服|停止服用)"
    r"[^，。；,.!?！？\n]{0,4}"
    r"(?:今天|昨天|前天|几天|一周|"
    r"[0-9一二两三四五六七八九十]+\s*天)"
)
_RECENT_STOP_PREFIX_EN = re.compile(
    r"(?:just|recently|yesterday|today)\s+"
    r"(?:stopped|discontinued)\s*$",
    re.IGNORECASE,
)
_RECENT_STOP_SUFFIX_EN = re.compile(
    r"^\s*(?:was\s+|has\s+been\s+|had\s+been\s+)?"
    r"(?:stopped|discontinued)\s+"
    r"(?:(?:\d+\s*)?(?:days?|weeks?)\s+ago|yesterday|today)",
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
#: 药名之后跨分句出现的续用/重启表述：「停了半年，最近又吃上了」
_RESTART_USE_SUFFIX = re.compile(
    r"^[^。；;！!？?\n]{0,24}"
    r"(?:又吃上|又用上|又服上|重新吃|重新服用|重新用|恢复服用|恢复吃"
    r"|又开始吃|又开始服|再次服用|继续服用|继续吃|还在吃|还在服用"
    r"|仍在吃|仍在服用|仍然服用)"
)
_RESTART_USE_SUFFIX_EN = re.compile(
    r"\b(?:restarted|started (?:it )?again|still taking|still on"
    r"|continuing (?:it|to take))\b",
    re.IGNORECASE,
)
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
            if _NEGATED_STOP_PREFIX.search(prefix):
                # 「没有停用」= 仍在服用
                return True
            if (
                _INTENT_STOP_PREFIX.search(prefix)
                or _INTENT_STOP_SUFFIX.search(suffix)
                or _NEGATED_STOP_SUFFIX.search(suffix)
                or _CONTINUOUS_USE_PREFIX.search(prefix)
                or _RESTART_USE_SUFFIX.search(long_suffix)
                or _RESTART_USE_SUFFIX_EN.search(long_suffix)
                or _ADHERENCE_USE_PREFIX.search(prefix)
                and _NEGATED_USE_PREFIX.search(prefix)
                or _RECENT_STOP_PREFIX.search(prefix)
                or _RECENT_STOP_PREFIX_EN.search(prefix)
                or _RECENT_STOP_SUFFIX.search(suffix)
                or _RECENT_STOP_SUFFIX_EN.search(suffix)
                or _RECENT_STOP_CONTEXT.search(prefix + suffix)
            ):
                return True
            if (
                _STOPPED_USE_PREFIX.search(prefix)
                or _STOPPED_USE_PREFIX_LONG.search(prefix)
                or _STOPPED_USE_SUFFIX.search(suffix)
                or _STOPPED_USE_SUFFIX_EN.search(suffix)
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
            _NEGATED_STOP_PREFIX.search(prefix)
            or _NEGATED_STOP_SUFFIX.search(suffix)
            or _INTENT_STOP_SUFFIX.search(suffix)
            or _INTENT_STOP_PREFIX.search(prefix)
            or _CONTINUOUS_USE_PREFIX.search(prefix)
            or _RESTART_USE_SUFFIX.search(long_suffix)
            or _RECENT_STOP_PREFIX.search(prefix)
            or _RECENT_STOP_SUFFIX.search(suffix)
            or _RECENT_STOP_CONTEXT.search(prefix + suffix)
            or (
                _ADHERENCE_USE_PREFIX.search(prefix)
                and _NEGATED_USE_PREFIX.search(prefix)
            )
        ):
            return True
        if (
            _STOPPED_USE_PREFIX.search(prefix)
            or _STOPPED_USE_PREFIX_LONG.search(prefix)
            or _STOPPED_USE_SUFFIX.search(suffix)
            or _NEGATED_USE_PREFIX.search(prefix)
            or _PAST_USE_PREFIX.search(prefix)
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
    return _find_drugs(_flatten(text).lower(), group)


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
        r"([^，。；,.!?！？\n]{1,20}?)\s*过敏(?!史|的药|的药物)"
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
            prefix = question[max(0, match.start() - 10):match.start()]
            if _ALLERGY_NEGATION_PATTERN.search(prefix):
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
    question = _flatten(question).lower()
    context = context if isinstance(context, dict) else {}
    medications = medication_context_text(context).lower()
    allergies = _allergy_text(context, question)

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
