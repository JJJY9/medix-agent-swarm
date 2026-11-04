"""常用临床计算工具（确定性公式）。

只做计算与分级，不提供治疗决策。所有结果都需要医生结合病史、
实验室质量与其他风险因素判断。
"""
from __future__ import annotations

from typing import Dict, List

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


PHQ9_QUESTIONS = (
    "做事时提不起劲或没有兴趣",
    "感到心情低落、沮丧或绝望",
    "入睡困难、睡不安稳或睡得过多",
    "感觉疲倦或没有活力",
    "食欲不振或吃太多",
    "觉得自己很糟，或觉得自己很失败，或让自己/家人失望",
    "对事物专注有困难，例如看报纸或看电视时",
    "动作或说话速度缓慢到别人已经察觉，或正好相反——烦躁或坐立不安、动来动去",
    "有不如死掉或用某种方式伤害自己的念头",
)


def calculate_phq9(answers: List[int]) -> Dict[str, object]:
    """PHQ-9 抑郁筛查评分（0–27 分）。

    仅用于近两周的筛查，不用于诊断。第 9 题大于 0 时标记 urgent，
    提示需要立即获得专业帮助。
    """
    if not isinstance(answers, list) or len(answers) != len(PHQ9_QUESTIONS):
        raise ValueError("PHQ-9 需要 9 个答案。")

    values = []
    for answer in answers:
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ValueError("PHQ-9 每个答案必须是 0–3 的整数。")
        if answer < 0 or answer > 3:
            raise ValueError("PHQ-9 每个答案必须在 0–3 之间。")
        values.append(answer)

    score = sum(values)
    if score <= 4:
        severity = "无或极轻"
    elif score <= 9:
        severity = "轻度"
    elif score <= 14:
        severity = "中度"
    elif score <= 19:
        severity = "中重度"
    else:
        severity = "重度"

    self_harm = values[8] > 0
    note = "PHQ-9 是近两周的筛查工具，不能替代临床诊断；结果需要由医生或心理专业人员结合访谈判断。"
    if self_harm:
        note += " 第 9 题提示存在自伤念头，请立即联系家人并寻求精神心理科或急诊帮助。"

    return {
        "score": score,
        "severity": severity,
        "self_harm": self_harm,
        "urgent": self_harm,
        "note": note,
    }


GAD7_QUESTIONS = (
    "感到紧张、焦虑或急切",
    "不能停止或控制担忧",
    "对各种各样的事情担忧过多",
    "很难放松下来",
    "由于不安而无法静坐",
    "变得容易烦恼或急躁",
    "感到似乎将有可怕的事情发生而害怕",
)


def calculate_gad7(answers: List[int]) -> Dict[str, object]:
    """GAD-7 焦虑筛查评分（0–21 分），仅用于近两周筛查。"""
    if not isinstance(answers, list) or len(answers) != len(GAD7_QUESTIONS):
        raise ValueError("GAD-7 需要 7 个答案。")

    values = []
    for answer in answers:
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ValueError("GAD-7 每个答案必须是 0–3 的整数。")
        if answer < 0 or answer > 3:
            raise ValueError("GAD-7 每个答案必须在 0–3 之间。")
        values.append(answer)

    score = sum(values)
    if score <= 4:
        severity = "无或极轻"
    elif score <= 9:
        severity = "轻度"
    elif score <= 14:
        severity = "中度"
    else:
        severity = "重度"

    return {
        "score": score,
        "severity": severity,
        "note": "GAD-7 是近两周的焦虑筛查工具，不能替代临床诊断；结果需要由医生或心理专业人员结合访谈判断。",
    }


def calculate_has_bled(
    age: int,
    hypertension: bool = False,
    abnormal_renal: bool = False,
    abnormal_liver: bool = False,
    stroke_history: bool = False,
    bleeding_history: bool = False,
    labile_inr: bool = False,
    drugs: bool = False,
    alcohol: bool = False,
) -> Dict[str, object]:
    """HAS-BLED 房颤抗凝出血风险评分（0–9 分）。

    仅用于出血风险分层；高分不是抗凝禁忌，需医生结合卒中风险综合判断。
    """
    criteria = []
    if hypertension:
        criteria.append("高血压（收缩压 > 160 mmHg）")
    if abnormal_renal:
        criteria.append("肾功能异常")
    if abnormal_liver:
        criteria.append("肝功能异常")
    if stroke_history:
        criteria.append("卒中史")
    if bleeding_history:
        criteria.append("出血史或出血倾向")
    if labile_inr:
        criteria.append("INR 不稳定")
    if int(age) > 65:
        criteria.append("年龄 > 65 岁")
    if drugs:
        criteria.append("合用抗血小板药或 NSAIDs")
    if alcohol:
        criteria.append("酒精过量")

    score = len(criteria)
    risk = "低危" if score <= 2 else "高危"
    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "HAS-BLED 用于出血风险分层，评分高不等于不能抗凝；请医生结合 CHA₂DS₂-VASc、肾功能与患者意愿综合评估。",
    }


def calculate_gcs(eye: int, verbal: int, motor: int) -> Dict[str, object]:
    """GCS 格拉斯哥昏迷评分（3–15 分）。

    仅用于意识水平评估，需结合瞳孔、生命体征与病因。
    """
    values = {"eye": eye, "verbal": verbal, "motor": motor}
    limits = {"eye": (1, 4), "verbal": (1, 5), "motor": (1, 6)}
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("GCS 各项必须是整数。")
        low, high = limits[key]
        if value < low or value > high:
            raise ValueError(f"GCS {key} 必须在 {low}–{high} 之间。")

    score = int(eye) + int(verbal) + int(motor)
    if score >= 13:
        severity = "轻度"
    elif score >= 9:
        severity = "中度"
    else:
        severity = "重度"

    return {
        "score": score,
        "severity": severity,
        "eye": int(eye),
        "verbal": int(verbal),
        "motor": int(motor),
        "note": "GCS ≤ 8 分通常提示需要气道保护，请立即由急诊或重症团队评估；评分需结合瞳孔、生命体征与病因。",
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
