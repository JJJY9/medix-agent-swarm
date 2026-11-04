"""知识库文档管理与索引重建。

内置文档放在 knowledge/data/documents（随仓库分发）；
用户上传的文档放在 knowledge/data/uploads（运行时生成，已加入 .gitignore）。
重建时两份目录一起导入，并替换旧 collection，避免重复导入导致向量块翻倍。
"""
from __future__ import annotations

import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from .documents import (
    SUPPORTED_SUFFIXES,
    infer_metadata,
    load_document,
    load_documents_from_directory,
)
from .sources import build_sources

KNOWLEDGE_DIR = Path(__file__).resolve().parent
DEFAULT_DOCS_DIR = KNOWLEDGE_DIR / "data" / "documents"
DEFAULT_UPLOAD_DIR = KNOWLEDGE_DIR / "data" / "uploads"
MAX_UPLOAD_BYTES = 5 * 1024 * 1024


class KnowledgeService:
    """知识库文件与索引的统一管理入口。"""

    def __init__(
        self,
        docs_dir: Optional[Path] = None,
        upload_dir: Optional[Path] = None,
        kb: Any = None
    ):
        self.docs_dir = Path(docs_dir or DEFAULT_DOCS_DIR)
        self.upload_dir = Path(upload_dir or DEFAULT_UPLOAD_DIR)
        self.docs_dir.mkdir(parents=True, exist_ok=True)
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self._kb = kb
        self._lock = threading.Lock()

    @property
    def kb(self):
        if self._kb is None:
            from .milvus_kb import MedicalKnowledgeBase
            self._kb = MedicalKnowledgeBase()
        return self._kb

    def list_documents(self) -> Dict[str, Any]:
        with self._lock:
            documents = []
            for path in sorted(self.docs_dir.iterdir()):
                documents.append(self._describe(path, builtin=True))
            for path in sorted(self.upload_dir.iterdir()):
                documents.append(self._describe(path, builtin=False))

            try:
                chunk_count = self.kb.count_documents()
            except Exception as e:
                logger.warning(f"Failed to count knowledge chunks: {e}")
                chunk_count = None

        documents = [doc for doc in documents if doc]
        return {
            "documents": documents,
            "document_count": len(documents),
            "chunk_count": chunk_count,
            "docs_dir": str(self.docs_dir),
            "upload_dir": str(self.upload_dir),
        }

    def _describe(self, path: Path, builtin: bool) -> Optional[Dict[str, Any]]:
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return None
        metadata = infer_metadata(path.name, "")
        return {
            "filename": path.name,
            "size": path.stat().st_size,
            "suffix": path.suffix.lower(),
            "type": metadata["type"],
            "source": metadata["source"],
            "builtin": builtin,
        }

    def save_upload(self, filename: Optional[str], content: bytes) -> Dict[str, Any]:
        if not content:
            raise ValueError("文件内容为空。")
        if len(content) > MAX_UPLOAD_BYTES:
            raise ValueError("文件超过 5 MB 限制。")

        safe_name = self._safe_filename(filename)
        if Path(safe_name).suffix.lower() not in SUPPORTED_SUFFIXES:
            raise ValueError("仅支持 .txt / .md / .markdown / .pdf 文件。")

        with self._lock:
            target = self._unique_path(safe_name)
            target.write_bytes(content)
            try:
                document = load_document(target, id_prefix="upload_")
                if not document:
                    raise ValueError("文件内容为空或无法解析。")
            except Exception:
                target.unlink(missing_ok=True)
                raise

        logger.info(f"Saved knowledge upload: {target.name} ({len(content)} bytes)")
        return {"filename": target.name, "size": len(content)}

    def delete_document(self, filename: str) -> bool:
        safe_name = self._safe_filename(filename)
        path = self.upload_dir / safe_name
        with self._lock:
            if not path.exists():
                if (self.docs_dir / safe_name).exists():
                    raise ValueError("内置文档请通过仓库更新，不能在此删除。")
                return False
            path.unlink()
        logger.info(f"Deleted knowledge upload: {safe_name}")
        return True

    def preview_document(
        self, filename: str, max_chars: Optional[int] = 4000
    ) -> Optional[Dict[str, Any]]:
        """读取文档内容，供知识库抽屉预览；max_chars 为 None 时返回全文。"""
        safe_name = self._safe_filename(filename)
        candidates = (self.upload_dir / safe_name, self.docs_dir / safe_name)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return None

        builtin = path.parent == self.docs_dir
        try:
            document = load_document(path)
        except Exception as e:
            raise ValueError(f"读取文档失败：{e}") from e
        if not document:
            raise ValueError("文档内容为空或无法解析。")

        content = document["content"]
        if max_chars is None:
            preview = content
            truncated = False
        else:
            max_chars = max(1, int(max_chars))
            preview = content[:max_chars]
            truncated = len(content) > max_chars

        return {
            "filename": path.name,
            "builtin": builtin,
            "size": path.stat().st_size,
            "suffix": path.suffix.lower(),
            "type": document["metadata"].get("type", ""),
            "source": document["metadata"].get("source", ""),
            "content": preview,
            "truncated": truncated,
            "total_length": len(content),
        }

    def update_document(self, filename: str, content: bytes) -> Dict[str, Any]:
        """在线更新上传的 TXT / Markdown 文档；内置文档与 PDF 不允许编辑。"""
        safe_name = self._safe_filename(filename)
        path = self.upload_dir / safe_name

        with self._lock:
            if not path.is_file():
                if (self.docs_dir / safe_name).exists():
                    raise ValueError("内置文档请通过仓库更新，不能在线编辑。")
                raise ValueError("文档不存在。")

            suffix = path.suffix.lower()
            if suffix not in {".txt", ".md", ".markdown"}:
                raise ValueError("仅支持在线编辑 TXT / Markdown 文档。")
            if not content:
                raise ValueError("文件内容为空。")
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError("文件超过 5 MB 限制。")

            original = path.read_bytes()
            path.write_bytes(content)
            try:
                document = load_document(path)
                if not document:
                    raise ValueError("文档内容为空或无法解析。")
            except Exception:
                path.write_bytes(original)
                raise

        logger.info(f"Updated knowledge upload: {path.name} ({len(content)} bytes)")
        return {"filename": path.name, "size": len(content)}

    def search(
        self,
        query: str,
        top_k: int = 5,
        filter_type: Optional[str] = None,
    ) -> Dict[str, Any]:
        """执行一次知识库检索，返回来源卡片，可按文档类型过滤。"""
        query = (query or "").strip()
        if not query:
            raise ValueError("请输入检索关键词。")

        top_k = max(1, min(int(top_k or 5), 20))
        with self._lock:
            try:
                results = self.kb.search(
                    query, top_k=top_k, filter_type=filter_type or None
                )
            except Exception as e:
                raise ValueError(f"检索失败：{e}") from e

        return {
            "query": query,
            "top_k": top_k,
            "filter_type": filter_type or "",
            "results": build_sources(results, max_items=top_k),
        }

    def rebuild(self, replace_existing: bool = True) -> Dict[str, Any]:
        builtin_documents = load_documents_from_directory(self.docs_dir)
        uploaded_documents = load_documents_from_directory(
            self.upload_dir, id_prefix="upload_"
        )
        documents = builtin_documents + uploaded_documents
        if not documents:
            raise ValueError("知识库目录中没有可导入的文档。")

        with self._lock:
            chunks = self.kb.add_documents(
                documents, replace_existing=replace_existing
            )
            try:
                chunk_count = self.kb.count_documents()
            except Exception:
                chunk_count = chunks

        logger.info(
            f"Knowledge rebuilt: {len(documents)} documents, {chunks} chunks"
        )
        return {
            "documents": len(documents),
            "chunks": chunks,
            "chunk_count": chunk_count,
            "builtin_documents": len(builtin_documents),
            "uploaded_documents": len(uploaded_documents),
        }

    @staticmethod
    def _safe_filename(filename: Optional[str]) -> str:
        name = Path(str(filename or "")).name
        name = re.sub(r"[^\w一-鿿.\-() ]+", "_", name, flags=re.UNICODE)
        name = name.strip(" .")
        if not name:
            raise ValueError("文件名无效。")
        return name[:120]

    def _unique_path(self, name: str) -> Path:
        path = self.upload_dir / name
        if not path.exists():
            return path
        stem, suffix = path.stem, path.suffix
        for index in range(1, 1000):
            candidate = self.upload_dir / f"{stem}-{index}{suffix}"
            if not candidate.exists():
                return candidate
        raise ValueError("同名文件过多，请先清理上传目录。")


_service: Optional[KnowledgeService] = None


def get_knowledge_service() -> KnowledgeService:
    global _service
    if _service is None:
        _service = KnowledgeService()
    return _service
