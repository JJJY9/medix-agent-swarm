"""化验列表渲染性能的前端回归测试。"""
from pathlib import Path

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
SCRIPT = (WEB_DIR / "app.js").read_text(encoding="utf-8")


def test_lab_sparkline_series_precomputed_once():
    # 按项目+单位分组排序一次，避免每行都全量过滤排序缓存（O(n²)）
    assert "const seriesByItem = new Map();" in SCRIPT
    assert "seriesByItem.get(" in SCRIPT
    assert "const series = (labItemsCache || [])" not in SCRIPT


def test_lab_list_caps_dom_rows_with_show_more():
    assert "let labRenderLimit = 300;" in SCRIPT
    assert "const visible = items.slice(0, labRenderLimit);" in SCRIPT
    assert "labRenderLimit += 300;" in SCRIPT
    assert "显示更多（还有" in SCRIPT
    assert "labRenderLimit = 300;" in SCRIPT
