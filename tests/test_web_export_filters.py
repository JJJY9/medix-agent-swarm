"""前端列表导出跟随搜索关键字的回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_list_export_buttons_forward_search_query():
    for function in (
        "exportAppointmentsCsv",
        "exportAllergiesCsv",
        "exportCareItemsCsv",
        "exportVisitsCsv",
        "exportConditionsCsv",
    ):
        start = SCRIPT.index(f"async function {function}()")
        block = SCRIPT[start:start + 500]
        assert "params.set('q', query);" in block, function
