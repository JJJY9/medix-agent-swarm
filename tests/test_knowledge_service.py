"""知识库文档加载与管理服务测试（使用假向量库，不加载模型）。"""
from pathlib import Path

import pytest

from knowledge.documents import infer_metadata, load_documents_from_directory
from knowledge.service import KnowledgeService, MAX_UPLOAD_BYTES


class FakeKB:
    def __init__(self):
        self.imported = []
        self.replace_flags = []
        self.search_results = []
        self.search_calls = []

    def add_documents(self, documents, replace_existing=False):
        self.imported.append(list(documents))
        self.replace_flags.append(replace_existing)
        return len(documents) * 2

    def count_documents(self):
        return len(self.imported[-1]) * 2 if self.imported else 0

    def search(self, query, top_k=5, filter_type=None):
        self.search_calls.append((query, top_k, filter_type))
        return self.search_results[:top_k]


@pytest.fixture
def service(tmp_path):
    docs_dir = tmp_path / "documents"
    upload_dir = tmp_path / "uploads"
    docs_dir.mkdir()
    upload_dir.mkdir()
    (docs_dir / "20_guideline_hypertension.txt").write_text(
        "高血压诊疗指南\n推荐低盐饮食。", encoding="utf-8"
    )
    kb = FakeKB()
    return KnowledgeService(docs_dir=docs_dir, upload_dir=upload_dir, kb=kb), kb


def test_infer_metadata_from_filename_prefix():
    metadata = infer_metadata("01_lifestyle_hypertension.txt", "内容")

    assert metadata["type"] == "lifestyle"
    assert metadata["source"] == "生活方式建议数据库"
    assert metadata["disease"] == "hypertension"


def test_load_documents_from_directory(tmp_path):
    (tmp_path / "10_icd10_diabetes.txt").write_text("糖尿病编码", encoding="utf-8")
    (tmp_path / "ignore.json").write_text("{}", encoding="utf-8")

    documents = load_documents_from_directory(tmp_path)

    assert len(documents) == 1
    assert documents[0]["metadata"]["type"] == "disease_classification"


def test_save_list_and_delete_upload(service):
    svc, _ = service

    saved = svc.save_upload("我的指南.md", "自定义内容".encode("utf-8"))
    overview = svc.list_documents()

    assert saved["filename"] == "我的指南.md"
    assert overview["document_count"] == 2
    assert any(doc["builtin"] for doc in overview["documents"])
    assert any(doc["filename"] == "我的指南.md" for doc in overview["documents"])

    assert svc.delete_document("我的指南.md") is True
    assert svc.list_documents()["document_count"] == 1


def test_search_builds_source_cards(service):
    svc, kb = service
    kb.search_results = [{
        "content": "高血压患者应低盐饮食。" * 20,
        "metadata": {
            "disease": "高血压",
            "source": "生活方式建议数据库",
            "filename": "01_lifestyle_hypertension.txt",
            "type": "lifestyle",
        },
        "score": 0.8341,
    }]

    result = svc.search("高血压 饮食", top_k=3)

    assert result["query"] == "高血压 饮食"
    assert result["top_k"] == 3
    assert result["results"][0]["title"] == "高血压"
    assert result["results"][0]["score"] == 0.8341
    assert len(result["results"][0]["snippet"]) <= 160
    assert kb.search_calls == [("高血压 饮食", 3, None)]

    with pytest.raises(ValueError):
        svc.search("   ")


def test_update_uploaded_document(service):
    svc, _ = service
    saved = svc.save_upload("可编辑.md", "旧内容".encode("utf-8"))

    result = svc.update_document(saved["filename"], "新的内容".encode("utf-8"))

    assert result["size"] == len("新的内容".encode("utf-8"))
    full = svc.preview_document(saved["filename"], max_chars=None)
    assert full["content"] == "新的内容"
    assert full["truncated"] is False

    with pytest.raises(ValueError):
        svc.update_document("20_guideline_hypertension.txt", b"x")

    (svc.upload_dir / "a.pdf").write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError):
        svc.update_document("a.pdf", b"x")


def test_preview_builtin_and_upload(service):
    svc, _ = service

    builtin = svc.preview_document("20_guideline_hypertension.txt")
    assert builtin["builtin"] is True
    assert "低盐" in builtin["content"]
    assert builtin["truncated"] is False

    saved = svc.save_upload("我的指南.md", ("自定义内容" * 2000).encode("utf-8"))
    preview = svc.preview_document(saved["filename"])

    assert preview["builtin"] is False
    assert preview["truncated"] is True
    assert len(preview["content"]) == 4000
    assert preview["total_length"] == 10000


def test_preview_missing_and_traversal(service):
    svc, _ = service

    assert svc.preview_document("missing.md") is None
    assert svc.preview_document("../../etc/passwd") is None


def test_duplicate_upload_gets_unique_name(service):
    svc, _ = service

    first = svc.save_upload("guide.md", b"first")
    second = svc.save_upload("guide.md", b"second")

    assert first["filename"] == "guide.md"
    assert second["filename"] == "guide-1.md"


def test_path_traversal_filename_is_sanitized(service, tmp_path):
    svc, _ = service

    saved = svc.save_upload("../../evil.md", b"content")

    assert saved["filename"] == "evil.md"
    assert (svc.upload_dir / "evil.md").exists()
    assert not (tmp_path.parent / "evil.md").exists()


def test_rejects_unsupported_extension(service):
    svc, _ = service

    with pytest.raises(ValueError):
        svc.save_upload("malware.exe", b"MZ")


def test_rejects_empty_and_oversized_files(service):
    svc, _ = service

    with pytest.raises(ValueError):
        svc.save_upload("empty.md", b"")

    with pytest.raises(ValueError):
        svc.save_upload("big.md", b"x" * (MAX_UPLOAD_BYTES + 1))


def test_rebuild_replaces_index_and_counts_sources(service):
    svc, kb = service
    svc.save_upload("extra.md", "上传内容".encode("utf-8"))

    result = svc.rebuild()

    assert result["documents"] == 2
    assert result["builtin_documents"] == 1
    assert result["uploaded_documents"] == 1
    assert result["chunks"] == 4
    assert kb.replace_flags == [True]


def test_builtin_documents_cannot_be_deleted(service):
    svc, _ = service

    with pytest.raises(ValueError):
        svc.delete_document("20_guideline_hypertension.txt")


def test_rebuild_skips_duplicate_document_content(service):
    svc, _kb = service
    builtin = (
        svc.docs_dir / "20_guideline_hypertension.txt"
    ).read_text(encoding="utf-8")
    svc.save_upload("copy.md", builtin.encode("utf-8"))

    result = svc.rebuild()

    assert result["documents"] == 1
    assert result["duplicate_documents"] == 1
    assert result["chunks"] == 2


def test_search_surfaces_index_errors(service):
    svc, kb = service

    def broken_search(*args, **kwargs):
        raise RuntimeError("index broken")

    kb.search = broken_search

    with pytest.raises(ValueError, match="检索失败"):
        svc.search("高血压")
