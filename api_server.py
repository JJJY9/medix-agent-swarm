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

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Windows 上 stdout 被重定向时默认用 GBK，日志里的 emoji 会抛 UnicodeEncodeError
from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

from core.security import install_log_redaction, redact_secrets
install_log_redaction()

from core.auth import get_access_token, is_authorized
from core.triage import detect_red_flags
from core.medication_safety import check_medication_safety
from core.llm_client import LLMError
from core.ratelimit import SlidingWindowLimiter, get_rate_limit
from core.pricing import get_pricing
from core.budget import get_token_budget
from core.serialization import sanitize_json

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


class ImportSessionsRequest(BaseModel):
    version: Optional[int] = None
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    profiles: Optional[List[Dict[str, Any]]] = None


class FavoriteRequest(BaseModel):
    favorite: bool


class ArchiveRequest(BaseModel):
    archived: bool


class FeedbackRequest(BaseModel):
    message_id: int = Field(..., ge=1, description="会诊答复的消息 id")
    rating: int = Field(
        ...,
        ge=-1,
        le=1,
        description="1 有用，-1 待改进，0 清除反馈",
    )
    comment: str = Field("", max_length=500, description="可选反馈备注")


class ProfileRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=40, description="档案名称")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="年龄、性别、病程、既往史、用药与过敏史",
    )


class UpdateDocumentRequest(BaseModel):
    content: str = Field(..., min_length=1)


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
            self.kb_error = redact_secrets(str(e))
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
        state.startup_error = redact_secrets(str(e))
        logger.error(f"初始化失败: {e}")
        yield
        return
    # 后台预热知识库，不阻塞服务启动
    asyncio.create_task(state.warm_up())
    yield
    logger.info("MediX API 服务已停止")


app = FastAPI(title="MediX 多智能体医疗助手", version="1.0.0", lifespan=lifespan)

# 前端加载和健康检查必须保持公开，其余 /api/* 在设置令牌后统一校验
PUBLIC_API_PATHS = {"/api/status"}
DOC_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
CHAT_PATHS = {"/api/chat", "/api/chat/stream"}
chat_limiter = SlidingWindowLimiter(get_rate_limit())

SECURITY_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "font-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def add_security_headers(
    response: Response, include_csp: bool = True
) -> Response:
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy", "camera=(), microphone=(), geolocation=()"
    )
    if include_csp:
        response.headers.setdefault("Content-Security-Policy", SECURITY_CSP)
    return response


@app.exception_handler(StarletteHTTPException)
async def redacting_http_exception_handler(
    request: Request, exc: StarletteHTTPException
) -> Response:
    """统一对 HTTP 错误详情脱敏，避免任何端点把密钥带回前端。"""
    detail = exc.detail
    if isinstance(detail, str):
        exc.detail = redact_secrets(detail)
    elif isinstance(detail, dict):
        exc.detail = {
            key: redact_secrets(value) if isinstance(value, str) else value
            for key, value in detail.items()
        }
    return await http_exception_handler(request, exc)


@app.middleware("http")
async def require_access_token(request: Request, call_next):
    """设置 MEDIX_ACCESS_TOKEN 后，为 API 增加可选的访问令牌校验。"""
    path = request.url.path
    if (
        get_access_token()
        and path.startswith("/api/")
        and path not in PUBLIC_API_PATHS
        and not is_authorized(request)
    ):
        return add_security_headers(JSONResponse(
            status_code=401,
            content={"detail": "需要访问令牌", "code": "auth_required"},
        ))

    if path in CHAT_PATHS:
        chat_limiter.limit = get_rate_limit()
        client_key = request.client.host if request.client else "unknown"
        allowed, retry_after = chat_limiter.check(client_key)
        if not allowed:
            return add_security_headers(JSONResponse(
                status_code=429,
                content={
                    "detail": "会诊请求过于频繁，请稍后再试。",
                    "code": "rate_limited",
                    "retry_after": round(retry_after, 1),
                },
                headers={"Retry-After": str(max(1, int(retry_after)))},
            ))

    return add_security_headers(
        await call_next(request),
        include_csp=path not in DOC_PATHS,
    )


# ===== API =====

@app.get("/api/auth/verify")
async def verify_access_token():
    """受保护的轻量接口，供前端确认访问令牌是否有效。"""
    return {"ok": True}


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
        "auth_required": bool(get_access_token()),
        "llm_configured": bool(api_key),
        "pricing": get_pricing(),
        "monthly_token_budget": get_token_budget(),
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


