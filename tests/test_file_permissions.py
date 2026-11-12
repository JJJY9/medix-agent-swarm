"""含健康数据的目录与文件在 POSIX 上仅当前用户可访问。"""
import os
import stat

import pytest

from core.fs_permissions import restrict_permissions
from memory.session_store import SessionStore
from memory.session_summary import SessionSummaryManager

pytestmark = pytest.mark.skipif(
    os.name != "posix", reason="Windows 上 chmod 不生效"
)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_restrict_permissions_sets_file_and_dir_modes(tmp_path):
    directory = tmp_path / "data"
    directory.mkdir()
    restrict_permissions(directory, 0o700)
    assert _mode(directory) == 0o700

    file_path = directory / "sessions.db"
    file_path.write_text("x", encoding="utf-8")
    restrict_permissions(file_path, 0o600)
    assert _mode(file_path) == 0o600


def test_session_database_is_owner_only(tmp_path):
    db_path = tmp_path / "sessions.db"
    store = SessionStore(str(db_path))
    try:
        assert _mode(db_path) == 0o600
        for suffix in ("-wal", "-shm"):
            path = tmp_path / ("sessions.db" + suffix)
            if path.exists():
                assert _mode(path) == 0o600
    finally:
        store.close()


def test_summary_directory_is_owner_only(tmp_path):
    manager = SessionSummaryManager(base_dir=str(tmp_path / "summaries"))
    path = manager._get_summary_path("s1")
    assert _mode(path.parent) == 0o700
    assert _mode(tmp_path / "summaries") == 0o700


def test_knowledge_upload_directory_and_file_are_owner_only(tmp_path):
    from knowledge.service import KnowledgeService

    service = KnowledgeService(
        docs_dir=tmp_path / "docs",
        upload_dir=tmp_path / "uploads",
    )
    result = service.save_upload(
        "note.md", "# 标题\n\n内容".encode("utf-8")
    )
    path = tmp_path / "uploads" / result["filename"]

    assert _mode(tmp_path / "uploads") == 0o700
    assert _mode(path) == 0o600
