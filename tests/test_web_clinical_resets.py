"""临床计算器跨患者状态与预填重置的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_open_calc_overwrites_patient_age_and_sex():
    start = SCRIPT.index("function openCalc()")
    body = SCRIPT[start:start + 1800]
    # 每次打开都覆盖当前患者的年龄/性别；缺失时清空而不是沿用旧值
    assert "els.calcEgfrAge.value = ageText;" in body
    assert "els.calcChadsAge.value = ageText;" in body
    assert "els.calcCurbAge.value = ageText;" in body
    assert "els.calcHasBledAge.value = ageText;" in body
    assert "els.calcFib4Age.value = ageText;" in body
    assert "els.calcEgfrSex.value = sexText;" in body
    assert "els.calcChadsSex.value = sexText;" in body


def test_curb_prefill_resets_manual_fields():
    start = SCRIPT.index("async function prefillCurb65FromLab")
    body = SCRIPT[start:start + 1800]
    assert "els.calcCurbSbp.value = '';" in body
    assert "els.calcCurbDbp.value = '';" in body
    assert "els.calcCurbRr.value = '';" in body
    assert "els.calcCurbConfusion.checked = false;" in body


def test_child_pugh_prefill_resets_manual_selects():
    start = SCRIPT.index("async function prefillChildPughFromLab")
    body = SCRIPT[start:start + 2200]
    assert "els.calcChildAscites.value = '1';" in body
    assert "els.calcChildEncephalopathy.value = '1';" in body


def test_apri_prefill_restores_default_uln():
    start = SCRIPT.index("async function prefillApriFromLab")
    body = SCRIPT[start:start + 1800]
    assert "els.calcApriUln.value = '40';" in body
    # ULN 可能带小数，输入框不能限制为整数步长
    assert 'id="calc-apri-uln"' in HTML
    assert 'step="any"' in HTML


def test_weekly_summary_compares_both_bp_values():
    start = SCRIPT.index("function weeklyMetricText(row)")
    body = SCRIPT[start:start + 1600]
    assert "row.prev_avg_value2" in body
    assert "diffs.every" in body


def test_bp_reference_matches_target_boundary():
    # 目标 135/85 按严格大于判超标，参考文本不能写成 <135/85
    assert "≤135/85 mmHg" in SCRIPT


def test_condition_template_refreshes_targets():
    start = SCRIPT.index("async function applyConditionTemplate")
    body = SCRIPT[start:start + 1600]
    assert "await loadMetricTargets();" in body
    assert "refreshMetrics();" in body