@app.post("/api/diagnose/llm")
async def diagnose_llm():
    """用一次最小请求验证模型连通性，供系统自检抽屉使用。"""
    from core.llm_client import LLMClient, LLMError

    try:
        client = LLMClient()
        text, usage = await client.chat_with_usage_retry(
            [{"role": "user", "content": "只回复：OK"}],
            max_retries=1,
            max_tokens=8,
            temperature=0,
        )
    except LLMError as e:
        return {"ok": False, "code": e.code, "error": e.user_message}
    except Exception as e:
        return {"ok": False, "error": redact_secrets(str(e))}

    return {
        "ok": True,
        "reply": (text or "").strip()[:50],
        "usage": usage or {},
    }


@app.get("/api/diagnose")
async def diagnose():
    """轻量环境自检：Python、依赖、配置与知识库文件。"""
    from core.diagnostics import collect_report

    try:
        return await asyncio.to_thread(collect_report)
    except Exception as e:
        logger.exception("系统自检失败")
        raise HTTPException(status_code=500, detail=f"系统自检失败：{e}")


@app.post("/api/sessions", response_model=SessionResponse)
async def create_session():
    session_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    return SessionResponse(session_id=session_id)


@app.get("/api/sessions")
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    q: Optional[str] = None,
    favorite: Optional[bool] = None,
    days: int = 0,
    archived: bool = False
):
    """历史会诊列表（按最近更新时间倒序，支持搜索、分页、收藏、时间范围与归档）。

    archived=false（默认）只返回未归档会话；archived=true 只返回已归档会话。
    summary 汇总所有匹配会话的 Token 用量、模型调用次数与反馈，
    不受当前分页影响；每条会话附带自身用量。
    """
    from memory import get_session_store

    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    days = max(0, min(int(days or 0), 365))
    query = q or ""
    favorites_only = bool(favorite)

    store = get_session_store()
    sessions, total, totals, feedback = await asyncio.gather(
        asyncio.to_thread(
            store.list_sessions,
            limit,
            query,
            offset,
            favorites_only,
            days,
            archived,
        ),
        asyncio.to_thread(
            store.count_sessions, query, favorites_only, days, archived
        ),
        asyncio.to_thread(
            store.usage_totals, query, favorites_only, days, archived
        ),
        asyncio.to_thread(
            store.feedback_totals, query, favorites_only, days, archived
        ),
    )
    stats = await asyncio.to_thread(
        store.usage_by_session, [item["id"] for item in sessions]
    )

    summary: Dict[str, Any] = {
        "sessions": total,
        "loaded": len(sessions),
        "offset": offset,
        "limit": limit,
        "turns": totals["turns"],
        "llm_calls": totals["llm_calls"],
        "usage": totals["usage"],
        "feedback": feedback,
    }
    for item in sessions:
        stat = stats.get(item["id"])
        if not stat:
            continue
        item["usage"] = stat["usage"]
        item["llm_calls"] = stat["llm_calls"]
        item["turns"] = stat["turns"]

    return {"sessions": sessions, "summary": summary}


@app.get("/api/usage/daily")
async def usage_daily(days: int = 7, favorite: Optional[bool] = None):
    """按自然日汇总最近 N 天的 Token 用量与调用次数。"""
    from memory import get_session_store

    days = max(1, min(int(days or 7), 30))
    return await asyncio.to_thread(
        get_session_store().daily_usage, days, bool(favorite)
    )


@app.get("/api/usage/month")
async def usage_month():
    """本月 Token 用量与预算进度。"""
    from memory import get_session_store

    data = await asyncio.to_thread(
        get_session_store().month_usage
    )
    budget = get_token_budget()
    total = int(data["usage"].get("total_tokens") or 0)
    percent = round(total / budget * 100, 1) if budget > 0 else None

    return {
        **data,
        "budget": budget,
        "percent": percent,
        "exceeded": bool(budget > 0 and total >= budget),
    }


