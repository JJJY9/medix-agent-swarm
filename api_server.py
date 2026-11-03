#!/usr/bin/env python3
"""
MediX 多智能体医疗助手 - Web API 服务

在 SwarmCoordinator 之上包一层 HTTP 接口，并托管前端页面（web/ 目录）。

启动：
    python api_server.py
    python api_server.py --port 8080 --host 127.0.0.1

浏览器打开 http://127.0.0.1:8000
"""
import argparse
import asyncio
import json
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Windows 上 stdout 被重定向时默认用 GBK，日志里的 emoji 会抛 UnicodeEncodeError
from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

WEB_DIR = project_root / "web"

# Agent 展示名，供前端协作面板使用
AGENT_LABELS = {
    "consultation_agent": "健康咨询",
    "diagnostic_agent": "症状诊断",
    "research_agent": "医学研究",
    "lead_agent": "任务协调",
}


# ===== 请求 / 响应模型 =====

class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="用户健康问题")
    session_id: Optional[str] = Field(None, description="会话ID，不传则自动生成")
    context: Optional[Dict[str, Any]] = Field(None, description="额外上下文（年龄、既往史等）")
    enable_swarm: bool = Field(True, description="是否允许群体智能协作")


class SessionResponse(BaseModel):
    session_id: str


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=40, description="新的会话标题")


# ===== 应用状态 =====

class AppState:
    """持有全局单例，避免每个请求重复初始化 Agent 和向量库。"""

    def __init__(self):
        self.coordinator = None
        self.lock = asyncio.Lock()  # Swarm 复用 Worker 实例，请求串行化
        self.kb_status = "pending"  # pending | loading | ready | failed
        self.kb_error: Optional[str] = None
        self.startup_error: Optional[str] = None

    def get_coordinator(self):
        if self.coordinator is None:
            from swarm import SwarmCoordinator
            self.coordinator = SwarmCoordinator(enable_swarm=True)
        return self.coordinator

    async def warm_up(self):
        """预加载 embedding 模型和知识库，避免第一条提问等待过久。"""
        self.kb_status = "loading"
        try:
            await asyncio.to_thread(self._load_kb_sync)
            self.kb_status = "ready"
            logger.info("知识库预热完成")
        except Exception as e:
            self.kb_status = "failed"
            self.kb_error = str(e)
            logger.error(f"知识库预热失败: {e}")

    @staticmethod
    def _load_kb_sync():
        from knowledge.milvus_kb import MedicalKnowledgeBase
        kb = MedicalKnowledgeBase()
        kb.count_documents()
        return kb


state = AppState()


