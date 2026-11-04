"""数据保留策略配置的单元测试。"""
from core.retention import MAX_AUTO_ARCHIVE_DAYS, get_auto_archive_days


def test_auto_archive_days_disabled_by_default(monkeypatch):
    monkeypatch.delenv("MEDIX_AUTO_ARCHIVE_DAYS", raising=False)
    assert get_auto_archive_days() == 0

    for raw in ("", "0", "-5", "abc", "1.5"):
        monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", raw)
        assert get_auto_archive_days() == 0


def test_auto_archive_days_parsing_and_cap(monkeypatch):
    monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", "30")
    assert get_auto_archive_days() == 30

    monkeypatch.setenv("MEDIX_AUTO_ARCHIVE_DAYS", "99999")
    assert get_auto_archive_days() == MAX_AUTO_ARCHIVE_DAYS
