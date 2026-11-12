"""慢病与用药风险提示（确定性规则）。

把慢病清单中的活动疾病与在用药物做规则核对：哮喘/COPD 与非选择性β受体阻滞剂、
消化性溃疡/消化道出血与 NSAIDs/抗凝抗血小板、CKD/心衰与 NSAIDs、肝硬化与
NSAIDs/苯二氮䓬、青光眼/前列腺增生与抗胆碱能药、糖尿病与全身糖皮质激素。

只提示「请与开药医生或药师核对」，不判定禁用，不建议自行停药，
也不能替代处方审核。疾病名为自由文本，匹配不到时不猜测。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Sequence, Tuple

from core.medication_safety import find_drugs

MAX_ALERTS = 8

#: 全身糖皮质激素的常见写法（局部制剂在规则中单独排除）
_STEROID_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("泼尼松龙", ("泼尼松龙", "prednisolone")),
    ("泼尼松", ("泼尼松", "强的松", "prednisone")),
    ("地塞米松", ("地塞米松", "dexamethasone")),
    ("甲泼尼龙", ("甲泼尼龙", "methylprednisolone")),
    ("氢化可的松", ("氢化可的松", "hydrocortisone")),
    ("倍他米松", ("倍他米松", "betamethasone")),
)

#: 局部用激素的剂型关键词；命中时不按全身用药提示
_TOPICAL_ROUTES = (
    "滴眼", "眼液", "滴耳", "乳膏", "软膏", "外用", "喷雾",
    "吸入", "鼻喷", "贴剂", "贴膏", "凝胶", "洗剂", "栓",
)

#: 每条规则的疾病关键词、药物组、标题与说明
_RULES: Tuple[Dict[str, Any], ...] = (
    {
        "key": "asthma_copd",
        "keywords": (
            "哮喘", "慢阻肺", "慢性阻塞性肺", "copd", "asthma",
        ),
        "drug_groups": ("beta_blockers_nonselective",),
        "title": "哮喘/慢阻肺与非选择性β受体阻滞剂",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "非选择性β受体阻滞剂可能诱发支气管痉挛，请与开药医生或药师核对，"
            "不要自行停药。"
        ),
    },
    {
        "key": "peptic_ulcer",
        "keywords": (
            "胃溃疡", "十二指肠溃疡", "十二指肠球部溃疡",
            "复合性溃疡", "消化道溃疡", "消化性溃疡",
            "消化道出血", "胃出血",
        ),
        "drug_groups": ("nsaids", "anticoagulants_antiplatelets"),
        "title": "溃疡/消化道出血与出血风险药物",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "NSAIDs、抗凝或抗血小板药物可能增加消化道出血风险，"
            "请与开药医生或药师核对。"
        ),
    },
    {
        "key": "ckd",
        "keywords": (
            "慢性肾脏病", "肾功能不全", "肾功能下降", "肾功能衰竭",
            "肾衰竭", "肾衰", "尿毒症", "ckd",
        ),
        "drug_groups": ("nsaids",),
        "title": "肾功能不全与 NSAIDs",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "NSAIDs 可能加重肾功能损害，请与开药医生或药师核对。"
        ),
    },
    {
        "key": "heart_failure",
        "keywords": (
            "心力衰竭", "心功能不全", "心功能衰竭", "心衰",
            "heart failure", "hfref", "hfpef",
        ),
        "drug_groups": ("nsaids",),
        "title": "心力衰竭与 NSAIDs",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "NSAIDs 可能导致水钠潴留并加重心衰，请与开药医生或药师核对。"
        ),
    },
    {
        "key": "cirrhosis",
        "keywords": ("肝硬化", "肝功能不全", "肝衰竭", "cirrhosis"),
        "drug_groups": ("nsaids", "benzodiazepines"),
        "title": "肝硬化与镇静/出血风险药物",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "肝硬化患者使用 NSAIDs 或苯二氮䓬类药物需谨慎，"
            "请与开药医生或药师核对。"
        ),
    },
    {
        "key": "glaucoma",
        "keywords": ("青光眼", "glaucoma"),
        "exclude_keywords": ("开角型", "open-angle", "open angle"),
        "drug_groups": ("anticholinergics",),
        "title": "青光眼与抗胆碱能药物",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "抗胆碱能药物可能诱发闭角型青光眼发作，"
            "请与开药医生或药师核对。"
        ),
    },
    {
        "key": "bph",
        "keywords": (
            "前列腺增生", "前列腺肥大", "bph", "benign prostatic",
        ),
        "drug_groups": ("anticholinergics",),
        "title": "前列腺增生与抗胆碱能药物",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "抗胆碱能药物可能加重排尿困难，请与开药医生或药师核对。"
        ),
    },
    {
        "key": "diabetes_steroid",
        "keywords": ("糖尿病", "diabetes"),
        "drug_groups": ("corticosteroids",),
        "title": "糖尿病与全身糖皮质激素",
        "detail": (
            "记录到「{condition}」，在用药物包含「{drugs}」。"
            "全身糖皮质激素可能升高血糖，请与开药医生或药师核对。"
        ),
    },
)


def _compact(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\-_/()（）:：,，.。]+", "", text)


def _condition_names(conditions: Sequence[Any]) -> List[str]:
    names: List[str] = []
    for item in conditions or []:
        raw = item.get("name") if isinstance(item, dict) else item
        name = " ".join(str(raw or "").split())
        if name:
            names.append(name)
    return names


_INACTIVE_MARKERS = ("已治愈", "治愈后", "家族史", "已好转", "已恢复")

#: 这些词说明疾病仍在活动期，前面的「未/无」不是否定
_ACTIVE_MARKERS = (
    "未控制", "未治疗", "未愈", "未规范治疗", "未达标",
    "控制不佳", "控制不良", "没有控制好", "没控制好", "控制不好",
    "未规律", "未缓解", "未稳定", "未愈合", "未手术",
)


def _is_inactive_condition(name: str) -> bool:
    normalized = unicodedata.normalize("NFKC", name).lower()
    if "未治愈" in normalized:
        return False
    return any(marker in normalized for marker in _INACTIVE_MARKERS)


def _negated_mention(normalized: str, keyword: str) -> bool:
    """判断疾病名是否以否定/已排除的语气提到该关键词。"""
    if "未治愈" in normalized:
        return False
    token = keyword.lower()
    # 活动状态限定语只在包含该疾病的分句内生效，避免
    # 「无糖尿病，血压未达标」把未达标算到糖尿病头上
    clauses = re.split(r"[，。；,;：:\s]+", normalized)
    target_clause = next(
        (clause for clause in clauses if token in clause), ""
    )
    if any(marker in target_clause for marker in _ACTIVE_MARKERS):
        return False
    if re.search(
        rf"(?:无|没有|否认|排除|非|未)[^，。；,;:：]{{0,6}}"
        rf"{re.escape(token)}",
        normalized,
    ):
        return True
    if re.search(
        rf"\b(?:no|without|denies)\b[a-z ]{{0,20}}"
        rf"{re.escape(token)}"
        rf"|negative for[a-z ]{{0,20}}{re.escape(token)}",
        normalized,
    ):
        return True
    return re.search(
        rf"{re.escape(token)}[a-z ]{{0,10}}(?:resolved|in remission)",
        normalized,
    ) is not None


def _match_condition(
    names: Sequence[str],
    keywords: Sequence[str],
    excludes: Sequence[str] = (),
) -> str:
    for name in names:
        if _is_inactive_condition(name):
            continue
        normalized = unicodedata.normalize("NFKC", name).lower()
        compact = _compact(name)
        for keyword in keywords:
            token = keyword.lower()
            if token.isascii():
                if not re.search(
                    rf"(?<![a-z]){re.escape(token)}(?![a-z])", normalized
                ):
                    continue
            elif token not in compact:
                continue
            if excludes and any(
                str(item).lower() in normalized for item in excludes
            ):
                continue
            if _negated_mention(normalized, keyword):
                continue
            return name
    return ""


def _steroid_drugs(medications: Sequence[str]) -> List[str]:
    """只返回全身用糖皮质激素，局部制剂（滴眼液/乳膏等）不提示。"""
    found: List[str] = []
    for medication in medications or []:
        text = " ".join(str(medication or "").split())
        if not text:
            continue
        normalized = unicodedata.normalize("NFKC", text).lower()
        compact = _compact(normalized)
        if any(route in compact for route in _TOPICAL_ROUTES):
            continue
        for canonical, aliases in _STEROID_ALIASES:
            hit = False
            for alias in aliases:
                token = alias.lower()
                if token.isascii():
                    if re.search(
                        rf"(?<![a-z]){re.escape(token)}(?![a-z])",
                        normalized,
                    ):
                        hit = True
                        break
                elif token in compact:
                    hit = True
                    break
            if hit:
                if canonical not in found:
                    found.append(canonical)
                break
    return found


def _merge_drugs(drugs: Sequence[str]) -> List[str]:
    """去重并移除被更长药名包含的短名（如「肝素」与「依诺肝素」）。"""
    unique: List[str] = []
    for drug in sorted(drugs, key=len, reverse=True):
        if drug and drug not in unique:
            unique.append(drug)
    return [
        drug for drug in unique
        if not any(
            drug != other and drug in other for other in unique
        )
    ]


def check_condition_drug_safety(
    medications: Sequence[str],
    conditions: Sequence[Any],
) -> Dict[str, Any]:
    """返回慢病与在用药物的风险提示；无命中时 alerts 为空。"""
    medication_names = [
        " ".join(str(name or "").split())
        for name in (medications or [])
        if str(name or "").strip()
    ]
    condition_names = _condition_names(conditions)
    if not medication_names or not condition_names:
        return {"alerts": []}

    alerts: List[Dict[str, str]] = []
    for rule in _RULES:
        condition = _match_condition(
            condition_names,
            rule["keywords"],
            rule.get("exclude_keywords", ()),
        )
        if not condition:
            continue
        if rule["key"] == "diabetes_steroid":
            drugs = _steroid_drugs(medication_names)
        else:
            drugs: List[str] = []
            for medication in medication_names:
                for group in rule["drug_groups"]:
                    for drug in find_drugs(medication, group):
                        if drug not in drugs:
                            drugs.append(drug)
        drugs = _merge_drugs(drugs)
        if not drugs:
            continue
        alerts.append({
            "severity": "warning",
            "title": f"{rule['title']}：{'、'.join(drugs)}",
            "detail": rule["detail"].format(
                condition=condition, drugs="、".join(drugs)
            ),
        })
    return {"alerts": alerts[:MAX_ALERTS]}
