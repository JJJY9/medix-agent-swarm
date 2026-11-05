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
import os
import re
import sys
import hashlib
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Literal, Optional

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exception_handlers import http_exception_handler
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field, field_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Windows 上 stdout 被重定向时默认用 GBK，日志里的 emoji 会抛 UnicodeEncodeError
from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

from core.security import install_log_redaction, redact_secrets, sanitize_base_url
install_log_redaction()

from core.auth import get_access_token, is_authorized, request_token
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

    @field_validator("session_id")
    @classmethod
    def _validate_session_id(cls, value):
        if value is None:
            return value
        text = str(value).strip()
        if (
            len(text) > 120
            or "/" in text
            or "\\" in text
            or ".." in text
            or any(ord(ch) < 32 for ch in text)
        ):
            raise ValueError("会话 ID 不合法")
        return text


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
    symptoms: Optional[List[Dict[str, Any]]] = None
    medications: Optional[List[Dict[str, Any]]] = None
    medication_logs: Optional[List[Dict[str, Any]]] = None
    audit_log: Optional[List[Dict[str, Any]]] = None


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
    advance_days: int = Field(
        0, ge=0, le=30, description="提前提醒天数，0 表示仅到期当天提醒"
    )
    repeat_days: int = Field(
        0, ge=0, le=365, description="重复间隔天数，0 表示不重复"
    )
    repeat_until: str = Field("", max_length=32, description="重复截止日期")
    medication_id: int = Field(
        0, ge=0, description="关联的用药记录 ID（用药提醒）"
    )


class ReminderUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=120)
    due_at: Optional[str] = Field(None, max_length=32)
    note: Optional[str] = Field(None, max_length=300)
    advance_days: Optional[int] = Field(None, ge=0, le=30)
    profile: Optional[str] = Field(None, max_length=40)
    repeat_days: Optional[int] = Field(None, ge=0, le=365)
    repeat_until: Optional[str] = Field(None, max_length=32)


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


class AuditCRequest(BaseModel):
    q1: Literal[0, 1, 2, 3, 4]
    q2: Literal[0, 1, 2, 3, 4]
    q3: Literal[0, 1, 2, 3, 4]
    sex: Literal["", "male", "female"] = ""


class MmrcRequest(BaseModel):
    grade: Literal[0, 1, 2, 3, 4]


class CatRequest(BaseModel):
    answers: List[int] = Field(..., min_length=8, max_length=8)


class EssRequest(BaseModel):
    answers: List[int] = Field(..., min_length=8, max_length=8)


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
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
    ]
    value1: float
    value2: Optional[float] = None
    measured_at: str = Field("", max_length=40, description="测量时间（ISO）")
    note: str = Field("", max_length=300)


class MetricUpdateRequest(BaseModel):
    """部分更新健康指标：只修改请求里显式给出的字段。"""

    profile: Optional[str] = Field(None, max_length=40)
    metric: Optional[Literal[
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
    ]] = None
    value1: Optional[float] = None
    value2: Optional[float] = None
    measured_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)






class MedicationRequest(BaseModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    name: str = Field(..., min_length=1, max_length=120)
    dose: str = Field("", max_length=60, description="剂量，如 5 mg")
    frequency: str = Field("", max_length=60, description="频次，如 每日一次")
    note: str = Field("", max_length=300)
    active: bool = True


class MedicationLogRequest(BaseModel):
    taken_at: str = Field("", max_length=40, description="服用时间（ISO），默认当前时间")
    note: str = Field("", max_length=300)


class MedicationUpdateRequest(BaseModel):
    """部分更新用药记录：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    dose: Optional[str] = Field(None, max_length=60)
    frequency: Optional[str] = Field(None, max_length=60)
    note: Optional[str] = Field(None, max_length=300)
    active: Optional[bool] = None


class SymptomRequest(BaseModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    symptom: str = Field(..., min_length=1, max_length=120)
    severity: int = Field(..., ge=0, le=10, description="严重程度 0–10")
    recorded_at: str = Field("", max_length=40, description="记录时间（ISO）")
    note: str = Field("", max_length=300)


class SymptomUpdateRequest(BaseModel):
    """部分更新症状记录：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    symptom: Optional[str] = Field(None, min_length=1, max_length=120)
    severity: Optional[int] = Field(None, ge=0, le=10)
    recorded_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)
