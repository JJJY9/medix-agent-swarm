#!/usr/bin/env python3
"""MediX 环境自检脚本。

灵感来自 MiQi 的 diagnose 脚本：用户在提 Issue 前跑一遍，把输出粘进模板，
维护者不用反复追问环境信息。

用法：
    python scripts/diagnose.py                  # 基础检查
    python scripts/diagnose.py --llm            # 额外测试 LLM 连通性（消耗少量额度）
    python scripts/diagnose.py --kb             # 额外加载知识库并做一次检索（较慢）
    python scripts/diagnose.py --api http://127.0.0.1:8000
    python scripts/diagnose.py --json           # 输出机器可读结果
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import platform
import sys
import urllib.request
from importlib import metadata
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

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


def package_version(dist_name: str) -> Optional[str]:
    try:
        return metadata.version(dist_name)
    except metadata.PackageNotFoundError:
        return None


def check_packages() -> List[Dict[str, Any]]:
    results = []
    for module_name, dist_name in {**REQUIRED_PACKAGES, **OPTIONAL_PACKAGES}.items():
        required = module_name in REQUIRED_PACKAGES
        try:
            importlib.import_module(module_name)
            results.append({
                "package": dist_name,
                "required": required,
                "ok": True,
                "version": package_version(dist_name),
            })
        except Exception as e:  # ImportError 或依赖自带的初始化异常
            results.append({
                "package": dist_name,
                "required": required,
                "ok": False,
                "error": str(e),
            })
    return results


def read_llm_config() -> Dict[str, Any]:
    """优先走项目自己的配置加载逻辑（.env > 上一层 config.py）。"""
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


def check_knowledge(run_query: bool) -> Dict[str, Any]:
    docs_dir = PROJECT_ROOT / "knowledge" / "data" / "documents"
    documents = sorted(docs_dir.glob("*.txt")) if docs_dir.exists() else []
    data_dir = PROJECT_ROOT / "knowledge" / "data"
    store_files = sorted(
        [p.name for p in data_dir.glob("milvus_lite.db*")] if data_dir.exists() else []
    )

    info: Dict[str, Any] = {
        "documents": len(documents),
        "vector_store_files": store_files,
        "ready": bool(documents),
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
            info["hint"] = "知识库为空，请先运行 python knowledge/scripts/import_hardcoded_data.py"
    except Exception as e:
        info["query_ok"] = False
        info["error"] = str(e)

    return info


def check_llm(config: Dict[str, Any]) -> Dict[str, Any]:
    if not config.get("api_key_set"):
        return {"ok": False, "error": "未配置 MEDIX_API_KEY"}

    try:
        from openai import OpenAI

        client = OpenAI(
            api_key=config.get("_api_key") or os.getenv("MEDIX_API_KEY", ""),
            base_url=config.get("base_url") or None,
            timeout=20.0,
        )
        response = client.chat.completions.create(
            model=config.get("model_name"),
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


def collect_report(args: argparse.Namespace) -> Dict[str, Any]:
    python_ok = sys.version_info >= MIN_PYTHON
    packages = check_packages()
    missing_required = [
        p["package"] for p in packages if p["required"] and not p["ok"]
    ]

    config = read_llm_config()
    # check_llm 需要真实 key，又不想把它写进报告
    report_config = dict(config)
    report_config.pop("_api_key", None)

    report: Dict[str, Any] = {
        "os": f"{platform.system()} {platform.release()}",
        "python": platform.python_version(),
        "python_ok": python_ok,
        "project_root": str(PROJECT_ROOT),
        "packages": packages,
        "missing_required": missing_required,
        "llm_config": report_config,
        "knowledge": check_knowledge(args.kb),
    }

    if args.llm:
        llm_config = dict(config)
        try:
            from core.llm_client import LLM_CONFIG
        except Exception as e:
            report["llm"] = {"ok": False, "error": f"配置加载失败：{e}"}
        else:
            llm_config["_api_key"] = LLM_CONFIG.get("api_key", "")
            report["llm"] = check_llm(llm_config)

    if args.api:
        report["api"] = check_api(args.api)

    return report


def print_report(report: Dict[str, Any]) -> None:
    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    print("=" * 64)
    print("MediX 环境自检")
    print("=" * 64)
    print(f"系统      : {report['os']}")
    print(f"Python    : {report['python']} {'(OK)' if report['python_ok'] else '(需要 >= 3.10)'}")
    print(f"项目目录  : {report['project_root']}")
    print()

    print("依赖：")
    for package in report["packages"]:
        if package["ok"]:
            version = package.get("version") or "未知版本"
            print(f"  {mark(True)} {package['package']:<22} {version}")
        else:
            level = "❌" if package["required"] else "⚠️"
            print(f"  {level} {package['package']:<22} 未安装（可选）" if not package["required"]
                  else f"  {level} {package['package']:<22} 未安装")
    print()

    config = report["llm_config"]
    print("LLM 配置：")
    print(f"  {mark(config.get('api_key_set'))} API Key : {'已配置' if config.get('api_key_set') else '未配置'}")
    print(f"  模型     : {config.get('model_name') or '—'}")
    print(f"  接口地址 : {config.get('base_url') or '—'}")
    if config.get("load_error"):
        print(f"  ⚠️ 配置加载提示: {config['load_error']}")
    print()

    knowledge = report["knowledge"]
    print("知识库：")
    print(f"  文档数量 : {knowledge['documents']} 个")
    print(f"  向量库   : {', '.join(knowledge['vector_store_files']) or '未生成'}")
    if "query_ok" in knowledge:
        print(f"  {mark(knowledge['query_ok'])} 检索测试 : "
              f"{'通过' if knowledge['query_ok'] else '失败'}"
              + (f"（最高相似度 {knowledge['top_score']}）" if knowledge.get("top_score") is not None else ""))
    if knowledge.get("hint"):
        print(f"  💡 {knowledge['hint']}")
    if knowledge.get("error"):
        print(f"  ❌ {knowledge['error']}")
    print()

    if "llm" in report:
        llm = report["llm"]
        print(f"LLM 连通性：{mark(llm['ok'])} " + (llm.get("reply") or llm.get("error", "")))
        print()

    if "api" in report:
        api = report["api"]
        print(f"API 服务：{mark(api['ok'])} " + (
            "正常" if api["ok"] else api.get("error", "异常")
        ))
        print()

    ok = (
        report["python_ok"]
        and not report["missing_required"]
        and report["llm_config"].get("api_key_set")
    )
    if "llm" in report:
        ok = ok and report["llm"]["ok"]
    if "api" in report:
        ok = ok and report["api"]["ok"]

    print("-" * 64)
    print("结论：" + ("✅ 环境正常，可以运行" if ok else "⚠️ 存在问题，请按上面的提示处理"))
    print("=" * 64)


def main() -> int:
    parser = argparse.ArgumentParser(description="MediX 环境自检")
    parser.add_argument("--llm", action="store_true", help="测试 LLM 接口连通性")
    parser.add_argument("--kb", action="store_true", help="加载知识库并做一次检索")
    parser.add_argument("--api", metavar="URL", help="检查运行中的 API 服务，如 http://127.0.0.1:8000")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args()

    report = collect_report(args)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print_report(report)

    ok = (
        report["python_ok"]
        and not report["missing_required"]
        and report["llm_config"].get("api_key_set")
        and report.get("llm", {}).get("ok", True)
        and report.get("api", {}).get("ok", True)
    )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
