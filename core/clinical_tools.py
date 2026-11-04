"""常用临床计算工具（确定性公式）。

只做计算与分级，不提供治疗决策。所有结果都需要医生结合病史、
实验室质量与其他风险因素判断。
"""
from __future__ import annotations

from typing import Dict

#: BMI 中国成人分类界值
BMI_CUTOFFS = (
    (18.5, "偏瘦"),
    (24.0, "正常"),
    (28.0, "超重"),
)
BMI_OBESITY = "肥胖"


def calculate_bmi(height_cm: float, weight_kg: float) -> Dict[str, object]:
    """计算 BMI，并给出中国成人分类与对应健康体重范围。"""
    height_m = float(height_cm) / 100
    bmi = float(weight_kg) / (height_m ** 2)

    category = BMI_OBESITY
    for cutoff, label in BMI_CUTOFFS:
        if bmi < cutoff:
            category = label
            break

    return {
        "bmi": round(bmi, 1),
        "category": category,
        "healthy_weight_min": round(18.5 * height_m ** 2, 1),
        "healthy_weight_max": round(23.9 * height_m ** 2, 1),
        "note": "BMI 不能区分肌肉与脂肪，儿童、孕妇、运动员及水肿患者需结合其他指标。",
    }


def _ckd_stage(egfr: float) -> Dict[str, str]:
    if egfr >= 90:
        return {"stage": "G1", "description": "正常或偏高"}
    if egfr >= 60:
        return {"stage": "G2", "description": "轻度下降"}
    if egfr >= 45:
        return {"stage": "G3a", "description": "轻中度下降"}
    if egfr >= 30:
        return {"stage": "G3b", "description": "中重度下降"}
    if egfr >= 15:
        return {"stage": "G4", "description": "重度下降"}
    return {"stage": "G5", "description": "肾衰竭范围"}


def calculate_egfr(
    age: int,
    sex: str,
    creatinine: float,
    unit: str = "mg/dL",
) -> Dict[str, object]:
    """CKD-EPI 2021（不含种族系数）估算肾小球滤过率。

    Args:
        age: 年龄（≥18 岁）
        sex: "male" / "female"（也接受 男 / 女）
        creatinine: 血肌酐
        unit: "mg/dL" 或 "umol/L"（也接受 μmol/L）
    """
    female = str(sex).lower() in ("female", "f", "女", "女性")
    normalized_unit = str(unit).lower().replace("μ", "u").replace("µ", "u")
    if normalized_unit in ("umol/l", "umoll"):
        scr = float(creatinine) / 88.4
    else:
        scr = float(creatinine)

    kappa = 0.7 if female else 0.9
    alpha = -0.241 if female else -0.302
    ratio = scr / kappa
    egfr = (
        142
        * min(ratio, 1.0) ** alpha
        * max(ratio, 1.0) ** -1.200
        * 0.9938 ** int(age)
    )
    if female:
        egfr *= 1.012

    egfr = round(egfr, 1)
    stage = _ckd_stage(egfr)
    return {
        "egfr": egfr,
        "stage": stage["stage"],
        "description": stage["description"],
        "creatinine_mg_dl": round(scr, 2),
        "note": "CKD-EPI 2021 仅适用于成人稳定状态；急性肾损伤、妊娠、极端肌肉量时结果可能不可靠。",
    }


def calculate_chads_vasc(
    age: int,
    sex: str,
    chf: bool = False,
    hypertension: bool = False,
    diabetes: bool = False,
    stroke_tia: bool = False,
    vascular_disease: bool = False,
) -> Dict[str, object]:
    """CHA₂DS₂-VASc 房颤卒中风险评分（0–9 分）。

    仅做风险分层，是否抗凝必须由医生结合出血风险与个体情况决定。
    """
    female = str(sex).lower() in ("female", "f", "女", "女性")
    age = int(age)
    score = 0

    if chf:
        score += 1
    if hypertension:
        score += 1
    if diabetes:
        score += 1
    if stroke_tia:
        score += 2
    if vascular_disease:
        score += 1
    if age >= 75:
        score += 2
    elif age >= 65:
        score += 1
    if female:
        score += 1

    if (female and score <= 1) or (not female and score == 0):
        risk = "低危"
    elif (female and score == 2) or (not female and score == 1):
        risk = "中危"
    else:
        risk = "高危"

    return {
        "score": score,
        "risk": risk,
        "note": "评分只用于风险分层；是否抗凝需医生结合出血风险、肾功能与患者意愿综合评估。",
    }


def calculate_curb65(
    age: int,
    confusion: bool,
    urea: float,
    respiratory_rate: float,
    systolic_bp: float,
    diastolic_bp: float,
) -> Dict[str, object]:
    """CURB-65 社区获得性肺炎严重程度评分（0–5 分）。

    仅做严重程度分层，不能替代血氧、影像学与合并症评估。
    """
    criteria = []
    if confusion:
        criteria.append("意识障碍")
    if float(urea) > 7:
        criteria.append("尿素氮 > 7 mmol/L")
    if float(respiratory_rate) >= 30:
        criteria.append("呼吸频率 ≥ 30 次/分")
    if float(systolic_bp) < 90 or float(diastolic_bp) <= 60:
        criteria.append("低血压（收缩压 < 90 或舒张压 ≤ 60 mmHg）")
    if int(age) >= 65:
        criteria.append("年龄 ≥ 65 岁")

    score = len(criteria)
    if score <= 1:
        risk = "低危"
    elif score == 2:
        risk = "中危"
    else:
        risk = "高危"

    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "CURB-65 用于社区获得性肺炎严重程度分层；是否住院或入住 ICU 需医生结合血氧、影像与基础疾病判断。",
    }
