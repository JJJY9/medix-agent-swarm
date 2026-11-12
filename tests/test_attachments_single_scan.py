"""附件读取改为一次扫描（时间线/健康搜索不再逐页重扫）。"""
import asyncio
from datetime import datetime, timedelta

from memory.session_store import SessionStore


def _today(offset=0):
    return (datetime.now() + timedelta(days=offset)).strftime("%Y-%m-%d")


def test_list_all_attachments_filters_profile_and_dates(tmp_path):
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.add_attachment(
            "妈妈", "visit", 1, "报告.pdf", "a.pdf", "application/pdf", 100,
        )
        store.add_attachment(
            "爸爸", "lab", 2, "化验.png", "b.png", "image/png", 200,
        )
        rows = store.list_all_attachments(profile="妈妈")
        assert [row["id"] for row in rows] == [first["id"]]

        assert store.list_all_attachments(start=_today(), end=_today())
        assert store.list_all_attachments(
            start=_today(1), end=_today(2)
        ) == []
    finally:
        store.close()


def test_list_attachments_paged_uses_single_scan():
    import api_server

    calls = []

    class FakeStore:
        def list_all_attachments(self, profile=None, start="", end=""):
            calls.append((profile, start, end))
            return [{"id": 1}, {"id": 2}]

    items = asyncio.run(api_server._list_attachments_paged(
        FakeStore(), "妈妈", "2026-01-01", "2026-01-31",
    ))

    assert [item["id"] for item in items] == [1, 2]
    assert calls == [("妈妈", "2026-01-01", "2026-01-31")]
