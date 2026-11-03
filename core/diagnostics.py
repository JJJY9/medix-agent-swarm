"""环境诊断：供 CLI 与 Web 系统自检共用。

只做轻量检查（Python 版本、包是否安装、配置、知识库文件），
不导入 torch / sentence-transformers，避免一次自检把模型加载起来。
"""
from __future__ import annotations

import importlib.util
import json
import os
import platform
import sys
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIN_PYTHON = (3, 10)

REQUIRED_PACKAGES = {
    "openai": "openai",
    "loguru": "loguru",
    "yaml": "pyyaml",
    "dotenv": "python-dotenv",
    "numpy": "numpy",
    "sentence_transformers": "sentence-transformers",
    "fastapi": "fastapi",
    "uvicorn": "uvicorn",
}

OPTIONAL_PACKAGES = {
    "pymilvus": "pymilvus",
    "mem0": "mem0ai",
    "redis": "redis",
}

KNOWLEDGE_SUFFIXES = {".txt", ".md", ".markdown", ".pdf"}


def package_version(dist_name: str) -> Optional[str]:
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return None


def module_available(module_name: str) -> bool:
    try:
        return importlib.util.find_spec(module_name) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def check_packages() -> List[Dict[str, Any]]:
    results = []
    for module_name, dist_name in {**REQUIRED_PACKAGES, **OPTIONAL_PACKAGES}.items():
        required = module_name in REQUIRED_PACKAGES
        available = module_available(module_name)
        results.append({
            "package": dist_name,
            "required": required,
            "ok": available,
            "version": package_version(dist_name) if available else None,
        })
    return results


def read_llm_config() -> Dict[str, Any]:
    """读取实际生效的 LLM 配置，但绝不返回 API Key 本身。"""
    try:
        from core.llm_client import LLM_CONFIG
        config = dict(LLM_CONFIG)
    except Exception as e:
        config = {
            "api_key": os.getenv("MEDIX_API_KEY", ""),
            "model_name": os.getenv("MEDIX_MODEL", ""),
            "base_url": os.getenv("MEDIX_BASE_URL", ""),
            "load_error": str(e),
        }

    config["api_key_set"] = bool((config.get("api_key") or "").strip())
    config.pop("api_key", None)
    return config


def check_knowledge(
    project_root: Path = PROJECT_ROOT,
    run_query: bool = False
) -> Dict[str, Any]:
    data_dir = Path(project_root) / "knowledge" / "data"
    docs_dir = data_dir / "documents"
    uploads_dir = data_dir / "uploads"

    def count_docs(path: Path) -> int:
        if not path.exists():
            return 0
        return sum(
            1 for item in path.iterdir()
            if item.is_file() and item.suffix.lower() in KNOWLEDGE_SUFFIXES
        )

    store_files = sorted(
        [p.name for p in data_dir.glob("milvus_lite.db*")]
        if data_dir.exists() else []
    )
    info: Dict[str, Any] = {
        "documents": count_docs(docs_dir),
        "uploads": count_docs(uploads_dir),
        "vector_store_files": store_files,
        "ready": bool(count_docs(docs_dir) + count_docs(uploads_dir)),
    }

    if not run_query:
        return info

    try:
        from knowledge.milvus_kb import MedicalKnowledgeBase
        kb = MedicalKnowledgeBase()
        results = kb.search("高血压 饮食 运动 建议", top_k=1, filter_type="lifestyle")
        info["query_ok"] = bool(results)
        info["top_score"] = round(float(results[0]["score"]), 4) if results else None
        if not results:
            info["hint"] = "知识库为空，请在「管理知识库」中上传文档或运行导入脚本"
    except Exception as e:
        info["query_ok"] = False
        info["error"] = str(e)

    return info


def check_runtime(project_root: Path = PROJECT_ROOT) -> Dict[str, Any]:
    """检查会话数据库与访问保护等运行时状态。"""
    import sqlite3

    from core.pricing import get_pricing
    from core.ratelimit import get_rate_limit

    root = Path(project_root)
    db_path = Path(
        os.getenv("MEDIX_SESSION_DB") or (root / "data" / "sessions.db")
    )
    info: Dict[str, Any] = {
        "session_db": str(db_path),
        "db_exists": db_path.is_file(),
        "db_size": db_path.stat().st_size if db_path.is_file() else 0,
        "sessions": 0,
        "messages": 0,
        "auth_enabled": bool((os.getenv("MEDIX_ACCESS_TOKEN") or "").strip()),
        "rate_limit": get_rate_limit(),
        "pricing_configured": get_pricing() is not None,
    }

    if info["db_exists"]:
        try:
            conn = sqlite3.connect(str(db_path), timeout=2)
            try:
                info["sessions"] = int(
                    conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
                )
                info["messages"] = int(
                    conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
                )
            finally:
                conn.close()
        except Exception as e:
            info["db_error"] = str(e)

    return info


def collect_report(
    project_root: Optional[Path] = None,
    run_kb: bool = False
) -> Dict[str, Any]:
    root = Path(project_root or PROJECT_ROOT)
    packages = check_packages()
    missing_required = [
        package["package"]
        for package in packages
        if package["required"] and not package["ok"]
    ]

    return {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "python_ok": sys.version_info >= MIN_PYTHON,
        "project_root": str(root),
        "packages": packages,
        "missing_required": missing_required,
        "llm_config": read_llm_config(),
        "knowledge": check_knowledge(root, run_query=run_kb),
        "runtime": check_runtime(root),
    }


def check_llm() -> Dict[str, Any]:
    """真实调用一次模型（仅 CLI 的 --llm 使用）。"""
    try:
        from openai import OpenAI
        from core.llm_client import LLM_CONFIG
    except Exception as e:
        return {"ok": False, "error": f"配置加载失败：{e}"}

    api_key = LLM_CONFIG.get("api_key") or os.getenv("MEDIX_API_KEY", "")
    if not api_key:
        return {"ok": False, "error": "未配置 MEDIX_API_KEY"}

    try:
        client = OpenAI(
            api_key=api_key,
            base_url=LLM_CONFIG.get("base_url") or None,
            timeout=20.0,
        )
        response = client.chat.completions.create(
            model=LLM_CONFIG.get("model_name"),
            messages=[{"role": "user", "content": "只回复：OK"}],
            max_tokens=8,
            temperature=0,
        )
        reply = (response.choices[0].message.content or "").strip()
        return {"ok": True, "reply": reply[:50]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def check_api(base_url: str) -> Dict[str, Any]:
    url = base_url.rstrip("/") + "/api/status"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return {"ok": bool(payload.get("ok")), "status": payload}
    except Exception as e:
        return {"ok": False, "error": str(e)}
