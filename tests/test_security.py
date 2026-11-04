"""安全边界测试：会话 ID 校验、摘要路径与密钥脱敏。"""
import pytest
from pydantic import ValidationError

from api_server import ChatRequest
from core.security import redact_secrets, register_secret
from memory.session_summary import SessionSummaryManager


def test_chat_request_rejects_unsafe_session_id():
    for value in ("../evil", "a/b", "a\\b", "..", "x" * 121):
        with pytest.raises(ValidationError):
            ChatRequest(question="测试", session_id=value)

    request = ChatRequest(question="测试", session_id="20261105-120000-abcd")
    assert request.session_id == "20261105-120000-abcd"


def test_summary_path_stays_inside_base_dir(tmp_path):
    base = tmp_path / "summaries"
    manager = SessionSummaryManager(str(base))

    for session_id in (
        "../../evil",
        "C:\\Users\\Public\\medix",
        "..",
        "",
    ):
        path = manager._get_summary_path(session_id)
        assert path.resolve().is_relative_to(base.resolve())
        assert path.suffix == ".md"

    assert manager._get_summary_path("会话-1").name == "会话-1.md"


def test_registered_secret_is_redacted():
    register_secret("custom-secret-value-123456")

    redacted = redact_secrets("error custom-secret-value-123456 end")

    assert "custom-secret-value-123456" not in redacted
    assert "***" in redacted


def test_public_base_url_strips_credentials():
    from api_server import _public_base_url

    assert _public_base_url(
        "https://user:pass@api.example.com/v1"
    ) == "https://api.example.com/v1"
    assert _public_base_url(
        "https://api.example.com/v1"
    ) == "https://api.example.com/v1"
    assert _public_base_url("") == ""
