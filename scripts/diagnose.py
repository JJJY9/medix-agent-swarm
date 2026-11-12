#!/usr/bin/env python3
"""MediX 环境自检脚本。

灵感来自 MiQi 的 diagnose 脚本：用户在提 Issue 前跑一遍，把输出粘进模板，
维护者不用反复追问环境信息。Web 端的「系统自检」与本脚本共用 core.diagnostics。

用法：
    python scripts/diagnose.py                  # 基础检查
    python scripts/diagnose.py --llm            # 额外测试 LLM 连通性（消耗少量额度）
    python scripts/diagnose.py --kb             # 额外加载知识库并做一次检索（较慢）
    python scripts/diagnose.py --api http://127.0.0.1:8000
    python scripts/diagnose.py --json           # 输出机器可读结果
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any, Dict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

#: 不导入 core 也能检查的必需依赖（core/__init__ 会连锁导入整个 agent 栈）
_REQUIRED_MODULES = (
    ("openai", "openai"),
    ("loguru", "loguru"),
    ("yaml", "pyyaml"),
    ("dotenv", "python-dotenv"),
    ("numpy", "numpy"),
    ("fastapi", "fastapi"),
    ("uvicorn", "uvicorn"),
    # sentence-transformers 只影响知识库，交给报告标记缺失即可，
    # 不让它阻断自检本身（CI 的轻量依赖集不含它）
)


def _missing_dependencies() -> list:
    missing = []
    for module_name, package_name in _REQUIRED_MODULES:
        try:
            available = (
                importlib.util.find_spec(module_name) is not None
            )
        except (ImportError, ModuleNotFoundError, ValueError):
            available = False
        if not available:
            missing.append(package_name)
    return missing


_missing = _missing_dependencies()
if _missing:
    print("❌ 缺少依赖：" + "、".join(_missing))
    print("请先安装依赖：python -m pip install -r requirements.txt")
    raise SystemExit(2)

try:
    from core.console import ensure_utf8_stdio

    ensure_utf8_stdio()
except Exception:
    # 标准输出仍是 GBK 时，至少保证本脚本的报错不会因 emoji 再抛异常
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

try:
    from core.diagnostics import check_api, check_llm, collect_report
except Exception as exc:  # 缺依赖时不再直接抛 traceback
    print("❌ 自检模块加载失败：" + str(exc))
    print("请先安装依赖：python -m pip install -r requirements.txt")
    raise SystemExit(2)


def print_report(report: Dict[str, Any]) -> None:
    def mark(ok: bool) -> str:
        return "✅" if ok else "❌"

    print("=" * 64)
    print("MediX 环境自检")
    print("=" * 64)
    print(f"系统      : {report['os']}")
    print(
        f"Python    : {report['python']} "
        f"{'(OK)' if report['python_ok'] else '(需要 >= 3.10)'}"
    )
    print(f"项目目录  : {report['project_root']}")
    print()

    print("依赖：")
    for package in report["packages"]:
        if package["ok"]:
            print(f"  {mark(True)} {package['package']:<22} {package.get('version') or '未知版本'}")
        else:
            level = "❌" if package["required"] else "⚠️"
            suffix = "" if package["required"] else "（可选）"
            print(f"  {level} {package['package']:<22} 未安装{suffix}")
    print()

    config = report["llm_config"]
    print("LLM 配置：")
    print(
        f"  {mark(config.get('api_key_set'))} API Key : "
        f"{'已配置' if config.get('api_key_set') else '未配置'}"
    )
    print(f"  模型     : {config.get('model_name') or '—'}")
    print(f"  接口地址 : {config.get('base_url') or '—'}")
    if config.get("load_error"):
        print(f"  ⚠️ 配置加载提示: {config['load_error']}")
    print()

    knowledge = report["knowledge"]
    print("知识库：")
    print(f"  内置文档 : {knowledge['documents']} 个")
    print(f"  上传文档 : {knowledge['uploads']} 个")
    print(f"  向量库   : {', '.join(knowledge['vector_store_files']) or '未生成'}")
    if "query_ok" in knowledge:
        print(
            f"  {mark(knowledge['query_ok'])} 检索测试 : "
            f"{'通过' if knowledge['query_ok'] else '失败'}"
            + (
                f"（最高相似度 {knowledge['top_score']}）"
                if knowledge.get("top_score") is not None else ""
            )
        )
    if knowledge.get("hint"):
        print(f"  💡 {knowledge['hint']}")
    if knowledge.get("error"):
        print(f"  ❌ {knowledge['error']}")
    print()

    runtime = report.get("runtime") or {}
    if runtime:
        print("运行时：")
        db_state = (
            "存在" if runtime.get("db_exists") else "尚未创建（首次启动后生成）"
        )
        print(f"  会话数据库 : {runtime.get('session_db') or '—'}（{db_state}）")
        if runtime.get("db_exists"):
            attachment_mb = float(runtime.get("attachment_size") or 0) / (
                1024 * 1024
            )
            print(
                "  数据量     : "
                f"{runtime.get('sessions', 0)} 个会话，"
                f"{runtime.get('messages', 0)} 条消息；"
                f"{runtime.get('attachment_count', 0)} 个附件"
                f"（{attachment_mb:.1f} MB）"
            )
        print(
            "  访问保护   : "
            + ("已启用" if runtime.get("auth_enabled") else "未启用")
        )
        print(f"  会诊限流   : {runtime.get('rate_limit', 0)} 次/分钟")
        if runtime.get("db_error"):
            print(f"  ❌ 数据库读取失败：{runtime['db_error']}")
        if runtime.get("data_errors"):
            print(
                "  ❌ 数据表读取失败："
                + "、".join(str(item) for item in runtime["data_errors"])
            )
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
    runtime = report.get("runtime") or {}
    ok = ok and not runtime.get("db_error") and not runtime.get("data_errors")
    if "llm" in report:
        ok = ok and report["llm"]["ok"]
    if "api" in report:
        ok = ok and report["api"]["ok"]
    if "query_ok" in knowledge:
        ok = ok and bool(knowledge["query_ok"])

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

    report = collect_report(run_kb=args.kb)
    if args.llm:
        report["llm"] = check_llm()
    if args.api:
        report["api"] = check_api(args.api)

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
        and report.get("knowledge", {}).get("query_ok", True)
    )
    runtime = report.get("runtime") or {}
    ok = ok and not runtime.get("db_error") and not runtime.get("data_errors")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
