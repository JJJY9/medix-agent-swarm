"""临床量表未作答校验的前端接线回归。"""
import pytest
from fastapi.testclient import TestClient

import api_server


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_generated_scales_have_placeholder(client):
    app_js = client.get("/static/app.js").text

    assert "function addScalePlaceholder(select)" in app_js
    assert "function collectScaleAnswers(container, label)" in app_js
    for label in ("PHQ-9", "GAD-7", "CAT", "ESS"):
        assert f"collectScaleAnswers(els.calc" in app_js
        assert f"'{label}'" in app_js


def test_auditc_has_placeholders_and_validation(client):
    index = client.get("/").text
    app_js = client.get("/static/app.js").text

    for field in ("calc-auditc-q1", "calc-auditc-q2", "calc-auditc-q3"):
        segment = index.split(f'id="{field}"', 1)[1][:200]
        assert 'value="" selected disabled' in segment
    assert "请回答全部题目后再计算 AUDIT-C。" in app_js
    assert "missing.focus();" in app_js


def test_scale_submit_focuses_first_missing_answer(client):
    app_js = client.get("/static/app.js").text

    start = app_js.index("function collectScaleAnswers")
    body = app_js[start:start + 600]
    assert "const missing = selects.find((select) => !select.value);" in body
    assert "window.alert(`请回答全部题目后再计算 ${label}。`);" in body
    assert "missing.focus();" in body
    assert "return null;" in body
