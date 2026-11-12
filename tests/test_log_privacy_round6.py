"""日志隐私回归：技能查询与访问日志不再包含患者文本。"""
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
from core.security import _AccessLogQueryFilter

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_access_log_query_string_is_redacted():
    record = logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        (
            "127.0.0.1:1234",
            "GET",
            "/api/symptoms?q=%E6%8A%91%E9%83%81 HTTP/1.1",
            "1.1",
            200,
        ),
        None,
    )

    assert _AccessLogQueryFilter().filter(record) is True
    message = record.getMessage()
    assert "抑郁" not in message
    assert "%E6%8A%91" not in message
    assert "/api/symptoms?<redacted>" in message


def test_skill_logs_do_not_include_patient_queries(client):
    assert client.get("/static/app.js").status_code == 200

    skill_texts = []
    for relative in (
        ".claude/skills/analyze-symptoms/script/symptoms.py",
        ".claude/skills/assess-risk/script/risk.py",
        ".claude/skills/search-knowledge/script/search.py",
        ".claude/skills/clinical-guideline/script/guideline.py",
        ".claude/skills/disease-code/script/code.py",
        ".claude/skills/recommend-lifestyle/script/lifestyle.py",
        ".claude/skills/deep-research/script/research.py",
    ):
        skill_texts.append(
            (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        )

    joined = "\n".join(skill_texts)
    assert "Analyzing symptoms: {symptoms}" not in joined
    assert "Assessing risk: symptoms={symptoms}" not in joined
    assert "query={query}" not in joined
    assert "for disease: {disease_name}" not in joined
    assert "Recommending lifestyle for: {diagnosis}" not in joined
    assert "query_length=" in joined
    assert "diagnosis_length=" in joined


def _access_record(path):
    return logging.LogRecord(
        "uvicorn.access",
        logging.INFO,
        "",
        0,
        '%s - "%s %s HTTP/%s" %d',
        ("127.0.0.1:1234", "GET", path, "1.1", 200),
        None,
    )


def test_access_log_path_segments_are_redacted():
    for path, expected in (
        (
            "/api/profiles/%E5%A6%88%E5%A6%88",
            "/api/profiles/<redacted>",
        ),
        (
            "/api/knowledge/documents/%E4%BD%93%E6%A3%80.pdf",
            "/api/knowledge/documents/<redacted>",
        ),
        (
            "/api/profiles/%E5%A6%88%E5%A6%88?x=1",
            "/api/profiles/<redacted>?<redacted>",
        ),
    ):
        record = _access_record(path)
        assert _AccessLogQueryFilter().filter(record) is True
        message = record.getMessage()
        assert "%E5%A6%88%E5%A6%88" not in message
        assert "%E4%BD%93%E6%A3%80" not in message
        assert expected in message


def test_knowledge_service_logs_do_not_include_filenames():
    text = (PROJECT_ROOT / "knowledge/service.py").read_text(
        encoding="utf-8"
    )

    assert "Saved knowledge upload: {target.name}" not in text
    assert "Deleted knowledge upload: {safe_name}" not in text
    assert "Updated knowledge upload: {path.name}" not in text
    assert "name_length=" in text
