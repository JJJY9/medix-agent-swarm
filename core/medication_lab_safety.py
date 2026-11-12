"""用药与最近化验的交叉安全提示（确定性规则）。

把在用药物与近 180 天化验结果做规则核对：ACEI/ARB/保钾利尿剂与血钾、
二甲双胍与 eGFR、他汀与转氨酶、华法林与 INR、地高辛与血药浓度、
利尿剂与电解质。只提示「请与开药医生或药师核对」，不给出剂量调整、
停药或诊断建议，也不能替代处方审核。
"""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from core.clinical_tools import calculate_egfr
from core.lab_values import normalize_lab_measurement, normalize_unit_key
from core.medication_safety import find_drugs

MAX_ALERTS = 5

_ALT_UNITS = {"u/l": 1.0, "iu/l": 1.0, "ukat/l": 60.0}
_DIGOXIN_UNITS = {"ng/ml": 1.0, "ug/l": 1.0, "ng/l": 0.001}
_EGFR_UNITS = {"ml/min/1.73m2": 1.0, "ml/min": 1.0}

_TEXT_SPLIT = re.compile(r"[\s\-_/()（）\[\]{}:：,，.。+]+")


def _text(value: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).lower().split()
    )


def _compact(text: str) -> str:
    return _TEXT_SPLIT.sub("", text)


def _ascii_token(text: str, token: str) -> bool:
    return re.search(
        rf"(?<![a-z]){re.escape(token)}(?![a-z])", text
    ) is not None


@lru_cache(maxsize=2048)
def _classify_extra(
    text: str, unit_key: str
) -> Optional[Tuple[str, str, str, float]]:
    """缓存（项目名, 单位）→（分析物, 标签, 规范单位, 换算系数）。"""
    compact = _compact(text)
    if not compact:
        return None

    if (
        any(alias in compact for alias in ("谷丙转氨酶", "丙氨酸氨基转移酶"))
        or _ascii_token(text, "alt")
        or _ascii_token(text, "gpt")
    ):
        if (
            any(
                token in compact
                for token in ("比值", "ratio", "astalt", "altast", "比率")
            )
            or ("谷草转氨酶" in compact and "谷丙转氨酶" in compact)
        ):
            return None
        factor = _ALT_UNITS.get(unit_key)
        if factor is None:
            return None
        return ("alt", "ALT", "U/L", factor)

    if "肾小球滤过率" in compact or _ascii_token(text, "egfr"):
        factor = _EGFR_UNITS.get(unit_key)
        if factor is None:
            return None
        return ("egfr", "eGFR", "mL/min/1.73m²", factor)

    if "地高辛" in compact or _ascii_token(text, "digoxin"):
        factor = _DIGOXIN_UNITS.get(unit_key)
        if factor is None:
            return None
        return ("digoxin", "地高辛浓度", "ng/mL", factor)
    return None


def _normalize_extra(
    item: Any, value: Any, unit: Any
) -> Optional[Dict[str, Any]]:
    """识别 ALT、eGFR 与地高辛浓度并换算为规范单位。"""
    classification = _classify_extra(_text(item), normalize_unit_key(unit))
    if classification is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    analyte, label, unit_label, factor = classification
    decimals = 4 if analyte == "digoxin" else 2
    return {
        "analyte": analyte,
        "label": label,
        "value": round(number * factor, decimals),
        "unit": unit_label,
        "factor": factor,
    }