@app.get("/api/usage/export.csv")
async def usage_export_csv(days: int = 7, favorite: Optional[bool] = None):
    """导出按日用量 CSV（带 BOM，Excel 可直接打开）。"""
    import csv
    import io

    from memory import get_session_store

    days = max(1, min(int(days or 7), 30))
    data = await asyncio.to_thread(
        get_session_store().daily_usage, days, bool(favorite)
    )
    pricing = get_pricing()

    header = ["日期", "输入tokens", "输出tokens", "总tokens", "模型调用次数", "会诊轮次"]
    if pricing:
        header.append(f"预估费用({pricing['currency']})")

    rows = [header]
    for day in data["days"]:
        usage = day["usage"]
        row = [
            day["date"],
            usage["prompt_tokens"],
            usage["completion_tokens"],
            usage["total_tokens"],
            day["llm_calls"],
            day["turns"],
        ]
        if pricing:
            cost = (
                usage["prompt_tokens"] / 1_000_000 * pricing["input_per_million"]
                + usage["completion_tokens"] / 1_000_000 * pricing["output_per_million"]
            )
            row.append(f"{cost:.6f}")
        rows.append(row)

    totals = data["totals"]
    total_row = [
        "合计",
        totals["usage"]["prompt_tokens"],
        totals["usage"]["completion_tokens"],
        totals["usage"]["total_tokens"],
        totals["llm_calls"],
        totals["turns"],
    ]
    if pricing:
        cost = (
            totals["usage"]["prompt_tokens"] / 1_000_000 * pricing["input_per_million"]
            + totals["usage"]["completion_tokens"] / 1_000_000 * pricing["output_per_million"]
        )
        total_row.append(f"{cost:.6f}")
    rows.append(total_row)

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-usage-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/sessions/export")
async def export_sessions():
    """导出全部会话与消息（JSON），用于本地备份或迁移。"""
    from memory import get_session_store

    try:
        data = await asyncio.to_thread(get_session_store().export_data)
    except Exception as e:
        logger.exception("导出会话失败")
        raise HTTPException(status_code=500, detail=f"导出会话失败：{e}")
    return data


@app.post("/api/sessions/import")
async def import_sessions(request: ImportSessionsRequest):
    """导入备份文件；已存在的会话编号会跳过，不覆盖现有记录。"""
    from memory import get_session_store

    payload = {
        "version": request.version,
        "sessions": request.sessions,
        "profiles": request.profiles,
    }
    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("导入会话失败")
        raise HTTPException(status_code=500, detail=f"导入会话失败：{e}")

    return {"ok": True, **result}


@app.get("/api/feedback/recent")
async def recent_feedback(limit: int = 10, rating: int = -1):
    """最近的会诊反馈（默认待改进），用于质量复核。"""
    from memory import get_session_store

    if rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating 只能是 1 或 -1")
    limit = max(1, min(int(limit or 10), 50))
    items = await asyncio.to_thread(
        get_session_store().recent_feedback, limit, rating
    )
    return {"feedback": items}


@app.get("/api/profiles")
async def list_profiles():
    """常用患者档案（服务端持久化，可跨浏览器使用）。"""
    from memory import get_session_store

    profiles = await asyncio.to_thread(get_session_store().list_profiles)
    return {"profiles": profiles}


@app.post("/api/profiles")
async def save_profile(request: ProfileRequest):
    """新增或覆盖常用患者档案。"""
    from memory import get_session_store

    profile = await asyncio.to_thread(
        get_session_store().save_profile, request.name, request.context
    )
    if profile is None:
        raise HTTPException(status_code=400, detail="档案名称或内容为空")
    return {"ok": True, "profile": profile}


