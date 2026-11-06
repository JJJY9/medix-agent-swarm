"""第六轮回归审计：今日修复引入的误伤与顺序问题。"""
import re

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from constraints import ConstraintValidator
from core.medication_safety import check_medication_safety
from core.triage import detect_red_flags
from knowledge.service import KnowledgeService
from memory.session_store import SessionStore
from validation.auto_fixer import AutoFixer


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    return TestClient(api_server.app)


def test_negated_stop_keeps_anticoagulation_active():
    for medications in ("未停用华法林", "华法林未停药", "不能停用华法林"):
        result = detect_red_flags(
            "摔倒撞到头了", {"medications": medications}
        )
        assert "抗凝治疗期间头部外伤" in result["matches"], medications


def test_negated_stop_keeps_medication_interaction_alert():
    result = check_medication_safety("不能停用华法林，能吃布洛芬吗？")

    titles = [alert["title"] for alert in result["alerts"]]
    assert any("华法林" in title and "非甾体" in title for title in titles)


def test_allergy_with_unknown_test_result_is_still_an_allergy():
    result = check_medication_safety(
        "能吃复方新诺明吗", {"allergies": "磺胺过敏（未查）"}
    )

    titles = [alert["title"] for alert in result["alerts"]]
    assert any("过敏" in title for title in titles)


def test_allergy_result_only_clause_is_still_ignored():
    result = check_medication_safety(
        "能吃阿莫西林吗", {"allergies": "过敏原：阿莫西林阴性"}
    )

    titles = [alert["title"] for alert in result["alerts"]]
    assert not any("过敏史" in title for title in titles)


def test_head_trauma_covers_back_of_head_and_scalp_injury():
    for text in ("滑倒，后脑勺着地", "摔倒磕破了头", "脑袋撞到桌角"):
        result = detect_red_flags(text, {"medications": "华法林"})
        assert "抗凝治疗期间头部外伤" in result["matches"], text


def test_template_download_preserves_bom(client):
    app_js = client.get("/static/app.js").text

    assert "await res.arrayBuffer()" in app_js
    assert "new Blob([buffer], { type: 'text/csv;charset=utf-8' })" in app_js


def test_retry_reconciles_even_with_baseline_zero(client):
    app_js = client.get("/static/app.js").text

    assert re.search(
        r"if \(sessionId\) \{\s*"
        r"const restored = await restoreCurrentSession\(\s*"
        r"retry\.question, Number\(retry\.baselineMessageId\) \|\| 0",
        app_js,
    )


def test_truncation_runs_before_disclaimer():
    validator = ConstraintValidator()
    output = "建议内容" + "很长的说明" * 600
    result = validator.validate_output("consultation_agent", output)

    fixed = AutoFixer().fix_output(output, result["auto_fixable"])

    assert "免责声明" in fixed
    assert len(fixed) <= 2000


def test_upload_display_metadata_matches_index_metadata(tmp_path):
    docs = tmp_path / "documents"
    uploads = tmp_path / "uploads"
    docs.mkdir()
    uploads.mkdir()
    (uploads / "notes_guideline_cold.txt").write_text(
        "用户自己的感冒笔记", encoding="utf-8"
    )

    class FakeKB:
        def count_documents(self):
            return 0

    service = KnowledgeService(docs_dir=docs, upload_dir=uploads, kb=FakeKB())
    listing = service.list_documents()
    item = next(
        doc for doc in listing["documents"]
        if doc["filename"] == "notes_guideline_cold.txt"
    )
    preview = service.preview_document("notes_guideline_cold.txt")

    assert item["type"] == "general"
    assert item["source"] == "自定义文档"
    assert preview["type"] == "general"
    assert preview["source"] == "自定义文档"
