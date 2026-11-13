"""第二十一轮审查：前端用量预算展示回归测试。"""
from pathlib import Path

APP = (
    Path(__file__).resolve().parents[1] / "web" / "app.js"
).read_text(encoding="utf-8")


def test_budget_exceeded_uses_backend_flag_not_rounded_percent():
    start = APP.index("function renderUsageBudget(data)")
    body = APP[start:start + 1600]

    assert "data.exceeded === true || total >= budget" in body
    assert "!exceeded && percent >= 100 ? 99.9 : percent" in body
    assert "els.usageBudget.classList.toggle('critical', exceeded)" in body
