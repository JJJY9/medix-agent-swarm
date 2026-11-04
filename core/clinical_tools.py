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


ESS_QUESTIONS = [
    "坐着阅读时",
    "看电视时",
    "在公共场所坐着不动时（如开会、剧院）",
    "作为乘客连续乘车 1 小时",
    "午后躺下休息时",
    "坐着与人交谈时",
    "午餐后安静坐着（未饮酒）",
    "开车时等红灯或堵车几分钟",
]

ESS_OPTIONS = [(0, "从不"), (1, "很少"), (2, "有时"), (3, "经常")]


def calculate_ess(answers: List[int]) -> Dict[str, object]:
    """Epworth 嗜睡量表（ESS，0–24 分）。

    仅用于日间嗜睡筛查，不能替代睡眠监测与医生诊断。
    """
    if not isinstance(answers, list) or len(answers) != len(ESS_QUESTIONS):
        raise ValueError("ESS 需要 8 个答案。")
    values = []
    for answer in answers:
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ValueError("ESS 每个答案必须是 0–3 的整数。")
        if answer < 0 or answer > 3:
            raise ValueError("ESS 每个答案必须在 0–3 之间。")
        values.append(answer)

    score = sum(values)
    if score <= 7:
        drowsiness = "正常"
    elif score <= 9:
        drowsiness = "轻度嗜睡"
    elif score <= 15:
        drowsiness = "中度嗜睡"
    else:
        drowsiness = "重度嗜睡"

    return {
        "score": score,
        "drowsiness": drowsiness,
        "note": "ESS 用于评估日间嗜睡程度，结果需结合睡眠史与医生判断，不能替代诊断。",
    }


CAT_QUESTIONS = [
    "我从不咳嗽 —— 我总在咳嗽",
    "我一点痰也没有 —— 我有很多很多痰",
    "我一点也没有胸闷的感觉 —— 我有很重的胸闷的感觉",
    "当我爬坡或爬一层楼时，我并不感到喘不过气来 —— 爬坡或爬一层楼时我非常喘不过气",
    "我在家里的任何劳动都不受慢阻肺影响 —— 家里的任何活动都很受慢阻肺影响",
    "每当我外出时就外出 —— 因为慢阻肺我很少外出",
    "我睡得很好 —— 因为慢阻肺我睡眠不好",
    "我精力旺盛 —— 我一点精力都没有",
]


def calculate_cat(answers: List[int]) -> Dict[str, object]:
    """CAT 慢阻肺评估测试（0–40 分）。

    仅用于症状负担评估，不用于诊断；分数越高提示影响越大。
    """
    if not isinstance(answers, list) or len(answers) != len(CAT_QUESTIONS):
        raise ValueError("CAT 需要 8 个答案。")
    values = []
    for answer in answers:
        if isinstance(answer, bool) or not isinstance(answer, int):
            raise ValueError("CAT 每个答案必须是 0–5 的整数。")
        if answer < 0 or answer > 5:
            raise ValueError("CAT 每个答案必须在 0–5 之间。")
        values.append(answer)

    score = sum(values)
    if score <= 10:
        impact = "轻微影响"
    elif score <= 20:
        impact = "中等影响"
    elif score <= 30:
        impact = "严重影响"
    else:
        impact = "非常严重影响"

    return {
        "score": score,
        "impact": impact,
        "note": "CAT 用于评估慢阻肺对生活的影响，不能替代肺功能检查与医生诊断。",
    }


MMRC_LABELS = {
    0: "仅在剧烈活动时感到气短",
    1: "快走或上缓坡时气短",
    2: "因气短比同龄人走得慢，或按自己的速度行走时需要停下休息",
    3: "平地行走约 100 米或几分钟后需要停下休息",
    4: "因气短不能离家，或穿衣时即感气短",
}


def calculate_mmrc(grade: int) -> Dict[str, object]:
    """mMRC 呼吸困难量表（0–4 级）。

    仅用于呼吸困难程度评估，不能替代肺功能检查与医生诊断。
    """
    if isinstance(grade, bool) or not isinstance(grade, int):
        raise ValueError("mMRC 等级必须是 0–4 的整数。")
    if grade < 0 or grade > 4:
        raise ValueError("mMRC 等级必须在 0–4 之间。")

    if grade <= 1:
        severity = "较轻"
    elif grade == 2:
        severity = "中等"
    else:
        severity = "较重"

    return {
        "grade": grade,
        "label": MMRC_LABELS[grade],
        "severity": severity,
        "note": "mMRC 为呼吸困难分级量表，结果需结合肺功能与医生评估，不能替代诊断。",
    }


