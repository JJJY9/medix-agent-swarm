"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶
"""
import asyncio
import functools
import uuid
from datetime import datetime
from typing import Callable, Dict, Any, Optional, List, Tuple
from loguru import logger

from core import LLMClient
from core.llm_client import LLMError
from core.context import format_context, public_context
from core.triage import detect_red_flags
from core.medication_safety import check_medication_safety
from core.citations import merge_sources
from core.usage import merge_usages
from core.session_context import (
    profile_memory_scope,
    session_memory_scope,
)
from .shared_context import SharedContext, TaskStatus
from .lead_agent import LeadAgent, is_degraded_answer
from .events import Event, EventType
from agents import ConsultationAgent, DiagnosticAgent, ResearchAgent
from memory import (
    SessionSummaryManager,
    SessionSummary,
    ShortTermMemory,
    LongTermMemory,
    get_session_store,
)

try:
    from constraints import ConstraintValidator
    CONSTRAINTS_AVAILABLE = True
except ImportError:  # pragma: no cover - constraints 是可选模块
    ConstraintValidator = None
    CONSTRAINTS_AVAILABLE = False

try:
    from validation.auto_fixer import AutoFixer
    AUTO_FIXER_AVAILABLE = True
except ImportError:  # pragma: no cover - validation 是可选模块
    AutoFixer = None
    AUTO_FIXER_AVAILABLE = False


#: 单次会诊最多拆出的子任务数，与 constraints/swarm_constraints.yaml 保持一致
MAX_SWARM_SUBTASKS = 5

#: 约束规则强制补全 Agent 时使用的任务描述
_REQUIRED_AGENT_DESCRIPTIONS = {
    "diagnostic_agent": "评估症状风险等级，分析症状模式并给出鉴别诊断思路",
    "research_agent": "检索权威临床指南与循证医学证据，为问题提供证据支持",
    "consultation_agent": "提供健康管理、生活方式和就医建议",
}


class SwarmCoordinator:
    """
    Swarm 协调器

    职责：
    1. 智能路由（简单 → 单 Agent，复杂 → Swarm）
    2. 初始化 SharedContext
    3. 启动和监控 Swarm
    4. 生成 SessionSummary

    不做：
    - 不编排 Worker 执行顺序
    - 不直接调用 Worker
    - 不控制任务分配
    """

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        enable_swarm: bool = True
    ):
        self.llm_client = llm_client or LLMClient()
        self.enable_swarm = enable_swarm

        # 初始化 Agent
        self.lead_agent = LeadAgent(llm_client=self.llm_client)
        self.consultation_agent = ConsultationAgent()
        self.diagnostic_agent = DiagnosticAgent()
        self.research_agent = ResearchAgent()

        # Worker 池
        self.worker_pool: List[Any] = [
            self.consultation_agent,
            self.diagnostic_agent,
            self.research_agent
        ]

        # 记忆管理器
        self.session_manager = SessionSummaryManager()
        self.short_term_memory = ShortTermMemory(storage_type="memory")  # 或 "redis"
        self.long_term_memory = LongTermMemory()
        self.session_store = get_session_store()

        # Harness Engineering: Swarm 约束（高危症状必须包含诊断 Agent 等）
        if CONSTRAINTS_AVAILABLE:
            try:
                self.validator = ConstraintValidator()
            except Exception as e:
                logger.warning(f"Swarm 约束加载失败，已禁用约束兜底: {e}")
                self.validator = None
        else:
            self.validator = None

        # 汇总答案同样需要约束兜底与自动修复
        if AUTO_FIXER_AVAILABLE:
            try:
                self.auto_fixer = AutoFixer()
            except Exception as e:
                logger.warning(f"Swarm 自动修复器加载失败: {e}")
                self.auto_fixer = None
        else:
            self.auto_fixer = None

        # 将短期记忆注入到所有 Worker Agent 的 Loop
        # 注意：LeadAgent 不继承 BaseAgent，没有 loop 属性，不需要注入
        for worker in self.worker_pool:
            if hasattr(worker, 'loop'):
                worker.loop.short_term_memory = self.short_term_memory

        logger.info(f"SwarmCoordinator initialized with {len(self.worker_pool)} workers")
        logger.info(f"Memory system: short_term={self.short_term_memory.storage_type}, long_term={'enabled' if self.long_term_memory.enabled else 'disabled'}")

    def _get_agent_by_id(self, agent_id: str):
        """根据 agent_id 返回对应的 Agent 实例"""
        mapping = {
            "consultation_agent": self.consultation_agent,
            "diagnostic_agent": self.diagnostic_agent,
            "research_agent": self.research_agent
        }
        return mapping.get(agent_id)

    def _enforce_final_constraints(self, answer: str, question: str) -> str:
        """对 Swarm 汇总答案执行约束校验与两轮自动修复。

        Worker 的答案各自经过 AgentLoop 校验，但 LeadAgent 重新生成的汇总
        文本没有；这里补上同样的免责声明/高危就医兜底。
        """
        if not self.validator or not answer:
            return answer
        agent_id = "swarm_lead"
        result = self.validator.validate_output(
            agent_id, answer, user_input=question
        )
        if result.get("valid"):
            return answer
        logger.warning(
            f"⚠️ Swarm 汇总输出约束违规: {result.get('violations')}"
        )
        if not self.auto_fixer or not result.get("auto_fixable"):
            return answer

        fixed = self.auto_fixer.fix_output(
            answer,
            result.get("auto_fixable", []),
            user_input=question,
        )
        if fixed == answer:
            return answer
        recheck = self.validator.validate_output(
            agent_id, fixed, user_input=question
        )
        if recheck.get("valid"):
            logger.info("✅ Swarm 汇总答案已自动修复")
            return fixed
        if recheck.get("auto_fixable"):
            second = self.auto_fixer.fix_output(
                fixed,
                recheck.get("auto_fixable", []),
                user_input=question,
            )
            if second != fixed:
                final_check = self.validator.validate_output(
                    agent_id, second, user_input=question
                )
                if final_check.get("valid"):
                    logger.info("✅ Swarm 汇总答案二次修复后已无违规")
                else:
                    logger.warning(
                        "⚠️ Swarm 汇总答案二次修复后仍有违规: "
                        f"{final_check.get('violations')}"
                    )
                return second
        logger.warning(
            "⚠️ Swarm 汇总答案自动修复后仍有违规: "
            f"{recheck.get('violations')}"
        )
        return fixed

    async def _restore_short_term_context(self, session_id: str) -> None:
        """服务重启后从 SQLite 恢复最近问答，保证多轮上下文不中断。"""
        if self.short_term_memory.get_session(session_id) is not None:
            return

        try:
            stored = await asyncio.to_thread(
                self.session_store.get_messages, session_id, 10
            )
        except Exception as e:
            logger.warning(f"读取历史会话失败（session={session_id}）: {e}")
            return

        if stored:
            # SQLite 中仍有记录说明会话存在（例如备份恢复），
            # 解除短期记忆墓碑后再回填历史
            self.short_term_memory.clear_tombstone(session_id)
        for message in stored:
            role = message.get("role")
            if role in ("user", "assistant"):
                self.short_term_memory.add_message(
                    session_id=session_id,
                    role=role,
                    content=message.get("content", ""),
                )

        if stored:
            logger.info(
                f"Restored {len(stored)} messages from session store "
                f"(session={session_id})"
            )

    def _save_exchange(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None,
        profile: str = "",
    ) -> Optional[int]:
        """把一轮问答写入 SQLite，供历史列表和重启恢复使用。

        Returns:
            assistant 消息 id；保存失败或答复为空时返回 None。
        """
        if not (answer or "").strip():
            return None
        try:
            return self.session_store.save_exchange(
                session_id, question, answer,
                metadata=metadata, profile=profile,
            )
        except Exception as e:
            logger.error(f"保存会话记录失败（session={session_id}）: {e}")
            return None

    async def _save_exchange_guarded(
        self,
        session_id: str,
        question: str,
        answer: str,
        metadata: Optional[Dict[str, Any]] = None,
        profile: str = "",
    ) -> Optional[int]:
        """落库期间被取消时，等线程完成并清掉用量元数据。

        asyncio.to_thread 的工作线程无法取消：取消恰好命中落库时，
        答复仍会写入 SQLite，而取消路径也把同一笔用量记入
        usage_events，造成 token/调用次数双计。
        """
        future = asyncio.get_running_loop().run_in_executor(
            None,
            functools.partial(
                self._save_exchange,
                session_id,
                question,
                answer,
                metadata=metadata,
                profile=profile,
            ),
        )
        try:
            return await asyncio.shield(future)
        except asyncio.CancelledError:
            try:
                message_id = await asyncio.shield(future)
            except Exception:
                message_id = None
            if message_id:
                try:
                    await asyncio.to_thread(
                        self.session_store.clear_message_usage,
                        message_id,
                    )
                except Exception:
                    pass
            raise

    @staticmethod
    def _history_metadata(result: Dict[str, Any]) -> Dict[str, Any]:
        """提取需要随会话历史保存的会诊依据。"""
        keys = (
            "swarm_enabled",
            "suggestions",
            "disclaimer",
            "triage",
            "medication_safety",
            "sources",
            "agent_contributions",
            "agents_involved",
            "subtasks_completed",
            "subtasks_failed",
            "total_time",
            "timeout_occurred",
            "usage",
            "llm_calls",
            "patient_context",
        )
        return {
            key: result.get(key)
            for key in keys
            if result.get(key) is not None
        }

    def _enforce_required_agents(self, question: str, assessment: Dict[str, Any]) -> None:
        """按 swarm_constraints.yaml 的规则补齐必须参与的 Agent。

        LeadAgent 的分解结果可能漏掉高危症状要求的诊断 Agent，
        或漏掉指南类问题要求的 ResearchAgent。这里用约束规则兜底，
        同时把子任务数量控制在 MAX_SWARM_SUBTASKS 以内。
        """
        if self.validator is None:
            return

        try:
            required_agents = self.validator.get_required_agents(question)
        except Exception as e:
            logger.warning(f"读取 Swarm 约束规则失败，跳过强制 Agent 补全: {e}")
            return

        subtasks = assessment.get("subtasks") or []
        if not isinstance(subtasks, list):
            return

        assigned = {
            task.get("assigned_agent")
            for task in subtasks
            if isinstance(task, dict)
        }

        for agent_id in required_agents:
            description = _REQUIRED_AGENT_DESCRIPTIONS.get(agent_id)
            if not description or agent_id in assigned:
                continue

            subtasks.append({
                "description": description,
                "assigned_agent": agent_id,
            })
            assigned.add(agent_id)
            logger.info(f"约束规则要求补充子任务：{agent_id}")

        if len(subtasks) > MAX_SWARM_SUBTASKS:
            required_set = set(required_agents)
            required_tasks = [
                task for task in subtasks
                if isinstance(task, dict) and task.get("assigned_agent") in required_set
            ]
            other_tasks = [
                task for task in subtasks
                if not isinstance(task, dict) or task.get("assigned_agent") not in required_set
            ]
            subtasks = (required_tasks + other_tasks)[:MAX_SWARM_SUBTASKS]
            logger.warning(
                f"子任务数量超过上限 {MAX_SWARM_SUBTASKS}，已截断为 {len(subtasks)} 个"
            )

        assessment["subtasks"] = subtasks

    @staticmethod
    def _attach_usage(
        exc: BaseException, usage: Optional[Dict[str, Any]], calls: int
    ) -> None:
        """把已消耗的用量挂到异常上，供 api_server 写入用量流水。"""
        if not usage and not calls:
            return
        try:
            exc.usage = usage or {}
            exc.llm_calls = max(0, int(calls))
        except (AttributeError, TypeError):
            pass

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        enable_swarm: Optional[bool] = None,
        patient_profile: str = "",
        request_started: str = "",
    ) -> Dict[str, Any]:
        """对外入口：异常路径补挂已消耗的用量。

        取消或模型失败时，本轮已计费的 tokens 可能只存在于局部变量
        （Lead 任务分解、单 Agent 结果、Swarm 汇总）。子流程若已把
        快照挂到异常上则直接复用，否则在这里补挂 Lead 与已完成结果
        的用量，api_server 的 _record_failed_usage 才能记账。
        """
        usage_state: Dict[str, Any] = {}
        try:
            return await self._process_inner(
                question=question,
                context=context,
                session_id=session_id,
                on_token=on_token,
                enable_swarm=enable_swarm,
                patient_profile=patient_profile,
                usage_state=usage_state,
                request_started=request_started,
            )
        except (asyncio.CancelledError, LLMError) as exc:
            if getattr(exc, "usage", None) or getattr(exc, "llm_calls", 0):
                raise
            usage: Dict[str, Any] = {}
            calls = 0
            result = usage_state.get("result")
            if isinstance(result, dict):
                # 结果已合并 Lead 用量，不能再叠加一次
                usage = merge_usages(usage, result.get("usage"))
                calls += int(result.get("llm_calls") or 0)
            else:
                lead_usage = usage_state.get("lead_usage")
                if isinstance(lead_usage, dict) and lead_usage:
                    usage = merge_usages(usage, lead_usage)
                    calls += 1
            self._attach_usage(exc, usage, calls)
            raise

    async def _process_inner(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        enable_swarm: Optional[bool] = None,
        patient_profile: str = "",
        usage_state: Optional[Dict[str, Any]] = None,
        request_started: str = "",
    ) -> Dict[str, Any]:
        """
        处理用户问题

        Args:
            question: 用户问题
            context: 额外上下文（年龄、既往史等）
            session_id: 会话ID（如果不提供，将自动生成）
            on_token: 可选的流式回调；Swarm 汇总阶段会逐段推送文本
            enable_swarm: 本次请求是否允许 Swarm；None 使用实例默认值

        Returns:
            处理结果
        """
        start_time = datetime.now()
        if session_id is None:
            session_id = f"{start_time.strftime('%Y%m%d-%H%M%S')}-{str(uuid.uuid4())[:8]}"
        use_swarm = self.enable_swarm if enable_swarm is None else bool(enable_swarm)

        # 档案可能在上一轮之后被改名：解析到当前名称，避免长期记忆写回
        # 旧命名空间、SQLite 会话重新绑定已经不存在的旧档案
        if patient_profile:
            try:
                resolved_profile = await asyncio.to_thread(
                    self.session_store.resolve_profile_alias,
                    patient_profile,
                )
            except Exception:
                resolved_profile = patient_profile
            if resolved_profile and resolved_profile != patient_profile:
                patient_profile = resolved_profile
                if isinstance(context, dict) and context.get(
                    "memory_scope"
                ):
                    context = {
                        **context,
                        "memory_scope": profile_memory_scope(
                            resolved_profile
                        ),
                    }

        # 长期记忆按患者档案/会话隔离；API 会把 memory_scope 放进 context
        memory_scope = ""
        if isinstance(context, dict):
            memory_scope = str(context.get("memory_scope") or "").strip()
        if not memory_scope:
            memory_scope = session_memory_scope(session_id)
        memory_scope = memory_scope[:120]

        logger.info(
            f"Processing question (session={session_id}): "
            f"length={len(question or '')}"
        )

        # 确定性急症预检：命中红旗时立即随结果返回，不依赖模型输出
        from core.triage import patient_complaint_text

        # 科普/定义型提问（“什么是咯血？”）不是患者主诉，不能触发急症
        triage = detect_red_flags(
            patient_complaint_text(question), context
        )

        # 确定性用药安全预检：过敏史、重复用药与常见相互作用
        medication_safety = check_medication_safety(question, context)

        # 不再每轮无条件解除删除墓碑：排队期间被删除的会话/档案
        # 必须保持删除状态；显式恢复走导入或重新创建会话的入口。
        started_text = (
            str(request_started or "").strip()[:40]
            or start_time.isoformat(timespec="seconds")
        )

        async def _profile_deleted_midflight() -> bool:
            """档案在本次会诊开始后被删除：不能再写回其命名空间或绑定。"""
            if not patient_profile:
                return False
            try:
                return await asyncio.to_thread(
                    self.session_store.profile_deleted_since,
                    patient_profile, started_text,
                )
            except Exception:
                return False

        async def _bound_profile() -> str:
            """写回 SQLite 的档案名：已改名时用新名称，已删除时为空。"""
            if await _profile_deleted_midflight():
                return ""
            try:
                resolved = await asyncio.to_thread(
                    self.session_store.resolve_profile_alias,
                    patient_profile,
                )
            except Exception:
                return patient_profile
            if not resolved:
                return patient_profile
            try:
                deleted = await asyncio.to_thread(
                    self.session_store.profile_deleted_since,
                    resolved, started_text,
                )
                if not deleted:
                    # 删除之后才到达的请求也要识别：档案留有墓碑且未
                    # 真正重建（patient_profiles 无同名行；删除时按约定
                    # 保留的健康数据不算重建）
                    deleted = await asyncio.to_thread(
                        self.session_store.profile_rebinding_blocked,
                        resolved,
                    )
            except Exception:
                deleted = False
            if deleted:
                return ""
            return resolved

        async def _write_scope() -> str:
            """长期记忆写入作用域：改名后改用新档案的命名空间。

            没有患者档案的会话（patient_profile 为空）必须回退到会话
            作用域；否则 add_session_summary 会落进共享 medix_local，
            既检索不到、删除会话时也清不掉。
            """
            if patient_profile:
                resolved = await _bound_profile()
                if not resolved:
                    return ""
                if resolved != patient_profile:
                    return profile_memory_scope(resolved)
            return memory_scope or session_memory_scope(session_id)

        # 服务重启后从 SQLite 恢复本会话的最近问答
        await self._restore_short_term_context(session_id)

        # ===== 统一的记忆检索（所有模式都使用）=====
        # 1. 检索短期记忆（当前会话历史）
        recent_history = self.short_term_memory.get_recent_messages(
            session_id=session_id,
            limit=10  # 最近5轮对话（10条消息）
        )

        # 2. 检索长期记忆（相似历史会话）；删除档案的云端清理尚未
        # 完成时不检索同名命名空间，避免召回已删除患者的病史
        skip_long_term = False
        scope_to_check = (
            str(memory_scope or "").strip()
            or session_memory_scope(session_id)
        )
        try:
            # 作用域级门禁同时覆盖整档删除与单个会话删除的待清理项
            skip_long_term = await asyncio.to_thread(
                self.session_store.memory_scope_cleanup_pending,
                scope_to_check,
            )
        except Exception:
            skip_long_term = False
        if skip_long_term:
            similar_memories = []
        else:
            similar_memories = await asyncio.to_thread(
                self.long_term_memory.search_similar_sessions,
                query=question,
                limit=3,
                user_id=memory_scope,
            )

        # 3. 构建增强上下文（复制一份，避免修改调用方传入的字典）
        enhanced_context = dict(context or {})
        patient_context = public_context(context)

        # 添加短期记忆
        if recent_history:
            enhanced_context["recent_history"] = [
                {"role": msg.get("role", ""), "content": msg.get("content", "")}
                for msg in recent_history
            ]
            logger.info(f"Loaded {len(recent_history)} recent messages from short-term memory")

        # 添加长期记忆
        if similar_memories:
            enhanced_context["historical_cases"] = [
                {
                    "summary": mem["content"],
                    "score": mem["score"]
                }
                for mem in similar_memories
            ]
            logger.info(f"Found {len(similar_memories)} similar historical cases from long-term memory")

        # 单 Agent 路径的 AgentLoop 会自行从短期记忆加载最近对话，
        # context 里再带一份 recent_history 会让历史在提示词中重复出现
        single_context = {
            key: value for key, value in enhanced_context.items()
            if key != "recent_history"
        }

        # Step 1: LeadAgent 分解任务
        # 追问的指代（“那还要继续吃吗”）必须让分解阶段看到最近对话；
        # memory_scope 是内部字段，仍不能传给模型
        lead_context = {
            key: value for key, value in enhanced_context.items()
            if key not in ("memory_scope", "historical_cases")
        }
        lead_usage: Dict[str, int] = {}
        if usage_state is not None:
            # 保存引用：usage_out 会就地累加，异常包装器读取最新值
            usage_state["lead_usage"] = lead_usage
        assessment = await self.lead_agent.assess_and_decompose(
            question,
            format_context(lead_context, include_internal=True) or "无",
            usage_out=lead_usage,
        )
        # 约束兜底：高危症状/指南类问题必须包含对应 Agent
        self._enforce_required_agents(question, assessment)
        subtasks = assessment.get("subtasks", [])

        logger.info(f"LeadAgent 分解任务：{len(subtasks)} 个")

        # Step 2: 根据任务数量路由
        final_answer = None
        mode = None

        if len(subtasks) == 1:
            # 单任务 → 直接调用对应 Agent
            task = subtasks[0]
            agent_id = task.get("assigned_agent")
            agent = self._get_agent_by_id(agent_id)

            if agent is None:
                # 如果找不到 Agent，降级到 ConsultationAgent
                logger.warning(f"Unknown agent_id: {agent_id}, fallback to ConsultationAgent")
                agent = self.consultation_agent

            logger.info(f"Route: Single Agent ({agent_id})")
            mode = "single_agent"
            routed_question = self._routed_question(question, task)
            try:
                result = await agent.process({
                    'question': routed_question,
                    'memory_question': question,
                    'context': single_context,
                    'session_id': session_id
                }, on_token=on_token)
            except (asyncio.CancelledError, LLMError) as exc:
                # AgentLoop 只挂自己的用量：Lead 分解的 token 也要合并
                merged = merge_usages(
                    lead_usage, getattr(exc, "usage", None)
                )
                calls = (1 if lead_usage else 0) + int(
                    getattr(exc, "llm_calls", 0) or 0
                )
                self._attach_usage(exc, merged, calls)
                raise
            final_answer = result.get('answer', '')

            result["usage"] = merge_usages(lead_usage, result.get("usage"))
            result["llm_calls"] = int(result.get("llm_calls") or 0) + 1
            if usage_state is not None:
                usage_state["result"] = result

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'route_reason': f'单任务路由到 {agent_id}',
                'triage': triage,
                'medication_safety': medication_safety,
                'patient_context': patient_context,
            })

            # 确保单Agent模式下也有 disclaimer 字段
            if 'disclaimer' not in result:
                result['disclaimer'] = "⚠️ 以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"

            # 确保单Agent模式下也有 suggestions 字段
            if 'suggestions' not in result:
                result['suggestions'] = []

        elif len(subtasks) >= 2 and use_swarm:
            # 多任务 → 启动 Swarm
            logger.info(f"Route: Swarm (Multi-Agent Collaboration) - {len(subtasks)} tasks")
            mode = "swarm"
            result = await self._process_with_swarm(
                question=question,
                context=enhanced_context,
                assessment=assessment,
                session_id=session_id,
                start_time=start_time,
                on_token=on_token,
                lead_usage=lead_usage,
                patient_context=patient_context,
                memory_scope=memory_scope,
                patient_profile=patient_profile,
                request_started=started_text,
            )
            final_answer = result.get('answer', '')
            if usage_state is not None:
                # 落库窗口被取消时，外层包装器仍有完整用量可记账
                usage_state["result"] = result

            # Swarm 的长期记忆已在 _process_with_swarm 中保存；这里补齐 triage 后
            # 再把会诊依据写入 SQLite
            result['triage'] = triage
            result['medication_safety'] = medication_safety
            bound_profile = await _bound_profile()
            if result.get('degraded'):
                # 兜底道歉不是医疗结论：不写入 SQLite 会话历史
                logger.warning(
                    "Degraded swarm answer, skip SQLite exchange "
                    f"(session={session_id})"
                )
            else:
                result['assistant_message_id'] = (
                    await self._save_exchange_guarded(
                        session_id,
                        question,
                        final_answer,
                        metadata=self._history_metadata(result),
                        profile=bound_profile,
                    )
                )
            return result

        else:
            # 0个任务或Swarm关闭 → 降级到 ConsultationAgent
            if len(subtasks) == 0:
                logger.warning("No subtasks generated, fallback to ConsultationAgent")
                mode = "fallback"
            else:
                logger.info("Swarm disabled, fallback to ConsultationAgent")
                mode = "disabled_swarm"

            try:
                result = await self.consultation_agent.process({
                    'question': question,
                    'context': single_context,
                    'session_id': session_id
                }, on_token=on_token)
            except (asyncio.CancelledError, LLMError) as exc:
                merged = merge_usages(
                    lead_usage, getattr(exc, "usage", None)
                )
                calls = (1 if lead_usage else 0) + int(
                    getattr(exc, "llm_calls", 0) or 0
                )
                self._attach_usage(exc, merged, calls)
                raise
            final_answer = result.get('answer', '')

            result["usage"] = merge_usages(lead_usage, result.get("usage"))
            result["llm_calls"] = int(result.get("llm_calls") or 0) + 1
            if usage_state is not None:
                usage_state["result"] = result

            result.update({
                'swarm_enabled': False,
                'session_id': session_id,
                'triage': triage,
                'medication_safety': medication_safety,
                'patient_context': patient_context,
            })

        # ===== 统一的记忆保存（非 Swarm 模式）=====
        end_time = datetime.now()

        # 注意：短期记忆已经在 Agent Loop 中保存了，这里不需要重复保存

        # 保存到长期记忆；兜底道歉/截断提示不是医疗结论，不写任何持久层
        degraded_answer = bool(result.get("degraded"))
        session_deleted = (
            degraded_answer or await self._is_session_deleted(session_id)
        )
        profile_deleted = (
            degraded_answer or await _profile_deleted_midflight()
        )
        if session_deleted or profile_deleted:
            # 会诊进行中用户已删除会话/档案：不能再把总结写回外部记忆
            logger.info(f"Session deleted, skip long-term memory (session={session_id})")
        else:
            write_scope = await _write_scope()
            try:
                memory_id = await asyncio.to_thread(
                    self.long_term_memory.add_session_summary,
                    session_id=session_id,
                    question=question,
                    answer=final_answer,
                    user_id=write_scope,
                    metadata={
                        "mode": mode,
                        "subtasks_count": len(subtasks),
                        "total_time": (end_time - start_time).total_seconds(),
                    },
                )
                # 删除可能发生在检查之后：写入后再核对并清理外部记忆
                if (
                    await self._is_session_deleted(session_id)
                    or await _profile_deleted_midflight()
                ):
                    await asyncio.to_thread(
                        self.long_term_memory.delete_session_summary,
                        session_id,
                        user_id=write_scope,
                    )
                    logger.info(
                        "Session deleted during long-term memory save; "
                        f"memory removed (session={session_id})"
                    )
                elif memory_id:
                    logger.info(f"Saved to long-term memory (session={session_id}, mode={mode})")
                else:
                    logger.warning(f"Long-term memory not saved (session={session_id}, mode={mode})")
            except Exception as e:
                logger.error(f"Failed to save to long-term memory: {e}")

        # 持久化到 SQLite，供历史列表与重启恢复使用
        bound_profile = await _bound_profile()
        if degraded_answer:
            logger.warning(
                "Degraded answer, skip SQLite exchange "
                f"(session={session_id})"
            )
        else:
            result['assistant_message_id'] = (
                await self._save_exchange_guarded(
                    session_id,
                    question,
                    final_answer,
                    metadata=self._history_metadata(result),
                    profile=bound_profile,
                )
            )

        return result

    @staticmethod
    def _routed_question(
        question: str, task: Dict[str, Any]
    ) -> str:
        """单 Agent 路由时把子任务描述并入问题，保留分解出的关注点。"""
        description = str(task.get("description") or "").strip()
        if description and description not in question:
            return f"{question}\n\n本次任务重点：{description}"
        return question

    async def _resolved_memory_scope(
        self, patient_profile: str, memory_scope: str, session_id: str
    ) -> str:
        """长期记忆写入作用域：档案改名后使用新档案的命名空间。"""
        scope = str(memory_scope or "").strip()
        if patient_profile:
            try:
                resolved = await asyncio.to_thread(
                    self.session_store.resolve_profile_alias,
                    patient_profile,
                )
            except Exception:
                resolved = patient_profile
            if resolved and resolved != patient_profile:
                return profile_memory_scope(resolved)
        return scope or session_memory_scope(session_id)

    async def _is_session_deleted(self, session_id: str) -> bool:
        """会话删除检查放到线程池，避免在事件循环上抢 SessionStore 锁。"""
        try:
            return await asyncio.to_thread(
                self.session_store.is_session_deleted, session_id
            )
        except Exception:
            return False

    async def _process_with_swarm(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        assessment: Dict[str, Any],
        session_id: str,
        start_time: datetime,
        on_token: Optional[Callable[[str], None]] = None,
        lead_usage: Optional[Dict[str, int]] = None,
        patient_context: Optional[Dict[str, Any]] = None,
        memory_scope: str = "",
        patient_profile: str = "",
        request_started: str = "",
    ) -> Dict[str, Any]:
        """包装 Swarm 流程：取消或模型失败时把已消耗的用量挂到异常上。

        客户端中途停止/断线会 cancel 整个任务，此时各家 LLM 的 token
        已被服务商计费，但 SQLite 里没有 assistant 消息可承载用量；
        由 api_server 从 CancelledError/LLMError 上读取后写入
        usage_events。汇总阶段的用量通过共享字典回传，确保覆盖。
        """
        stale_ids = {
            id(getattr(worker, "shared_context", None))
            for worker in self.worker_pool
        }
        synthesis_usage: Dict[str, int] = {}
        try:
            return await self._process_with_swarm_inner(
                question=question,
                context=context,
                assessment=assessment,
                session_id=session_id,
                start_time=start_time,
                on_token=on_token,
                lead_usage=lead_usage,
                patient_context=patient_context,
                memory_scope=memory_scope,
                patient_profile=patient_profile,
                synthesis_usage=synthesis_usage,
                request_started=request_started,
            )
        except (asyncio.CancelledError, LLMError) as exc:
            usage, calls = self._cancelled_usage_snapshot(
                lead_usage, stale_ids, synthesis_usage
            )
            self._attach_usage(exc, usage, calls)
            raise

    def _cancelled_usage_snapshot(
        self,
        lead_usage: Optional[Dict[str, int]] = None,
        stale_ids: Optional[set] = None,
        synthesis_usage: Optional[Dict[str, int]] = None,
    ) -> Tuple[Dict[str, Any], int]:
        """收集本次 Swarm 已消耗的用量：Lead、汇总、科室与失败子任务。"""
        skip = stale_ids or set()
        usage: Dict[str, Any] = dict(lead_usage or {})
        calls = 1 if lead_usage else 0
        if synthesis_usage:
            usage = merge_usages(usage, synthesis_usage)
            calls += 1
        seen = set(skip)
        for worker in self.worker_pool:
            shared = getattr(worker, "shared_context", None)
            if shared is None or id(shared) in seen:
                continue
            seen.add(id(shared))
            for entry in shared.data.get("failed_usage", []) or []:
                if not isinstance(entry, dict):
                    continue
                usage = merge_usages(usage, entry.get("usage"))
                try:
                    calls += max(0, int(entry.get("llm_calls") or 0))
                except (TypeError, ValueError, OverflowError):
                    pass
            for contribution in shared.get_contributions():
                result = getattr(contribution, "result", None) or {}
                usage = merge_usages(usage, result.get("usage"))
                try:
                    calls += max(0, int(result.get("llm_calls") or 0))
                except (TypeError, ValueError, OverflowError):
                    pass
        return usage, calls

    async def _process_with_swarm_inner(
        self,
        question: str,
        context: Optional[Dict[str, Any]],
        assessment: Dict[str, Any],
        session_id: str,
        start_time: datetime,
        on_token: Optional[Callable[[str], None]] = None,
        lead_usage: Optional[Dict[str, int]] = None,
        patient_context: Optional[Dict[str, Any]] = None,
        memory_scope: str = "",
        patient_profile: str = "",
        synthesis_usage: Optional[Dict[str, int]] = None,
        request_started: str = "",
    ) -> Dict[str, Any]:
        """
        使用 Swarm 处理复杂问题

        这是群体智能的核心流程

        注意：context 已经包含了长短期记忆（在 process() 中注入）
        """
        # context 已经包含 recent_history 和 historical_cases，
        # 一并交给 Worker，保证多轮追问能理解指代
        worker_context = dict(patient_context or {})
        for key in ("recent_history", "historical_cases"):
            value = (context or {}).get(key)
            if value:
                worker_context[key] = value

        # 创建 SharedContext
        shared_context = SharedContext(session_id=session_id)
        # Worker 需要原始主诉才能看到具体症状，而不只是分解出的任务描述
        shared_context.data["original_question"] = question
        # 让每个 Worker 在处理子任务时都能看到患者背景与最近对话
        shared_context.data["patient_context"] = worker_context
        if memory_scope:
            shared_context.data["memory_scope"] = memory_scope

        # 附加 SharedContext 到所有 Worker
        for worker in self.worker_pool:
            worker.attach_shared_context(shared_context)

        # 发布 Swarm 启动事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_STARTED,
            source_agent="swarm_coordinator",
            data={
                "question": question,
                "num_subtasks": len(assessment.get("subtasks", []))
            }
        ))

        # Step 1: LeadAgent 分解任务
        subtasks = self.lead_agent.create_subtasks(assessment, shared_context)
        logger.info(f"Created {len(subtasks)} subtasks")

        # Step 2: Worker 执行分配的任务（并行）
        tasks = []
        for worker in self.worker_pool:
            task = asyncio.create_task(
                self._worker_execute_assigned_tasks(worker, shared_context)
            )
            tasks.append(task)

        # 等待所有 Worker 完成（或超时）
        timeout_occurred = False
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=90.0  # 增加超时时间到 90 秒，应对复杂案例
            )
        except asyncio.TimeoutError:
            timeout_occurred = True
            logger.warning("Swarm execution timeout (90s)")
            # 记录哪些 Agent 已完成，哪些未完成
            completed_agents = list(shared_context.agent_contributions.keys())
            unfinished_tasks = [
                (subtask.assigned_agent, subtask.type)
                for subtask in shared_context.task_decomposition.values()
                if subtask.status in (
                    TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS
                )
            ]
            logger.info(f"Completed agents: {completed_agents}")
            logger.info(f"Timed out tasks: {unfinished_tasks}")

            # 超时后仍未结束的子任务标记为失败，让汇总和统计有明确状态
            for subtask in shared_context.task_decomposition.values():
                if subtask.status in (
                    TaskStatus.PENDING, TaskStatus.CLAIMED, TaskStatus.IN_PROGRESS
                ):
                    try:
                        shared_context.fail_subtask(
                            subtask.id, subtask.assigned_agent, "系统超时"
                        )
                    except ValueError:
                        pass

        # Step 3: LeadAgent 汇总结果
        # 即使超时，也尝试汇总已完成的部分结果
        had_contributions = bool(shared_context.get_contributions())
        if synthesis_usage is None:
            synthesis_usage = {}
        raw_answer = await self.lead_agent.synthesize_results(
            question=question,
            shared_context=shared_context,
            timeout_occurred=timeout_occurred,
            on_token=on_token,
            usage_out=synthesis_usage
        )
        # 约束兜底会在道歉前插入「⚠️ 重要提醒」，必须在改写前判定降级
        degraded = is_degraded_answer(raw_answer)
        final_answer = self._enforce_final_constraints(
            raw_answer, question
        )
        if degraded:
            logger.warning(
                "Swarm produced a degraded answer, skip long-term memory "
                f"(session={session_id})"
            )

        end_time = datetime.now()

        # Worker 没有 session_id，不会写短期记忆；由协调器记录本轮问答，
        # 下一次 Swarm 才能拿到上一轮上下文
        if final_answer:
            self.short_term_memory.add_message(
                session_id=session_id, role="user", content=question
            )
            self.short_term_memory.add_message(
                session_id=session_id, role="assistant",
                content=final_answer,
            )

        async def _profile_gone() -> bool:
            """档案在本次会诊开始后被删除：不能再写入其 Mem0 命名空间。"""
            if not patient_profile:
                return False
            names = [patient_profile]
            try:
                resolved = await asyncio.to_thread(
                    self.session_store.resolve_profile_alias,
                    patient_profile,
                )
            except Exception:
                resolved = patient_profile
            if resolved and resolved not in names:
                names.append(resolved)
            started = (
                str(request_started or "").strip()[:40]
                or start_time.isoformat(timespec="seconds")
            )
            for name in names:
                try:
                    if await asyncio.to_thread(
                        self.session_store.profile_deleted_since,
                        name, started,
                    ):
                        return True
                except Exception:
                    continue
            try:
                # 只对解析后的当前名做墓碑判定：改名前的旧名在重建后
                # 改名时也会“有墓碑且无档案行”，会误判为已删除
                resolved = names[-1]
                if await asyncio.to_thread(
                    self.session_store.profile_rebinding_blocked,
                    resolved,
                ):
                    return True
            except Exception:
                pass
            return False

        # Step 4: 生成 SessionSummary
        try:
            summary = SessionSummary.from_shared_context(
                session_id=session_id,
                question=question,
                shared_context=shared_context,
                final_answer=final_answer,
                start_time=start_time,
                end_time=end_time
            )
            if await self._is_session_deleted(session_id):
                # 会话已删除，不再落盘包含主诉的总结文件
                logger.info(
                    f"Session deleted, skip session summary (session={session_id})"
                )
            else:
                await asyncio.to_thread(
                    self.session_manager.save_summary, summary
                )
                # 删除可能发生在检查之后：落盘后再核对并清理
                if await self._is_session_deleted(session_id):
                    await asyncio.to_thread(
                        self.session_manager.delete_summary, session_id
                    )
                    logger.info(
                        "Session deleted during summary save; summary removed "
                        f"(session={session_id})"
                    )
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")

        # 注意：短期记忆已经在 Agent Loop 中保存了，这里不需要重复保存
        # Agent Loop 保存了完整的对话历史（user + assistant + tool messages）

        # 保存到 Mem0 长期记忆
        if (
            degraded
            or await self._is_session_deleted(session_id)
            or await _profile_gone()
        ):
            logger.info(
                f"Session deleted, skip Mem0 summary (session={session_id})"
            )
        else:
            try:
                # 保存会话总结
                memory_user_id = await self._resolved_memory_scope(
                    patient_profile, memory_scope, session_id
                )
                memory_id = await asyncio.to_thread(
                    self.long_term_memory.add_session_summary,
                    session_id=session_id,
                    question=question,
                    answer=final_answer,
                    user_id=memory_user_id,
                    metadata={
                        "mode": "swarm",
                        "agents_count": len(shared_context.agent_contributions),
                        "total_time": (end_time - start_time).total_seconds(),
                        "timeout_occurred": timeout_occurred
                    },
                )

                # 删除可能发生在检查之后：写入后再核对并清理
                if (
                    await self._is_session_deleted(session_id)
                    or await _profile_gone()
                ):
                    await asyncio.to_thread(
                        self.long_term_memory.delete_session_summary,
                        session_id,
                        user_id=memory_user_id,
                    )
                    logger.info(
                        "Session deleted during Mem0 save; memory removed "
                        f"(session={session_id})"
                    )
                elif memory_id:
                    logger.info(f"Saved to Mem0 long-term memory (session={session_id})")
                else:
                    logger.warning(f"Mem0 long-term memory not saved (session={session_id})")

            except Exception as e:
                logger.error(f"Failed to save to Mem0: {e}")

        # 发布 Swarm 完成事件
        shared_context.publish_event(Event(
            type=EventType.SWARM_COMPLETED,
            source_agent="swarm_coordinator",
            data={
                "duration": (end_time - start_time).total_seconds(),
                "agents_count": len(shared_context.agent_contributions)
            }
        ))

        # 返回结果
        completed_agents = list(shared_context.agent_contributions.keys())
        failed_subtasks = [
            subtask
            for subtask in shared_context.task_decomposition.values()
            if subtask.status == TaskStatus.FAILED
        ]

        agent_contributions, sources = self._collect_insights(shared_context)

        failed_usage_entries = shared_context.data.get("failed_usage") or []
        usage_total = merge_usages(
            lead_usage or {},
            synthesis_usage,
            *[entry.get("usage") for entry in agent_contributions],
            *[entry.get("usage") for entry in failed_usage_entries],
        )
        llm_calls = (
            1  # LeadAgent 分解
            + (1 if had_contributions else 0)  # 有贡献时才真正调用汇总
            + sum(int(entry.get("llm_calls") or 0) for entry in agent_contributions)
            + sum(
                int(entry.get("llm_calls") or 0)
                for entry in failed_usage_entries
            )
        )

        result = {
            'answer': final_answer,
            'swarm_enabled': True,
            'degraded': degraded,
            'session_id': session_id,
            'agents_involved': completed_agents,
            'agent_contributions': agent_contributions,
            'sources': sources,
            'usage': usage_total,
            'llm_calls': llm_calls,
            'patient_context': patient_context or {},
            'subtasks_completed': len(shared_context.get_all_completed_subtasks()),
            'subtasks_failed': len(failed_subtasks),
            'total_time': (end_time - start_time).total_seconds(),
            'swarm_metadata': shared_context.get_summary(),
            'timeout_occurred': timeout_occurred
        }

        # 提取建议和免责声明（简化实现）
        result['suggestions'] = self._extract_suggestions(final_answer)

        # 根据是否超时/是否有失败模块调整免责声明
        if not completed_agents:
            reason = "系统响应超时" if timeout_occurred else "分析模块未能完成"
            result['disclaimer'] = (
                f"由于{reason}，未能提供完整分析。"
                "建议简化问题重试，或在紧急情况下立即就医。"
            )
        elif failed_subtasks:
            detail = (
                "部分分析模块超时未完成"
                if timeout_occurred
                else f"{len(failed_subtasks)} 个分析模块未能完成"
            )
            result['disclaimer'] = (
                f"以上分析基于 {len(completed_agents)} 个 Agent 的部分协作结果"
                f"（{detail}），仅供参考，不能替代医生诊断。"
            )
        else:
            result['disclaimer'] = "以上分析基于多个专业 Agent 的协作，仅供参考，不能替代医生诊断。"

        return result

    async def _worker_execute_assigned_tasks(
        self,
        worker: Any,
        shared_context: SharedContext
    ):
        """
        Worker 执行分配给它的任务

        简化后的流程：
        - 查找分配给自己的任务
        - 执行任务
        - 记录结果
        """
        try:
            # 获取分配给该 Agent 的任务
            assigned_tasks = shared_context.get_subtasks_for_agent(worker.agent_id)

            if not assigned_tasks:
                logger.debug(f"{worker.agent_id}: No assigned tasks")
                return

            # 同一个 Worker 复用同一个 AgentLoop 和 LLM 客户端。
            # 子任务串行执行，避免并发请求互相抢占工具预算和 API 速率配额；
            # 不同 Worker 之间仍然并行。
            for subtask in assigned_tasks:
                logger.info(f"{worker.agent_id}: Starting {subtask.type}")
                if not shared_context.start_subtask(subtask.id):
                    logger.warning(
                        f"{worker.agent_id}: SubTask {subtask.id} 已被其他 Worker 认领"
                    )
                    continue

                await self._execute_single_subtask(worker, subtask, shared_context)

        except Exception as e:
            logger.error(f"{worker.agent_id}: Error processing subtask: {e}")

    async def _execute_single_subtask(self, worker, subtask, shared_context):
        """执行单个子任务，失败时记录状态而不是静默吞掉。"""
        try:
            # 显式传入本次请求的 SharedContext：Worker 是长期复用的实例，
            # 并发请求下实例字段会互相覆盖
            result = await worker.process_subtask(
                subtask, shared_context=shared_context
            )
            degraded_reason = ""
            if isinstance(result, dict):
                degraded_reason = str(
                    result.get("warning") or result.get("error") or ""
                )
                if result.get("degraded"):
                    degraded_reason = degraded_reason or "degraded"
            if degraded_reason:
                # 重试耗尽/收尾失败后的道歉文本不是有效科室意见，
                # 记入失败并保留已消耗的 token
                shared_context.data.setdefault(
                    "failed_usage", []
                ).append({
                    "usage": result.get("usage") or {},
                    "llm_calls": int(result.get("llm_calls") or 0),
                })
                try:
                    shared_context.fail_subtask(
                        subtask.id, worker.agent_id, degraded_reason
                    )
                except ValueError as state_error:
                    logger.error(
                        f"记录子任务降级状态时出错: {state_error}"
                    )
                logger.warning(
                    f"{worker.agent_id}: Degraded {subtask.type}: "
                    f"{degraded_reason}"
                )
                return
            shared_context.complete_subtask(subtask.id, worker.agent_id, result)
            logger.info(f"{worker.agent_id}: Completed {subtask.type}")
        except asyncio.CancelledError as e:
            # 超时/断连会取消 gather，Worker 已消耗的 token 仍需计入本轮
            failed_usage = getattr(e, "usage", None)
            failed_calls = int(getattr(e, "llm_calls", 0) or 0)
            if failed_usage or failed_calls:
                shared_context.data.setdefault(
                    "failed_usage", []
                ).append({
                    "usage": failed_usage or {},
                    "llm_calls": failed_calls,
                })
            raise
        except Exception as e:
            logger.error(f"{worker.agent_id}: Error in {subtask.type}: {e}")
            # 失败子任务已消耗的 token 也要计入本轮用量
            failed_usage = getattr(e, "usage", None)
            failed_calls = int(getattr(e, "llm_calls", 0) or 0)
            if failed_usage or failed_calls:
                shared_context.data.setdefault(
                    "failed_usage", []
                ).append({
                    "usage": failed_usage or {},
                    "llm_calls": failed_calls,
                })
            try:
                shared_context.fail_subtask(subtask.id, worker.agent_id, str(e))
            except ValueError as state_error:
                logger.error(f"记录子任务失败状态时出错: {state_error}")

    @staticmethod
    def _collect_insights(shared_context: SharedContext):
        """汇总各科室原始意见、来源与执行统计，供前端展示。"""
        contributions = []
        sources = []
        for contribution in shared_context.get_contributions():
            payload = contribution.result if isinstance(contribution.result, dict) else {}
            content = payload.get("answer") or str(contribution.result)
            subtask = shared_context.get_subtask(contribution.subtask_id)

            duration = None
            if subtask and subtask.started_at and subtask.completed_at:
                duration = round(
                    (subtask.completed_at - subtask.started_at).total_seconds(), 1
                )

            entry = {
                "agent_id": contribution.agent_id,
                "subtask_id": contribution.subtask_id,
                "task_type": subtask.type if subtask else "",
                "content": content,
                "confidence": contribution.confidence,
                "duration": duration,
            }
            if payload.get("tool_calls_used") is not None:
                entry["tool_calls"] = payload["tool_calls_used"]
            if payload.get("iterations") is not None:
                entry["iterations"] = payload["iterations"]
            if payload.get("usage"):
                entry["usage"] = payload["usage"]
            if payload.get("llm_calls") is not None:
                entry["llm_calls"] = payload["llm_calls"]
            contributions.append(entry)

            sources.extend(payload.get("sources") or [])
        return contributions, merge_sources(sources)

    def _extract_suggestions(self, final_answer: str) -> List[str]:
        """从最终答案中提取建议（简化实现）"""
        suggestions = []

        # 简单的文本匹配
        if "【核心建议】" in final_answer:
            # 提取核心建议部分
            start_idx = final_answer.find("【核心建议】")
            end_idx = final_answer.find("【", start_idx + 1)
            if end_idx == -1:
                end_idx = len(final_answer)

            suggestions_text = final_answer[start_idx:end_idx]

            # 提取编号列表
            import re
            matches = re.findall(r'\d+\.\s*([^\n]+)', suggestions_text)
            suggestions = matches[:5]  # 最多5条

        return suggestions or ["请遵循医嘱，注意休息和营养"]

_default_coordinators: Dict[bool, "SwarmCoordinator"] = {}


def get_default_coordinator(enable_swarm: bool = True) -> "SwarmCoordinator":
    """获取进程级复用的 SwarmCoordinator。

    每次调用都新建协调器会重复初始化三个 Agent、重复扫描并动态加载 9 个
    Skills，命令行连续问答时开销明显。按 enable_swarm 缓存，请求之间
    只共享无状态的执行框架，会话数据仍由 ShortTermMemory 按 session 隔离。
    """
    if enable_swarm not in _default_coordinators:
        _default_coordinators[enable_swarm] = SwarmCoordinator(enable_swarm=enable_swarm)
    return _default_coordinators[enable_swarm]


def reset_default_coordinators() -> None:
    """清空协调器缓存（测试或需要重新加载配置时使用）。"""
    _default_coordinators.clear()


async def process_with_swarm(
    question: str,
    context: Optional[Dict[str, Any]] = None,
    enable_swarm: bool = True,
    session_id: Optional[str] = None
) -> Dict[str, Any]:
    """
    便捷函数：使用 Swarm 处理问题

    Args:
        question: 用户问题
        context: 额外上下文
        enable_swarm: 是否启用 Swarm（False 则总是用单 Agent）
        session_id: 会话ID（如果提供，将使用该ID而不是生成新的）

    Returns:
        处理结果
    """
    coordinator = get_default_coordinator(enable_swarm)
    return await coordinator.process(question, context, session_id=session_id)
