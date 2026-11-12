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
import math
import os
import re
import sys
import tempfile
import time
import hashlib
import ipaddress
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, AsyncIterator, Dict, List, Literal, Optional, Tuple
from urllib.parse import urlsplit

from fastapi import (
    FastAPI, File, Form, HTTPException, Query, Request, UploadFile,
)
from fastapi import Path as ApiPath
from fastapi.encoders import jsonable_encoder
from fastapi.exception_handlers import http_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, Field, field_validator, model_validator
from starlette.exceptions import HTTPException as StarletteHTTPException

project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Windows 上 stdout 被重定向时默认用 GBK，日志里的 emoji 会抛 UnicodeEncodeError
from core.console import ensure_utf8_stdio

ensure_utf8_stdio()

from core.security import (
    configure_server_logging,
    install_log_redaction,
    redact_secrets,
    sanitize_base_url,
)
install_log_redaction()
configure_server_logging()

from core.auth import get_access_token, is_authorized, request_token
from core.retention import get_auto_archive_days
from core.triage import detect_red_flags
from core.medication_safety import check_medication_safety
from core.llm_client import LLMError
from core.ratelimit import SlidingWindowLimiter, get_rate_limit
from core.fs_permissions import restrict_permissions
from core.pricing import get_pricing
from core.budget import get_token_budget
from core.serialization import (
    contains_lone_surrogate,
    sanitize_json,
    strip_lone_surrogates,
)
from core.session_context import session_memory_scope
from core.backup import MAX_BACKUP_FILE_BYTES

WEB_DIR = project_root / "web"


def _compute_web_build_id() -> str:
    """用前端资源内容生成构建号，供缓存版本与更新提示使用。"""
    digest = hashlib.sha256()
    for name in (
        "index.html", "app.js", "lab-prefill-core.js", "styles.css",
        "theme-init.js", "sw.js", "manifest.webmanifest",
    ):
        path = WEB_DIR / name
        try:
            digest.update(path.read_bytes())
        except OSError:
            continue
    return digest.hexdigest()[:12]


WEB_BUILD_ID = _compute_web_build_id()

# Agent 展示名，供前端协作面板使用
AGENT_LABELS = {
    "consultation_agent": "健康咨询",
    "diagnostic_agent": "症状诊断",
    "research_agent": "医学研究",
    "lead_agent": "任务协调",
}


# ===== 请求 / 响应模型 =====

# SQLite INTEGER 是有符号 64 位；超出范围的值在绑定参数时抛 OverflowError
MAX_SQLITE_INT = 2**63 - 1
#: 路径里的数据库 ID：必须是正整数且落在 SQLite 可绑定范围内
SafeIntPath = Annotated[int, ApiPath(ge=1, le=MAX_SQLITE_INT)]


def _reject_lone_surrogates(value: Any, path: str = "输入") -> None:
    """递归拒绝含孤立 UTF-16 代理码位的字符串。

    JSON 允许 ``\\ud800`` 这样的转义，但孤立代理码位无法编码成 UTF-8，
    会在哈希命名空间或写入 SQLite 时抛 UnicodeEncodeError（HTTP 500）。
    """
    if isinstance(value, str):
        if contains_lone_surrogate(value):
            raise ValueError(f"{path}包含无效的 Unicode 字符")
    elif isinstance(value, dict):
        for key, item in value.items():
            if isinstance(key, str) and contains_lone_surrogate(key):
                raise ValueError(f"{path}包含无效的 Unicode 字符")
            _reject_lone_surrogates(item, path)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _reject_lone_surrogates(item, path)


class StrictRequestModel(BaseModel):
    """请求体基类：在进入业务逻辑前拒绝孤立代理码位。"""

    @model_validator(mode="before")
    @classmethod
    def _validate_strings(cls, data):
        _reject_lone_surrogates(data)
        return data


def _bounded_offset(offset: Any) -> int:
    """把分页偏移限制在 SQLite 可绑定范围内，避免超大整数触发 500。"""
    return max(0, min(int(offset or 0), MAX_SQLITE_INT))


class ChatRequest(StrictRequestModel):
    question: str = Field(
        ..., min_length=1, max_length=2000, description="用户健康问题"
    )
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

    @model_validator(mode="after")
    def _validate_context_size(self):
        """限制追问上下文体积，避免同一段文本被多次拼进提示词。"""
        if self.context is None:
            return self
        if len(self.context) > 50:
            raise ValueError("上下文项过多")
        try:
            serialized = json.dumps(
                self.context, ensure_ascii=False, default=str
            )
        except (TypeError, ValueError):
            raise ValueError("上下文包含无法序列化的内容")
        if len(serialized) > 8000:
            raise ValueError("上下文内容过长")
        return self


class SessionResponse(BaseModel):
    session_id: str


class RenameSessionRequest(StrictRequestModel):
    title: str = Field(..., min_length=1, max_length=40, description="新的会话标题")


class ImportSessionsRequest(StrictRequestModel):
    version: Optional[int] = None
    sessions: List[Dict[str, Any]] = Field(default_factory=list)
    profiles: Optional[List[Dict[str, Any]]] = None
    health_metrics: Optional[List[Dict[str, Any]]] = None
    metric_targets: Optional[List[Dict[str, Any]]] = None
    reminders: Optional[List[Dict[str, Any]]] = None
    symptoms: Optional[List[Dict[str, Any]]] = None
    medications: Optional[List[Dict[str, Any]]] = None
    medication_logs: Optional[List[Dict[str, Any]]] = None
    medication_history: Optional[List[Dict[str, Any]]] = None
    vaccinations: Optional[List[Dict[str, Any]]] = None
    appointments: Optional[List[Dict[str, Any]]] = None
    lab_results: Optional[List[Dict[str, Any]]] = None
    allergies: Optional[List[Dict[str, Any]]] = None
    care_items: Optional[List[Dict[str, Any]]] = None
    visit_records: Optional[List[Dict[str, Any]]] = None
    conditions: Optional[List[Dict[str, Any]]] = None
    audit_log: Optional[List[Dict[str, Any]]] = None
    checksum: Optional[str] = None


class FavoriteRequest(StrictRequestModel):
    favorite: bool


class ArchiveRequest(StrictRequestModel):
    archived: bool


class TagsRequest(StrictRequestModel):
    tags: List[str] = Field(default_factory=list)


class PurgeRequest(StrictRequestModel):
    days: int = Field(..., ge=1, le=3650, description="删除多少天以前的会诊")


class ConfirmRequest(StrictRequestModel):
    confirm: bool = Field(False, description="确认执行该操作")


class AttachmentCleanupRequest(StrictRequestModel):
    confirm: bool = Field(False, description="确认清理孤儿文件")
    expected_orphans: int = Field(
        ..., ge=0, description="预检时看到的孤儿文件数量"
    )


class AttachmentLinkRequest(StrictRequestModel):
    ids: List[int] = Field(
        ..., min_length=1, max_length=200, description="附件 ID 列表"
    )
    ref_id: int = Field(
        0,
        ge=0,
        le=MAX_SQLITE_INT,
        description="关联的就诊/化验记录 ID，0 表示取消关联",
    )


class AttachmentNoteRequest(StrictRequestModel):
    note: str = Field("", max_length=300, description="附件备注")


class BulkSessionsRequest(StrictRequestModel):
    ids: List[str] = Field(..., min_length=1, max_length=200)


class BulkArchiveRequest(BulkSessionsRequest):
    archived: bool


class BulkTagsRequest(BulkSessionsRequest):
    tags: List[str] = Field(default_factory=list)


class FeedbackRequest(StrictRequestModel):
    message_id: int = Field(
        ..., ge=1, le=MAX_SQLITE_INT, description="会诊答复的消息 id"
    )
    rating: int = Field(
        ...,
        ge=-1,
        le=1,
        description="1 有用，-1 待改进，0 清除反馈",
    )
    comment: str = Field("", max_length=500, description="可选反馈备注")


class ProfileRequest(StrictRequestModel):
    name: str = Field(..., min_length=1, max_length=40, description="档案名称")
    context: Dict[str, Any] = Field(
        default_factory=dict,
        description="年龄、性别、病程、既往史、用药与过敏史",
    )


class ProfileRenameRequest(StrictRequestModel):
    new_name: str = Field(..., min_length=1, max_length=40)


class ReminderRequest(StrictRequestModel):
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
        0, ge=0, le=MAX_SQLITE_INT, description="关联的用药记录 ID（用药提醒）"
    )
    vaccination_id: int = Field(
        0, ge=0, le=MAX_SQLITE_INT, description="关联的疫苗接种记录 ID"
    )
    appointment_id: int = Field(
        0, ge=0, le=MAX_SQLITE_INT, description="关联的就诊预约 ID"
    )


class ReminderUpdateRequest(StrictRequestModel):
    title: Optional[str] = Field(None, max_length=120)
    due_at: Optional[str] = Field(None, max_length=32)
    note: Optional[str] = Field(None, max_length=300)
    advance_days: Optional[int] = Field(None, ge=0, le=30)
    profile: Optional[str] = Field(None, max_length=40)
    repeat_days: Optional[int] = Field(None, ge=0, le=365)
    repeat_until: Optional[str] = Field(None, max_length=32)


class BmiRequest(StrictRequestModel):
    height_cm: float = Field(..., gt=50, lt=260, description="身高（厘米）")
    weight_kg: float = Field(..., gt=10, lt=400, description="体重（千克）")


class EgfrRequest(StrictRequestModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    sex: Literal["male", "female"]
    creatinine: float = Field(..., gt=0, lt=3000, description="血肌酐")
    unit: Literal["mg/dL", "umol/L", "μmol/L", "µmol/L"] = "mg/dL"


class ChadsVascRequest(StrictRequestModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    sex: Literal["male", "female"]
    chf: bool = False
    hypertension: bool = False
    diabetes: bool = False
    stroke_tia: bool = False
    vascular_disease: bool = False


class Curb65Request(StrictRequestModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    confusion: bool = False
    urea: float = Field(..., ge=0, le=100, description="尿素氮（mmol/L）")
    respiratory_rate: float = Field(..., ge=0, le=100, description="呼吸频率（次/分）")
    systolic_bp: float = Field(..., ge=0, le=300, description="收缩压（mmHg）")
    diastolic_bp: float = Field(..., ge=0, le=200, description="舒张压（mmHg）")


class ChildPughRequest(StrictRequestModel):
    bilirubin: int = Field(..., ge=1, le=3)
    albumin: int = Field(..., ge=1, le=3)
    inr: int = Field(..., ge=1, le=3)
    ascites: int = Field(..., ge=1, le=3)
    encephalopathy: int = Field(..., ge=1, le=3)


class Phq9Request(StrictRequestModel):
    answers: List[int] = Field(..., min_length=9, max_length=9)


class Gad7Request(StrictRequestModel):
    answers: List[int] = Field(..., min_length=7, max_length=7)


class AuditCRequest(StrictRequestModel):
    q1: Literal[0, 1, 2, 3, 4]
    q2: Literal[0, 1, 2, 3, 4]
    q3: Literal[0, 1, 2, 3, 4]
    sex: Literal["", "male", "female"] = ""


class MmrcRequest(StrictRequestModel):
    grade: Literal[0, 1, 2, 3, 4]


class CatRequest(StrictRequestModel):
    answers: List[int] = Field(..., min_length=8, max_length=8)


class EssRequest(StrictRequestModel):
    answers: List[int] = Field(..., min_length=8, max_length=8)


class HasBledRequest(StrictRequestModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    hypertension: bool = False
    abnormal_renal: bool = False
    abnormal_liver: bool = False
    stroke_history: bool = False
    bleeding_history: bool = False
    labile_inr: bool = False
    drugs: bool = False
    alcohol: bool = False


class GcsRequest(StrictRequestModel):
    eye: int = Field(..., ge=1, le=4, description="睁眼反应")
    verbal: int = Field(..., ge=1, le=5, description="语言反应")
    motor: int = Field(..., ge=1, le=6, description="运动反应")


class WellsDvtRequest(StrictRequestModel):
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


class WellsPeRequest(StrictRequestModel):
    clinical_signs_dvt: bool = False
    pe_more_likely: bool = False
    heart_rate_gt_100: bool = False
    immobilization_or_surgery: bool = False
    previous_dvt_pe: bool = False
    hemoptysis: bool = False
    malignancy: bool = False


class InteractionsRequest(StrictRequestModel):
    drugs: str = Field(
        ..., min_length=1, max_length=1000, description="药物列表，可换行或顿号分隔"
    )
    allergies: str = Field("", max_length=200, description="可选过敏史")


class QsofaRequest(StrictRequestModel):
    respiratory_rate: float = Field(..., gt=0, le=80, description="呼吸频率（次/分）")
    systolic_bp: float = Field(..., gt=0, le=300, description="收缩压（mmHg）")
    altered_mentation: bool = False


class Fib4Request(StrictRequestModel):
    age: int = Field(..., ge=18, le=120, description="年龄（岁）")
    ast: float = Field(..., gt=0, le=10000, description="AST（U/L）")
    alt: float = Field(..., gt=0, le=10000, description="ALT（U/L）")
    platelets: float = Field(..., gt=0, le=2000, description="血小板（×10⁹/L）")


class MeldRequest(StrictRequestModel):
    bilirubin: float = Field(..., gt=0, le=100, description="总胆红素（mg/dL）")
    inr: float = Field(..., gt=0, le=20, description="INR")
    creatinine: float = Field(..., gt=0, le=30, description="血肌酐（mg/dL）")
    dialysis: bool = False


class ApriRequest(StrictRequestModel):
    ast: float = Field(..., gt=0, le=10000, description="AST（U/L）")
    platelets: float = Field(..., gt=0, le=2000, description="血小板（×10⁹/L）")
    ast_uln: float = Field(40, gt=0, le=1000, description="AST 正常上限（U/L）")


class EagRequest(StrictRequestModel):
    hba1c: float = Field(..., ge=3, le=20, description="HbA1c（%）")


class StopBangRequest(StrictRequestModel):
    snoring: bool = False
    tired: bool = False
    observed_apnea: bool = False
    hypertension: bool = False
    bmi_gt_35: bool = False
    age_gt_50: bool = False
    neck_gt_40cm: bool = False
    male: bool = False


class PaduaRequest(StrictRequestModel):
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


class Abcd2Request(StrictRequestModel):
    age_ge_60: bool = False
    bp_ge_140_90: bool = False
    unilateral_weakness: bool = False
    speech_disturbance: bool = False
    duration_ge_60: bool = False
    duration_10_59: bool = False
    diabetes: bool = False


class MorseFallRequest(StrictRequestModel):
    history_of_falling: Literal[0, 25] = 0
    secondary_diagnosis: Literal[0, 15] = 0
    ambulatory_aid: Literal[0, 15, 30] = 0
    intravenous: Literal[0, 20] = 0
    gait: Literal[0, 10, 20] = 0
    mental_status: Literal[0, 15] = 0


class MetricRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    metric: Literal[
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
    ]
    value1: float
    value2: Optional[float] = None
    measured_at: str = Field("", max_length=40, description="测量时间（ISO）")
    context: str = Field(
        "", max_length=20,
        description="测量情境：bp=morning/evening，glucose=fasting/postprandial/bedtime/random",
    )
    note: str = Field("", max_length=300)


class QuickParseRequest(StrictRequestModel):
    text: str = Field(
        ..., min_length=1, max_length=120,
        description="一句话健康记录，如：血压 150/95 早上",
    )


class MetricUpdateRequest(StrictRequestModel):
    """部分更新健康指标：只修改请求里显式给出的字段。"""

    profile: Optional[str] = Field(None, max_length=40)
    metric: Optional[Literal[
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
    ]] = None
    value1: Optional[float] = None
    value2: Optional[float] = None
    measured_at: Optional[str] = Field(None, max_length=40)
    context: Optional[str] = Field(None, max_length=20)
    note: Optional[str] = Field(None, max_length=300)






class MedicationRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    name: str = Field(..., min_length=1, max_length=120)
    dose: str = Field("", max_length=60, description="剂量，如 5 mg")
    frequency: str = Field("", max_length=60, description="频次，如 每日一次")
    note: str = Field("", max_length=300)
    active: bool = True
    stock: Optional[float] = Field(
        None, ge=0, le=1000000, description="剩余库存；空表示不管理库存"
    )
    stock_unit: str = Field("", max_length=10, description="库存单位，如 片")
    low_stock_threshold: float = Field(
        0, ge=0, le=1000000, description="低库存阈值"
    )
    expiry_date: str = Field(
        "", max_length=40, description="有效期 YYYY-MM-DD"
    )
    started_at: str = Field("", max_length=40, description="开始用药日期")
    stopped_at: str = Field("", max_length=40, description="停药日期")
    stop_reason: str = Field("", max_length=200, description="停药原因")


class MedicationLogRequest(StrictRequestModel):
    taken_at: str = Field("", max_length=40, description="服用时间（ISO），默认当前时间")
    note: str = Field("", max_length=300)


class MedicationSkipRequest(StrictRequestModel):
    reason: Literal[
        "forget", "side_effect", "worry_side_effect",
        "cost", "inconvenient", "other",
    ] = Field(..., description="未服原因")
    skipped_at: str = Field(
        "", max_length=40, description="未服时间（ISO），默认当前时间"
    )
    note: str = Field("", max_length=300)


class MedicationUpdateRequest(StrictRequestModel):
    """部分更新用药记录：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    dose: Optional[str] = Field(None, max_length=60)
    frequency: Optional[str] = Field(None, max_length=60)
    note: Optional[str] = Field(None, max_length=300)
    active: Optional[bool] = None
    stock: Optional[float] = Field(None, ge=0, le=1000000)
    stock_unit: Optional[str] = Field(None, max_length=10)
    low_stock_threshold: Optional[float] = Field(None, ge=0, le=1000000)
    expiry_date: Optional[str] = Field(None, max_length=40)
    started_at: Optional[str] = Field(None, max_length=40)
    stopped_at: Optional[str] = Field(None, max_length=40)
    stop_reason: Optional[str] = Field(None, max_length=200)


class VaccinationRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    vaccine_name: str = Field(..., min_length=1, max_length=80)
    dose_number: int = Field(1, ge=1, le=20, description="第几剂")
    vaccinated_at: str = Field("", max_length=40, description="接种日期")
    next_due: str = Field("", max_length=40, description="下一剂应种日期")
    note: str = Field("", max_length=300)


class VaccinationUpdateRequest(StrictRequestModel):
    """部分更新疫苗接种记录：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    vaccine_name: Optional[str] = Field(None, min_length=1, max_length=80)
    dose_number: Optional[int] = Field(None, ge=1, le=20)
    vaccinated_at: Optional[str] = Field(None, max_length=40)
    next_due: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)


class AppointmentRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    title: str = Field(..., min_length=1, max_length=120)
    department: str = Field("", max_length=60, description="科室")
    doctor: str = Field("", max_length=60, description="医生")
    location: str = Field("", max_length=60, description="就诊地点")
    scheduled_at: str = Field(
        ..., min_length=8, max_length=40, description="就诊时间"
    )
    status: str = Field("upcoming", max_length=20)
    note: str = Field("", max_length=300)


class AppointmentUpdateRequest(StrictRequestModel):
    """部分更新就诊预约：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    department: Optional[str] = Field(None, max_length=60)
    doctor: Optional[str] = Field(None, max_length=60)
    location: Optional[str] = Field(None, max_length=60)
    scheduled_at: Optional[str] = Field(None, min_length=8, max_length=40)
    status: Optional[str] = Field(None, max_length=20)
    note: Optional[str] = Field(None, max_length=300)


class LabResultRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    panel: str = Field("", max_length=60, description="化验分组，如肝功能")
    item: str = Field(..., min_length=1, max_length=80, description="化验项目")
    value: float = Field(..., ge=-1000000000, le=1000000000)
    unit: str = Field("", max_length=20)
    ref_low: Optional[float] = Field(None, ge=-1000000000, le=1000000000)
    ref_high: Optional[float] = Field(None, ge=-1000000000, le=1000000000)
    tested_at: str = Field("", max_length=40, description="检测时间")
    lab_name: str = Field("", max_length=80, description="化验机构")
    note: str = Field("", max_length=300)


class LabResultUpdateRequest(StrictRequestModel):
    """部分更新化验结果：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    panel: Optional[str] = Field(None, max_length=60)
    item: Optional[str] = Field(None, min_length=1, max_length=80)
    value: Optional[float] = Field(None, ge=-1000000000, le=1000000000)
    unit: Optional[str] = Field(None, max_length=20)
    ref_low: Optional[float] = Field(None, ge=-1000000000, le=1000000000)
    ref_high: Optional[float] = Field(None, ge=-1000000000, le=1000000000)
    tested_at: Optional[str] = Field(None, max_length=40)
    lab_name: Optional[str] = Field(None, max_length=80)
    note: Optional[str] = Field(None, max_length=300)


class AllergyRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    substance: str = Field(..., min_length=1, max_length=80, description="过敏原")
    reaction: str = Field("", max_length=120, description="过敏反应")
    severity: str = Field("moderate", max_length=20)
    kind: str = Field("drug", max_length=20)
    active: bool = True
    noted_at: str = Field("", max_length=40, description="发现时间")
    note: str = Field("", max_length=300)


class AllergyUpdateRequest(StrictRequestModel):
    """部分更新过敏史：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    substance: Optional[str] = Field(None, min_length=1, max_length=80)
    reaction: Optional[str] = Field(None, max_length=120)
    severity: Optional[str] = Field(None, max_length=20)
    kind: Optional[str] = Field(None, max_length=20)
    active: Optional[bool] = None
    noted_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)


class CareItemRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    title: str = Field(..., min_length=1, max_length=120)
    interval_days: int = Field(30, ge=1, le=3650)
    last_done_at: str = Field("", max_length=40)
    next_due_at: str = Field("", max_length=40)
    note: str = Field("", max_length=300)
    active: bool = True


class CareItemUpdateRequest(StrictRequestModel):
    """部分更新复查计划：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    title: Optional[str] = Field(None, min_length=1, max_length=120)
    interval_days: Optional[int] = Field(None, ge=1, le=3650)
    last_done_at: Optional[str] = Field(None, max_length=40)
    next_due_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)
    active: Optional[bool] = None


class CareDoneRequest(StrictRequestModel):
    done_at: str = Field("", max_length=40)


class VisitRecordRequest(StrictRequestModel):
    profile: str = Field("", max_length=40)
    appointment_id: int = Field(0, ge=0, le=MAX_SQLITE_INT)
    visit_date: str = Field("", max_length=40)
    department: str = Field("", max_length=60)
    doctor: str = Field("", max_length=60)
    facility: str = Field("", max_length=80)
    diagnosis: str = Field("", max_length=300)
    advice: str = Field("", max_length=1000)
    prescription: str = Field("", max_length=1000)
    next_followup_date: str = Field("", max_length=40)
    note: str = Field("", max_length=300)


class VisitRecordUpdateRequest(StrictRequestModel):
    profile: Optional[str] = Field(None, max_length=40)
    appointment_id: Optional[int] = Field(None, ge=0, le=MAX_SQLITE_INT)
    visit_date: Optional[str] = Field(None, max_length=40)
    department: Optional[str] = Field(None, max_length=60)
    doctor: Optional[str] = Field(None, max_length=60)
    facility: Optional[str] = Field(None, max_length=80)
    diagnosis: Optional[str] = Field(None, max_length=300)
    advice: Optional[str] = Field(None, max_length=1000)
    prescription: Optional[str] = Field(None, max_length=1000)
    next_followup_date: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)


class ConditionRequest(StrictRequestModel):
    profile: str = Field("", max_length=40)
    name: str = Field(..., min_length=1, max_length=120)
    icd10: str = Field("", max_length=20)
    diagnosed_at: str = Field("", max_length=40)
    status: str = Field("active", max_length=20)
    severity: str = Field("moderate", max_length=20)
    note: str = Field("", max_length=300)


class ConditionUpdateRequest(StrictRequestModel):
    profile: Optional[str] = Field(None, max_length=40)
    name: Optional[str] = Field(None, min_length=1, max_length=120)
    icd10: Optional[str] = Field(None, max_length=20)
    diagnosed_at: Optional[str] = Field(None, max_length=40)
    status: Optional[str] = Field(None, max_length=20)
    severity: Optional[str] = Field(None, max_length=20)
    note: Optional[str] = Field(None, max_length=300)


class SymptomRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    symptom: str = Field(..., min_length=1, max_length=120)
    severity: int = Field(..., ge=0, le=10, description="严重程度 0–10")
    recorded_at: str = Field("", max_length=40, description="记录时间（ISO）")
    note: str = Field("", max_length=300)


class SymptomUpdateRequest(StrictRequestModel):
    """部分更新症状记录：未给出的字段保持原值。"""

    profile: Optional[str] = Field(None, max_length=40)
    symptom: Optional[str] = Field(None, min_length=1, max_length=120)
    severity: Optional[int] = Field(None, ge=0, le=10)
    recorded_at: Optional[str] = Field(None, max_length=40)
    note: Optional[str] = Field(None, max_length=300)


class MetricTargetRequest(StrictRequestModel):
    profile: str = Field("", max_length=40, description="关联的患者档案名称")
    metric: Literal[
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
    ]
    value1: float = Field(
        ...,
        description=(
            "目标值（默认上限；血氧/睡眠/步数/饮水量为下限，血压为收缩压）"
        ),
    )
    value2: Optional[float] = Field(None, description="血压目标舒张压")


class UpdateDocumentRequest(StrictRequestModel):
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
        self.warmup_task = None

    def get_coordinator(self):
        if self.coordinator is None:
            from swarm import SwarmCoordinator
            coordinator = SwarmCoordinator(enable_swarm=True)
            self.coordinator = coordinator
            # 启动时失败、后续懒加载成功：清除错误状态，/api/status 恢复 ok
            self.startup_error = None
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

#: SSE 等锁：单次等待切片与总等待上限
STREAM_LOCK_ACQUIRE_SECONDS = 10
STREAM_LOCK_TIMEOUT_SECONDS = 300


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


AUTO_ARCHIVE_INTERVAL_SECONDS = 6 * 60 * 60


async def auto_archive_loop() -> None:
    """定期执行自动归档；只在启动时执行一次会让长期运行的实例漏掉归档。"""
    while True:
        await auto_archive_sessions()
        await asyncio.sleep(AUTO_ARCHIVE_INTERVAL_SECONDS)


async def auto_backup_once():
    """执行一次本地备份；未配置间隔时返回 None。"""
    from core.backup import create_backup, get_auto_backup_hours

    if get_auto_backup_hours() <= 0:
        return None
    from memory import get_session_store

    payload = await asyncio.to_thread(get_session_store().export_data)
    return await asyncio.to_thread(create_backup, payload)


def auto_backup_wait_seconds(hours: int) -> float:
    """距下一份自动备份还需等待的秒数；没有备份或已到期返回 0。"""
    from core.backup import latest_backup

    latest = latest_backup()
    if latest is None:
        return 0.0
    try:
        age = time.time() - latest.stat().st_mtime
    except OSError:
        return 0.0
    # 文件时间戳精度可能让 age 略小于 0，等待时间不应超过一个间隔
    return min(hours * 3600, max(0.0, hours * 3600 - age))


async def auto_backup_loop() -> None:
    """按 MEDIX_AUTO_BACKUP_HOURS 定期备份；关闭时每小时检查一次配置。"""
    from core.backup import get_auto_backup_hours

    while True:
        try:
            hours = get_auto_backup_hours()
            if hours <= 0:
                await asyncio.sleep(3600)
                continue
            interval = hours * 3600
            wait = await asyncio.to_thread(
                auto_backup_wait_seconds, hours
            )
            if wait > 0:
                # 进程重启不应额外生成备份，否则连续重启会把历史快照
                # 按份数挤出保留窗口，只剩同一天的重复文件
                await asyncio.sleep(wait)
                continue
            try:
                path = await auto_backup_once()
                if path:
                    logger.info(f"本地自动备份完成: {path}")
            except Exception as e:
                logger.warning(f"本地自动备份失败: {e}")
            await asyncio.sleep(interval)
        except Exception as e:
            # 备份目录不可访问/配置异常不应让任务永久退出
            logger.warning(f"自动备份循环异常，稍后重试: {e}")
            await asyncio.sleep(3600)


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


_CSV_DANGEROUS_LEADING = ("=", "+", "-", "@", "\t", "\r")


def csv_safe_cell(value: Any) -> str:
    """防止 CSV 单元格被表格软件当作公式执行。

    以撇号开头的真实数据同样加一层撇号，保证导出→导入可无损还原。
    """
    text = str(value if value is not None else "")
    if text[:1] in _CSV_DANGEROUS_LEADING or text[:1] == "'":
        return "'" + text
    return text


def csv_restore_cell(value: Any) -> str:
    """还原导出时为防止公式执行而加上的前导撇号。"""
    text = str(value if value is not None else "")
    if text[:1] == "'" and (
        text[1:2] == "'" or text[1:2] in _CSV_DANGEROUS_LEADING
    ):
        return text[1:]
    return text


def csv_number(value: Any) -> str:
    """导出浮点数时保留可往返的精度（:g 会把大数/小数截成 6 位）。"""
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if not math.isfinite(number):
        return ""
    text = repr(number)
    if text.endswith(".0"):
        text = text[:-2]
    return text


#: CSV 状态列中明确表示"停用"的写法；空值与其他未知值按在用处理，
#: 避免把用户手写的变体（已停药/暂停等）误导入为在用药物
_CSV_INACTIVE_STATUS = {
    "停用", "已停用", "停用中", "已停药", "停药", "暂停", "已暂停",
    "停服", "已停服", "0", "false", "no", "否",
}


def csv_active_status(value: Any) -> bool:
    """解析 CSV 状态列：只有明确的停用值才视为停用，空值默认为在用。"""
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return True
    return text not in _CSV_INACTIVE_STATUS


#: CSV 导入模板：列名与导出文件保持一致，示例行帮助用户直接开始录入
CSV_IMPORT_TEMPLATES: Dict[str, Dict[str, Any]] = {
    "metrics": {
        "headers": ["测量时间", "患者档案", "指标", "数值1", "数值2", "单位", "情境", "备注"],
        "example": [
            "2026-01-01T08:00", "妈妈", "血压", "130", "85", "mmHg", "晨起", "晨起测量",
        ],
    },
    "symptoms": {
        "headers": ["记录时间", "患者档案", "症状", "程度", "备注"],
        "example": [
            "2026-01-01T09:00", "妈妈", "头痛", "4", "休息后缓解",
        ],
    },
    "medications": {
        "headers": [
            "药物名称", "剂量", "频次", "患者档案", "状态", "备注",
            "库存", "库存单位", "低库存阈值", "有效期",
            "开始日期", "停药日期", "停药原因",
        ],
        "example": [
            "氨氯地平", "5 mg", "每日一次", "妈妈", "在用", "早餐后",
            "30", "片", "7", "2027-01-01", "2026-01-01", "", "",
        ],
    },
    "reminders": {
        "headers": ["到期日", "内容", "档案", "状态", "提前天数", "重复天数", "备注"],
        "example": [
            "2026-01-10", "复查血压", "妈妈", "待完成", "1", "0", "带上次化验单",
        ],
    },
    "lab-results": {
        "headers": [
            "检测时间", "患者档案", "分组", "项目", "数值", "单位",
            "参考下限", "参考上限", "化验机构", "备注",
        ],
        "example": [
            "2026-01-01T08:00", "妈妈", "肝功能", "ALT", "28", "U/L",
            "7", "40", "市医院检验科", "空腹",
        ],
    },
    "allergies": {
        "headers": [
            "过敏原", "患者档案", "过敏反应", "严重程度", "类型",
            "状态", "发现时间", "备注",
        ],
        "example": [
            "青霉素", "妈妈", "皮疹", "重度", "药物", "在用",
            "2020-05-01", "曾出现全身荨麻疹",
        ],
    },
}


def _convert_import_metric(
    metric: str, unit: str, value1: float, value2: Optional[float]
) -> Optional[Tuple[float, Optional[float]]]:
    """把导入行的单位换算为系统规范单位；未知单位返回 None。"""
    from memory.session_store import METRIC_TYPES

    canonical = METRIC_TYPES.get(metric, (None, "", None))[1]
    text = (
        str(unit or "")
        .strip()
        .lower()
        .replace(" ", "")
        .replace("℃", "°c")
        .replace("℉", "°f")
    )
    if not text or text == canonical.lower():
        return value1, value2

    identity = {
        "weight": {"kg", "千克", "公斤"},
        "waist": {"cm", "厘米"},
        "steps": {"步"},
        "water": {"ml", "毫升"},
        "temperature": {"°c", "c", "摄氏", "摄氏度", "celsius"},
        "glucose": {"mmol/l", "mmol", "毫摩尔/升"},
        "bp": {"mmhg", "毫米汞柱"},
        "heart_rate": {"次/分", "bpm", "/min", "次每分"},
        "spo2": {"%", "percent", "百分比"},
        "phq9": {"分"}, "gad7": {"分"}, "cat": {"分"},
        "ess": {"分"}, "auditc": {"分"}, "mmrc": {"级"},
    }
    if text in identity.get(metric, set()):
        return value1, value2

    if metric == "water":
        factor = {"l": 1000.0, "升": 1000.0, "公升": 1000.0}.get(text)
        if factor is None:
            return None
        return value1 * factor, value2

    if metric == "weight":
        factor = {
            "lb": 0.45359237, "lbs": 0.45359237, "磅": 0.45359237,
            "斤": 0.5, "g": 0.001, "克": 0.001,
        }.get(text)
        if factor is None:
            return None
        return (
            value1 * factor,
            value2 * factor if value2 is not None else None,
        )

    if metric == "waist":
        factor = {"in": 2.54, "inch": 2.54, "英寸": 2.54, "吋": 2.54}.get(
            text
        )
        if factor is None:
            return None
        return value1 * factor, value2

    if metric == "temperature":
        if text in {"°f", "f", "华氏", "华氏度", "fahrenheit"}:
            def to_celsius(value: float) -> float:
                return (value - 32) * 5 / 9

            return (
                to_celsius(value1),
                to_celsius(value2) if value2 is not None else None,
            )
        return None

    if metric == "glucose":
        if text in {"mg/dl", "mgdl", "毫克/分升"}:
            return value1 / 18.0182, value2
        return None

    if metric == "bp":
        if text in {"kpa", "千帕"}:
            factor = 7.50062
            return (
                value1 * factor,
                value2 * factor if value2 is not None else None,
            )
        return None

    return None


_RISK_TAG_PATTERN = re.compile(
    r"(?:综合)?风险(?:等级|程度|评级)?\s*[：:为是]\s*[\*「【\s]*"
    r"(中等风险|中等|中风险|紧急|高危|中危|低危"
    r"|高(?!血压|风险|危)|中(?!等|风险|危)|低(?!危|风险))"
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
    r"(?:不属于|不是|并非|排除|未见|没有|无明显|无|非|不)"
    r"(?:明显|任何|明确|相关|特殊)?\s*$"
)
#: 「不排除/不能排除/未排除高危」是双重否定，表示风险仍然存在
_RISK_RULE_OUT_PATTERN = re.compile(
    r"(?:不|不能|无法|难以|未能|未|没有?)\s*(?:完全|明确|彻底)?\s*排除\s*$"
)
_RISK_TAG_LEVELS = {
    "紧急": "emergency",
    "高危": "high",
    "高": "high",
    "中危": "medium",
    "中等风险": "medium",
    "中等": "medium",
    "中风险": "medium",
    "中": "medium",
    "低危": "low",
    "低": "low",
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
            prefix = content[max(0, match.start() - 12):match.start()]
            # 「不排除高危」= 高危仍有可能，不能按否定跳过
            if _RISK_RULE_OUT_PATTERN.search(prefix):
                return level
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
#: 就医资料附件：本机目录与允许的扩展名
ATTACHMENT_DIR = project_root / "data" / "attachments"
MAX_ATTACHMENT_UPLOAD_BYTES = 10 * 1024 * 1024
# 图片在浏览器中解码后才占内存；限制像素规模，避免小文件解压炸弹
MAX_ATTACHMENT_PIXELS = 40_000_000
MAX_ATTACHMENT_EDGE = 20_000
# 导出附件包时的总字节上限，避免一次请求占用过多内存
MAX_ATTACHMENT_EXPORT_BYTES = 200 * 1024 * 1024


class AttachmentExportTooLarge(Exception):
    """附件包超出导出上限。"""
ATTACHMENT_MIME_BY_EXT = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".pdf": "application/pdf",
}


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
    """审计 target 只保留提醒 id，不写入提醒标题。"""
    return str(reminder.get("id") or "")


def _profile_audit_key(profile: str) -> str:
    """档案审计目标用短哈希，导出的审计/备份不包含患者姓名。"""
    from memory.session_store import _normalize_profile_name

    normalized = _normalize_profile_name(profile)
    if not normalized:
        return "未指定"
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16]
    return f"profile:{digest}"


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
        # 不直接退出：首次请求会懒加载重试，后台维护任务也应继续运行
        logger.error(f"初始化失败（将在首次请求时重试）: {e}")
    # 后台预热知识库，不阻塞服务启动；测试环境可跳过真实模型加载
    if str(os.environ.get("MEDIX_SKIP_WARMUP", "")).strip().lower() not in {
        "1", "true", "yes", "on"
    }:
        state.warmup_task = asyncio.create_task(state.warm_up())
    archive_task = asyncio.create_task(auto_archive_loop())
    backup_task = asyncio.create_task(auto_backup_loop())
    try:
        yield
    finally:
        if state.warmup_task is not None:
            state.warmup_task.cancel()
            with suppress(asyncio.CancelledError):
                await state.warmup_task
            state.warmup_task = None
        archive_task.cancel()
        backup_task.cancel()
        with suppress(asyncio.CancelledError, Exception):
            await archive_task
        with suppress(asyncio.CancelledError, Exception):
            await backup_task
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
KNOWLEDGE_WRITE_PATHS = {
    "/api/knowledge/rebuild",
    "/api/knowledge/upload",
    "/api/knowledge/upload-batch",
}
chat_limiter = SlidingWindowLimiter(get_rate_limit())
# 访问令牌错误尝试限制，避免局域网内暴力猜测
auth_failure_limiter = SlidingWindowLimiter(10, window=300)
_short_access_token_warned = False


def _warn_if_short_access_token() -> None:
    """令牌过短时提醒一次；不拒绝启动，避免影响已有部署。"""
    global _short_access_token_warned
    token = get_access_token()
    if (
        token
        and len(token) < 16
        and not _short_access_token_warned
    ):
        _short_access_token_warned = True
        logger.warning(
            "MEDIX_ACCESS_TOKEN 长度不足 16 个字符，容易被猜测，"
            "建议更换更长的随机令牌。"
        )


INSECURE_LAN_ENV = "MEDIX_ALLOW_INSECURE_LAN"


def _is_loopback_host(host: str) -> bool:
    text = str(host or "").strip().lower()
    if text in ("localhost", "::1"):
        return True
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def check_bind_security(
    host: str, token: str, allow_insecure_lan: bool = False
) -> Optional[str]:
    """绑定非本机地址且未设置访问令牌时返回错误消息；可启动时返回 None。"""
    if token or allow_insecure_lan or _is_loopback_host(host):
        return None
    return (
        f"拒绝启动：绑定到 {host} 会向局域网暴露服务，但未设置 "
        "MEDIX_ACCESS_TOKEN。请设置访问令牌；仅在完全可信的内网使用时，"
        "可用 --allow-insecure-lan 或 MEDIX_ALLOW_INSECURE_LAN=1 显式放行。"
    )


# 自检端点会真实调用一次付费模型，单独限制频率
diagnose_limiter = SlidingWindowLimiter(5)
# 重建/上传会触发全量索引，限制频率避免放大资源消耗
knowledge_limiter = SlidingWindowLimiter(20)
# 附件单文件可达 10 MB，限制上传频率避免磁盘与内存被快速填满
attachment_limiter = SlidingWindowLimiter(30)
# 导出附件包可能读取大量文件，限制并发/频率
attachment_export_limiter = SlidingWindowLimiter(6)

SECURITY_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "frame-src 'self' blob:; "
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
        # 整库备份需要可恢复；仍设上限防止超大导入长期占住数据库锁
        return MAX_BACKUP_FILE_BYTES
    if path == "/api/knowledge/upload-batch":
        return MAX_UPLOAD_BATCH_BYTES + 1024 * 1024
    if path.startswith("/api/knowledge/upload"):
        return MAX_UPLOAD_BYTES + 1024 * 1024
    if path == "/api/attachments":
        return MAX_ATTACHMENT_UPLOAD_BYTES + 1024 * 1024
    if path.startswith("/api/knowledge/documents/"):
        # 在线编辑按 update_document 的 5MB 上限，额外预留 JSON 转义开销
        return MAX_UPLOAD_BYTES + 1024 * 1024
    if path.endswith("/import"):
        return MAX_UPLOAD_BYTES + 1024 * 1024
    if path in ("/api/chat", "/api/chat/stream"):
        # 问题上限 2000 字、上下文上限 8000 字，64KB 足够且能挡住灌入
        return 64 * 1024
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


def _client_ip_key(request: Request) -> str:
    """令牌错误尝试必须按来源 IP 计数，不能按猜测值分散。"""
    host = request.client.host if request.client else "unknown"
    return f"ip:{host}"


def _is_loopback_client(request: Request) -> bool:
    """本机请求不参与令牌错误限流（本机进程本身已可信）。"""
    host = request.client.host if request.client else ""
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"testclient", "localhost"}


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


@app.exception_handler(RequestValidationError)
async def sanitized_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> Response:
    """422 详情会回显原始输入，先替换孤立代理码位再渲染。

    否则 Starlette 用 ensure_ascii=False 编码响应体时会再次抛
    UnicodeEncodeError，把本应 422 的请求变成 500。
    """
    detail = strip_lone_surrogates(jsonable_encoder(exc.errors()))
    return JSONResponse(status_code=422, content={"detail": detail})


_UNSAFE_HTTP_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def _default_http_port(scheme: str) -> Optional[int]:
    scheme = str(scheme or "").lower()
    if scheme == "http":
        return 80
    if scheme == "https":
        return 443
    return None


def _same_origin(request: Request, value: str) -> bool:
    """判断 Origin/Referer 是否与当前请求同源。"""
    try:
        parsed = urlsplit(str(value or ""))
        request_scheme = request.url.scheme.lower()
        if not parsed.scheme or parsed.hostname is None:
            return False
        if request.url.hostname is None:
            return False
        parsed_port = parsed.port or _default_http_port(parsed.scheme)
        request_port = request.url.port or _default_http_port(request_scheme)
    except (TypeError, ValueError):
        return False
    return (
        parsed.scheme.lower() == request_scheme
        and parsed.hostname == request.url.hostname
        and parsed_port == request_port
    )


def _is_cross_site_request(request: Request) -> bool:
    """拒绝带跨站来源的请求；无 Origin/Referer 的脚本客户端不受影响。"""
    site = (request.headers.get("sec-fetch-site") or "").strip().lower()
    if site == "cross-site":
        return True
    origin = request.headers.get("origin")
    if origin:
        return not _same_origin(request, origin)
    referer = request.headers.get("referer")
    if referer:
        return not _same_origin(request, referer)
    return False


def _host_header_allowed(request: Request) -> bool:
    """校验 Host 头，防止 DNS rebinding 把恶意域名解析到本机。

    IP 字面量（含局域网地址）默认允许；localhost 与 testserver 放行；
    其它域名需要在 MEDIX_ALLOWED_HOSTS 中显式配置（逗号分隔）。
    """
    host = str(request.headers.get("host") or "").strip()
    if not host:
        return False
    if host.startswith("["):
        end = host.find("]")
        hostname = host[1:end] if end != -1 else ""
    else:
        hostname = host.split(":", 1)[0]
    hostname = hostname.strip().lower().rstrip(".")
    if not hostname:
        return False
    if hostname in {"localhost", "testserver"}:
        return True
    try:
        ipaddress.ip_address(hostname)
        return True
    except ValueError:
        pass
    allowed = {
        value.strip().lower()
        for value in os.environ.get("MEDIX_ALLOWED_HOSTS", "").split(",")
        if value.strip()
    }
    return hostname in allowed


@app.middleware("http")
async def require_access_token(request: Request, call_next):
    """设置 MEDIX_ACCESS_TOKEN 后，为 API 增加可选的访问令牌校验。"""
    # 子路径部署时 request.url.path 会带 root_path 前缀，
    # 直接匹配 /api/ 会整体跳过鉴权与限流
    path = _scope_route_path(request.scope)
    if not _host_header_allowed(request):
        return add_security_headers(JSONResponse(
            status_code=421,
            content={"detail": "Host 头不受信任", "code": "host_rejected"},
        ))
    if path.startswith("/api/") and _is_cross_site_request(request):
        return add_security_headers(JSONResponse(
            status_code=403,
            content={"detail": "跨站请求被拒绝", "code": "csrf_rejected"},
        ))

    if (
        get_access_token()
        and path.startswith("/api/")
        and path not in PUBLIC_API_PATHS
    ):
        _warn_if_short_access_token()
        if not is_authorized(request):
            if not _is_loopback_client(request):
                allowed, retry_after = auth_failure_limiter.check(
                    _client_ip_key(request)
                )
                if not allowed:
                    return add_security_headers(JSONResponse(
                        status_code=429,
                        content={
                            "detail": "访问令牌错误次数过多，请稍后再试。",
                            "code": "auth_rate_limited",
                            "retry_after": round(retry_after, 1),
                        },
                        headers={
                            "Retry-After": str(
                                max(1, math.ceil(retry_after))
                            )
                        },
                    ))
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
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
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
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            ))

    knowledge_write = path in KNOWLEDGE_WRITE_PATHS or (
        path.startswith("/api/knowledge/documents/")
        and request.method in ("PUT", "DELETE")
    )
    if knowledge_write:
        client_key = _rate_limit_key(request)
        allowed, retry_after = knowledge_limiter.check(client_key)
        if not allowed:
            return add_security_headers(JSONResponse(
                status_code=429,
                content={
                    "detail": "知识库操作过于频繁，请稍后再试。",
                    "code": "rate_limited",
                    "retry_after": round(retry_after, 1),
                },
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            ))

    if path == "/api/attachments/export.zip":
        client_key = _rate_limit_key(request)
        allowed, retry_after = attachment_export_limiter.check(client_key)
        if not allowed:
            return add_security_headers(JSONResponse(
                status_code=429,
                content={
                    "detail": "附件导出过于频繁，请稍后再试。",
                    "code": "rate_limited",
                    "retry_after": round(retry_after, 1),
                },
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            ))

    if path == "/api/attachments" and request.method == "POST":
        client_key = _rate_limit_key(request)
        allowed, retry_after = attachment_limiter.check(client_key)
        if not allowed:
            return add_security_headers(JSONResponse(
                status_code=429,
                content={
                    "detail": "附件上传过于频繁，请稍后再试。",
                    "code": "rate_limited",
                    "retry_after": round(retry_after, 1),
                },
                headers={"Retry-After": str(max(1, math.ceil(retry_after)))},
            ))

    response = add_security_headers(
        await call_next(request),
        include_csp=path not in DOC_PATHS,
    )
    if path.startswith("/api/"):
        # 会诊与健康数据不应被浏览器或中间代理缓存
        response.headers.setdefault("Cache-Control", "no-store")
    return response


# ===== API =====

@app.get("/api/auth/verify")
async def verify_access_token():
    """受保护的轻量接口，供前端确认访问令牌是否有效。"""
    return {"ok": True}


@app.get("/api/templates/{kind}")
async def download_csv_template(kind: str):
    """下载健康数据导入模板（带 BOM，列名与导入器一致）。"""
    import csv
    import io

    template = CSV_IMPORT_TEMPLATES.get(str(kind or "").strip().lower())
    if template is None:
        raise HTTPException(status_code=404, detail="模板不存在")
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(
        [template["headers"], template["example"]]
    )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition":
                f'attachment; filename="medix-{kind}-template.csv"',
            "Cache-Control": "no-store",
        },
    )


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
            csv_safe_cell(item.get("created_at", "")),
            csv_safe_cell(item.get("action", "")),
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
async def get_status(request: Request):
    """健康检查 + 配置状态，前端用它决定是否显示警告。"""
    from core.llm_client import LLM_CONFIG

    api_key = (LLM_CONFIG.get("api_key") or "").strip()
    mem0_enabled = False
    if state.coordinator is not None:
        # 测试或自定义协调器可能没有长期记忆字段，逐级 getattr
        long_term = getattr(state.coordinator, "long_term_memory", None)
        mem0_enabled = bool(getattr(long_term, "enabled", False))

    # 状态接口保持公开供登录页探活，但未授权时不返回内部路径、模型地址和原始异常
    privileged = is_authorized(request)
    startup_error = state.startup_error
    kb_error = state.kb_error
    if not privileged:
        startup_error = "服务初始化失败，请查看服务端日志。" if startup_error else None
        kb_error = "知识库加载失败，请查看服务端日志。" if kb_error else None

    return {
        "ok": state.startup_error is None,
        "web_build": WEB_BUILD_ID,
        "startup_error": startup_error,
        "auth_required": bool(get_access_token()),
        "llm_configured": bool(api_key),
        "pricing": get_pricing(),
        "monthly_token_budget": get_token_budget(),
        "auto_archive_days": get_auto_archive_days(),
        "model_name": LLM_CONFIG.get("model_name") if privileged else None,
        "base_url": (
            _public_base_url(LLM_CONFIG.get("base_url")) if privileged else None
        ),
        "mem0_enabled": mem0_enabled,
        "knowledge_base": {
            "status": state.kb_status,
            "error": kb_error,
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
        raise HTTPException(status_code=500, detail="系统自检失败，请稍后重试。")


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
    offset = _bounded_offset(offset)
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
        helpful = 0
        unhelpful = 0
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
                    feedback = stat.get("feedback") or {}
                    helpful += int(feedback.get("helpful") or 0)
                    unhelpful += int(feedback.get("unhelpful") or 0)
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
            "feedback": {
                "total": helpful + unhelpful,
                "helpful": helpful,
                "unhelpful": unhelpful,
                "rate": round(helpful / (helpful + unhelpful) * 100, 1)
                if helpful + unhelpful else None,
            },
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
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("导出会话失败")
        raise HTTPException(status_code=500, detail="导出会话失败，请稍后重试。")
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

    # 一次查询读取全部结果：分页 OFFSET 期间若有会话被更新，
    # updated_at 排序会让同一行跨页漂移，导致导出漏行或重复
    items = await asyncio.to_thread(
        store.list_sessions,
        100000,
        query,
        0,
        favorites_only,
        days,
        archived,
        tag,
        profile,
    )

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

    # 只回传请求中实际出现的字段：历史备份可能没有后续新增的列表，
    # 若补成 None 参与校验和计算，会让原本有效的旧备份被判为篡改。
    payload = request.model_dump(exclude_unset=True)
    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception("导入会话失败")
        raise HTTPException(status_code=500, detail="导入会话失败，请稍后重试。")

    await asyncio.to_thread(
        _clear_short_term_tombstones,
        [
            item.get("id") for item in request.sessions
            if isinstance(item, dict)
        ],
    )
    await record_audit("session.import", detail=_import_result_detail(result))
    return {"ok": True, **result}


def _import_result_detail(result: Dict[str, Any]) -> str:
    keys = (
        "imported", "skipped", "profiles", "metrics", "targets",
        "reminders", "symptoms", "medications", "medication_logs",
        "medication_history", "vaccinations", "appointments",
        "lab_results", "allergies", "care_items", "visit_records",
        "conditions", "audit_log",
    )
    return " ".join(f"{key}={result.get(key, 0)}" for key in keys)


@app.get("/api/backups")
async def list_local_backups():
    """列出本地自动备份，供恢复入口选择。"""
    from core.backup import list_backups

    items = []
    for path in await asyncio.to_thread(list_backups):
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "name": path.name,
            "size": stat.st_size,
            "modified_at": datetime.fromtimestamp(
                stat.st_mtime
            ).isoformat(timespec="seconds"),
        })
        if len(items) >= 50:
            break
    return {"backups": items}


@app.post("/api/backups")
async def create_local_backup(request: ConfirmRequest):
    """立即创建一份本地备份，不受自动备份间隔限制。"""
    from core.backup import create_backup
    from memory import get_session_store

    if not request.confirm:
        raise HTTPException(status_code=400, detail="请确认创建本地备份。")

    try:
        payload = await asyncio.to_thread(
            get_session_store().export_data
        )
        path = await asyncio.to_thread(create_backup, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("创建本地备份失败")
        raise HTTPException(
            status_code=500, detail="创建本地备份失败，请稍后重试。"
        )

    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    await record_audit(
        "session.export",
        detail=f"source=manual-backup name={path.name}",
    )
    return {"ok": True, "backup": path.name, "size": size}


@app.get("/api/backups/{name}/inspect")
async def inspect_local_backup(name: str):
    """恢复前预检：校验备份完整性并汇总各类记录数量。"""
    from core.backup import resolve_backup
    from memory.session_store import _backup_checksum

    try:
        path = await asyncio.to_thread(resolve_backup, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="备份不存在或已被清理。"
        )
    try:
        stat = await asyncio.to_thread(path.stat)
    except OSError:
        raise HTTPException(
            status_code=404, detail="备份不存在或已被清理。"
        )
    if stat.st_size > MAX_BACKUP_FILE_BYTES:
        raise HTTPException(
            status_code=413, detail="备份文件过大，无法预检。"
        )
    try:
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        payload = await asyncio.to_thread(json.loads, text)
    except (OSError, ValueError, UnicodeError):
        raise HTTPException(
            status_code=400, detail="备份文件无法读取或已损坏。"
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="备份文件格式不正确。")

    checksum = payload.get("checksum")
    checksum_present = isinstance(checksum, str) and bool(checksum.strip())
    checksum_ok = None
    checksum_error = ""
    if checksum_present:
        try:
            expected = await asyncio.to_thread(_backup_checksum, payload)
        except (UnicodeError, ValueError, TypeError):
            checksum_ok = False
            checksum_error = "备份包含无法编码的字符，恢复会失败。"
        else:
            checksum_ok = checksum.strip() == expected

    count_keys = (
        "profiles", "health_metrics", "metric_targets", "reminders",
        "symptoms", "medications", "medication_logs",
        "medication_history", "vaccinations", "appointments",
        "lab_results", "allergies", "care_items", "visit_records",
        "conditions", "audit_log",
    )
    counts = {}
    for key in count_keys:
        value = payload.get(key)
        counts[key] = len(value) if isinstance(value, list) else 0
    sessions_value = payload.get("sessions")
    sessions_type_error = (
        sessions_value is not None and not isinstance(sessions_value, list)
    )
    sessions = sessions_value if isinstance(sessions_value, list) else []
    valid_sessions = 0
    invalid_sessions = 0
    message_count = 0
    for session in sessions:
        if not isinstance(session, dict):
            invalid_sessions += 1
            continue
        if not str(session.get("id") or "").strip():
            invalid_sessions += 1
            continue
        messages = session.get("messages")
        if not isinstance(messages, list):
            invalid_sessions += 1
            continue
        valid_messages = 0
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = message.get("role")
            content = message.get("content")
            if role not in ("user", "assistant"):
                continue
            if not isinstance(content, str) or not content.strip():
                continue
            valid_messages += 1
        if not valid_messages:
            invalid_sessions += 1
            continue
        valid_sessions += 1
        message_count += valid_messages
    counts["sessions"] = valid_sessions
    counts["messages"] = message_count
    total_records = sum(
        value for key, value in counts.items() if key != "messages"
    )

    from memory import get_session_store
    attachment_kinds = await asyncio.to_thread(
        get_session_store().attachment_kind_stats
    )
    attachment_count = sum(
        int(item.get("count") or 0) for item in attachment_kinds
    )
    attachment_size = sum(
        int(item.get("size") or 0) for item in attachment_kinds
    )

    warnings = []
    if checksum_error:
        warnings.append(checksum_error)
    elif checksum_ok is False:
        warnings.append("校验失败，备份可能已损坏或被修改。")
    elif not checksum_present:
        warnings.append("备份没有校验信息，请确认来源可靠。")
    if sessions_type_error:
        warnings.append("会话列表格式不正确，恢复会失败。")
    if invalid_sessions:
        warnings.append(
            f"{invalid_sessions} 个会话缺少编号或有效消息，恢复时会跳过。"
        )
    if not isinstance(payload.get("version"), int):
        warnings.append("备份缺少版本号。")
    if attachment_count:
        if attachment_size >= 1024 * 1024:
            size_text = f"{attachment_size / (1024 * 1024):.1f} MB"
        elif attachment_size >= 1024:
            size_text = f"{round(attachment_size / 1024)} KB"
        else:
            size_text = f"{attachment_size} 字节"
        warnings.append(
            f"本备份不包含附件文件；本机现有 {attachment_count} 份附件"
            f"（{size_text}）不会随备份恢复，请另行导出附件包。"
        )
    if total_records == 0:
        warnings.append("备份中没有可恢复记录。")
    has_backup_arrays = isinstance(sessions_value, list) or any(
        isinstance(payload.get(key), list) for key in count_keys
    )
    restore_ready = (
        checksum_ok is not False
        and not sessions_type_error
        and has_backup_arrays
        and total_records > 0
    )

    return {
        "name": path.name,
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(
            stat.st_mtime
        ).isoformat(timespec="seconds"),
        "version": payload.get("version"),
        "exported_at": payload.get("exported_at"),
        "checksum_present": checksum_present,
        "checksum_ok": checksum_ok,
        "counts": counts,
        "attachment_summary": {
            "count": attachment_count,
            "size": attachment_size,
        },
        "invalid_sessions": invalid_sessions,
        "total_records": total_records,
        "restore_ready": restore_ready,
        "warnings": warnings,
    }


@app.post("/api/backups/{name}/restore")
async def restore_local_backup(name: str, request: ConfirmRequest):
    """恢复指定本地备份；与导入一样只合并、跳过已有记录。"""
    from core.backup import resolve_backup
    from memory import get_session_store

    if not request.confirm:
        raise HTTPException(status_code=400, detail="请确认恢复本地备份。")

    try:
        path = await asyncio.to_thread(resolve_backup, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="备份不存在或已被清理。"
        )

    try:
        if path.stat().st_size > MAX_BACKUP_FILE_BYTES:
            raise HTTPException(
                status_code=413, detail="备份文件过大，无法直接恢复。"
            )
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        payload = json.loads(text)
    except HTTPException:
        raise
    except (OSError, ValueError, UnicodeError):
        raise HTTPException(
            status_code=400, detail="备份文件无法读取或已损坏。"
        )
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="备份文件格式不正确。")

    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, payload
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        logger.exception("恢复本地备份失败")
        raise HTTPException(
            status_code=500, detail="恢复备份失败，请稍后重试。"
        )

    await asyncio.to_thread(
        _clear_short_term_tombstones,
        [
            item.get("id") for item in (payload.get("sessions") or [])
            if isinstance(item, dict)
        ],
    )
    await record_audit(
        "session.import",
        detail=f"source=backup:{name} {_import_result_detail(result)}",
    )
    return {"ok": True, "backup": name, **result}


@app.delete("/api/backups/{name}")
async def delete_local_backup(name: str, request: ConfirmRequest):
    """删除指定的本地备份文件。"""
    from core.backup import delete_backup

    if not request.confirm:
        raise HTTPException(status_code=400, detail="请确认删除本地备份。")

    try:
        await asyncio.to_thread(delete_backup, name)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except FileNotFoundError:
        raise HTTPException(
            status_code=404, detail="备份不存在或已被清理。"
        )
    except OSError:
        logger.exception("删除本地备份失败")
        raise HTTPException(
            status_code=500, detail="删除本地备份失败，请稍后重试。"
        )

    await record_audit("backup.delete", name)
    return {"ok": True, "backup": name}


@app.post("/api/maintenance/purge")
async def purge_old_sessions(request: PurgeRequest):
    """删除指定天数以前的会诊及其消息（不可恢复）。"""
    from datetime import timedelta

    from memory import ShortTermMemory, get_session_store

    cutoff = (
        datetime.now() - timedelta(days=request.days)
    ).isoformat(timespec="seconds")
    result = await asyncio.to_thread(
        get_session_store().purge_sessions_before, cutoff
    )
    session_ids = result.pop("session_ids", [])
    session_profiles = result.pop("session_profiles", {})
    if session_ids:
        def clear_short_term() -> None:
            memory = ShortTermMemory()
            for session_id in session_ids:
                memory.clear_session(session_id)
                memory.seal_session(session_id)

        await asyncio.to_thread(clear_short_term)
        await asyncio.to_thread(
            _erase_long_term_entries,
            [
                (str(session_profiles.get(session_id) or ""), session_id)
                for session_id in session_ids
            ],
        )
        await asyncio.to_thread(_erase_session_summaries, session_ids)
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
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
    ):
        raise HTTPException(status_code=400, detail="不支持的健康指标类型")
    limit = max(1, min(int(limit or 100), 500))
    days = max(0, min(int(days or 0), 3650))
    offset = _bounded_offset(offset)
    items = await asyncio.to_thread(
        get_session_store().list_metrics,
        profile, metric, limit, days, offset, q,
    )
    from memory.session_store import critical_metric_alerts

    for item in items:
        item["critical_alerts"] = critical_metric_alerts(
            item.get("metric"), item.get("value1"), item.get("value2")
        )
    return {"metrics": items}


@app.get("/api/metrics/summary")
async def metric_summary(
    metric: str,
    profile: Optional[str] = None,
    days: int = 0,
):
    """所选范围内的指标统计（平均/最高/最低，以及与上一周期的差值）。"""
    from memory import get_session_store

    if metric not in (
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
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
    offset = _bounded_offset(offset)
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
        "剩余库存", "库存单位", "低库存阈值", "有效期", "库存与效期提示",
        "开始日期", "停药日期", "停药原因",
    ]]
    for item in items:
        expected = int(item.get("expected_week_doses") or 0)
        taken = int(item.get("week_count") or 0)
        alerts = []
        if item.get("out_of_stock"):
            alerts.append("缺货")
        elif item.get("low_stock"):
            alerts.append("库存偏低")
        if item.get("expired"):
            alerts.append("已过期")
        elif item.get("expiring_soon"):
            alerts.append("即将过期")
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
            "" if item.get("stock") is None else csv_number(item["stock"]),
            csv_safe_cell(item.get("stock_unit", "")),
            csv_number(item.get("low_stock_threshold") or 0),
            csv_safe_cell(item.get("expiry_date", "")),
            csv_safe_cell("、".join(alerts)),
            csv_safe_cell(item.get("started_at", "")),
            csv_safe_cell(item.get("stopped_at", "")),
            csv_safe_cell(item.get("stop_reason", "")),
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
    rows = [[
        "药物名称", "剂量", "服用时间", "患者档案",
        "状态", "未服原因", "备注",
    ]]
    for item in logs:
        skipped = bool(item.get("skipped"))
        rows.append([
            csv_safe_cell(item.get("medication_name", "")),
            csv_safe_cell(item.get("medication_dose", "")),
            csv_safe_cell(item.get("taken_at", "")),
            csv_safe_cell(item.get("profile", "")),
            "未服" if skipped else "已服",
            csv_safe_cell(
                item.get("skip_reason_label", "") if skipped else ""
            ),
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
        "库存": "stock",
        "剩余库存": "stock",
        "库存单位": "stock_unit",
        "低库存阈值": "low_stock_threshold",
        "有效期": "expiry_date",
        "开始日期": "started_at",
        "开始用药": "started_at",
        "停药日期": "stopped_at",
        "停药原因": "stop_reason",
    }

    records = []
    invalid = 0
    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")

    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            name = key.strip()
            mapped[field_alias.get(name, name)] = csv_restore_cell(value).strip()

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
            "active": csv_active_status(active),
            "stock": mapped.get("stock") or None,
            "stock_unit": mapped.get("stock_unit", ""),
            "low_stock_threshold": mapped.get("low_stock_threshold") or 0,
            "expiry_date": mapped.get("expiry_date", ""),
            "started_at": mapped.get("started_at", ""),
            "stopped_at": mapped.get("stopped_at", ""),
            "stop_reason": mapped.get("stop_reason", ""),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的用药记录（无效 {invalid} 行）",
        )

    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"medications": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("medications", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "medication.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
        "invalid": invalid,
    }


def _evaluate_medication_safety(
    names: List[str],
    allergy_parts: List[str],
    context_age: str,
    context_sex: str,
    lab_rows: List[Dict[str, Any]],
    condition_rows: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """根据已加载的数据计算单个成员的用药安全提示。"""
    from core.medication_safety import check_medication_safety
    from core.medication_lab_safety import check_medication_lab_safety
    from core.condition_drug_safety import check_condition_drug_safety

    safety = check_medication_safety(
        "",
        {
            "medications": "、".join(names),
            "allergies": "；".join(allergy_parts),
        },
    )
    lab_alerts = check_medication_lab_safety(
        names, lab_rows, {"age": context_age, "sex": context_sex}
    ).get("alerts") or []
    condition_alerts = check_condition_drug_safety(
        names, condition_rows
    ).get("alerts") or []
    if lab_alerts or condition_alerts:
        # 化验与慢病提示在前，既有过敏/相互作用提示全部保留
        merged = (
            [
                {**alert, "source": "lab"}
                for alert in lab_alerts
            ]
            + [
                {**alert, "source": "condition"}
                for alert in condition_alerts
            ]
            + [
                {**alert, "source": "base"}
                for alert in (safety.get("alerts") or [])
            ]
        )
        safety = {"level": "warning", "alerts": merged[:15]}
    return safety


async def _medication_safety_for_profile(
    store: Any, profile: str
) -> Dict[str, Any]:
    """按当前档案的在用药物与过敏史检查新增/修改后的用药安全。"""
    active = await asyncio.to_thread(
        store.list_medications, profile, True, 1000
    )
    names = [
        str(item.get("name") or "").strip()
        for item in active
        if item.get("name")
    ]
    allergies = await asyncio.to_thread(
        store.list_allergies, profile, True, None, "", 1000
    )
    allergy_parts = []
    for item in allergies:
        substance = str(item.get("substance") or "").strip()
        if substance:
            allergy_parts.append(substance)
    profiles = await asyncio.to_thread(store.list_profiles)
    profile_key = _normalized_profile_key(profile)
    context_age = ""
    context_sex = ""
    for saved in profiles:
        if saved.get("name") == profile_key:
            saved_context = saved.get("context") or {}
            context_allergies = str(
                saved_context.get("allergies") or ""
            ).strip()
            if context_allergies:
                allergy_parts.append(context_allergies)
            context_age = str(saved_context.get("age") or "")
            context_sex = str(saved_context.get("sex") or "")
            break
    lab_rows = []
    if names:
        lab_rows = await asyncio.to_thread(
            store.list_lab_results, profile, None, None, 180, 100000, 0
        )
    condition_rows: List[Dict[str, Any]] = []
    if names:
        condition_rows = await asyncio.to_thread(
            store.list_conditions, profile, True, None, 1000
        )
    return _evaluate_medication_safety(
        names, allergy_parts, context_age, context_sex,
        lab_rows, condition_rows,
    )


async def _family_medication_safety(store: Any) -> Optional[Dict[str, Any]]:
    """一次性加载全部成员数据，再按档案分组计算用药安全提示。"""
    active = await asyncio.to_thread(
        store.list_medications, None, True, 100000
    )
    allergies = await asyncio.to_thread(
        store.list_allergies, None, True, None, "", 100000
    )
    saved_profiles = await asyncio.to_thread(store.list_profiles)
    used_names = await asyncio.to_thread(store.list_used_profile_names)

    candidates = {
        _normalized_profile_key(item.get("name") or "")
        for item in saved_profiles
        if item.get("name")
    }
    candidates.update(
        _normalized_profile_key(name) for name in used_names if name
    )
    candidates.update(
        _normalized_profile_key(item.get("profile") or "")
        for item in active
    )
    candidates.update(
        _normalized_profile_key(item.get("profile") or "")
        for item in allergies
    )
    candidates.add("")

    meds_by: Dict[str, List[Dict[str, Any]]] = {}
    for item in active:
        meds_by.setdefault(
            _normalized_profile_key(item.get("profile") or ""), []
        ).append(item)
    allergies_by: Dict[str, List[Dict[str, Any]]] = {}
    for item in allergies:
        allergies_by.setdefault(
            _normalized_profile_key(item.get("profile") or ""), []
        ).append(item)
    contexts = {
        _normalized_profile_key(item.get("name") or ""): (
            item.get("context") or {}
        )
        for item in saved_profiles
        if item.get("name")
    }

    lab_rows: List[Dict[str, Any]] = []
    condition_rows: List[Dict[str, Any]] = []
    if active:
        lab_rows = await asyncio.to_thread(
            store.list_lab_results, None, None, None, 180, 100000, 0
        )
        condition_rows = await asyncio.to_thread(
            store.list_conditions, None, True, None, 100000
        )
    labs_by: Dict[str, List[Dict[str, Any]]] = {}
    for item in lab_rows:
        labs_by.setdefault(
            _normalized_profile_key(item.get("profile") or ""), []
        ).append(item)
    conditions_by: Dict[str, List[Dict[str, Any]]] = {}
    for item in condition_rows:
        conditions_by.setdefault(
            _normalized_profile_key(item.get("profile") or ""), []
        ).append(item)

    family_alerts: List[Dict[str, Any]] = []
    for name in sorted(candidates):
        context = contexts.get(name) or {}
        allergy_parts = [
            str(item.get("substance") or "").strip()
            for item in allergies_by.get(name, [])
            if item.get("substance")
        ]
        context_allergies = str(
            context.get("allergies") or ""
        ).strip()
        if context_allergies:
            allergy_parts.append(context_allergies)
        names = [
            str(item.get("name") or "").strip()
            for item in meds_by.get(name, [])
            if item.get("name")
        ]
        member_safety = _evaluate_medication_safety(
            names,
            allergy_parts,
            str(context.get("age") or ""),
            str(context.get("sex") or ""),
            labs_by.get(name, []),
            conditions_by.get(name, []),
        )
        if member_safety.get("level") != "warning":
            continue
        for alert in member_safety.get("alerts") or []:
            family_alerts.append({
                **alert,
                "title": (
                    f"{name or '未指定档案'}："
                    f"{alert.get('title') or ''}"
                )[:160],
            })
    if not family_alerts:
        return None
    return {"level": "warning", "alerts": family_alerts[:5]}


@app.post("/api/medications")
async def add_medication(request: MedicationRequest):
    """新增一条用药记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(
        store.add_medication,
        request.profile,
        request.name,
        request.dose,
        request.frequency,
        request.note,
        request.active,
        request.stock,
        request.stock_unit,
        request.low_stock_threshold,
        request.expiry_date,
        request.started_at,
        request.stopped_at,
        request.stop_reason,
    )
    if item is None:
        raise HTTPException(
            status_code=400, detail="药物名称、库存或有效期不正确"
        )
    await record_audit(
        "medication.create", str(item["id"])
    )
    safety = await _medication_safety_for_profile(
        store, item.get("profile") or ""
    )
    return {"ok": True, "medication": item, "safety": safety}


@app.patch("/api/medications/{medication_id}")
async def update_medication(
    medication_id: SafeIntPath, request: MedicationUpdateRequest
):
    """部分更新一条用药记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_medication, medication_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")

    # 只把请求实际给出的字段交给存储层，未给出的字段在事务内沿用当前值，
    # 避免锁外快照覆盖其它设备刚提交的修改
    fields = request.model_dump(exclude_unset=True)
    item = await asyncio.to_thread(
        store.update_medication, medication_id, **fields
    )
    if item is None:
        if await asyncio.to_thread(
            store.get_medication, medication_id
        ) is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        raise HTTPException(
            status_code=400,
            detail="用药内容不正确，请检查剂量、日期等字段",
        )
    await record_audit(
        "medication.update", str(item["id"])
    )
    safety = await _medication_safety_for_profile(
        store, item.get("profile") or ""
    )
    return {"ok": True, "medication": item, "safety": safety}


@app.delete("/api/medications/{medication_id}")
async def delete_medication(medication_id: SafeIntPath):
    """删除一条用药记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_medication, medication_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_medication, medication_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await asyncio.to_thread(
        store.redact_audit_targets,
        [
            "medication.create", "medication.update", "medication.delete",
            "medication.log", "medication.log_delete", "medication.skip",
        ],
        f"{item['id']}·",
        str(item["id"]),
    )
    await record_audit(
        "medication.delete", str(item["id"])
    )
    return {"ok": True, "id": medication_id}


@app.get("/api/medications/skip-reasons")
async def list_medication_skip_reasons():
    """未服原因选项（键与中文名）。"""
    from memory.session_store import MEDICATION_SKIP_REASONS

    return {
        "reasons": [
            {"key": key, "label": label}
            for key, label in MEDICATION_SKIP_REASONS.items()
        ]
    }


@app.get("/api/medications/{medication_id}/logs")
async def list_medication_logs(
    medication_id: SafeIntPath,
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
    offset = _bounded_offset(offset)
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


@app.get("/api/medications/{medication_id}/history")
async def list_medication_history(
    medication_id: SafeIntPath, limit: int = 100
):
    """某个药物的变更历史（创建、剂量/频次调整、启停等，按时间倒序）。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_medication, medication_id)
    if item is None:
        raise HTTPException(status_code=404, detail="药物不存在")
    limit = max(1, min(int(limit or 100), 100000))
    history = await asyncio.to_thread(
        store.list_medication_history, medication_id, limit
    )
    return {"medication_id": medication_id, "history": history}


@app.post("/api/medications/{medication_id}/logs")
async def add_medication_log(
    medication_id: SafeIntPath,
    request: MedicationLogRequest,
):
    """记录一次用药（打卡）。"""
    from memory import get_session_store

    store = get_session_store()
    medication = await asyncio.to_thread(
        store.get_medication, medication_id
    )
    if medication is None:
        raise HTTPException(status_code=404, detail="药物不存在")
    log = await asyncio.to_thread(
        store.add_medication_log,
        medication_id,
        request.taken_at,
        request.note,
    )
    if log is None:
        raise HTTPException(
            status_code=400, detail="服用时间或备注不正确"
        )
    await record_audit(
        "medication.log",
        f"{log['medication_id']}·{log['taken_at']}",
    )
    return {"ok": True, "log": log}


@app.post("/api/medications/{medication_id}/skip")
async def skip_medication(
    medication_id: SafeIntPath,
    request: MedicationSkipRequest,
):
    """记录一次未服及原因；不计入已服次数，也不扣减库存。"""
    from memory import get_session_store

    store = get_session_store()
    medication = await asyncio.to_thread(
        store.get_medication, medication_id
    )
    if medication is None:
        raise HTTPException(status_code=404, detail="药物不存在")
    log = await asyncio.to_thread(
        store.add_medication_log,
        medication_id,
        request.skipped_at,
        request.note,
        True,
        request.reason,
    )
    if log is None:
        raise HTTPException(
            status_code=400, detail="未服时间或原因不正确"
        )
    await record_audit(
        "medication.skip",
        f"{log['medication_id']}·{log['taken_at']}·{log['skip_reason']}",
    )
    return {"ok": True, "log": log}


@app.post("/api/medications/{medication_id}/reminder")
async def create_medication_alert_reminder(
    medication_id: SafeIntPath, kind: str = "stock"
):
    """为低库存或临期药物创建一次性提醒；已有未完成提醒时直接返回。"""
    from memory import get_session_store

    kind = str(kind or "").strip().lower()
    if kind not in {"stock", "expiry"}:
        raise HTTPException(status_code=400, detail="不支持的提醒类型")
    store = get_session_store()
    item = await asyncio.to_thread(store.get_medication, medication_id)
    if item is None:
        raise HTTPException(status_code=404, detail="药物不存在")

    today = datetime.now().date()
    if kind == "stock":
        if item.get("stock") is None:
            raise HTTPException(status_code=400, detail="该药物未登记库存")
        title = f"补货：{item.get('name') or '药物'}"[:120]
        due_at = today.strftime("%Y-%m-%d")
        note = "库存偏低，请及时补货"
    else:
        expiry = str(item.get("expiry_date") or "")[:10]
        if not expiry:
            raise HTTPException(status_code=400, detail="该药物未登记有效期")
        try:
            expiry_date = datetime.strptime(expiry, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(status_code=400, detail="有效期格式不正确")
        title = f"药物到期：{item.get('name') or '药物'}"[:120]
        reminder_date = expiry_date - timedelta(days=30)
        due_at = max(reminder_date, today).strftime("%Y-%m-%d")
        note = f"有效期至 {expiry}，请检查并更换"

    reminder, created = await asyncio.to_thread(
        store.ensure_reminder,
        title,
        due_at,
        note,
        "",
        item.get("profile") or "",
        0,
        int(medication_id),
    )
    if reminder is None:
        raise HTTPException(status_code=400, detail="提醒日期不正确")
    if created:
        await record_audit("medication.reminder", str(medication_id))
    return {"ok": True, "created": created, "reminder": reminder}


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
    offset = _bounded_offset(offset)
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
async def delete_medication_log(log_id: SafeIntPath):
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


@app.get("/api/vaccinations")
async def list_vaccinations(
    profile: Optional[str] = None, limit: int = 1000
):
    """疫苗接种记录（按接种日期倒序，可按档案筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    items = await asyncio.to_thread(
        get_session_store().list_vaccinations, profile, limit
    )
    return {"vaccinations": items}


@app.post("/api/vaccinations")
async def add_vaccination(request: VaccinationRequest):
    """新增一条疫苗接种记录。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_vaccination,
            request.profile,
            request.vaccine_name,
            request.dose_number,
            request.vaccinated_at,
            request.next_due,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("vaccination.create", str(item["id"]))
    return {"ok": True, "vaccination": item}


@app.patch("/api/vaccinations/{vaccination_id}")
async def update_vaccination(
    vaccination_id: SafeIntPath,
    request: VaccinationUpdateRequest,
):
    """部分更新一条疫苗接种记录。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(
        store.get_vaccination, vaccination_id
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    merged = {**existing, **request.model_dump(exclude_unset=True)}
    try:
        item = await asyncio.to_thread(
            store.update_vaccination,
            vaccination_id,
            merged.get("profile") or "",
            merged.get("vaccine_name") or "",
            merged.get("dose_number"),
            merged.get("vaccinated_at") or "",
            merged.get("next_due") or "",
            merged.get("note") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("vaccination.update", str(item["id"]))
    return {"ok": True, "vaccination": item}


@app.delete("/api/vaccinations/{vaccination_id}")
async def delete_vaccination(vaccination_id: SafeIntPath):
    """删除一条疫苗接种记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_vaccination, vaccination_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(
        store.delete_vaccination, vaccination_id
    )
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("vaccination.delete", str(vaccination_id))
    return {"ok": True, "id": vaccination_id}


@app.get("/api/vaccinations/export.csv")
async def export_vaccinations_csv(profile: Optional[str] = None):
    """导出疫苗接种记录 CSV（带 BOM），可按档案筛选。"""
    import csv
    import io

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_vaccinations, profile, 100000
    )
    rows = [[
        "接种日期", "患者档案", "疫苗名称", "剂次", "下一剂", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("vaccinated_at", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("vaccine_name", "")),
            int(item.get("dose_number") or 0),
            csv_safe_cell(item.get("next_due", "")),
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-vaccinations-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/vaccinations/{vaccination_id}/reminder")
async def create_vaccination_reminder(vaccination_id: SafeIntPath):
    """为下一剂接种创建提前 7 天的提醒；已有未完成提醒时直接返回。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_vaccination, vaccination_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    due_at = str(item.get("next_due") or "").strip()
    if not due_at:
        raise HTTPException(status_code=400, detail="请先填写下一剂日期")
    title = (
        f"接种：{item.get('vaccine_name') or '疫苗'}"
        f"（第{int(item.get('dose_number') or 1)}剂）"
    )[:120]
    reminder, created = await asyncio.to_thread(
        store.ensure_reminder,
        title,
        due_at,
        "疫苗接种提醒",
        "",
        item.get("profile") or "",
        7,
        0,
        int(vaccination_id),
        0,
        False,
    )
    if reminder is None:
        raise HTTPException(status_code=400, detail="下一剂日期格式不正确")
    if created:
        await record_audit(
            "vaccination.reminder", str(item["id"])
        )
    return {"ok": True, "created": created, "reminder": reminder}


@app.get("/api/appointments")
async def list_appointments(
    profile: Optional[str] = None,
    status: Optional[str] = None,
    upcoming: bool = False,
    limit: int = 1000,
    offset: int = 0,
    q: str = "",
):
    """就诊预约列表（可按档案、状态与是否只看待就诊筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    offset = _bounded_offset(offset)
    try:
        items = await asyncio.to_thread(
            get_session_store().list_appointments,
            profile, status, upcoming, limit, offset, q=q,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"appointments": items}


@app.post("/api/appointments")
async def add_appointment(request: AppointmentRequest):
    """新增一条就诊预约。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_appointment,
            request.profile,
            request.title,
            request.department,
            request.doctor,
            request.location,
            request.scheduled_at,
            request.status,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("appointment.create", str(item["id"]))
    return {"ok": True, "appointment": item}


@app.patch("/api/appointments/{appointment_id}")
async def update_appointment(
    appointment_id: SafeIntPath,
    request: AppointmentUpdateRequest,
):
    """部分更新一条就诊预约。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(
        store.get_appointment, appointment_id
    )
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    merged = {**existing, **request.model_dump(exclude_unset=True)}
    try:
        item = await asyncio.to_thread(
            store.update_appointment,
            appointment_id,
            merged.get("profile") or "",
            merged.get("title") or "",
            merged.get("department") or "",
            merged.get("doctor") or "",
            merged.get("location") or "",
            merged.get("scheduled_at") or "",
            merged.get("status") or "upcoming",
            merged.get("note") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("appointment.update", str(item["id"]))
    return {"ok": True, "appointment": item}


@app.delete("/api/appointments/{appointment_id}")
async def delete_appointment(appointment_id: SafeIntPath):
    """删除一条就诊预约。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(
        store.get_appointment, appointment_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_appointment, appointment_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("appointment.delete", str(appointment_id))
    return {"ok": True, "id": appointment_id}


@app.get("/api/appointments/export.csv")
async def export_appointments_csv(
    profile: Optional[str] = None,
    status: Optional[str] = None,
    upcoming: bool = False,
    q: str = "",
):
    """导出就诊预约 CSV（带 BOM），支持档案、状态与关键字筛选。"""
    import csv
    import io

    from memory import get_session_store

    try:
        items = await asyncio.to_thread(
            get_session_store().list_appointments,
            profile, status, upcoming, 100000, 0, q=q,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    status_labels = {
        "upcoming": "待就诊",
        "completed": "已完成",
        "cancelled": "已取消",
    }
    rows = [[
        "就诊时间", "患者档案", "主题", "科室", "医生", "地点",
        "状态", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("scheduled_at", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("department", "")),
            csv_safe_cell(item.get("doctor", "")),
            csv_safe_cell(item.get("location", "")),
            status_labels.get(item.get("status") or "", ""),
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-appointments-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/appointments/{appointment_id}/reminder")
async def create_appointment_reminder(appointment_id: SafeIntPath):
    """为待就诊预约创建提前 1 天的提醒；已有未完成提醒时直接返回。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_appointment, appointment_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if item.get("status") != "upcoming":
        raise HTTPException(status_code=400, detail="只能为待就诊预约创建提醒")
    scheduled = str(item.get("scheduled_at") or "")
    try:
        scheduled_dt = datetime.fromisoformat(scheduled)
    except ValueError:
        raise HTTPException(status_code=400, detail="就诊时间格式不正确")
    today = datetime.now().date()
    reminder_date = scheduled_dt.date() - timedelta(days=1)
    due_at = max(reminder_date, today).strftime("%Y-%m-%d")
    title = f"就诊：{item.get('title') or '就诊'}"[:120]
    detail_parts = [
        scheduled.replace("T", " ")[:16],
        item.get("department") or "",
        item.get("doctor") or "",
        item.get("location") or "",
    ]
    note = " ".join(part for part in detail_parts if part)[:300]

    reminder, created = await asyncio.to_thread(
        store.ensure_reminder,
        title,
        due_at,
        note,
        "",
        item.get("profile") or "",
        0,
        0,
        0,
        int(appointment_id),
        False,
    )
    if reminder is None:
        raise HTTPException(status_code=400, detail="提醒日期格式不正确")
    if created:
        await record_audit("appointment.reminder", str(appointment_id))
    return {"ok": True, "created": created, "reminder": reminder}


@app.get("/api/lab-results")
async def list_lab_results(
    profile: Optional[str] = None,
    item: Optional[str] = None,
    panel: Optional[str] = None,
    days: int = 0,
    limit: int = 1000,
    offset: int = 0,
):
    """化验结果列表（可按档案、项目、分组与最近天数筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    offset = _bounded_offset(offset)
    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().list_lab_results,
        profile, item, panel, days, limit, offset,
    )
    return {"lab_results": items}


@app.get("/api/lab-results/latest")
async def latest_lab_results(profile: Optional[str] = None):
    """每个化验项目最近一次结果，用于计算器预填与摘要。"""
    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().latest_lab_results, profile
    )
    return {"lab_results": items}


@app.post("/api/lab-results")
async def add_lab_result(request: LabResultRequest):
    """新增一条化验结果。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_lab_result,
            request.profile,
            request.item,
            request.value,
            request.unit,
            request.ref_low,
            request.ref_high,
            request.tested_at,
            request.panel,
            request.lab_name,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("lab.create", str(item["id"]))
    return {"ok": True, "lab_result": item}


@app.patch("/api/lab-results/{lab_id}")
async def update_lab_result(
    lab_id: SafeIntPath, request: LabResultUpdateRequest
):
    """部分更新一条化验结果。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_lab_result, lab_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    merged = {**existing, **request.model_dump(exclude_unset=True)}
    try:
        item = await asyncio.to_thread(
            store.update_lab_result,
            lab_id,
            merged.get("profile") or "",
            merged.get("item") or "",
            merged.get("value"),
            merged.get("unit") or "",
            merged.get("ref_low"),
            merged.get("ref_high"),
            merged.get("tested_at") or "",
            merged.get("panel") or "",
            merged.get("lab_name") or "",
            merged.get("note") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("lab.update", str(item["id"]))
    return {"ok": True, "lab_result": item}


@app.delete("/api/lab-results/{lab_id}")
async def delete_lab_result(lab_id: SafeIntPath):
    """删除一条化验结果。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_lab_result, lab_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_lab_result, lab_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await asyncio.to_thread(store.detach_attachments, "lab", lab_id)
    await record_audit("lab.delete", str(lab_id))
    return {"ok": True, "id": lab_id}


@app.get("/api/lab-results/export.csv")
async def export_lab_results_csv(
    profile: Optional[str] = None,
    item: Optional[str] = None,
    panel: Optional[str] = None,
    days: int = 0,
):
    """导出化验结果 CSV（带 BOM）。"""
    import csv
    import io

    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().list_lab_results,
        profile, item, panel, days, 100000, 0,
    )
    rows = [[
        "检测时间", "患者档案", "分组", "项目", "数值", "单位",
        "参考下限", "参考上限", "结果提示", "危急值", "化验机构",
        "备注", "ID",
    ]]
    for row in items:
        if row.get("abnormal_low"):
            flag = "偏低"
        elif row.get("abnormal_high"):
            flag = "偏高"
        else:
            flag = ""
        rows.append([
            csv_safe_cell(row.get("tested_at", "")),
            csv_safe_cell(row.get("profile", "")),
            csv_safe_cell(row.get("panel", "")),
            csv_safe_cell(row.get("item", "")),
            csv_number(row.get('value') or 0),
            csv_safe_cell(row.get("unit", "")),
            "" if row.get("ref_low") is None
            else csv_number(row["ref_low"]),
            "" if row.get("ref_high") is None
            else csv_number(row["ref_high"]),
            flag,
            "是" if row.get("critical") else "",
            csv_safe_cell(row.get("lab_name", "")),
            csv_safe_cell(row.get("note", "")),
            int(row.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-lab-results-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/lab-results/import")
async def import_lab_results_csv(
    request: Request, file: UploadFile = File(...)
):
    """从导出的 CSV 格式导入化验结果，重复记录自动跳过。"""
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
        "检测时间": "tested_at",
        "日期": "tested_at",
        "时间": "tested_at",
        "患者档案": "profile",
        "档案": "profile",
        "分组": "panel",
        "化验分组": "panel",
        "项目": "item",
        "化验项目": "item",
        "数值": "value",
        "结果": "value",
        "单位": "unit",
        "参考下限": "ref_low",
        "下限": "ref_low",
        "参考上限": "ref_high",
        "上限": "ref_high",
        "化验机构": "lab_name",
        "机构": "lab_name",
        "备注": "note",
    }
    records = []
    invalid = 0
    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")
    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            mapped[field_alias.get(key.strip(), key.strip())] = (
                csv_restore_cell(value).strip()
            )
        item_name = mapped.get("item", "")
        if not item_name:
            invalid += 1
            continue
        records.append({
            "profile": mapped.get("profile", ""),
            "panel": mapped.get("panel", ""),
            "item": item_name,
            "value": mapped.get("value", ""),
            "unit": mapped.get("unit", ""),
            "ref_low": mapped.get("ref_low") or None,
            "ref_high": mapped.get("ref_high") or None,
            "tested_at": mapped.get("tested_at", ""),
            "lab_name": mapped.get("lab_name", ""),
            "note": mapped.get("note", ""),
        })
    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的化验结果（无效 {invalid} 行）",
        )
    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"lab_results": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("lab_results", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "lab.import", detail=f"imported={imported} invalid={invalid}"
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
        "invalid": invalid,
    }


@app.get("/api/allergies")
async def list_allergies(
    profile: Optional[str] = None,
    active_only: bool = False,
    kind: Optional[str] = None,
    q: str = "",
    limit: int = 1000,
):
    """过敏史列表（可按档案、类型、状态与关键字筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    try:
        items = await asyncio.to_thread(
            get_session_store().list_allergies,
            profile, active_only, kind, q, limit,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"allergies": items}


@app.post("/api/allergies")
async def add_allergy(request: AllergyRequest):
    """新增一条过敏史。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_allergy,
            request.profile,
            request.substance,
            request.reaction,
            request.severity,
            request.kind,
            request.active,
            request.noted_at,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("allergy.create", str(item["id"]))
    return {"ok": True, "allergy": item}


@app.patch("/api/allergies/{allergy_id}")
async def update_allergy(
    allergy_id: SafeIntPath, request: AllergyUpdateRequest
):
    """部分更新一条过敏史。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_allergy, allergy_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    fields = request.model_dump(exclude_unset=True)
    try:
        item = await asyncio.to_thread(
            store.update_allergy, allergy_id, **fields
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("allergy.update", str(item["id"]))
    return {"ok": True, "allergy": item}


@app.delete("/api/allergies/{allergy_id}")
async def delete_allergy(allergy_id: SafeIntPath):
    """删除一条过敏史。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_allergy, allergy_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_allergy, allergy_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("allergy.delete", str(allergy_id))
    return {"ok": True, "id": allergy_id}


@app.get("/api/allergies/export.csv")
async def export_allergies_csv(
    profile: Optional[str] = None,
    active_only: bool = False,
    kind: Optional[str] = None,
    q: str = "",
):
    """导出过敏史 CSV（带 BOM），支持档案、类型、状态与关键字筛选。"""
    import csv
    import io

    from memory import get_session_store

    try:
        items = await asyncio.to_thread(
            get_session_store().list_allergies,
            profile, active_only, kind, q, 100000,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    severity_labels = {
        "mild": "轻度",
        "moderate": "中度",
        "severe": "重度",
    }
    kind_labels = {
        "drug": "药物",
        "food": "食物",
        "environment": "环境",
        "other": "其他",
    }
    rows = [[
        "过敏原", "患者档案", "过敏反应", "严重程度", "类型",
        "状态", "发现时间", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("substance", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("reaction", "")),
            severity_labels.get(item.get("severity") or "", ""),
            kind_labels.get(item.get("kind") or "", ""),
            "在用" if item.get("active") else "已停用",
            csv_safe_cell(item.get("noted_at", "")),
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-allergies-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/allergies/import")
async def import_allergies_csv(
    request: Request, file: UploadFile = File(...)
):
    """从导出的 CSV 格式导入过敏史，重复记录自动跳过。"""
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
        "过敏原": "substance",
        "物质": "substance",
        "患者档案": "profile",
        "档案": "profile",
        "过敏反应": "reaction",
        "反应": "reaction",
        "严重程度": "severity",
        "程度": "severity",
        "类型": "kind",
        "状态": "active",
        "发现时间": "noted_at",
        "备注": "note",
    }
    severity_map = {"轻度": "mild", "中度": "moderate", "重度": "severe"}
    kind_map = {"药物": "drug", "食物": "food", "环境": "environment", "其他": "other"}
    records = []
    invalid = 0
    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")
    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            mapped[field_alias.get(key.strip(), key.strip())] = (
                csv_restore_cell(value).strip()
            )
        substance = mapped.get("substance", "")
        if not substance:
            invalid += 1
            continue
        severity = mapped.get("severity", "")
        kind = mapped.get("kind", "")
        active = mapped.get("active", "")
        records.append({
            "profile": mapped.get("profile", ""),
            "substance": substance,
            "reaction": mapped.get("reaction", ""),
            "severity": severity_map.get(severity, severity or "moderate"),
            "kind": kind_map.get(kind, kind or "drug"),
            "active": csv_active_status(active),
            "noted_at": mapped.get("noted_at", ""),
            "note": mapped.get("note", ""),
        })
    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的过敏史（无效 {invalid} 行）",
        )
    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"allergies": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("allergies", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "allergy.import", detail=f"imported={imported} invalid={invalid}"
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
        "invalid": invalid,
    }


async def _complete_care_item_old_reminders(
    store, reminders, title: str, keep_id: int = 0
) -> None:
    """完成同一复查计划下旧的未完成提醒，避免完成后仍显示逾期。"""
    for reminder in reminders:
        if reminder.get("done") or reminder.get("title") != title:
            continue
        try:
            reminder_id = int(reminder.get("id"))
        except (TypeError, ValueError, OverflowError):
            continue
        if keep_id and reminder_id == keep_id:
            continue
        await asyncio.to_thread(store.complete_reminder, reminder_id)


async def _ensure_care_item_reminder(store, item):
    """按复查计划的下次日期创建提前 7 天的提醒；已有提醒时复用。

    返回 (reminder, created)；下次日期缺失或格式不正确时返回 (None, False)。
    """
    due = str(item.get("next_due_at") or "")[:10]
    if not due:
        return None, False
    try:
        due_date = datetime.strptime(due, "%Y-%m-%d").date()
    except ValueError:
        return None, False
    today = datetime.now().date()
    reminder_due = max(due_date - timedelta(days=7), today)
    title = f"复查：{item.get('title') or '复查'}"[:120]
    note = f"下次复查 {due}"
    reminder, created = await asyncio.to_thread(
        store.ensure_reminder,
        title,
        reminder_due.strftime("%Y-%m-%d"),
        note,
        "",
        item.get("profile") or "",
        0,
        0,
        0,
        0,
        True,
        True,
    )
    if reminder is None:
        return None, False
    reminders = await asyncio.to_thread(
        store.list_reminders, True, item.get("profile") or ""
    )
    await _complete_care_item_old_reminders(
        store, reminders, title, keep_id=int(reminder["id"])
    )
    return reminder, created


@app.get("/api/care-items")
async def list_care_items(
    profile: Optional[str] = None,
    active_only: bool = False,
    overdue: bool = False,
    limit: int = 1000,
    q: str = "",
):
    """复查计划列表（可按档案、状态与逾期筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    items = await asyncio.to_thread(
        get_session_store().list_care_items,
        profile, active_only, overdue, limit, q=q,
    )
    return {"care_items": items}


@app.post("/api/care-items")
async def add_care_item(request: CareItemRequest):
    """新增一条复查计划。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_care_item,
            request.profile,
            request.title,
            request.interval_days,
            request.last_done_at,
            request.next_due_at,
            request.note,
            request.active,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("care.create", str(item["id"]))
    return {"ok": True, "care_item": item}


@app.patch("/api/care-items/{care_id}")
async def update_care_item(
    care_id: SafeIntPath, request: CareItemUpdateRequest
):
    """部分更新一条复查计划。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_care_item, care_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    merged = {**existing, **request.model_dump(exclude_unset=True)}
    try:
        item = await asyncio.to_thread(
            store.update_care_item,
            care_id,
            merged.get("profile") or "",
            merged.get("title") or "",
            merged.get("interval_days") or 30,
            merged.get("last_done_at") or "",
            merged.get("next_due_at") or "",
            merged.get("note") or "",
            bool(merged.get("active", True)),
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("care.update", str(item["id"]))
    return {"ok": True, "care_item": item}


@app.post("/api/care-items/{care_id}/done")
async def done_care_item(
    care_id: SafeIntPath, request: CareDoneRequest
):
    """记录一次复查完成、顺延下次日期，并自动创建下次复查提醒。"""
    from memory import get_session_store

    store = get_session_store()
    try:
        item = await asyncio.to_thread(
            store.mark_care_item_done,
            care_id,
            request.done_at,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    reminder = None
    reminder_created = False
    if item.get("active"):
        reminder, reminder_created = await _ensure_care_item_reminder(
            store, item
        )
    await record_audit("care.done", str(care_id))
    return {
        "ok": True,
        "care_item": item,
        "reminder": reminder,
        "reminder_created": reminder_created,
    }


@app.delete("/api/care-items/{care_id}")
async def delete_care_item(care_id: SafeIntPath):
    """删除一条复查计划。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_care_item, care_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_care_item, care_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("care.delete", str(care_id))
    return {"ok": True, "id": care_id}


@app.get("/api/care-items/export.csv")
async def export_care_items_csv(
    profile: Optional[str] = None,
    active_only: bool = False,
    overdue: bool = False,
    q: str = "",
):
    """导出复查计划 CSV（带 BOM），支持状态与关键字筛选。"""
    import csv
    import io

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_care_items,
        profile, active_only, overdue, 100000, q=q,
    )
    rows = [[
        "复查项目", "患者档案", "间隔天数", "上次完成", "下次复查",
        "状态", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("title", "")),
            csv_safe_cell(item.get("profile", "")),
            int(item.get("interval_days") or 0),
            csv_safe_cell(item.get("last_done_at", "")),
            csv_safe_cell(item.get("next_due_at", "")),
            "启用" if item.get("active") else "停用",
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-care-plan-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/care-items/{care_id}/reminder")
async def create_care_item_reminder(care_id: SafeIntPath):
    """为复查计划创建提前 7 天的提醒；已有未完成提醒时直接返回。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_care_item, care_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    due = str(item.get("next_due_at") or "")[:10]
    if not due:
        raise HTTPException(status_code=400, detail="请先填写下次复查日期")
    try:
        datetime.strptime(due, "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail="下次复查日期格式不正确")
    reminder, created = await _ensure_care_item_reminder(store, item)
    if reminder is None:
        raise HTTPException(status_code=400, detail="下次复查日期格式不正确")
    if created:
        await record_audit("care.reminder", str(care_id))
    return {"ok": True, "created": created, "reminder": reminder}


@app.get("/api/visits")
async def list_visits(
    profile: Optional[str] = None,
    days: int = 0,
    q: str = "",
    limit: int = 1000,
):
    """就诊记录列表（可按档案、最近天数与关键字筛选）。"""
    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    limit = max(1, min(int(limit or 1000), 100000))
    items = await asyncio.to_thread(
        get_session_store().list_visit_records,
        profile, days, q, limit,
    )
    return {"visits": items}


@app.post("/api/visits")
async def add_visit(request: VisitRecordRequest):
    """新增一条就诊记录。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_visit_record,
            request.profile,
            request.appointment_id,
            request.visit_date,
            request.department,
            request.doctor,
            request.facility,
            request.diagnosis,
            request.advice,
            request.prescription,
            request.next_followup_date,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("visit.create", str(item["id"]))
    return {"ok": True, "visit": item}


@app.patch("/api/visits/{visit_id}")
async def update_visit(
    visit_id: SafeIntPath, request: VisitRecordUpdateRequest
):
    """部分更新一条就诊记录。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_visit_record, visit_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    merged = {**existing, **request.model_dump(exclude_unset=True)}
    try:
        item = await asyncio.to_thread(
            store.update_visit_record,
            visit_id,
            merged.get("profile") or "",
            merged.get("appointment_id") or 0,
            merged.get("visit_date") or "",
            merged.get("department") or "",
            merged.get("doctor") or "",
            merged.get("facility") or "",
            merged.get("diagnosis") or "",
            merged.get("advice") or "",
            merged.get("prescription") or "",
            merged.get("next_followup_date") or "",
            merged.get("note") or "",
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("visit.update", str(item["id"]))
    return {"ok": True, "visit": item}


@app.post("/api/visits/{visit_id}/schedule-followup")
async def schedule_visit_followup(visit_id: SafeIntPath):
    """按就诊记录的下次复诊日期创建预约，并生成提前 1 天提醒。"""
    from memory import get_session_store

    store = get_session_store()
    visit = await asyncio.to_thread(store.get_visit_record, visit_id)
    if visit is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    followup_date = str(visit.get("next_followup_date") or "")[:10]
    if not followup_date:
        raise HTTPException(
            status_code=400, detail="该就诊记录没有填写下次复诊日期"
        )
    try:
        followup_day = datetime.strptime(followup_date, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(status_code=400, detail="下次复诊日期格式不正确")

    appointment = None
    appointment_created = False
    linked_appointment_id = int(visit.get("followup_appointment_id") or 0)
    if linked_appointment_id:
        appointment = await asyncio.to_thread(
            store.get_appointment, linked_appointment_id
        )
        if appointment and appointment.get("status") == "cancelled":
            appointment = None
    if appointment is None:
        title = (
            f"复诊：{visit.get('diagnosis') or visit.get('department') or '复诊'}"
        )[:120]
        appointment = await asyncio.to_thread(
            store.add_appointment,
            visit.get("profile") or "",
            title,
            visit.get("department") or "",
            visit.get("doctor") or "",
            visit.get("facility") or "",
            f"{followup_date}T09:00",
            "upcoming",
            f"由就诊记录 #{visit_id} 自动创建",
        )
        appointment_created = True

    reminder = None
    reminder_created = False
    linked_reminder_id = int(visit.get("followup_reminder_id") or 0)
    if linked_reminder_id:
        reminder = await asyncio.to_thread(
            store.get_reminder, linked_reminder_id
        )
        if (
            reminder
            and int(reminder.get("appointment_id") or 0)
            != int(appointment["id"])
        ):
            reminder = None
    if reminder is None:
        reminders = await asyncio.to_thread(
            store.list_reminders, True, visit.get("profile") or ""
        )
        for candidate in reminders:
            if int(candidate.get("appointment_id") or 0) == int(
                appointment["id"]
            ):
                reminder = candidate
                break
    if reminder is None:
        today = datetime.now().date()
        reminder_due = max(followup_day - timedelta(days=1), today)
        title = (
            f"复诊：{visit.get('diagnosis') or visit.get('department') or '复诊'}"
        )[:120]
        note = " ".join(
            part for part in (
                f"复诊 {followup_date}",
                visit.get("department") or "",
                visit.get("doctor") or "",
            ) if part
        )[:300]
        reminder = await asyncio.to_thread(
            store.add_reminder,
            title,
            reminder_due.strftime("%Y-%m-%d"),
            note,
            "",
            visit.get("profile") or "",
            0,
            0,
            "",
            0,
            0,
            int(appointment["id"]),
        )
        reminder_created = True

    await asyncio.to_thread(
        store.set_visit_followup_links,
        visit_id,
        appointment["id"],
        reminder["id"],
    )
    await record_audit("visit.followup", str(visit_id))
    return {
        "ok": True,
        "appointment": appointment,
        "appointment_created": appointment_created,
        "reminder": reminder,
        "reminder_created": reminder_created,
    }


@app.delete("/api/visits/{visit_id}")
async def delete_visit(visit_id: SafeIntPath):
    """删除一条就诊记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_visit_record, visit_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_visit_record, visit_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await asyncio.to_thread(store.detach_attachments, "visit", visit_id)
    await record_audit("visit.delete", str(visit_id))
    return {"ok": True, "id": visit_id}


@app.get("/api/visits/export.csv")
async def export_visits_csv(
    profile: Optional[str] = None,
    days: int = 0,
    q: str = "",
):
    """导出就诊记录 CSV（带 BOM）。"""
    import csv
    import io

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_visit_records,
        profile, days, q, 100000,
    )
    rows = [[
        "就诊日期", "患者档案", "科室", "医生", "机构", "诊断",
        "医嘱", "处方", "下次复诊", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("visit_date", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("department", "")),
            csv_safe_cell(item.get("doctor", "")),
            csv_safe_cell(item.get("facility", "")),
            csv_safe_cell(item.get("diagnosis", "")),
            csv_safe_cell(item.get("advice", "")),
            csv_safe_cell(item.get("prescription", "")),
            csv_safe_cell(item.get("next_followup_date", "")),
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-visits-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


#: 慢病模板：匹配疾病后一键生成复查计划与指标目标
CHRONIC_CARE_TEMPLATES = (
    {
        "key": "hypertension",
        "name": "高血压",
        "keywords": ("高血压",),
        "icd10_prefixes": ("I10", "I11", "I12", "I13", "I15"),
        "care_items": (
            ("血脂", 180),
            ("肾功能与电解质", 180),
            ("尿常规", 180),
            ("眼底检查", 365),
        ),
        "targets": (("bp", 130, 80),),
    },
    {
        "key": "diabetes",
        "name": "糖尿病",
        "keywords": ("糖尿病",),
        "icd10_prefixes": ("E10", "E11", "E13", "E14"),
        "care_items": (
            ("糖化血红蛋白", 90),
            ("尿微量白蛋白/肌酐比", 180),
            ("血脂", 180),
            ("眼底检查", 365),
            ("足部检查", 365),
        ),
        "targets": (("glucose", 7.0, None),),
    },
    {
        "key": "ckd",
        "name": "慢性肾脏病",
        "keywords": ("慢性肾脏病", "肾功能不全", "CKD"),
        "icd10_prefixes": ("N18",),
        "care_items": (
            ("血肌酐与 eGFR", 90),
            ("尿蛋白定量", 90),
            ("电解质", 90),
            ("血脂", 180),
        ),
        "targets": (("bp", 130, 80),),
    },
    {
        "key": "dyslipidemia",
        "name": "血脂异常",
        "keywords": ("血脂异常", "高脂血症", "高血脂"),
        "icd10_prefixes": ("E78",),
        "care_items": (
            ("血脂", 180),
            ("肝功能", 180),
        ),
        "targets": (),
    },
)


def _condition_template(
    condition: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """按疾病名称关键字或 ICD-10 前缀匹配慢病模板。"""
    name = str(condition.get("name") or "")
    icd10 = str(condition.get("icd10") or "").strip().upper()
    for template in CHRONIC_CARE_TEMPLATES:
        if any(word in name for word in template["keywords"]):
            return template
        if icd10 and any(
            icd10.startswith(prefix)
            for prefix in template["icd10_prefixes"]
        ):
            return template
    return None


def _annotate_condition_template(
    item: Dict[str, Any],
) -> Dict[str, Any]:
    """为慢病记录补充前端识别模板用的字段。"""
    template = _condition_template(item)
    item["template_key"] = template["key"] if template else ""
    item["template_name"] = template["name"] if template else ""
    return item


@app.get("/api/conditions")
async def list_conditions(
    profile: Optional[str] = None,
    active_only: bool = False,
    status: Optional[str] = None,
    limit: int = 1000,
    q: str = "",
):
    """慢病/健康问题清单（可按档案、状态筛选）。"""
    from memory import get_session_store

    limit = max(1, min(int(limit or 1000), 100000))
    try:
        items = await asyncio.to_thread(
            get_session_store().list_conditions,
            profile, active_only, status, limit, q=q,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    items = [_annotate_condition_template(item) for item in items]
    return {"conditions": items}


@app.post("/api/conditions")
async def add_condition(request: ConditionRequest):
    """新增一条慢病/健康问题。"""
    from memory import get_session_store

    try:
        item = await asyncio.to_thread(
            get_session_store().add_condition,
            request.profile,
            request.name,
            request.icd10,
            request.diagnosed_at,
            request.status,
            request.severity,
            request.note,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await record_audit("condition.create", str(item["id"]))
    return {
        "ok": True,
        "condition": _annotate_condition_template(item),
    }


@app.patch("/api/conditions/{condition_id}")
async def update_condition(
    condition_id: SafeIntPath, request: ConditionUpdateRequest
):
    """部分更新一条慢病/健康问题。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_condition, condition_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    fields = request.model_dump(exclude_unset=True)
    try:
        item = await asyncio.to_thread(
            store.update_condition, condition_id, **fields
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("condition.update", str(item["id"]))
    return {
        "ok": True,
        "condition": _annotate_condition_template(item),
    }


@app.post("/api/conditions/{condition_id}/apply-template")
async def apply_condition_template(condition_id: SafeIntPath):
    """按慢病类型一键生成复查计划与指标目标；已有项目自动跳过。"""
    from memory import get_session_store

    store = get_session_store()
    condition = await asyncio.to_thread(store.get_condition, condition_id)
    if condition is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    template = _condition_template(condition)
    if template is None:
        raise HTTPException(status_code=400, detail="该慢病暂无可用模板")
    profile = condition.get("profile") or ""

    existing = await asyncio.to_thread(
        store.list_care_items, profile, True, False, 100000
    )
    existing_titles = {str(row.get("title") or "") for row in existing}
    today = datetime.now().date()
    created_items = []
    skipped_titles = []
    for title, interval in template["care_items"]:
        if title in existing_titles:
            skipped_titles.append(title)
            continue
        item = await asyncio.to_thread(
            store.add_care_item,
            profile,
            title,
            interval,
            "",
            (today + timedelta(days=interval)).strftime("%Y-%m-%d"),
            f"慢病模板：{condition.get('name') or template['name']}",
            True,
        )
        created_items.append(item)
        existing_titles.add(title)

    targets = await asyncio.to_thread(store.list_metric_targets, profile)
    existing_metrics = {row.get("metric") for row in targets}
    created_targets = []
    skipped_targets = []
    for metric, value1, value2 in template["targets"]:
        if metric in existing_metrics:
            skipped_targets.append(metric)
            continue
        target = await asyncio.to_thread(
            store.set_metric_target, profile, metric, value1, value2
        )
        if target is not None:
            created_targets.append(target)

    await record_audit("condition.template", str(condition_id))
    return {
        "ok": True,
        "template": template["name"],
        "care_items_created": created_items,
        "care_items_skipped": skipped_titles,
        "targets_created": created_targets,
        "targets_skipped": skipped_targets,
    }


@app.delete("/api/conditions/{condition_id}")
async def delete_condition(condition_id: SafeIntPath):
    """删除一条慢病/健康问题。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_condition, condition_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_condition, condition_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    await record_audit("condition.delete", str(condition_id))
    return {"ok": True, "id": condition_id}


@app.get("/api/conditions/export.csv")
async def export_conditions_csv(
    profile: Optional[str] = None,
    active_only: bool = False,
    status: Optional[str] = None,
    q: str = "",
):
    """导出慢病/健康问题 CSV（带 BOM），支持状态与关键字筛选。"""
    import csv
    import io

    from memory import get_session_store

    try:
        items = await asyncio.to_thread(
            get_session_store().list_conditions,
            profile, active_only, status, 100000, q=q,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    status_labels = {
        "active": "管理中",
        "controlled": "已控制",
        "resolved": "已痊愈",
    }
    severity_labels = {
        "mild": "轻度",
        "moderate": "中度",
        "severe": "重度",
    }
    rows = [[
        "疾病/健康问题", "患者档案", "ICD-10", "确诊日期",
        "状态", "严重程度", "备注", "ID",
    ]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("name", "")),
            csv_safe_cell(item.get("profile", "")),
            csv_safe_cell(item.get("icd10", "")),
            csv_safe_cell(item.get("diagnosed_at", "")),
            status_labels.get(item.get("status") or "", ""),
            severity_labels.get(item.get("severity") or "", ""),
            csv_safe_cell(item.get("note", "")),
            int(item.get("id") or 0),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = f"medix-conditions-{datetime.now().strftime('%Y%m%d')}.csv"
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


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
    offset = _bounded_offset(offset)
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
    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")

    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            name = key.strip()
            mapped[field_alias.get(name, name)] = csv_restore_cell(value).strip()

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

    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"symptoms": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("symptoms", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "symptom.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
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
        "symptom.create", str(item["id"]), item["recorded_at"]
    )
    return {"ok": True, "symptom": item}


@app.patch("/api/symptoms/{symptom_id}")
async def update_symptom(symptom_id: SafeIntPath, request: SymptomUpdateRequest):
    """部分更新一条症状记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_symptom, symptom_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if (
        "recorded_at" in request.model_fields_set
        and not str(request.recorded_at or "").strip()
    ):
        raise HTTPException(status_code=400, detail="记录时间不能为空")

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
        raise HTTPException(status_code=400, detail="症状内容或时间不正确")
    await record_audit(
        "symptom.update", str(item["id"])
    )
    return {"ok": True, "symptom": item}


@app.delete("/api/symptoms/{symptom_id}")
async def delete_symptom(symptom_id: SafeIntPath):
    """删除一条症状记录。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(store.get_symptom, symptom_id)
    if item is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    ok = await asyncio.to_thread(store.delete_symptom, symptom_id)
    if not ok:
        raise HTTPException(status_code=404, detail="记录不存在")
    # 旧版本审计 target 形如「id·症状」，删除时清理症状文本
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["symptom.create", "symptom.update", "symptom.delete"],
        f"{item['id']}·",
        str(item["id"]),
    )
    await record_audit("symptom.delete", str(item["id"]))
    return {"ok": True, "id": symptom_id}


@app.get("/api/metrics/latest")
async def latest_metrics(profile: Optional[str] = None, days: int = 0):
    """每种指标类型最新的一条记录（可按档案与最近天数筛选）。"""
    from memory import get_session_store

    days = max(0, min(int(days or 0), 3650))
    items = await asyncio.to_thread(
        get_session_store().latest_metrics, profile, days
    )
    from memory.session_store import critical_metric_alerts

    for item in items:
        item["critical_alerts"] = critical_metric_alerts(
            item.get("metric"), item.get("value1"), item.get("value2")
        )
    return {"metrics": items}


def _latest_vaccination_ids(vaccinations: List[Dict[str, Any]]) -> set:
    """返回每个 (档案, 疫苗) 最新一剂的记录 id。

    多剂次疫苗完成后，旧剂次的 next_due 不代表仍需补种；逾期与下一剂
    只应看最新一剂。
    """
    latest: Dict[tuple, tuple] = {}
    for item in vaccinations:
        name = str(item.get("vaccine_name") or "")
        if not name:
            continue
        key = (item.get("profile") or "", name)
        try:
            item_id = int(item.get("id") or 0)
        except (TypeError, ValueError, OverflowError):
            item_id = 0
        rank = (str(item.get("vaccinated_at") or ""), item_id)
        if key not in latest or rank > latest[key][0]:
            latest[key] = (rank, item_id)
    return {entry[1] for entry in latest.values()}


async def _weekly_report_impl(
    profile: Optional[str] = None,
    days: int = 7,
    max_days: int = 90,
    include_previous: bool = True,
):
    """近 N 天健康小结：指标、症状、用药打卡、会诊与待办提醒汇总。

    include_previous=False 时只加载本期数据，供 insights/dossier 等
    不展示环比的使用方减少查询与聚合开销。
    """
    from datetime import timedelta

    from memory import get_session_store

    days = max(1, min(int(days or 7), int(max_days or 90)))
    window_days = days * 2 if include_previous else days
    store = get_session_store()
    now = datetime.now()
    end = now.strftime("%Y-%m-%d")
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    raw_metrics = await asyncio.to_thread(
        store.list_metrics, profile, None, 100000, window_days
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
        store.list_symptoms, profile, 100000, window_days
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
        store.list_medication_logs, None, profile, 100000, 0, window_days
    )
    logs = [
        item for item in raw_logs
        if start <= str(item.get("taken_at") or "")[:10] <= end
    ]
    previous_logs = [
        item for item in raw_logs
        if prev_start <= str(item.get("taken_at") or "")[:10] < start
    ]
    taken_logs = [item for item in logs if not item.get("skipped")]
    skip_logs = [item for item in logs if item.get("skipped")]
    previous_taken_logs = [
        item for item in previous_logs if not item.get("skipped")
    ]
    previous_skip_logs = [
        item for item in previous_logs if item.get("skipped")
    ]
    session_window_start = prev_start if include_previous else start
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline,
        profile, session_window_start, f"{end}T23:59:59", 100000,
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
        profile_key = _normalized_profile_key(profile)
        latest_answers = await asyncio.to_thread(
            store.latest_session_answers_by_profile,
            profile_key == "",
        )
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
        METRIC_TYPES,
        adherence_rate,
        critical_metric_alerts,
        expected_doses_for_period,
        metric_context_label,
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
            "max1": None,
            "max2": None,
            "latest": None,
            "earliest": None,
            "target": None,
            "within": 0,
        })
        entry["count"] += 1
        first_value = float(item["value1"])
        entry["sum1"] += first_value
        entry["max1"] = (
            first_value if entry["max1"] is None
            else max(entry["max1"], first_value)
        )
        if item.get("value2") is not None:
            second_value = float(item["value2"])
            entry["sum2"] += second_value
            entry["with_second"] += 1
            entry["max2"] = (
                second_value if entry["max2"] is None
                else max(entry["max2"], second_value)
            )
        if entry["latest"] is None:
            entry["latest"] = item
        measured_at = str(item.get("measured_at") or "")
        if entry["earliest"] is None or (
            measured_at
            and measured_at < str(
                entry["earliest"].get("measured_at") or ""
            )
        ):
            entry["earliest"] = item
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
            "max_value1": entry["max1"],
            "max_value2": entry["max2"],
            "latest": entry["latest"],
            "earliest": entry["earliest"],
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
    doses_by_medication: dict = {}
    for log in taken_logs:
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
    adherence_med_ids = set()
    for item in active_meds:
        expected = expected_doses_for_period(
            item.get("frequency", ""), item.get("created_at", ""), now, days
        )
        if expected:
            adherence_expected += expected
            adherence_med_ids.add(item["id"])
    for log in taken_logs:
        # 必要时/无法估算频次的药物只展示打卡，不进入依从率分子，
        # 否则会出现“按时药一次没吃但依从率 100%”的假象
        if log["medication_id"] in adherence_med_ids:
            adherence_taken += 1
    skip_reason_counts: dict = {}
    for log in skip_logs:
        reason = str(log.get("skip_reason") or "other")
        skip_reason_counts[reason] = skip_reason_counts.get(reason, 0) + 1
    from memory.session_store import MEDICATION_SKIP_REASONS

    skip_reasons = [
        {
            "reason": reason,
            "label": MEDICATION_SKIP_REASONS.get(reason, reason),
            "count": count,
        }
        for reason, count in sorted(
            skip_reason_counts.items(), key=lambda pair: (-pair[1], pair[0])
        )[:3]
    ]
    skip_members: List[str] = []
    for item in sorted(
        skip_logs,
        key=lambda row: str(row.get("taken_at") or ""),
        reverse=True,
    ):
        name = str(item.get("profile") or "")
        if name and name not in skip_members:
            skip_members.append(name)
    skip_items = [
        {
            "medication_name": item.get("medication_name") or "已删除药物",
            "profile": item.get("profile") or "",
            "taken_at": item.get("taken_at") or "",
            "skip_reason": item.get("skip_reason") or "",
            "skip_reason_label": item.get("skip_reason_label") or "",
            "note": item.get("note") or "",
        }
        for item in sorted(
            skip_logs,
            key=lambda row: str(row.get("taken_at") or ""),
            reverse=True,
        )[:5]
    ]
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

    raw_labs = await asyncio.to_thread(
        store.list_lab_results, profile, None, None, window_days, 100000, 0
    )
    lab_results = [
        item for item in raw_labs
        if start <= str(item.get("tested_at") or "")[:10] <= end
    ]
    previous_labs = [
        item for item in raw_labs
        if prev_start <= str(item.get("tested_at") or "")[:10] < start
    ]
    abnormal_labs = [item for item in lab_results if item.get("abnormal")]
    critical_labs = [
        item for item in lab_results if item.get("critical")
    ]
    previous_abnormal = [
        item for item in previous_labs if item.get("abnormal")
    ]
    lab_rows = [
        {
            "item": item.get("item", ""),
            "value": item.get("value"),
            "unit": item.get("unit", ""),
            "tested_at": item.get("tested_at", ""),
            "abnormal_low": bool(item.get("abnormal_low")),
            "abnormal_high": bool(item.get("abnormal_high")),
        }
        for item in sorted(
            abnormal_labs,
            key=lambda row: str(row.get("tested_at") or ""),
            reverse=True,
        )[:8]
    ]
    critical_lab_items = []
    for item in sorted(
        critical_labs,
        key=lambda row: str(row.get("tested_at") or ""),
        reverse=True,
    )[:5]:
        alert = (item.get("critical_alerts") or [{}])[0]
        critical_lab_items.append({
            "item": item.get("item", ""),
            "value": item.get("value"),
            "unit": item.get("unit", ""),
            "tested_at": item.get("tested_at", ""),
            "profile": item.get("profile", ""),
            "stale": bool(alert.get("stale")),
            "message": alert.get("message") or "",
        })

    vaccinations = await asyncio.to_thread(
        store.list_vaccinations, profile, 100000
    )
    vaccinations_in_period = [
        item for item in vaccinations
        if start <= str(item.get("vaccinated_at") or "")[:10] <= end
    ]
    latest_vaccination_ids = _latest_vaccination_ids(vaccinations)
    vaccine_overdue = [
        item for item in vaccinations
        if int(item.get("id") or 0) in latest_vaccination_ids
        and str(item.get("next_due") or "")[:10]
        and str(item.get("next_due") or "")[:10] < today
    ]
    vaccine_upcoming = [
        item for item in vaccinations
        if int(item.get("id") or 0) in latest_vaccination_ids
        and today <= str(item.get("next_due") or "")[:10] <= horizon
    ]

    appointments = await asyncio.to_thread(
        store.list_appointments, profile, None, False, 100000, 0
    )
    appointments_in_period = [
        item for item in appointments
        if start <= str(item.get("scheduled_at") or "")[:10] <= end
    ]
    appointments_done = [
        item for item in appointments_in_period
        if item.get("status") == "completed"
    ]
    appointments_upcoming = [
        item for item in appointments
        if item.get("status") == "upcoming"
        and today <= str(item.get("scheduled_at") or "")[:10] <= horizon
    ]
    appointments_overdue = [
        item for item in appointments
        if item.get("status") == "upcoming"
        and str(item.get("scheduled_at") or "")[:10] < today
    ]

    care_items = await asyncio.to_thread(
        store.list_care_items, profile, False, False, 100000
    )
    care_active = [item for item in care_items if item.get("active")]
    care_due_period = [
        item for item in care_active
        if start <= str(item.get("next_due_at") or "")[:10] <= end
    ]
    care_overdue = [
        item for item in care_active
        if str(item.get("next_due_at") or "")[:10] < today
    ]
    care_due_soon = [
        item for item in care_active
        if today <= str(item.get("next_due_at") or "")[:10] <= horizon
    ]
    care_completed = [
        item for item in care_items
        if start <= str(item.get("last_done_at") or "")[:10] <= end
    ]

    raw_visits = await asyncio.to_thread(
        store.list_visit_records, profile, window_days, "", 100000
    )
    # 复诊日期可能晚于就诊日期很多，统计复诊不能只看近 2×days 天的就诊
    followup_visits = await asyncio.to_thread(
        store.list_visit_records, profile, 0, "", 100000
    )
    visits = [
        item for item in raw_visits
        if start <= str(item.get("visit_date") or "")[:10] <= end
    ]
    previous_visits = [
        item for item in raw_visits
        if prev_start <= str(item.get("visit_date") or "")[:10] < start
    ]
    upcoming_followups = [
        item for item in followup_visits
        if today <= str(item.get("next_followup_date") or "")[:10]
        <= horizon
    ]
    overdue_followups = [
        item for item in followup_visits
        if str(item.get("next_followup_date") or "")[:10]
        and str(item.get("next_followup_date") or "")[:10] < today
        and not item.get("followup_scheduled")
    ]
    # “最近就诊”不能受 2×days 窗口限制：稍旧的记录也应作为最近一次展示
    latest_visit = followup_visits[0] if followup_visits else None

    active_conditions = await asyncio.to_thread(
        store.list_conditions, profile, True, None, 100000
    )

    context_groups: dict = {}
    for item in metrics:
        metric = item.get("metric") or ""
        if metric not in ("bp", "glucose"):
            continue
        context_key = str(item.get("context") or "")
        entry = context_groups.setdefault(metric, {}).setdefault(
            context_key or "unspecified",
            {
                "context": context_key or "unspecified",
                "label": metric_context_label(metric, context_key) or "未指定",
                "count": 0,
                "sum1": 0.0,
                "sum2": 0.0,
                "with_second": 0,
            },
        )
        entry["count"] += 1
        entry["sum1"] += float(item.get("value1") or 0)
        if item.get("value2") is not None:
            entry["sum2"] += float(item.get("value2") or 0)
            entry["with_second"] += 1
    metric_contexts = {
        metric: [
            {
                "context": row["context"],
                "label": row["label"],
                "count": row["count"],
                "avg_value1": round(row["sum1"] / row["count"], 2),
                "avg_value2": (
                    round(row["sum2"] / row["with_second"], 2)
                    if row["with_second"] else None
                ),
            }
            for row in sorted(
                groups.values(), key=lambda row: row["context"]
            )
        ]
        for metric, groups in context_groups.items()
    }

    critical_count = 0
    critical_items = []
    family_scope = not str(profile or "").strip()
    for item in metrics:
        alerts = critical_metric_alerts(
            item.get("metric"), item.get("value1"), item.get("value2")
        )
        if not alerts:
            continue
        critical_count += 1
        if len(critical_items) < 5:
            label = METRIC_TYPES.get(
                item.get("metric"), (item.get("metric"), "", (0, 0))
            )[0]
            value = f"{float(item.get('value1') or 0):g}"
            if item.get("value2") is not None:
                value += f"/{float(item['value2']):g}"
            unit = item.get("unit") or ""
            text = f"{label} {value} {unit}".strip()
            if family_scope:
                member = str(item.get("profile") or "") or "未指定档案"
                text = f"{member}：{text}"
            critical_items.append(text)

    streak = _streak_info(
        await asyncio.to_thread(store.recorded_days, profile, 3650)
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
            "doses": len(taken_logs),
            "by_medication": medication_rows,
            "prev_doses": len(previous_taken_logs),
            "skips": len(skip_logs),
            "prev_skips": len(previous_skip_logs),
            "skip_reasons": skip_reasons,
            "skip_members": skip_members,
            "skip_items": skip_items,
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
        "labs": {
            "count": len(lab_results),
            "abnormal": len(abnormal_labs),
            "critical": len(critical_labs),
            "critical_items": critical_lab_items,
            "prev_count": len(previous_labs),
            "prev_abnormal": len(previous_abnormal),
            "items": lab_rows,
        },
        "vaccinations": {
            "count": len(vaccinations_in_period),
            "overdue": len(vaccine_overdue),
            "overdue_items": [
                {
                    "vaccine_name": item.get("vaccine_name", ""),
                    "next_due": item.get("next_due", ""),
                }
                for item in sorted(
                    vaccine_overdue,
                    key=lambda row: str(row.get("next_due") or ""),
                )[:5]
            ],
            "upcoming": [
                {
                    "vaccine_name": item.get("vaccine_name", ""),
                    "dose_number": item.get("dose_number"),
                    "next_due": item.get("next_due", ""),
                }
                for item in sorted(
                    vaccine_upcoming,
                    key=lambda row: str(row.get("next_due") or ""),
                )[:5]
            ],
        },
        "care_plan": {
            "due_in_period": len(care_due_period),
            "completed": len(care_completed),
            "overdue": len(care_overdue),
            "due_in_7_days": len(care_due_soon),
            "items": [
                {
                    "title": item.get("title", ""),
                    "next_due_at": item.get("next_due_at", ""),
                    "overdue": bool(item.get("overdue")),
                }
                for item in sorted(
                    care_overdue + care_due_soon,
                    key=lambda row: str(row.get("next_due_at") or ""),
                )[:5]
            ],
        },
        "appointments": {
            "in_period": len(appointments_in_period),
            "total": len(appointments),
            "completed": len(appointments_done),
            "overdue": len(appointments_overdue),
            "overdue_items": [
                {
                    "title": item.get("title", ""),
                    "scheduled_at": item.get("scheduled_at", ""),
                }
                for item in sorted(
                    appointments_overdue,
                    key=lambda row: str(row.get("scheduled_at") or ""),
                )[:5]
            ],
            "upcoming": [
                {
                    "title": item.get("title", ""),
                    "scheduled_at": item.get("scheduled_at", ""),
                    "department": item.get("department", ""),
                    "doctor": item.get("doctor", ""),
                }
                for item in sorted(
                    appointments_upcoming,
                    key=lambda row: str(row.get("scheduled_at") or ""),
                )[:5]
            ],
        },
        "visits": {
            "count": len(visits),
            "prev_count": len(previous_visits),
            "latest": (
                {
                    "visit_date": latest_visit.get("visit_date", ""),
                    "diagnosis": latest_visit.get("diagnosis", ""),
                    "department": latest_visit.get("department", ""),
                    "doctor": latest_visit.get("doctor", ""),
                    "next_followup_date": latest_visit.get(
                        "next_followup_date", ""
                    ),
                    "followup_scheduled": bool(
                        latest_visit.get("followup_scheduled")
                    ),
                }
                if latest_visit else None
            ),
            "overdue_followups": [
                {
                    "diagnosis": item.get("diagnosis", ""),
                    "next_followup_date": item.get(
                        "next_followup_date", ""
                    ),
                }
                for item in sorted(
                    overdue_followups,
                    key=lambda row: str(
                        row.get("next_followup_date") or ""
                    ),
                )[:5]
            ],
            "upcoming_followups": [
                {
                    "diagnosis": item.get("diagnosis", ""),
                    "next_followup_date": item.get(
                        "next_followup_date", ""
                    ),
                }
                for item in sorted(
                    upcoming_followups,
                    key=lambda row: str(row.get("next_followup_date") or ""),
                )[:5]
            ],
        },
        "conditions": {
            "active": len(active_conditions),
            "names": [
                item.get("name") for item in active_conditions[:5]
                if item.get("name")
            ],
        },
        "metric_contexts": metric_contexts,
        "critical_alerts": {
            "count": critical_count,
            "items": critical_items,
        },
        "streak": streak,
    }


HEALTH_INSIGHT_LEVEL_ORDER = {"danger": 0, "warning": 1, "info": 2, "ok": 3}


def _health_insight(
    key: str,
    level: str,
    title: str,
    detail: str = "",
    action: str = "",
    section: str = "",
    metric: str = "",
) -> Dict[str, str]:
    return {
        "key": key,
        "level": level,
        "title": str(title)[:120],
        "detail": str(detail)[:200],
        "action": str(action)[:160],
        "section": section,
        "metric": metric,
    }


def _build_health_insights(
    report: Dict[str, Any],
    safety: Optional[Dict[str, Any]] = None,
    allow_trends: bool = True,
) -> List[Dict[str, str]]:
    """把近 N 天健康汇总转成按优先级排序的可执行提示。"""
    from memory.session_store import (
        LOWER_BOUND_TARGET_METRICS,
        METRIC_TYPES,
    )

    days = int(report.get("days") or 7)
    insights: List[Dict[str, str]] = []
    family_scope = not str(report.get("profile") or "").strip()

    critical = report.get("critical_alerts") or {}
    if critical.get("count"):
        insights.append(_health_insight(
            "critical",
            "danger",
            f"近 {days} 天出现 {critical['count']} 次危急值",
            "、".join(critical.get("items") or [])[:200],
            "请立即复测；如仍异常，联系医生或前往急诊。",
            "metric",
        ))

    if (
        safety
        and safety.get("level") == "warning"
        and safety.get("alerts")
    ):
        alerts = safety.get("alerts") or []
        source_labels = (
            ("lab", "化验"),
            ("condition", "慢病用药"),
            ("base", "过敏/相互作用"),
        )
        source_counts: Dict[str, int] = {}
        for item in alerts:
            source_key = str(item.get("source") or "base")
            source_counts[source_key] = (
                source_counts.get(source_key, 0) + 1
            )
        summary = "、".join(
            f"{label} {source_counts[key]} 条"
            for key, label in source_labels
            if source_counts.get(key)
        )
        titles = "；".join(
            str(item.get("title") or "") for item in alerts[:3]
        )
        insights.append(_health_insight(
            "medication_safety",
            "warning",
            f"在用药物存在 {len(alerts)} 条安全提示",
            (summary + (f"；{titles}" if titles else ""))[:200],
            "请让医生或药师确认后再调整用药。",
            "medication",
        ))

    for row in (report.get("metrics") or {}).get("by_metric") or []:
        metric = str(row.get("metric") or "")
        label, unit, _ = METRIC_TYPES.get(metric, (metric, "", (0, 0)))
        count = int(row.get("count") or 0)
        if count <= 0:
            continue
        target = row.get("target") or None
        within = row.get("within_target")
        avg1 = float(row.get("avg_value1") or 0)
        avg2 = float(row.get("avg_value2") or 0)
        if target and within is not None:
            over = count - int(within)
            if over > 0:
                target_text = f"{float(target['value1']):g}"
                if target.get("value2") is not None:
                    target_text += f"/{float(target['value2']):g}"
                latest = row.get("latest") or {}
                latest_text = ""
                if latest:
                    latest_text = (
                        "，最近 "
                        + _health_search_number(latest.get("value1"))
                    )
                    if latest.get("value2") is not None:
                        latest_text += (
                            "/"
                            + _health_search_number(latest.get("value2"))
                        )
                insights.append(_health_insight(
                    f"metric_{metric}",
                    "warning" if over >= 2 else "info",
                    f"{label} {over} 次"
                    + ("低于目标" if metric in LOWER_BOUND_TARGET_METRICS
                       else "超出目标")
                    + f"（共 {count} 次）",
                    (
                        f"目标"
                        + ("≥" if metric in LOWER_BOUND_TARGET_METRICS
                           else "≤")
                        + f" {target_text} {unit}{latest_text}"
                    ).strip(),
                    (
                        "复测后仍低于目标时，与医生确认是否需要调整方案。"
                        if metric in LOWER_BOUND_TARGET_METRICS
                        else "复测后仍超标时，与医生确认是否需要调整方案。"
                    ),
                    "metric",
                    metric=metric,
                ))
        elif metric == "bp":
            max1 = float(row.get("max_value1") or 0)
            max2 = float(row.get("max_value2") or 0)
            if max1 >= 160 or max2 >= 100:
                insights.append(_health_insight(
                    "metric_bp_single",
                    "warning",
                    (
                        f"家庭成员单次血压最高 {max1:g}/{max2:g} mmHg"
                        if family_scope
                        else f"单次血压最高 {max1:g}/{max2:g} mmHg"
                    ),
                    (
                        "家庭范围内单次最高读数；请按成员分别查看各自血压。"
                        if family_scope
                        else f"近 {days} 天平均 {avg1:g}/{avg2:g} mmHg。"
                    ),
                    "单次明显升高也需要复测；持续偏高请咨询医生。",
                    "metric",
                    metric="bp",
                ))
            elif max1 >= 140 or max2 >= 90:
                insights.append(_health_insight(
                    "metric_bp_single",
                    "info",
                    (
                        f"家庭成员有血压读数达到 {max1:g}/{max2:g} mmHg"
                        if family_scope
                        else f"有血压读数达到 {max1:g}/{max2:g} mmHg"
                    ),
                    (
                        "家庭范围内出现偏高读数；"
                        "建议按成员分别复测并记录。"
                        if family_scope
                        else f"近 {days} 天平均 {avg1:g}/{avg2:g} mmHg；"
                        "建议早晚测量并记录。"
                    ),
                    "设置血压目标后可自动判断是否达标。",
                    "metric",
                    metric="bp",
                ))
        elif metric == "glucose":
            contexts = {
                str(item.get("context") or "unspecified"): item
                for item in (report.get("metric_contexts") or {})
                .get("glucose", [])
            }
            fasting_groups = [
                contexts[key]
                for key in ("fasting", "unspecified")
                if contexts.get(key)
            ]
            fasting_count = sum(
                int(group.get("count") or 0) for group in fasting_groups
            )
            fasting_avg = (
                sum(
                    float(group.get("avg_value1") or 0)
                    * int(group.get("count") or 0)
                    for group in fasting_groups
                ) / fasting_count
                if fasting_count else 0.0
            )
            postprandial = contexts.get("postprandial")
            if fasting_count and fasting_avg >= 7:
                insights.append(_health_insight(
                    "metric_glucose_fasting",
                    "info",
                    f"空腹/未分情境血糖平均 "
                    f"{fasting_avg:g} mmol/L 偏高",
                    f"共 {fasting_count} 次；"
                    "建议按空腹与餐后分别记录。",
                    "如多次偏高，请咨询医生。",
                    "metric",
                    metric="glucose",
                ))
            elif (
                postprandial
                and float(postprandial.get("avg_value1") or 0) >= 11.1
            ):
                insights.append(_health_insight(
                    "metric_glucose_postprandial",
                    "info",
                    f"餐后血糖平均 "
                    f"{float(postprandial['avg_value1']):g} mmol/L 明显偏高",
                    f"共 {postprandial.get('count')} 次。",
                    "如多次偏高，请咨询医生。",
                    "metric",
                    metric="glucose",
                ))
        if allow_trends and metric == "weight" and count >= 2:
            earliest = row.get("earliest") or {}
            latest = row.get("latest") or {}
            try:
                first_value = float(earliest.get("value1"))
                last_value = float(latest.get("value1"))
            except (TypeError, ValueError):
                first_value = last_value = None
            if first_value and last_value and first_value > 0:
                change = last_value - first_value
                pct = round(abs(change) / first_value * 100, 2)
                direction = "下降" if change < 0 else "增加"
                if pct >= 5:
                    insights.append(_health_insight(
                        "metric_weight_change",
                        "warning",
                        f"体重近 {days} 天{direction} {pct:.1f}%"
                        f"（{first_value:g}→{last_value:g} kg）",
                        "非刻意的明显体重变化建议关注饮食、活动与"
                        "基础疾病。",
                        "如持续变化或伴随其他症状，请咨询医生。",
                        "metric",
                        metric="weight",
                    ))
                elif pct >= 2:
                    insights.append(_health_insight(
                        "metric_weight_change",
                        "info",
                        f"体重近 {days} 天{direction} {pct:.1f}%",
                        f"{first_value:g}→{last_value:g} kg。",
                        "",
                        "metric",
                        metric="weight",
                    ))

    bp_contexts = {
        str(item.get("context") or ""): item
        for item in (report.get("metric_contexts") or {}).get("bp", [])
    }
    morning = bp_contexts.get("morning")
    evening = bp_contexts.get("evening")
    if (
        allow_trends
        and morning
        and evening
        and int(morning.get("count") or 0) >= 2
        and int(evening.get("count") or 0) >= 2
    ):
        morning_sys = float(morning.get("avg_value1") or 0)
        evening_sys = float(evening.get("avg_value1") or 0)
        delta = morning_sys - evening_sys
        if delta >= 10:
            insights.append(_health_insight(
                "metric_bp_morning_evening",
                "info" if delta < 20 else "warning",
                f"晨间收缩压平均比睡前高 {delta:.0f} mmHg",
                f"晨间 {morning_sys:g}/"
                f"{float(morning.get('avg_value2') or 0):g}，"
                f"睡前 {evening_sys:g}/"
                f"{float(evening.get('avg_value2') or 0):g}。",
                "晨峰明显时可与医生讨论测量与用药时间。",
                "metric",
                metric="bp",
            ))

    symptoms = report.get("symptoms") or {}
    max_severity = int(symptoms.get("max_severity") or 0)
    if max_severity >= 7:
        insights.append(_health_insight(
            "symptoms_severe",
            "warning",
            f"近 {days} 天出现重度症状（最高 {max_severity}/10）",
            "、".join(
                str(item.get("symptom") or "")
                for item in (symptoms.get("top") or [])[:3]
            ),
            "若症状持续或加重，请及时就医。",
            "symptom",
        ))

    consultations = report.get("consultations") or {}
    if consultations.get("risk") in {"high", "emergency"}:
        insights.append(_health_insight(
            "consultation_risk",
            "warning",
            "最近一次会诊提示较高风险",
            f"近 {days} 天会诊 {consultations.get('count', 0)} 次。",
            "请按会诊建议尽快线下就医或复诊。",
            "",
        ))

    adherence = (report.get("medications") or {}).get("adherence") or {}
    if adherence.get("expected", 0) >= 3 and adherence.get("rate", 100) < 60:
        rate = int(adherence.get("rate") or 0)
        insights.append(_health_insight(
            "adherence",
            "warning" if rate < 40 else "info",
            f"近 {days} 天用药打卡 {rate}%",
            f"应服 {adherence.get('expected')} 次，"
            f"已打卡 {adherence.get('taken')} 次。",
            "漏服较多时可调整用药提醒或与医生确认服药方案。",
            "medication",
        ))

    medication_skips = int(
        (report.get("medications") or {}).get("skips") or 0
    )
    if medication_skips:
        medications_report = report.get("medications") or {}
        skip_reasons = medications_report.get("skip_reasons") or []
        detail_parts = [
            f"{item.get('label') or item.get('reason')} "
            f"{int(item.get('count') or 0)} 次"
            for item in skip_reasons
        ]
        if not str(report.get("profile") or ""):
            members = [
                str(name)
                for name in medications_report.get("skip_members") or []
                if name
            ]
            if members:
                shown = "、".join(members[:3])
                if len(members) > 3:
                    shown += f" 等 {len(members)} 人"
                detail_parts.append(f"涉及 {shown}")
        insights.append(_health_insight(
            "medication_skips",
            "warning" if medication_skips >= 3 else "info",
            f"近 {days} 天登记未服 {medication_skips} 次",
            "；".join(detail_parts)[:200],
            "反复未服可与医生或药师讨论服药方案，不要自行停药。",
            "medication",
        ))

    labs = report.get("labs") or {}
    if labs.get("critical"):
        critical_labs = labs.get("critical_items") or []
        scope_profile = str(report.get("profile") or "")
        detail_parts = []
        for item in critical_labs[:5]:
            prefix = ""
            if not scope_profile and item.get("profile"):
                prefix = f"{item.get('profile')}："
            text = (
                f"{prefix}{item.get('item', '')} "
                f"{_health_search_number(item.get('value'))}"
                f"{item.get('unit') or ''}"
            )
            if item.get("stale"):
                text += "（结果较旧）"
            detail_parts.append(text)
        insights.append(_health_insight(
            "lab_critical",
            "danger",
            f"近 {days} 天 {labs['critical']} 项化验达到危急值",
            "、".join(detail_parts)[:200],
            "请立即复测并联系医生或前往急诊；以检验报告标注与本机构标准为准。",
            "lab",
        ))
    if labs.get("abnormal"):
        items = labs.get("items") or []
        detail = "、".join(
            f"{item.get('item', '')} "
            f"{_health_search_number(item.get('value'))}"
            f"{item.get('unit') or ''}"
            for item in items[:5]
        )
        insights.append(_health_insight(
            "labs",
            "warning",
            f"近 {days} 天 {labs['abnormal']} 项化验异常",
            detail,
            "请结合参考范围复查，必要时咨询医生。",
            "lab",
        ))

    care = report.get("care_plan") or {}
    if care.get("overdue"):
        overdue_items = [
            item for item in care.get("items") or []
            if item.get("overdue")
        ]
        insights.append(_health_insight(
            "care_overdue",
            "warning",
            f"{care['overdue']} 项复查已逾期",
            "、".join(
                f"{item.get('title', '')}（{item.get('next_due_at', '')}）"
                for item in overdue_items[:5]
            ),
            "请尽快安排复查并记录结果。",
            "care",
        ))
    elif care.get("due_in_7_days"):
        insights.append(_health_insight(
            "care_due_soon",
            "info",
            f"未来 7 天有 {care['due_in_7_days']} 项复查到期",
            "、".join(
                item.get("title", "")
                for item in (care.get("items") or [])[:5]
            ),
            "可提前预约检查。",
            "care",
        ))

    vaccines = report.get("vaccinations") or {}
    if vaccines.get("overdue"):
        insights.append(_health_insight(
            "vaccine_overdue",
            "info",
            f"{vaccines['overdue']} 剂疫苗已逾期",
            "、".join(
                f"{item.get('vaccine_name', '')}"
                f"（{item.get('next_due', '')}）"
                for item in (vaccines.get("overdue_items") or [])[:5]
            ),
            "请核对接种记录并咨询接种点。",
            "vaccination",
        ))
    elif vaccines.get("upcoming"):
        first = vaccines["upcoming"][0]
        insights.append(_health_insight(
            "vaccine_upcoming",
            "info",
            f"疫苗 {first.get('vaccine_name')} 将在 "
            f"{first.get('next_due')} 到期",
            f"第 {first.get('dose_number') or 1} 剂",
            "",
            "vaccination",
        ))

    appointments = report.get("appointments") or {}
    if appointments.get("overdue"):
        insights.append(_health_insight(
            "appointment_overdue",
            "info",
            f"{appointments['overdue']} 个就诊预约已过期未更新",
            "、".join(
                f"{item.get('title', '')}"
                f"（{str(item.get('scheduled_at') or '')[:16].replace('T', ' ')}）"
                for item in (appointments.get("overdue_items") or [])[:5]
            ),
            "请确认实际就诊情况并更新状态。",
            "appointment",
        ))
    if appointments.get("upcoming"):
        first = appointments["upcoming"][0]
        insights.append(_health_insight(
            "appointment_upcoming",
            "info",
            f"下一次就诊：{first.get('title') or '就诊'}",
            " · ".join(part for part in (
                str(first.get("scheduled_at") or "")
                .replace("T", " ")[:16],
                first.get("department") or "",
                first.get("doctor") or "",
            ) if part),
            "可提前一天创建就诊提醒。",
            "appointment",
        ))

    visits = report.get("visits") or {}
    followups = visits.get("upcoming_followups") or []
    overdue_followups = visits.get("overdue_followups") or []
    if overdue_followups:
        first = overdue_followups[0]
        insights.append(_health_insight(
            "followup_overdue",
            "warning",
            f"复诊已逾期（{first.get('next_followup_date')}）",
            first.get("diagnosis") or "",
            "请尽快联系医院安排复诊。",
            "visit",
        ))
    elif followups:
        first = followups[0]
        insights.append(_health_insight(
            "followup_upcoming",
            "info",
            f"下次复诊：{first.get('next_followup_date')}",
            first.get("diagnosis") or "",
            "可一键安排复诊预约与提醒。",
            "visit",
        ))

    reminders = report.get("reminders") or {}
    if reminders.get("overdue"):
        insights.append(_health_insight(
            "reminder_overdue",
            "warning",
            f"{reminders['overdue']} 条提醒已逾期",
            "请处理后标记完成或调整时间。",
            "",
            "reminder",
        ))
    elif reminders.get("due_in_7_days"):
        insights.append(_health_insight(
            "reminder_due_soon",
            "info",
            f"未来 7 天有 {reminders['due_in_7_days']} 条提醒到期",
            "",
            "",
            "reminder",
        ))

    if not insights:
        record_count = (
            int((report.get("metrics") or {}).get("count") or 0)
            + int((report.get("symptoms") or {}).get("count") or 0)
            + int((report.get("labs") or {}).get("count") or 0)
            + int((report.get("visits") or {}).get("count") or 0)
            + int((report.get("appointments") or {}).get("total") or 0)
            + int((report.get("care_plan") or {}).get("due_in_period") or 0)
            + int((report.get("vaccinations") or {}).get("count") or 0)
            + int((report.get("medications") or {}).get("active") or 0)
            + int((report.get("reminders") or {}).get("pending") or 0)
        )
        if record_count == 0:
            insights.append(_health_insight(
                "no_data",
                "info",
                f"近 {days} 天还没有健康记录",
                "可以从记录一次血压、血糖或症状开始。",
                "",
                "metric",
            ))
        else:
            insights.append(_health_insight(
                "all_clear",
                "ok",
                "暂未发现需要特别关注的问题",
                "继续保持记录，提示会随数据自动更新。",
                "",
                "",
            ))

    insights.sort(
        key=lambda item: HEALTH_INSIGHT_LEVEL_ORDER.get(item["level"], 9)
    )
    return insights[:8]


@app.get("/api/health/insights")
async def health_insights(profile: Optional[str] = None, days: int = 30):
    """基于近 N 天健康数据的规则化提示。"""
    from memory import get_session_store

    store = get_session_store()
    safety = None
    if profile is not None:
        safety = await _medication_safety_for_profile(store, profile)
    else:
        # 家庭范围一次加载全部成员数据后分组计算，避免每个成员重复查询
        safety = await _family_medication_safety(store)
    report = await _weekly_report_impl(
        profile=profile, days=days, include_previous=False
    )
    insights = _build_health_insights(
        report,
        safety=safety,
        # 家庭范围下记录来自不同成员，跨成员的趋势没有意义
        allow_trends=profile is not None,
    )
    counts = {"danger": 0, "warning": 0, "info": 0, "ok": 0}
    for item in insights:
        counts[item["level"]] = counts.get(item["level"], 0) + 1
    return {
        "profile": report.get("profile") or "",
        "days": report.get("days"),
        "start": report.get("start"),
        "end": report.get("end"),
        "counts": counts,
        "insights": insights,
    }


@app.get("/api/reports/weekly")
async def weekly_report(
    profile: Optional[str] = None,
    days: int = 7,
    include_previous: bool = True,
):
    """近 N 天健康小结：指标、症状、用药打卡、会诊与待办提醒汇总。

    include_previous=false 供首页概览等不展示环比的调用方使用，避免额外
    加载上一周期数据。
    """
    return await _weekly_report_impl(
        profile=profile, days=days, include_previous=include_previous
    )


DATA_QUALITY_LEVEL_ORDER = {"warning": 0, "info": 1}

#: 化验名中按字母边界匹配的无单位项目，兼容 PT-INR、INR（PT）、尿 pH、HOMA-IR 等写法
_LAB_UNITLESS_PATTERN = re.compile(
    r"(?<![a-z])(?:inr|ph|homa[\s\-]?ir)(?![a-z])"
)
#: 以浓度比表示、实际带单位的「比值」项目（如尿白蛋白/肌酐比值，单位 mg/g）
_LAB_RATIO_WITH_UNIT = re.compile(
    r"(白蛋白.*肌酐|肌酐.*白蛋白|蛋白.*肌酐|"
    r"albumin.*creatinine|creatinine.*albumin|uacr)"
)


def _lab_item_unitless(name: Any) -> bool:
    text = " ".join(str(name or "").lower().split())
    if not text:
        return False
    if _LAB_UNITLESS_PATTERN.search(text):
        return True
    if _LAB_RATIO_WITH_UNIT.search(text):
        return False
    return any(
        token in text
        for token in (
            "国际标准化", "比值", "ratio", "指数", "评分", "滴度",
        )
    )


@app.get("/api/health/data-quality")
async def health_data_quality(profile: Optional[str] = None):
    """健康数据完整性检查：重复记录、缺失单位、过期用药与逾期提醒。"""
    from memory import get_session_store

    store = get_session_store()
    (
        labs, medications, vaccinations, care_items, reminders,
        attachments, attachment_files,
    ) = await asyncio.gather(
        asyncio.to_thread(
            store.list_lab_results, profile, None, None, 0, 100000, 0
        ),
        asyncio.to_thread(
            store.list_medications, profile, True, 100000, 0, 7
        ),
        asyncio.to_thread(store.list_vaccinations, profile, 100000),
        asyncio.to_thread(
            store.list_care_items, profile, True, False, 100000
        ),
        asyncio.to_thread(store.list_reminders, False, profile),
        asyncio.to_thread(
            store.list_all_attachments,
        ),
        asyncio.to_thread(_attachment_dir_files),
    )
    if profile is not None:
        profile_key = _normalized_profile_key(profile)
        attachments = [
            item for item in attachments
            if _normalized_profile_key(item.get("profile")) == profile_key
        ]

    issues: List[Dict[str, Any]] = []

    def add(
        key: str,
        level: str,
        title: str,
        detail: str,
        hint: str,
        section: str,
        count: int,
        query: str = "",
    ) -> None:
        if count <= 0:
            return
        issues.append({
            "key": key,
            "level": level,
            "title": title,
            "detail": str(detail or "")[:200],
            "hint": str(hint or "")[:160],
            "section": section,
            "count": int(count),
            "query": str(query or "")[:80],
        })

    missing_unit = [
        item for item in labs
        if not str(item.get("unit") or "").strip()
        and not _lab_item_unitless(item.get("item"))
    ]
    if missing_unit:
        names: List[str] = []
        for item in missing_unit:
            name = str(item.get("item") or "").strip()
            if name and name not in names:
                names.append(name)
        add(
            "lab_missing_unit", "warning",
            f"{len(missing_unit)} 条化验缺少单位",
            "、".join(names[:5]),
            "补全单位后才能正确换算与对比趋势。",
            "lab", len(missing_unit),
        )

    medication_groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for item in medications:
        # 停用记录属于历史疗程，与再次启用的同药记录不算重复登记
        if not item.get("active"):
            continue
        key = (
            item.get("profile") or "",
            str(item.get("name") or "").strip().lower(),
            str(item.get("dose") or "").strip().lower(),
            str(item.get("frequency") or "").strip().lower(),
        )
        if not key[1]:
            continue
        medication_groups.setdefault(key, []).append(item)
    duplicate_meds = [
        rows for rows in medication_groups.values() if len(rows) > 1
    ]
    if duplicate_meds:
        names = [
            f"{rows[0].get('name') or '药物'} ×{len(rows)}"
            for rows in duplicate_meds[:5]
        ]
        add(
            "medication_duplicate", "warning",
            f"{len(duplicate_meds)} 组重复用药",
            "、".join(names),
            "确认是否为重复登记，保留一条即可。",
            "medication", len(duplicate_meds),
        )

    vaccination_groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for item in vaccinations:
        key = (
            item.get("profile") or "",
            str(item.get("vaccine_name") or "").strip().lower(),
            int(item.get("dose_number") or 1),
            str(item.get("vaccinated_at") or "")[:10],
        )
        if not key[1]:
            continue
        vaccination_groups.setdefault(key, []).append(item)
    duplicate_vaccines = [
        rows for rows in vaccination_groups.values() if len(rows) > 1
    ]
    if duplicate_vaccines:
        names = [
            f"{rows[0].get('vaccine_name')} "
            f"第{rows[0].get('dose_number') or 1}剂"
            for rows in duplicate_vaccines[:5]
        ]
        add(
            "vaccination_duplicate", "warning",
            f"{len(duplicate_vaccines)} 组重复疫苗记录",
            "、".join(names),
            "核对接种记录后删除重复条目。",
            "vaccination", len(duplicate_vaccines),
        )

    care_groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for item in care_items:
        if not item.get("active"):
            continue
        key = (
            item.get("profile") or "",
            str(item.get("title") or "").strip().lower(),
        )
        if not key[1]:
            continue
        care_groups.setdefault(key, []).append(item)
    duplicate_care = [
        rows for rows in care_groups.values() if len(rows) > 1
    ]
    if duplicate_care:
        add(
            "care_duplicate", "warning",
            f"{len(duplicate_care)} 组重复复查计划",
            "、".join(
                f"{rows[0].get('title') or '复查'} ×{len(rows)}"
                for rows in duplicate_care[:5]
            ),
            "确认是否重复登记，保留或停用其中一条。",
            "care", len(duplicate_care),
        )

    today = datetime.now().strftime("%Y-%m-%d")
    # 今天到期不算逾期：提醒抽屉里「已逾期」与「今天到期」是分开的两组
    overdue_reminders = [
        item for item in reminders
        if str(item.get("due_at") or "")[:10] < today
    ]
    if overdue_reminders:
        add(
            "reminder_overdue", "info",
            f"{len(overdue_reminders)} 条提醒已逾期",
            "",
            "处理后标记完成或调整日期，避免长期堆积。",
            "reminder", len(overdue_reminders),
        )

    stock_issues = [
        item for item in medications
        if item.get("active")
        and (item.get("expired") or item.get("out_of_stock"))
    ]
    if stock_issues:
        add(
            "medication_stock", "warning",
            f"{len(stock_issues)} 种在用药物已过期或缺货",
            "、".join(
                str(item.get("name") or "")
                for item in stock_issues[:5]
            ),
            "及时补货或更新用药清单。",
            "medication", len(stock_issues),
        )

    file_names = {
        os.path.normcase(name) for name in attachment_files
    }
    file_sizes = {
        os.path.normcase(name): int(info.get("size") or 0)
        for name, info in attachment_files.items()
    }
    missing_attachments = [
        item for item in attachments
        if os.path.normcase(str(item.get("stored_name") or ""))
        not in file_names
    ]
    size_mismatched = []
    for item in attachments:
        key = os.path.normcase(str(item.get("stored_name") or ""))
        if key not in file_sizes:
            continue
        try:
            db_size = int(item.get("size") or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if db_size != file_sizes[key]:
            size_mismatched.append(item)
    if missing_attachments:
        add(
            "attachment_missing", "warning",
            f"{len(missing_attachments)} 份附件文件缺失",
            "、".join(
                str(item.get("original_name") or "附件")
                for item in missing_attachments[:5]
            ),
            "在就医资料中运行完整性检查，必要时删除缺失登记。",
            "attachment", len(missing_attachments),
        )
    if size_mismatched:
        add(
            "attachment_size_mismatch", "warning",
            f"{len(size_mismatched)} 份附件文件大小与登记不一致",
            "、".join(
                str(item.get("original_name") or "附件")
                for item in size_mismatched[:5]
            ),
            "文件可能被截断或替换，建议重新上传或删除后重试。",
            "attachment", len(size_mismatched),
        )

    attachment_groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for item in attachments:
        name = str(item.get("original_name") or "").strip().lower()
        if not name:
            continue
        key = (
            item.get("profile") or "",
            name,
            int(item.get("size") or 0),
        )
        attachment_groups.setdefault(key, []).append(item)
    duplicate_attachments = [
        rows for rows in attachment_groups.values() if len(rows) > 1
    ]
    if duplicate_attachments:
        names = [
            f"{rows[0].get('original_name') or '附件'} ×{len(rows)}"
            for rows in duplicate_attachments[:5]
        ]
        add(
            "attachment_duplicate", "warning",
            f"{len(duplicate_attachments)} 组附件可能重复",
            "、".join(names),
            "核对文件名与大小，确认后可删除多余附件。",
            "attachment", len(duplicate_attachments),
            duplicate_attachments[0][0].get("original_name") or "",
        )

    issues.sort(
        key=lambda item: (
            DATA_QUALITY_LEVEL_ORDER.get(item["level"], 9),
            -item["count"],
        )
    )
    return {
        "profile": profile or "",
        "total": len(issues),
        "issues": issues,
    }


async def _list_attachments_paged(
    store,
    profile: Optional[str] = None,
    start: str = "",
    end: str = "",
) -> List[Dict[str, Any]]:
    """一次读取附件登记（时间线与健康搜索只需轻量字段）。"""
    return await asyncio.to_thread(
        store.list_all_attachments, profile, start, end
    )


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
    from memory.session_store import (
        LOWER_BOUND_TARGET_METRICS, METRIC_TYPES,
        metric_context_label, metric_over_target,
    )

    day = str(date or "").strip()
    if day:
        try:
            day = datetime.strptime(day, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail="日期格式应为 YYYY-MM-DD"
            )
    days = max(1, min(int(days or 30), 365))
    limit = max(1, min(int(limit or 100), 100000))
    type_key = str(type or "").strip()

    def source_limit(kind: str) -> int:
        """单个数据源最多读取多少行。

        每类都按时间倒序返回，5×limit（至少 1000）足够覆盖全局前 limit 条
        与“加载更多”分页，同时避免把 10 万行全部物化成事件字典。
        q/alert/risk 是合并后的后置过滤，命中行可能落在截断之外，
        因此带筛选时放宽读取上限。type 指定时其余来源只取 1 条。
        """
        if type_key and type_key != kind:
            return 1
        if str(q or "").strip() or only_alert or only_risk:
            return min(max(limit * 20, 5000), 20000)
        return max(limit * 5, 1000)

    store = get_session_store()
    if day:
        start = day
        window_days = 0
    else:
        start = (
            datetime.now() - timedelta(days=days - 1)
        ).strftime("%Y-%m-%d")
        window_days = days
    window_end = day or ""
    attachment_end = day or datetime.now().strftime("%Y-%m-%d")
    raw_metrics = await asyncio.to_thread(
        store.list_metrics,
        profile, None, source_limit("metric"), window_days, 0, "",
        start, window_end,
    )
    raw_symptoms = await asyncio.to_thread(
        store.list_symptoms,
        profile, source_limit("symptom"), window_days, 0,
        start, window_end,
    )
    raw_logs = await asyncio.to_thread(
        store.list_medication_logs,
        None, profile, source_limit("medication"), 0, window_days,
        start, window_end,
    )
    all_vaccinations = await asyncio.to_thread(
        store.list_vaccinations, profile, 100000
    )
    vaccination_end = day or datetime.now().strftime("%Y-%m-%d")
    raw_vaccinations = [
        item for item in all_vaccinations
        if start <= str(item.get("vaccinated_at") or "")[:10]
        <= vaccination_end
    ]
    latest_vaccination_ids = _latest_vaccination_ids(all_vaccinations)
    raw_appointments = await asyncio.to_thread(
        store.list_appointments,
        profile, None, False, source_limit("appointment"), 0,
        start, window_end,
    )
    raw_lab_results = await asyncio.to_thread(
        store.list_lab_results,
        profile, None, None, 0, source_limit("lab"), 0,
        start, window_end,
    )
    raw_care_items = await asyncio.to_thread(
        store.list_care_items,
        profile, False, False, source_limit("care"), start, window_end,
    )
    raw_visit_records = await asyncio.to_thread(
        store.list_visit_records,
        profile, 0, "", source_limit("visit"), start, window_end,
    )
    raw_attachments = await _list_attachments_paged(
        store, profile, start, attachment_end
    )
    session_end = (
        f"{day}T23:59:59" if day
        else datetime.now().replace(
            hour=23, minute=59, second=59
        ).isoformat(timespec="seconds")
    )
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline,
        profile, start, session_end, source_limit("consultation"), True,
    )
    targets = await asyncio.to_thread(store.list_metric_targets, None)
    target_map = {
        (item.get("profile") or "", item.get("metric")): item
        for item in targets
        if item.get("metric")
    }

    events: List[Dict[str, Any]] = []
    now_window = datetime.now()
    today_key = now_window.strftime("%Y-%m-%d")
    # 保留一周内的预约/复查，但不让几个月后的远期事件排在最前
    future_limit = (
        now_window + timedelta(days=7)
    ).strftime("%Y-%m-%d")

    def in_window(at: str) -> bool:
        if not at:
            return False
        if day:
            return at[:10] == day
        return start <= at[:10] <= future_limit

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
        context_label = metric_context_label(
            item["metric"], item.get("context")
        )
        value_text = f"{value} {unit}".strip()
        if context_label:
            value_text += f"（{context_label}）"
        target = target_map.get(
            (item.get("profile") or "", item["metric"])
        )
        alert = bool(
            target is not None
            and metric_over_target(
                item["metric"], item["value1"], item.get("value2"), target
            )
        )
        from memory.session_store import critical_metric_alerts

        critical_alerts = critical_metric_alerts(
            item["metric"], item["value1"], item.get("value2")
        )
        if critical_alerts:
            alert = True
        events.append({
            "type": "metric",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": label,
            "value": value_text,
            "note": item.get("note", ""),
            "alert": alert,
            "critical": bool(critical_alerts),
            "critical_message": (
                critical_alerts[0]["message"] if critical_alerts else ""
            ),
            "target_direction": (
                "low"
                if item["metric"] in LOWER_BOUND_TARGET_METRICS
                else "high"
            ),
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
        skipped = bool(item.get("skipped"))
        details = [
            str(item.get("medication_dose") or "").strip(),
            "未服" if skipped else "打卡",
        ]
        if skipped and item.get("skip_reason_label"):
            details.append(str(item["skip_reason_label"]))
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

    for item in raw_vaccinations:
        at = str(item.get("vaccinated_at") or "")
        if not in_window(at):
            continue
        next_due = str(item.get("next_due") or "")[:10]
        today = datetime.now().strftime("%Y-%m-%d")
        events.append({
            "type": "vaccination",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("vaccine_name") or "疫苗接种",
            "value": f"第 {int(item.get('dose_number') or 1)} 剂"
            + (f" · 下一剂 {next_due}" if next_due else ""),
            "note": item.get("note", ""),
            "alert": bool(
                int(item.get("id") or 0) in latest_vaccination_ids
                and next_due
                and next_due <= today
            ),
        })

    for item in raw_appointments:
        at = str(item.get("scheduled_at") or "")
        if not in_window(at):
            continue
        status_labels = {
            "upcoming": "待就诊",
            "completed": "已完成",
            "cancelled": "已取消",
        }
        details = [
            item.get("department") or "",
            item.get("doctor") or "",
            item.get("location") or "",
        ]
        details = [part for part in details if part]
        value = " · ".join(details)
        label = status_labels.get(item.get("status") or "", "")
        if label:
            value = f"{label} · {value}" if value else label
        events.append({
            "type": "appointment",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("title") or "就诊预约",
            "value": value,
            "note": item.get("note", ""),
            "alert": bool(
                item.get("status") == "upcoming"
                and (item.get("soon") or item.get("is_past"))
            ),
        })

    for item in raw_lab_results:
        at = str(item.get("tested_at") or "")
        if not in_window(at):
            continue
        value = f"{float(item.get('value') or 0):g}"
        unit = str(item.get("unit") or "")
        if unit:
            value += f" {unit}"
        ref_text = str(item.get("ref_text") or "")
        if ref_text:
            value += f"（参考 {ref_text}）"
        events.append({
            "type": "lab",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("item") or "化验结果",
            "value": value,
            "note": item.get("note", ""),
            "critical": bool(item.get("critical")),
            "critical_message": (
                ((item.get("critical_alerts") or [{}])[0]).get("message") or ""
            ),
            "alert": bool(item.get("abnormal") or item.get("critical")),
        })

    for item in raw_care_items:
        if not item.get("active"):
            continue
        at = str(item.get("next_due_at") or "")
        if not in_window(at):
            continue
        days_until = item.get("days_until")
        value_parts = []
        if item.get("interval_days"):
            value_parts.append(f"每 {int(item['interval_days'])} 天")
        if days_until is not None:
            if item.get("overdue"):
                value_parts.append(f"已逾期 {abs(int(days_until))} 天")
            elif days_until == 0:
                value_parts.append("今天到期")
            else:
                value_parts.append(f"{int(days_until)} 天后")
        events.append({
            "type": "care",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("title") or "复查计划",
            "value": " · ".join(value_parts),
            "note": item.get("note", ""),
            "alert": bool(item.get("overdue") or item.get("due_soon")),
        })

    for item in raw_visit_records:
        at = str(item.get("visit_date") or "")
        if not in_window(at):
            continue
        details = [
            item.get("department") or "",
            item.get("doctor") or "",
            item.get("facility") or "",
        ]
        details = [part for part in details if part]
        value = " · ".join(details)
        if item.get("next_followup_date"):
            followup = f"下次复诊 {item['next_followup_date']}"
            value = f"{value} · {followup}" if value else followup
        events.append({
            "type": "visit",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("diagnosis") or "就诊记录",
            "value": value,
            "note": item.get("note", ""),
            "alert": False,
        })

    for item in raw_attachments:
        at = str(item.get("created_at") or "")
        if not in_window(at):
            continue
        value = " · ".join(part for part in (
            item.get("kind_label") or "",
            item.get("note") or "",
        ) if part)
        events.append({
            "type": "attachment",
            "at": at,
            "id": item.get("id"),
            "profile": item.get("profile", ""),
            "title": item.get("original_name") or "附件",
            "value": value,
            "note": "",
            "alert": False,
            "kind": item.get("kind") or "other",
            "ref_id": int(item.get("ref_id") or 0),
            "mime": item.get("mime") or "",
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
    if type in {
        "metric", "symptom", "medication", "vaccination",
        "appointment", "lab", "care", "visit", "consultation",
        "attachment",
    }:
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
    """导出健康时间线 CSV（带 BOM），可按档案、天数、类型、异常、高危会诊与关键字筛选。"""
    import csv
    import io

    data = await health_timeline(
        profile=profile, days=days, limit=100000, date=date,
        type=type, only_alert=only_alert, only_risk=only_risk, q=q,
    )
    if data["total"] > len(data["events"]):
        raise HTTPException(
            status_code=400,
            detail="时间线事件过多，请缩小时间范围或增加筛选条件后重试。",
        )
    type_labels = {
        "metric": "指标",
        "symptom": "症状",
        "medication": "用药",
        "vaccination": "疫苗",
        "appointment": "就诊",
        "lab": "化验",
        "care": "复查",
        "visit": "就诊记录",
        "consultation": "会诊",
        "attachment": "资料",
    }
    events = data["events"]
    rows = [["时间", "类型", "项目", "数值", "患者档案", "备注", "异常"]]
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


HEALTH_SEARCH_TYPE_LABELS = {
    "metric": "指标",
    "symptom": "症状",
    "medication": "用药",
    "lab": "化验",
    "vaccination": "疫苗",
    "appointment": "预约",
    "care": "复查",
    "visit": "就诊",
    "allergy": "过敏",
    "condition": "慢病",
    "reminder": "提醒",
    "attachment": "资料",
}
HEALTH_SEARCH_TYPE_ORDER = tuple(HEALTH_SEARCH_TYPE_LABELS)
HEALTH_SEARCH_MAX_QUERY_CHARS = 80
HEALTH_SEARCH_PER_TYPE_LIMIT = 20


def _health_search_text(*fields: Any) -> str:
    return " ".join(str(field or "") for field in fields).lower()


def _health_search_number(value: Any) -> str:
    if isinstance(value, bool) or value is None:
        return ""
    if isinstance(value, (int, float)):
        return f"{value:g}"
    return str(value)


def _health_search_prefilter(type_key: str, key: str) -> str:
    """返回可安全下推到 SQL 的预过滤词；空串表示不能下推。

    只有查询不可能命中 Python 侧拼出的派生文本（指标中文名、状态标签、
    化验参考范围等）时才下推，确保 SQL 结果是最终结果的超集。
    """
    if not key:
        return ""
    if type_key == "metric":
        # 中文指标名/情境名由调用方翻译成存储代码后并入 SQL；
        # 此处仍返回原始查询用于匹配备注、单位等存储文本
        return key
    if type_key == "medication":
        return "" if key in "在用" or key in "停用" else key
    if type_key == "lab":
        if "–" in key or "≥" in key or "≤" in key:
            return ""
        if any(character.isdigit() for character in key):
            return ""
        return "" if any(
            key in label for label in ("偏高", "偏低", "危急值")
        ) else key
    if type_key == "vaccination":
        if any(character.isdigit() for character in key):
            return ""
        return "" if "第" in key or "剂" in key else key
    if type_key == "appointment":
        return "" if any(
            key in label for label in ("待就诊", "已完成", "已取消")
        ) else key
    if type_key == "care":
        return "" if key in "已停用" else key
    if type_key == "allergy":
        return "" if any(
            key in label
            for label in ("轻度", "中度", "重度", "药物", "食物",
                          "环境", "其他", "已停用")
        ) else key
    if type_key == "condition":
        return "" if any(
            key in label
            for label in ("管理中", "已控制", "已痊愈", "轻度", "中度",
                          "重度")
        ) else key
    if type_key == "reminder":
        return "" if key in "已完成" or key in "待处理" else key
    if type_key == "symptom" or type_key == "visit":
        return key
    # 附件与其余类型的匹配依赖关联记录或派生文本，不做 SQL 预过滤
    return ""


def _health_search_metric_filters(key: str):
    """把中文指标名/情境名翻译为存储代码，供 SQL 预过滤使用。"""
    from memory.session_store import METRIC_CONTEXT_LABELS, METRIC_TYPES

    codes = [
        code for code, (label, _unit, _range) in METRIC_TYPES.items()
        if key in str(label or "").lower()
    ]
    contexts = [
        code for code, label in METRIC_CONTEXT_LABELS.items()
        if key in str(label or "").lower()
    ]
    return codes, contexts


@app.get("/api/health/search")
async def health_search(
    q: str = "",
    profile: Optional[str] = None,
    limit: int = 50,
    per_type_limit: int = Query(
        HEALTH_SEARCH_PER_TYPE_LIMIT, include_in_schema=False
    ),
    only_type: str = Query("", alias="type", include_in_schema=False),
):
    """跨健康记录搜索：指标、症状、用药、化验、疫苗、预约、复查、就诊、过敏、慢病、提醒与就医资料。"""
    from memory import get_session_store
    from memory.session_store import METRIC_TYPES, metric_context_label

    per_type_limit = max(
        1, min(int(per_type_limit or HEALTH_SEARCH_PER_TYPE_LIMIT), 200)
    )
    only_type = str(only_type or "").strip()
    if only_type and only_type not in HEALTH_SEARCH_TYPE_ORDER:
        raise HTTPException(status_code=400, detail="未知的搜索类型")

    query = " ".join(str(q or "").split())[:HEALTH_SEARCH_MAX_QUERY_CHARS]
    if not query:
        raise HTTPException(status_code=400, detail="请输入搜索关键字")
    key = query.lower()
    limit = max(1, min(int(limit or 50), 200))
    store = get_session_store()

    sql_queries = {
        type_key: _health_search_prefilter(type_key, key)
        for type_key in HEALTH_SEARCH_TYPE_ORDER
    }
    metric_codes, metric_contexts = _health_search_metric_filters(key)

    (
        metrics, symptoms, medications, labs, vaccinations,
        appointments, care_items, visits, allergies, conditions,
        reminders, attachments,
    ) = await asyncio.gather(
        asyncio.to_thread(
            store.list_metrics, profile, None, 100000, 0, 0, "",
            search_q=sql_queries["metric"],
            search_metric_codes=metric_codes,
            search_contexts=metric_contexts,
        ),
        asyncio.to_thread(
            store.list_symptoms, profile, 100000, 0, 0,
            search_q=sql_queries["symptom"],
        ),
        asyncio.to_thread(
            store.list_medications, profile, False, 100000, 0, 7,
            search_q=sql_queries["medication"],
        ),
        asyncio.to_thread(
            store.list_lab_results, profile, None, None, 0, 100000, 0,
            search_q=sql_queries["lab"],
        ),
        asyncio.to_thread(
            store.list_vaccinations, profile, 100000,
            search_q=sql_queries["vaccination"],
        ),
        asyncio.to_thread(
            store.list_appointments, profile, None, False, 100000, 0,
            q=sql_queries["appointment"],
        ),
        asyncio.to_thread(
            store.list_care_items, profile, False, False, 100000,
            q=sql_queries["care"],
        ),
        asyncio.to_thread(
            store.list_visit_records, profile, 0, "", 100000,
            search_q=sql_queries["visit"],
        ),
        asyncio.to_thread(
            store.list_allergies, profile, False, None,
            sql_queries["allergy"], 100000,
        ),
        asyncio.to_thread(
            store.list_conditions, profile, False, None, 100000,
            sql_queries["condition"],
        ),
        asyncio.to_thread(
            store.list_reminders, True, profile,
            search_q=sql_queries["reminder"],
        ),
        _list_attachments_paged(store, profile),
    )

    buckets: Dict[str, List[Dict[str, Any]]] = {
        type_key: [] for type_key in HEALTH_SEARCH_TYPE_ORDER
    }

    def add(
        type_key: str,
        row: Dict[str, Any],
        matched: bool,
        title: str,
        detail: str,
        at: Any,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not matched:
            return
        item = {
            "type": type_key,
            "label": HEALTH_SEARCH_TYPE_LABELS[type_key],
            "id": int(row.get("id") or 0),
            "profile": row.get("profile") or "",
            "title": str(title)[:120],
            "detail": str(detail)[:200],
            "at": str(at or "")[:19],
        }
        if extra:
            item.update(extra)
        buckets[type_key].append(item)

    for row in metrics:
        metric = str(row.get("metric") or "")
        label, unit, _ = METRIC_TYPES.get(metric, (metric, "", None))
        context = metric_context_label(metric, row.get("context"))
        value1 = _health_search_number(row.get("value1"))
        value2 = _health_search_number(row.get("value2"))
        value_text = f"{value1}/{value2}" if value2 else value1
        matched = key in _health_search_text(
            label, metric, unit, context, row.get("note")
        )
        add(
            "metric", row, matched,
            f"{label} {value_text} {unit}".strip(),
            " · ".join(part for part in (
                row.get("profile") or "",
                context,
                row.get("note") or "",
            ) if part),
            row.get("measured_at"),
            {"metric": metric},
        )

    for row in symptoms:
        severity = _health_search_number(row.get("severity"))
        matched = key in _health_search_text(
            row.get("symptom"), row.get("note")
        )
        add(
            "symptom", row, matched,
            f"{row.get('symptom') or '症状'} {severity}/10",
            " · ".join(part for part in (
                row.get("profile") or "",
                row.get("note") or "",
            ) if part),
            row.get("recorded_at"),
        )

    for row in medications:
        active = bool(row.get("active"))
        matched = key in _health_search_text(
            row.get("name"), row.get("dose"), row.get("frequency"),
            "在用" if active else "停用", row.get("note"),
        )
        add(
            "medication", row, matched,
            f"{row.get('name') or '药物'} {row.get('dose') or ''}".strip(),
            " · ".join(part for part in (
                row.get("profile") or "",
                row.get("frequency") or "",
                "在用" if active else "停用",
                row.get("note") or "",
            ) if part),
            row.get("updated_at") or row.get("started_at")
            or row.get("created_at"),
        )

    for row in labs:
        abnormal = (
            "偏高" if row.get("abnormal_high")
            else "偏低" if row.get("abnormal_low") else ""
        )
        if row.get("critical"):
            abnormal = f"{abnormal} 危急值".strip()
        value = _health_search_number(row.get("value"))
        matched = key in _health_search_text(
            row.get("item"), row.get("panel"), row.get("lab_name"),
            row.get("unit"), row.get("ref_text"), abnormal, row.get("note"),
        )
        add(
            "lab", row, matched,
            f"{row.get('item') or '化验'} {value} "
            f"{row.get('unit') or ''}".strip(),
            " · ".join(part for part in (
                row.get("profile") or "",
                row.get("panel") or "",
                row.get("lab_name") or "",
                abnormal,
                f"参考 {row.get('ref_text')}" if row.get("ref_text") else "",
                row.get("note") or "",
            ) if part),
            row.get("tested_at"),
        )

    for row in vaccinations:
        dose = _health_search_number(row.get("dose_number")) or "1"
        matched = key in _health_search_text(
            row.get("vaccine_name"), row.get("note")
        )
        add(
            "vaccination", row, matched,
            f"{row.get('vaccine_name') or '疫苗'} 第{dose}剂",
            " · ".join(part for part in (
                row.get("profile") or "",
                f"下一剂 {row.get('next_due')}" if row.get("next_due") else "",
                row.get("note") or "",
            ) if part),
            row.get("vaccinated_at"),
        )

    for row in appointments:
        status = {
            "upcoming": "待就诊",
            "completed": "已完成",
            "cancelled": "已取消",
        }.get(str(row.get("status") or ""), str(row.get("status") or ""))
        matched = key in _health_search_text(
            row.get("title"), row.get("department"), row.get("doctor"),
            row.get("location"), status, row.get("note"),
        )
        add(
            "appointment", row, matched,
            row.get("title") or "就诊预约",
            " · ".join(part for part in (
                row.get("profile") or "",
                status,
                row.get("department") or "",
                row.get("doctor") or "",
                row.get("location") or "",
                row.get("scheduled_at") or "",
                row.get("note") or "",
            ) if part),
            row.get("scheduled_at"),
        )

    for row in care_items:
        interval = _health_search_number(row.get("interval_days"))
        matched = key in _health_search_text(
            row.get("title"),
            "已停用" if not row.get("active") else "",
            row.get("note"),
        )
        add(
            "care", row, matched,
            row.get("title") or "复查",
            " · ".join(part for part in (
                row.get("profile") or "",
                f"每 {interval} 天" if interval else "",
                f"下次 {row.get('next_due_at')}"
                if row.get("next_due_at") else "",
                "已停用" if not row.get("active") else "",
                row.get("note") or "",
            ) if part),
            row.get("next_due_at"),
        )

    for row in visits:
        matched = key in _health_search_text(
            row.get("diagnosis"), row.get("department"), row.get("doctor"),
            row.get("facility"), row.get("advice"),
            row.get("prescription"), row.get("note"),
        )
        add(
            "visit", row, matched,
            row.get("diagnosis") or "就诊记录",
            " · ".join(part for part in (
                row.get("profile") or "",
                row.get("visit_date") or "",
                row.get("department") or "",
                row.get("doctor") or "",
                row.get("facility") or "",
                row.get("advice") or "",
                row.get("prescription") or "",
                row.get("note") or "",
            ) if part),
            row.get("visit_date"),
        )

    visit_map = {
        int(row["id"]): row for row in visits if row.get("id")
    }
    lab_map = {
        int(row["id"]): row for row in labs if row.get("id")
    }
    for row in attachments:
        ref_id = int(row.get("ref_id") or 0)
        ref_label = ""
        if row.get("kind") == "visit" and ref_id in visit_map:
            visit = visit_map[ref_id]
            ref_label = " · ".join(part for part in (
                str(visit.get("visit_date") or "")[:10],
                visit.get("department") or "",
                visit.get("diagnosis") or "",
            ) if part)
        elif row.get("kind") == "lab" and ref_id in lab_map:
            lab = lab_map[ref_id]
            try:
                value = f"{float(lab.get('value')):g}"
            except (TypeError, ValueError, OverflowError):
                value = str(lab.get("value") or "")
            result = f"{lab.get('item') or '化验'} {value}".strip()
            if lab.get("unit"):
                result += f" {lab['unit']}"
            ref_label = " · ".join(part for part in (
                str(lab.get("tested_at") or "")[:10],
                result,
                lab.get("panel") or "",
            ) if part)
        matched = key in _health_search_text(
            row.get("original_name"), row.get("note"),
            row.get("kind_label"), row.get("profile"), ref_label,
        )
        add(
            "attachment", row, matched,
            row.get("original_name") or "附件",
            " · ".join(part for part in (
                row.get("profile") or "",
                row.get("kind_label") or "",
                ref_label,
                row.get("note") or "",
            ) if part),
            row.get("created_at"),
            {
                "kind": row.get("kind") or "other",
                "ref_id": ref_id,
                "mime": row.get("mime") or "",
            },
        )

    severity_labels = {"mild": "轻度", "moderate": "中度", "severe": "重度"}
    kind_labels = {
        "drug": "药物",
        "food": "食物",
        "environment": "环境",
        "other": "其他",
    }
    for row in allergies:
        severity = severity_labels.get(str(row.get("severity") or ""), "")
        kind = kind_labels.get(str(row.get("kind") or ""), "")
        matched = key in _health_search_text(
            row.get("substance"), row.get("reaction"), severity, kind,
            "已停用" if not row.get("active") else "",
            row.get("note"),
        )
        add(
            "allergy", row, matched,
            row.get("substance") or "过敏原",
            " · ".join(part for part in (
                row.get("profile") or "",
                kind,
                severity,
                row.get("reaction") or "",
                "已停用" if not row.get("active") else "",
                row.get("note") or "",
            ) if part),
            row.get("noted_at"),
        )

    status_labels = {
        "active": "管理中",
        "controlled": "已控制",
        "resolved": "已痊愈",
    }
    for row in conditions:
        status = status_labels.get(str(row.get("status") or ""), "")
        severity = severity_labels.get(str(row.get("severity") or ""), "")
        matched = key in _health_search_text(
            row.get("name"), row.get("icd10"), status, severity,
            row.get("note"),
        )
        add(
            "condition", row, matched,
            row.get("name") or "慢病",
            " · ".join(part for part in (
                row.get("profile") or "",
                status,
                severity,
                row.get("icd10") or "",
                row.get("note") or "",
            ) if part),
            row.get("diagnosed_at") or row.get("updated_at"),
        )

    for row in reminders:
        done = bool(row.get("done"))
        matched = key in _health_search_text(
            row.get("title"),
            "已完成" if done else "待处理",
            row.get("note"),
        )
        add(
            "reminder", row, matched,
            row.get("title") or "提醒",
            " · ".join(part for part in (
                row.get("profile") or "",
                f"到期 {row.get('due_at')}" if row.get("due_at") else "",
                "已完成" if done else "待处理",
                row.get("note") or "",
            ) if part),
            row.get("due_at"),
        )

    counts = {
        type_key: len(items) for type_key, items in buckets.items()
    }
    if only_type:
        # 按类型导出时先收窄类型再套全局上限，避免其它类型占满 200 条
        counts = {only_type: counts.get(only_type, 0)}
    results: List[Dict[str, Any]] = []
    for type_key in (
        (only_type,) if only_type else HEALTH_SEARCH_TYPE_ORDER
    ):
        items = sorted(
            buckets[type_key], key=lambda item: item["at"], reverse=True
        )
        results.extend(items[:per_type_limit])
    results.sort(key=lambda item: (item["at"], item["id"]), reverse=True)
    shown = results[:limit]
    return {
        "query": query,
        "total": sum(counts.values()),
        "returned": len(shown),
        "truncated": sum(counts.values()) > len(shown),
        "counts": counts,
        "results": shown,
    }


@app.get("/api/health/search/export.csv")
async def export_health_search_csv(
    q: str = "",
    profile: Optional[str] = None,
    type_: str = Query("", alias="type"),
):
    """导出全局健康搜索结果 CSV（带 BOM，最多 200 条）。"""
    import csv
    import io

    type_key = str(type_ or "").strip()
    if type_key and type_key not in HEALTH_SEARCH_TYPE_ORDER:
        raise HTTPException(status_code=400, detail="未知的搜索类型")
    data = await health_search(
        q=q,
        profile=profile,
        limit=200,
        per_type_limit=200,
        only_type=type_key,
    )
    items = data.get("results") or []
    rows = [["类型", "时间", "患者档案", "标题", "详情"]]
    for item in items:
        rows.append([
            csv_safe_cell(item.get("label") or ""),
            csv_safe_cell(str(item.get("at") or "")),
            csv_safe_cell(item.get("profile") or ""),
            csv_safe_cell(item.get("title") or ""),
            csv_safe_cell(item.get("detail") or ""),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    filename = (
        "medix-health-search-"
        + datetime.now().strftime("%Y%m%d")
        + ".csv"
    )
    return Response(
        content="﻿" + buffer.getvalue(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.get("/api/health/bp-monitoring")
async def health_bp_monitoring(
    profile: Optional[str] = None,
    days: int = 7,
    start: Optional[str] = None,
):
    """家庭血压监测：统计晨起/睡前时段完成度与均值，不判断是否达标。"""
    from memory import get_session_store

    days = max(1, min(int(days or 7), 30))
    today = datetime.now().date()
    start_text = str(start or "").strip()
    if start_text:
        try:
            start_date = datetime.strptime(start_text, "%Y-%m-%d").date()
        except ValueError:
            raise HTTPException(
                status_code=400, detail="开始日期格式应为 YYYY-MM-DD"
            )
    else:
        start_date = today - timedelta(days=days - 1)
    if start_date > today:
        raise HTTPException(status_code=400, detail="开始日期不能晚于今天")
    if (today - start_date).days > 90:
        raise HTTPException(
            status_code=400, detail="开始日期距今天不能超过 90 天"
        )
    planned_end = start_date + timedelta(days=days - 1)
    end_date = min(planned_end, today)
    start_key = start_date.isoformat()
    end_key = end_date.isoformat()
    window_days = (today - start_date).days + 1
    store = get_session_store()
    raw = await asyncio.to_thread(
        store.list_metrics, profile, "bp", 100000, window_days, 0, ""
    )
    records = [
        item for item in raw
        if start_key <= str(item.get("measured_at") or "")[:10] <= end_key
    ]

    slots: Dict[str, set] = {}
    per_day_counts: Dict[str, int] = {}
    morning: List[Tuple[float, float]] = []
    evening: List[Tuple[float, float]] = []
    all_values: List[Tuple[float, float]] = []
    unlabeled = 0
    for item in records:
        day = str(item.get("measured_at") or "")[:10]
        try:
            systolic = float(item.get("value1"))
            raw_diastolic = item.get("value2")
            diastolic = (
                float(raw_diastolic)
                if raw_diastolic is not None else 0.0
            )
        except (TypeError, ValueError, OverflowError):
            continue
        per_day_counts[day] = per_day_counts.get(day, 0) + 1
        all_values.append((systolic, diastolic))
        context = str(item.get("context") or "").strip().lower()
        if context == "morning":
            morning.append((systolic, diastolic))
            slots.setdefault(day, set()).add("morning")
        elif context == "evening":
            evening.append((systolic, diastolic))
            slots.setdefault(day, set()).add("evening")
        else:
            unlabeled += 1

    labels = {"morning": "晨起", "evening": "睡前"}
    daily: List[Dict[str, Any]] = []
    missing: List[Dict[str, Any]] = []
    completed_slots = 0
    expected_slots = 0
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        done = slots.get(key, set())
        expected_slots += 2
        completed_slots += len(done)
        missing_contexts = [
            context for context in ("morning", "evening")
            if context not in done
        ]
        daily.append({
            "date": key,
            "morning": "morning" in done,
            "evening": "evening" in done,
            "readings": per_day_counts.get(key, 0),
        })
        if missing_contexts:
            missing.append({
                "date": key,
                "missing": missing_contexts,
                "labels": [labels[context] for context in missing_contexts],
            })
        current += timedelta(days=1)

    def _average(rows: List[Tuple[float, float]]) -> Dict[str, Any]:
        if not rows:
            return {
                "count": 0,
                "avg_systolic": None,
                "avg_diastolic": None,
            }
        return {
            "count": len(rows),
            "avg_systolic": round(
                sum(row[0] for row in rows) / len(rows), 1
            ),
            "avg_diastolic": round(
                sum(row[1] for row in rows) / len(rows), 1
            ),
        }

    overall = _average(all_values)
    morning_stats = _average(morning)
    evening_stats = _average(evening)
    diff = None
    if morning and evening:
        raw_morning = sum(row[0] for row in morning) / len(morning)
        raw_evening = sum(row[0] for row in evening) / len(evening)
        diff = round(raw_morning - raw_evening + 1e-9, 1)
    return {
        "profile": profile or "",
        "start": start_key,
        "end": end_key,
        "days": days,
        "planned_end": planned_end.isoformat(),
        "is_future_plan": planned_end > today,
        "expected_slots": expected_slots,
        "completed_slots": completed_slots,
        "completion_rate": (
            round(
                completed_slots / expected_slots * 100 + 1e-9
            )
            if expected_slots else 0
        ),
        "days_completed": sum(
            1 for row in daily if row["morning"] and row["evening"]
        ),
        "readings": len(records),
        "unlabeled": unlabeled,
        "average": overall,
        "morning": morning_stats,
        "evening": evening_stats,
        "morning_evening_systolic_diff": diff,
        "daily": daily,
        "missing": missing,
    }


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
        store.list_metrics,
        profile, None, 100000, 0, 0, "", start, end,
    )
    raw_symptoms = await asyncio.to_thread(
        store.list_symptoms, profile, 100000, 0, 0, start, end
    )
    raw_logs = await asyncio.to_thread(
        store.list_medication_logs,
        None, profile, 100000, 0, 0, start, end,
    )
    raw_sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline,
        profile, start, f"{end}T23:59:59", 100000, True,
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
    vaccinations = await asyncio.to_thread(
        store.list_vaccinations, profile, 100000, start, end
    )
    appointments = await asyncio.to_thread(
        store.list_appointments,
        profile, None, False, 100000, 0, start, end,
    )
    lab_results = await asyncio.to_thread(
        store.list_lab_results,
        profile, None, None, 0, 100000, 0, start, end,
    )
    care_items = await asyncio.to_thread(
        store.list_care_items,
        profile, False, False, 100000, start, end,
    )
    visits = await asyncio.to_thread(
        store.list_visit_records, profile, 0, "", 100000, start, end
    )
    attachment_daily = await asyncio.to_thread(
        store.count_attachments_by_day, profile, start, end
    )

    days: Dict[str, Dict[str, int]] = {}

    def bucket(day_key: str) -> Dict[str, int]:
        return days.setdefault(day_key, {
            "metrics": 0, "symptoms": 0, "doses": 0,
            "consultations": 0, "reminders_due": 0, "reminders_done": 0,
            "vaccinations": 0, "appointments": 0, "labs": 0,
            "care_due": 0, "visits": 0, "attachments": 0, "alerts": 0,
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
            else:
                from memory.session_store import critical_metric_alerts

                if critical_metric_alerts(
                    item["metric"],
                    item.get("value1"),
                    item.get("value2"),
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
        if in_month(at) and not item.get("skipped"):
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
    for item in vaccinations:
        at = str(item.get("vaccinated_at") or "")
        if in_month(at):
            bucket(at[:10])["vaccinations"] += 1
    for item in appointments:
        at = str(item.get("scheduled_at") or "")
        if in_month(at):
            bucket(at[:10])["appointments"] += 1
    for item in lab_results:
        at = str(item.get("tested_at") or "")
        if not in_month(at):
            continue
        entry = bucket(at[:10])
        entry["labs"] += 1
        if item.get("abnormal") or item.get("critical"):
            entry["alerts"] += 1
    for item in care_items:
        if not item.get("active"):
            continue
        at = str(item.get("next_due_at") or "")
        if in_month(at):
            bucket(at[:10])["care_due"] += 1
    for item in visits:
        at = str(item.get("visit_date") or "")
        if in_month(at):
            bucket(at[:10])["visits"] += 1
    for day_key, total in attachment_daily.items():
        if in_month(day_key):
            bucket(day_key)["attachments"] += int(total)

    totals = {
        key: sum(entry[key] for entry in days.values())
        for key in (
            "metrics", "symptoms", "doses",
            "consultations", "reminders_due", "reminders_done",
            "vaccinations", "appointments", "labs", "care_due",
            "visits", "attachments", "alerts",
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
        "疫苗", "预约", "化验", "复查", "就诊", "就医资料",
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
            str(int(entry.get("vaccinations") or 0)),
            str(int(entry.get("appointments") or 0)),
            str(int(entry.get("labs") or 0)),
            str(int(entry.get("care_due") or 0)),
            str(int(entry.get("visits") or 0)),
            str(int(entry.get("attachments") or 0)),
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
    days = await asyncio.to_thread(store.recorded_days, profile, 3650)
    info = _streak_info(days)
    info["profile"] = profile or ""
    return info


@app.post("/api/metrics/quick-parse")
async def quick_parse_metric(request: QuickParseRequest):
    """解析一句话健康记录，只返回预览，不写入数据库。"""
    from core.quick_entry import QuickEntryError, parse_quick_metric

    try:
        preview = parse_quick_metric(request.text)
    except QuickEntryError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "ok": False,
                "code": exc.code,
                "detail": exc.message,
            },
        )
    return {"ok": True, "preview": preview}


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
        request.context,
    )
    if item is None:
        raise HTTPException(status_code=400, detail="健康指标内容或范围不正确")
    from memory.session_store import critical_metric_alerts

    alerts = critical_metric_alerts(
        item.get("metric"), item.get("value1"), item.get("value2")
    )
    await record_audit("metric.create", f"{item['id']}·{item['metric']}")
    return {"ok": True, "metric": item, "critical_alerts": alerts}


@app.patch("/api/metrics/{metric_id}")
async def update_metric(metric_id: SafeIntPath, request: MetricUpdateRequest):
    """部分更新一条健康指标记录；未提供的字段保持原值。"""
    from memory import get_session_store

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_metric, metric_id)
    if existing is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    if (
        "measured_at" in request.model_fields_set
        and not str(request.measured_at or "").strip()
    ):
        raise HTTPException(status_code=400, detail="测量时间不能为空")

    # 只更新请求实际给出的字段，未给出的字段在事务内沿用当前值
    fields = request.model_dump(exclude_unset=True)
    item = await asyncio.to_thread(
        store.update_metric, metric_id, **fields
    )
    if item is None:
        if await asyncio.to_thread(store.get_metric, metric_id) is None:
            raise HTTPException(status_code=404, detail="记录不存在")
        raise HTTPException(
            status_code=400, detail="健康指标内容或范围不正确"
        )
    from memory.session_store import critical_metric_alerts

    alerts = critical_metric_alerts(
        item.get("metric"), item.get("value1"), item.get("value2")
    )
    await record_audit("metric.update", f"{item['id']}·{item['metric']}")
    return {"ok": True, "metric": item, "critical_alerts": alerts}


@app.delete("/api/metrics/{metric_id}")
async def delete_metric(metric_id: SafeIntPath):
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
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
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
        "sleep": "睡眠时长",
        "steps": "步数",
        "water": "饮水量",
        "phq9": "PHQ-9 评分",
        "gad7": "GAD-7 评分",
        "cat": "CAT 评分",
        "ess": "ESS 评分",
        "auditc": "AUDIT-C 评分",
        "mmrc": "mMRC 分级",
    }

    rows = [[
        "测量时间", "患者档案", "指标", "数值1", "数值2", "单位",
        "情境", "备注",
    ]]
    from memory.session_store import metric_context_label

    for item in reversed(items):
        rows.append([
            item.get("measured_at", ""),
            csv_safe_cell(item.get("profile", "")),
            labels.get(item.get("metric"), item.get("metric", "")),
            item.get("value1", ""),
            item.get("value2", "") if item.get("value2") is not None else "",
            item.get("unit", ""),
            csv_safe_cell(
                metric_context_label(
                    item.get("metric", ""), item.get("context")
                )
            ),
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
async def import_metrics_csv(
    request: Request,
    file: UploadFile = File(...),
    profile: str = Form(""),
):
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
        "情境": "context",
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
        "睡眠时长": "sleep",
        "睡眠": "sleep",
        "步数": "steps",
        "步行": "steps",
        "饮水量": "water",
        "饮水": "water",
        "喝水": "water",
    }
    allowed = {
        "bp", "weight", "glucose", "temperature", "heart_rate", "spo2",
        "waist", "sleep", "steps", "water", "phq9", "gad7", "cat", "ess",
        "auditc", "mmrc"
    }

    from memory.session_store import METRIC_CONTEXTS

    records = []
    invalid = 0
    context_ignored = 0
    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")

    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            name = key.strip()
            mapped[field_alias.get(name, name)] = csv_restore_cell(value).strip()

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

        converted = _convert_import_metric(
            metric, mapped.get("unit", ""), value1, value2
        )
        if converted is None:
            # 未知单位不能静默按规范单位入库
            invalid += 1
            continue
        value1, value2 = converted

        context_text = mapped.get("context", "")
        context_map = {
            "晨起": "morning",
            "睡前": "bedtime",
            "空腹": "fasting",
            "餐后": "postprandial",
            "随机": "random",
        }
        context = context_map.get(context_text, context_text)
        if metric == "bp" and context == "bedtime":
            context = "evening"
        elif metric == "glucose" and context == "evening":
            context = "bedtime"
        allowed_contexts = METRIC_CONTEXTS.get(metric, ())
        if context and allowed_contexts and context not in allowed_contexts:
            # 情境只是可选标签；不适用于该指标时忽略标签而不是丢弃整条读数
            context = ""
            context_ignored += 1

        records.append({
            "profile": mapped.get("profile", "") or profile,
            "metric": metric,
            "value1": value1,
            "value2": value2,
            "measured_at": mapped.get("measured_at", ""),
            "context": context,
            "note": mapped.get("note", ""),
        })

    if not records:
        raise HTTPException(
            status_code=400,
            detail=f"没有可导入的健康记录（无效 {invalid} 行）",
        )

    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"health_metrics": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("metrics", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "metric.import",
        detail=(
            f"imported={imported} invalid={invalid} "
            f"context_ignored={context_ignored}"
        ),
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
        "invalid": invalid,
        "context_ignored": context_ignored,
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
        f"{_profile_audit_key(request.profile)}·{target['metric']}",
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
    store = get_session_store()
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["metric_target.set", "metric_target.delete"],
        f"{profile or '未指定'}·",
        metric,
    )
    await record_audit(
        "metric_target.delete",
        f"{_profile_audit_key(profile)}·{metric}",
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
    await record_audit("profile.save", _profile_audit_key(profile["name"]))
    return {"ok": True, "profile": profile}


@app.delete("/api/profiles/{name}")
async def delete_profile(name: str):
    from memory import get_session_store
    from memory.session_store import _normalize_profile_name

    store = get_session_store()
    ok = await asyncio.to_thread(store.delete_profile, name)
    if not ok:
        raise HTTPException(status_code=404, detail="档案不存在")
    # 档案作用域是档案名的哈希；删除档案必须清空对应命名空间，
    # 否则重建同名档案会召回旧患者的病史对话
    await asyncio.to_thread(_erase_long_term_scope, name)
    normalized_name = _normalize_profile_name(name)
    audit_key = _profile_audit_key(name)
    # 清理历史审计中的档案名
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["profile.rename"],
        normalized_name,
        audit_key,
        "renamed",
    )
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["profile.save", "profile.delete"],
        normalized_name,
        audit_key,
    )
    await record_audit("profile.delete", audit_key)
    return {"ok": True, "name": name}


@app.patch("/api/profiles/{name}")
async def rename_profile(name: str, request: ProfileRenameRequest):
    """重命名患者档案，并同步健康指标中的档案名。"""
    from memory import get_session_store
    from memory.session_store import _normalize_profile_name

    try:
        new_name = await asyncio.to_thread(
            get_session_store().rename_profile, name, request.new_name
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))
    if new_name is None:
        raise HTTPException(status_code=404, detail="档案不存在")
    # 路径里的旧名称可能含多余空格；只有规范化后确实改名才清理旧命名空间
    if new_name != _normalize_profile_name(name):
        # 旧命名空间无法原地改名；删除它，避免旧患者病史在新名字下被召回
        await asyncio.to_thread(_erase_long_term_scope, name)
    store = get_session_store()
    old_key = _profile_audit_key(name)
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["profile.rename"],
        _normalize_profile_name(name),
        old_key,
        "renamed",
    )
    await asyncio.to_thread(
        store.redact_audit_targets,
        ["profile.save", "profile.delete"],
        _normalize_profile_name(name),
        old_key,
    )
    await record_audit("profile.rename", _profile_audit_key(new_name))
    return {"ok": True, "old_name": name, "name": new_name}


FAMILY_ATTENTION_ORDER = {"danger": 0, "warning": 1, "info": 2, "ok": 3}


def _family_attention(
    *,
    critical: Dict[str, Any],
    consultation_risk: str,
    labs: Dict[str, Any],
    reminders: Dict[str, Any],
    care: Dict[str, Any],
    symptoms: Dict[str, Any],
    medications: Dict[str, Any],
    medication_lab: Dict[str, Any],
    vaccinations: Dict[str, Any],
    appointments: Dict[str, Any],
    metrics: List[Dict[str, Any]],
) -> Dict[str, str]:
    """从家庭总览已聚合的数据中提取最需要关注的一条提示。"""
    if critical.get("count"):
        return {
            "level": "danger",
            "label": f"危急值 {critical['count']} 项",
            "detail": "、".join(critical.get("items") or [])[:120],
        }
    if labs.get("critical"):
        return {
            "level": "danger",
            "label": f"化验危急值 {labs['critical']} 项",
            "detail": "、".join(labs.get("critical_items") or [])[:120],
        }
    if medication_lab.get("count"):
        return {
            "level": "warning",
            "label": f"用药安全需核对 {medication_lab['count']} 条",
            "detail": "、".join(
                str(item.get("title") or "")
                for item in (medication_lab.get("alerts") or [])
                if item.get("title")
            )[:120],
        }
    if consultation_risk in {"high", "emergency"}:
        return {
            "level": "warning",
            "label": "最近会诊提示高风险",
            "detail": "",
        }
    if reminders.get("overdue"):
        return {
            "level": "warning",
            "label": f"逾期提醒 {reminders['overdue']} 条",
            "detail": "",
        }
    if care.get("overdue"):
        return {
            "level": "warning",
            "label": f"复查逾期 {care['overdue']} 项",
            "detail": "、".join(
                care.get("overdue_items")
                or care.get("items") or []
            )[:120],
        }
    max_severity = int(symptoms.get("max_severity") or 0)
    if max_severity >= 7:
        return {
            "level": "warning",
            "label": "近期重度症状",
            "detail": f"{symptoms.get('severe_symptom') or ''} "
            f"{max_severity}/10".strip(),
        }
    if labs.get("abnormal"):
        return {
            "level": "warning",
            "label": f"化验异常 {labs['abnormal']} 项",
            "detail": "、".join(labs.get("items") or [])[:120],
        }
    over_target = [
        item for item in (metrics or []) if item.get("over_target")
    ]
    if over_target:
        return {
            "level": "warning",
            "label": f"指标未达标 {len(over_target)} 项",
            "detail": "、".join(
                f"{item.get('label', '')} {item.get('value', '')}"
                f"{item.get('unit') or ''}"
                for item in over_target[:3]
            )[:120],
        }
    if medications.get("expired") or medications.get("low_stock"):
        parts = []
        if medications.get("expired"):
            parts.append(f"过期 {medications['expired']} 种")
        if medications.get("low_stock"):
            parts.append(f"低库存 {medications['low_stock']} 种")
        return {
            "level": "warning",
            "label": "用药库存提醒",
            "detail": "、".join(parts),
        }
    adherence = medications.get("adherence") or {}
    if adherence.get("expected", 0) >= 3 and adherence.get("rate", 100) < 60:
        return {
            "level": "info",
            "label": f"用药打卡 {int(adherence['rate'])}%",
            "detail": "",
        }
    if vaccinations.get("overdue"):
        return {
            "level": "info",
            "label": f"疫苗逾期 {vaccinations['overdue']} 剂",
            "detail": vaccinations.get("next_due") or "",
        }
    if reminders.get("due_today"):
        return {
            "level": "info",
            "label": f"今天有 {reminders['due_today']} 条提醒",
            "detail": "",
        }
    if care.get("due_soon"):
        return {
            "level": "info",
            "label": f"复查临近 {care['due_soon']} 项",
            "detail": "、".join(care.get("items") or [])[:120],
        }
    next_appointment = appointments.get("next") or {}
    days_until = next_appointment.get("days_until")
    if (
        next_appointment.get("title")
        and isinstance(days_until, int)
        and 0 <= days_until <= 7
    ):
        when = str(next_appointment.get("scheduled_at") or "")
        return {
            "level": "info",
            "label": "有近期就诊预约",
            "detail": " ".join(
                part for part in (
                    next_appointment.get("title") or "",
                    when[:16].replace("T", " "),
                ) if part
            )[:120],
        }
    if medications.get("expiring"):
        return {
            "level": "info",
            "label": f"药物即将过期 {medications['expiring']} 种",
            "detail": "",
        }
    return {"level": "ok", "label": "暂无需要关注", "detail": ""}


@app.get("/api/family/overview")
async def family_overview(days: int = 7):
    """家庭健康总览：按档案汇总最近指标、用药、提醒、症状与会诊。"""
    from memory import get_session_store
    from memory.session_store import (
        LOWER_BOUND_TARGET_METRICS,
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
    vaccinations = await asyncio.to_thread(
        store.list_vaccinations, None, 100000
    )
    appointments = await asyncio.to_thread(
        store.list_appointments, None, None, False, 100000, 0
    )
    lab_results = await asyncio.to_thread(
        store.list_lab_results, None, None, None, days, 100000, 0
    )
    lab_history = await asyncio.to_thread(
        store.list_lab_results, None, None, None, 180, 100000, 0
    )
    care_items = await asyncio.to_thread(
        store.list_care_items, None, False, False, 100000
    )
    visit_records = await asyncio.to_thread(
        store.list_visit_records, None, 0, "", 100000
    )
    condition_items = await asyncio.to_thread(
        store.list_conditions, None, True, None, 100000
    )
    targets = await asyncio.to_thread(store.list_metric_targets, None)
    sessions = await asyncio.to_thread(
        store.list_sessions_for_timeline, None, cutoff, "", 100000
    )
    latest_answers = await asyncio.to_thread(
        store.latest_session_answers_by_profile
    )
    recorded_days = await asyncio.to_thread(
        store.recorded_days_by_profile, 3650
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
    critical_by_profile: Dict[str, Dict[str, Any]] = {}
    from memory.session_store import critical_metric_alerts

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
        alerts = critical_metric_alerts(
            metric, item.get("value1"), item.get("value2")
        )
        if alerts:
            entry = critical_by_profile.setdefault(profile, {
                "count": 0, "items": [],
            })
            entry["count"] += 1
            if len(entry["items"]) < 3:
                entry["items"].append(
                    f"{label} {format_metric_value(item)}"
                )
        metrics_by_profile.setdefault(profile, []).append({
            "metric": metric,
            "label": label,
            "value": format_metric_value(item),
            "unit": item.get("unit") or default_unit,
            "measured_at": item.get("measured_at", ""),
            "over_target": over_target,
            "target_direction": (
                "low" if metric in LOWER_BOUND_TARGET_METRICS else "high"
            ),
            "critical_alerts": alerts,
        })

    medications_by_profile: Dict[str, Dict[str, Any]] = {}
    medication_names_by_profile: Dict[str, List[str]] = {}
    for item in medications:
        profile = item.get("profile") or ""
        if not profile or not item.get("active"):
            continue
        if item.get("name"):
            medication_names_by_profile.setdefault(
                profile, []
            ).append(str(item["name"]))
        entry = medications_by_profile.setdefault(profile, {
            "active": 0,
            "taken_today": 0,
            "skipped_today": 0,
            "names": [],
            "last_taken_at": "",
            "last_skip_at": "",
            "week_count": 0,
            "expected_week_doses": 0,
            "low_stock": 0,
            "expiring": 0,
            "expired": 0,
            "stock_alerts": [],
        })
        entry["active"] += 1
        entry["taken_today"] += int(item.get("today_count") or 0)
        entry["skipped_today"] += int(item.get("skipped_today") or 0)
        if item.get("low_stock"):
            entry["low_stock"] += 1
        if item.get("expired"):
            entry["expired"] += 1
        elif item.get("expiring_soon"):
            entry["expiring"] += 1
        if (
            item.get("low_stock")
            or item.get("expired")
            or item.get("expiring_soon")
        ) and len(entry["stock_alerts"]) < 3:
            entry["stock_alerts"].append(item.get("name") or "")
        expected_week = int(item.get("expected_week_doses") or 0)
        # 无明确频次（必要时/每月一次等）的药物只展示次数，不进入
        # 家庭依从率的分子
        if expected_week > 0:
            entry["week_count"] += int(item.get("week_count") or 0)
        entry["expected_week_doses"] += expected_week
        if len(entry["names"]) < 3:
            entry["names"].append(item.get("name") or "")
        last_taken = str(item.get("last_taken_at") or "")
        if last_taken > entry["last_taken_at"]:
            entry["last_taken_at"] = last_taken
        last_skip = str(item.get("last_skip_at") or "")
        if last_skip > entry["last_skip_at"]:
            entry["last_skip_at"] = last_skip

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
            "count": 0, "latest": None, "max_severity": 0,
            "severe_symptom": "",
        })
        entry["count"] += 1
        severity = int(item.get("severity") or 0)
        if severity > int(entry["max_severity"] or 0):
            entry["max_severity"] = severity
            entry["severe_symptom"] = item.get("symptom", "")
        if entry["latest"] is None:
            entry["latest"] = {
                "symptom": item.get("symptom", ""),
                "severity": severity,
                "recorded_at": item.get("recorded_at", ""),
            }

    vaccinations_by_profile: Dict[str, Dict[str, Any]] = {}
    latest_vaccination_ids = _latest_vaccination_ids(vaccinations)
    for item in vaccinations:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = vaccinations_by_profile.setdefault(profile, {
            "count": 0, "latest": None, "next_due": "", "overdue": 0,
        })
        entry["count"] += 1
        if entry["latest"] is None:
            entry["latest"] = {
                "vaccine_name": item.get("vaccine_name", ""),
                "dose_number": int(item.get("dose_number") or 0),
                "vaccinated_at": item.get("vaccinated_at", ""),
            }
        if int(item.get("id") or 0) not in latest_vaccination_ids:
            continue
        due = str(item.get("next_due") or "")[:10]
        if due:
            if due < today:
                entry["overdue"] += 1
            elif not entry["next_due"] or due < entry["next_due"]:
                entry["next_due"] = due

    labs_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in lab_results:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = labs_by_profile.setdefault(profile, {
            "count": 0, "abnormal": 0, "critical": 0,
            "items": [], "critical_items": [],
        })
        entry["count"] += 1
        if item.get("critical"):
            entry["critical"] += 1
            if len(entry["critical_items"]) < 3:
                value = f"{float(item.get('value') or 0):g}"
                unit = str(item.get("unit") or "")
                if unit:
                    value += f" {unit}"
                entry["critical_items"].append(
                    f"{item.get('item') or '化验'} {value}".strip()
                )
        if item.get("abnormal"):
            entry["abnormal"] += 1
            if len(entry["items"]) < 3:
                value = f"{float(item.get('value') or 0):g}"
                unit = str(item.get("unit") or "")
                if unit:
                    value += f" {unit}"
                entry["items"].append(
                    f"{item.get('item') or '化验'} {value}".strip()
                )

    labs_history_by_profile: Dict[str, List[Dict[str, Any]]] = {}
    for item in lab_history:
        profile = item.get("profile") or ""
        if profile:
            labs_history_by_profile.setdefault(profile, []).append(item)
    condition_names_by_profile: Dict[str, List[str]] = {}
    for item in condition_items:
        profile = item.get("profile") or ""
        name = str(item.get("name") or "").strip()
        if profile and name:
            condition_names_by_profile.setdefault(profile, []).append(name)
    from core.medication_lab_safety import check_medication_lab_safety
    from core.condition_drug_safety import check_condition_drug_safety

    medication_lab_by_profile: Dict[str, Dict[str, Any]] = {}
    for profile, med_names in medication_names_by_profile.items():
        lab_safety_alerts = check_medication_lab_safety(
            med_names,
            labs_history_by_profile.get(profile, []),
            contexts.get(profile) or {},
        ).get("alerts") or []
        condition_safety_alerts = check_condition_drug_safety(
            med_names,
            condition_names_by_profile.get(profile, []),
        ).get("alerts") or []
        combined = lab_safety_alerts + condition_safety_alerts
        if combined:
            medication_lab_by_profile[profile] = {
                "count": len(combined),
                "alerts": combined[:3],
                "lab_count": len(lab_safety_alerts),
                "condition_count": len(condition_safety_alerts),
            }

    conditions_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in condition_items:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = conditions_by_profile.setdefault(profile, {
            "count": 0, "names": [],
        })
        entry["count"] += 1
        if len(entry["names"]) < 5:
            entry["names"].append(item.get("name") or "")

    visits_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in visit_records:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = visits_by_profile.setdefault(profile, {
            "count": 0, "latest": None,
        })
        entry["count"] += 1
        if entry["latest"] is None:
            entry["latest"] = {
                "id": item.get("id"),
                "visit_date": item.get("visit_date", ""),
                "diagnosis": item.get("diagnosis", ""),
                "department": item.get("department", ""),
                "doctor": item.get("doctor", ""),
                "next_followup_date": item.get("next_followup_date", ""),
            }

    care_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in care_items:
        profile = item.get("profile") or ""
        if not profile or not item.get("active"):
            continue
        entry = care_by_profile.setdefault(profile, {
            "overdue": 0, "due_soon": 0, "next": None, "items": [],
            "overdue_items": [],
        })
        if item.get("overdue"):
            entry["overdue"] += 1
            if len(entry["overdue_items"]) < 3:
                entry["overdue_items"].append(
                    item.get("title") or ""
                )
        if item.get("due_soon"):
            entry["due_soon"] += 1
        due = str(item.get("next_due_at") or "")
        if due and (
            entry["next"] is None
            or due < str(entry["next"].get("next_due_at") or "")
        ):
            entry["next"] = {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "next_due_at": due,
                "days_until": item.get("days_until"),
            }
        if (
            item.get("overdue") or item.get("due_soon")
        ) and len(entry["items"]) < 3:
            entry["items"].append(item.get("title") or "")

    appointments_by_profile: Dict[str, Dict[str, Any]] = {}
    for item in appointments:
        profile = item.get("profile") or ""
        if not profile:
            continue
        entry = appointments_by_profile.setdefault(profile, {
            "upcoming": 0, "next": None,
        })
        if item.get("status") != "upcoming" or item.get("is_past"):
            continue
        entry["upcoming"] += 1
        current = entry["next"]
        if current is None or str(item.get("scheduled_at") or "") < str(
            current.get("scheduled_at") or ""
        ):
            entry["next"] = {
                "id": item.get("id"),
                "title": item.get("title", ""),
                "scheduled_at": item.get("scheduled_at", ""),
                "department": item.get("department", ""),
                "doctor": item.get("doctor", ""),
                "days_until": item.get("days_until"),
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
            "active": 0, "taken_today": 0, "skipped_today": 0,
            "names": [], "last_taken_at": "", "last_skip_at": "",
            "adherence": None,
            "low_stock": 0, "expiring": 0, "expired": 0,
            "stock_alerts": [],
        })
        reminder = reminders_by_profile.get(name, {
            "pending": 0, "overdue": 0, "due_today": 0,
        })
        symptom = symptoms_by_profile.get(name, {
            "count": 0, "latest": None, "max_severity": 0,
            "severe_symptom": "",
        })
        vaccination = vaccinations_by_profile.get(name, {
            "count": 0, "latest": None, "next_due": "", "overdue": 0,
        })
        appointment = appointments_by_profile.get(name, {
            "upcoming": 0, "next": None,
        })
        labs = labs_by_profile.get(name, {
            "count": 0, "abnormal": 0, "critical": 0,
            "items": [], "critical_items": [],
        })
        medication_lab = medication_lab_by_profile.get(name, {
            "count": 0, "alerts": [],
        })
        critical = critical_by_profile.get(name, {
            "count": 0, "items": [],
        })
        care = care_by_profile.get(name, {
            "overdue": 0, "due_soon": 0, "next": None, "items": [],
            "overdue_items": [],
        })
        visits = visits_by_profile.get(name, {
            "count": 0, "latest": None,
        })
        conditions_info = conditions_by_profile.get(name, {
            "count": 0, "names": [],
        })
        consultation = sessions_by_profile.get(name, {
            "count": 0, "latest_at": "",
        })
        activity_candidates = [
            metrics[0]["measured_at"] if metrics else "",
            (symptom.get("latest") or {}).get("recorded_at") or "",
            (vaccination.get("latest") or {}).get("vaccinated_at") or "",
            consultation.get("latest_at") or "",
            medication.get("last_taken_at") or "",
            medication.get("last_skip_at") or "",
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
            or str(
                (vaccination.get("latest") or {}).get("vaccinated_at") or ""
            )[:10] == today
            or medication.get("taken_today")
            or medication.get("skipped_today")
            or str(consultation.get("latest_at") or "")[:10] == today
        )
        attention = _family_attention(
            critical=critical,
            consultation_risk=(
                risk_by_profile.get(name, "")
                if consultation.get("count") else ""
            ),
            labs=labs,
            reminders=reminder,
            care=care,
            symptoms=symptom,
            medications=medication,
            medication_lab=medication_lab,
            vaccinations=vaccination,
            appointments=appointment,
            metrics=metrics,
        )
        profiles.append({
            "name": name,
            "context": contexts.get(name) or {},
            "metrics": metrics,
            "medications": medication,
            "medication_lab_safety": medication_lab,
            "reminders": reminder,
            "symptoms": symptom,
            "vaccinations": vaccination,
            "appointments": appointment,
            "labs": labs,
            "critical_alerts": critical,
            "care_plan": care,
            "visits": visits,
            "conditions": conditions_info,
            "consultations": {
                **consultation,
                "risk": risk_by_profile.get(name, ""),
            },
            "attention": attention,
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
    # 稳定排序：需要关注的成员排在前面，同级别内保持最近活动顺序
    profiles.sort(
        key=lambda item: FAMILY_ATTENTION_ORDER.get(
            (item.get("attention") or {}).get("level"), 9
        )
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
        "待办提醒", f"近{days}天症状", f"近{days}天会诊", "最近活动",
        "疫苗接种", "用药库存", "下次就诊", "化验异常", "危急值",
        "复查计划", "最近就诊", "慢病", "关注提示",
    ]]
    for item in data["profiles"]:
        context = item.get("context") or {}
        metric_text = "；".join(
            (
                f"{metric['label']} {metric['value']} {metric['unit']}".strip()
                + (
                    "（低于目标）"
                    if metric.get("target_direction") == "low"
                    else "（超标）"
                ) if metric.get("over_target") else ""
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
        vaccinations_info = item.get("vaccinations") or {}
        vaccination_text = ""
        if vaccinations_info.get("count"):
            vaccination_text = str(vaccinations_info.get("count"))
            latest_vaccination = vaccinations_info.get("latest") or {}
            if latest_vaccination.get("vaccine_name"):
                vaccination_text += (
                    f"（最近 {latest_vaccination['vaccine_name']}"
                    f" 第{int(latest_vaccination.get('dose_number') or 1)}剂）"
                )
            if vaccinations_info.get("overdue"):
                vaccination_text += (
                    f"，逾期 {int(vaccinations_info['overdue'])}"
                )
            elif vaccinations_info.get("next_due"):
                vaccination_text += (
                    f"，下一剂 {vaccinations_info['next_due']}"
                )
        stock_parts = []
        if medications.get("low_stock"):
            stock_parts.append(f"库存偏低 {medications['low_stock']} 种")
        if medications.get("expired"):
            stock_parts.append(f"已过期 {medications['expired']} 种")
        elif medications.get("expiring"):
            stock_parts.append(f"即将过期 {medications['expiring']} 种")
        names = [
            name for name in (medications.get("stock_alerts") or []) if name
        ]
        if names:
            stock_parts.append("；".join(names))
        stock_text = "，".join(stock_parts)
        appointment_info = item.get("appointments") or {}
        next_visit = appointment_info.get("next") or {}
        if next_visit:
            visit_text = (
                f"{str(next_visit.get('scheduled_at') or '')[:16]} "
                f"{next_visit.get('title') or '就诊'}"
            )
            if next_visit.get("department"):
                visit_text += f"（{next_visit['department']}）"
            days_until = next_visit.get("days_until")
            if days_until == 0:
                visit_text += " · 今天"
            elif isinstance(days_until, int) and days_until > 0:
                visit_text += f" · {days_until} 天后"
            if int(appointment_info.get("upcoming") or 0) > 1:
                visit_text += (
                    f"；共 {int(appointment_info['upcoming'])} 个待就诊"
                )
        else:
            visit_text = ""
        labs_info = item.get("labs") or {}
        lab_text = ""
        if labs_info.get("count"):
            lab_text = f"近 {days} 天 {int(labs_info['count'])} 条"
            if labs_info.get("critical"):
                lab_text += f"，危急值 {int(labs_info['critical'])} 项"
            if labs_info.get("abnormal"):
                lab_text += f"，异常 {int(labs_info['abnormal'])} 项"
            names = [name for name in (labs_info.get("items") or []) if name]
            if names:
                lab_text += "（" + "；".join(names) + "）"
        medication_lab_info = item.get("medication_lab_safety") or {}
        if medication_lab_info.get("count"):
            lab_text += (
                f"；用药安全需核对 {int(medication_lab_info['count'])} 条"
            )
        critical_info = item.get("critical_alerts") or {}
        critical_text = ""
        if critical_info.get("count"):
            critical_text = f"{int(critical_info['count'])} 项"
            names = [
                name for name in (critical_info.get("items") or []) if name
            ]
            if names:
                critical_text += "（" + "；".join(names) + "）"
        care_info = item.get("care_plan") or {}
        care_parts = []
        if care_info.get("overdue"):
            care_parts.append(f"逾期 {int(care_info['overdue'])} 项")
        if care_info.get("due_soon"):
            care_parts.append(f"近 7 天 {int(care_info['due_soon'])} 项")
        next_care = care_info.get("next") or {}
        if next_care.get("title"):
            care_parts.append(
                f"{next_care['title']} {next_care.get('next_due_at', '')}"
            )
        care_text = "，".join(care_parts)
        visit_info = item.get("visits") or {}
        latest_visit = visit_info.get("latest") or {}
        if latest_visit:
            visit_record_text = (
                f"{latest_visit.get('visit_date') or ''} "
                f"{latest_visit.get('diagnosis') or '就诊'}"
            )
            details = [
                latest_visit.get("department") or "",
                latest_visit.get("doctor") or "",
            ]
            details = [part for part in details if part]
            if details:
                visit_record_text += f"（{' · '.join(details)}）"
            if latest_visit.get("next_followup_date"):
                visit_record_text += (
                    f"，下次复诊 {latest_visit['next_followup_date']}"
                )
        else:
            visit_record_text = ""
        conditions_info = item.get("conditions") or {}
        condition_text = ""
        if conditions_info.get("count"):
            condition_text = f"{int(conditions_info['count'])} 项"
            names = [
                name for name in (conditions_info.get("names") or [])
                if name
            ]
            if names:
                condition_text += "（" + "；".join(names) + "）"
        attention_info = item.get("attention") or {}
        attention_text = attention_info.get("label") or ""
        if attention_info.get("detail"):
            attention_text += f"（{attention_info['detail']}）"
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
            csv_safe_cell(vaccination_text),
            csv_safe_cell(stock_text),
            csv_safe_cell(visit_text),
            csv_safe_cell(lab_text),
            csv_safe_cell(critical_text),
            csv_safe_cell(care_text),
            csv_safe_cell(visit_record_text),
            csv_safe_cell(condition_text),
            csv_safe_cell(attention_text),
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


@app.get("/api/family/compare/export.csv")
async def export_family_compare_csv(metric: str = "weight", days: int = 30):
    """导出家庭指标对比 CSV（带 BOM）。"""
    import csv
    import io

    data = await family_compare(metric=metric, days=days)

    def fmt(value: Any) -> str:
        if value is None:
            return ""
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            return str(value)
        return str(int(number)) if number.is_integer() else f"{number:g}"

    rows = [[
        "档案", "指标", "单位", "天数", "平均", "最低", "最高",
        "次数", "达标次数", "最近日期", "最近值",
    ]]
    for item in data["profiles"]:
        avg = fmt(item.get("avg_value1"))
        if item.get("avg_value2") is not None:
            avg += f"/{fmt(item.get('avg_value2'))}"
        latest = item.get("latest") or {}
        latest_value = fmt(latest.get("value1"))
        if latest.get("value2") is not None:
            latest_value += f"/{fmt(latest.get('value2'))}"
        rows.append([
            csv_safe_cell(item.get("name", "")),
            csv_safe_cell(data["label"]),
            csv_safe_cell(data["unit"]),
            str(data["days"]),
            csv_safe_cell(avg),
            csv_safe_cell(fmt(item.get("min_value1"))),
            csv_safe_cell(fmt(item.get("max_value1"))),
            str(int(item.get("count") or 0)),
            csv_safe_cell(fmt(item.get("within_target"))),
            csv_safe_cell(latest.get("measured_at", "")),
            csv_safe_cell(latest_value),
        ])
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = (
        "medix-family-compare-"
        + datetime.now().strftime("%Y%m%d")
        + ".csv"
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


def _attachment_dir_files() -> Dict[str, Dict[str, Any]]:
    """扫描附件目录中的文件，返回文件名到大小/修改时间的映射。"""
    base = ATTACHMENT_DIR.resolve()
    files: Dict[str, Dict[str, Any]] = {}
    if not base.is_dir():
        return files
    try:
        entries = list(base.iterdir())
    except OSError:
        return files
    for path in entries:
        if not path.is_file():
            continue
        try:
            stat = path.stat()
        except OSError:
            files[path.name] = {
                "name": path.name,
                "size": 0,
                "modified_at": "",
                "mtime": 0.0,
                "stat_error": True,
            }
            continue
        try:
            modified_at = datetime.fromtimestamp(
                stat.st_mtime
            ).isoformat(timespec="seconds")
        except (OSError, OverflowError, ValueError):
            modified_at = ""
        files[path.name] = {
            "name": path.name,
            "size": stat.st_size,
            "modified_at": modified_at,
            "mtime": stat.st_mtime,
        }
    return files


def _safe_attachment_name(name: Any) -> str:
    text = str(name or "").replace("\\", "/").split("/")[-1]
    text = "".join(char for char in text if char.isprintable())
    text = " ".join(text.split())
    if not text:
        return "附件"
    if len(text) <= 120:
        return text
    suffix = Path(text).suffix
    if suffix and len(suffix) < 120:
        return text[: 120 - len(suffix)].rstrip() + suffix
    return text[:120]


def _attachment_content_matches(mime: str, content: bytes) -> bool:
    """校验文件头，避免把其他内容伪装成图片或 PDF。"""
    if mime == "application/pdf":
        return content.startswith(b"%PDF-")
    if mime == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime == "image/webp":
        return (
            len(content) >= 12
            and content[:4] == b"RIFF"
            and content[8:12] == b"WEBP"
        )
    return False


def _png_dimensions(content: bytes) -> Optional[tuple]:
    if len(content) < 24 or content[12:16] != b"IHDR":
        return None
    return (
        int.from_bytes(content[16:20], "big"),
        int.from_bytes(content[20:24], "big"),
    )


def _jpeg_dimensions(content: bytes) -> Optional[tuple]:
    size = len(content)
    pos = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while pos + 3 < size:
        if content[pos] != 0xFF:
            pos += 1
            continue
        marker = content[pos + 1]
        pos += 2
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue
        if marker in (0xD9, 0xDA):
            return None
        if pos + 2 > size:
            return None
        length = int.from_bytes(content[pos:pos + 2], "big")
        if length < 2 or pos + length > size:
            return None
        if marker in sof_markers:
            if length < 7:
                return None
            return (
                int.from_bytes(content[pos + 5:pos + 7], "big"),
                int.from_bytes(content[pos + 3:pos + 5], "big"),
            )
        pos += length
    return None


def _webp_dimensions(content: bytes) -> Optional[tuple]:
    if len(content) < 30:
        return None
    chunk = content[12:16]
    if chunk == b"VP8X":
        return (
            1 + int.from_bytes(content[24:27], "little"),
            1 + int.from_bytes(content[27:30], "little"),
        )
    if chunk == b"VP8 ":
        if content[23:26] != b"\x9d\x01\x2a":
            return None
        return (
            int.from_bytes(content[26:28], "little") & 0x3FFF,
            int.from_bytes(content[28:30], "little") & 0x3FFF,
        )
    if chunk == b"VP8L":
        if content[20] != 0x2F:
            return None
        bits = int.from_bytes(content[21:25], "little")
        return (
            1 + (bits & 0x3FFF),
            1 + ((bits >> 14) & 0x3FFF),
        )
    return None


def _attachment_dimensions_allowed(mime: str, content: bytes) -> bool:
    """拒绝解码后规模过大的图片；无法解析尺寸时放行。"""
    if mime == "image/png":
        dimensions = _png_dimensions(content)
    elif mime == "image/jpeg":
        dimensions = _jpeg_dimensions(content)
    elif mime == "image/webp":
        dimensions = _webp_dimensions(content)
    else:
        return True
    if dimensions is None:
        return True
    width, height = dimensions
    if width <= 0 or height <= 0:
        return False
    return (
        width <= MAX_ATTACHMENT_EDGE
        and height <= MAX_ATTACHMENT_EDGE
        and width * height <= MAX_ATTACHMENT_PIXELS
    )


@app.post("/api/attachments")
async def upload_attachment(
    request: Request,
    file: UploadFile = File(...),
    profile: str = Form(""),
    kind: str = Form("other"),
    ref_id: int = Form(0),
    note: str = Form(""),
):
    """上传本地就医资料（JPG/PNG/WebP/PDF，单文件不超过 10 MB）。"""
    from memory import get_session_store
    from memory.session_store import ATTACHMENT_KINDS

    if (
        _request_content_length(request)
        > MAX_ATTACHMENT_UPLOAD_BYTES + 1024 * 1024
    ):
        raise HTTPException(status_code=413, detail="附件超过 10 MB")
    if ref_id < 0 or ref_id > MAX_SQLITE_INT:
        raise HTTPException(status_code=400, detail="关联记录编号不正确")
    raw_name = str(file.filename or "").replace("\\", "/").split("/")[-1]
    # 扩展名从原始文件名读取，避免超长名称被截断后再取后缀
    suffix = Path(raw_name).suffix.lower()
    original_name = _safe_attachment_name(raw_name)
    mime = ATTACHMENT_MIME_BY_EXT.get(suffix)
    if mime is None:
        raise HTTPException(
            status_code=400, detail="仅支持 JPG/PNG/WebP/PDF 附件"
        )
    declared = str(file.content_type or "").split(";")[0].strip().lower()
    if declared in {"image/jpg", "image/pjpeg"}:
        declared = "image/jpeg"
    elif declared == "application/x-pdf":
        declared = "application/pdf"
    if (
        declared
        and declared != "application/octet-stream"
        and declared != mime
    ):
        raise HTTPException(
            status_code=400, detail="附件类型与扩展名不一致"
        )
    content = await file.read(MAX_ATTACHMENT_UPLOAD_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="附件内容为空")
    if len(content) > MAX_ATTACHMENT_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="附件超过 10 MB")
    if not _attachment_content_matches(mime, content):
        raise HTTPException(
            status_code=400, detail="附件内容与文件类型不一致"
        )
    if not _attachment_dimensions_allowed(mime, content):
        raise HTTPException(
            status_code=400, detail="图片尺寸过大，请压缩后再上传"
        )

    store = get_session_store()
    kind_key = str(kind or "other").strip().lower()
    if kind_key not in ATTACHMENT_KINDS:
        kind_key = "other"
    if ref_id:
        if kind_key not in {"visit", "lab"}:
            raise HTTPException(
                status_code=400, detail="该资料类型不支持关联记录"
            )
        getter = (
            store.get_visit_record if kind_key == "visit"
            else store.get_lab_result
        )
        record = await asyncio.to_thread(getter, ref_id)
        if record is None:
            raise HTTPException(
                status_code=400, detail="关联的就诊/化验记录不存在"
            )
        if (
            _normalized_profile_key(record.get("profile"))
            != _normalized_profile_key(profile)
        ):
            raise HTTPException(
                status_code=400, detail="关联记录不属于该患者档案"
            )

    attachment_dir = ATTACHMENT_DIR.resolve()
    attachment_dir.mkdir(parents=True, exist_ok=True)
    restrict_permissions(attachment_dir, 0o700)
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    target = (attachment_dir / stored_name).resolve()
    if target.parent != attachment_dir:
        raise HTTPException(status_code=400, detail="附件名称不正确")
    try:
        target.write_bytes(content)
        restrict_permissions(target, 0o600)
    except OSError:
        logger.warning("附件写入失败")
        raise HTTPException(status_code=500, detail="附件保存失败")

    try:
        item = await asyncio.to_thread(
            store.add_attachment,
            profile,
            kind_key,
            ref_id,
            original_name,
            stored_name,
            mime,
            len(content),
            note,
        )
    except Exception:
        try:
            target.unlink()
        except OSError:
            pass
        logger.exception("附件登记失败")
        raise HTTPException(status_code=500, detail="附件保存失败")
    if item is None:
        try:
            target.unlink()
        except OSError:
            pass
        raise HTTPException(status_code=400, detail="附件信息不正确")
    await record_audit("attachment.create", str(item["id"]))
    return {"ok": True, "attachment": item}


@app.get("/api/attachments")
async def list_attachments(
    profile: Optional[str] = None,
    kind: Optional[str] = None,
    ref_id: Optional[int] = None,
    limit: int = 100,
    offset: int = 0,
    q: str = "",
    linked: Optional[bool] = None,
    start: str = "",
    end: str = "",
    sort: str = "",
):
    """附件列表（不含文件内容）；可按档案、类型、关联、日期与关键字筛选。"""
    from memory import get_session_store
    from memory.session_store import ATTACHMENT_KINDS

    limit = max(1, min(int(limit or 100), 1000))
    offset = _bounded_offset(offset)
    q = " ".join(str(q or "").split())[:80]
    kind_key = str(kind or "").strip().lower()
    if kind_key and kind_key not in ATTACHMENT_KINDS:
        raise HTTPException(status_code=400, detail="资料类型不正确")
    if ref_id is not None and (ref_id < 0 or ref_id > MAX_SQLITE_INT):
        raise HTTPException(status_code=400, detail="关联记录编号不正确")

    def _day(value: str, label: str) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            return datetime.strptime(text, "%Y-%m-%d").strftime("%Y-%m-%d")
        except ValueError:
            raise HTTPException(
                status_code=400, detail=f"{label}格式应为 YYYY-MM-DD"
            )

    start_day = _day(start, "开始日期")
    end_day = _day(end, "结束日期")
    if start_day and end_day and start_day > end_day:
        raise HTTPException(
            status_code=400, detail="开始日期不能晚于结束日期"
        )
    sort_key = str(sort or "").strip().lower()
    if sort_key not in {
        "", "created_asc", "size_desc", "size_asc", "name_asc",
    }:
        raise HTTPException(status_code=400, detail="排序方式不正确")
    store = get_session_store()
    items = await asyncio.to_thread(
        store.list_attachments,
        profile,
        kind_key or None,
        ref_id,
        limit,
        offset,
        q,
        linked,
        start_day,
        end_day,
        sort_key,
    )
    visit_ids = {
        int(item.get("ref_id") or 0)
        for item in items
        if item.get("kind") == "visit" and item.get("ref_id")
    }
    lab_ids = {
        int(item.get("ref_id") or 0)
        for item in items
        if item.get("kind") == "lab" and item.get("ref_id")
    }
    visit_map: Dict[int, Dict[str, Any]] = {}
    lab_map: Dict[int, Dict[str, Any]] = {}
    if visit_ids:
        visits = await asyncio.to_thread(
            store.list_visit_records_by_ids, visit_ids
        )
        visit_map = {
            int(row["id"]): row for row in visits
            if int(row.get("id") or 0) in visit_ids
        }
    if lab_ids:
        labs = await asyncio.to_thread(
            store.list_lab_results_by_ids, lab_ids
        )
        lab_map = {
            int(row["id"]): row for row in labs
            if int(row.get("id") or 0) in lab_ids
        }
    for item in items:
        reference = int(item.get("ref_id") or 0)
        label = ""
        if item.get("kind") == "visit" and reference in visit_map:
            visit = visit_map[reference]
            label = " · ".join(part for part in (
                str(visit.get("visit_date") or "")[:10],
                visit.get("department") or "",
                visit.get("diagnosis") or "",
            ) if part)
        elif item.get("kind") == "lab" and reference in lab_map:
            lab = lab_map[reference]
            try:
                value = f"{float(lab.get('value')):g}"
            except (TypeError, ValueError, OverflowError):
                value = str(lab.get("value") or "")
            result = f"{lab.get('item') or '化验'} {value}".strip()
            if lab.get("unit"):
                result += f" {lab['unit']}"
            label = " · ".join(part for part in (
                str(lab.get("tested_at") or "")[:10],
                result,
                lab.get("panel") or "",
            ) if part)
        item["ref_label"] = label[:100]
    attachment_dir = ATTACHMENT_DIR.resolve()
    for item in items:
        stored_name = str(item.get("stored_name") or "")
        target = None
        if stored_name:
            try:
                target = (attachment_dir / stored_name).resolve()
            except (OSError, ValueError):
                target = None
        item["file_missing"] = not bool(
            target
            and target.parent == attachment_dir
            and target.is_file()
        )
    total_count = 0
    total_size = 0
    if items:
        total_count = int(items[0].pop("_total_count", len(items)))
        total_size = int(items[0].pop("_total_size", 0))
    elif offset:
        # 翻页越过末页时列表为空，但总数仍应按筛选条件返回
        probe = await asyncio.to_thread(
            store.list_attachments,
            profile,
            kind_key or None,
            ref_id,
            1,
            0,
            q,
            linked,
            start_day,
            end_day,
            sort_key,
        )
        if probe:
            total_count = int(probe[0].get("_total_count") or 0)
            total_size = int(probe[0].get("_total_size") or 0)
    for item in items:
        item.pop("_total_count", None)
        item.pop("_total_size", None)
    return {
        "attachments": items,
        "total_count": total_count,
        "total_size": total_size,
    }


@app.get("/api/attachments/counts")
async def attachment_ref_counts(profile: Optional[str] = None):
    """按就诊/化验记录统计已关联附件数量，并返回类型分布。"""
    from memory import get_session_store

    store = get_session_store()
    visit_counts, lab_counts, kinds = await asyncio.gather(
        asyncio.to_thread(store.count_attachments_by_ref, "visit"),
        asyncio.to_thread(store.count_attachments_by_ref, "lab"),
        asyncio.to_thread(store.attachment_kind_stats, profile),
    )
    return {
        "counts": {"visit": visit_counts, "lab": lab_counts},
        "kinds": kinds,
        "total_count": sum(item["count"] for item in kinds),
        "total_size": sum(item["size"] for item in kinds),
    }


@app.get("/api/attachments/export.csv")
async def export_attachments_csv(
    profile: Optional[str] = None,
    kind: str = "",
    ref_id: Optional[int] = None,
    start: str = "",
    end: str = "",
    q: str = "",
    linked: Optional[bool] = None,
    sort: str = "",
):
    """导出附件清单 CSV（带 BOM，不含文件内容）。"""
    import csv
    import io

    rows = [[
        "上传日期", "文件名", "类型", "大小(字节)", "备注",
        "关联状态", "关联编号", "关联摘要", "文件状态",
    ]]
    offset = 0
    while offset < 100000:
        data = await list_attachments(
            profile=profile,
            kind=kind,
            ref_id=ref_id,
            limit=1000,
            offset=offset,
            q=q,
            linked=linked,
            start=start,
            end=end,
            sort=sort,
        )
        batch = data.get("attachments") or []
        for item in batch:
            rows.append([
                csv_safe_cell(str(item.get("created_at") or "")[:10]),
                csv_safe_cell(item.get("original_name") or "附件"),
                csv_safe_cell(item.get("kind_label") or "其他"),
                str(int(item.get("size") or 0)),
                csv_safe_cell(item.get("note") or ""),
                "已关联" if item.get("ref_id") else "未关联",
                str(int(item.get("ref_id") or 0)),
                csv_safe_cell(item.get("ref_label") or ""),
                "缺失" if item.get("file_missing") else "正常",
            ])
        if len(batch) < 1000:
            break
        offset += 1000
    buffer = io.StringIO()
    csv.writer(buffer, lineterminator="\r\n").writerows(rows)
    content = "﻿" + buffer.getvalue()
    filename = (
        "medix-attachments-"
        + datetime.now().strftime("%Y%m%d")
        + ".csv"
    )
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )


@app.post("/api/attachments/link")
async def link_attachments(payload: AttachmentLinkRequest):
    """批量关联附件到同档案的就诊/化验记录，ref_id=0 表示取消关联。"""
    from memory import get_session_store

    store = get_session_store()
    ids = list(dict.fromkeys(payload.ids))
    if not ids or any(value <= 0 or value > MAX_SQLITE_INT for value in ids):
        raise HTTPException(status_code=400, detail="附件编号不正确")
    items = await asyncio.gather(*(
        asyncio.to_thread(store.get_attachment, value) for value in ids
    ))
    if any(item is None for item in items):
        raise HTTPException(
            status_code=404, detail="部分附件不存在，请刷新后重试"
        )
    profile_keys = {
        _normalized_profile_key(item.get("profile")) for item in items
    }
    kind_key = ""
    if payload.ref_id:
        kinds = {str(item.get("kind") or "") for item in items}
        if len(kinds) != 1 or next(iter(kinds)) not in {"visit", "lab"}:
            raise HTTPException(
                status_code=400,
                detail="只能将同类型的就诊资料或化验单关联到记录",
            )
        kind_key = next(iter(kinds))
        getter = (
            store.get_visit_record if kind_key == "visit"
            else store.get_lab_result
        )
        record = await asyncio.to_thread(getter, payload.ref_id)
        if record is None:
            raise HTTPException(
                status_code=400, detail="关联的就诊/化验记录不存在"
            )
        if (
            len(profile_keys) != 1
            or next(iter(profile_keys))
            != _normalized_profile_key(record.get("profile"))
        ):
            raise HTTPException(
                status_code=400, detail="关联记录不属于该患者档案"
            )
        updated = await asyncio.to_thread(
            store.set_attachment_refs,
            ids,
            payload.ref_id,
            kind_key,
            next(iter(profile_keys)),
        )
    else:
        updated = await asyncio.to_thread(
            store.set_attachment_refs, ids, 0
        )
    if updated != len(ids):
        raise HTTPException(
            status_code=409, detail="部分附件已变化，请刷新后重试"
        )
    await record_audit(
        "attachment.link" if payload.ref_id else "attachment.unlink",
        str(payload.ref_id),
        ",".join(str(value) for value in ids),
    )
    return {"ok": True, "updated": updated, "ref_id": payload.ref_id}


@app.patch("/api/attachments/{attachment_id}")
async def update_attachment_note(
    attachment_id: SafeIntPath, payload: AttachmentNoteRequest
):
    """更新附件备注；备注为空表示清空。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(
        store.get_attachment, attachment_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    updated = await asyncio.to_thread(
        store.update_attachment_note, attachment_id, payload.note
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    await record_audit("attachment.note", str(attachment_id))
    return {"ok": True, "attachment": updated}


@app.get("/api/attachments/{attachment_id}/file")
async def download_attachment(attachment_id: SafeIntPath):
    """读取附件原文件；文件只保存在本机数据目录。"""
    from memory import get_session_store

    item = await asyncio.to_thread(
        get_session_store().get_attachment, attachment_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    attachment_dir = ATTACHMENT_DIR.resolve()
    target = (attachment_dir / str(item.get("stored_name") or "")).resolve()
    if target.parent != attachment_dir or not target.is_file():
        raise HTTPException(status_code=404, detail="附件文件不存在")
    return FileResponse(
        target,
        media_type=item.get("mime") or "application/octet-stream",
        filename=str(item.get("original_name") or "附件"),
        content_disposition_type="inline",
        headers={"Cache-Control": "no-store"},
    )


@app.delete("/api/attachments/{attachment_id}")
async def delete_attachment(attachment_id: SafeIntPath):
    """删除附件登记并清理本机文件。"""
    from memory import get_session_store

    store = get_session_store()
    item = await asyncio.to_thread(
        store.get_attachment, attachment_id
    )
    if item is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    attachment_dir = ATTACHMENT_DIR.resolve()
    target = (
        attachment_dir / str(item.get("stored_name") or "")
    ).resolve()
    if target.parent == attachment_dir:
        try:
            # 先删文件再删登记：文件被占用时保留登记，避免留下无法清理的孤儿文件
            target.unlink(missing_ok=True)
        except OSError:
            logger.warning("附件文件删除失败")
            raise HTTPException(
                status_code=409,
                detail="附件文件正被占用，请稍后重试",
            )
    removed = await asyncio.to_thread(
        store.delete_attachment, attachment_id
    )
    if removed is None:
        raise HTTPException(status_code=404, detail="附件不存在")
    await record_audit("attachment.delete", str(attachment_id))
    return {"ok": True, "id": attachment_id}


@app.get("/api/attachments/integrity")
async def attachment_integrity():
    """检查附件登记与本地文件是否一致：缺失、孤儿与大小不一致。"""
    from time import time

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_all_attachments
    )
    files = await asyncio.to_thread(_attachment_dir_files)
    db_names = {
        os.path.normcase(str(item.get("stored_name"))): item
        for item in items
        if item.get("stored_name")
    }
    files_norm = {os.path.normcase(name): name for name in files}
    missing = [
        {
            "id": item.get("id"),
            "profile": item.get("profile") or "",
            "kind": item.get("kind") or "",
            "kind_label": item.get("kind_label") or "",
            "original_name": item.get("original_name") or "",
            "stored_name": str(item.get("stored_name") or ""),
        }
        for key, item in db_names.items()
        if key not in files_norm
    ]
    size_mismatch = []
    for key, item in db_names.items():
        file_name = files_norm.get(key)
        if file_name is None:
            continue
        try:
            db_size = int(item.get("size") or 0)
            file_size = int(files[file_name]["size"])
        except (TypeError, ValueError, KeyError, OverflowError):
            continue
        if db_size != file_size:
            size_mismatch.append({
                "id": item.get("id"),
                "profile": item.get("profile") or "",
                "kind": item.get("kind") or "",
                "kind_label": item.get("kind_label") or "",
                "original_name": item.get("original_name") or "",
                "stored_name": str(item.get("stored_name") or ""),
                "db_size": db_size,
                "file_size": file_size,
            })
    size_mismatch.sort(key=lambda item: item["stored_name"])
    now = time()
    orphans = []
    for name, info in files.items():
        if os.path.normcase(name) in db_names:
            continue
        orphans.append({
            "name": name,
            "size": info["size"],
            "modified_at": info["modified_at"],
            "recent": now - float(info["mtime"]) < 600,
        })
    orphans.sort(key=lambda item: item["name"])
    return {
        "db_count": len(items),
        "file_count": len(files),
        "missing_count": len(missing),
        "size_mismatch_count": len(size_mismatch),
        "orphan_count": len(orphans),
        "recent_orphan_count": sum(
            1 for item in orphans if item["recent"]
        ),
        "missing": missing[:100],
        "size_mismatch": size_mismatch[:100],
        "orphans": orphans[:100],
    }


@app.get("/api/attachments/export.zip")
async def export_attachments_zip(
    ids: Optional[List[int]] = Query(None)
):
    """把本机附件与清单打包为 ZIP；传 ids 时只导出选中附件。"""
    import hashlib
    import io
    import zipfile

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_all_attachments
    )
    if ids is not None:
        selected_ids = list(dict.fromkeys(ids))
        if (
            not selected_ids
            or len(selected_ids) > 200
            or any(
                value <= 0 or value > MAX_SQLITE_INT
                for value in selected_ids
            )
        ):
            raise HTTPException(
                status_code=400, detail="附件编号应为 1–200 个"
            )
        by_id = {int(item["id"]): item for item in items}
        if any(value not in by_id for value in selected_ids):
            raise HTTPException(
                status_code=404, detail="部分附件不存在"
            )
        items = [by_id[value] for value in selected_ids]

    def _build() -> Tuple[Any, int, int]:
        base = ATTACHMENT_DIR.resolve()
        manifest = {
            "version": 1,
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "count": len(items),
            "items": [],
        }
        total_bytes = 0
        missing_count = 0
        buffer = tempfile.SpooledTemporaryFile(
            max_size=8 * 1024 * 1024, mode="w+b"
        )
        with zipfile.ZipFile(
            buffer, "w", compression=zipfile.ZIP_DEFLATED
        ) as archive:
            for item in items:
                row = {
                    key: item.get(key)
                    for key in (
                        "id", "profile", "kind", "kind_label", "ref_id",
                        "original_name", "stored_name", "mime", "size",
                        "note", "created_at",
                    )
                }
                stored_name = str(item.get("stored_name") or "")
                row["file_error"] = ""
                row["sha256"] = ""
                target = None
                if stored_name:
                    try:
                        target = (base / stored_name).resolve()
                    except (OSError, ValueError):
                        row["file_error"] = "路径不合法"
                if target and target.parent != base:
                    row["file_error"] = "路径不在附件目录"
                present = bool(
                    target
                    and target.parent == base
                    and target.is_file()
                )
                if present:
                    try:
                        file_size = target.stat().st_size
                    except FileNotFoundError:
                        present = False
                    except OSError:
                        present = False
                        row["file_error"] = "无法读取文件信息"
                    else:
                        if (
                            total_bytes + file_size
                            > MAX_ATTACHMENT_EXPORT_BYTES
                        ):
                            raise AttachmentExportTooLarge(
                                "附件总大小超过上限，无法一次导出。"
                            )
                        try:
                            with target.open("rb") as source, archive.open(
                                f"files/{target.name}", "w"
                            ) as destination:
                                hasher = hashlib.sha256()
                                archived_size = 0
                                while True:
                                    chunk = source.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    archived_size += len(chunk)
                                    total_bytes += len(chunk)
                                    if (
                                        total_bytes
                                        > MAX_ATTACHMENT_EXPORT_BYTES
                                    ):
                                        raise AttachmentExportTooLarge(
                                            "附件总大小超过上限，无法一次导出。"
                                        )
                                    hasher.update(chunk)
                                    destination.write(chunk)
                        except FileNotFoundError:
                            present = False
                        except OSError:
                            present = False
                            row["file_error"] = "无法读取文件"
                        else:
                            row["sha256"] = hasher.hexdigest()
                            archive_name = target.name
                            row["archive_name"] = archive_name
                            row["archived_size"] = archived_size
                row["file_present"] = present
                if not present:
                    missing_count += 1
                manifest["items"].append(row)
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2),
            )
        total_size = buffer.tell()
        buffer.seek(0)
        return buffer, total_size, missing_count

    try:
        buffer, total_size, missing_count = await asyncio.to_thread(_build)
    except AttachmentExportTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    await record_audit(
        "attachment.export",
        detail=(
            f"count={len(items)} missing={missing_count} "
            f"bytes={total_size}"
        ),
    )
    filename = (
        "medix-attachments-"
        + datetime.now().strftime("%Y%m%d")
        + ".zip"
    )

    def _stream():
        try:
            while True:
                chunk = buffer.read(64 * 1024)
                if not chunk:
                    break
                yield chunk
        finally:
            buffer.close()

    return StreamingResponse(
        _stream(),
        media_type="application/zip",
        headers={
            "Content-Disposition": (
                f'attachment; filename="{filename}"'
            ),
            "Cache-Control": "no-store",
            "X-Medix-Attachment-Count": str(len(items)),
            "X-Medix-Attachment-Missing": str(missing_count),
        },
    )


@app.post("/api/attachments/cleanup")
async def cleanup_attachment_orphans(request: AttachmentCleanupRequest):
    """清理附件目录中没有数据库登记的孤儿文件。"""
    from time import time

    from memory import get_session_store

    if not request.confirm:
        raise HTTPException(status_code=400, detail="请确认清理孤儿文件。")
    store = get_session_store()
    items = await asyncio.to_thread(store.list_all_attachments)
    db_names = {
        os.path.normcase(str(item.get("stored_name")))
        for item in items
        if item.get("stored_name")
    }
    files = await asyncio.to_thread(_attachment_dir_files)
    orphan_names = [
        name for name in files
        if os.path.normcase(name) not in db_names
    ]
    if request.expected_orphans != len(orphan_names):
        raise HTTPException(
            status_code=409,
            detail=(
                "孤儿文件数量已变化（当前 "
                f"{len(orphan_names)} 个），请重新检查后再清理。"
            ),
        )

    def _remove(names: List[str]) -> tuple:
        base = ATTACHMENT_DIR.resolve()
        removed = 0
        failed = 0
        skipped = 0
        if not base.is_dir():
            return removed, failed, skipped
        now = time()
        for name in names:
            path = base / name
            if not path.is_file():
                skipped += 1
                continue
            try:
                modified = path.stat().st_mtime
            except OSError:
                failed += 1
                continue
            # 刚写入但尚未登记的文件可能处于上传过程中，保留 10 分钟
            if now - modified < 600:
                skipped += 1
                continue
            # 删除前再确认一次，避免检查后完成的登记被误删
            fresh_names = {
                os.path.normcase(stored)
                for stored in store.list_attachment_stored_names()
            }
            if os.path.normcase(name) in fresh_names:
                continue
            try:
                path.unlink()
            except OSError:
                failed += 1
            else:
                removed += 1
        return removed, failed, skipped

    removed, failed, skipped = await asyncio.to_thread(
        _remove, orphan_names
    )
    await record_audit(
        "attachment.cleanup",
        detail=f"removed={removed} failed={failed} skipped={skipped}",
    )
    return {
        "ok": True,
        "removed": removed,
        "failed": failed,
        "skipped": skipped,
    }


@app.get("/api/health/emergency-card")
async def health_emergency_card(profile: str = ""):
    """急救信息卡：单页可打印 HTML，汇总血型、过敏、用药、慢病与联系人。"""
    import html as html_module

    from memory import get_session_store

    profile_key = _normalized_profile_key(profile)
    if not profile_key:
        raise HTTPException(status_code=400, detail="请先选择患者档案")
    store = get_session_store()
    profiles = await asyncio.to_thread(store.list_profiles)
    context: Dict[str, Any] = {}
    profile_found = False
    for item in profiles:
        if item.get("name") == profile_key:
            context = item.get("context") or {}
            profile_found = True
            break
    allergies, conditions, medications = await asyncio.gather(
        asyncio.to_thread(
            store.list_allergies, profile_key, True, None, "", 1000
        ),
        asyncio.to_thread(
            store.list_conditions, profile_key, True, None, 1000
        ),
        asyncio.to_thread(
            store.list_medications, profile_key, True, 1000
        ),
    )

    escape = html_module.escape
    blood_type = str(context.get("blood_type") or "不详")[:10]
    meta_parts = [f"档案：{profile_key}"]
    age = str(context.get("age") or "").strip()[:10]
    sex = str(context.get("sex") or "").strip()[:10]
    if age:
        meta_parts.append(f"{age} 岁")
    if sex:
        meta_parts.append(sex)
    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    profile_warning = ""
    if not profile_found:
        profile_warning = (
            '<div class="warning">未找到该档案的保存信息（可能已删除），'
            "血型与紧急联系人可能缺失，请核对后再使用。</div>"
        )

    severity_labels = {
        "mild": "轻度", "moderate": "中度", "severe": "重度",
    }
    allergy_lines = []
    for item in allergies:
        substance = str(item.get("substance") or "").strip()
        if not substance:
            continue
        severity = severity_labels.get(
            str(item.get("severity") or ""), ""
        )
        reaction = str(item.get("reaction") or "").strip()
        parts = [substance]
        if severity:
            parts.append(f"（{severity}）")
        if reaction:
            parts.append(f"：{reaction}")
        allergy_lines.append("".join(parts))

    anticoagulant_keywords = (
        "华法林", "阿司匹林", "氯吡格雷", "替格瑞洛", "利伐沙班",
        "阿哌沙班", "艾多沙班", "达比加群", "肝素", "依诺肝素",
        "warfarin", "aspirin", "clopidogrel", "rivaroxaban", "apixaban",
    )
    medication_lines = []
    medication_text = str(context.get("medications") or "").strip()
    if medication_text:
        suffix = ""
        if any(
            keyword.lower() in medication_text.lower()
            for keyword in anticoagulant_keywords
        ):
            suffix = "（含抗凝/抗血小板记录，注意出血风险）"
        medication_lines.append(f"自述：{medication_text}{suffix}")
    for item in medications:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        dose = str(item.get("dose") or "").strip()
        frequency = str(item.get("frequency") or "").strip()
        line = " ".join(part for part in (name, dose, frequency) if part)
        if any(
            keyword.lower() in name.lower()
            for keyword in anticoagulant_keywords
        ):
            line += "（抗凝/抗血小板，注意出血风险）"
        medication_lines.append(line)

    allergy_text = str(context.get("allergies") or "").strip()
    if allergy_text:
        allergy_lines.append(f"自述：{allergy_text}")

    condition_lines = []
    history_text = str(context.get("history") or "").strip()
    if history_text:
        condition_lines.append(f"自述：{history_text}")
    condition_lines.extend(
        str(item.get("name") or "").strip()
        for item in conditions
        if str(item.get("name") or "").strip()
    )

    contacts_raw = context.get("emergency_contacts") or []
    if isinstance(contacts_raw, str):
        contacts_source = contacts_raw.split("；")
    elif isinstance(contacts_raw, list):
        contacts_source = contacts_raw
    else:
        contacts_source = []
    contacts = [
        str(item).strip()[:60]
        for item in contacts_source
        if str(item).strip()
    ][:2]

    def section(title: str, lines: List[str], empty: str) -> str:
        if lines:
            body = "".join(
                f"<li>{escape(line)}</li>" for line in lines
            )
        else:
            body = f'<li class="empty">{escape(empty)}</li>'
        return (
            f"<section><h2>{escape(title)}</h2>"
            f"<ul>{body}</ul></section>"
        )

    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>急救信息卡 · {escape(profile_key)}</title>
<style>
  :root {{ --ink:#10202a; --muted:#5b6776; --rule:#d8dee6; --red:#b42318; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#f3f5f8; color:var(--ink);
         font-family:"Microsoft YaHei", system-ui, sans-serif; }}
  .card {{ max-width:720px; margin:24px auto; background:#fff;
          padding:28px 32px; border-radius:12px;
          border:1px solid var(--rule); }}
  header {{ display:flex; justify-content:space-between;
           align-items:flex-start; gap:16px; padding-bottom:12px;
           border-bottom:3px solid var(--red); }}
  h1 {{ margin:0; font-size:26px; }}
  .meta {{ margin:6px 0 0; color:var(--muted); font-size:13px; }}
  .blood {{ font-size:26px; font-weight:700; color:var(--red);
           text-align:center; line-height:1.2; white-space:nowrap; }}
  .blood small {{ display:block; font-size:12px; font-weight:400;
                 color:var(--muted); }}
  .emergency {{ margin:14px 0; padding:10px 14px; background:#fdf0ef;
               border:1px solid #f0cfc9; color:var(--red);
               font-weight:600; border-radius:8px; }}
  .warning {{ margin:0 0 14px; padding:10px 14px; background:#fff7e6;
             border:1px solid #f0d9a8; color:#8a5a00;
             border-radius:8px; font-size:13px; }}
  section {{ margin-top:16px; }}
  h2 {{ font-size:14px; margin:0 0 6px; color:var(--muted);
       letter-spacing:.08em; }}
  ul {{ margin:0; padding-left:18px; line-height:1.7; }}
  .empty {{ color:var(--muted); }}
  footer {{ margin-top:20px; border-top:1px solid var(--rule);
           padding-top:10px; font-size:12px; color:var(--muted);
           line-height:1.6; }}
  .print-hint {{ margin:0 auto 32px; text-align:center;
                font-size:12px; color:var(--muted); }}
  @media print {{
    body {{ background:#fff; }}
    .card {{ margin:0; border:none; max-width:none; }}
    .print-hint {{ display:none; }}
  }}
</style>
</head>
<body>
<main class="card">
  <header>
    <div>
      <h1>急救信息卡</h1>
      <p class="meta">{escape(" · ".join(meta_parts))} ·
        更新 {escape(generated)}</p>
    </div>
    <div class="blood"><small>血型</small>{escape(blood_type)}</div>
  </header>
  <div class="emergency">
    紧急情况请立即拨打 120。本卡仅整理本机健康记录，不构成诊断或用药建议。
  </div>
  {profile_warning}
  {section("紧急联系人", contacts, "未登记紧急联系人")}
  {section("过敏史", allergy_lines, "未登记活动过敏史")}
  {section("在用药物", medication_lines, "未登记在用药物")}
  {section("慢病与既往问题", condition_lines, "未登记活动慢病")}
  <footer>
    数据来源：MediX 本机档案「{escape(profile_key)}」，生成于
    {escape(generated)}。请定期核对血型、过敏、用药与联系人信息；
    就医时以医院记录和医生判断为准。
  </footer>
</main>
<p class="print-hint">可使用浏览器菜单打印或另存为 PDF。</p>
</body>
</html>"""
    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-store"},
    )


@app.get("/api/health/visit-prep")
async def health_visit_prep(
    profile: str = "", days: int = 30, since_last_visit: bool = False
):
    """复诊准备单：汇总近期异常、用药、症状并生成想问医生的问题。"""
    from collections import Counter
    from datetime import timedelta

    from memory import get_session_store
    from memory.session_store import (
        LOWER_BOUND_TARGET_METRICS,
        METRIC_TYPES,
        critical_metric_alerts,
        metric_context_label,
        metric_over_target,
    )

    profile_key = _normalized_profile_key(profile)
    if not profile_key:
        raise HTTPException(status_code=400, detail="请先选择患者档案")
    now = datetime.now()
    store = get_session_store()
    window_from_last_visit = False
    if since_last_visit:
        recent_visits = await asyncio.to_thread(
            store.list_visit_records, profile_key, 0, "", 1
        )
        if recent_visits:
            visit_date = str(
                recent_visits[0].get("visit_date") or ""
            )[:10]
            try:
                parsed_visit = datetime.strptime(
                    visit_date, "%Y-%m-%d"
                ).date()
            except ValueError:
                parsed_visit = None
            if parsed_visit is not None:
                days = max(
                    1,
                    min((now.date() - parsed_visit).days + 1, 180),
                )
                window_from_last_visit = True
    if not window_from_last_visit:
        try:
            days = max(1, min(int(days or 30), 180))
        except (TypeError, ValueError, OverflowError):
            days = 30
    start = (now - timedelta(days=days - 1)).strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    (
        raw_metrics, raw_labs, symptoms, medications, logs,
        allergies, conditions, reminders, visits, care_items, targets,
        attachments,
    ) = await asyncio.gather(
        asyncio.to_thread(
            store.list_metrics, profile_key, None, 100000, days
        ),
        asyncio.to_thread(
            store.list_lab_results, profile_key, None, None, days,
            100000, 0,
        ),
        asyncio.to_thread(
            store.list_symptoms, profile_key, 100000, days
        ),
        asyncio.to_thread(
            store.list_medications, profile_key, True, 1000, 0, days
        ),
        asyncio.to_thread(
            store.list_medication_logs, None, profile_key, 100000, 0, days
        ),
        asyncio.to_thread(
            store.list_allergies, profile_key, True, None, "", 1000
        ),
        asyncio.to_thread(
            store.list_conditions, profile_key, True, None, 1000
        ),
        asyncio.to_thread(store.list_reminders, False, profile_key),
        asyncio.to_thread(store.list_visit_records, profile_key, 0, "", 5),
        asyncio.to_thread(
            store.list_care_items, profile_key, True, False, 1000
        ),
        asyncio.to_thread(store.list_metric_targets, profile_key),
        asyncio.to_thread(
            store.list_attachments, profile_key, None, None, 100, 0
        ),
    )
    attachment_stats = await asyncio.to_thread(
        store.attachment_kind_stats, profile_key
    )

    # “目前用药”只统计仍在用的药物；停用药物在窗口内的旧打卡
    # 不应让准备单出现没有对应药物条目的依从数字
    active_medication_ids = {
        item.get("id") for item in medications if item.get("id") is not None
    }
    logs = [
        item for item in logs
        if item.get("medication_id") in active_medication_ids
    ]

    def format_metric(item: Dict[str, Any]) -> str:
        try:
            first = float(item.get("value1"))
        except (TypeError, ValueError, OverflowError):
            return ""
        second = item.get("value2")
        if item.get("metric") == "bp" and second not in (None, ""):
            try:
                return f"{first:g}/{float(second):g}"
            except (TypeError, ValueError, OverflowError):
                return f"{first:g}"
        return f"{first:g}"

    target_map = {
        item["metric"]: item for item in targets if item.get("metric")
    }
    latest_metrics: Dict[str, Dict[str, Any]] = {}
    critical_metrics: Dict[str, Dict[str, Any]] = {}
    for item in raw_metrics:
        metric = str(item.get("metric") or "")
        if (
            not metric
            or str(item.get("measured_at") or "")[:10] > end
        ):
            continue
        if metric not in latest_metrics:
            latest_metrics[metric] = item
        if metric not in critical_metrics and critical_metric_alerts(
            metric, item.get("value1"), item.get("value2")
        ):
            critical_metrics[metric] = item
    abnormal_metrics = []
    for metric, latest in latest_metrics.items():
        # 最新一次恢复正常也不能漏掉窗口内更早的危急值
        item = critical_metrics.get(metric, latest)
        target = target_map.get(metric)
        over_target = False
        if target is not None:
            try:
                over_target = metric_over_target(
                    metric, item.get("value1"), item.get("value2"), target
                )
            except (TypeError, ValueError, OverflowError):
                over_target = False
        alerts = critical_metric_alerts(
            metric, item.get("value1"), item.get("value2")
        )
        if not over_target and not alerts:
            continue
        label, unit, _ = METRIC_TYPES.get(metric, (metric, "", (0, 0)))
        abnormal_metrics.append({
            "metric": metric,
            "label": label,
            "value": format_metric(item),
            "unit": unit,
            "measured_at": str(item.get("measured_at") or "")[:16],
            "context": metric_context_label(
                metric, item.get("context")
            ),
            "over_target": over_target,
            "target_direction": (
                "low" if metric in LOWER_BOUND_TARGET_METRICS else "high"
            ),
            "critical": bool(alerts),
            "notes": [alert.get("message") for alert in alerts][:3],
        })
    abnormal_metrics.sort(
        key=lambda item: str(item.get("measured_at") or ""), reverse=True
    )
    abnormal_metrics.sort(key=lambda item: 0 if item["critical"] else 1)

    abnormal_labs = [
        item for item in raw_labs
        if str(item.get("tested_at") or "")[:10] <= end
        and (item.get("abnormal") or item.get("critical"))
    ]
    abnormal_labs.sort(
        key=lambda item: str(item.get("tested_at") or ""), reverse=True
    )
    abnormal_labs.sort(key=lambda item: 0 if item.get("critical") else 1)
    lab_rows = []
    for item in abnormal_labs[:10]:
        try:
            value_text = f"{float(item.get('value')):g}"
        except (TypeError, ValueError, OverflowError):
            value_text = str(item.get("value") or "")
        lab_rows.append({
            "item": str(item.get("item") or ""),
            "panel": str(item.get("panel") or ""),
            "value": value_text,
            "unit": str(item.get("unit") or ""),
            "ref_text": str(item.get("ref_text") or ""),
            "tested_at": str(item.get("tested_at") or "")[:16],
            "abnormal_high": bool(item.get("abnormal_high")),
            "abnormal_low": bool(item.get("abnormal_low")),
            "critical": bool(item.get("critical")),
            "notes": [
                alert.get("message")
                for alert in (item.get("critical_alerts") or [])
            ][:3],
        })

    symptom_buckets: Dict[str, Dict[str, Any]] = {}
    for item in symptoms:
        name = str(item.get("symptom") or "").strip()
        if not name:
            continue
        try:
            severity = int(item.get("severity") or 0)
        except (TypeError, ValueError, OverflowError):
            severity = 0
        if severity <= 0:
            continue
        bucket = symptom_buckets.setdefault(name, {
            "symptom": name,
            "count": 0,
            "max_severity": 0,
            "last_at": "",
        })
        bucket["count"] += 1
        bucket["max_severity"] = max(bucket["max_severity"], severity)
        bucket["last_at"] = max(
            bucket["last_at"], str(item.get("recorded_at") or "")[:16]
        )
    top_symptoms = sorted(
        symptom_buckets.values(),
        key=lambda item: (
            -int(item["max_severity"]), -int(item["count"]), item["symptom"]
        ),
    )[:5]

    skip_logs = [item for item in logs if item.get("skipped")]
    reason_counter = Counter(
        str(item.get("skip_reason_label") or "未说明") for item in skip_logs
    )
    skip_reasons = [
        {"reason": reason, "count": count}
        for reason, count in reason_counter.most_common(3)
    ]
    taken_count = len(logs) - len(skip_logs)

    latest_visit = visits[0] if visits else None
    last_visit = None
    if latest_visit:
        last_visit = {
            "visit_date": str(latest_visit.get("visit_date") or "")[:10],
            "department": str(latest_visit.get("department") or ""),
            "doctor": str(latest_visit.get("doctor") or ""),
            "diagnosis": str(latest_visit.get("diagnosis") or ""),
            "advice": str(latest_visit.get("advice") or "")[:300],
            "next_followup_date": str(
                latest_visit.get("next_followup_date") or ""
            )[:10],
        }

    severity_labels = {"mild": "轻度", "moderate": "中度", "severe": "重度"}
    kind_labels = {
        "drug": "药物", "food": "食物",
        "environment": "环境", "other": "其他",
    }
    status_labels = {
        "active": "活动", "controlled": "已控制", "resolved": "已痊愈",
    }
    allergy_rows = [
        {
            "substance": str(item.get("substance") or ""),
            "kind": kind_labels.get(
                str(item.get("kind") or ""), str(item.get("kind") or "")
            ),
            "severity": severity_labels.get(
                str(item.get("severity") or ""),
                str(item.get("severity") or ""),
            ),
            "reaction": str(item.get("reaction") or "")[:120],
        }
        for item in allergies[:8]
    ]
    condition_rows = [
        {
            "name": str(item.get("name") or ""),
            "status": status_labels.get(
                str(item.get("status") or ""),
                str(item.get("status") or ""),
            ),
            "severity": severity_labels.get(
                str(item.get("severity") or ""),
                str(item.get("severity") or ""),
            ),
        }
        for item in conditions[:10]
    ]

    today = end
    reminder_rows = []
    overdue_reminders = 0
    overdue_medication_reminders = 0
    for item in reminders:
        due = str(item.get("due_at") or "")
        overdue = due[:10] < today
        if overdue:
            overdue_reminders += 1
            if item.get("medication_id"):
                overdue_medication_reminders += 1
        if len(reminder_rows) < 5:
            reminder_rows.append({
                "title": str(item.get("title") or "")[:120],
                "due_at": due[:16],
                "overdue": overdue,
            })

    care_due = [
        {
            "title": str(item.get("title") or ""),
            "next_due_at": str(item.get("next_due_at") or "")[:10],
            "days_until": item.get("days_until"),
            "overdue": bool(item.get("overdue")),
        }
        for item in care_items
        if item.get("overdue") or item.get("due_today") or item.get("due_soon")
    ][:5]

    attachment_count = sum(
        int(item.get("count") or 0) for item in attachment_stats
    )
    visit_ref_map = {
        int(row["id"]): row for row in visits if row.get("id")
    }
    lab_ref_map = {
        int(row["id"]): row for row in raw_labs if row.get("id")
    }
    attachment_rows = []
    for item in attachments[:10]:
        ref_id = int(item.get("ref_id") or 0)
        ref_label = ""
        if item.get("kind") == "visit" and ref_id in visit_ref_map:
            visit_ref = visit_ref_map[ref_id]
            ref_label = " · ".join(part for part in (
                str(visit_ref.get("visit_date") or "")[:10],
                visit_ref.get("department") or "",
                visit_ref.get("diagnosis") or "",
            ) if part)
        elif item.get("kind") == "lab" and ref_id in lab_ref_map:
            lab_ref = lab_ref_map[ref_id]
            try:
                value = f"{float(lab_ref.get('value')):g}"
            except (TypeError, ValueError, OverflowError):
                value = str(lab_ref.get("value") or "")
            result = f"{lab_ref.get('item') or '化验'} {value}".strip()
            if lab_ref.get("unit"):
                result += f" {lab_ref['unit']}"
            ref_label = " · ".join(part for part in (
                str(lab_ref.get("tested_at") or "")[:10],
                result,
                lab_ref.get("panel") or "",
            ) if part)
        attachment_rows.append({
            "name": str(item.get("original_name") or "附件")[:120],
            "kind": str(item.get("kind_label") or ""),
            "created_at": str(item.get("created_at") or "")[:10],
            "linked": bool(item.get("ref_id")),
            "ref_label": ref_label[:100],
        })

    active_medications = [
        {
            "name": str(item.get("name") or ""),
            "dose": str(item.get("dose") or ""),
            "frequency": str(item.get("frequency") or ""),
        }
        for item in medications[:20]
    ]

    questions: List[str] = []
    critical_labs = [item for item in lab_rows if item["critical"]]
    other_labs = [item for item in lab_rows if not item["critical"]]
    if critical_labs:
        first = critical_labs[0]
        questions.append(
            f"化验「{first['item']}」达到危急值（{first['value']}"
            f"{first['unit']}，{first['tested_at'][:10]}），"
            "是否需要立即处理或复查？"
        )
    if other_labs:
        critical_names = {item["item"] for item in critical_labs}
        names: List[str] = []
        for item in other_labs:
            name = item["item"]
            if name and name not in names and name not in critical_names:
                names.append(name)
        if names:
            questions.append(
                f"化验 {'、'.join(names[:3])} 异常，"
                "是否需要复查或调整治疗？"
            )
    critical_metrics = [
        item for item in abnormal_metrics if item["critical"]
    ]
    over_target_metrics = [
        item for item in abnormal_metrics
        if item["over_target"] and not item["critical"]
    ]
    if critical_metrics:
        first = critical_metrics[0]
        questions.append(
            f"{first['label']} {first['measured_at'][:10]} 为 "
            f"{first['value']} {first['unit']}（危急值），"
            "需要立即就医还是调整用药？"
        )
    if over_target_metrics:
        parts = []
        for item in over_target_metrics[:3]:
            direction = (
                "低于目标"
                if item.get("target_direction") == "low"
                else "超出目标"
            )
            parts.append(f"{item['label']} {item['value']} {direction}")
        questions.append(
            "、".join(parts) + "，是否需要调整方案？"
        )
    if skip_logs:
        reason_text = (
            f"，主要原因：{skip_reasons[0]['reason']}"
            if skip_reasons else ""
        )
        questions.append(
            f"近 {days} 天有 {len(skip_logs)} 次未服药{reason_text}，"
            "是否需要调整服药方式？"
        )
    if top_symptoms:
        first = top_symptoms[0]
        symptom_prefix = (
            f"近 {days} 天反复出现{first['symptom']}"
            if first["count"] > 1
            else f"近 {days} 天出现{first['symptom']}"
        )
        questions.append(
            f"{symptom_prefix}"
            f"（{first['count']} 次，最高 {first['max_severity']}/10），"
            "是否需要进一步检查？"
        )
    if allergy_rows:
        names = "、".join(item["substance"] for item in allergy_rows[:3])
        kinds = {item["kind"] for item in allergy_rows}
        if kinds == {"药物"}:
            suffix = "用药时需要避开吗？"
        elif kinds == {"食物"}:
            suffix = "饮食上需要避开吗？"
        elif kinds == {"环境"}:
            suffix = "日常生活中需要避开吗？"
        else:
            suffix = "需要如何避开？"
        questions.append(f"目前记录对 {names} 过敏，{suffix}")
    planning_parts: List[str] = []
    if overdue_reminders:
        if overdue_medication_reminders == overdue_reminders:
            planning_parts.append(
                f"{overdue_reminders} 项用药提醒已逾期"
            )
        elif overdue_medication_reminders:
            planning_parts.append(
                f"{overdue_reminders} 项提醒已逾期（含用药提醒）"
            )
        else:
            planning_parts.append(
                f"{overdue_reminders} 项待办提醒已逾期"
            )
    if care_due:
        names = "、".join(
            f"{item['title']}（{item['next_due_at']}）"
            for item in care_due[:3]
        )
        planning_parts.append(f"复查计划 {names} 已临近或逾期")
    if last_visit and last_visit["next_followup_date"]:
        planning_parts.append(
            f"上次就诊建议 {last_visit['next_followup_date']} 复诊"
        )
    if planning_parts:
        questions.append(
            "；".join(planning_parts) + "，本次就诊需要一并安排吗？"
        )
    if not questions:
        questions.append(
            "近期记录较少，请向医生补充描述症状、用药与检查结果。"
        )
    questions = questions[:8]

    lines = [f"复诊准备单（{profile_key} · {start} 至 {end}）", ""]
    if last_visit:
        visit_text = " ".join(part for part in (
            last_visit["visit_date"],
            last_visit["department"],
            last_visit["doctor"],
        ) if part)
        lines.append(f"上次就诊：{visit_text or '已记录'}")
        if last_visit["diagnosis"]:
            lines.append(f"诊断：{last_visit['diagnosis']}")
        if last_visit["advice"]:
            lines.append(f"医嘱：{last_visit['advice']}")
    if abnormal_metrics or lab_rows:
        lines.append("")
        lines.append("一、需要告知医生的异常")
        for item in abnormal_metrics[:8]:
            context = f"（{item['context']}）" if item["context"] else ""
            if item["critical"]:
                flag = "危急值"
            elif item.get("target_direction") == "low":
                flag = "低于目标"
            else:
                flag = "超标"
            lines.append(
                f"- {item['label']} {item['value']} {item['unit']}"
                f" · {item['measured_at'][:10]}{context} · {flag}"
            )
        for item in lab_rows:
            flag = "危急值" if item["critical"] else (
                "偏高" if item["abnormal_high"] else "偏低"
            )
            ref = f"，参考 {item['ref_text']}" if item["ref_text"] else ""
            lines.append(
                f"- 化验 {item['item']} {item['value']} {item['unit']}"
                f" · {item['tested_at'][:10]}{ref} · {flag}"
            )
    if top_symptoms:
        lines.append("")
        lines.append(f"二、近 {days} 天症状")
        for item in top_symptoms:
            lines.append(
                f"- {item['symptom']}：{item['count']} 次，"
                f"最高 {item['max_severity']}/10，最近 {item['last_at'][:10]}"
            )
    if active_medications or logs:
        lines.append("")
        lines.append(f"三、目前用药（{len(medications)} 种）")
        for item in active_medications:
            detail = " ".join(part for part in (
                item["dose"], item["frequency"]
            ) if part)
            lines.append(
                f"- {item['name']}{f'（{detail}）' if detail else ''}"
            )
        lines.append(
            f"- 近 {days} 天打卡：已服 {taken_count} 次，"
            f"未服 {len(skip_logs)} 次"
        )
        if skip_reasons:
            lines.append(
                "- 未服原因："
                + "、".join(
                    f"{item['reason']} {item['count']} 次"
                    for item in skip_reasons
                )
            )
    if allergy_rows or condition_rows:
        lines.append("")
        lines.append("四、过敏与慢病")
        for item in allergy_rows:
            reaction = f"，反应：{item['reaction']}" if item["reaction"] else ""
            lines.append(
                f"- 过敏：{item['substance']}（{item['kind']}，"
                f"{item['severity']}{reaction}）"
            )
        for item in condition_rows:
            lines.append(
                f"- 慢病：{item['name']}（{item['status']}）"
            )
    if reminder_rows or care_due:
        lines.append("")
        lines.append("五、待办与复查")
        for item in reminder_rows:
            flag = "已逾期" if item["overdue"] else "待完成"
            lines.append(f"- 提醒：{item['title']}（{item['due_at'][:10]}，{flag}）")
        for item in care_due:
            flag = "已逾期" if item["overdue"] else "临近"
            lines.append(
                f"- 复查：{item['title']}（{item['next_due_at']}，{flag}）"
            )
    if attachment_rows:
        lines.append("")
        lines.append(f"六、携带资料（{attachment_count} 份）")
        for item in attachment_rows:
            linked = "，已关联" if item["linked"] else ""
            ref_text = (
                f" · {item['ref_label']}" if item.get("ref_label") else ""
            )
            lines.append(
                f"- {item['name']}（{item['kind']}{linked}{ref_text}）"
            )
        omitted = attachment_count - len(attachment_rows)
        if omitted > 0:
            lines.append(f"- 另有 {omitted} 份未列出")
    lines.append("")
    lines.append("建议询问医生：")
    for index, question in enumerate(questions, 1):
        lines.append(f"{index}. {question}")
    lines.append("")
    lines.append(
        "本准备单由 MediX 根据本机健康记录生成，"
        "仅供与医生沟通参考，不构成医疗建议。"
    )

    return {
        "profile": profile_key,
        "start": start,
        "end": end,
        "days": days,
        "since_last_visit": window_from_last_visit,
        "last_visit": last_visit,
        "abnormal_metrics": abnormal_metrics[:8],
        "abnormal_labs": lab_rows,
        "symptoms": top_symptoms,
        "medications": active_medications,
        "medication_summary": {
            "active_count": len(medications),
            "taken_count": taken_count,
            "skipped_count": len(skip_logs),
            "skip_reasons": skip_reasons,
        },
        "allergies": allergy_rows,
        "conditions": condition_rows,
        "reminders": {
            "pending_count": len(reminders),
            "overdue_count": overdue_reminders,
            "items": reminder_rows,
        },
        "care_items": care_due,
        "attachments": attachment_rows,
        "attachment_count": attachment_count,
        "questions": questions,
        "text": "\n".join(lines),
    }


@app.get("/api/health/visit-prep/print")
async def health_visit_prep_print(
    profile: str = "", days: int = 30, since_last_visit: bool = False
):
    """复诊准备单打印版：单页 HTML，可另存为 PDF。"""
    import html as html_module

    data = await health_visit_prep(
        profile=profile, days=days,
        since_last_visit=since_last_visit,
    )
    escape = html_module.escape
    profile_name = escape(str(data.get("profile") or ""))
    range_text = escape(
        f"{data.get('start') or ''} 至 {data.get('end') or ''}"
    )
    generated = escape(datetime.now().strftime("%Y-%m-%d %H:%M"))
    title = escape(f"复诊准备单 · {data.get('profile') or ''}")
    body_text = escape(str(data.get("text") or ""))
    html_content = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
  :root {{ --ink:#1f2933; --muted:#5b6770; --rule:#d7dde2;
          --accent:#1f6feb; }}
  * {{ box-sizing:border-box; }}
  body {{ margin:0; background:#f5f7f9; color:var(--ink);
          font-family: -apple-system, BlinkMacSystemFont, "Segoe UI",
          "PingFang SC", "Microsoft YaHei", sans-serif; }}
  .sheet {{ max-width:820px; margin:24px auto; background:#fff;
            padding:32px 36px; border:1px solid var(--rule);
            border-radius:12px; }}
  header {{ display:flex; justify-content:space-between;
            align-items:flex-start; gap:16px; padding-bottom:12px;
            border-bottom:2px solid var(--accent); }}
  h1 {{ margin:0; font-size:22px; }}
  .meta {{ margin:6px 0 0; color:var(--muted); font-size:13px; }}
  pre {{ white-space:pre-wrap; overflow-wrap:anywhere;
         font-family:inherit; font-size:14px; line-height:1.75;
         margin:18px 0 0; }}
  footer {{ margin-top:20px; border-top:1px solid var(--rule);
            padding-top:10px; font-size:12px; color:var(--muted);
            line-height:1.6; }}
  .print-hint {{ margin:0 auto 32px; text-align:center;
                 font-size:12px; color:var(--muted); }}
  @media print {{
    body {{ background:#fff; }}
    .sheet {{ margin:0; border:none; max-width:none; padding:0; }}
    .print-hint {{ display:none; }}
  }}
  @page {{ margin:16mm; }}
</style>
</head>
<body>
<main class="sheet">
  <header>
    <div>
      <h1>复诊准备单</h1>
      <p class="meta">档案：{profile_name} · {range_text}</p>
    </div>
  </header>
  <pre>{body_text}</pre>
  <footer>
    本准备单由 MediX 根据本机健康记录生成于 {generated}，
    仅供与医生沟通参考，不构成医疗建议。
  </footer>
</main>
<p class="print-hint">可使用浏览器菜单打印或另存为 PDF。</p>
</body>
</html>"""
    return HTMLResponse(
        content=html_content,
        headers={"Cache-Control": "no-store"},
    )


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
        metric_context_label,
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
    targets = await asyncio.to_thread(
        store.list_metric_targets, profile_key
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
    vaccinations = await asyncio.to_thread(
        store.list_vaccinations, profile_key, 100000
    )
    appointments = await asyncio.to_thread(
        store.list_appointments, profile_key, None, False, 100000, 0
    )
    lab_results = await asyncio.to_thread(
        store.list_lab_results, profile_key, None, None, days, 300, 0
    )
    allergies = await asyncio.to_thread(
        store.list_allergies, profile_key, False, None, "", 1000
    )
    condition_items = await asyncio.to_thread(
        store.list_conditions, profile_key, False, None, 1000
    )
    care_items = await asyncio.to_thread(
        store.list_care_items, profile_key, False, False, 1000
    )
    visit_records = await asyncio.to_thread(
        store.list_visit_records, profile_key, 0, "", 1000
    )
    attachments = await asyncio.to_thread(
        store.list_attachments, profile_key, None, None, 1000, 0
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
        store.list_sessions_for_timeline, profile_key, cutoff, "", 100000, True
    )
    streak = _streak_info(
        await asyncio.to_thread(store.recorded_days, profile_key, 3650)
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

    report = await _weekly_report_impl(
        profile=profile_key, days=days, max_days=365,
        include_previous=False,
    )
    safety = await _medication_safety_for_profile(store, profile_key)
    insights = [
        item for item in _build_health_insights(report, safety=safety)
        if item["level"] != "ok"
    ]
    lines.append("## 健康提示")
    if insights:
        level_labels = {
            "danger": "危急",
            "warning": "需关注",
            "info": "提示",
        }
        for item in insights:
            lines.append(
                f"- [{level_labels.get(item['level'], item['level'])}] "
                f"{item['title']}"
                + (f"：{item['detail']}" if item["detail"] else "")
            )
    else:
        lines.append(
            f"- 近 {report.get('days')} 天暂未发现需要特别关注的问题。"
        )
    lines.append("")

    lines.append("## 过敏史")
    if allergies:
        severity_labels = {
            "mild": "轻度",
            "moderate": "中度",
            "severe": "重度",
        }
        kind_labels = {
            "drug": "药物",
            "food": "食物",
            "environment": "环境",
            "other": "其他",
        }
        for item in allergies:
            detail = f"- {item.get('substance') or '过敏原'}"
            extras = [
                item.get("reaction") or "",
                severity_labels.get(item.get("severity") or "", ""),
                kind_labels.get(item.get("kind") or "", ""),
            ]
            extras = [part for part in extras if part]
            if extras:
                detail += f"（{'，'.join(extras)}）"
            if not item.get("active"):
                detail += "（已停用）"
            if item.get("noted_at"):
                detail += f"，发现于 {item['noted_at']}"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂未记录过敏史")
    lines.append("")

    lines.append("## 慢病清单")
    if condition_items:
        status_labels = {
            "active": "管理中",
            "controlled": "已控制",
            "resolved": "已痊愈",
        }
        severity_labels = {
            "mild": "轻度",
            "moderate": "中度",
            "severe": "重度",
        }
        for item in condition_items:
            detail = f"- {item.get('name') or '健康问题'}"
            extras = []
            if item.get("icd10"):
                extras.append(str(item["icd10"]))
            if item.get("diagnosed_at"):
                extras.append(f"确诊 {item['diagnosed_at']}")
            status_label = status_labels.get(item.get("status") or "", "")
            if status_label:
                extras.append(status_label)
            severity_label = severity_labels.get(
                item.get("severity") or "", ""
            )
            if severity_label:
                extras.append(severity_label)
            if extras:
                detail += f"（{'，'.join(extras)}）"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂未记录慢病/健康问题")
    lines.append("")

    lines.append("## 复查计划")
    if care_items:
        for item in care_items:
            status = "启用" if item.get("active") else "停用"
            detail = (
                f"- {item.get('title') or '复查'}"
                f"（每 {int(item.get('interval_days') or 0)} 天"
                f"，下次 {item.get('next_due_at') or '—'}"
                f"，{status}）"
            )
            if item.get("last_done_at"):
                detail += f"，上次 {item['last_done_at']}"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂未设置复查计划")
    lines.append("")

    lines.append("## 近期就诊")
    if visit_records:
        for item in visit_records:
            detail = (
                f"- {item.get('visit_date') or ''} "
                f"{item.get('diagnosis') or '就诊'}"
            )
            extras = [
                item.get("department") or "",
                item.get("doctor") or "",
                item.get("facility") or "",
            ]
            extras = [part for part in extras if part]
            if extras:
                detail += f"（{' · '.join(extras)}）"
            if item.get("advice"):
                detail += f"；医嘱：{item['advice']}"
            if item.get("prescription"):
                detail += f"；处方：{item['prescription']}"
            if item.get("next_followup_date"):
                detail += f"；下次复诊 {item['next_followup_date']}"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂未记录就诊记录")
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
            context_label = metric_context_label(
                metric, item.get("context")
            )
            if context_label:
                details.append(context_label)
            if target is not None and metric_over_target(
                metric, item["value1"], item.get("value2"), target
            ):
                details.append(
                    "低于目标"
                    if metric in LOWER_BOUND_TARGET_METRICS
                    else "超标"
                )
            from memory.session_store import critical_metric_alerts

            if critical_metric_alerts(
                metric, item["value1"], item.get("value2")
            ):
                details.append("危急值")
            details = [part for part in details if part]
            suffix = f"（{'，'.join(details)}）" if details else ""
            lines.append(f"- {label}：{value} {unit}{suffix}")
    else:
        lines.append("- 暂无健康指标记录")
    lines.append("")

    lines.append("## 最近化验")
    if lab_results:
        if len(lab_results) >= 300:
            lines.append(
                f"- 化验记录较多，仅显示近 {days} 天内最近 300 条"
            )
        for item in lab_results:
            value = f"{float(item.get('value') or 0):g}"
            unit = str(item.get("unit") or "")
            if unit:
                value += f" {unit}"
            suffix = ""
            if item.get("abnormal_low"):
                suffix = "（偏低）"
            elif item.get("abnormal_high"):
                suffix = "（偏高）"
            if item.get("critical"):
                alert = (item.get("critical_alerts") or [{}])[0]
                suffix += "（危急值）"
                if alert.get("stale"):
                    suffix += "（结果较旧）"
            ref_text = str(item.get("ref_text") or "")
            details = [
                str(item.get("tested_at") or "").replace("T", " ")[:16],
            ]
            if ref_text:
                details.append(f"参考 {ref_text}")
            if item.get("lab_name"):
                details.append(str(item["lab_name"]))
            detail = f"- {item.get('item') or '化验'}：{value}{suffix}"
            detail += f"（{'，'.join(part for part in details if part)}）"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂未记录化验结果")
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
                if int(item.get("skipped_range") or 0):
                    detail += (
                        f"，未服 {int(item['skipped_range'])} 次"
                    )
                expected = item.get("expected_week_doses")
                if expected:
                    rate = adherence_rate(item.get("week_count"), expected)
                    detail += f"，依从率 {rate}%"
            if item.get("stock") is not None:
                unit = item.get("stock_unit") or ""
                detail += f"；库存 {item['stock']:g}{unit}"
                if item.get("out_of_stock"):
                    detail += "（缺货）"
                elif item.get("low_stock"):
                    detail += "（偏低）"
            if item.get("expiry_date"):
                detail += f"；有效期 {item['expiry_date']}"
                if item.get("expired"):
                    detail += "（已过期）"
                elif item.get("expiring_soon"):
                    detail += "（即将过期）"
            if item.get("started_at"):
                detail += f"；开始 {item['started_at']}"
            if item.get("stopped_at"):
                detail += f"；停药 {item['stopped_at']}"
                if item.get("stop_reason"):
                    detail += f"（{item['stop_reason']}）"
            detail += "）"
            lines.append(detail)
    else:
        lines.append("- 暂无用药记录")
    lines.append("")

    lines.append("## 疫苗接种")
    if vaccinations:
        for item in vaccinations:
            detail = (
                f"- {str(item.get('vaccinated_at') or '')[:10]} "
                f"{item.get('vaccine_name') or '疫苗'} "
                f"第{int(item.get('dose_number') or 1)}剂"
            )
            if item.get("next_due"):
                detail += f"，下一剂 {item['next_due']}"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂无疫苗接种记录")
    lines.append("")

    lines.append("## 就诊预约")
    if appointments:
        status_labels = {
            "upcoming": "待就诊",
            "completed": "已完成",
            "cancelled": "已取消",
        }
        for item in appointments:
            detail = (
                f"- {str(item.get('scheduled_at') or '').replace('T', ' ')[:16]} "
                f"{item.get('title') or '就诊'}"
                f"（{status_labels.get(item.get('status') or '', '')}"
            )
            extras = [
                item.get("department") or "",
                item.get("doctor") or "",
                item.get("location") or "",
            ]
            extras = [part for part in extras if part]
            if extras:
                detail += "，" + " · ".join(extras)
            detail += "）"
            if item.get("note"):
                detail += f"（{item['note']}）"
            lines.append(detail)
    else:
        lines.append("- 暂无就诊预约")
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

    lines.append("## 就医资料")
    if attachments:
        for item in attachments[:100]:
            linked = "已关联" if item.get("ref_id") else "未关联"
            note = str(item.get("note") or "").strip()
            note_text = f"，{note}" if note else ""
            lines.append(
                f"- {str(item.get('created_at') or '')[:10]} "
                f"{item.get('original_name') or '附件'}"
                f"（{item.get('kind_label') or '其他'}，{linked}{note_text}）"
            )
    else:
        lines.append("- 暂无就医资料")
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
        await asyncio.to_thread(
            store.list_sessions_for_timeline, "", "", "", 1
        ),
        await asyncio.to_thread(store.list_reminders, False, ""),
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
            request.vaccination_id,
            request.appointment_id,
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
        "关联会话ID", "ID",
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
            csv_safe_cell(item.get("session_id", "")),
            int(item.get("id") or 0) or "",
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
    from datetime import timedelta, time, timezone

    from memory import get_session_store

    items = await asyncio.to_thread(
        get_session_store().list_reminders, include_done, profile
    )

    def parse_repeat_until(value: Any) -> Optional[datetime]:
        text = str(value or "").strip()
        if not text:
            return None
        try:
            # 与 complete_reminder_with_repeat 一致：解析完整值并转成本地
            # naive 时间；截断到 19 位会丢掉 Z/时区偏移
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            if len(text) == 10:
                parsed = datetime.combine(parsed.date(), time(23, 59, 59))
            return parsed
        except (TypeError, ValueError):
            return None

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
            until_text = str(item.get("repeat_until") or "").strip()
            until_dt = parse_repeat_until(until_text)
            next_start = start + timedelta(days=repeat_days)
            # 第一次顺延已晚于截止时间时不应导出 RRULE，避免日历里出现
            # 应用自身不会创建的幽灵日程
            if until_dt is None or next_start <= until_dt:
                rule = f"RRULE:FREQ=DAILY;INTERVAL={repeat_days}"
                if until_text:
                    if timed and "T" in until_text[:19] and until_dt is not None:
                        rule += f";UNTIL={until_dt.strftime('%Y%m%dT%H%M%S')}"
                    else:
                        until_date = until_text[:10].replace("-", "")
                        rule += (
                            f";UNTIL={until_date}T235959"
                            if timed else f";UNTIL={until_date}"
                        )
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
        "关联会话ID": "session_id",
        "ID": "id",
    }

    try:
        csv_rows = list(reader)
    except csv.Error as e:
        logger.warning(f"提醒 CSV 解析失败: {e}")
        raise HTTPException(status_code=400, detail="CSV 格式错误，请检查文件内容。")

    records = []
    invalid = 0
    for row in csv_rows:
        mapped = {}
        for key, value in row.items():
            if not key:
                continue
            name = key.strip()
            mapped[field_alias.get(name, name)] = csv_restore_cell(value).strip()

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
        try:
            reminder_id = max(0, int(mapped.get("id") or 0))
        except (TypeError, ValueError):
            reminder_id = 0
        records.append({
            "id": reminder_id,
            "title": title,
            "due_at": due_at,
            "session_id": mapped.get("session_id", ""),
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

    try:
        result = await asyncio.to_thread(
            get_session_store().import_data, {"reminders": records}
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    imported = int(result.get("reminders", 0))
    store_invalid = int(result.get("invalid", 0))
    invalid += store_invalid
    await record_audit(
        "reminder.import",
        detail=f"imported={imported} invalid={invalid}",
    )
    return {
        "ok": True,
        "imported": imported,
        "skipped": max(0, len(records) - imported - store_invalid),
        "invalid": invalid,
    }


@app.post("/api/reminders/{reminder_id}/complete")
async def complete_reminder(reminder_id: SafeIntPath):
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
async def reopen_reminder(reminder_id: SafeIntPath):
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
async def update_reminder(reminder_id: SafeIntPath, request: ReminderUpdateRequest):
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
async def snooze_reminder(reminder_id: SafeIntPath, days: int = 7):
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
async def delete_reminder(reminder_id: SafeIntPath):
    """删除随访提醒。"""
    from memory import get_session_store

    store = get_session_store()
    reminder = await asyncio.to_thread(store.get_reminder, reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail="提醒不存在")
    ok = await asyncio.to_thread(store.delete_reminder, reminder_id)
    if not ok:
        raise HTTPException(status_code=404, detail="提醒不存在")
    await asyncio.to_thread(
        store.redact_audit_targets,
        [
            "reminder.create", "reminder.update", "reminder.complete",
            "reminder.reopen", "reminder.snooze", "reminder.delete",
        ],
        f"{reminder['id']}·",
        str(reminder["id"]),
    )
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


@app.post("/api/tools/apri")
async def tool_apri(request: ApriRequest):
    """APRI 肝纤维化指数。"""
    from core.clinical_tools import calculate_apri

    try:
        result = calculate_apri(
            request.ast, request.platelets, request.ast_uln
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


@app.post("/api/tools/eag")
async def tool_eag(request: EagRequest):
    """由 HbA1c 估算平均血糖（eAG）。"""
    from core.clinical_tools import calculate_eag

    try:
        result = calculate_eag(request.hba1c)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True, **result}


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
    # limit<=0 是导出/对比用的“全部”语义，使用不受 500 条限制的读取，
    # 但仍设 10000 条硬上限防止超大响应
    if limit <= 0:
        stored = await asyncio.to_thread(
            store.get_all_messages, session_id, 10000
        )
    else:
        stored = await asyncio.to_thread(
            store.get_messages, session_id, max(1, min(limit, 500))
        )
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
    deleted = await asyncio.to_thread(store.is_session_deleted, session_id)
    # 已删除会话即使短期记忆里有迟到写入，也不能再通过回退接口返回
    messages = [] if deleted else memory.get_recent_messages(
        session_id=session_id, limit=limit
    )
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

    store = get_session_store()
    tags = await asyncio.to_thread(store.set_tags, session_id, request.tags)
    if tags is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    try:
        # 标签可能含“抑郁”“化疗”等健康信息：清理该会话历史审计中的原文
        await asyncio.to_thread(
            store.redact_audit_targets,
            ["session.tags"], session_id, session_id, "tags=redacted",
        )
    except Exception as e:
        logger.warning(f"清理历史标签审计失败: {e}")
    await record_audit("session.tags", session_id, f"count={len(tags)}")
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
    ids = list(dict.fromkeys(request.ids))
    entries = []
    for session_id in ids:
        existing = await asyncio.to_thread(store.get_session, session_id)
        entries.append(
            (str((existing or {}).get("profile") or ""), session_id)
        )
        memory.clear_session(session_id)
        memory.seal_session(session_id)
        ok = await asyncio.to_thread(store.delete_session, session_id)
        if ok:
            deleted += 1
        else:
            missing.append(session_id)

    await asyncio.to_thread(_erase_long_term_entries, entries)
    await asyncio.to_thread(_erase_session_summaries, ids)
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

    try:
        # 批量审计没有单条 target，按 action 清理全部历史标签原文
        await asyncio.to_thread(
            store.redact_audit_targets,
            ["session.bulk_tags"], "", "", "tags=redacted",
            match_all=True,
        )
    except Exception as e:
        logger.warning(f"清理批量标签审计失败: {e}")
    await record_audit(
        "session.bulk_tags",
        detail=(
            f"updated={updated} missing={len(missing)} "
            f"count={len(normalized)}"
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

    store = get_session_store()
    existing = await asyncio.to_thread(store.get_session, session_id)
    short_term = ShortTermMemory()
    short_term.clear_session(session_id)
    short_term.seal_session(session_id)
    deleted = await asyncio.to_thread(store.delete_session, session_id)
    profile = str((existing or {}).get("profile") or "")
    # SQLite 删除后同步清理外部长期记忆与会诊总结明文，
    # 兑现「删除后不可恢复」的承诺
    await asyncio.to_thread(_erase_long_term_entries, [(profile, session_id)])
    await asyncio.to_thread(_erase_session_summaries, [session_id])
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
        raise HTTPException(status_code=500, detail="读取知识库失败，请稍后重试。")


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
            raise HTTPException(status_code=500, detail="重建知识库失败，请稍后重试。")

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
        raise HTTPException(status_code=500, detail="保存文档失败，请稍后重试。")

    result = None
    if rebuild:
        async with state.kb_lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("上传后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail="文档已保存，但重建索引失败，请稍后重试。",
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
                "error": "保存失败，请稍后重试。",
            })

    if not saved:
        raise HTTPException(
            status_code=400,
            detail="所有文件均未上传成功，请检查文件格式与大小后重试。",
        )

    result = None
    if rebuild:
        async with state.kb_lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("批量上传后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail="文档已保存，但重建索引失败，请稍后重试。",
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
        "symptoms",
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
        raise HTTPException(status_code=500, detail="检索失败，请稍后重试。")
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
        raise HTTPException(status_code=500, detail="读取文档失败，请稍后重试。")

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
        message = str(e)
        status_code = 404 if message.startswith("文档不存在") else 400
        raise HTTPException(status_code=status_code, detail=message)
    except Exception as e:
        logger.exception("更新知识库文档失败")
        raise HTTPException(status_code=500, detail="保存文档失败，请稍后重试。")

    result = None
    if rebuild:
        async with state.kb_lock:
            try:
                result = await asyncio.to_thread(service.rebuild)
            except Exception as e:
                logger.exception("更新后重建知识库失败")
                raise HTTPException(
                    status_code=500,
                    detail="文档已保存，但重建索引失败，请稍后重试。",
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
                    detail="文档已删除，但重建索引失败，请稍后重试。",
                )

    return {"ok": True, "filename": filename, "rebuild": result}


def _clear_short_term_tombstones(session_ids: List[Any]) -> None:
    """导入/恢复会话后解除短期记忆墓碑，允许恢复后的会话继续写入。"""
    try:
        from memory import ShortTermMemory

        memory = ShortTermMemory()
        for raw in session_ids:
            session_id = str(raw or "").strip()
            if session_id:
                memory.clear_tombstone(session_id)
    except Exception as e:
        logger.warning(f"解除短期记忆墓碑失败（忽略）: {e}")


def _memory_scope(patient_profile: str, session_id: str) -> str:
    """长期记忆命名空间：只发送不可逆哈希。

    外部记忆服务（Mem0 等）会持久化 user_id，档案姓名属于敏感信息，
    不能出现在命名空间里；同一档案仍共享作用域，不同档案不会碰撞。
    旧版本以可读姓名写入的历史记忆不会自动迁移到新命名空间。
    """
    from memory.session_store import _normalize_profile_name

    raw_profile = _normalize_profile_name(patient_profile)
    if raw_profile:
        digest = hashlib.sha256(
            raw_profile.encode("utf-8")
        ).hexdigest()[:24]
        return f"medix_profile_{digest}"
    return session_memory_scope(session_id)


_standalone_long_term_memory = None


def _get_long_term_memory():
    """优先复用协调器已初始化的 Mem0 客户端，否则按需创建。"""
    global _standalone_long_term_memory
    coordinator = state.coordinator
    if coordinator is not None:
        memory = getattr(coordinator, "long_term_memory", None)
        if memory is not None:
            return memory
    if _standalone_long_term_memory is None:
        from memory.long_term import LongTermMemory

        _standalone_long_term_memory = LongTermMemory()
    return _standalone_long_term_memory


def _get_session_summary_manager():
    coordinator = state.coordinator
    if coordinator is not None:
        manager = getattr(coordinator, "session_manager", None)
        if manager is not None:
            return manager
    from memory import SessionSummaryManager

    return SessionSummaryManager()


def _erase_long_term_entries(entries: List[Any]) -> None:
    """清理会话对应的 Mem0 长期记忆；失败只记日志，不影响删除主流程。

    entries 为 ``(患者档案, 会话 ID)`` 列表。档案作用域由多个会话共享，
    只能按 metadata.session_id 精确删除；无档案的会话独占自己的命名空间。
    """
    try:
        memory = _get_long_term_memory()
    except Exception as e:
        logger.warning(
            f"初始化长期记忆失败（跳过清理）: {redact_secrets(str(e))}"
        )
        return
    if not getattr(memory, "enabled", False):
        return
    from memory.session_store import _normalize_profile_name

    for profile, session_id in entries:
        try:
            # 会话可能先以无档案状态写入自己的命名空间，之后才绑定档案；
            # 两次写入落在不同 user_id 下，因此两边都要清理
            memory.delete_scope(session_memory_scope(session_id))
        except Exception as e:
            logger.warning(f"清理会话记忆失败（忽略）: {redact_secrets(str(e))}")
        if _normalize_profile_name(profile):
            try:
                memory.delete_session_summary(
                    session_id, user_id=_memory_scope(profile, session_id)
                )
            except Exception as e:
                logger.warning(
                    f"清理档案记忆失败（忽略）: {redact_secrets(str(e))}"
                )


def _erase_long_term_scope(profile: str) -> None:
    """删除患者档案关联的整个 Mem0 命名空间。"""
    try:
        memory = _get_long_term_memory()
        if getattr(memory, "enabled", False):
            memory.delete_scope(_memory_scope(profile, ""))
    except Exception as e:
        logger.warning(f"清理档案长期记忆失败（忽略）: {redact_secrets(str(e))}")


def _erase_session_summaries(session_ids: List[str]) -> None:
    """删除会话总结明文文件；失败只记日志。"""
    try:
        manager = _get_session_summary_manager()
    except Exception as e:
        logger.warning(
            f"初始化会话总结管理器失败（跳过清理）: {redact_secrets(str(e))}"
        )
        return
    for session_id in session_ids:
        try:
            manager.delete_summary(session_id)
        except Exception as e:
            logger.warning(f"删除会话总结失败（忽略）: {redact_secrets(str(e))}")


async def _resolve_memory_profile(session_id: str, requested: str) -> str:
    """会话已绑定的档案优先，避免客户端用请求字段切换到其他患者。"""
    from memory import get_session_store

    try:
        session = await asyncio.to_thread(
            get_session_store().get_session, session_id
        )
    except Exception:
        session = None
    bound = str((session or {}).get("profile") or "").strip()
    return bound or str(requested or "").strip()


def _empty_answer_reason() -> str:
    """
    空答复几乎总是上游 LLM 调用失败导致的。
    对客户端只返回可操作的安全提示，不暴露 .env/config.py/base_url 等部署细节。
    """
    return (
        "模型没有返回任何内容，请稍后重试；"
        "若持续出现，请打开系统自检检查 API Key 与模型配置。"
    )


def _serialize_result(result: Dict[str, Any], agent_labels: Dict[str, str]) -> Dict[str, Any]:
    """把 coordinator 的原始返回值整理成前端直接可用的结构。"""
    raw_agents = result.get("agents_involved") or []
    payload = {
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
    }
    return sanitize_json(strip_lone_surrogates(payload))


def _allergy_text_for_context(items: List[Dict[str, Any]]) -> str:
    severity_labels = {
        "mild": "轻度",
        "moderate": "中度",
        "severe": "重度",
    }
    parts = []
    for item in items:
        substance = str(item.get("substance") or "").strip()
        if not substance:
            continue
        detail = []
        if item.get("reaction"):
            detail.append(str(item["reaction"]))
        label = severity_labels.get(item.get("severity") or "", "")
        if label:
            detail.append(label)
        parts.append(
            substance + (f"（{'，'.join(detail)}）" if detail else "")
        )
    return "；".join(parts)


async def _attach_structured_allergies(
    context: Dict[str, Any], profile: str
) -> Dict[str, Any]:
    """把结构化过敏史并入会诊上下文；失败时保留原上下文。"""
    from memory import get_session_store

    if not profile:
        return context
    try:
        items = await asyncio.to_thread(
            get_session_store().list_allergies,
            profile, True, None, "", 1000,
        )
    except Exception:
        return context
    if not items:
        return context
    merged = dict(context)
    existing = str(merged.get("allergies") or "").strip()
    if existing:
        extra_items = [
            item for item in items
            if item.get("substance")
            and str(item["substance"]) not in existing
        ]
        extra_text = _allergy_text_for_context(extra_items)
        if not extra_text:
            return merged
        merged["allergies"] = f"{existing}；{extra_text}"
    else:
        merged["allergies"] = _allergy_text_for_context(items)
    return merged


def _condition_context_text(items: List[Dict[str, Any]]) -> str:
    status_labels = {
        "active": "管理中",
        "controlled": "已控制",
        "resolved": "已痊愈",
    }
    parts = []
    for item in items:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        details = []
        if item.get("diagnosed_at"):
            details.append(str(item["diagnosed_at"]))
        label = status_labels.get(item.get("status") or "", "")
        if label:
            details.append(label)
        if item.get("icd10"):
            details.append(str(item["icd10"]))
        parts.append(
            name + (f"（{'，'.join(details)}）" if details else "")
        )
    return "；".join(parts)


async def _attach_structured_conditions(
    context: Dict[str, Any], profile: str
) -> Dict[str, Any]:
    """把未痊愈的慢病清单并入会诊既往史；失败时保留原上下文。"""
    from memory import get_session_store

    if not profile:
        return context
    try:
        items = await asyncio.to_thread(
            get_session_store().list_conditions,
            profile, True, None, 1000,
        )
    except Exception:
        return context
    text = _condition_context_text(items)
    if not text:
        return context
    merged = dict(context)
    existing = str(merged.get("history") or "").strip()
    merged["history"] = (
        f"{existing}；慢病：{text}" if existing else f"慢病：{text}"
    )
    return merged


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """同步接口：等 Swarm 全部跑完再返回。"""
    session_id = request.session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    memory_profile = await _resolve_memory_profile(
        session_id, request.patient_profile
    )
    memory_context = dict(request.context or {})
    # 紧急联系人属于本机急救卡信息，不发送给模型或写入会诊上下文
    memory_context.pop("emergency_contacts", None)
    memory_context["memory_scope"] = _memory_scope(
        memory_profile, session_id
    )
    memory_context = await _attach_structured_allergies(
        memory_context, memory_profile
    )
    memory_context = await _attach_structured_conditions(
        memory_context, memory_profile
    )

    async with state.lock:
        try:
            coordinator = state.get_coordinator()
            process_kwargs = {
                "question": request.question,
                "context": memory_context,
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
async def chat_stream(request: ChatRequest, http_request: Request):
    """
    SSE 流式接口。

    两路事件：
    - 轮询 worker 的 shared_context，播报「哪个 Agent 已完成」；
    - Swarm 汇总阶段通过 on_token 回调把增量文本推成 token 事件，
      前端据此渐进渲染会诊意见。
    """
    session_id = request.session_id or f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
    memory_profile = await _resolve_memory_profile(
        session_id, request.patient_profile
    )
    memory_context = dict(request.context or {})
    # 紧急联系人属于本机急救卡信息，不发送给模型或写入会诊上下文
    memory_context.pop("emergency_contacts", None)
    memory_context["memory_scope"] = _memory_scope(
        memory_profile, session_id
    )
    memory_context = await _attach_structured_allergies(
        memory_context, memory_profile
    )
    memory_context = await _attach_structured_conditions(
        memory_context, memory_profile
    )
    triage = detect_red_flags(request.question, memory_context)
    medication_safety = check_medication_safety(
        request.question, memory_context
    )

    async def event_stream() -> AsyncIterator[str]:
        def sse(payload: Dict[str, Any]) -> str:
            # 模型输出可能包含孤立代理码位，直接 encode 会让流中断
            safe_payload = strip_lone_surrogates(payload)
            return (
                "data: "
                + json.dumps(safe_payload, ensure_ascii=False, default=str)
                + "\n\n"
            )

        yield sse({"type": "stage", "stage": "routing", "session_id": session_id})

        # 急症红旗在调用模型之前就推给前端，不必等待会诊结果
        if triage["level"] != "none":
            yield sse({"type": "triage", **triage})

        # 用药安全提示同样先行推送，让用户在等待模型时就能看到
        if medication_safety["alerts"]:
            yield sse({"type": "medication_safety", **medication_safety})

        # 等待会诊锁期间也要保持心跳：另一次会诊可能运行数十秒，
        # 期间静默会让代理/浏览器读超时断流。设置总等待上限并按
        # 客户端断开及时退出，避免锁泄漏时无限排队。
        deadline = asyncio.get_event_loop().time() + STREAM_LOCK_TIMEOUT_SECONDS
        while True:
            if await http_request.is_disconnected():
                return
            try:
                await asyncio.wait_for(
                    state.lock.acquire(), timeout=STREAM_LOCK_ACQUIRE_SECONDS
                )
                break
            except asyncio.TimeoutError:
                if asyncio.get_event_loop().time() >= deadline:
                    yield sse({
                        "type": "error",
                        "code": "busy",
                        "message": "当前会诊较多，请稍后重试。",
                    })
                    return
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
            "context": memory_context,
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

        def link_profile_when_done(done_task):
            # 客户端在 result 事件前断开时，已保存的会诊仍要关联患者档案
            if done_task.cancelled():
                return
            try:
                done_task.result()
            except Exception:
                return
            asyncio.create_task(
                _link_patient_profile(session_id, request.patient_profile)
            )

        task.add_done_callback(link_profile_when_done)

        def finish_task(done_task):
            # 锁必须等子任务真正结束才释放：仅 cancel() 后立刻 release
            # 会让下一个请求与仍在收尾的 consultation 并发
            if not done_task.cancelled():
                try:
                    done_task.exception()
                except Exception:
                    pass
            for worker in getattr(coordinator, "worker_pool", []):
                sc = getattr(worker, "shared_context", None)
                if sc is None or id(sc) in stale_contexts:
                    continue
                detach = getattr(worker, "attach_shared_context", None)
                if callable(detach):
                    detach(None)
                else:
                    try:
                        worker.shared_context = None
                    except Exception:
                        pass
            release_lock()

        task.add_done_callback(finish_task)
        # 锁只在会诊执行与事件排空期间持有；任务结束后由 finally
        # 在客户端断开/尾帧发送完成后统一释放

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
            # 客户端断开时取消子任务；锁、Worker 解绑与异常取走
            # 都交给 finish_task 在任务真正结束后执行
            if task is not None and not task.done():
                task.cancel()

        payload = _serialize_result(result, AGENT_LABELS)
        if not (payload["answer"] or "").strip():
            yield sse({
                "type": "error",
                "code": "empty_answer",
                "message": _empty_answer_reason(),
            })
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
    content = sw_file.read_text(encoding="utf-8").replace(
        "__MEDIX_BUILD__", WEB_BUILD_ID
    )
    return Response(
        content=content,
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
    parser.add_argument(
        "--allow-insecure-lan",
        action="store_true",
        help="未设置访问令牌时仍允许绑定非本机地址（仅限完全可信的内网）",
    )
    args = parser.parse_args()

    allow_insecure = args.allow_insecure_lan or (
        (os.getenv(INSECURE_LAN_ENV) or "").strip().lower()
        in {"1", "true", "yes", "on"}
    )
    bind_error = check_bind_security(
        args.host, get_access_token(), allow_insecure
    )
    if bind_error:
        logger.error(bind_error)
        raise SystemExit(2)
    if (
        allow_insecure
        and not get_access_token()
        and not _is_loopback_host(args.host)
    ):
        logger.warning(
            "已按显式配置在没有访问令牌的情况下绑定 "
            f"{args.host}：局域网内任何设备都可读写健康数据，请确认网络可信。"
        )

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
