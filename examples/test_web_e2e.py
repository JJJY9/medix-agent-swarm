"""端到端验证脚本：确认 SSE 播报的科室与实际参与的一致。"""
import json
import os
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

# 服务开启访问令牌时，脚本也要读取同一份 .env 才能通过鉴权
try:
    from dotenv import load_dotenv

    load_dotenv(project_root / ".env", encoding="utf-8-sig")
except Exception:
    pass

# Windows 控制台默认 GBK，答复里带 emoji 会抛 UnicodeEncodeError
from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

import httpx

BASE = "http://127.0.0.1:8000"
TOKEN = (os.getenv("MEDIX_ACCESS_TOKEN") or "").strip()


def ask(question: str, sid: str) -> dict:
    events = []
    headers = {"X-Medix-Token": TOKEN} if TOKEN else {}
    with httpx.Client(timeout=300) as client:
        with client.stream(
            "POST",
            f"{BASE}/api/chat/stream",
            json={"question": question, "session_id": sid, "enable_swarm": True},
            headers=headers,
        ) as resp:
            if resp.status_code != 200:
                detail = ""
                try:
                    payload = resp.json()
                    if isinstance(payload, dict):
                        detail = str(payload.get("detail") or "")
                except Exception:
                    pass
                message = f"HTTP {resp.status_code}"
                if detail:
                    message += f"：{detail}"
                if resp.status_code == 401:
                    message += "（请先在 .env 设置 MEDIX_ACCESS_TOKEN）"
                return {"error": message, "broadcast": []}
            content_type = resp.headers.get("content-type", "")
            if "text/event-stream" not in content_type:
                return {
                    "error": f"响应不是 SSE（{content_type or '未知类型'}）",
                    "broadcast": [],
                }
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                events.append(ev)

    done = [e["name"] for e in events if e.get("type") == "agent_done"]
    err = next((e["message"] for e in events if e.get("type") == "error"), None)
    result = next((e for e in events if e.get("type") == "result"), None)

    if err:
        return {"error": err, "broadcast": done}
    if result is None:
        return {"error": "未收到 result 事件", "broadcast": done}

    involved = [a["name"] for a in result.get("agents_involved", [])]
    return {
        "mode": result["mode"],
        "broadcast": sorted(done),
        "actual": sorted(involved),
        "consistent": sorted(done) == sorted(involved),
        "answer_len": len(result.get("answer") or ""),
        "suggestions": len(result.get("suggestions") or []),
        "time": result.get("total_time"),
        "answer": result.get("answer") or "",
    }


def main():
    cases = [
        ("简单问题", "高血压患者每天应该测量几次血压？", "smoke-simple"),
        ("复杂问题", "最近三天持续发热咳嗽伴咽痛，还有胸闷，需要去医院吗？", "smoke-complex"),
    ]
    report = []
    for label, q, sid in cases:
        print(f"\n{'=' * 68}\n{label}：{q}\n{'=' * 68}")
        r = ask(q, sid)
        if "error" in r:
            print("  失败：", r["error"])
            report.append(False)
            continue
        print(f"  路由模式   : {r['mode']}")
        print(f"  SSE 播报   : {r['broadcast'] or '（无）'}")
        print(f"  实际参与   : {r['actual'] or '（无）'}")
        print(f"  播报一致   : {'是' if r['consistent'] else '否 ✗'}")
        print(f"  答复长度   : {r['answer_len']} 字，建议 {r['suggestions']} 条，用时 {r['time']}")
        print(f"\n  --- 答复前 400 字 ---")
        print("  " + (r["answer"][:400].replace("\n", "\n  ") or "(空)"))
        report.append(r["consistent"] and r["answer_len"] > 0)

    print(f"\n{'=' * 68}")
    print("全部通过" if all(report) else "存在失败用例")
    return 0 if all(report) else 1


if __name__ == "__main__":
    sys.exit(main())