class NoCacheStaticFiles(StaticFiles):
    """静态资源每次用 ETag 校验，避免升级后浏览器继续用旧 CSS/JS。"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response


@asynccontextmanager
async def lifespan(_: FastAPI):
    logger.info("MediX API 服务启动中...")
    # 只要导入不报错就说明依赖完整；真正的 LLM 调用失败会在请求时反馈
    try:
        state.get_coordinator()
    except Exception as e:
        state.startup_error = str(e)
        logger.error(f"初始化失败: {e}")
        yield
        return
    # 后台预热知识库，不阻塞服务启动
    asyncio.create_task(state.warm_up())
    yield
    logger.info("MediX API 服务已停止")


app = FastAPI(title="MediX 多智能体医疗助手", version="1.0.0", lifespan=lifespan)


# ===== API =====

@app.get("/api/status")
async def get_status():
    """健康检查 + 配置状态，前端用它决定是否显示警告。"""
    from core.llm_client import LLM_CONFIG

    api_key = (LLM_CONFIG.get("api_key") or "").strip()
    mem0_enabled = False
    if state.coordinator is not None:
        mem0_enabled = getattr(state.coordinator.long_term_memory, "enabled", False)

    return {
        "ok": state.startup_error is None,
        "startup_error": state.startup_error,
        "llm_configured": bool(api_key),
        "model_name": LLM_CONFIG.get("model_name"),
        "base_url": LLM_CONFIG.get("base_url"),
        "mem0_enabled": mem0_enabled,
        "knowledge_base": {
            "status": state.kb_status,
            "error": state.kb_error,
        },
        "agents": [
            {"id": agent_id, "name": name}
            for agent_id, name in AGENT_LABELS.items()
            if agent_id != "lead_agent"
        ],
    }


@app.post("/api/sessions", response_model=SessionResponse)
async def create_session():
    session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    return SessionResponse(session_id=session_id)


@app.get("/api/sessions")
async def list_sessions(limit: int = 50, q: Optional[str] = None):
    """历史会诊列表（按最近更新时间倒序，支持标题/编号搜索）。"""
    from memory import get_session_store

    sessions = await asyncio.to_thread(
        get_session_store().list_sessions, limit, q or ""
    )
    return {"sessions": sessions}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 50):
    from memory import ShortTermMemory, get_session_store

    stored = await asyncio.to_thread(
        get_session_store().get_messages, session_id, limit
    )
    if stored:
        return {
            "session_id": session_id,
            "messages": [
                {
                    "role": m["role"],
                    "content": m["content"],
                    "timestamp": m["created_at"],
                }
                for m in stored
                if m["role"] in ("user", "assistant")
            ],
            "source": "store",
        }

    memory = ShortTermMemory()
    messages = memory.get_recent_messages(session_id=session_id, limit=limit)
    return {
        "session_id": session_id,
        "messages": [
            {"role": m.get("role"), "content": m.get("content"), "timestamp": m.get("timestamp")}
            for m in messages
        ],
        "source": "memory",
    }


@app.patch("/api/sessions/{session_id}")
async def rename_session(session_id: str, request: RenameSessionRequest):
    from memory import get_session_store

    title = request.title.strip()
    ok = await asyncio.to_thread(
        get_session_store().rename_session, session_id, title
    )
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "session_id": session_id, "title": title}


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    from memory import ShortTermMemory, get_session_store

    ShortTermMemory().clear_session(session_id)
    await asyncio.to_thread(get_session_store().delete_session, session_id)
    return {"ok": True, "session_id": session_id}


def _empty_answer_reason() -> str:
    """
    空答复几乎总是上游 LLM 调用失败导致的：
    AgentLoop 会把每轮的异常吞掉并重试，最后返回空结果。
    与其让用户看到一片空白，不如直接说明可能的原因。
    """
    return (
        "模型没有返回任何内容。常见原因：API Key 未配置或已失效、"
        "模型名/接入点（endpoint）不正确、账户额度不足或网络无法访问 base_url。"
        "请检查上一层目录的 config.py 或本目录的 .env 配置，然后重启服务。"
    )


def _serialize_result(result: Dict[str, Any], agent_labels: Dict[str, str]) -> Dict[str, Any]:
    """把 coordinator 的原始返回值整理成前端直接可用的结构。"""
    raw_agents = result.get("agents_involved") or []
    return {
        "answer": result.get("answer", ""),
        "disclaimer": result.get("disclaimer", ""),
        "suggestions": result.get("suggestions") or [],
        "mode": "swarm" if result.get("swarm_enabled") else "single",
        "agents_involved": [
            {"id": aid, "name": agent_labels.get(aid, aid)} for aid in raw_agents
        ],
        "subtasks_completed": result.get("subtasks_completed", 0),
        "subtasks_failed": result.get("subtasks_failed", 0),
        "total_time": result.get("total_time"),
        "timeout_occurred": result.get("timeout_occurred", False),
        "session_id": result.get("session_id"),
        "route_reason": result.get("route_reason"),
    }


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """同步接口：等 Swarm 全部跑完再返回。"""
    session_id = request.session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

    async with state.lock:
        try:
            coordinator = state.get_coordinator()
            result = await coordinator.process(
                question=request.question,
                context=request.context,
                session_id=session_id,
            )
        except Exception as e:
            logger.exception("处理请求失败")
            raise HTTPException(status_code=500, detail=f"处理失败：{e}")

    payload = _serialize_result(result, AGENT_LABELS)
    if not (payload["answer"] or "").strip():
        raise HTTPException(status_code=502, detail=_empty_answer_reason())

    return payload


@app.post("/api/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    SSE 流式接口。

    两路事件：
    - 轮询 worker 的 shared_context，播报「哪个 Agent 已完成」；
    - Swarm 汇总阶段通过 on_token 回调把增量文本推成 token 事件，
      前端据此渐进渲染会诊意见。
    """
    session_id = request.session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

    async def event_stream() -> AsyncIterator[str]:
        def sse(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield sse({"type": "stage", "stage": "routing", "session_id": session_id})

        async with state.lock:
            try:
                coordinator = state.get_coordinator()
            except Exception as e:
                yield sse({"type": "error", "message": f"初始化失败：{e}"})
                return

            # 记下请求开始前 worker 上挂着的 SharedContext。
            # 只有本次请求真的走进 swarm 分支，_process_with_swarm 才会新建 SharedContext；
            # 否则读到的会是上一次会诊的残留，播报出根本没参与的科室。
            stale_contexts = {
                id(getattr(w, "shared_context", None))
                for w in getattr(coordinator, "worker_pool", [])
            }

            token_queue: asyncio.Queue[str] = asyncio.Queue()

            def on_token(delta: str) -> None:
                token_queue.put_nowait(delta)

            task = asyncio.create_task(
                coordinator.process(
                    question=request.question,
                    context=request.context,
                    session_id=session_id,
                    on_token=on_token,
                )
            )

            seen_agents = set()
            last_keepalive = asyncio.get_event_loop().time()
            try:
                while not task.done() or not token_queue.empty():
                    # 先推送汇总阶段已经产生的增量文本
                    while not token_queue.empty():
                        yield sse({
                            "type": "token",
                            "delta": token_queue.get_nowait(),
                        })

                    # worker 在 swarm 模式下会被挂上 SharedContext，据此读取进展
                    for worker in getattr(coordinator, "worker_pool", []):
                        sc = getattr(worker, "shared_context", None)
                        if sc is None or id(sc) in stale_contexts:
                            continue
                        for agent_id in list(getattr(sc, "agent_contributions", {}).keys()):
                            if agent_id not in seen_agents:
                                seen_agents.add(agent_id)
                                yield sse({
                                    "type": "agent_done",
                                    "agent_id": agent_id,
                                    "name": AGENT_LABELS.get(agent_id, agent_id),
                                })

                    now = asyncio.get_event_loop().time()
                    if now - last_keepalive >= 15:
                        # SSE 注释行，前端解析器会忽略；防止长时间无事件被代理断开
                        yield ": keep-alive\n\n"
                        last_keepalive = now

                    if not task.done():
                        await asyncio.sleep(0.1)

                try:
                    result = await task
                except Exception as e:
                    logger.exception("流式处理失败")
                    yield sse({"type": "error", "message": str(e)})
                    return
            finally:
                # 客户端断开时生成器被关闭，别让任务继续空转
                if not task.done():
                    task.cancel()

        payload = _serialize_result(result, AGENT_LABELS)
        if not (payload["answer"] or "").strip():
            yield sse({"type": "error", "message": _empty_answer_reason()})
            return

        yield sse({"type": "result", **payload})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ===== 前端静态资源 =====

@app.get("/")
async def index():
    index_file = WEB_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="前端页面不存在，请检查 web/ 目录")
    return FileResponse(index_file, headers={"Cache-Control": "no-cache"})


if WEB_DIR.exists():
    app.mount("/static", NoCacheStaticFiles(directory=str(WEB_DIR)), name="static")


def main():
    parser = argparse.ArgumentParser(description="MediX 多智能体医疗助手 Web 服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式，代码变更自动重载")
    args = parser.parse_args()

    import uvicorn

    logger.info(f"启动服务：http://{args.host}:{args.port}")
    uvicorn.run(
        "api_server:app" if args.reload else app,
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
    )


if __name__ == "__main__":
    main()
