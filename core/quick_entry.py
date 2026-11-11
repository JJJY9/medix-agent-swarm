"""一句话健康记录解析（确定性规则）。

把「血压 150/95 早上」「血糖 7.8 空腹」这类短句解析为指标、数值与情境。
数值必须紧邻指标名（或在仅含单位/情境词时按无歧义单位推断），
解析结果只用于预览，确认后仍走现有 /api/metrics 校验与保存；
无法识别、多指标、数值位置不明或越界时明确报错，绝不猜测或静默写入。
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional, Tuple

#: 指标别名（按长度倒序匹配，避免「血氧饱和度」被「血氧」抢先）
_METRIC_ALIASES: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("bp", ("血压", "bp", "mmhg")),
    ("glucose", ("血糖", "葡萄糖", "glu")),
    ("weight", ("体重", "重量", "weight")),
    ("temperature", ("体温", "温度", "temperature")),
    ("heart_rate", ("心率", "脉搏", "heartrate", "heart rate")),
    ("spo2", ("血氧饱和度", "血氧", "spo2")),
    ("waist", ("腰围", "waist")),
    ("sleep", ("睡眠时长", "睡眠", "睡了", "sleep")),
)

#: 未写指标名时按无歧义单位推断；mmol/L、cm 之类可能是其他指标，不推断
_UNIT_METRICS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("bp", ("mmhg",)),
    ("weight", ("kg", "公斤")),
    ("temperature", ("°c", "℃", "度")),
    ("heart_rate", ("bpm", "次/分", "次/分钟")),
    ("spo2", ("%",)),
)

#: 每个指标接受的单位与换算系数（"f_to_c" 表示华氏度转摄氏度）
_METRIC_UNITS: Dict[str, Dict[str, Any]] = {
    "bp": {"mmhg": 1.0},
    "weight": {
        "kg": 1.0, "公斤": 1.0, "千克": 1.0,
        "斤": 0.5, "lb": 0.4536, "磅": 0.4536,
    },
    "glucose": {"mmol/l": 1.0, "mg/dl": 1 / 18.0},
    "temperature": {
        "°c": 1.0, "度": 1.0, "°f": "f_to_c",
    },
    "heart_rate": {"bpm": 1.0, "次/分": 1.0, "次/分钟": 1.0},
    "spo2": {"%": 1.0},
    "waist": {"cm": 1.0, "厘米": 1.0},
}

_ALL_UNIT_TOKENS = sorted(
    {token for table in _METRIC_UNITS.values() for token in table},
    key=len,
    reverse=True,
)

_CONTEXT_WORDS: Tuple[Tuple[str, Tuple[str, ...]], ...] = (
    ("morning", ("晨起", "早上", "清晨", "上午")),
    ("evening", ("睡前", "晚上", "夜间")),
    ("fasting", ("空腹", "餐前")),
    ("postprandial", ("餐后", "饭后")),
    ("bedtime", ("睡前",)),
    ("random", ("随机",)),
)

_CN_DIGITS = {
    "零": 0, "〇": 0, "一": 1, "二": 2, "两": 2, "三": 3,
    "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}

_ASCII_NUMBER = r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)"
_CN_NUMBER = (
    r"[零〇一二两三四五六七八九十百千]+"
    r"(?:点[零〇一二两三四五六七八九]+)?"
)
_NUM_RE = rf"{_ASCII_NUMBER}|{_CN_NUMBER}"
_NUMBER_PATTERN = re.compile(_NUM_RE)
_PAIR_PATTERN = re.compile(rf"({_NUM_RE})\s*[/／]\s*({_NUM_RE})")

#: 数值与指标名的最大距离（字符），超过视为位置不明
_MAX_NUMBER_DISTANCE = 16


class QuickEntryError(ValueError):
    """解析失败；code 供前端区分错误类型。"""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _normalize(text: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(text or ""))
    normalized = " ".join(normalized.lower().split())
    # 「7点8」「37度2」按小数处理；「7点」后面没有数字时保持原样
    normalized = re.sub(r"([0-9])\s*点\s*([0-9])", r"\1.\2", normalized)
    normalized = re.sub(r"([0-9])\s*度\s*([0-9])", r"\1.\2", normalized)
    normalized = re.sub(
        r"(?<=[零〇一二两三四五六七八九十百千])度"
        r"(?=[零〇一二两三四五六七八九])",
        "点",
        normalized,
    )
    return normalized


def _cn_number(text: str) -> Optional[float]:
    """把一百五十、九十五、一百二（=120）、六十二点五转成数值。"""
    if not text:
        return None
    integer_part, _, decimal_part = text.partition("点")
    section = 0
    current = 0
    last_unit = 0
    for char in integer_part:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
        elif char in _CN_UNITS:
            unit = _CN_UNITS[char]
            section += (current or 1) * unit
            last_unit = unit
            current = 0
        else:
            return None
    # 「一百二」按惯例是 120，「一千二」是 1200
    if current and last_unit == 100:
        current *= 10
    elif current and last_unit == 1000:
        current *= 100
    total = section + current
    if decimal_part:
        digits = []
        for char in decimal_part:
            if char not in _CN_DIGITS:
                return None
            digits.append(str(_CN_DIGITS[char]))
        return float(f"{total}." + "".join(digits))
    return float(total)


def _number(text: str) -> Optional[float]:
    if re.fullmatch(r"[+-]?(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+)", text):
        try:
            return float(text)
        except ValueError:
            return None
    return _cn_number(text)


def _alias_span(text: str, alias: str) -> Optional[Tuple[int, int]]:
    if alias.isascii():
        match = re.search(
            rf"(?<![a-z]){re.escape(alias)}(?![a-z])", text
        )
        return match.span() if match else None
    index = text.find(alias)
    return (index, index + len(alias)) if index >= 0 else None


def _metric_hits(text: str) -> List[Tuple[str, Tuple[int, int]]]:
    hits: List[Tuple[str, Tuple[int, int]]] = []
    for metric, aliases in _METRIC_ALIASES:
        span: Optional[Tuple[int, int]] = None
        for alias in sorted(aliases, key=len, reverse=True):
            found = _alias_span(text, alias)
            if found and (span is None or found[0] < span[0]):
                span = found
        if span:
            hits.append((metric, span))
    return hits


def _overlaps(span: Tuple[int, int], other: Tuple[int, int]) -> bool:
    return span[0] < other[1] and other[0] < span[1]


def _distance(span: Tuple[int, int], alias: Tuple[int, int]) -> int:
    if span[1] <= alias[0]:
        return alias[0] - span[1]
    if span[0] >= alias[1]:
        return span[0] - alias[1]
    return -1


def _nearest_number(
    text: str, alias_span: Tuple[int, int], exclude: List[Tuple[int, int]]
) -> Optional[float]:
    """取距离指标名最近且不与别名重叠的数值。"""
    best: Optional[Tuple[Tuple[int, int], str]] = None
    for match in _NUMBER_PATTERN.finditer(text):
        span = match.span()
        if _overlaps(span, alias_span):
            continue
        if any(_overlaps(span, item) for item in exclude):
            continue
        distance = _distance(span, alias_span)
        if distance < 0 or distance > _MAX_NUMBER_DISTANCE:
            continue
        # 距离相同时优先指标名后面的数值
        after = 0 if span[0] >= alias_span[1] else 1
        key = (distance, after, span[0])
        if best is None or key < best[0]:
            best = (key, match.group(0))
    return _number(best[1]) if best else None


def _pair_candidates(
    text: str, alias_span: Tuple[int, int], exclude: List[Tuple[int, int]]
) -> List[Tuple[float, float]]:
    """按与指标名的距离排序返回所有血压数值对。"""
    candidates: List[Tuple[Tuple[int, int, int], float, float]] = []
    for match in _PAIR_PATTERN.finditer(text):
        span = match.span()
        if _overlaps(span, alias_span):
            continue
        if any(_overlaps(span, item) for item in exclude):
            continue
        distance = _distance(span, alias_span)
        if distance < 0 or distance > _MAX_NUMBER_DISTANCE:
            continue
        first = _number(match.group(1))
        second = _number(match.group(2))
        if first is None or second is None:
            continue
        after = 0 if span[0] >= alias_span[1] else 1
        candidates.append(((distance, after, span[0]), first, second))
    candidates.sort(key=lambda item: item[0])
    return [(first, second) for _key, first, second in candidates]


def _infer_metric_from_unit(text: str) -> Optional[str]:
    """仅当整句除数值、单位与情境词外没有其他内容时才推断。"""
    for metric, units in _UNIT_METRICS:
        for unit in units:
            if unit not in text:
                continue
            residue = _NUMBER_PATTERN.sub("", text)
            for _, words in _CONTEXT_WORDS:
                for word in words:
                    residue = residue.replace(word, "")
            residue = residue.replace(unit, "")
            residue = re.sub(r"[\s,，。;；:：/\\()（）\-]+", "", residue)
            if not residue:
                return metric
    return None


def _metric_context(metric: str, text: str) -> str:
    from memory.session_store import METRIC_CONTEXTS

    allowed = METRIC_CONTEXTS.get(metric, ())
    for context, words in _CONTEXT_WORDS:
        if context not in allowed:
            continue
        for word in words:
            if word in text:
                return context
    return ""


def _token_spans(text: str, token: str) -> List[Tuple[int, int]]:
    spans: List[Tuple[int, int]] = []
    if token.isascii() and any(char.isalpha() for char in token):
        match = re.search(
            rf"(?<![a-z]){re.escape(token)}(?![a-z])", text
        )
        if match:
            spans.append(match.span())
    else:
        start = 0
        while True:
            index = text.find(token, start)
            if index < 0:
                break
            spans.append((index, index + len(token)))
            start = index + 1
    return spans


def _units_in(
    text: str, exclude: List[Tuple[int, int]]
) -> List[str]:
    found: List[str] = []
    for token in _ALL_UNIT_TOKENS:
        spans = [
            span for span in _token_spans(text, token)
            if not any(_overlaps(span, item) for item in exclude)
        ]
        if spans:
            found.append(token)
    return found


def _convert_with_unit(
    metric: str,
    label: str,
    text: str,
    values: List[float],
    exclude: List[Tuple[int, int]],
) -> List[float]:
    """按文本中的单位换算数值；未知或不匹配的单位直接报错。"""
    tokens = _units_in(text, exclude)
    if not tokens:
        return values
    allowed = _METRIC_UNITS.get(metric, {})
    unknown = [token for token in tokens if token not in allowed]
    if unknown:
        raise QuickEntryError(
            "unknown_unit",
            f"单位「{unknown[0]}」与{label}不匹配，请使用"
            f"{'、'.join(allowed) or '规范单位'}。",
        )
    if len(set(tokens)) > 1:
        raise QuickEntryError(
            "ambiguous_unit", "一句话里出现多个单位，请只保留一个。"
        )
    factor = allowed[tokens[0]]
    if factor == "f_to_c":
        return [round((value - 32) * 5 / 9, 2) for value in values]
    return [round(value * float(factor), 2) for value in values]


def _validate_range(label: str, value: float, low: float, high: float, unit: str) -> None:
    if not (low <= value <= high):
        raise QuickEntryError(
            "out_of_range",
            f"{label} {value:g} 超出可记录范围（{low:g}–{high:g} {unit}）。",
        )


def parse_quick_metric(text: Any) -> Dict[str, Any]:
    """解析一句话健康记录；失败时抛出 QuickEntryError。"""
    from memory.session_store import METRIC_CONTEXTS, METRIC_TYPES
    from memory.session_store import metric_context_label

    original = str(text or "").strip()
    normalized = _normalize(original)
    if not normalized:
        raise QuickEntryError("empty", "请输入要记录的内容，例如：血压 150/95 早上。")

    hits = _metric_hits(normalized)
    metrics = [metric for metric, _span in hits]
    if len(metrics) > 1:
        raise QuickEntryError(
            "multiple_metrics",
            "一句话里包含多个指标，请分开记录，例如：血压 150/95。",
        )
    if metrics:
        metric = metrics[0]
        alias_span = hits[0][1]
        exclude = [span for _metric, span in hits]
    else:
        metric = _infer_metric_from_unit(normalized)
        if not metric:
            raise QuickEntryError(
                "unknown_metric",
                "没有识别到指标，请写明血压、血糖、体重、体温、心率、血氧、腰围或睡眠时长。",
            )
        alias_span = (0, 0)
        exclude = []

    label, unit, (low, high) = METRIC_TYPES[metric]

    if metric == "bp":
        pairs = _pair_candidates(normalized, alias_span, exclude)
        if not pairs:
            if _nearest_number(normalized, alias_span, exclude) is not None:
                raise QuickEntryError(
                    "bp_needs_two",
                    "血压需要收缩压/舒张压两个数值，例如：血压 150/95。",
                )
            raise QuickEntryError(
                "missing_value",
                "没有在指标附近识别到血压数值，例如：血压 150/95。",
            )
        chosen = None
        for first, second in pairs:
            first, second = _convert_with_unit(
                metric, label, normalized, [first, second], exclude
            )
            if (
                30 <= second <= 200
                and first > second
                and low <= first <= high
            ):
                chosen = (first, second)
                break
        if chosen is None:
            raise QuickEntryError(
                "out_of_range",
                "血压需满足收缩压大于舒张压，且舒张压在 30–200 mmHg。",
            )
        first, second = chosen
    else:
        first = _nearest_number(normalized, alias_span, exclude)
        second = None
        if first is None:
            raise QuickEntryError(
                "missing_value",
                "没有在指标附近识别到数值，例如：体重 62.5。",
            )
        first = _convert_with_unit(
            metric, label, normalized, [first], exclude
        )[0]
        _validate_range(label, first, low, high, unit)

    context = _metric_context(metric, normalized)
    return {
        "metric": metric,
        "label": label,
        "unit": unit,
        "value1": first,
        "value2": second,
        "context": context,
        "context_label": metric_context_label(metric, context),
        "text": original,
        "supports_context": bool(METRIC_CONTEXTS.get(metric)),
    }
