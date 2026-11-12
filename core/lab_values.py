"""化验危急值提示（确定性规则）。

只覆盖成人常见、各机构通用性较高的危急值阈值，用于把需要立即处理的
化验数值与普通偏高/偏低区分开。阈值会因实验室、年龄与妊娠状态而异，
提示语只建议复测并联系医生，不提供诊断或处置建议。

单位缺失或无法识别时不做判断（INR 等本身无单位的项目除外），
避免因单位错配产生误报。
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

#: 结果超过该天数时在提示后附加「结果较旧」说明
STALE_AFTER_DAYS = 180

#: 每个分析物的别名、单位换算与成人危急值阈值
#: low/high 为规范单位下的阈值（严格小于/大于才触发）
ANALYTES: Tuple[Dict[str, Any], ...] = (
    {
        "key": "potassium",
        "label": "血钾",
        "cjk": ("钾", "血钾", "血清钾", "钾离子"),
        "ascii": ("k", "potassium"),
        "exclude": ("尿", "urine"),
        "units": {"mmol/l": 1.0, "meq/l": 1.0},
        "unit_label": "mmol/L",
        "low": 2.5,
        "high": 6.5,
        "low_message": (
            "血钾 {value} mmol/L 明显偏低，可能影响心律，"
            "请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血钾 {value} mmol/L 明显升高，可能影响心律，"
            "请立即联系医生或前往急诊。"
        ),
    },
    {
        "key": "sodium",
        "label": "血钠",
        "cjk": ("钠", "血钠", "血清钠", "钠离子"),
        "ascii": ("na", "sodium"),
        "exclude": ("尿", "urine", "钠肽", "脑钠肽", "bnp"),
        "units": {"mmol/l": 1.0, "meq/l": 1.0},
        "unit_label": "mmol/L",
        "low": 120.0,
        "high": 160.0,
        "low_message": (
            "血钠 {value} mmol/L 明显偏低，请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血钠 {value} mmol/L 明显升高，请立即联系医生或前往急诊。"
        ),
    },
    {
        "key": "glucose",
        "label": "血糖",
        "cjk": ("血糖", "葡萄糖"),
        "ascii": ("glu", "glucose"),
        "exclude": (
            "尿", "urine", "糖化", "hba1c", "脑脊液", "csf",
            "胸水", "腹水", "浆膜腔", "积液", "关节液", "滑膜液",
            "pleural", "ascites",
        ),
        "units": {"mmol/l": 1.0, "mg/dl": 1 / 18.0},
        "unit_label": "mmol/L",
        "low": 2.8,
        "high": 22.2,
        "low_message": (
            "血糖 {value} mmol/L 明显偏低，警惕低血糖，"
            "请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血糖 {value} mmol/L 明显升高，警惕高血糖急症，"
            "请立即就医。"
        ),
    },
    {
        "key": "hemoglobin",
        "label": "血红蛋白",
        "cjk": ("血红蛋白", "血色素"),
        "ascii": ("hgb", "hb", "hemoglobin", "haemoglobin"),
        "exclude": (
            "糖化", "hba1c", "a1c", "平均红细胞", "mch", "mchc",
            "尿", "urine", "胎儿", "hbf",
        ),
        "units": {"g/l": 1.0, "g/dl": 10.0},
        "unit_label": "g/L",
        "low": 50.0,
        "high": 200.0,
        "low_message": (
            "血红蛋白 {value} g/L 明显偏低，请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血红蛋白 {value} g/L 明显升高，请尽快就医评估。"
        ),
    },
    {
        "key": "platelet",
        "label": "血小板",
        "cjk": ("血小板计数", "血小板"),
        "ascii": ("plt", "platelet", "platelets"),
        "exclude": (
            "尿", "urine", "压积", "分布宽度", "平均体积", "mpv",
            "pdw", "pct", "抗体",
        ),
        "unit_family": "count",
        "unit_label": "×10⁹/L",
        "low": 20.0,
        "high": 1000.0,
        "low_message": (
            "血小板 {value} ×10⁹/L 明显偏低，存在出血风险，"
            "请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血小板 {value} ×10⁹/L 明显升高，请尽快就医评估。"
        ),
    },
    {
        "key": "wbc",
        "label": "白细胞",
        "cjk": ("白细胞计数", "白细胞"),
        "ascii": ("wbc", "leukocyte", "leukocytes"),
        "exclude": ("尿", "urine", "分类", "介素", "抗体"),
        "unit_family": "count",
        "unit_label": "×10⁹/L",
        "low": 1.0,
        "high": 50.0,
        "low_message": (
            "白细胞 {value} ×10⁹/L 明显偏低，感染风险升高，"
            "请立即联系医生。"
        ),
        "high_message": (
            "白细胞 {value} ×10⁹/L 明显升高，请尽快就医评估。"
        ),
    },
    {
        "key": "inr",
        "label": "INR",
        "cjk": ("国际标准化比值", "凝血酶原时间国际标准化"),
        "ascii": ("inr",),
        "exclude": (),
        "units": {},
        "unitless": True,
        "unit_label": "",
        "low": None,
        "high": 5.0,
        "low_message": "",
        "high_message": (
            "INR {value} 明显升高，出血风险增加，请立即联系医生。"
        ),
    },
    {
        "key": "creatinine",
        "label": "肌酐",
        "cjk": ("肌酐", "血肌酐", "血清肌酐"),
        "ascii": ("cr", "crea", "creatinine"),
        "exclude": ("尿", "urine", "清除", "clearance", "内生"),
        "units": {"umol/l": 1.0, "mg/dl": 88.4},
        "unit_label": "μmol/L",
        "low": None,
        "high": 707.0,
        "low_message": "",
        "high_message": (
            "肌酐 {value} μmol/L 明显升高，请立即联系医生或前往急诊。"
        ),
    },
    {
        "key": "ionized_calcium",
        "label": "离子钙",
        "cjk": ("离子钙", "游离钙", "钙离子"),
        "ascii": ("ica",),
        "exclude": ("尿", "urine"),
        "units": {"mmol/l": 1.0, "mg/dl": 0.25},
        "unit_label": "mmol/L",
        "low": 0.8,
        "high": 1.6,
        "low_message": (
            "离子钙 {value} mmol/L 明显偏低，请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "离子钙 {value} mmol/L 明显升高，请立即联系医生或前往急诊。"
        ),
    },
    {
        "key": "calcium",
        "label": "血钙",
        "cjk": ("钙", "血钙", "血清钙", "总钙"),
        "ascii": ("ca", "calcium"),
        "exclude": ("尿", "urine", "钙卫蛋白", "calprotectin"),
        "units": {"mmol/l": 1.0, "mg/dl": 0.25},
        "unit_label": "mmol/L",
        "low": 1.5,
        "high": 3.5,
        "low_message": (
            "血钙 {value} mmol/L 明显偏低，请立即联系医生或前往急诊。"
        ),
        "high_message": (
            "血钙 {value} mmol/L 明显升高，请立即联系医生或前往急诊。"
        ),
    },
)


def _compact(text: Any) -> str:
    """去掉空白与常见分隔符，便于中文项目名匹配。"""
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    return re.sub(
        r"[\s\-_/()（）\[\]{}:：,，.。+]+", "", normalized.lower()
    )


def _unit_key(unit: Any) -> str:
    # NFKC 把全角字母/斜杠与 ⁹、³ 等上标统一成 ASCII
    text = unicodedata.normalize("NFKC", str(unit or ""))
    text = (
        text.strip().lower()
        .replace("μ", "u")
        .replace("µ", "u")
        .replace("×", "x")
    )
    return re.sub(r"\s+", "", text)


def _match_ascii(text: str, token: str) -> bool:
    return re.search(
        rf"(?<![a-z]){re.escape(token)}(?![a-z])", text
    ) is not None


def _matches(spec: Dict[str, Any], text: str, compact: str) -> bool:
    for token in spec.get("exclude", ()):
        if token in compact:
            return False
    for alias in spec.get("cjk", ()):
        if len(alias) == 1:
            if compact == alias:
                return True
            # 单字项目（钾/钠/钙）与分组拼成「钾 电解质」时仍应识别；
            # CJK 边界保证不会误匹配 血钾/钾离子/尿钾
            if re.search(
                rf"(?<![一-鿿]){re.escape(alias)}(?![一-鿿])", text
            ):
                return True
        elif alias in compact:
            return True
    for alias in spec.get("ascii", ()):
        if _match_ascii(text, alias):
            return True
    return False


def _lab_item_text(item: Any) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(item or "")).lower().split()
    )


@lru_cache(maxsize=2048)
def _analyte_index(text: str) -> int:
    """把化验项目名解析为 ANALYTES 下标；未识别返回 -1。

    项目名基数很低（真实数据通常几十种），对整表逐行跑 10 组别名正则
    是纯浪费；缓存后每行只剩一次字典查找。
    """
    compact = _compact(text)
    if not compact:
        return -1
    for index, spec in enumerate(ANALYTES):
        if _matches(spec, text, compact):
            return index
    return -1


def _count_factor(unit: str) -> Optional[float]:
    """把白细胞/血小板的常见单位换算为 ×10⁹/L 的系数。"""
    if not unit:
        return None
    # ×10⁹/L、10^9/L、10*9/L、10E9/L 等
    if re.search(r"10[\^*e]?9/l", unit):
        return 1.0
    # 10³/µL 与 K/µL 等价于 ×10⁹/L
    if re.search(r"10[\^*e]?3/ul", unit):
        return 1.0
    if unit.endswith("/ul"):
        # 1 K/µL = 1 ×10⁹/L，1 /µL = 0.001 ×10⁹/L
        prefix = unit[:-3]
        return 1.0 if prefix.endswith("k") else 0.001
    return None


def _normalized_value(
    value: float, unit: Any, spec: Dict[str, Any]
) -> Optional[float]:
    key = _unit_key(unit)
    if spec.get("unit_family") == "count":
        factor = _count_factor(key)
    elif spec.get("unitless"):
        factor = 1.0 if not key or key in {
            "ratio", "比值", "无", "-", "—",
        } else None
    else:
        factor = (spec.get("units") or {}).get(key)
    if factor is None:
        return None
    return value * factor


def _is_stale(tested_at: Any) -> bool:
    text = str(tested_at or "")[:10]
    try:
        tested = datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return False
    return (datetime.now().date() - tested).days > STALE_AFTER_DAYS


def _build_alert(
    spec: Dict[str, Any],
    direction: str,
    normalized_value: float,
    tested_at: Any,
) -> Dict[str, Any]:
    """按规范单位生成提示，数值与单位保持同一套单位。"""
    template = spec.get("high_message" if direction == "high" else "low_message")
    stale = _is_stale(tested_at)
    display = f"{round(normalized_value, 2):g}"
    message = template.format(value=display)
    if stale:
        message += "（该结果已超过 180 天，请复查确认）"
    return {
        "level": "critical",
        "analyte": spec["key"],
        "direction": direction,
        "stale": stale,
        "message": message,
    }


def normalize_unit_key(unit: Any) -> str:
    """公开的单位归一化入口（供用药-化验联动等其他规则复用）。"""
    return _unit_key(unit)


def normalize_lab_measurement(
    item: Any, value: Any, unit: Any
) -> Optional[Dict[str, Any]]:
    """把化验值换算为规范单位；无法识别时返回 None。"""
    spec_index = _analyte_index(_lab_item_text(item))
    if spec_index < 0:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    spec = ANALYTES[spec_index]
    normalized = _normalized_value(number, unit, spec)
    if normalized is None:
        return None
    return {
        "analyte": spec["key"],
        "label": spec["label"],
        "value": round(normalized, 4),
        "unit": spec["unit_label"],
    }


def lab_critical_alerts(
    item: Any,
    value: Any,
    unit: Any,
    tested_at: Any = None,
) -> List[Dict[str, Any]]:
    """返回化验达到危急值时的提示；未识别或无法换算时返回空列表。"""
    spec_index = _analyte_index(_lab_item_text(item))
    if spec_index < 0:
        return []
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return []
    spec = ANALYTES[spec_index]
    normalized = _normalized_value(number, unit, spec)
    if normalized is None:
        return []
    if spec.get("low") is not None and normalized < float(spec["low"]):
        return [_build_alert(spec, "low", normalized, tested_at)]
    if spec.get("high") is not None and normalized > float(spec["high"]):
        return [_build_alert(spec, "high", normalized, tested_at)]
    return []
