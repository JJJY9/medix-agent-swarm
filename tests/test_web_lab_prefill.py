"""化验结果带入临床计算器的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
CORE = (WEB_DIR / "lab-prefill-core.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")


def test_calculators_have_lab_prefill_buttons():
    assert 'id="calc-egfr-lab"' in HTML
    assert 'id="calc-fib4-lab"' in HTML
    assert 'id="calc-meld-lab"' in HTML
    assert (
        '<button type="button" class="btn-quiet" id="calc-curb-lab">'
        in HTML
    )
    assert (
        '<button type="button" class="btn-quiet" id="calc-child-pugh-lab">'
        in HTML
    )
    assert '/static/lab-prefill-core.js' in HTML


def test_lab_prefill_handles_aliases_and_units():
    assert "prefillEgfrFromLab" in SCRIPT
    assert "prefillFib4FromLab" in SCRIPT
    assert "prefillMeldFromLab" in SCRIPT
    assert "LAB_PREFILL_ALIASES" in CORE
    assert "/api/lab-results/latest" in SCRIPT
    assert "88.4" in CORE
    assert "17.1" in CORE
    assert "calcEgfrLab.addEventListener" in SCRIPT


def test_child_pugh_prefill_maps_labs_to_scores():
    assert "prefillChildPughFromLab" in SCRIPT
    assert "labBilirubinUmolL" in SCRIPT
    assert "labAlbuminGL" in SCRIPT
    assert "childPughBilirubinScore" in SCRIPT
    assert "calcChildPughLab.addEventListener" in SCRIPT
    assert "腹水与肝性脑病请按临床情况选择" in SCRIPT
    # 分值边界必须与表单选项区间一致
    assert "childPughBilirubinScore" in CORE
    assert "childPughAlbuminScore" in CORE
    assert "childPughInrScore" in CORE
    assert "valueUmolL < 34" in CORE
    assert "valueUmolL <= 51" in CORE
    assert "valueGL > 35" in CORE
    assert "valueGL >= 28" in CORE
    assert "valueInr < 1.7" in CORE
    assert "valueInr <= 2.3" in CORE
    # 缺失化验不能被当成 0 值
    assert "if (!item) return null;" in CORE


def test_lab_aliases_exclude_related_but_different_items():
    assert "LAB_PREFILL_EXCLUDES" in CORE
    assert "excludes.some" in CORE
    for term in (
        "前白蛋白",
        "微量白蛋白",
        "糖化白蛋白",
        "球蛋白",
        "直接胆红素",
        "间接胆红素",
        "尿酸",
    ):
        assert term in CORE


def test_curb65_prefill_uses_urea_and_latest_bp():
    assert "prefillCurb65FromLab" in SCRIPT
    assert "labUreaMmolL" in SCRIPT
    assert "metric=bp&limit=1" in SCRIPT
    assert "encodeURIComponent(profile)" in SCRIPT
    assert "calcCurbLab.addEventListener" in SCRIPT
    assert "bpFailed" in SCRIPT
    assert "尿素氮" in SCRIPT
    assert "isNitrogen ? 2.8 : 6.0" in CORE


def test_core_module_is_precached_offline():
    sw = (WEB_DIR / "sw.js").read_text(encoding="utf-8")
    assert "/static/lab-prefill-core.js" in sw
