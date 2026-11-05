"""知识库文档加载：从 knowledge/data/documents 读取 TXT / Markdown / PDF。"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

SUPPORTED_SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}

#: 文件名第二段 → (类型, 来源)
_CATEGORY_TYPES = {
    "lifestyle": ("lifestyle", "生活方式建议数据库"),
    "symptoms": ("symptoms", "症状与急症提示库"),
    "icd10": ("disease_classification", "ICD-10疾病编码数据库"),
    "guideline": ("clinical_guideline", "临床指南数据库"),
}

#: 兼容旧命名：按数字区间推断类型
_DOC_TYPES = (
    (10, "lifestyle", "生活方式建议数据库"),
    (20, "disease_classification", "ICD-10疾病编码数据库"),
    (30, "clinical_guideline", "临床指南数据库"),
)


def read_text_file(path: Path) -> str:
    """读取文本文件，兼容 UTF-8（含 BOM）、UTF-16 与 GB18030。"""
    data = path.read_bytes()

    # 先按 BOM 判断编码，避免 UTF-16 被替换解码成乱码
    if data.startswith(b"\xef\xbb\xbf"):
        return data.decode("utf-8-sig")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")

    for encoding in ("utf-8", "gb18030"):
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        # 含 NUL 基本是二进制文件，继续尝试其他编码
        if "\x00" in text:
            continue
        return text

    # 替换解码兜底：含 NUL 或替换字符占比过高时视为无法识别，
    # 不能把乱码当作正常文档索引
    text = data.decode("utf-8", errors="replace")
    if "\x00" in text or text.count("�") > max(1, len(text) // 20):
        raise ValueError("文件编码无法识别，请另存为 UTF-8 后重试。")
    logger.warning(f"Unknown encoding, decoded with replacement: {path.name}")
    return text.lstrip("﻿")


def extract_pdf_text(path: Path) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("解析 PDF 需要安装 pypdf：pip install pypdf") from e

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages)


def infer_metadata(filename: str, content: str) -> Dict[str, str]:
    """根据文件名前缀和内容推断类型、来源与疾病名。"""
    stem = Path(filename).stem
    parts = stem.split("_", 2)
    file_num = parts[0]
    category = parts[1].lower() if len(parts) > 1 else ""

    doc_type = "general"
    source = "医学知识库"
    if category in _CATEGORY_TYPES:
        doc_type, source = _CATEGORY_TYPES[category]
    elif file_num.isdigit():
        number = int(file_num)
        for limit, candidate_type, candidate_source in _DOC_TYPES:
            if number < limit:
                doc_type = candidate_type
                source = candidate_source
                break

    # 只有「数字_已知类别_疾病」这种约定命名才把第三段当疾病名
    disease = ""
    if (
        len(parts) > 2
        and file_num.isdigit()
        and category in _CATEGORY_TYPES
    ):
        disease = parts[2]
    if not disease:
        first_line = next(
            (line.strip() for line in content.splitlines() if line.strip()), ""
        )
        disease = first_line[:40] or stem

    return {
        "type": doc_type,
        "source": source,
        "disease": disease,
        "filename": Path(filename).name,
    }


def load_document(path: Path, id_prefix: str = "") -> Optional[Dict[str, Any]]:
    """加载单个文档；无法解析或内容为空时返回 None。"""
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        return None

    if suffix == ".pdf":
        content = extract_pdf_text(path)
    else:
        content = read_text_file(path)

    content = content.strip()
    if not content:
        return None

    metadata = infer_metadata(path.name, content)
    return {
        "id": f"{id_prefix}{metadata['type']}_{path.stem}",
        "content": content,
        "metadata": metadata,
    }


def load_documents_from_directory(
    doc_dir: Path,
    id_prefix: str = ""
) -> List[Dict[str, Any]]:
    """加载目录下所有支持的文档，解析失败的文件跳过并记录日志。"""
    doc_dir = Path(doc_dir)
    if not doc_dir.exists():
        logger.warning(f"Documents directory not found: {doc_dir}")
        return []

    documents = []
    for path in sorted(doc_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
            continue
        try:
            document = load_document(path, id_prefix=id_prefix)
        except Exception as e:
            logger.error(f"Failed to load {path.name}: {e}")
            continue
        if document:
            documents.append(document)

    logger.info(f"Loaded {len(documents)} documents from {doc_dir}")
    return documents