class MetricTargetRequest(BaseModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    metric: Literal[
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
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
        # 知识库读写/重建单独一把锁：重建可能耗时数十秒，
        # 不应阻塞正在进行或排队的会诊
        self.kb_lock = asyncio.Lock()
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


_RISK_TAG_PATTERN = re.compile(
    r"(?:综合)?风险(?:等级|程度|评级)?\s*[：:为是]\s*[\*「【\s]*"
    r"(中等风险|中等|中风险|紧急|高危|中危|低危)"
)
_RISK_KEYWORD_PATTERNS = (
    (
        "high",
        re.compile(r"高风险(?!人群|因素)|高度风险(?!人群|因素)|高危(?!人群|因素)"),
    ),
    (
        "medium",
        re.compile(r"中风险(?!人群|因素)|中等风险(?!人群|因素)|中危(?!人群|因素)"),
    ),
    (
        "low",
        re.compile(r"低风险(?!人群|因素)|风险较低|风险不高|低危(?!人群|因素)"),
    ),
)
_RISK_NEGATION_PATTERN = re.compile(
    r"(?:不属于|不是|并非|排除|未见|无明显|没有|无|非|不)\s*$"
)
_RISK_TAG_LEVELS = {
    "紧急": "emergency",
    "高危": "high",
    "中危": "medium",
    "中等风险": "medium",
    "中等": "medium",
    "中风险": "medium",
    "低危": "low",
}
RISK_LEVEL_LABELS = {
    "emergency": "紧急",
    "high": "高危",
    "medium": "中危",
    "low": "低危",
}

#: 风险从低到高的排序，用于「中危及以上」等筛选
RISK_LEVEL_ORDER = {"": 0, "low": 1, "medium": 2, "high": 3, "emergency": 4}
RISK_FILTER_VALUES = {"medium", "high", "emergency"}


def _risk_at_least(level: str, threshold: str) -> bool:
    return RISK_LEVEL_ORDER.get(level, 0) >= RISK_LEVEL_ORDER[threshold]


def parse_risk_level(text: Any) -> str:
    """从会诊意见中提取风险等级；无法判断时返回空字符串。"""
    content = str(text or "")
    if not content:
        return ""
    tagged = _RISK_TAG_PATTERN.search(content)
    if tagged:
        return _RISK_TAG_LEVELS.get(tagged.group(1), "")
    for level, pattern in _RISK_KEYWORD_PATTERNS:
        for match in pattern.finditer(content):
            prefix = content[max(0, match.start() - 6):match.start()]
            if _RISK_NEGATION_PATTERN.search(prefix):
                continue
            return level
    return ""


def _normalized_profile_key(value: Any) -> str:
    """与写入侧一致地规范化档案名，避免首尾/连续空格导致过滤丢数据。"""
    from memory.session_store import _normalize_profile_name

    return _normalize_profile_name(value)


def _streak_info(days: List[str]) -> Dict[str, Any]:
    """按有记录的日期列表计算连续记录信息。"""
    from datetime import date

    today = datetime.now().date()
    today_key = today.isoformat()
    day_set = {
        str(day) for day in days
        if day and str(day) <= today_key
    }
    recorded_today = today_key in day_set
    cursor = today if recorded_today else today - timedelta(days=1)
    current = 0
    while cursor.isoformat() in day_set:
        current += 1
        cursor -= timedelta(days=1)
    best = 0
    run = 0
    prev = None
    for day in sorted(day_set):
        try:
            parsed = date.fromisoformat(day)
        except ValueError:
            continue
        if prev is not None and (parsed - prev).days == 1:
            run += 1
        else:
            run = 1
        best = max(best, run)
        prev = parsed
    cutoff = (today - timedelta(days=29)).isoformat()
    days_last_30 = sum(1 for day in day_set if day >= cutoff)
    return {
        "current": current,
        "best": best,
        "recorded_today": recorded_today,
        "days_last_30": days_last_30,
    }


MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_UPLOAD_BATCH_BYTES = MAX_UPLOAD_BYTES * 10


def _request_content_length(request: Request) -> int:
    try:
        return max(0, int(request.headers.get("content-length") or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _public_base_url(value: Any) -> Any:
    """去掉服务地址中可能携带的账号密码与查询参数后再对外展示。"""
    text = str(value or "")
    if not text:
        return value
    return sanitize_base_url(text)


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


_AUTH_ENABLED = bool((os.getenv("MEDIX_ACCESS_TOKEN") or "").strip())

app = FastAPI(
    title="MediX 多智能体医疗助手",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if _AUTH_ENABLED else "/docs",
    redoc_url=None if _AUTH_ENABLED else "/redoc",
    openapi_url=None if _AUTH_ENABLED else "/openapi.json",
)

# 前端加载和健康检查必须保持公开，其余 /api/* 在设置令牌后统一校验
PUBLIC_API_PATHS = {"/api/status"}
DOC_PATHS = {"/docs", "/redoc", "/openapi.json", "/docs/oauth2-redirect"}
CHAT_PATHS = {"/api/chat", "/api/chat/stream"}
DIAGNOSE_PATHS = {"/api/diagnose/llm"}
chat_limiter = SlidingWindowLimiter(get_rate_limit())
# 自检端点会真实调用一次付费模型，单独限制频率
diagnose_limiter = SlidingWindowLimiter(5)

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


class _BodyTooLarge(Exception):
    """请求体超过路径允许的上限。"""


def _scope_route_path(scope: Dict[str, Any]) -> str:
    """去掉 root_path 后的路由路径（子路径部署时鉴权与限流仍能匹配）。"""
    path = str(scope.get("path") or "")
    root = str(scope.get("root_path") or "")
    if root and path.startswith(root):
        path = path[len(root):]
    return path or "/"


def _body_limit_for_path(path: str) -> int:
    if path == "/api/sessions/import":
        # 整库备份可能很大，保留较宽上限但不再无限
        return 100 * 1024 * 1024
    if path == "/api/knowledge/upload-batch":
        return MAX_UPLOAD_BATCH_BYTES + 1024 * 1024
    if path.startswith("/api/knowledge/upload"):
        return MAX_UPLOAD_BYTES + 1024 * 1024
    if path.endswith("/import"):
        return MAX_UPLOAD_BYTES + 1024 * 1024
    return 2 * 1024 * 1024


class BodySizeLimitMiddleware:
    """限制请求体大小：先看 Content-Length，再按实际接收字节兜底。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            return await self.app(scope, receive, send)

        limit = _body_limit_for_path(_scope_route_path(scope))
        headers = {
            key.decode("latin-1").lower(): value
            for key, value in (scope.get("headers") or [])
        }
        raw_length = headers.get("content-length")
        if raw_length:
            try:
                declared = int(raw_length)
            except (TypeError, ValueError):
                declared = 0
            if declared > limit:
                response = JSONResponse(
                    status_code=413, content={"detail": "请求体过大"}
                )
                return await response(scope, receive, send)

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _BodyTooLarge()
            return message

        try:
            return await self.app(scope, limited_receive, send)
        except _BodyTooLarge:
            response = JSONResponse(
                status_code=413, content={"detail": "请求体过大"}
            )
            return await response(scope, receive, send)


app.add_middleware(BodySizeLimitMiddleware)


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


def _rate_limit_key(request: Request) -> str:
    """优先按访问令牌限流；匿名请求退回源 IP。"""
    token = request_token(request) if get_access_token() else None
    if token:
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
        return f"token:{digest}"
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


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
    # 子路径部署时 request.url.path 会带 root_path 前缀，
    # 直接匹配 /api/ 会整体跳过鉴权与限流
    path = _scope_route_path(request.scope)
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
        client_key = _rate_limit_key(request)
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

    if path in DIAGNOSE_PATHS:
        client_key = _rate_limit_key(request)
        allowed, retry_after = diagnose_limiter.check(client_key)
        if not allowed:
            return add_security_headers(JSONResponse(
                status_code=429,
                content={
                    "detail": "自检请求过于频繁，请稍后再试。",
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
        # 测试或自定义协调器可能没有长期记忆字段，逐级 getattr
        long_term = getattr(state.coordinator, "long_term_memory", None)
        mem0_enabled = bool(getattr(long_term, "enabled", False))

    return {
        "ok": state.startup_error is None,
        "startup_error": state.startup_error,
        "auth_required": bool(get_access_token()),
        "llm_configured": bool(api_key),
        "pricing": get_pricing(),
        "monthly_token_budget": get_token_budget(),
        "auto_archive_days": get_auto_archive_days(),
        "model_name": LLM_CONFIG.get("model_name"),
        "base_url": _public_base_url(LLM_CONFIG.get("base_url")),
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
    profile: Optional[str] = None,
    risk: Optional[str] = None,
):
    """历史会诊列表（按最近更新时间倒序，支持搜索、分页、收藏、时间范围、归档、标签、档案与风险筛选）。

    archived=false（默认）只返回未归档会话；archived=true 只返回已归档会话。
    summary 汇总所有匹配会话的 Token 用量、模型调用次数与反馈，
    不受当前分页影响；每条会话附带自身用量。
    risk 取 medium/high/emergency 时按「该等级及以上」筛选。
    """
    from memory import get_session_store

    limit = max(1, min(int(limit or 50), 200))
    offset = max(0, int(offset or 0))
    days = max(0, min(int(days or 0), 365))
    query = q or ""
    favorites_only = bool(favorite)
    profile = profile or None
    risk = str(risk or "").strip().lower()
    if risk and risk not in RISK_FILTER_VALUES:
        raise HTTPException(status_code=400, detail="不支持的风险筛选")

    store = get_session_store()
    if risk:
        matched: List[Dict[str, Any]] = []
        matched_stats: Dict[str, Dict[str, Any]] = {}
        total_turns = 0
        total_calls = 0
        totals_usage: Dict[str, int] = {}
        scan_offset = 0
        # 逐页扫描到结束：固定 2000 条上限会让后面的高危会话被静默忽略
        while True:
            batch = await asyncio.to_thread(
                store.list_sessions,
                200,
                query,
                scan_offset,
                favorites_only,
                days,
                archived,
                tag,
                profile,
            )
            if not batch:
                break
            batch_matched = []
            for item in batch:
                level = parse_risk_level(item.pop("risk_text", ""))
                if _risk_at_least(level, risk):
                    item["risk"] = level
                    batch_matched.append(item)
            if batch_matched:
                stats_batch = await asyncio.to_thread(
                    store.usage_by_session,
                    [item["id"] for item in batch_matched],
                )
                matched_stats.update(stats_batch)
                for stat in stats_batch.values():
                    for key, value in (stat.get("usage") or {}).items():
                        totals_usage[key] = (
                            totals_usage.get(key, 0) + int(value or 0)
                        )
                    total_turns += int(stat.get("turns") or 0)
                    total_calls += int(stat.get("llm_calls") or 0)
                matched.extend(batch_matched)
            if len(batch) < 200:
                break
            scan_offset += len(batch)

        total = len(matched)
        sessions = matched[offset:offset + limit]
        for item in sessions:
            stat = matched_stats.get(item["id"])
            if not stat:
                continue
            item["usage"] = stat["usage"]
            item["llm_calls"] = stat["llm_calls"]
            item["turns"] = stat["turns"]
        summary: Dict[str, Any] = {
            "sessions": total,
            "loaded": len(sessions),
            "offset": offset,
            "limit": limit,
            "turns": total_turns,
            "llm_calls": total_calls,
            "usage": totals_usage,
            "feedback": None,
        }
        return {"sessions": sessions, "summary": summary}
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
        item["risk"] = parse_risk_level(item.pop("risk_text", ""))
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
    risk: Optional[str] = None,
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
    risk_filter = str(risk or "").strip().lower()
    if risk_filter and risk_filter not in RISK_FILTER_VALUES:
        raise HTTPException(status_code=400, detail="不支持的风险筛选")

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

    if risk_filter:
        filtered = []
        for item in items:
            level = parse_risk_level(item.pop("risk_text", ""))
            if _risk_at_least(level, risk_filter):
                item["risk"] = level
                filtered.append(item)
        items = filtered

    usage = await asyncio.to_thread(
        store.usage_by_session, [item["id"] for item in items]
    )
    rows = [[
        "会话编号", "标题", "档案", "标签", "收藏", "归档",
        "消息数", "轮次", "Token", "模型调用", "创建时间", "更新时间",
        "风险",
    ]]
    for item in items:
        stats = usage.get(item["id"], {})
        if "risk" in item:
            risk_level = item["risk"]
        else:
            risk_level = parse_risk_level(item.pop("risk_text", ""))
        rows.append([
            csv_safe_cell(item.get("id", "")),
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell("、".join(item.get("tags") or [])),
            "是" if item.get("favorite") else "否",
            "是" if item.get("archived") else "否",
            item.get("message_count", 0),
            stats.get("turns", 0),
            (stats.get("usage") or {}).get("total_tokens", 0),
            stats.get("llm_calls", 0),
            csv_safe_cell(item.get("created_at", "")),
            csv_safe_cell(item.get("updated_at", "")),
            csv_safe_cell(RISK_LEVEL_LABELS.get(risk_level, "")),
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
        "symptoms": request.symptoms,
        "medications": request.medications,
        "medication_logs": request.medication_logs,
        "audit_log": request.audit_log,
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
            f"reminders={result.get('reminders', 0)} "
            f"symptoms={result.get('symptoms', 0)} "
            f"medications={result.get('medications', 0)} "
            f"medication_logs={result.get('medication_logs', 0)} "
            f"audit_log={result.get('audit_log', 0)}"
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
            csv_safe_cell(item.get("updated_at", "")),
            csv_safe_cell(item.get("session_id", "")),
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
    offset: int = 0,
    q: str = "",
):
    """健康指标记录（默认最近 100 条，可按最近天数与关键字筛选）。"""
    from memory import get_session_store

    if metric and metric not in (
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")
    limit = max(1, min(int(limit or 100), 500))
    days = max(0, min(int(days or 0), 3650))
    offset = max(0, int(offset or 0))
    items = await asyncio.to_thread(
        get_session_store().list_metrics,
        profile, metric, limit, days, offset, q,
    )
    return {"metrics": items}


@app.get("/api/metrics/summary")
async def metric_summary(
    profile: Optional[str] = None,
    metric: str = "",
    days: int = 0,
):
    """所选范围内的指标统计（平均/最高/最低，以及与上一周期的差值）。"""
    from memory import get_session_store

    if metric not in (
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")
    days = max(0, min(int(days or 0), 3650))
    data = await asyncio.to_thread(
        get_session_store().metric_summary, profile, metric, days
    )
    return data


@app.get("/api/medications")
async def list_medications(
    profile: Optional[str] = None,
    active_only: bool = False,
    limit: int = 1000,
    offset: int = 0,
    days: int = 7,
):
    """用药清单（可按档案、在用状态与最近天数筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    offset = max(0, int(offset or 0))
    days = max(1, min(int(days or 7), 365))
    items = await asyncio.to_thread(
        get_session_store().list_medications,
        profile, active_only, limit, offset, days,
    )
    return {"medications": items}


@app.get("/api/medications/export.csv")
async def export_medications_csv(
    profile: Optional[str] = None,
    active_only: bool = False,
    status: Optional[str] = None,
    q: Optional[str] = None,
):
    """导出用药清单 CSV（带 BOM），支持档案、在用状态与关键字筛选。"""
    import csv
    import io

    from memory import get_session_store
    from memory.session_store import adherence_rate

    items = await asyncio.to_thread(
        get_session_store().list_medications,
        profile, active_only, 100000,
    )
    status = str(status or "").strip().lower()
    if status == "active":
        items = [item for item in items if item.get("active")]
    elif status == "inactive":
        items = [item for item in items if not item.get("active")]
    query = str(q or "").strip().lower()
    if query:
        items = [
            item for item in items
            if query in " ".join(
                str(item.get(key) or "")
                for key in ("name", "dose", "frequency", "note", "profile")
            ).lower()
        ]
    rows = [[
        "药物名称", "剂量", "频次", "患者档案", "状态", "备注",
        "今日次数", "近7天次数", "最近服用", "近7天应服", "依从率",
    ]]
    for item in items:
        expected = int(item.get("expected_week_doses") or 0)
        taken = int(item.get("week_count") or 0)
        rows.append([
            csv_safe_cell(item.get("name", "")),
            csv_safe_cell(item.get("dose", "")),
            csv_safe_cell(item.get("frequency", "")),
            csv_safe_cell(item.get("profile", "")),
            "在用" if item.get("active") else "停用",
            csv_safe_cell(item.get("note", "")),
            str(int(item.get("today_count") or 0)),
            str(taken),
            csv_safe_cell(item.get("last_taken_at") or ""),
            str(expected) if expected else "",
            str(adherence_rate(taken, expected))
            if expected else "",
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    filename = f"medix-medications-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/medications/logs/export.csv")
async def export_medication_logs_csv(
    profile: Optional[str] = None,
    days: int = 0,
):
    """导出用药打卡记录 CSV（带 BOM），可按档案与最近天数筛选。"""
    import csv
    import io

    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    logs = await asyncio.to_thread(
        get_session_store().list_medication_logs,
        None,
        profile,
        100000,
        0,
        days,
    )
    rows = [["药物名称", "剂量", "服用时间", "患者档案", "备注"]]
    for item in logs:
        rows.append([
            csv_safe_cell(item.get("medication_name", "")),
            csv_safe_cell(item.get("medication_dose", "")),
            csv_safe_cell(item.get("taken_at", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("note", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    filename = (
        f"medix-medication-logs-{datetime.now().strftime('%Y%m%d')}.csv"
    )

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/medications/import")
async def import_medications_csv(request: Request, file: UploadFile = File(...)):
    """从导出的 CSV 格式导入用药清单，重复记录自动跳过。"""
    import csv
    import io

    from memory import get_session_store

    if _request_content_length(request) > 5 * 1024 * 1024 + 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="CSV 文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")

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
        "药物名称": "name",
        "名称": "name",
        "药名": "name",
        "剂量": "dose",
        "频次": "frequency",
        "频率": "frequency",
        "患者档案": "profile",
        "档案": "profile",
        "状态": "active",
        "备注": "note",
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

        name = mapped.get("name", "")
        if not name:
            invalid += 1
            continue
        active = mapped.get("active", "")
        records.append({
            "profile": mapped.get("profile", ""),
            "name": name,
            "dose": mapped.get("dose", ""),
            "frequency": mapped.get("frequency", ""),
            "note": mapped.get("note", ""),
            "active": active not in ("停用", "0", "false", "False", "否"),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的用药记录（无效 {invalid} 行）",
        )

    result = await asyncio.to_thread(
        get_session_store().import_data, {"medications": records}
    )
    imported = int(result.get("medications", 0))
    await record_audit(
        "medication.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": len(records) - imported,
        "invalid": invalid,
    }


@app.post("/api/medications")
async def add_medication(request: MedicationRequest):
    """新增一条用药记录。"""
    from memory import get_session_store

    item = await asyncio.to_thread(
        get_session_store().add_medication,
        request.profile,
        request.name,
        request.dose,
        request.frequency,
        request.note,
        request.active,
    )
    if item is None:
        raise HTTPException(status_code=400, detail="药物名称不能为空")
    await record_audit(
        "medication.create", f"{item['id']}·{item['name']}"
    )
    return {"ok": True, "medication": item}


@app.patch("/api/medications/{medication_id}")
async def update_medication(
    medication_id: int, request: MedicationUpdateRequest
):
    """部分更新一条用药记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_medication, medication_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    merged = {**existing, **request.model_dump(exclude_unset=True)}
    item = await asyncio.to_thread(
        store.update_medication,
        medication_id,
        merged.get("profile") or "",
        merged.get("name") or "",
        merged.get("dose") or "",
        merged.get("frequency") or "",
        merged.get("note") or "",
        bool(merged.get("active", True)),
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在或内容不正确")
    await record_audit(
        "medication.update", f"{item['id']}·{item['name']}"
    )
    return {"ok": True, "medication": item}


@app.delete("/api/medications/{medication_id}")
async def delete_medication(medication_id: int):
    """删除一条用药记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_medication, medication_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_medication, medication_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit(
        "medication.delete", f"{item['id']}·{item['name']}"
    )
    return {"ok": True, "id": medication_id}


@app.get("/api/medications/{medication_id}/logs")
async def list_medication_logs(
    medication_id: int,
    limit: int = 100,
    offset: int = 0,
    days: int = 0,
):
    """某个药物的到药记录（按服用时间倒序）。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_medication, medication_id)
    if item is None:
        raise HTTPException(status_code=404, detail="药物不存在")
    limit = max(1, min(int(limit or 100), 1000))
    offset = max(0, int(offset or 0))
    days = max(0, min(int(days or 0), 3650))
    logs = await asyncio.to_thread(
        store.list_medication_logs,
        medication_id,
        None,
        limit,
        offset,
        days,
    )
    return {"logs": logs}


@app.post("/api/medications/{medication_id}/logs")
async def add_medication_log(
    medication_id: int,
    request: MedicationLogRequest,
):
    """记录一次用药（打卡）。"""
    from memory import get_session_store

    log = await asyncio.to_thread(
        get_session_store().add_medication_log,
        medication_id,
        request.taken_at,
        request.note,
    )
    if log is None:
        raise HTTPException(status_code=404, detail="药物不存在或时间不正确")
    await record_audit(
        "medication.log",
        f"{log['medication_id']}·{log['taken_at']}",
    )
    return {"ok": True, "log": log}


@app.get("/api/medication-logs")
async def list_all_medication_logs(
    profile: Optional[str] = None,
    days: int = 0,
    limit: int = 100,
    offset: int = 0,
):
    """用药打卡记录（跨药物，按服用时间倒序）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 100), 1000))
    offset = max(0, int(offset or 0))
    days = max(0, min(int(days or 0), 3650))
    logs = await asyncio.to_thread(
        get_session_store().list_medication_logs,
        None,
        profile,
        limit,
        offset,
        days,
    )
    return {"logs": logs}


@app.delete("/api/medication-logs/{log_id}")
async def delete_medication_log(log_id: int):
    """撤销一条用药记录。"""
    from memory import get_session_store

    store = get_session_store()
    log = await asyncio.to_thread(store.get_medication_log, log_id)
    if log is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_medication_log, log_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit(
        "medication.log_delete",
        f"{log['medication_id']}·{log['taken_at']}",
    )
    return {"ok": True, "id": log_id}


@app.get("/api/symptoms")
async def list_symptoms(
    profile: Optional[str] = None,
    limit: int = 100,
    days: int = 0,
    offset: int = 0,
):
    """症状记录（按记录时间倒序，可按档案与最近天数筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 100), 100000))
    days = max(0, min(int(days or 0), 3650))
    offset = max(0, int(offset or 0))
    items = await asyncio.to_thread(
        get_session_store().list_symptoms, profile, limit, days, offset
    )
    return {"symptoms": items}


@app.get("/api/symptoms/export.csv")
async def export_symptoms_csv(
    profile: Optional[str] = None,
    days: int = 0,
):
    """导出症状日记 CSV（带 BOM），可按档案与最近天数筛选。"""
    import csv
    import io

    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().list_symptoms, profile, 100000, days
    )

    rows = [["记录时间", "患者档案", "症状", "程度", "备注"]]
    for item in reversed(items):
        rows.append([
            item.get("recorded_at", ""),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("symptom", "")),
            int(item.get("severity") or 0),
            csv_safe_cell(item.get("note", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "\ufeff" + buffer.getvalue()
    filename = f"medix-symptoms-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/symptoms/import")
async def import_symptoms_csv(request: Request, file: UploadFile = File(...)):
    """从导出的 CSV 格式导入症状记录，重复记录自动跳过。"""
    import csv
    import io

    from memory import get_session_store

    if _request_content_length(request) > 5 * 1024 * 1024 + 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="CSV 文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")

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
        "记录时间": "recorded_at",
        "时间": "recorded_at",
        "患者档案": "profile",
        "档案": "profile",
        "症状": "symptom",
        "程度": "severity",
        "严重程度": "severity",
        "备注": "note",
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

        symptom = mapped.get("symptom", "")
        if not symptom:
            invalid += 1
            continue
        severity_text = mapped.get("severity", "")
        if severity_text == "":
            invalid += 1
            continue
        try:
            severity = int(severity_text)
        except (TypeError, ValueError):
            invalid += 1
            continue
        if severity < 0 or severity > 10:
            invalid += 1
            continue
        recorded_at = mapped.get("recorded_at", "")
        if not recorded_at:
            invalid += 1
            continue

        records.append({
            "profile": mapped.get("profile", ""),
            "symptom": symptom,
            "severity": severity,
            "recorded_at": recorded_at,
            "note": mapped.get("note", ""),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的症状记录（无效 {invalid} 行）",
        )

    result = await asyncio.to_thread(
        get_session_store().import_data, {"symptoms": records}
    )
    imported = int(result.get("symptoms", 0))
    await record_audit(
        "symptom.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": len(records) - imported,
        "invalid": invalid,
    }


@app.post("/api/symptoms")
async def add_symptom(request: SymptomRequest):
    """新增一条症状记录。"""
    from memory import get_session_store

    item = await asyncio.to_thread(
        get_session_store().add_symptom,
        request.profile,
        request.symptom,
        request.severity,
        request.recorded_at,
        request.note,
    )
    if item is None:
        raise HTTPException(status_code=400, detail="症状内容或程度不正确")
    await record_audit(
        "symptom.create", f"{item['id']}·{item['symptom']}", item["recorded_at"]
    )
    return {"ok": True, "symptom": item}


@app.patch("/api/symptoms/{symptom_id}")
async def update_symptom(symptom_id: int, request: SymptomUpdateRequest):
    """部分更新一条症状记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_symptom, symptom_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    merged = {**existing, **request.model_dump(exclude_unset=True)}
    item = await asyncio.to_thread(
        store.update_symptom,
        symptom_id,
        merged.get("profile") or "",
        merged.get("symptom") or "",
        merged.get("severity"),
        merged.get("recorded_at") or "",
        merged.get("note") or "",
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在或内容不正确")
    await record_audit(
        "symptom.update", f"{item['id']}·{item['symptom']}"
    )
    return {"ok": True, "symptom": item}


@app.delete("/api/symptoms/{symptom_id}")
async def delete_symptom(symptom_id: int):
    """删除一条症状记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_symptom, symptom_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_symptom, symptom_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("symptom.delete", f"{item['id']}·{item['symptom']}")
    return {"ok": True, "id": symptom_id}


@app.get("/api/metrics/latest")
async def latest_metrics(profile: Optional[str] = None, days: int = 0):
    """每种指标类型最新的一条记录（可按档案与最近天数筛选）。"""
    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().latest_metrics, profile, days
    )
    return {"metrics": items}


@app.get("/api/reports/weekly")
async def weekly_report(profile: Optional[str] = None, days: int = 7):
    """近 N 天健康小结：指标、症状、用药打卡、会诊与待办提醒汇总。"""
    from datetime import timedelta

    from memory import get_session_store

    days = max(1, min(int(days or 7), 90))
    store = get_session_store()
    now = datetime.now()
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    raw_metrics = await asyncio.to_thread(
        store.list_metrics, profile, None, 100000, days * 2
    )
    metrics = [
        item for item in raw_metrics
        if start <= str(item.get("measured_at") or "")[:10] <= end
    ]
    prev_start = (
        now - timedelta(days=days * 2 - 1)
    ).strftime("%Y-%m-%d")
    previous_metrics = [
        item for item in raw_metrics
        if prev_start <= str(item.get("measured_at") or "")[:10] < start
    ]
    raw_symptoms = await asyncio.to_thread(
        store.list_symptoms, profile, 100000, days * 2
    )
    symptoms = [
        item for item in raw_symptoms
        if start <= str(item.get("recorded_at") or "")[:10] <= end
    ]
    previous_symptoms = [
        item for item in raw_symptoms
        if prev_start <= str(item.get("recorded_at") or "")[:10] < start
    ]
    previous_severity_total = sum(
        int(item.get("severity") or 0) for item in previous_symptoms
    )
    medications = await asyncio.to_thread(
        store.list_medications, profile, False, 100000
    )
    raw_logs = await asyncio.to_thread(
        store.list_medication_logs, None, profile, 100000, 0, days * 2
    )
    logs = [
        item for item in raw_logs
        if start <= str(item.get("taken_at") or "")[:10] <= end
    ]
    previous_logs = [
        item for item in raw_logs
        if prev_start <= str(item.get("taken_at") or "")[:10] < start
    ]
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline, profile, prev_start, "", 2000
    )
    sessions = [
        item for item in raw_sessions
        if int(item.get("message_count") or 0) > 0
        and start <= str(item.get("created_at") or "")[:10] <= end
    ]
    previous_sessions = [
        item for item in raw_sessions
        if int(item.get("message_count") or 0) > 0
        and prev_start <= str(item.get("created_at") or "")[:10] < start
    ]
    risk_level = ""
    if profile is not None and sessions:
        latest_answers = await asyncio.to_thread(
            store.latest_session_answers_by_profile
        )
        profile_key = _normalized_profile_key(profile)
        for item in latest_answers:
            if (item.get("profile") or "") == profile_key:
                risk_level = parse_risk_level(item.get("last_answer"))
                break
    pending_reminders = await asyncio.to_thread(
        store.list_reminders, False, None
    )
    if profile is not None:
        profile_key = _normalized_profile_key(profile)
        reminders = [
            item for item in pending_reminders
            if (item.get("profile") or "") == profile_key
        ]
    else:
        reminders = pending_reminders

    raw_targets = await asyncio.to_thread(store.list_metric_targets, None)
    target_map: dict = {}
    if profile is not None:
        profile_key = _normalized_profile_key(profile)
        target_map = {
            (item.get("profile") or "", item["metric"]): item
            for item in raw_targets
            if (item.get("profile") or "") == profile_key
            and isinstance(item, dict)
            and item.get("metric")
        }

    from memory.session_store import (
        adherence_rate,
        expected_doses_for_period,
        metric_over_target,
    )

    grouped: dict = {}
    for item in metrics:
        entry = grouped.setdefault(item["metric"], {
            "metric": item["metric"],
            "unit": item.get("unit", ""),
            "count": 0,
            "sum1": 0.0,
            "sum2": 0.0,
            "with_second": 0,
            "latest": None,
            "target": None,
            "within": 0,
        })
        entry["count"] += 1
        entry["sum1"] += float(item["value1"])
        if item.get("value2") is not None:
            entry["sum2"] += float(item["value2"])
            entry["with_second"] += 1
        if entry["latest"] is None:
            entry["latest"] = item
        target = target_map.get(
            (item.get("profile") or "", item["metric"])
        )
        if target is not None:
            entry["target"] = target
            if not metric_over_target(
                item["metric"], item["value1"], item.get("value2"), target
            ):
                entry["within"] += 1

    previous_grouped: dict = {}
    for item in previous_metrics:
        entry = previous_grouped.setdefault(item["metric"], {
            "count": 0,
            "sum1": 0.0,
            "sum2": 0.0,
            "with_second": 0,
        })
        entry["count"] += 1
        entry["sum1"] += float(item["value1"])
        if item.get("value2") is not None:
            entry["sum2"] += float(item["value2"])
            entry["with_second"] += 1
    previous_rows = {
        metric: {
            "prev_avg_value1": round(entry["sum1"] / entry["count"], 2),
            "prev_avg_value2": (
                round(entry["sum2"] / entry["with_second"], 2)
                if entry["with_second"] else None
            ),
        }
        for metric, entry in previous_grouped.items()
    }

    metric_rows = [
        {
            "metric": entry["metric"],
            "unit": entry["unit"],
            "count": entry["count"],
            "avg_value1": round(entry["sum1"] / entry["count"], 2),
            "avg_value2": (
                round(entry["sum2"] / entry["with_second"], 2)
                if entry["with_second"] else None
            ),
            "latest": entry["latest"],
            "target": entry["target"],
            "within_target": (
                entry["within"] if entry["target"] is not None else None
            ),
            "prev_avg_value1": previous_rows.get(
                entry["metric"], {}
            ).get("prev_avg_value1"),
            "prev_avg_value2": previous_rows.get(
                entry["metric"], {}
            ).get("prev_avg_value2"),
        }
        for entry in grouped.values()
    ]
    metric_rows.sort(key=lambda row: row["metric"])

    symptom_counts: dict = {}
    severity_total = 0
    max_severity = 0
    for item in symptoms:
        name = item["symptom"]
        symptom_counts[name] = symptom_counts.get(name, 0) + 1
        severity = int(item.get("severity") or 0)
        severity_total += severity
        max_severity = max(max_severity, severity)
    symptom_top = [
        {"symptom": name, "count": count}
        for name, count in sorted(
            symptom_counts.items(), key=lambda pair: (-pair[1], pair[0])
        )[:5]
    ]

    active_meds = [item for item in medications if item.get("active")]
    meds_by_id = {item["id"]: item for item in medications}
    active_med_ids = {item["id"] for item in active_meds}
    doses_by_medication: dict = {}
    for log in logs:
        key = log["medication_id"]
        entry = doses_by_medication.setdefault(key, {
            "medication_id": key,
            "name": log.get("medication_name") or "已删除药物",
            "dose": log.get("medication_dose") or "",
            "doses": 0,
        })
        entry["doses"] += 1
    for item in active_meds:
        if item["id"] not in doses_by_medication:
            doses_by_medication[item["id"]] = {
                "medication_id": item["id"],
                "name": item.get("name") or "",
                "dose": item.get("dose") or "",
                "doses": 0,
            }
    adherence_expected = 0
    adherence_taken = 0
    for item in active_meds:
        expected = expected_doses_for_period(
            item.get("frequency", ""), item.get("created_at", ""), now, days
        )
        if expected:
            adherence_expected += expected
    for log in logs:
        if log["medication_id"] in active_med_ids:
            adherence_taken += 1
    medication_rows = []
    for row in doses_by_medication.values():
        medication = meds_by_id.get(row["medication_id"])
        expected = None
        if medication is not None and medication.get("active"):
            expected = expected_doses_for_period(
                medication.get("frequency", ""),
                medication.get("created_at", ""),
                now,
                days,
            )
        row["expected"] = expected
        row["rate"] = (
            adherence_rate(row["doses"], expected) if expected else None
        )
        medication_rows.append(row)
    medication_rows.sort(key=lambda row: (-row["doses"], row["name"]))

    today = end
    horizon = (now + timedelta(days=7)).strftime("%Y-%m-%d")
    overdue = 0
    due_soon = 0
    for item in reminders:
        due = str(item.get("due_at") or "")[:10]
        if not due:
            continue
        if due < today:
            overdue += 1
        elif due <= horizon:
            due_soon += 1

    streak = _streak_info(
        await asyncio.to_thread(store.recorded_days, profile, 365)
    )

    return {
        "profile": profile or "",
        "days": days,
        "start": start,
        "end": end,
        "metrics": {"count": len(metrics), "by_metric": metric_rows},
        "symptoms": {
            "count": len(symptoms),
            "avg_severity": (
                round(severity_total / len(symptoms), 1)
                if symptoms else None
            ),
            "max_severity": max_severity,
            "top": symptom_top,
            "prev_count": len(previous_symptoms),
            "prev_avg_severity": (
                round(
                    previous_severity_total / len(previous_symptoms), 1
                )
                if previous_symptoms else None
            ),
        },
        "medications": {
            "active": len(active_meds),
            "doses": len(logs),
            "by_medication": medication_rows,
            "prev_doses": len(previous_logs),
            "adherence": (
                {
                    "expected": adherence_expected,
                    "taken": adherence_taken,
                    "rate": adherence_rate(
                        adherence_taken, adherence_expected
                    ),
                }
                if adherence_expected else None
            ),
        },
        "reminders": {
            "pending": len(reminders),
            "overdue": overdue,
            "due_in_7_days": due_soon,
        },
        "consultations": {
            "count": len(sessions),
            "prev_count": len(previous_sessions),
            "risk": risk_level,
        },
        "streak": streak,
    }


@app.get("/api/timeline")
async def health_timeline(
    profile: Optional[str] = None,
    days: int = 30,
    limit: int = 100,
    date: Optional[str] = None,
    type: Optional[str] = None,
    only_alert: bool = False,
    only_risk: bool = False,
    q: str = "",
):
    """健康时间线：指标、症状、用药打卡与会诊按时间倒序合并。"""
    from memory import get_session_store
    from memory.session_store import METRIC_TYPES, metric_over_target

    day = str(date or "").strip()
    if day:
        try:
            day = datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="日期格式应为 YYYY-MM-DD"
            )
    days = max(1, min(int(days or 30), 365))
    limit = max(1, min(int(limit or 100), 2000))
    store = get_session_store()
    if day:
        start = day
        window_days = 0
    else:
        start = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        window_days = days
    raw_metrics = await asyncio.to_thread(
        store.list_metrics, profile, None, 100000, window_days
    )
    raw_symptoms = await asyncio.to_thread(
        store.list_symptoms, profile, 100000, window_days
    )
    raw_logs = await asyncio.to_thread(
        store.list_medication_logs, None, profile, 100000, 0, window_days
    )
    session_end = (
        f"{day}T23:59:59" if day
        else datetime.now().replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
    )
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline,
        profile, start, session_end, 500, True,
    )
    targets = await asyncio.to_thread(store.list_metric_targets, None)
    target_map = {
        (item.get("profile") or "", item.get("metric")): item
        for item in targets
        if item.get("metric")
    }

    events: List[Dict[str, Any]] = []

    def in_window(at: str) -> bool:
        if not at:
            return False
        if day:
            return at[:10] == day
        return at[:10] >= start

    def format_value(value: Any) -> str:
        number = float(value)
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"

    for item in raw_metrics:
        at = str(item.get("measured_at") or "")
        if not in_window(at):
            continue
        label = METRIC_TYPES.get(
            item["metric"], (item["metric"], "", (0, 0))
        )[0]
        if (
            item["metric"] == "bp"
            and item.get("value2") is not None
        ):
            value = (
                f"{format_value(item['value1'])}/"
                f"{format_value(item['value2'])}"
            )
        else:
            value = format_value(item["value1"])
        unit = str(item.get("unit") or "").strip()
        target = target_map.get(
            (item.get("profile") or "", item["metric"])
        )
        alert = bool(
            target is not None
            and metric_over_target(
                item["metric"], item["value1"], item.get("value2"), target
            )
        )
        events.append({
            "type": "metric",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": label,
            "value": f"{value} {unit}".strip(),
            "note": item.get("note", ""),
            "alert": alert,
        })
    for item in raw_symptoms:
        at = str(item.get("recorded_at") or "")
        if not in_window(at):
            continue
        events.append({
            "type": "symptom",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("symptom", ""),
            "value": f"{int(item.get('severity') or 0)}/10",
            "note": item.get("note", ""),
            "alert": False,
        })
    for item in raw_logs:
        at = str(item.get("taken_at") or "")
        if not in_window(at):
            continue
        details = [
            str(item.get("medication_dose") or "").strip(),
            "打卡",
        ]
        events.append({
            "type": "medication",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("medication_name") or "用药打卡",
            "value": " · ".join(part for part in details if part),
            "note": item.get("note", ""),
            "alert": False,
        })

    for item in raw_sessions:
        at = str(item.get("created_at") or "")
        if not in_window(at):
            continue
        message_count = int(item.get("message_count") or 0)
        if message_count <= 0:
            continue
        risk = parse_risk_level(item.get("last_answer"))
        risk_label = RISK_LEVEL_LABELS.get(risk, "")
        events.append({
            "type": "consultation",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": str(item.get("title") or "").strip() or "会诊记录",
            "value": f"会诊 · {message_count} 条消息"
            + (f" · {risk_label}" if risk_label else ""),
            "note": "",
            "alert": False,
            "risk": risk,
        })

    def sort_key(event: Dict[str, Any]):
        event_id = event.get("id")
        if isinstance(event_id, int):
            return (event["at"], 0, event_id)
        return (event["at"], 1, str(event_id or ""))

    events.sort(key=sort_key, reverse=True)
    if type in {"metric", "symptom", "medication", "consultation"}:
        events = [event for event in events if event.get("type") == type]
    if only_alert:
        events = [event for event in events if event.get("alert")]
    if only_risk:
        events = [
            event for event in events
            if event.get("type") == "consultation"
            and event.get("risk") in ("high", "emergency")
        ]
    query = str(q or "").strip().lower()
    if query:
        events = [
            event for event in events
            if query in " ".join(
                str(event.get(key) or "")
                for key in ("title", "value", "note", "profile")
            ).lower()
        ]
    return {
        "events": events[:limit],
        "total": len(events),
        "days": days,
        "start": start,
        "date": day,
    }


@app.get("/api/timeline/export.csv")
async def export_timeline_csv(
    profile: Optional[str] = None,
    days: int = 30,
    type: Optional[str] = None,
    only_alert: bool = False,
    only_risk: bool = False,
    q: str = "",
    date: Optional[str] = None,
):
    """导出健康时间线 CSV（带 BOM），可按档案、天数、类型、超标、高危会诊与关键字筛选。"""
    import csv
    import io

    data = await health_timeline(
        profile=profile, days=days, limit=2000, date=date,
        type=type, only_alert=only_alert, only_risk=only_risk, q=q,
    )
    type_labels = {
        "metric": "指标",
        "symptom": "症状",
        "medication": "用药",
        "consultation": "会诊",
    }
    events = data["events"]
    rows = [["时间", "类型", "项目", "数值", "患者档案", "备注", "超标"]]
    for event in events:
        rows.append([
            csv_safe_cell(event.get("at", "")),
            type_labels.get(event.get("type"), event.get("type", "")),
            csv_safe_cell(event.get("title", "")),
            csv_safe_cell(event.get("value", "")),
            csv_safe_cell(event.get("profile", "")),
            csv_safe_cell(event.get("note", "")),
            "是" if event.get("alert") else "",
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-timeline-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/health/calendar")
async def health_calendar(profile: Optional[str] = None, month: str = ""):
    """健康月历：按天汇总指标、症状、用药打卡、会诊与提醒。"""
    import calendar as calendar_module

    from memory import get_session_store
    from memory.session_store import metric_over_target

    now = datetime.now()
    month_key = str(month or "").strip()
    if month_key:
        try:
            parsed = datetime.strptime(month_key, "%Y-%m")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="月份格式应为 YYYY-MM"
            )
        if not 2000 <= parsed.year <= 2100:
            raise HTTPException(status_code=400, detail="月份超出支持范围")
        month_key = parsed.strftime("%Y-%m")
    else:
        month_key = now.strftime("%Y-%m")
    year, month_number = int(month_key[:4]), int(month_key[5:7])
    last_day = calendar_module.monthrange(year, month_number)[1]
    start = f"{month_key}-01"
    end = f"{month_key}-{last_day:02d}"

    store = get_session_store()
    raw_metrics = await asyncio.to_thread(
        store.list_metrics, profile, None, 100000, 0
    )
    raw_symptoms = await asyncio.to_thread(
        store.list_symptoms, profile, 100000, 0
    )
    raw_logs = await asyncio.to_thread(
        store.list_medication_logs, None, profile, 100000, 0, 0
    )
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline, profile, start, "", 2000, True
    )
    targets = await asyncio.to_thread(store.list_metric_targets, None)
    target_map = {
        (item.get("profile") or "", item.get("metric")): item
        for item in targets
        if item.get("metric")
    }
    reminders = await asyncio.to_thread(store.list_reminders, True, None)
    if profile is not None:
        profile_key = _normalized_profile_key(profile)
        reminders = [
            item for item in reminders
            if (item.get("profile") or "") == profile_key
        ]

    days: Dict[str, Dict[str, int]] = {}

    def bucket(day_key: str) -> Dict[str, int]:
        return days.setdefault(day_key, {
            "metrics": 0, "symptoms": 0, "doses": 0,
            "consultations": 0, "reminders_due": 0, "reminders_done": 0,
            "alerts": 0,
        })

    def in_month(value: Any) -> bool:
        key = str(value or "")[:10]
        return start <= key <= end

    for item in raw_metrics:
        at = str(item.get("measured_at") or "")
        if in_month(at):
            entry = bucket(at[:10])
            entry["metrics"] += 1
            target = target_map.get(
                (item.get("profile") or "", item.get("metric"))
            )
            if target is not None and metric_over_target(
                item["metric"],
                item.get("value1"),
                item.get("value2"),
                target,
            ):
                entry["alerts"] += 1
    for item in raw_symptoms:
        at = str(item.get("recorded_at") or "")
        if in_month(at):
            entry = bucket(at[:10])
            entry["symptoms"] += 1
            if int(item.get("severity") or 0) >= 7:
                entry["alerts"] += 1
    for item in raw_logs:
        at = str(item.get("taken_at") or "")
        if in_month(at):
            bucket(at[:10])["doses"] += 1
    for item in raw_sessions:
        at = str(item.get("created_at") or "")
        if in_month(at) and int(item.get("message_count") or 0) > 0:
            entry = bucket(at[:10])
            entry["consultations"] += 1
            risk = parse_risk_level(item.get("last_answer"))
            if risk in ("high", "emergency"):
                entry["alerts"] += 1
    for item in reminders:
        at = str(item.get("due_at") or "")
        if not in_month(at):
            continue
        entry = bucket(at[:10])
        if item.get("done"):
            entry["reminders_done"] += 1
        else:
            entry["reminders_due"] += 1

    totals = {
        key: sum(entry[key] for entry in days.values())
        for key in (
            "metrics", "symptoms", "doses",
            "consultations", "reminders_due", "reminders_done",
            "alerts",
        )
    }
    return {
        "month": month_key,
        "profile": profile or "",
        "start": start,
        "end": end,
        "days": days,
        "totals": totals,
    }


@app.get("/api/health/calendar/export.csv")
async def export_health_calendar_csv(
    profile: Optional[str] = None, month: str = ""
):
    """导出健康月历 CSV（带 BOM），按天列出指标、症状、用药、会诊与提醒。"""
    import calendar as calendar_module
    import csv
    import io

    data = await health_calendar(profile=profile, month=month)
    month_key = data["month"]
    year, month_number = int(month_key[:4]), int(month_key[5:7])
    last_day = calendar_module.monthrange(year, month_number)[1]
    rows = [[
        "日期", "指标", "症状", "用药打卡", "会诊",
        "待办提醒", "已完成提醒", "异常",
    ]]
    for day in range(1, last_day + 1):
        key = f"{month_key}-{day:02d}"
        entry = data["days"].get(key) or {}
        rows.append([
            key,
            str(int(entry.get("metrics") or 0)),
            str(int(entry.get("symptoms") or 0)),
            str(int(entry.get("doses") or 0)),
            str(int(entry.get("consultations") or 0)),
            str(int(entry.get("reminders_due") or 0)),
            str(int(entry.get("reminders_done") or 0)),
            str(int(entry.get("alerts") or 0)),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-calendar-{month_key}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/health/streak")
async def health_streak(profile: Optional[str] = None):
    """健康记录连续天数：当前连续、最长连续与近 30 天记录天数。"""
    from memory import get_session_store

    store = get_session_store()
    days = await asyncio.to_thread(store.recorded_days, profile, 365)
    info = _streak_info(days)
    info["profile"] = profile or ""
    return info


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
    await record_audit("metric.create", f"{item['id']}·{item['metric']}")
    return {"ok": True, "metric": item}


@app.patch("/api/metrics/{metric_id}")
async def update_metric(metric_id: int, request: MetricUpdateRequest):
    """部分更新一条健康指标记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_metric, metric_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    merged = {**existing, **request.model_dump(exclude_unset=True)}
    item = await asyncio.to_thread(
        store.update_metric,
        metric_id,
        merged.get("profile") or "",
        merged.get("metric") or "",
        merged.get("value1"),
        merged.get("value2"),
        merged.get("measured_at") or "",
        merged.get("note") or "",
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在或内容不正确")
    await record_audit("metric.update", f"{item['id']}·{item['metric']}")
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
    days: int = 0,
    q: str = "",
):
    """导出健康指标 CSV（带 BOM，Excel 可直接打开）。"""
    import csv
    import io

    from memory import get_session_store

    if metric and metric not in (
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")

    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().list_metrics,
        profile, metric, 100000, days, 0, q,
    )
    labels = {
        "bp": "血压",
        "weight": "体重",
        "glucose": "血糖",
        "temperature": "体温",
        "heart_rate": "心率",
        "spo2": "血氧饱和度",
        "waist": "腰围",
        "phq9": "PHQ-9 评分",
        "gad7": "GAD-7 评分",
        "cat": "CAT 评分",
        "ess": "ESS 评分",
        "auditc": "AUDIT-C 评分",
        "mmrc": "mMRC 分级",
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
async def import_metrics_csv(request: Request, file: UploadFile = File(...)):
    """从导出的 CSV 格式导入健康指标，重复记录自动跳过。"""
    import csv
    import io

    from memory import get_session_store

    if _request_content_length(request) > 5 * 1024 * 1024 + 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="CSV 文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")

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
        "CAT 评分": "cat",
        "CAT评分": "cat",
        "ESS 评分": "ess",
        "ESS评分": "ess",
        "AUDIT-C 评分": "auditc",
        "AUDIT-C评分": "auditc",
        "mMRC 分级": "mmrc",
        "mMRC分级": "mmrc",
        "心率": "heart_rate",
        "血氧": "spo2",
        "血氧饱和度": "spo2",
        "腰围": "waist",
        "心率": "heart_rate",
        "血氧": "spo2",
        "血氧饱和度": "spo2",
        "腰围": "waist",
    }
    allowed = {
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "phq9", "gad7", "cat", "ess", "auditc", "mmrc"
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
    await record_audit("profile.save", profile["name"])
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


@app.get("/api/family/overview")
async def family_overview(days: int = 7):
    """家庭健康总览：按档案汇总最近指标、用药、提醒、症状与会诊。"""
    from memory import get_session_store
    from memory.session_store import (
        adherence_rate,
        METRIC_TYPES,
        metric_over_target,
    )

    days = max(1, min(int(days or 7), 90))
    store = get_session_store()
    now = datetime.now()
    today = now.strftime("%Y-%m-%d")
    cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    saved_profiles = await asyncio.to_thread(store.list_profiles)
    used_names = await asyncio.to_thread(store.list_used_profile_names)
    contexts = {
        item["name"]: item.get("context") or {} for item in saved_profiles
    }
    names = sorted(set(contexts) | set(used_names))
    if not names:
        return {"days": days, "profiles": []}

    latest_metrics = await asyncio.to_thread(store.latest_metrics_by_profile)
    medications = await asyncio.to_thread(
        store.list_medications, None, False, 100000
    )
    reminders = await asyncio.to_thread(store.list_reminders, False, None)
    symptoms = await asyncio.to_thread(
        store.list_symptoms, None, 100000, days
    )
    targets = await asyncio.to_thread(store.list_metric_targets, None)
    sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline, None, cutoff, "", 2000
    )
    latest_answers = await asyncio.to_thread(
        store.latest_session_answers_by_profile
    )
    recorded_days = await asyncio.to_thread(
        store.recorded_days_by_profile, 365
    )
    risk_by_profile: Dict[str, str] = {}
    for item in latest_answers:
        profile_name = item.get("profile") or ""
        risk = parse_risk_level(item.get("last_answer"))
        if profile_name and risk:
            risk_by_profile[profile_name] = risk

    target_map = {
        (item.get("profile") or "", item.get("metric")): item
        for item in targets
        if item.get("metric")
    }

    def format_metric_value(item: Dict[str, Any]) -> str:
        text = f"{float(item.get('value1') or 0):g}"
        if item.get("value2") is not None:
            return f"{text}/{float(item['value2']):g}"
        return text

    metrics_by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for item in latest_metrics:
        profile = item.get("profile") or ""
        if not profile:
            continue
        metric = item.get("metric") or ""
        label, default_unit, _ = METRIC_TYPES.get(metric, (metric, "", (0, 0)))
        target = target_map.get((profile, metric))
        over_target: Optional[bool] = None
        if target is not None:
            over_target = metric_over_target(
                metric, item.get("value1"), item.get("value2"), target
            )
        metrics_by_profile.setdefault(profile, []).append({
            "metric": metric,
            "label": label,
            "value": format_metric_value(item),
            "unit": item.get("unit") or default_unit,
            "measured_at": item.get("measured_at", ""),
            "over_target": over_target,
        })

    medications_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in medications:
        profile = item.get("profile") or ""
        if not profile or not item.get("active"):
            continue
        entry = medications_by_profile.setdefault(profile, {
            "active": 0,
            "taken_today": 0,
            "names": [],
            "last_taken_at": "",
            "week_count": 0,
            "expected_week_doses": 0,
        })
        entry["active"] += 1
        entry["taken_today"] += int(item.get("today_count") or 0)
        entry["week_count"] += int(item.get("week_count") or 0)
        entry["expected_week_doses"] += int(
            item.get("expected_week_doses") or 0
        )
        if len(entry["names"]) < 3:
            entry["names"].append(item.get("name") or "")
        last_taken = str(item.get("last_taken_at") or "")
        if last_taken > entry["last_taken_at"]:
            entry["last_taken_at"] = last_taken

    for entry in medications_by_profile.values():
        expected = entry.pop("expected_week_doses")
        taken = entry.pop("week_count")
        entry["adherence"] = (
            {
                "expected": expected,
                "taken": taken,
                "rate": adherence_rate(taken, expected),
            }
            if expected else None
        )

    reminders_by_profile: Dict[str, Dict[str, int]] = {}
    for item in reminders:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = reminders_by_profile.setdefault(profile, {
            "pending": 0, "overdue": 0, "due_today": 0,
        })
        entry["pending"] += 1
        due = str(item.get("due_at") or "")[:10]
        if due and due < today:
            entry["overdue"] += 1
        elif due == today:
            entry["due_today"] += 1

    symptoms_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in symptoms:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = symptoms_by_profile.setdefault(profile, {
            "count": 0, "latest": None,
        })
        entry["count"] += 1
        if entry["latest"] is None:
            entry["latest"] = {
                "symptom": item.get("symptom", ""),
                "severity": int(item.get("severity") or 0),
                "recorded_at": item.get("recorded_at", ""),
            }

    sessions_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in sessions:
        profile = item.get("profile") or ""
        if not profile or int(item.get("message_count") or 0) <= 0:
            continue
        at = str(item.get("created_at") or "")
        if not at or at[:10] < cutoff:
            continue
        entry = sessions_by_profile.setdefault(profile, {
            "count": 0, "latest_at": "",
        })
        entry["count"] += 1
        if at > entry["latest_at"]:
            entry["latest_at"] = at

    profiles = []
    for name in names:
        metrics = sorted(
            metrics_by_profile.get(name, []),
            key=lambda row: str(row.get("measured_at") or ""),
            reverse=True,
        )
        medication = medications_by_profile.get(name, {
            "active": 0, "taken_today": 0, "names": [],
            "last_taken_at": "", "adherence": None,
        })
        reminder = reminders_by_profile.get(name, {
            "pending": 0, "overdue": 0, "due_today": 0,
        })
        symptom = symptoms_by_profile.get(name, {
            "count": 0, "latest": None,
        })
        consultation = sessions_by_profile.get(name, {
            "count": 0, "latest_at": "",
        })
        activity_candidates = [
            metrics[0]["measured_at"] if metrics else "",
            (symptom.get("latest") or {}).get("recorded_at") or "",
            consultation.get("latest_at") or "",
            medication.get("last_taken_at") or "",
        ]
        last_activity = max(activity_candidates)
        recorded_today = bool(
            any(
                str(metric.get("measured_at") or "")[:10] == today
                for metric in metrics
            )
            or str(
                (symptom.get("latest") or {}).get("recorded_at") or ""
            )[:10] == today
            or medication.get("taken_today")
            or str(consultation.get("latest_at") or "")[:10] == today
        )
        profiles.append({
            "name": name,
            "context": contexts.get(name) or {},
            "metrics": metrics,
            "medications": medication,
            "reminders": reminder,
            "symptoms": symptom,
            "consultations": {
                **consultation,
                "risk": risk_by_profile.get(name, ""),
            },
            "last_activity": last_activity,
            "recorded_today": recorded_today,
            "streak": _streak_info(
                recorded_days.get(name, [])
            )["current"],
        })

    profiles.sort(
        key=lambda item: (item["last_activity"], item["name"]),
        reverse=True,
    )
    return {"days": days, "profiles": profiles}


@app.get("/api/family/overview/export.csv")
async def export_family_overview_csv(days: int = 7):
    """导出家庭健康总览 CSV（带 BOM），每位成员一行。"""
    import csv
    import io

    data = await family_overview(days=days)
    days = data["days"]
    rows = [[
        "成员", "年龄", "性别", "最近指标", "用药", "近7天依从率",
        "待办提醒", "近7天症状", "近7天会诊", "最近活动",
    ]]
    for item in data["profiles"]:
        context = item.get("context") or {}
        metric_text = "；".join(
            (
                f"{metric['label']} {metric['value']} {metric['unit']}".strip()
                + ("（超标）" if metric.get("over_target") else "")
            )
            for metric in (item.get("metrics") or [])[:3]
        )
        medications = item.get("medications") or {}
        medication_text = ""
        adherence_text = ""
        if medications.get("active"):
            medication_text = (
                f"{medications['active']} 种，"
                f"今日 {medications.get('taken_today', 0)} 次"
            )
            adherence = medications.get("adherence")
            if adherence:
                adherence_text = f"{adherence['rate']}%"
        reminders = item.get("reminders") or {}
        reminder_text = (
            f"待办 {reminders.get('pending', 0)}，"
            f"逾期 {reminders.get('overdue', 0)}，"
            f"今天 {reminders.get('due_today', 0)}"
        )
        symptoms = item.get("symptoms") or {}
        symptom_text = str(symptoms.get("count") or 0)
        latest = symptoms.get("latest") or {}
        if latest.get("symptom"):
            symptom_text += (
                f"（最近 {latest['symptom']} {latest['severity']}/10）"
            )
        consultations = item.get("consultations") or {}
        risk_label = {
            "emergency": "紧急",
            "high": "高危",
            "medium": "中危",
            "low": "低危",
        }.get(consultations.get("risk") or "", "")
        rows.append([
            csv_safe_cell(item.get("name", "")),
            csv_safe_cell(context.get("age", "")),
            csv_safe_cell(context.get("sex", "")),
            csv_safe_cell(metric_text),
            csv_safe_cell(medication_text),
            csv_safe_cell(adherence_text),
            csv_safe_cell(reminder_text),
            csv_safe_cell(symptom_text),
            csv_safe_cell(
                f"近 {days} 天 {consultations.get('count', 0)} 次"
                + (f"（{risk_label}）" if risk_label else "")
            ),
            csv_safe_cell(item.get("last_activity", "")),
        ])

    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = f"medix-family-{datetime.now().strftime('%Y%m%d')}.csv"

    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/family/compare")
async def family_compare(metric: str = "weight", days: int = 30):
    """家庭指标对比：按档案统计指定指标的平均值与最近记录。"""
    from memory import get_session_store
    from memory.session_store import METRIC_TYPES

    metric = str(metric or "").strip()
    if metric not in METRIC_TYPES:
        raise HTTPException(status_code=400, detail="不支持的指标类型")
    days = max(1, min(int(days or 30), 365))
    store = get_session_store()
    names = await asyncio.to_thread(store.list_used_profile_names)
    rows = []
    for name in names:
        summary = await asyncio.to_thread(
            store.metric_summary, name, metric, days
        )
        if summary is None:
            continue
        latest = summary.get("latest") or {}
        value1 = summary.get("value1") or {}
        value2 = summary.get("value2") or {}
        rows.append({
            "name": name,
            "count": int(summary.get("count") or 0),
            "avg_value1": value1.get("avg"),
            "avg_value2": value2.get("avg"),
            "min_value1": value1.get("min"),
            "max_value1": value1.get("max"),
            "latest": {
                "value1": latest.get("value1"),
                "value2": latest.get("value2"),
                "unit": latest.get("unit", ""),
                "measured_at": latest.get("measured_at", ""),
            } if latest else None,
            "within_target": summary.get("within_target"),
            "target_lower_bound": summary.get("target_lower_bound"),
            "target": summary.get("target"),
        })
    rows.sort(
        key=lambda row: (
            row["avg_value1"] is None,
            -(row["avg_value1"] or 0),
            row["name"],
        )
    )
    label, unit, _ = METRIC_TYPES[metric]
    return {
        "metric": metric,
        "label": label,
        "unit": unit,
        "days": days,
        "profiles": rows,
    }


@app.get("/api/health/dossier.md")
async def export_health_dossier(
    profile: str = "", days: int = 30, allow_unassigned: bool = False
):
    """导出单个患者的健康档案 Markdown（指标、症状、用药、提醒与会诊）。"""
    from memory import get_session_store
    from memory.session_store import (
        LOWER_BOUND_TARGET_METRICS,
        METRIC_TYPES,
        adherence_rate,
        metric_over_target,
    )

    profile_key = _normalized_profile_key(profile)
    # 空档案名表示「未指定档案」的记录；单独导出接口仍要求先选择档案，
    # 家庭导出通过 allow_unassigned=True 附带这部分记录
    if not profile_key and not allow_unassigned:
        raise HTTPException(status_code=400, detail="请先选择患者档案")
    days = max(1, min(int(days or 30), 365))
    store = get_session_store()
    now = datetime.now()
    cutoff = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")

    profiles = await asyncio.to_thread(store.list_profiles)
    context: Dict[str, Any] = {}
    for item in profiles:
        if item.get("name") == profile_key:
            context = item.get("context") or {}
            break
    targets = (
        await asyncio.to_thread(store.list_metric_targets, profile_key)
        if profile_key else []
    )
    target_map = {
        item["metric"]: item
        for item in targets
        if isinstance(item, dict) and item.get("metric")
    }
    latest_metrics = await asyncio.to_thread(store.latest_metrics, profile_key)
    symptoms = await asyncio.to_thread(
        store.list_symptoms, profile_key, 100000, days
    )
    medications = await asyncio.to_thread(
        store.list_medications, profile_key, False, 100000
    )
    if profile_key:
        reminders = await asyncio.to_thread(
            store.list_reminders, False, profile_key
        )
    else:
        all_reminders = await asyncio.to_thread(
            store.list_reminders, False, None
        )
        reminders = [
            item for item in all_reminders
            if not (item.get("profile") or "")
        ]
    sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline, profile_key, cutoff, "", 200, True
    )
    streak = _streak_info(
        await asyncio.to_thread(store.recorded_days, profile_key, 365)
    )

    lines: List[str] = [
        f"# 健康档案：{profile_key or '未指定'}", ""
    ]
    lines.append(f"- 生成时间：{now.strftime('%Y-%m-%d %H:%M')}")
    context_parts = []
    if context.get("age"):
        context_parts.append(f"{context['age']} 岁")
    if context.get("sex"):
        context_parts.append(str(context["sex"]))
    if context.get("duration"):
        context_parts.append(f"病程：{context['duration']}")
    if context.get("history"):
        context_parts.append(f"既往史：{context['history']}")
    if context.get("medications"):
        context_parts.append(f"长期用药：{context['medications']}")
    if context.get("allergies"):
        context_parts.append(f"过敏史：{context['allergies']}")
    if context_parts:
        lines.append("- 档案信息：" + " · ".join(context_parts))
    lines.append(
        f"- 连续记录：已连续 {streak['current']} 天"
        f"（近 30 天记录 {streak['days_last_30']} 天）"
    )
    lines.append("")

    lines.append("## 指标目标")
    if target_map:
        for metric, target in sorted(target_map.items()):
            label, unit, _ = METRIC_TYPES.get(metric, (metric, "", (0, 0)))
            unit = f" {unit}" if unit else ""
            if metric == "bp" and target.get("value2") is not None:
                text = f"≤ {target['value1']:g}/{target['value2']:g}{unit}"
            elif metric in LOWER_BOUND_TARGET_METRICS:
                text = f"≥ {target['value1']:g}{unit}"
            else:
                text = f"≤ {target['value1']:g}{unit}"
            lines.append(f"- {label}：{text}")
    else:
        lines.append("- 暂未设置指标目标")
    lines.append("")

    lines.append("## 最近指标")
    if latest_metrics:
        for item in sorted(
            latest_metrics,
            key=lambda row: str(row.get("measured_at") or ""),
            reverse=True,
        ):
            metric = item["metric"]
            label, default_unit, _ = METRIC_TYPES.get(
                metric, (metric, "", (0, 0))
            )
            unit = item.get("unit") or default_unit
            value = f"{float(item['value1']):g}"
            if item.get("value2") is not None:
                value += f"/{float(item['value2']):g}"
            target = target_map.get(metric)
            details = [str(item.get("measured_at") or "").replace("T", " ")[:16]]
            if target is not None and metric_over_target(
                metric, item["value1"], item.get("value2"), target
            ):
                details.append("超标")
            details = [part for part in details if part]
            suffix = f"（{'，'.join(details)}）" if details else ""
            lines.append(f"- {label}：{value} {unit}{suffix}")
    else:
        lines.append("- 暂无健康指标记录")
    lines.append("")

    lines.append(f"## 近 {days} 天症状")
    if symptoms:
        for item in symptoms:
            note = f"（{item['note']}）" if item.get("note") else ""
            lines.append(
                f"- {str(item.get('recorded_at') or '').replace('T', ' ')[:16]} "
                f"{item.get('symptom', '')} "
                f"{int(item.get('severity') or 0)}/10{note}"
            )
    else:
        lines.append("- 暂无症状记录")
    lines.append("")

    lines.append("## 用药清单")
    if medications:
        for item in medications:
            status = "在用" if item.get("active") else "停用"
            detail = f"- {item.get('name') or ''}"
            if item.get("dose"):
                detail += f" {item['dose']}"
            if item.get("frequency"):
                detail += f"，{item['frequency']}"
            detail += f"（{status}"
            if item.get("active"):
                detail += (
                    f"；今日 {int(item.get('today_count') or 0)} 次，"
                    f"近 7 天 {int(item.get('week_count') or 0)} 次"
                )
                expected = item.get("expected_week_doses")
                if expected:
                    rate = adherence_rate(item.get("week_count"), expected)
                    detail += f"，依从率 {rate}%"
            detail += "）"
            lines.append(detail)
    else:
        lines.append("- 暂无用药记录")
    lines.append("")

    lines.append("## 待办提醒")
    today = now.strftime("%Y-%m-%d")
    if reminders:
        for item in reminders:
            due = str(item.get("due_at") or "")
            overdue = "（已逾期）" if due[:10] < today else ""
            lines.append(
                f"- {due} {item.get('title') or '随访提醒'}{overdue}"
            )
    else:
        lines.append("- 暂无待办提醒")
    lines.append("")

    lines.append(f"## 近 {days} 天会诊")
    visible_sessions = [
        item for item in sessions
        if int(item.get("message_count") or 0) > 0
    ]
    if visible_sessions:
        for item in visible_sessions:
            risk = parse_risk_level(item.get("last_answer"))
            risk_text = (
                f"（{RISK_LEVEL_LABELS[risk]}）" if risk else ""
            )
            lines.append(
                f"- {str(item.get('created_at') or '').replace('T', ' ')[:16]} "
                f"{str(item.get('title') or '').strip() or '会诊记录'}"
                f"{risk_text}"
            )
    else:
        lines.append("- 暂无会诊记录")
    lines.append("")
    lines.append("---")
    lines.append(
        "本档案由 MediX 生成，仅供个人健康记录参考，不构成医疗建议。"
    )

    content = "\n".join(lines) + "\n"
    filename = f"medix-dossier-{now.strftime('%Y%m%d')}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/health/dossier-family.md")
async def export_family_dossier(days: int = 30):
    """导出所有患者档案的健康档案 Markdown（含未指定档案的记录）。"""
    from memory import get_session_store

    store = get_session_store()
    names = await asyncio.to_thread(store.list_used_profile_names)
    unassigned = any([
        await asyncio.to_thread(store.list_metrics, "", None, 1),
        await asyncio.to_thread(store.list_symptoms, "", 1),
        await asyncio.to_thread(store.list_medications, "", False, 1),
    ])
    if not names and not unassigned:
        raise HTTPException(status_code=400, detail="暂无患者档案")
    parts = []
    if unassigned:
        response = await export_health_dossier(
            profile="", days=days, allow_unassigned=True
        )
        parts.append(response.body.decode("utf-8").rstrip() + "\n")
    for name in names:
        response = await export_health_dossier(profile=name, days=days)
        parts.append(response.body.decode("utf-8").rstrip() + "\n")
    content = "\n".join(parts)
    filename = f"medix-family-dossier-{datetime.now().strftime('%Y%m%d')}.md"
    return Response(
        content=content,
        media_type="text/markdown; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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


@app.get("/api/reminders/stats")
async def reminder_stats(profile: Optional[str] = None, days: int = 30):
    """近 N 天到期提醒的完成情况统计。"""
    from memory import get_session_store

    days = max(1, min(int(days or 30), 365))
    now = datetime.now()
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    store = get_session_store()
    reminders = await asyncio.to_thread(store.list_reminders, True, None)
    if profile is not None:
        profile_key = _normalized_profile_key(profile)
        reminders = [
            item for item in reminders
            if (item.get("profile") or "") == profile_key
        ]
    due = [
        item for item in reminders
        if start <= str(item.get("due_at") or "")[:10] <= end
    ]
    done_total = sum(1 for item in due if item.get("done"))
    pending_total = len(due) - done_total
    overdue_total = sum(
        1 for item in due
        if not item.get("done")
        and str(item.get("due_at") or "")[:10] < end
    )
    completion_rate = (
        round(done_total / len(due) * 100 + 1e-9) if due else None
    )
    return {
        "days": days,
        "start": start,
        "end": end,
        "due_total": len(due),
        "done_total": done_total,
        "pending_total": pending_total,
        "overdue_total": overdue_total,
        "completion_rate": completion_rate,
    }


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
            request.advance_days,
            request.repeat_days,
            request.repeat_until,
            request.medication_id,
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
    q: Optional[str] = None,
    overdue: bool = False,
    date: Optional[str] = None,
):
    """导出随访提醒 CSV（带 BOM），支持档案、完成状态、关键字、逾期与日期筛选。"""
    import csv
    import io

    from memory import get_session_store

    store = get_session_store()
    items = await asyncio.to_thread(
        store.list_reminders, include_done, profile
    )
    query = str(q or "").strip().lower()
    if query:
        items = [
            item for item in items
            if query in " ".join(
                str(item.get(key) or "")
                for key in ("title", "note", "profile")
            ).lower()
        ]
    if overdue:
        today = datetime.now().strftime("%Y-%m-%d")
        items = [
            item for item in items
            if not item.get("done")
            and str(item.get("due_at") or "")[:10] < today
        ]
    day = str(date or "").strip()[:10]
    if day:
        items = [
            item for item in items
            if str(item.get("due_at") or "")[:10] == day
        ]
    medications = await asyncio.to_thread(
        store.list_medications, None, False, 100000
    )
    medication_names = {
        int(item["id"]): item.get("name", "") for item in medications
    }
    rows = [[
        "到期日", "内容", "档案", "状态", "提前天数", "重复天数",
        "重复截止", "重复来源ID", "备注", "关联药物ID", "关联药物",
    ]]
    for item in items:
        medication_id = int(item.get("medication_id") or 0)
        rows.append([
            item.get("due_at", ""),
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("profile", "")),
            "已完成" if item.get("done") else "待完成",
            int(item.get("advance_days") or 0),
            int(item.get("repeat_days") or 0),
            item.get("repeat_until", "") or "",
            int(item.get("repeats_from") or 0) or "",
            csv_safe_cell(item.get("note", "")),
            medication_id or "",
            csv_safe_cell(medication_names.get(medication_id, "")),
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
        due_text = str(item.get("due_at") or "").strip()
        try:
            start = datetime.fromisoformat(due_text[:19])
        except ValueError:
            continue
        timed = "T" in due_text[:19]
        if timed:
            try:
                end = start + timedelta(minutes=30)
            except OverflowError:
                end = start
            start_line = f"DTSTART:{start.strftime('%Y%m%dT%H%M%S')}"
            end_line = f"DTEND:{end.strftime('%Y%m%dT%H%M%S')}"
        else:
            try:
                end = start + timedelta(days=1)
            except OverflowError:
                end = start
            start_line = f"DTSTART;VALUE=DATE:{start.strftime('%Y%m%d')}"
            end_line = f"DTEND;VALUE=DATE:{end.strftime('%Y%m%d')}"
        description = item.get("note") or ""
        if item.get("profile"):
            description = f"档案：{item['profile']}。{description}".strip()
        lines.extend([
            "BEGIN:VEVENT",
            f"UID:medix-reminder-{item['id']}@medix.local",
            f"DTSTAMP:{stamp}",
            start_line,
            end_line,
            f"SUMMARY:{escape_ics(item.get('title') or '随访提醒')}",
            f"DESCRIPTION:{escape_ics(description)}",
            # RFC 5545 的 VEVENT STATUS 只允许 TENTATIVE/CONFIRMED/CANCELLED
            "STATUS:CONFIRMED",
        ])
        if item.get("done"):
            # 已完成的提醒用透明事件表示，避免日历客户端解析失败
            lines.append("TRANSP:TRANSPARENT")
        repeat_days = int(item.get("repeat_days") or 0)
        if repeat_days > 0 and not item.get("done"):
            rule = f"RRULE:FREQ=DAILY;INTERVAL={repeat_days}"
            until = str(item.get("repeat_until") or "")[:10].replace("-", "")
            if until:
                rule += f";UNTIL={until}T235959" if timed else f";UNTIL={until}"
            lines.append(rule)
        advance = int(item.get("advance_days") or 0)
        if advance > 0 and not item.get("done"):
            lines.extend([
                "BEGIN:VALARM",
                f"TRIGGER:-P{advance}D",
                "ACTION:DISPLAY",
                "DESCRIPTION:随访提醒",
                "END:VALARM",
            ])
        lines.append("END:VEVENT")
    lines.append("END:VCALENDAR")

    content = "\r\n".join(fold_line(line) for line in lines) + "\r\n"
    filename = f"medix-reminders-{datetime.now().strftime('%Y%m%d')}.ics"

    return Response(
        content=content,
        media_type="text/calendar; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/reminders/import")
async def import_reminders_csv(request: Request, file: UploadFile = File(...)):
    """从导出的 CSV 格式导入随访提醒，重复提醒自动跳过。"""
    import csv
    import io

    from memory import get_session_store

    if _request_content_length(request) > 5 * 1024 * 1024 + 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")
    raw = await file.read(5 * 1024 * 1024 + 1)
    if not raw:
        raise HTTPException(status_code=400, detail="CSV 文件为空")
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="CSV 文件超过 5 MB")

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
        "到期日": "due_at",
        "日期": "due_at",
        "提醒日期": "due_at",
        "内容": "title",
        "提醒内容": "title",
        "标题": "title",
        "档案": "profile",
        "患者档案": "profile",
        "状态": "done",
        "提前天数": "advance_days",
        "重复天数": "repeat_days",
        "重复截止": "repeat_until",
        "重复来源ID": "repeats_from",
        "备注": "note",
        "关联药物ID": "medication_id",
        "关联药物": "medication_name",
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

        title = mapped.get("title", "")
        due_at = mapped.get("due_at", "")
        if not title or not due_at:
            invalid += 1
            continue

        status = mapped.get("done", "")
        try:
            advance_days = max(
                0, min(int(mapped.get("advance_days") or 0), 30)
            )
        except (TypeError, ValueError):
            advance_days = 0
        try:
            repeat_days = max(
                0, min(int(mapped.get("repeat_days") or 0), 365)
            )
        except (TypeError, ValueError):
            repeat_days = 0
        try:
            repeats_from = max(0, int(mapped.get("repeats_from") or 0))
        except (TypeError, ValueError):
            repeats_from = 0
        try:
            medication_id = max(0, int(mapped.get("medication_id") or 0))
        except (TypeError, ValueError):
            medication_id = 0
        records.append({
            "title": title,
            "due_at": due_at,
            "profile": mapped.get("profile", ""),
            "note": mapped.get("note", ""),
            "advance_days": advance_days,
            "repeat_days": repeat_days,
            "repeat_until": mapped.get("repeat_until", ""),
            "repeats_from": repeats_from,
            "medication_id": medication_id,
            "medication_name": mapped.get("medication_name", ""),
            "done": status in ("已完成", "完成", "done", "1", "true", "True"),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的提醒（无效 {invalid} 行）",
        )

    result = await asyncio.to_thread(
        get_session_store().import_data, {"reminders": records}
    )
    imported = int(result.get("reminders", 0))
    await record_audit(
        "reminder.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": len(records) - imported,
        "invalid": invalid,
    }


@app.post("/api/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: int):
    """把随访提醒标记为已完成。"""
    from memory import get_session_store

    store = get_session_store()
    reminder = await asyncio.to_thread(store.get_reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    result = await asyncio.to_thread(
        store.complete_reminder_with_repeat, reminder_id
    )
    if result is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await record_audit("reminder.complete", reminder_audit_target(reminder))
    return {"ok": True, "id": reminder_id, "next": result.get("next")}


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
        and request.advance_days is None
        and request.profile is None
        and request.repeat_days is None
        and request.repeat_until is None
    ):
        raise HTTPException(status_code=400, detail="没有需要修改的字段")
    try:
        reminder = await asyncio.to_thread(
            get_session_store().update_reminder,
            reminder_id,
            request.title,
            request.due_at,
            request.note,
            request.advance_days,
            request.profile,
            request.repeat_days,
            request.repeat_until,
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
        raise HTTPException(
            status_code=404, detail="提醒不存在或已完成，无法推迟"
        )
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


@app.post("/api/tools/cat")
async def tool_cat(request: CatRequest):
    """CAT 慢阻肺评估测试评分。"""
    from core.clinical_tools import calculate_cat

    try:
        result = calculate_cat(request.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/ess")
async def tool_ess(request: EssRequest):
    """Epworth 嗜睡量表评分。"""
    from core.clinical_tools import calculate_ess

    try:
        result = calculate_ess(request.answers)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/mmrc")
async def tool_mmrc(request: MmrcRequest):
    """mMRC 呼吸困难量表分级。"""
    from core.clinical_tools import calculate_mmrc

    try:
        result = calculate_mmrc(request.grade)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/audit-c")
async def tool_audit_c(request: AuditCRequest):
    """AUDIT-C 酒精使用筛查评分。"""
    from core.clinical_tools import calculate_audit_c

    try:
        result = calculate_audit_c(
            request.q1, request.q2, request.q3, request.sex
        )
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
    )
    if not drugs:
        raise HTTPException(status_code=400, detail="请至少填写一种药物")
    if len(drugs) > 500:
        raise HTTPException(
            status_code=400, detail="药物列表过长，请控制在 500 字以内"
        )

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
        async with state.kb_lock:
            return await asyncio.to_thread(service.list_documents)
    except Exception as e:
        logger.exception("读取知识库失败")
        raise HTTPException(status_code=500, detail=f"读取知识库失败：{e}")


@app.post("/api/knowledge/rebuild")
async def knowledge_rebuild():
    """重建知识库索引（先删除旧 collection，避免重复导入）。"""
    from knowledge.service import get_knowledge_service

    service = get_knowledge_service()
    async with state.kb_lock:
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
    request: Request,
    file: UploadFile = File(...),
    rebuild: bool = True
):
    """上传 TXT / Markdown / PDF 文档，可选立即重建索引。"""
    from knowledge.service import get_knowledge_service

    if _request_content_length(request) > MAX_UPLOAD_BYTES + 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大（最大 5 MB）")
    service = get_knowledge_service()
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件过大（最大 5 MB）")
    try:
        saved = await asyncio.to_thread(service.save_upload, file.filename, content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("保存知识库文档失败")
        raise HTTPException(status_code=500, detail=f"保存文档失败：{e}")

    result = None
    if rebuild:
        async with state.kb_lock:
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
    request: Request,
    files: List[UploadFile] = File(...),
    rebuild: bool = True
):
    """一次上传多个 TXT / Markdown / PDF 文档，全部保存后只重建一次索引。"""
    from knowledge.service import get_knowledge_service

    if not files:
        raise HTTPException(status_code=400, detail="请选择要上传的文件")
    if len(files) > 10:
        raise HTTPException(status_code=400, detail="一次最多上传 10 个文件")
    if _request_content_length(request) > MAX_UPLOAD_BATCH_BYTES + 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="上传内容过大")

    service = get_knowledge_service()
    saved: List[Dict[str, Any]] = []
    failed: List[Dict[str, str]] = []

    for file in files:
        try:
            content = await file.read(MAX_UPLOAD_BYTES + 1)
            if len(content) > MAX_UPLOAD_BYTES:
                raise ValueError("文件过大（最大 5 MB）")
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
        async with state.kb_lock:
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
        async with state.kb_lock:
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
        async with state.kb_lock:
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

        # 等待会诊锁期间也要保持心跳：另一次会诊可能运行数十秒，
        # 期间静默会让代理/浏览器读超时断流
        while True:
            try:
                await asyncio.wait_for(state.lock.acquire(), timeout=10)
                break
            except asyncio.TimeoutError:
                yield ": keep-alive\n\n"
        lock_released = False

        def release_lock(_task=None):
            nonlocal lock_released
            if lock_released:
                return
            lock_released = True
            state.lock.release()

        try:
            coordinator = state.get_coordinator()
        except Exception as e:
            release_lock()
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
        try:
            task = asyncio.create_task(
                coordinator.process(**stream_kwargs)
            )
        except Exception:
            release_lock()
            raise
        # 锁只在会诊执行期间持有；任务结束（成功/失败/取消）立即释放，
        # 不再因为客户端读取慢而在 yield 上一直占着全局锁
        task.add_done_callback(release_lock)

        seen_agents = set()
        started_agents = set()
        last_keepalive = asyncio.get_event_loop().time()
        try:
            while not task.done() or not token_queue.empty():
                # worker 在 swarm 模式下会被挂上 SharedContext，据此读取进展
                for worker in getattr(coordinator, "worker_pool", []):
                    sc = getattr(worker, "shared_context", None)
                    if sc is None or id(sc) in stale_contexts:
                        continue

                    agent_subtasks: Dict[str, List[str]] = {}
                    for subtask in list(
                        getattr(sc, "task_decomposition", {}).values()
                    ):
                        agent_id = getattr(subtask, "assigned_agent", None)
                        status = getattr(
                            getattr(subtask, "status", None), "value", ""
                        )
                        if not agent_id:
                            continue
                        agent_subtasks.setdefault(agent_id, []).append(status)
                        if (
                            status == "in_progress"
                            and agent_id not in started_agents
                        ):
                            started_agents.add(agent_id)
                            yield sse({
                                "type": "agent_started",
                                "agent_id": agent_id,
                                "name": AGENT_LABELS.get(agent_id, agent_id),
                            })

                    # 同一 Agent 的全部子任务都结束后再推 agent_done；
                    # 多子任务时不能在第一个完成就点亮，快速任务也补发开始事件
                    for agent_id, statuses in agent_subtasks.items():
                        if agent_id in seen_agents:
                            continue
                        if not statuses or not all(
                            status in ("completed", "failed")
                            for status in statuses
                        ):
                            continue
                        if agent_id not in started_agents:
                            started_agents.add(agent_id)
                            yield sse({
                                "type": "agent_started",
                                "agent_id": agent_id,
                                "name": AGENT_LABELS.get(agent_id, agent_id),
                            })
                        seen_agents.add(agent_id)
                        yield sse({
                            "type": "agent_done",
                            "agent_id": agent_id,
                            "name": AGENT_LABELS.get(agent_id, agent_id),
                        })

                # 进展事件之后再推送汇总阶段已经产生的增量文本
                while not token_queue.empty():
                    yield sse({
                        "type": "token",
                        "delta": token_queue.get_nowait(),
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
