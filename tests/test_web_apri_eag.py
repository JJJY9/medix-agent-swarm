"""APRI 与 eAG 计算器前端入口的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")
HTML = (WEB_DIR / "index.html").read_text(encoding="utf-8")
CORE = (WEB_DIR / "lab-prefill-core.js").read_text(encoding="utf-8")


def test_apri_form_and_prefill_exist():
    assert 'id="calc-form-apri"' in HTML
    assert 'data-tool="apri"' in HTML
    assert 'id="calc-apri-ast"' in HTML
    assert 'id="calc-apri-platelets"' in HTML
    assert 'id="calc-apri-uln"' in HTML
    assert '<option value="apri">' in HTML
    assert "submitApri" in SCRIPT
    assert "prefillApriFromLab" in SCRIPT
    assert "/api/tools/apri" in SCRIPT
    assert "calcFormApri.addEventListener('submit', submitApri)" in SCRIPT
    assert "calcApriLab.addEventListener" in SCRIPT
    assert "calcApriAst: document.getElementById('calc-apri-ast')" in SCRIPT
    assert "els.calcFormApri," in SCRIPT
    assert "AST 正常上限按化验单带入" in SCRIPT


def test_eag_form_and_prefill_exist():
    assert 'id="calc-form-eag"' in HTML
    assert 'data-tool="eag"' in HTML
    assert 'id="calc-eag-hba1c"' in HTML
    assert '<option value="eag">' in HTML
    assert "submitEag" in SCRIPT
    assert "prefillEagFromLab" in SCRIPT
    assert "/api/tools/eag" in SCRIPT
    assert "calcFormEag.addEventListener('submit', submitEag)" in SCRIPT
    assert "calcEagLab.addEventListener" in SCRIPT
    assert "calcFormEag: document.getElementById('calc-form-eag')" in SCRIPT
    assert "els.calcFormEag," in SCRIPT
    assert "mmol/mol" in SCRIPT
    assert "高于常见控制目标 7%" in SCRIPT


def test_hba1c_lab_alias_exists():
    assert "hba1c" in CORE
    assert "糖化血红蛋白" in CORE
