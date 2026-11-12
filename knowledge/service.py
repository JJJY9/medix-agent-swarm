"""知识库文档管理与索引重建。

内置文档放在 knowledge/data/documents（随仓库分发）；
用户上传的文档放在 knowledge/data/uploads（运行时生成，已加入 .gitignore）。
重建时两份目录一起导入，并替换旧 collection，避免重复导入导致向量块翻倍。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from core.fs_permissions import restrict_permissions

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
        restrict_permissions(self.upload_dir, 0o700)
        self._kb = kb
        self._lock = threading.RLock()

    @property
    def kb(self):
        if self._kb is None:
            from .milvus_kb import MedicalKnowledgeBase
            self._kb = MedicalKnowledgeBase()
        return self._kb

    def _manifest_path(self) -> Path:
        return self.upload_dir.parent / "index_manifest.json"

    def _document_fingerprint(self) -> str:
        """文档目录（文件名/大小/mtime）的指纹，用于检测索引是否过期。"""
        digest = hashlib.sha256()
        for directory, tag in (
            (self.docs_dir, "docs"),
            (self.upload_dir, "uploads"),
        ):
            try:
                entries = sorted(directory.iterdir(), key=lambda p: p.name)
            except OSError:
                entries = []
            for path in entries:
                if (
                    not path.is_file()
                    or path.suffix.lower() not in SUPPORTED_SUFFIXES
                ):
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                digest.update(
                    f"{tag}:{path.name}:{stat.st_size}:"
                    f"{stat.st_mtime_ns}\n".encode("utf-8")
                )
        return digest.hexdigest()

    def _read_manifest(self) -> str:
        try:
            payload = json.loads(
                self._manifest_path().read_text(encoding="utf-8")
            )
            return str(payload.get("fingerprint") or "")
        except (OSError, ValueError, TypeError, AttributeError):
            # 合法 JSON 但不是对象（如 []、1）时没有 .get
            return ""

    def _write_manifest(self, fingerprint: str) -> None:
        try:
            self._manifest_path().write_text(
                json.dumps(
                    {"fingerprint": fingerprint},
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning(f"写入知识库索引清单失败: {e}")

    def index_is_stale(self) -> bool:
        """当前文档与上次成功重建时的指纹是否不一致。"""
        with self._lock:
            return self._read_manifest() != self._document_fingerprint()

    def ensure_fresh(self) -> None:
        """检索前确保索引与文档一致。

        技能与深度研究直接调用 MedicalKnowledgeBase.search，不经过
        HTTP 检索端点；在它们创建知识库实例前调用本方法，避免删除/
        更新文档后仍检索到旧索引。
        """
        with self._lock:
            self._ensure_index_fresh_locked()

    def _document_count(self) -> int:
        """文档目录中可索引文件的数量（用于判断空库是否需要重建）。"""
        count = 0
        for directory in (self.docs_dir, self.upload_dir):
            try:
                entries = directory.iterdir()
            except OSError:
                continue
            for path in entries:
                try:
                    if (
                        path.is_file()
                        and path.suffix.lower() in SUPPORTED_SUFFIXES
                    ):
                        count += 1
                except OSError:
                    continue
        return count

    def _ensure_index_fresh_locked(self) -> None:
        fingerprint = self._document_fingerprint()
        if self._read_manifest() == fingerprint:
            # 清单匹配但向量库可能被删除或换了工作目录：
            # 有文档却没有索引时必须重建，不能静默返回空检索
            try:
                indexed = int(self.kb.count_documents() or 0)
            except Exception as e:
                logger.warning(f"读取知识库索引数量失败: {e}")
                indexed = 0
            if indexed > 0 or self._document_count() == 0:
                return
            logger.warning("索引清单匹配但向量库为空，触发重建")
        else:
            logger.info("Knowledge index is stale, rebuilding before search")
        try:
            self._rebuild_locked(True)
        except Exception as e:
            # 宁可报错也不返回与磁盘文档不一致的旧索引
            raise ValueError(
                f"知识库索引与文档不一致，自动重建失败：{e}"
            ) from e

    def list_documents(self) -> Dict[str, Any]:
        with self._lock:
            documents = []
            builtin_names = {
                path.name for path in self.docs_dir.iterdir() if path.is_file()
            }
            for path in sorted(self.docs_dir.iterdir()):
                documents.append(self._describe(path, builtin=True))
            for path in sorted(self.upload_dir.iterdir()):
                if path.name in builtin_names:
                    # 历史遗留的同名上传会遮蔽内置文档，列表与预览只保留内置版本
                    logger.warning(
                        "Ignoring upload shadowed by builtin document: "
                        f"name_length={len(path.name)}"
                    )
                    continue
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
            # 只返回目录名，避免把服务器绝对路径暴露给前端
            "docs_dir": self.docs_dir.name,
            "upload_dir": self.upload_dir.name,
        }

    def _describe(self, path: Path, builtin: bool) -> Optional[Dict[str, Any]]:
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            return None
        metadata = infer_metadata(path.name, "", is_upload=not builtin)
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
            try:
                target.write_bytes(content)
                restrict_permissions(target, 0o600)
            except Exception:
                # 磁盘满/占用导致半写时清理残留，避免下次重建索引残缺文件
                target.unlink(missing_ok=True)
                raise
            try:
                document = load_document(target, id_prefix="upload_")
                if not document:
                    raise ValueError("文件内容为空或无法解析。")
            except Exception:
                target.unlink(missing_ok=True)
                raise

        logger.info(
            "Saved knowledge upload: "
            f"name_length={len(target.name)} ({len(content)} bytes)"
        )
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
        logger.info(
            f"Deleted knowledge upload: name_length={len(safe_name)}"
        )
        return True

    def preview_document(
        self, filename: str, max_chars: Optional[int] = 4000
    ) -> Optional[Dict[str, Any]]:
        """读取文档内容，供知识库抽屉预览；max_chars 为 None 时返回全文。"""
        safe_name = self._safe_filename(filename)
        # 内置文档优先；同名上传在历史数据中可能遮蔽内置文档
        candidates = (self.docs_dir / safe_name, self.upload_dir / safe_name)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            return None

        builtin = path.parent == self.docs_dir
        try:
            document = load_document(path, is_upload=not builtin)
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

            # 先写同后缀临时文件再原子替换：写入中断不会截断原文件
            temp_path = path.with_name(f".{path.stem}.tmp{path.suffix}")
            try:
                temp_path.write_bytes(content)
                restrict_permissions(temp_path, 0o600)
                document = load_document(temp_path)
                if not document:
                    raise ValueError("文档内容为空或无法解析。")
                os.replace(temp_path, path)
            except Exception:
                temp_path.unlink(missing_ok=True)
                raise

        logger.info(
            "Updated knowledge upload: "
            f"name_length={len(path.name)} ({len(content)} bytes)"
        )
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
            self._ensure_index_fresh_locked()
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
        """重建索引；整个读取+写入过程持锁，避免读到半写入的上传文件。"""
        with self._lock:
            return self._rebuild_locked(replace_existing)

    def _rebuild_locked(self, replace_existing: bool = True) -> Dict[str, Any]:
        fingerprint = self._document_fingerprint()
        builtin_documents = load_documents_from_directory(self.docs_dir)
        uploaded_documents = load_documents_from_directory(
            self.upload_dir, id_prefix="upload_", is_upload=True
        )
        # 历史遗留的同名上传虽然不在列表中展示，也不能进入索引，
        # 否则会与内置文档一起返回陈旧内容
        builtin_names = {
            path.name for path in self.docs_dir.iterdir() if path.is_file()
        }
        shadowed_count = 0
        filtered_uploads = []
        for document in uploaded_documents:
            filename = str(
                (document.get("metadata") or {}).get("filename") or ""
            )
            if filename in builtin_names:
                shadowed_count += 1
                logger.warning(
                    "Skipping upload shadowed by builtin document: "
                    f"name_length={len(filename)}"
                )
                continue
            filtered_uploads.append(document)
        uploaded_documents = filtered_uploads
        documents = []
        seen_contents = set()
        duplicate_count = 0
        for document in builtin_documents + uploaded_documents:
            normalized = (
                str(document.get("content") or "")
                .replace("\r\n", "\n")
                .replace("\r", "\n")
                .strip()
            )
            content_key = hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest()
            if content_key in seen_contents:
                duplicate_count += 1
                logger.info(
                    "Skipping duplicate document content: "
                    f"hash_prefix={content_key[:8]}"
                )
                continue
            seen_contents.add(content_key)
            documents.append(document)
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
            self._write_manifest(fingerprint)

        logger.info(
            f"Knowledge rebuilt: {len(documents)} documents, {chunks} chunks"
        )
        return {
            "documents": len(documents),
            "chunks": chunks,
            "chunk_count": chunk_count,
            "builtin_documents": len(builtin_documents),
            "uploaded_documents": len(uploaded_documents),
            "duplicate_documents": duplicate_count,
            "shadowed_uploads": shadowed_count,
        }

    @staticmethod
    def _safe_filename(filename: Optional[str]) -> str:
        name = Path(str(filename or "")).name
        name = re.sub(r"[^\w一-鿿.\-() ]+", "_", name, flags=re.UNICODE)
        name = name.strip(" .")
        if not name:
            raise ValueError("文件名无效。")
        if len(name) > 120:
            # 截断时保留扩展名，避免长文件名被误判为不支持的类型
            suffix = Path(name).suffix
            stem_budget = max(1, 120 - len(suffix))
            name = f"{name[:stem_budget]}{suffix}"
        return name

    def _unique_path(self, name: str) -> Path:
        suffix = Path(name).suffix
        stem = Path(name).stem

        def available(path: Path) -> bool:
            # 不允许上传文件与内置文档同名，否则预览/来源会互相遮蔽
            return not path.exists() and not (self.docs_dir / path.name).exists()

        def build(candidate_stem: str) -> str:
            # 预留扩展名与 "-N" 后缀，保证落盘名 ≤120 且再次清洗不变
            budget = max(1, 120 - len(suffix))
            return f"{candidate_stem[:budget]}{suffix}"

        path = self.upload_dir / build(stem)
        if available(path):
            return path
        if (self.docs_dir / path.name).exists():
            raise ValueError("与内置文档同名，请重命名后再上传。")
        for index in range(1, 1000):
            marker = f"-{index}"
            budget = max(1, 120 - len(suffix) - len(marker))
            candidate = self.upload_dir / (
                f"{stem[:budget]}{marker}{suffix}"
            )
            if available(candidate):
                return candidate
        raise ValueError("同名文件过多，请先清理上传目录。")


_service: Optional[KnowledgeService] = None


def get_knowledge_service() -> KnowledgeService:
    global _service
    if _service is None:
        _service = KnowledgeService()
    return _service


_freshness_lock = threading.Lock()


def ensure_knowledge_index_fresh(kb: Any = None) -> None:
    """技能入口在检索前调用：索引与磁盘文档不一致时自动重建。

    传入技能自己的知识库实例可复用它完成重建，避免同一进程再加载
    一份 embedding 模型；重建仍用模块级锁串行，防止并发技能互相覆盖。
    """
    if kb is None:
        get_knowledge_service().ensure_fresh()
        return
    with _freshness_lock:
        KnowledgeService(kb=kb).ensure_fresh()
