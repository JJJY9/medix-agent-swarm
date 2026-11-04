"""用药安全提示（确定性规则）。

在模型回答之前，根据主诉、正在使用的药物和过敏史做一次规则筛查：
命中明确的重复用药、药物相互作用或过敏交叉风险时，给出可读提示。

规则只覆盖说明书/指南中常见的明确风险，不提供剂量调整或停药建议，
也不能替代医生或药师的判断。宁可少报，也不制造无依据的恐慌。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Tuple

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
        ("含碘造影剂", ("含碘造影剂", "碘造影", "造影剂", "增强ct", "增强 ct", "contrast")),
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
)

ALCOHOL_TERMS = (
    "喝酒", "饮酒", "喝了酒", "酒精", "啤酒", "白酒", "红酒", "药酒",
    "alcohol", "beer", "wine",
)

#: 过敏史 → (展示名, 过敏词, 需要警惕的药物组)
ALLERGY_RULES: Tuple[Tuple[str, Tuple[str, ...], Tuple[str, ...]], ...] = (
    (
        "青霉素类",
        ("青霉素", "penicillin"),
        ("penicillins", "cephalosporins"),
    ),
    (
        "头孢类",
        ("头孢", "cephalosporin"),
        ("cephalosporins",),
    ),
    (
        "磺胺类",
        ("磺胺", "sulfa"),
        ("sulfonamides", "inr_raisers"),
    ),
    (
        "非甾体抗炎药",
        ("阿司匹林", "布洛芬", "双氯芬酸", "萘普生", "nsaid", "aspirin", "ibuprofen", "diclofenac"),
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


def _find_drugs(text: str, group: str) -> List[str]:
    """返回文本中命中的组内规范药名（去重、保持定义顺序）。"""
    found: List[str] = []
    for canonical, aliases in DRUG_GROUPS.get(group, ()):
        if any(alias.lower() in text for alias in aliases):
            if canonical not in found:
                found.append(canonical)
    return found


def _allergy_text(context: Dict[str, Any], question: str) -> str:
    parts = [_flatten(context.get("allergies"))]
    if "过敏" in question:
        parts.append(question)
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
    medications = _flatten(context.get("medications")).lower()
    allergies = _allergy_text(context, question)

    # 药物相互作用的判断只使用主诉与当前用药，避免把过敏史里的药名当成正在服用。
    drug_text = " ".join(part for part in (question, medications) if part)

    alerts: List[Dict[str, str]] = []

    # 过敏史优先保留：即使同时命中多种相互作用，也不应被 MAX_ALERTS 截掉
    for group_label, allergy_terms, drug_groups in ALLERGY_RULES:
        if not any(term.lower() in allergies for term in allergy_terms):
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

    if _find_drugs(drug_text, "cephalosporins") and any(
        term in drug_text for term in ALCOHOL_TERMS
    ):
        alerts.append({
            "severity": "info",
            "title": "头孢类与酒精",
            "detail": "部分头孢类药物与酒精同用可能出现双硫仑样反应。用药期间及停药后数天内建议避免饮酒，具体请咨询医生或药师。",
        })

    if _find_drugs(drug_text, "acetaminophen") and any(
        term in drug_text for term in ALCOHOL_TERMS
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