def calculate_audit_c(q1: int, q2: int, q3: int) -> Dict[str, object]:
    """AUDIT-C 酒精使用筛查评分（0–12 分）。

    仅用于筛查风险饮酒，不用于诊断；男性 ≥4 分、女性 ≥3 分通常提示
    需要进一步评估，结果应结合医生判断。
    """
    values = []
    for value in (q1, q2, q3):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("AUDIT-C 每个答案必须是 0–4 的整数。")
        if value < 0 or value > 4:
            raise ValueError("AUDIT-C 每个答案必须在 0–4 之间。")
        values.append(value)

    score = sum(values)
    if score <= 3:
        risk = "低风险"
    elif score <= 7:
        risk = "偏高"
    else:
        risk = "高风险"

    return {
        "score": score,
        "risk": risk,
        "note": "AUDIT-C 是酒精使用筛查工具，不能替代临床诊断；男性 ≥4 分、女性 ≥3 分通常提示风险饮酒，请结合医生判断。",
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

def calculate_stop_bang(
    snoring: bool = False,
    tired: bool = False,
    observed_apnea: bool = False,
    hypertension: bool = False,
    bmi_gt_35: bool = False,
    age_gt_50: bool = False,
    neck_gt_40cm: bool = False,
    male: bool = False,
) -> Dict[str, object]:
    """STOP-BANG 阻塞性睡眠呼吸暂停筛查（0–8 分）。"""
    criteria = []
    if snoring:
        criteria.append("打鼾（Snoring）")
    if tired:
        criteria.append("白天嗜睡/疲乏（Tired）")
    if observed_apnea:
        criteria.append("目击呼吸暂停（Observed）")
    if hypertension:
        criteria.append("高血压（Pressure）")
    if bmi_gt_35:
        criteria.append("BMI > 35 kg/m²（B）")
    if age_gt_50:
        criteria.append("年龄 > 50 岁（A）")
    if neck_gt_40cm:
        criteria.append("颈围 > 40 cm（N）")
    if male:
        criteria.append("男性（G）")

    score = len(criteria)
    if score >= 5:
        risk = "高风险"
    elif score >= 3:
        risk = "中风险"
    else:
        risk = "低风险"

    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "STOP-BANG 用于阻塞性睡眠呼吸暂停筛查；≥3 分建议进一步睡眠监测，确诊与治疗需专科评估。",
    }


def calculate_padua(
    active_cancer: bool = False,
    previous_vte: bool = False,
    reduced_mobility: bool = False,
    thrombophilia: bool = False,
    recent_trauma_surgery: bool = False,
    age_ge_70: bool = False,
    heart_respiratory_failure: bool = False,
    acute_mi_stroke: bool = False,
    acute_infection_rheumatologic: bool = False,
    bmi_ge_30: bool = False,
    hormonal_treatment: bool = False,
) -> Dict[str, object]:
    """Padua 内科住院患者静脉血栓风险评分。"""
    criteria = []
    score = 0

    def add(points: int, label: str, present: bool) -> None:
        nonlocal score
        if present:
            score += points
            criteria.append(f"{label}（+{points}）")

    add(3, "活动性肿瘤", active_cancer)
    add(3, "既往 VTE", previous_vte)
    add(3, "活动受限", reduced_mobility)
    add(3, "已知易栓症", thrombophilia)
    add(2, "近期创伤或手术", recent_trauma_surgery)
    add(1, "年龄 ≥ 70 岁", age_ge_70)
    add(1, "心衰或呼吸衰竭", heart_respiratory_failure)
    add(1, "急性心肌梗死或缺血性卒中", acute_mi_stroke)
    add(1, "急性感染或风湿性疾病", acute_infection_rheumatologic)
    add(1, "BMI ≥ 30 kg/m²", bmi_ge_30)
    add(1, "正在接受激素治疗", hormonal_treatment)

    risk = "高风险" if score >= 4 else "低风险"
    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "Padua 评分用于内科住院患者静脉血栓风险分层；≥4 分提示高风险，是否预防性抗凝需医生结合出血风险决定。",
    }

def calculate_abcd2(
    age_ge_60: bool = False,
    bp_ge_140_90: bool = False,
    unilateral_weakness: bool = False,
    speech_disturbance: bool = False,
    duration_ge_60: bool = False,
    duration_10_59: bool = False,
    diabetes: bool = False,
) -> Dict[str, object]:
    """ABCD2 TIA 后短期卒中风险评分（0–7 分）。"""
    criteria = []
    score = 0

    if age_ge_60:
        score += 1
        criteria.append("年龄 ≥ 60 岁（+1）")
    if bp_ge_140_90:
        score += 1
        criteria.append("血压 ≥ 140/90 mmHg（+1）")
    if unilateral_weakness:
        score += 2
        criteria.append("单侧肢体无力（+2）")
    elif speech_disturbance:
        score += 1
        criteria.append("言语障碍不伴肢体无力（+1）")
    if duration_ge_60:
        score += 2
        criteria.append("症状持续 ≥ 60 分钟（+2）")
    elif duration_10_59:
        score += 1
        criteria.append("症状持续 10–59 分钟（+1）")
    if diabetes:
        score += 1
        criteria.append("糖尿病（+1）")

    if score >= 4:
        risk = "高风险"
    elif score >= 2:
        risk = "中风险"
    else:
        risk = "低风险"

    return {
        "score": score,
        "risk": risk,
        "criteria": criteria,
        "note": "ABCD2 用于短暂性脑缺血发作后的短期卒中风险分层；出现 TIA 症状应尽快就医，评分不能替代急诊评估。",
    }


def calculate_morse_fall(
    history_of_falling: int = 0,
    secondary_diagnosis: int = 0,
    ambulatory_aid: int = 0,
    intravenous: int = 0,
    gait: int = 0,
    mental_status: int = 0,
) -> Dict[str, object]:
    """Morse 跌倒风险评分（0–125 分）。"""
    score = int(history_of_falling) + int(secondary_diagnosis) + int(
        ambulatory_aid
    ) + int(intravenous) + int(gait) + int(mental_status)

    if score >= 45:
        risk = "高风险"
    elif score >= 25:
        risk = "中风险"
    else:
        risk = "低风险"

    return {
        "score": score,
        "risk": risk,
        "criteria": [
            f"既往跌倒 {int(history_of_falling)} 分",
            f"次要诊断 {int(secondary_diagnosis)} 分",
            f"行走辅助 {int(ambulatory_aid)} 分",
            f"静脉输液 {int(intravenous)} 分",
            f"步态 {int(gait)} 分",
            f"精神状态 {int(mental_status)} 分",
        ],
        "note": "Morse 跌倒风险评分用于住院患者跌倒风险筛查；≥45 分高风险，需采取防跌倒措施并由护理团队评估。",
    }
