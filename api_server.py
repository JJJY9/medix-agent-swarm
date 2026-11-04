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
import re
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

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
from core.retention import get_auto_archive_days
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
    patient_profile: str = Field(
        "", max_length=40, description="关联的常用患者档案名称"
    )


class SessionResponse(BaseModel):
    session_id: str


class RenameSessionRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=40, description="新的会话标题")


class ImportSessionsRequest(BaseModel):
    version: Optional[int] = None
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    profiles: Optional[List[Dict[str, Any]]] = None
    health_metrics: Optional[List[Dict[str, Any]]] = None
    metric_targets: Optional[List[Dict[str, Any]]] = None
    reminders: Optional[List[Dict[str, Any]]] = None


class FavoriteRequest(BaseModel):
    favorite: bool


class ArchiveRequest(BaseModel):
    archived: bool


class TagsRequest(BaseModel):
    tags: List[str] = Field(default_factory=list)


class PurgeRequest(BaseModel):
    days: int = Field(..., ge=1, le=3650, description="删除多少天以前的会诊")


class BulkSessionsRequest(BaseModel):
    ids: List[str] = Field(..., min_length=1, max_length=200)


class BulkArchiveRequest(BulkSessionsRequest):
    archived: bool


class BulkTagsRequest(BulkSessionsRequest):
    tags: List[str] = Field(default_factory=list)


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


class ProfileRenameRequest(BaseModel):
    new_name: str = Field(..., min_length=1, max_length=40)


class ReminderRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=120, description="提醒内容")
    due_at: str = Field(
        ..., min_length=8, max_length=32, description="YYYY-MM-DD 或 ISO 时间"
    )
    note: str = Field("", max_length=300, description="可选备注")
    session_id: str = Field("", max_length=64, description="关联的会诊编号")
    profile: str = Field("", max_length=40, description="关联的患者档案名称")


class ReminderUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    due_at: Optional[str] = Field(None, max_length=32)
    note: Optional[str] = Field(None, max_length=300)


class BmiRequest(BaseModel):
    height_cm: float = Field(..., gt=50, lt=260, description="身高（厘米）")
    weight_kg: float = Field(..., gt=10, lt=400, description="体重（千克）")


