"""化验结果带入临床计算器的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"


def test_calculators_have_lab_prefill_buttons():
    html = (WEB_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="calc-egfr-lab"' in html
    assert 'id="calc-fib4-lab"' in html
    assert 'id="calc-meld-lab"' in html


def test_lab_prefill_handles_aliases_and_units():
    script = (WEB_DIR / "app.js").read_text(encoding="utf-8")
    assert "prefillEgfrFromLab" in script
    assert "prefillFib4FromLab" in script
    assert "prefillMeldFromLab" in script
    assert "LAB_PREFILL_ALIASES" in script
    assert "/api/lab-results/latest" in script
    assert "88.4" in script
    assert "17.1" in script
    assert "calcEgfrLab.addEventListener" in script