def _collect_measurements(
    labs: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """按分析物收集最近一次可换算的化验结果。"""
    measurements: Dict[str, Dict[str, Any]] = {}
    for row in labs:
        if not isinstance(row, dict):
            continue
        item = row.get("item")
        panel = str(row.get("panel") or "")
        value = row.get("value")
        unit = row.get("unit")
        # 标本类型可能只写在分组里（如 panel=尿电解质、item=钾）
        match_text = f"{item or ''} {panel}".strip()
        measured = normalize_lab_measurement(
            match_text, value, unit
        ) or _normalize_extra(match_text, value, unit)
        if not measured:
            continue
        ref_high = row.get("ref_high")
        factor = measured.pop("factor", None)
        if factor and ref_high not in (None, ""):
            try:
                ref_high = round(float(ref_high) * factor, 2)
            except (TypeError, ValueError, OverflowError):
                ref_high = None
        record = {
            **measured,
            "tested_at": str(row.get("tested_at") or ""),
            "ref_high": ref_high,
            "item": item,
        }
        key = record["analyte"]
        current = measurements.get(key)
        # 列表按检测时间倒序返回；同时间保留先出现（较新 id）的一条
        if current is None or record["tested_at"] > current["tested_at"]:
            measurements[key] = record
    return measurements


def _parse_age(value: Any) -> Optional[int]:
    match = re.search(r"\d{1,3}", str(value or ""))
    if not match:
        return None
    age = int(match.group())
    return age if 18 <= age <= 120 else None


def _egfr_from(
    measurements: Dict[str, Dict[str, Any]], context: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """采用最近一次 eGFR：已登记结果与按肌酐估算结果比较检测时间。"""
    candidates: List[Dict[str, Any]] = []
    lab = measurements.get("egfr")
    if lab:
        candidates.append({
            "value": float(lab["value"]),
            "source": "化验",
            "tested_at": lab["tested_at"],
        })
    creatinine = measurements.get("creatinine")
    if creatinine:
        try:
            creatinine_value = float(creatinine["value"])
        except (TypeError, ValueError, OverflowError):
            creatinine_value = 0.0
        age = _parse_age(context.get("age"))
        sex = str(context.get("sex") or "").strip()
        if (
            creatinine_value > 0
            and age is not None
            and sex in ("男", "女", "male", "female", "m", "f")
        ):
            try:
                result = calculate_egfr(
                    age, sex, creatinine_value, "umol/L"
                )
            except (TypeError, ValueError, ZeroDivisionError):
                result = None
            if result is not None:
                candidates.append({
                    "value": float(result["egfr"]),
                    "source": "按最近肌酐估算",
                    "tested_at": creatinine["tested_at"],
                })
    if not candidates:
        return None
    # 时间相同时优先直接登记的 eGFR
    candidates.sort(key=lambda item: item["tested_at"], reverse=True)
    return candidates[0]


def _date_text(tested_at: Any) -> str:
    return str(tested_at or "").replace("T", " ")[:10]


def _alert(title: str, detail: str) -> Dict[str, str]:
    return {"severity": "warning", "title": title, "detail": detail}


def check_medication_lab_safety(
    medications: List[str],
    labs: List[Dict[str, Any]],
    context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """返回药物与最近化验交叉风险提示；无命中时 alerts 为空。"""
    context = context if isinstance(context, dict) else {}
    medication_names = [
        str(name) for name in (medications or []) if str(name or "").strip()
    ]
    if not medication_names or not labs:
        return {"alerts": []}
    measurements = _collect_measurements(labs)
    if not measurements:
        return {"alerts": []}

    def find_any(group: str) -> List[str]:
        found: List[str] = []
        for medication in medication_names:
            for drug in find_drugs(medication, group):
                if drug not in found:
                    found.append(drug)
        return found

    raas = find_any("raas_blockers")
    sparing = find_any("potassium_sparing")
    metformin = find_any("metformin")
    statins = find_any("statins")
    warfarin = find_any("warfarin")
    digoxin = find_any("digoxin")
    diuretics = (
        find_any("loop_diuretics")
        + find_any("thiazides")
    )

    alerts: List[Dict[str, str]] = []
    potassium = measurements.get("potassium")

    if potassium and float(potassium["value"]) > 5.0:
        drugs = raas + [
            name for name in sparing if name not in raas
        ]
        if drugs:
            alerts.append(_alert(
                f"{'、'.join(drugs)}与血钾偏高",
                f"最近血钾 {potassium['value']:g} mmol/L"
                f"（{_date_text(potassium['tested_at'])}）。"
                "这类药物可能进一步升高血钾，请与开药医生或药师核对，"
                "不要自行调整剂量或停药。",
            ))

    egfr = _egfr_from(measurements, context)
    if raas and egfr and egfr["value"] < 60:
        alerts.append(_alert(
            f"{'、'.join(raas)}与肾功能下降",
            f"最近 eGFR {egfr['value']:g} mL/min/1.73m²"
            f"（{egfr['source']}，{_date_text(egfr['tested_at'])}）。"
            "肾功能下降时这类药物需要医生评估，"
            "请与开药医生或药师核对。",
        ))
    if metformin and egfr and egfr["value"] < 45:
        alerts.append(_alert(
            f"{'、'.join(metformin)}与 eGFR 下降",
            f"最近 eGFR {egfr['value']:g} mL/min/1.73m²"
            f"（{egfr['source']}，{_date_text(egfr['tested_at'])}）。"
            "二甲双胍在肾功能明显下降时需要"
            "医生或药师评估，请勿自行停药或调整剂量。",
        ))

    alt = measurements.get("alt")
    if statins and alt:
        try:
            ref_high = float(alt.get("ref_high"))
        except (TypeError, ValueError, OverflowError):
            ref_high = 0.0
        if ref_high > 0 and float(alt["value"]) > 3 * ref_high:
            ratio = float(alt["value"]) / ref_high
            alerts.append(_alert(
                f"{'、'.join(statins)}与转氨酶升高",
                f"最近 ALT {alt['value']:g} U/L（参考上限 "
                f"{ref_high:g} U/L，约 {ratio:.1f} 倍，"
                f"{_date_text(alt['tested_at'])}）。他汀类药物可能影响"
                "肝功能，请与开药医生或药师核对。",
            ))

    inr = measurements.get("inr")
    if warfarin and inr and float(inr["value"]) > 3.0:
        alerts.append(_alert(
            f"{'、'.join(warfarin)}与 INR 升高",
            f"最近 INR {inr['value']:g}"
            f"（{_date_text(inr['tested_at'])}），出血风险增加，"
            "请与开药医生或药师核对。",
        ))

    digoxin_level = measurements.get("digoxin")
    if digoxin and digoxin_level and float(digoxin_level["value"]) > 2.0:
        alerts.append(_alert(
            f"{'、'.join(digoxin)}与地高辛浓度偏高",
            f"最近地高辛浓度 {digoxin_level['value']:g} ng/mL"
            f"（{_date_text(digoxin_level['tested_at'])}），"
            "请与开药医生或药师核对。",
        ))

    if diuretics:
        parts = []
        stamps = []
        if potassium and float(potassium["value"]) < 3.5:
            parts.append(f"血钾 {potassium['value']:g} mmol/L")
            stamps.append(potassium["tested_at"])
        sodium = measurements.get("sodium")
        if sodium and float(sodium["value"]) < 135:
            parts.append(f"血钠 {sodium['value']:g} mmol/L")
            stamps.append(sodium["tested_at"])
        if parts:
            when = _date_text(max(stamps)) if stamps else ""
            alerts.append(_alert(
                f"{'、'.join(diuretics)}与电解质偏低",
                f"最近 {'、'.join(parts)}（{when}）。利尿剂可能导致"
                "电解质丢失，请与开药医生或药师核对。",
            ))

    unique: List[Dict[str, str]] = []
    seen = set()
    for item in alerts:
        if item["title"] in seen:
            continue
        seen.add(item["title"])
        unique.append(item)
    return {"alerts": unique[:MAX_ALERTS]}
