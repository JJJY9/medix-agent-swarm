"""常用临床计算工具（确定性公式）。

只做计算与分级，不提供治疗决策。所有结果都需要医生结合病史、
实验室质量与其他风险因素判断。
"""
from __future__ import annotations

import math
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


def calculate_wells_dvt(
    active_cancer: bool = False,
    paralysis_or_immobilization: bool = False,
    bedridden_or_surgery: bool = False,
    deep_vein_tenderness: bool = False,
    entire_leg_swelling: bool = False,
    calf_swelling: bool = False,
    pitting_edema: bool = False,
    collateral_veins: bool = False,
    previous_dvt: bool = False,
    alternative_diagnosis: bool = False,
) -> Dict[str, object]:
    """Wells 评分（深静脉血栓，DVT）概率分层。

    仅用于临床概率评估，不能替代 D-二聚体、超声等客观检查。
    """
    criteria = []
    score = 0

    def add(flag: bool, points: int, label: str) -> None:
        nonlocal score
        if flag:
            score += points
            criteria.append(f"{label}（{points:+d}）")

    add(active_cancer, 1, "活动性肿瘤")
    add(paralysis_or_immobilization, 1, "瘫痪/近期石膏固定")
    add(bedridden_or_surgery, 1, "卧床 >3 天或 12 周内大手术")
    add(deep_vein_tenderness, 1, "深静脉走行区压痛")
    add(entire_leg_swelling, 1, "整个下肢肿胀")
    add(calf_swelling, 1, "小腿肿胀 >3 cm")
    add(pitting_edema, 1, "患肢凹陷性水肿")
    add(collateral_veins, 1, "浅表侧支静脉")
    add(previous_dvt, 1, "既往 DVT")
    add(alternative_diagnosis, -2, "其他诊断可能性不低于 DVT")

    if score <= 0:
        risk = "低概率"
    elif score <= 2:
        risk = "中概率"
    else:
        risk = "高概率"

    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "Wells 评分用于 DVT 概率分层，不能替代 D-二聚体、静脉超声等检查；高概率或症状进行性加重请尽快就医。",
    }


def calculate_child_pugh(
    bilirubin: int,
    albumin: int,
    inr: int,
    ascites: int,
    encephalopathy: int,
) -> Dict[str, object]:
    """Child-Pugh 肝硬化严重程度评分（5–15 分）。

    每项取 1/2/3 分，A 级 5–6 分、B 级 7–9 分、C 级 10–15 分。
    """
    values = {
        "bilirubin": bilirubin,
        "albumin": albumin,
        "inr": inr,
        "ascites": ascites,
        "encephalopathy": encephalopathy,
    }
    for key, value in values.items():
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("Child-Pugh 每项必须是整数。")
        if value < 1 or value > 3:
            raise ValueError("Child-Pugh 每项必须在 1–3 分之间。")

    score = sum(values.values())
    if score <= 6:
        grade = "A"
    elif score <= 9:
        grade = "B"
    else:
        grade = "C"

    return {
        "score": score,
        "grade": grade,
        "components": values,
        "note": "Child-Pugh 用于肝硬化肝功能储备分层，需结合病因、并发症与临床判断；C 级提示肝功能储备较差。",
    }