class EgfrRequest(BaseModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    sex: Literal["male", "female"]
    creatinine: float = Field(..., gt=0, lt=3000, description="血肌酐")
    unit: Literal["mg/dL", "umol/L"] = "mg/dL"


class ChadsVascRequest(BaseModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    sex: Literal["male", "female"]
    chf: bool = False
    hypertension: bool = False
    diabetes: bool = False
    stroke_tia: bool = False
    vascular_disease: bool = False


class Curb65Request(BaseModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    confusion: bool = False
    urea: float = Field(..., ge=0, le=100, description="尿素氮（mmol/L）")
    respiratory_rate: float = Field(..., ge=0, le=100, description="呼吸频率（次/分）")
    systolic_bp: float = Field(..., ge=0, le=300, description="收缩压（mmHg）")
    diastolic_bp: float = Field(..., ge=0, le=200, description="舒张压（mmHg）")


class ChildPughRequest(BaseModel):
    bilirubin: int = Field(..., ge=1, le=3)
    albumin: int = Field(..., ge=1, le=3)
    inr: int = Field(..., ge=1, le=3)
    ascites: int = Field(..., ge=1, le=3)
    encephalopathy: int = Field(..., ge=1, le=3)


class Phq9Request(BaseModel):
    answers: List[int] = Field(..., min_length=9, max_length=9)


class Gad7Request(BaseModel):
    answers: List[int] = Field(..., min_length=7, max_length=7)


class HasBledRequest(BaseModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    hypertension: bool = False
    abnormal_renal: bool = False
    abnormal_liver: bool = False
    stroke_history: bool = False
    bleeding_history: bool = False
    labile_inr: bool = False
    drugs: bool = False
    alcohol: bool = False


class GcsRequest(BaseModel):
    eye: int = Field(..., ge=1, le=4, description="睁眼反应")
    verbal: int = Field(..., ge=1, le=5, description="语言反应")
    motor: int = Field(..., ge=1, le=6, description="运动反应")


class WellsDvtRequest(BaseModel):
    active_cancer: bool = False
    paralysis_or_immobilization: bool = False
    bedridden_or_surgery: bool = False
    deep_vein_tenderness: bool = False
    entire_leg_swelling: bool = False
    calf_swelling: bool = False
    pitting_edema: bool = False
    collateral_veins: bool = False
    previous_dvt: bool = False
    alternative_diagnosis: bool = False


class WellsPeRequest(BaseModel):
    clinical_signs_dvt: bool = False
    pe_more_likely: bool = False
    heart_rate_gt_100: bool = False
    immobilization_or_surgery: bool = False
    previous_dvt_pe: bool = False
    hemoptysis: bool = False
    malignancy: bool = False


class InteractionsRequest(BaseModel):
    drugs: str = Field(
        ..., min_length=1, max_length=1000, description="药物列表，可换行或顿号分隔"
    )
    allergies: str = Field("", max_length=200, description="可选过敏史")


class QsofaRequest(BaseModel):
    respiratory_rate: float = Field(..., gt=0, le=80, description="呼吸频率（次/分）")
    systolic_bp: float = Field(..., gt=0, le=300, description="收缩压（mmHg）")
    altered_mentation: bool = False


class Fib4Request(BaseModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    ast: float = Field(..., gt=0, le=10000, description="AST（U/L）")
    alt: float = Field(..., gt=0, le=10000, description="ALT（U/L）")
    platelets: float = Field(..., gt=0, le=2000, description="血小板（×10⁹/L）")


class MeldRequest(BaseModel):
    bilirubin: float = Field(..., gt=0, le=100, description="总胆红素（mg/dL）")
    inr: float = Field(..., gt=0, le=20, description="INR")
    creatinine: float = Field(..., gt=0, le=30, description="血肌酐（mg/dL）")
    dialysis: bool = False


class StopBangRequest(BaseModel):
    snoring: bool = False
    tired: bool = False
    observed_apnea: bool = False
    hypertension: bool = False
    bmi_gt_35: bool = False
    age_gt_50: bool = False
    neck_gt_40cm: bool = False
    male: bool = False


class PaduaRequest(BaseModel):
    active_cancer: bool = False
    previous_vte: bool = False
    reduced_mobility: bool = False
    thrombophilia: bool = False
    recent_trauma_surgery: bool = False
    age_ge_70: bool = False
    heart_respiratory_failure: bool = False
    acute_mi_stroke: bool = False
    acute_infection_rheumatologic: bool = False
    bmi_ge_30: bool = False
    hormonal_treatment: bool = False


class Abcd2Request(BaseModel):
    age_ge_60: bool = False
    bp_ge_140_90: bool = False
    unilateral_weakness: bool = False
    speech_disturbance: bool = False
    duration_ge_60: bool = False
    duration_10_59: bool = False
    diabetes: bool = False


class MorseFallRequest(BaseModel):
    history_of_falling: Literal[0, 25] = 0
    secondary_diagnosis: Literal[0, 15] = 0
    ambulatory_aid: Literal[0, 15, 30] = 0
    intravenous: Literal[0, 20] = 0
    gait: Literal[0, 10, 20] = 0
    mental_status: Literal[0, 15] = 0


class MetricRequest(BaseModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    metric: Literal[
        "bp", "weight", "glucose", "temperature", "phq9", "gad7"
    ]
    value1: float
    value2: Optional[float] = None
    measured_at: str = Field("", max_length=40, description="测量时间（ISO）")
    note: str = Field("", max_length=300)


class MetricTargetRequest(BaseModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    metric: Literal[
        "bp", "weight", "glucose", "temperature", "phq9", "gad7"
    ]
    value1: float = Field(..., description="目标上限（血压为收缩压）")
    value2: Optional[float] = Field(None, description="血压目标舒张压")


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


async def auto_archive_sessions() -> None:
    """按 MEDIX_AUTO_ARCHIVE_DAYS 自动归档旧会话（不删除）。"""
    days = get_auto_archive_days()
    if days <= 0:
        return
    try:
        from memory import get_session_store

        count = await asyncio.to_thread(
            get_session_store().archive_older_than, days
        )
        if count:
            logger.info(f"自动归档 {count} 个超过 {days} 天的会话")
    except Exception as e:
        logger.warning(f"自动归档旧会话失败: {e}")


async def record_audit(action: str, target: str = "", detail: str = "") -> None:
    """记录操作审计；审计失败不影响主流程。"""
    try:
        from memory import get_session_store

        await asyncio.to_thread(
            get_session_store().log_action, action, target, detail
        )
    except Exception as e:
        logger.warning(f"记录操作审计失败: {e}")


async def _link_patient_profile(session_id: str, raw_profile: str) -> None:
    """把已有会话关联到患者档案；失败只记日志，不影响会诊结果。"""
    profile = " ".join(str(raw_profile or "").split())
    if not profile:
        return
    try:
        from memory import get_session_store

        await asyncio.to_thread(
            get_session_store().set_session_profile, session_id, profile
        )
    except Exception as e:
        logger.warning(f"关联患者档案失败（忽略）: {redact_secrets(str(e))}")


def csv_safe_cell(value: Any) -> str:
    """防止 CSV 单元格被表格软件当作公式执行。"""
    text = str(value if value is not None else "")
    if text[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


def reminder_audit_target(reminder: Dict[str, Any]) -> str:
    return f"{reminder.get('id')}·{reminder.get('title', '')}"[:120]


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
    asyncio.create_task(auto_archive_sessions())
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


AUDIT_DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2}(:\d{2})?)?$")


def audit_date_param(name: str, value: Optional[str]) -> Optional[str]:
    """校验审计筛选日期：接受 YYYY-MM-DD 或 ISO 时间。"""
    value = (value or "").strip()
    if not value:
        return None
    if not AUDIT_DATE_PATTERN.match(value):
        raise HTTPException(
            status_code=400,
            detail=f"{name} 格式应为 YYYY-MM-DD 或 ISO 时间",
        )
    return value


def audit_filter_kwargs(
    action: Optional[str],
    start: Optional[str],
    end: Optional[str],
    keyword: Optional[str],
) -> Dict[str, Any]:
    return {
        "action": action or None,
        "start": audit_date_param("start", start),
        "end": audit_date_param("end", end),
        "keyword": keyword or None,
    }


@app.get("/api/audit")
async def audit_log(
    limit: int = 50,
    action: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    q: Optional[str] = None,
):
    """最近的操作审计（删除、归档、导入导出等），支持筛选。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 50), 500))
    kwargs = audit_filter_kwargs(action, start, end, q)
    store = get_session_store()
    actions = await asyncio.to_thread(
        store.list_audit_log, limit, **kwargs
    )
    available = await asyncio.to_thread(store.list_audit_actions)
    return {"actions": actions, "available_actions": available}


@app.get("/api/audit/export.csv")
async def export_audit_csv(
    action: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
    q: Optional[str] = None,
):
    """导出操作审计 CSV（带 BOM），筛选条件与列表一致。"""
    import csv
    import io

    from memory import get_session_store

    kwargs = audit_filter_kwargs(action, start, end, q)
    items = await asyncio.to_thread(
        get_session_store().list_audit_log, 100000, **kwargs
    )
    rows = [["时间", "动作", "对象", "说明"]]
    for item in reversed(items):
        rows.append([
            item.get("created_at", ""),
            item.get("action", ""),
            csv_safe_cell(item.get("target", "")),
            csv_safe_cell(item.get("detail", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-audit-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
        "auto_archive_days": get_auto_archive_days(),
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
    archived: bool = False,
    tag: Optional[str] = None,
    profile: Optional[str] = None
):
    """历史会诊列表（按最近更新时间倒序，支持搜索、分页、收藏、时间范围、归档、标签与档案）。

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
    profile = profile or None

    store = get_session_store()
    sessions, total, combined = await asyncio.gather(
        asyncio.to_thread(
            store.list_sessions,
            limit,
            query,
            offset,
            favorites_only,
            days,
            archived,
            tag,
            profile,
        ),
        asyncio.to_thread(
            store.count_sessions,
            query,
            favorites_only,
            days,
            archived,
            tag,
            profile,
        ),
        asyncio.to_thread(
            store.session_totals,
            query,
            favorites_only,
            days,
            archived,
            tag,
            profile,
        ),
    )
    totals = {
        "usage": combined["usage"],
        "llm_calls": combined["llm_calls"],
        "turns": combined["turns"],
    }
    feedback = combined["feedback"]
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
async def usage_daily(
    days: int = 7,
    favorite: Optional[bool] = None,
    profile: Optional[str] = None,
):
    """按自然日汇总最近 N 天的 Token 用量与调用次数。"""
    from memory import get_session_store

    days = max(1, min(int(days or 7), 30))
    return await asyncio.to_thread(
        get_session_store().daily_usage, days, bool(favorite), profile
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
async def usage_export_csv(
    days: int = 7,
    favorite: Optional[bool] = None,
    profile: Optional[str] = None,
):
    """导出按日用量 CSV（带 BOM，Excel 可直接打开）。"""
    import csv
    import io

    from memory import get_session_store

    days = max(1, min(int(days or 7), 30))
    data = await asyncio.to_thread(
        get_session_store().daily_usage, days, bool(favorite), profile
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
    await record_audit(
        "session.export",
        detail=f"sessions={data.get('session_count', 0)}",
    )
    return data


@app.get("/api/sessions/export.csv")
async def export_sessions_csv(
    q: Optional[str] = None,
    favorite: Optional[bool] = None,
    days: int = 0,
    archived: bool = False,
    tag: Optional[str] = None,
    profile: Optional[str] = None,
):
    """按当前筛选导出会话列表 CSV（带 BOM），用于表格分析。"""
    import csv
    import io

    from memory import get_session_store

    store = get_session_store()
    query = q or ""
    favorites_only = bool(favorite)
    days = max(0, min(int(days or 0), 365))
    profile = profile or None

    items: List[Dict[str, Any]] = []
    offset = 0
    while True:
        batch = await asyncio.to_thread(
            store.list_sessions,
            200,
            query,
            offset,
            favorites_only,
            days,
            archived,
            tag,
            profile,
        )
        items.extend(batch)
        if len(batch) < 200:
            break
        offset += len(batch)

    usage = await asyncio.to_thread(
        store.usage_by_session, [item["id"] for item in items]
    )
    rows = [[
        "会话编号", "标题", "档案", "标签", "收藏", "归档",
        "消息数", "轮次", "Token", "模型调用", "创建时间", "更新时间",
    ]]
    for item in items:
        stats = usage.get(item["id"], {})
        rows.append([
            item.get("id", ""),
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell("、".join(item.get("tags") or [])),
            "是" if item.get("favorite") else "否",
            "是" if item.get("archived") else "否",
            item.get("message_count", 0),
            stats.get("turns", 0),
            (stats.get("usage") or {}).get("total_tokens", 0),
            stats.get("llm_calls", 0),
            item.get("created_at", ""),
            item.get("updated_at", ""),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-sessions-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/sessions/import")
async def import_sessions(request: ImportSessionsRequest):
    """导入备份文件；已存在的会话编号会跳过，不覆盖现有记录。"""
    from memory import get_session_store

    payload = {
        "version": request.version,
        "sessions": request.sessions,
        "profiles": request.profiles,
        "health_metrics": request.health_metrics,
        "metric_targets": request.metric_targets,
        "reminders": request.reminders,
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

    await record_audit(
        "session.import",
        detail=(
            f"imported={result.get('imported', 0)} "
            f"skipped={result.get('skipped', 0)} "
            f"profiles={result.get('profiles', 0)} "
            f"metrics={result.get('metrics', 0)} "
            f"targets={result.get('targets', 0)} "
            f"reminders={result.get('reminders', 0)}"
        ),
    )
    return {"ok": True, **result}


@app.post("/api/maintenance/purge")
async def purge_old_sessions(request: PurgeRequest):
    """删除指定天数以前的会诊及其消息（不可恢复）。"""
    from datetime import timedelta

    from memory import get_session_store

    cutoff = (
        datetime.now() - timedelta(days=request.days)
    ).isoformat(timespec="seconds")
    result = await asyncio.to_thread(
        get_session_store().purge_sessions_before, cutoff
    )
    await record_audit(
        "maintenance.purge",
        f"days={request.days}",
        f"sessions={result['sessions']} messages={result['messages']}",
    )
    return {"ok": True, **result, "cutoff": cutoff}


@app.get("/api/feedback/recent")
async def recent_feedback(
    limit: int = 10,
    rating: int = -1,
    profile: Optional[str] = None,
):
    """最近的会诊反馈（默认待改进），用于质量复核。"""
    from memory import get_session_store

    if rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating 只能是 1 或 -1")
    limit = max(1, min(int(limit or 10), 50))
    items = await asyncio.to_thread(
        get_session_store().recent_feedback, limit, rating, profile
    )
    return {"feedback": items}


@app.get("/api/feedback/export.csv")
async def export_feedback_csv(
    rating: int = -1, profile: Optional[str] = None
):
    """导出会诊反馈 CSV（默认待改进），带 BOM。"""
    import csv
    import io

    from memory import get_session_store

    if rating not in (-1, 1):
        raise HTTPException(status_code=400, detail="rating 只能是 1 或 -1")

    items = await asyncio.to_thread(
        get_session_store().recent_feedback, 100000, rating, profile
    )
    rows = [["更新时间", "会话编号", "会话标题", "评分", "反馈原因"]]
    for item in items:
        rows.append([
            item.get("updated_at", ""),
            item.get("session_id", ""),
            csv_safe_cell(item.get("session_title", "")),
            "有用" if item.get("rating") == 1 else "待改进",
            csv_safe_cell(item.get("comment", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-feedback-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/insights/quality")
async def quality_insights(
    days: int = 30, profile: Optional[str] = None
):
    """最近一段时间的会诊质量概览（急症、用药提示、反馈与平均用量）。"""
    from memory import get_session_store

    days = max(1, min(int(days or 30), 365))
    return await asyncio.to_thread(
        get_session_store().quality_insights, days, profile
    )


@app.get("/api/metrics")
async def list_metrics(
    profile: Optional[str] = None,
    metric: Optional[str] = None,
    limit: int = 100,
    days: int = 0,
):
    """健康指标记录（默认最近 100 条，可按最近天数筛选）。"""
    from memory import get_session_store

    if metric and metric not in (
        "bp", "weight", "glucose", "temperature", "phq9", "gad7"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")
    limit = max(1, min(int(limit or 100), 500))
    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().list_metrics, profile, metric, limit, days
    )
    return {"metrics": items}


@app.post("/api/metrics")
async def add_metric(request: MetricRequest):
    """新增血压、体重、血糖或体温记录。"""
    from memory import get_session_store

    item = await asyncio.to_thread(
        get_session_store().add_metric,
        request.profile,
        request.metric,
        request.value1,
        request.value2,
        request.measured_at,
        request.note,
    )
    if item is None:
        raise HTTPException(status_code=400, detail="健康指标内容或范围不正确")
    return {"ok": True, "metric": item}


@app.patch("/api/metrics/{metric_id}")
async def update_metric(metric_id: int, request: MetricRequest):
    """更新一条健康指标记录。"""
    from memory import get_session_store

    item = await asyncio.to_thread(
        get_session_store().update_metric,
        metric_id,
        request.profile,
        request.metric,
        request.value1,
        request.value2,
        request.measured_at,
        request.note,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在或内容不正确")
    return {"ok": True, "metric": item}


@app.delete("/api/metrics/{metric_id}")
async def delete_metric(metric_id: int):
    from memory import get_session_store

    ok = await asyncio.to_thread(
        get_session_store().delete_metric, metric_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("metric.delete", str(metric_id))
    return {"ok": True, "id": metric_id}


@app.get("/api/metrics/export.csv")
async def export_metrics_csv(
    profile: Optional[str] = None,
    metric: Optional[str] = None,
):
    """导出健康指标 CSV（带 BOM，Excel 可直接打开）。"""
    import csv
    import io

    from memory import get_session_store

    if metric and metric not in (
        "bp", "weight", "glucose", "temperature", "phq9", "gad7"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")

    items = await asyncio.to_thread(
        get_session_store().list_metrics, profile, metric, 100000
    )
    labels = {
        "bp": "血压",
        "weight": "体重",
        "glucose": "血糖",
        "temperature": "体温",
        "phq9": "PHQ-9 评分",
        "gad7": "GAD-7 评分",
    }

    rows = [["测量时间", "患者档案", "指标", "数值1", "数值2", "单位", "备注"]]
    for item in reversed(items):
        rows.append([
            item.get("measured_at", ""),
            csv_safe_cell(item.get("profile", "")),
            labels.get(item.get("metric"), item.get("metric", "")),
            item.get("value1", ""),
            item.get("value2", "") if item.get("value2") is not None else "",
            item.get("unit", ""),
            csv_safe_cell(item.get("note", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-metrics-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/metrics/import")
async def import_metrics_csv(file: UploadFile = File(...)):
    """从导出的 CSV 格式导入健康指标，重复记录自动跳过。"""
    import csv
    import io

    from memory import get_session_store

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="CSV 文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="CSV 文件超过 5 MB")

    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        try:
            text = raw.decode("gb18030")
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="CSV 编码无法识别")

    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 缺少表头")

    field_alias = {
        "测量时间": "measured_at",
        "时间": "measured_at",
        "患者档案": "profile",
        "档案": "profile",
        "指标": "metric",
        "数值1": "value1",
        "数值2": "value2",
        "单位": "unit",
        "备注": "note",
    }
    metric_alias = {
        "血压": "bp",
        "体重": "weight",
        "血糖": "glucose",
        "体温": "temperature",
        "PHQ-9 评分": "phq9",
        "PHQ-9评分": "phq9",
        "GAD-7 评分": "gad7",
        "GAD-7评分": "gad7",
    }
    allowed = {
        "bp", "weight", "glucose", "temperature", "phq9", "gad7"
    }

    records = []
    invalid = 0
    for row in reader:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            name = key.strip()
            mapped[field_alias.get(name, name)] = (value or "").strip()

        metric = mapped.get("metric", "")
        metric = metric_alias.get(metric, metric)
        if metric not in allowed:
            invalid += 1
            continue

        try:
            value1 = float(mapped.get("value1", ""))
        except (TypeError, ValueError):
            invalid += 1
            continue

        value2 = None
        if mapped.get("value2"):
            try:
                value2 = float(mapped["value2"])
            except (TypeError, ValueError):
                invalid += 1
                continue

        records.append({
            "profile": mapped.get("profile", ""),
            "metric": metric,
            "value1": value1,
            "value2": value2,
            "measured_at": mapped.get("measured_at", ""),
            "note": mapped.get("note", ""),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的健康记录（无效 {invalid} 行）",
        )

    result = await asyncio.to_thread(
        get_session_store().import_data, {"health_metrics": records}
    )
    imported = int(result.get("metrics", 0))
    await record_audit(
        "metric.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": len(records) - imported,
        "invalid": invalid,
    }


@app.get("/api/metric-targets")
async def list_metric_targets(profile: Optional[str] = None):
    """自定义健康指标目标（默认全部档案）。"""
    from memory import get_session_store

    targets = await asyncio.to_thread(
        get_session_store().list_metric_targets, profile
    )
    return {"targets": targets}


@app.put("/api/metric-targets")
async def set_metric_target(request: MetricTargetRequest):
    """设置或覆盖某档案某指标的目标上限。"""
    from memory import get_session_store

    target = await asyncio.to_thread(
        get_session_store().set_metric_target,
        request.profile,
        request.metric,
        request.value1,
        request.value2,
    )
    if target is None:
        raise HTTPException(status_code=400, detail="目标数值无效")
    await record_audit(
        "metric_target.set",
        f"{target['profile'] or '未指定'}·{target['metric']}",
        f"{target['value1']:g}"
        + (f"/{target['value2']:g}" if target["value2"] is not None else ""),
    )
    return {"ok": True, "target": target}


@app.delete("/api/metric-targets/{metric}")
async def delete_metric_target(metric: str, profile: str = ""):
    """删除某档案某指标的自定义目标。"""
    from memory import get_session_store

    ok = await asyncio.to_thread(
        get_session_store().delete_metric_target, profile, metric
    )
    if not ok:
        raise HTTPException(status_code=404, detail="目标不存在")
    await record_audit(
        "metric_target.delete", f"{profile or '未指定'}·{metric}"
    )
    return {"ok": True, "metric": metric}


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
    await record_audit("profile.delete", name)
    return {"ok": True, "name": name}


@app.patch("/api/profiles/{name}")
async def rename_profile(name: str, request: ProfileRenameRequest):
    """重命名患者档案，并同步健康指标中的档案名。"""
    from memory import get_session_store

    try:
        new_name = await asyncio.to_thread(
            get_session_store().rename_profile, name, request.new_name
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if new_name is None:
        raise HTTPException(status_code=404, detail="档案不存在")
    await record_audit("profile.rename", name, new_name)
    return {"ok": True, "old_name": name, "name": new_name}


@app.get("/api/reminders")
async def list_reminders(
    include_done: bool = False, profile: Optional[str] = None
):
    """随访提醒列表，默认只返回未完成的，可按档案筛选。"""
    from memory import get_session_store

    reminders = await asyncio.to_thread(
        get_session_store().list_reminders, include_done, profile
    )
    return {"reminders": reminders}


@app.post("/api/reminders")
async def add_reminder(request: ReminderRequest):
    """新增随访提醒。"""
    from memory import get_session_store

    try:
        reminder = await asyncio.to_thread(
            get_session_store().add_reminder,
            request.title,
            request.due_at,
            request.note,
            request.session_id,
            request.profile,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit(
        "reminder.create", reminder_audit_target(reminder), reminder["due_at"]
    )
    return {"ok": True, "reminder": reminder}


@app.get("/api/reminders/export.csv")
async def export_reminders_csv(
    profile: Optional[str] = None,
    include_done: bool = True,
):
    """导出随访提醒 CSV（带 BOM），可按档案与完成状态筛选。"""
    import csv
    import io

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_reminders, include_done, profile
    )
    rows = [["到期日", "内容", "档案", "状态", "备注"]]
    for item in items:
        rows.append([
            item.get("due_at", ""),
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("profile", "")),
            "已完成" if item.get("done") else "待完成",
            csv_safe_cell(item.get("note", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-reminders-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/api/reminders/export.ics")
async def export_reminders_ics(
    profile: Optional[str] = None,
    include_done: bool = False,
):
    """导出随访提醒为 iCalendar（.ics），可导入手机/桌面日历。"""
    from datetime import timedelta, timezone

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_reminders, include_done, profile
    )

    def escape_ics(value: str) -> str:
        text = str(value or "")
        return (
            text.replace("\\", "\\\\")
            .replace(";", "\\;")
            .replace(",", "\\,")
            .replace("\r\n", "\\n")
            .replace("\n", "\\n")
        )

    def fold_line(line: str) -> str:
        """按 RFC 5545 将超过 75 字节的行折行（不拆分 UTF-8 字符）。"""
        if len(line.encode("utf-8")) <= 75:
            return line
        parts = []
        current = []
        current_len = 0
        for char in line:
            char_len = len(char.encode("utf-8"))
            if current_len + char_len > 75:
                parts.append("".join(current))
                current = [" "]
                current_len = 1
            current.append(char)
            current_len += char_len
        if current:
            parts.append("".join(current))
        return "\r\n".join(parts)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//MediX//Reminders//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]
    for item in items:
        due = str(item.get("due_at") or "")[:10]
        try:
            start = datetime.fromisoformat(due)
        except ValueError:
            continue
        try:
            end = start + timedelta(days=1)
        except OverflowError:
            end = start
        description = item.get("note") or ""
        if item.get("profile"):
            description = f"档案：{item['profile']}。{description}".strip()
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:medix-reminder-{item['id']}@medix.local",
            f"DTSTAMP:{stamp}",
            f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}",
            f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}",
            f"SUMMARY:{escape_ics(item.get('title') or '随访提醒')}",
            f"DESCRIPTION:{escape_ics(description)}",
            "STATUS:COMPLETED" if item.get("done") else "STATUS:CONFIRMED",
            "END:VEVENT",
        ])
    lines.append("END:VCALENDAR")

    content = "\r\n".join(fold_line(line) for line in lines) + "\r\n"
    filename = f"medix-reminders-{datetime.now().strftime('%Y%m%d')}.ics"

    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: int):
    """把随访提醒标记为已完成。"""
    from memory import get_session_store

    store = get_session_store()
    reminder = await asyncio.to_thread(store.get_reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    ok = await asyncio.to_thread(store.complete_reminder, reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit("reminder.complete", reminder_audit_target(reminder))
    return {"ok": True, "id": reminder_id}


@app.post("/api/reminders/{reminder_id}/reopen")
async def reopen_reminder(reminder_id: int):
    """把已完成的随访提醒恢复为待完成。"""
    from memory import get_session_store

    store = get_session_store()
    reminder = await asyncio.to_thread(store.get_reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    ok = await asyncio.to_thread(store.reopen_reminder, reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit("reminder.reopen", reminder_audit_target(reminder))
    return {"ok": True, "id": reminder_id}


@app.patch("/api/reminders/{reminder_id}")
async def update_reminder(reminder_id: int, request: ReminderUpdateRequest):
    """修改随访提醒的内容、日期或备注。"""
    from memory import get_session_store

    if (
        request.title is None
        and request.due_at is None
        and request.note is None
    ):
        raise HTTPException(status_code=400, detail="没有需要修改的字段")
    try:
        reminder = await asyncio.to_thread(
            get_session_store().update_reminder,
            reminder_id,
            request.title,
            request.due_at,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit("reminder.update", reminder_audit_target(reminder))
    return {"ok": True, "reminder": reminder}


@app.post("/api/reminders/{reminder_id}/snooze")
async def snooze_reminder(reminder_id: int, days: int = 7):
    """把随访提醒推迟若干天（默认 7 天）。"""
    from memory import get_session_store

    days = max(1, min(int(days or 7), 365))
    reminder = await asyncio.to_thread(
        get_session_store().snooze_reminder, reminder_id, days
    )
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit(
        "reminder.snooze", reminder_audit_target(reminder), f"days={days}"
    )
    return {"ok": True, "reminder": reminder}


@app.delete("/api/reminders/{reminder_id}")
async def delete_reminder(reminder_id: int):
    """删除随访提醒。"""
    from memory import get_session_store

    store = get_session_store()
    reminder = await asyncio.to_thread(store.get_reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    ok = await asyncio.to_thread(store.delete_reminder, reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit("reminder.delete", reminder_audit_target(reminder))
    return {"ok": True, "id": reminder_id}


@app.post("/api/tools/bmi")
async def tool_bmi(request: BmiRequest):
    """BMI 计算（中国成人分类）。"""
    from core.clinical_tools import calculate_bmi

    return {
        "ok": True,
        **calculate_bmi(request.height_cm, request.weight_kg),
    }


@app.post("/api/tools/egfr")
async def tool_egfr(request: EgfrRequest):
    """CKD-EPI 2021 估算肾小球滤过率。"""
    from core.clinical_tools import calculate_egfr

    return {
        "ok": True,
        **calculate_egfr(
            request.age,
            request.sex,
            request.creatinine,
            unit=request.unit,
        ),
    }


@app.post("/api/tools/chads-vasc")
async def tool_chads_vasc(request: ChadsVascRequest):
    """CHA₂DS₂-VASc 房颤卒中风险评分。"""
    from core.clinical_tools import calculate_chads_vasc

    return {
        "ok": True,
        **calculate_chads_vasc(
            request.age,
            request.sex,
            chf=request.chf,
            hypertension=request.hypertension,
            diabetes=request.diabetes,
            stroke_tia=request.stroke_tia,
            vascular_disease=request.vascular_disease,
        ),
    }


@app.post("/api/tools/curb65")
async def tool_curb65(request: Curb65Request):
    """CURB-65 社区获得性肺炎严重程度评分。"""
    from core.clinical_tools import calculate_curb65

    return {
        "ok": True,
        **calculate_curb65(
            request.age,
            request.confusion,
            request.urea,
            request.respiratory_rate,
            request.systolic_bp,
            request.diastolic_bp,
        ),
    }


@app.post("/api/tools/child-pugh")
async def tool_child_pugh(request: ChildPughRequest):
    """Child-Pugh 肝硬化严重程度评分。"""
    from core.clinical_tools import calculate_child_pugh

    try:
        result = calculate_child_pugh(
            request.bilirubin,
            request.albumin,
            request.inr,
            request.ascites,
            request.encephalopathy,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/phq9")
async def tool_phq9(request: Phq9Request):
    """PHQ-9 抑郁筛查评分；第 9 题阳性时标记 urgent。"""
    from core.clinical_tools import calculate_phq9

    try:
        result = calculate_phq9(request.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/gad7")
async def tool_gad7(request: Gad7Request):
    """GAD-7 焦虑筛查评分。"""
    from core.clinical_tools import calculate_gad7

    try:
        result = calculate_gad7(request.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/has-bled")
async def tool_has_bled(request: HasBledRequest):
    """HAS-BLED 房颤抗凝出血风险评分。"""
    from core.clinical_tools import calculate_has_bled

    return {
        "ok": True,
        **calculate_has_bled(
            request.age,
            hypertension=request.hypertension,
            abnormal_renal=request.abnormal_renal,
            abnormal_liver=request.abnormal_liver,
            stroke_history=request.stroke_history,
            bleeding_history=request.bleeding_history,
            labile_inr=request.labile_inr,
            drugs=request.drugs,
            alcohol=request.alcohol,
        ),
    }


@app.post("/api/tools/gcs")
async def tool_gcs(request: GcsRequest):
    """GCS 格拉斯哥昏迷评分。"""
    from core.clinical_tools import calculate_gcs

    try:
        result = calculate_gcs(
            request.eye, request.verbal, request.motor
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/wells-dvt")
async def tool_wells_dvt(request: WellsDvtRequest):
    """Wells 评分（DVT 概率分层）。"""
    from core.clinical_tools import calculate_wells_dvt

    return {
        "ok": True,
        **calculate_wells_dvt(
            active_cancer=request.active_cancer,
            paralysis_or_immobilization=request.paralysis_or_immobilization,
            bedridden_or_surgery=request.bedridden_or_surgery,
            deep_vein_tenderness=request.deep_vein_tenderness,
            entire_leg_swelling=request.entire_leg_swelling,
            calf_swelling=request.calf_swelling,
            pitting_edema=request.pitting_edema,
            collateral_veins=request.collateral_veins,
            previous_dvt=request.previous_dvt,
            alternative_diagnosis=request.alternative_diagnosis,
        ),
    }


@app.post("/api/tools/wells-pe")
async def tool_wells_pe(request: WellsPeRequest):
    """Wells 评分（PE 概率分层）。"""
    from core.clinical_tools import calculate_wells_pe

    return {
        "ok": True,
        **calculate_wells_pe(
            clinical_signs_dvt=request.clinical_signs_dvt,
            pe_more_likely=request.pe_more_likely,
            heart_rate_gt_100=request.heart_rate_gt_100,
            immobilization_or_surgery=request.immobilization_or_surgery,
            previous_dvt_pe=request.previous_dvt_pe,
            hemoptysis=request.hemoptysis,
            malignancy=request.malignancy,
        ),
    }


@app.post("/api/tools/interactions")
async def tool_interactions(request: InteractionsRequest):
    """药物相互作用检查（确定性规则，仅提示常见明确风险）。"""
    from core.medication_safety import check_medication_safety

    drugs = "、".join(
        part.strip()
        for part in re.split(r"[\n,，、;；]+", request.drugs)
        if part.strip()
    )[:500]
    if not drugs:
        raise HTTPException(status_code=400, detail="请至少填写一种药物")

    result = check_medication_safety(
        "", {"medications": drugs, "allergies": request.allergies}
    )
    return {
        "ok": True,
        "drugs": drugs,
        "level": result.get("level", "none"),
        "alerts": result.get("alerts", []),
    }


@app.post("/api/tools/qsofa")
async def tool_qsofa(request: QsofaRequest):
    """qSOFA 脓毒症风险筛查。"""
    from core.clinical_tools import calculate_qsofa

    return {
        "ok": True,
        **calculate_qsofa(
            request.respiratory_rate,
            request.systolic_bp,
            request.altered_mentation,
        ),
    }


@app.post("/api/tools/fib-4")
async def tool_fib4(request: Fib4Request):
    """FIB-4 肝纤维化指数。"""
    from core.clinical_tools import calculate_fib4

    return {
        "ok": True,
        **calculate_fib4(
            request.age,
            request.ast,
            request.alt,
            request.platelets,
        ),
    }


@app.post("/api/tools/meld")
async def tool_meld(request: MeldRequest):
    """经典 MELD 终末期肝病评分。"""
    from core.clinical_tools import calculate_meld

    return {
        "ok": True,
        **calculate_meld(
            request.bilirubin,
            request.inr,
            request.creatinine,
            request.dialysis,
        ),
    }


@app.post("/api/tools/stop-bang")
async def tool_stop_bang(request: StopBangRequest):
    """STOP-BANG 阻塞性睡眠呼吸暂停筛查。"""
    from core.clinical_tools import calculate_stop_bang

    return {
        "ok": True,
        **calculate_stop_bang(
            request.snoring,
            request.tired,
            request.observed_apnea,
            request.hypertension,
            request.bmi_gt_35,
            request.age_gt_50,
            request.neck_gt_40cm,
            request.male,
        ),
    }


@app.post("/api/tools/padua")
async def tool_padua(request: PaduaRequest):
    """Padua 内科住院患者静脉血栓风险评分。"""
    from core.clinical_tools import calculate_padua

    return {
        "ok": True,
        **calculate_padua(
            request.active_cancer,
            request.previous_vte,
            request.reduced_mobility,
            request.thrombophilia,
            request.recent_trauma_surgery,
            request.age_ge_70,
            request.heart_respiratory_failure,
            request.acute_mi_stroke,
            request.acute_infection_rheumatologic,
            request.bmi_ge_30,
            request.hormonal_treatment,
        ),
    }


@app.post("/api/tools/abcd2")
async def tool_abcd2(request: Abcd2Request):
    """ABCD2 TIA 后短期卒中风险评分。"""
    from core.clinical_tools import calculate_abcd2

    return {
        "ok": True,
        **calculate_abcd2(
            request.age_ge_60,
            request.bp_ge_140_90,
            request.unilateral_weakness,
            request.speech_disturbance,
            request.duration_ge_60,
            request.duration_10_59,
            request.diabetes,
        ),
    }


@app.post("/api/tools/morse-fall")
async def tool_morse_fall(request: MorseFallRequest):
    """Morse 跌倒风险评分。"""
    from core.clinical_tools import calculate_morse_fall

    return {
        "ok": True,
        **calculate_morse_fall(
            request.history_of_falling,
            request.secondary_diagnosis,
            request.ambulatory_aid,
            request.intravenous,
            request.gait,
            request.mental_status,
        ),
    }


@app.get("/api/sessions/{session_id}/messages")
async def get_session_messages(session_id: str, limit: int = 50):
    from memory import ShortTermMemory, get_session_store

    store = get_session_store()
    if limit <= 0:
        stored = await asyncio.to_thread(store.get_all_messages, session_id)
    else:
        stored = await asyncio.to_thread(store.get_messages, session_id, limit)
    if stored:
        session = await asyncio.to_thread(store.get_session, session_id)
        return {
            "session_id": session_id,
            "profile": (session or {}).get("profile", ""),
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
        "profile": "",
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


@app.get("/api/tags")
async def list_tags(profile: Optional[str] = None):
    """未归档会话的标签及数量，供历史筛选；可按档案筛选。"""
    from memory import get_session_store

    tags = await asyncio.to_thread(
        get_session_store().list_tags, False, profile
    )
    return {"tags": tags}


@app.get("/api/session-profiles")
async def list_session_profiles():
    """会话中出现过的档案名及数量（含已删除档案留下的历史名字）。"""
    from memory import get_session_store

    profiles = await asyncio.to_thread(
        get_session_store().list_session_profiles
    )
    return {"profiles": profiles}


@app.get("/api/profile-names")
async def list_profile_names():
    """所有数据表中出现过的档案名（含已删除档案留下的历史名字）。"""
    from memory import get_session_store

    names = await asyncio.to_thread(
        get_session_store().list_used_profile_names
    )
    return {"profiles": names}


@app.patch("/api/sessions/{session_id}/tags")
async def set_session_tags(session_id: str, request: TagsRequest):
    """覆盖会话标签（最多 5 个，每个最长 20 字）。"""
    from memory import get_session_store

    tags = await asyncio.to_thread(
        get_session_store().set_tags, session_id, request.tags
    )
    if tags is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    await record_audit("session.tags", session_id, "、".join(tags))
    return {"ok": True, "session_id": session_id, "tags": tags}


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


@app.patch("/api/bulk/sessions/archive")
async def bulk_archive_sessions(request: BulkArchiveRequest):
    """批量归档或移出归档，返回成功与缺失数量。"""
    from memory import get_session_store

    store = get_session_store()
    updated = 0
    missing = []
    for session_id in dict.fromkeys(request.ids):
        ok = await asyncio.to_thread(
            store.set_archived, session_id, request.archived
        )
        if ok:
            updated += 1
        else:
            missing.append(session_id)

    await record_audit(
        "session.bulk_archive",
        detail=(
            f"archived={int(request.archived)} "
            f"updated={updated} missing={len(missing)}"
        ),
    )
    return {
        "ok": True,
        "updated": updated,
        "missing": missing,
        "archived": request.archived,
    }


@app.post("/api/bulk/sessions/delete")
async def bulk_delete_sessions(request: BulkSessionsRequest):
    """批量删除会诊；返回删除与缺失数量。"""
    from memory import ShortTermMemory, get_session_store

    store = get_session_store()
    memory = ShortTermMemory()
    deleted = 0
    missing = []
    for session_id in dict.fromkeys(request.ids):
        memory.clear_session(session_id)
        ok = await asyncio.to_thread(store.delete_session, session_id)
        if ok:
            deleted += 1
        else:
            missing.append(session_id)

    await record_audit(
        "session.bulk_delete",
        detail=f"deleted={deleted} missing={len(missing)}",
    )
    return {"ok": True, "deleted": deleted, "missing": missing}


@app.patch("/api/bulk/sessions/tags")
async def bulk_tag_sessions(request: BulkTagsRequest):
    """批量覆盖会话标签。"""
    from memory import get_session_store

    store = get_session_store()
    updated = 0
    missing = []
    normalized = []
    for session_id in dict.fromkeys(request.ids):
        tags = await asyncio.to_thread(
            store.set_tags, session_id, request.tags
        )
        if tags is None:
            missing.append(session_id)
        else:
            updated += 1
            normalized = tags

    await record_audit(
        "session.bulk_tags",
        detail=(
            f"updated={updated} missing={len(missing)} "
            f"tags={'、'.join(normalized)}"
        ),
    )
    return {
        "ok": True,
        "updated": updated,
        "missing": missing,
        "tags": normalized,
    }


@app.patch("/api/sessions/{session_id}/archive")
async def archive_session(session_id: str, request: ArchiveRequest):
    """归档或移出归档；归档不会删除会话与用量记录。"""
    from memory import get_session_store

    ok = await asyncio.to_thread(
        get_session_store().set_archived, session_id, request.archived
    )
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    await record_audit(
        "session.archive" if request.archived else "session.unarchive",
        session_id,
    )
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
    deleted = await asyncio.to_thread(
        get_session_store().delete_session, session_id
    )
    if deleted:
        await record_audit("session.delete", session_id)
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


@app.post("/api/knowledge/upload-batch")
async def knowledge_upload_batch(
    files: List[UploadFile] = File(...),
    rebuild: bool = True
):
    """一次上传多个 TXT / Markdown / PDF 文档，全部保存后只重建一次索引。"""
    from knowledge.service import get_knowledge_service

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="一次最多上传 10 个文件")

    service = get_knowledge_service()
    saved: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []

    for file in files:
        try:
            content = await file.read()
            document = await asyncio.to_thread(
                service.save_upload, file.filename, content
            )
            saved.append(document)
        except ValueError as e:
            failed.append({
                "filename": file.filename or "未命名文件",
                "error": str(e),
            })
        except Exception as e:
            logger.exception("批量保存知识库文档失败")
            failed.append({
                "filename": file.filename or "未命名文件",
                "error": f"保存失败：{e}",
            })

    if not saved:
        detail = "所有文件均未上传成功"
        if failed:
            detail += "：" + "；".join(
                f"{item['filename']}（{item['error']}）" for item in failed
            )
        raise HTTPException(status_code=400, detail=detail)

    result = None
    if rebuild:
        async with state.lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("批量上传后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail=f"文档已保存，但重建索引失败：{e}",
                )

    return {
        "ok": True,
        "documents": saved,
        "failed": failed,
        "rebuild": result,
    }


@app.get("/api/knowledge/search")
async def knowledge_search(
    q: str = "",
    top_k: int = 5,
    filter_type: Optional[str] = None,
):
    """知识库检索测试：返回来源、相关度与原文片段，可按文档类型过滤。"""
    from knowledge.service import get_knowledge_service

    allowed_types = {
        "lifestyle",
        "disease_classification",
        "clinical_guideline",
        "general",
    }
    if filter_type and filter_type not in allowed_types:
        raise HTTPException(status_code=400, detail="不支持的文档类型")

    service = get_knowledge_service()
    try:
        result = await asyncio.to_thread(
            service.search, q, top_k, filter_type
        )
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

    await _link_patient_profile(session_id, request.patient_profile)

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

        await _link_patient_profile(session_id, request.patient_profile)

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


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    sw_file = WEB_DIR / "sw.js"
    if not sw_file.exists():
        raise HTTPException(status_code=404, detail="Service Worker 不存在")
    return FileResponse(
        sw_file,
        media_type="application/javascript",
        headers={
            "Cache-Control": "no-cache",
            "Service-Worker-Allowed": "/",
        },
    )


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