@app.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    from memory import get_session_store

    ok = await asyncio.to_thread(get_session_store().delete_profile, name)
    if not ok:
        raise HTTPException(status_code=404, detail="档案不存在")
    return {"ok": True, "name": name}


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 50):
    from memory import ShortTermMemory, get_session_store

    store = get_session_store()
    if limit <= 0:
        stored = await asyncio.to_thread(store.get_all_messages, session_id)
    else:
        stored = await asyncio.to_thread(store.get_messages, session_id, limit)
    if stored:
        return {
            "session_id": session_id,
            "messages": [
                {
                    "id": m.get("id"),
                    "role": m["role"],
                    "content": m["content"],
                    "timestamp": m["created_at"],
                    "metadata": m.get("metadata") or {},
                    "feedback": (m.get("metadata") or {}).get("feedback"),
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
            {
                "id": None,
                "role": m.get("role"),
                "content": m.get("content"),
                "timestamp": m.get("timestamp"),
                "metadata": {},
                "feedback": None,
            }
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


@app.patch("/api/sessions/{session_id}/favorite")
async def favorite_session(session_id: str, request: FavoriteRequest):
    from memory import get_session_store

    ok = await asyncio.to_thread(
        get_session_store().set_favorite, session_id, request.favorite
    )
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "session_id": session_id, "favorite": request.favorite}


@app.patch("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str, request: ArchiveRequest):
    """归档或移出归档；归档不会删除会话与用量记录。"""
    from memory import get_session_store

    ok = await asyncio.to_thread(
        get_session_store().set_archived, session_id, request.archived
    )
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return {"ok": True, "session_id": session_id, "archived": request.archived}


@app.post("/api/sessions/{session_id}/feedback")
async def submit_feedback(session_id: str, request: FeedbackRequest):
    """记录会诊答复的「有用 / 待改进」反馈，rating=0 时清除。"""
    from memory import get_session_store

    result = await asyncio.to_thread(
        get_session_store().set_message_feedback,
        session_id,
        request.message_id,
        request.rating,
        request.comment,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="消息不存在或不是会诊答复")
    return {"ok": True, "session_id": session_id, **result}


@app.delete("/api/sessions/{session_id}")
async def clear_session(session_id: str):
    from memory import ShortTermMemory, get_session_store

    ShortTermMemory().clear_session(session_id)
    await asyncio.to_thread(get_session_store().delete_session, session_id)
    return {"ok": True, "session_id": session_id}


# ===== 知识库管理 =====

@app.get("/api/knowledge")
async def knowledge_overview():
    """知识库文档、向量块数量与目录信息。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    try:
        async with state.lock:
            return await asyncio.to_thread(service.list_documents)
    except Exception as e:
        logger.exception("读取知识库失败")
        raise HTTPException(status_code=500, detail=f"读取知识库失败：{e}")


@app.post("/api/knowledge/rebuild")
async def knowledge_rebuild():
    """重建知识库索引（先删除旧 collection，避免重复导入）。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    async with state.lock:
        try:
            result = await asyncio.to_thread(service.rebuild)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.exception("重建知识库失败")
            raise HTTPException(status_code=500, detail=f"重建知识库失败：{e}")

    return {"ok": True, **result}


@app.post("/api/knowledge/upload")
async def knowledge_upload(
    file: UploadFile = File(...),
    rebuild: bool = True
):
    """上传 TXT / Markdown / PDF 文档，可选立即重建索引。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    content = await file.read()
    try:
        saved = await asyncio.to_thread(service.save_upload, file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("保存知识库文档失败")
        raise HTTPException(status_code=500, detail=f"保存文档失败：{e}")

    result = None
    if rebuild:
        async with state.lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("上传后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail=f"文档已保存，但重建索引失败：{e}",
                )

    return {"ok": True, "document": saved, "rebuild": result}


@app.get("/api/knowledge/search")
async def knowledge_search(q: str = "", top_k: int = 5):
    """知识库检索测试：返回来源、相关度与原文片段。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    try:
        result = await asyncio.to_thread(service.search, q, top_k)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("知识库检索失败")
        raise HTTPException(status_code=500, detail=f"检索失败：{e}")
    return result


@app.get("/api/knowledge/documents/{filename}/preview")
async def knowledge_preview(filename: str, full: bool = False):
    """预览内置或上传文档；full=1 时返回全文供编辑。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    try:
        result = await asyncio.to_thread(
            service.preview_document,
            filename,
            None if full else 4000,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("预览知识库文档失败")
        raise HTTPException(status_code=500, detail=f"读取文档失败：{e}")

    if not result:
        raise HTTPException(status_code=404, detail="文档不存在")
    return result


@app.put("/api/knowledge/documents/{filename}")
async def knowledge_update(
    filename: str,
    request: UpdateDocumentRequest,
    rebuild: bool = True
):
    """在线更新上传的 TXT / Markdown 文档，可选立即重建索引。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    content = request.content.encode("utf-8")
    try:
        saved = await asyncio.to_thread(
            service.update_document, filename, content
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("更新知识库文档失败")
        raise HTTPException(status_code=500, detail=f"保存文档失败：{e}")

    result = None
    if rebuild:
        async with state.lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("更新后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail=f"文档已保存，但重建索引失败：{e}",
                )

    return {"ok": True, "document": saved, "rebuild": result}


@app.delete("/api/knowledge/documents/{filename}")
async def knowledge_delete(filename: str, rebuild: bool = True):
    """删除用户上传的文档（内置文档不可删除）。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    try:
        deleted = await asyncio.to_thread(service.delete_document, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not deleted:
        raise HTTPException(status_code=404, detail="文档不存在")

    result = None
    if rebuild:
        async with state.lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("删除后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail=f"文档已删除，但重建索引失败：{e}",
                )

    return {"ok": True, "filename": filename, "rebuild": result}


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
    return sanitize_json({
        "answer": result.get("answer", ""),
        "disclaimer": result.get("disclaimer", ""),
        "suggestions": result.get("suggestions") or [],
        "mode": "swarm" if result.get("swarm_enabled") else "single",
        "agents_involved": [
            {"id": aid, "name": agent_labels.get(aid, aid)} for aid in raw_agents
        ],
        "agent_contributions": [
            {
                **contribution,
                "agent_name": agent_labels.get(
                    contribution.get("agent_id"), contribution.get("agent_id")
                ),
            }
            for contribution in result.get("agent_contributions") or []
        ],
        "sources": result.get("sources") or [],
        "subtasks_completed": result.get("subtasks_completed", 0),
        "subtasks_failed": result.get("subtasks_failed", 0),
        "triage": result.get("triage") or {"level": "none", "matches": [], "message": ""},
        "medication_safety": result.get("medication_safety")
        or {"level": "none", "alerts": []},
        "usage": result.get("usage") or {},
        "llm_calls": result.get("llm_calls", 0),
        "patient_context": result.get("patient_context") or {},
        "total_time": result.get("total_time"),
        "timeout_occurred": result.get("timeout_occurred", False),
        "session_id": result.get("session_id"),
        "assistant_message_id": result.get("assistant_message_id"),
        "route_reason": result.get("route_reason"),
    })


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """同步接口：等 Swarm 全部跑完再返回。"""
    session_id = request.session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"

    async with state.lock:
        try:
            coordinator = state.get_coordinator()
            process_kwargs = {
                "question": request.question,
                "context": request.context,
                "session_id": session_id,
            }
            if not request.enable_swarm:
                process_kwargs["enable_swarm"] = False
            result = await coordinator.process(**process_kwargs)
        except LLMError as e:
            logger.error(f"模型调用失败: {e.code} - {e.user_message}")
            raise HTTPException(
                status_code=502,
                detail={"code": e.code, "message": e.user_message},
            )
        except Exception as e:
            logger.exception("处理请求失败")
            raise HTTPException(
                status_code=500,
                detail=f"处理失败：{redact_secrets(str(e))}",
            )

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
    triage = detect_red_flags(request.question, request.context)
    medication_safety = check_medication_safety(request.question, request.context)

    async def event_stream() -> AsyncIterator[str]:
        def sse(payload: Dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        yield sse({"type": "stage", "stage": "routing", "session_id": session_id})

        # 急症红旗在调用模型之前就推给前端，不必等待会诊结果
        if triage["level"] != "none":
            yield sse({"type": "triage", **triage})

        # 用药安全提示同样先行推送，让用户在等待模型时就能看到
        if medication_safety["alerts"]:
            yield sse({"type": "medication_safety", **medication_safety})

        async with state.lock:
            try:
                coordinator = state.get_coordinator()
            except Exception as e:
                yield sse({"type": "error", "message": f"初始化失败：{redact_secrets(str(e))}"})
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

            stream_kwargs = {
                "question": request.question,
                "context": request.context,
                "session_id": session_id,
                "on_token": on_token,
            }
            if not request.enable_swarm:
                stream_kwargs["enable_swarm"] = False
            task = asyncio.create_task(
                coordinator.process(**stream_kwargs)
            )

            seen_agents = set()
            started_agents = set()
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

                        # 子任务进入执行中时先点亮对应科室
                        for subtask in list(
                            getattr(sc, "task_decomposition", {}).values()
                        ):
                            agent_id = getattr(subtask, "assigned_agent", None)
                            status = getattr(
                                getattr(subtask, "status", None), "value", ""
                            )
                            if (
                                agent_id
                                and status == "in_progress"
                                and agent_id not in started_agents
                            ):
                                started_agents.add(agent_id)
                                yield sse({
                                    "type": "agent_started",
                                    "agent_id": agent_id,
                                    "name": AGENT_LABELS.get(agent_id, agent_id),
                                })

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
                except LLMError as e:
                    logger.error(f"流式模型调用失败: {e.code} - {e.user_message}")
                    yield sse({
                        "type": "error",
                        "code": e.code,
                        "message": e.user_message,
                    })
                    return
                except Exception as e:
                    logger.exception("流式处理失败")
                    yield sse({
                        "type": "error",
                        "message": redact_secrets(str(e)),
                    })
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