def calculate_wells_pe(
    clinical_signs_dvt: bool = False,
    pe_more_likely: bool = False,
    heart_rate_gt_100: bool = False,
    immobilization_or_surgery: bool = False,
    previous_dvt_pe: bool = False,
    hemoptysis: bool = False,
    malignancy: bool = False,
) -> Dict[str, object]:
    """Wells 评分（肺栓塞，PE）概率分层。

    仅用于临床概率评估，不能替代 D-二聚体、CTPA 等检查。
    """
    criteria = []
    score = 0.0

    def add(flag: bool, points: float, label: str) -> None:
        nonlocal score
        if flag:
            score += points
            criteria.append(f"{label}（{points:g}）")

    add(clinical_signs_dvt, 3, "存在 DVT 临床体征")
    add(pe_more_likely, 3, "PE 可能性高于其他诊断")
    add(heart_rate_gt_100, 1.5, "心率 > 100 次/分")
    add(immobilization_or_surgery, 1.5, "4 周内制动或手术")
    add(previous_dvt_pe, 1.5, "既往 DVT/PE")
    add(hemoptysis, 1, "咯血")
    add(malignancy, 1, "活动性肿瘤")

    if score < 2:
        risk = "低概率"
    elif score <= 6:
        risk = "中概率"
    else:
        risk = "高概率"

    return {
        "score": round(score, 1),
        "risk": risk,
        "criteria": criteria,
        "note": "Wells 评分用于 PE 概率分层，不能替代 D-二聚体、CTPA 等检查；出现呼吸困难加重、晕厥或血氧下降请立即就医。",
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


def calculate_qsofa(
    respiratory_rate: float,
    systolic_bp: float,
    altered_mentation: bool,
) -> Dict[str, object]:
    """qSOFA 脓毒症风险筛查（0–3 分）。"""
    criteria = []
    if float(respiratory_rate) >= 22:
        criteria.append("呼吸频率 ≥ 22 次/分")
    if float(systolic_bp) <= 100:
        criteria.append("收缩压 ≤ 100 mmHg")
    if altered_mentation:
        criteria.append("意识状态改变（GCS < 15）")

    score = len(criteria)
    risk = "高风险" if score >= 2 else "低风险"
    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "qSOFA ≥ 2 分提示脓毒症风险较高，应尽快由医生评估；qSOFA < 2 不能排除脓毒症。",
    }


def calculate_fib4(
    age: int,
    ast: float,
    alt: float,
    platelets: float,
) -> Dict[str, object]:
    """FIB-4 肝纤维化指数，血小板单位为 ×10⁹/L。"""
    score = float(age) * float(ast) / (float(platelets) * math.sqrt(float(alt)))
    if score < 1.3:
        risk = "低风险"
        detail = "进展性肝纤维化可能性较低"
    elif score <= 2.67:
        risk = "中风险"
        detail = "结果处于灰区，需结合弹性成像或其他无创指标"
    else:
        risk = "高风险"
        detail = "进展性肝纤维化可能性较高"

    return {
        "fib4": round(score, 2),
        "risk": risk,
        "detail": detail,
        "note": "FIB-4 适用于成人慢性肝病风险分层；65 岁以上人群阈值需上调，急性肝损伤或血小板/转氨酶异常会影响结果。",
    }


def calculate_meld(
    bilirubin: float,
    inr: float,
    creatinine: float,
    dialysis: bool = False,
) -> Dict[str, object]:
    """经典 MELD 终末期肝病评分（胆红素、肌酐单位 mg/dL）。"""
    bilirubin = max(1.0, float(bilirubin))
    inr = max(1.0, float(inr))
    creatinine = 4.0 if dialysis else max(1.0, float(creatinine))
    creatinine = min(creatinine, 4.0)

    raw = (
        3.78 * math.log(bilirubin)
        + 11.2 * math.log(inr)
        + 9.57 * math.log(creatinine)
        + 6.43
    )
    score = max(6, min(int(round(raw)), 40))

    if score < 10:
        risk = "低风险"
    elif score < 20:
        risk = "中风险"
    elif score < 30:
        risk = "高风险"
    else:
        risk = "极高风险"

    return {
        "meld": score,
        "risk": risk,
        "criteria": [
            f"总胆红素 {bilirubin:g} mg/dL",
            f"INR {inr:g}",
            f"血肌酐 {creatinine:g} mg/dL" + ("（透析）" if dialysis else ""),
        ],
        "note": "MELD 用于终末期肝病短期预后评估；不能替代肝移植评估，胆红素与肌酐需按 mg/dL 输入。",
    }
