"""收藏会话时原地更新列表，不重置分页与焦点。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_toggle_favorite_updates_item_in_place():
    start = SCRIPT.index("async function toggleFavorite(item)")
    end = SCRIPT.index("async function loadHistoryTags()", start)
    body = SCRIPT[start:end]

    assert "item.favorite = favorite;" in body
    assert "historyItemMap.set(item.id, item);" in body
    assert "star.classList.toggle('active', favorite);" in body
    assert "star.focus();" in body
    # 仅「只看收藏」取消收藏时才重载，且保留滚动位置
    assert body.count("loadHistory(") == 1
    assert "scrollTop" in body
    # 不再无条件把焦点移回搜索框
    assert "els.historySearch.focus()" not in body.split(
        "if (favoritesOnly && !favorite)", 1
    )[0]
