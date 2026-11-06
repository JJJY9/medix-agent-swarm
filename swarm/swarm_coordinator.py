"""
SwarmCoordinator：Swarm 入口和智能路由

注意：这不是编排器！
- 只负责路由决策：简单问题 → 单 Agent，复杂问题 → Swarm
- 不控制 Agent 执行
- 不编排任务顺序

类比：交通信号灯，决定车辆走哪条路，但不控制车辆如何行驶
"""
import asyncio
import uuid
from datetime import datetime
from typing import Callable, Dict, Any, Optional, List
from loguru import logger

from core import LLMClient
from core.context import format_context, public_context
from core.triage import detect_red_flags
from core.medication_safety import check_medication_safety
from core.citations import merge_sources
from core.usage import merge_usages
from core.session_context import session_memory_scope
from .shared_context import SharedContext, TaskStatus
from .lead_agent import LeadAgent
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
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[int]:
        """把一轮问答写入 SQLite，供历史列表和重启恢复使用。

        Returns:
            assistant 消息 id；保存失败或答复为空时返回 None。
        """
        if not (answer or "").strip():
            return None
        try:
            return self.session_store.save_exchange(
                session_id, question, answer, metadata=metadata
            )
        except Exception as e:
            logger.error(f"保存会话记录失败（session={session_id}）: {e}")
            return None

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

    async def process(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        session_id: Optional[str] = None,
        on_token: Optional[Callable[[str], None]] = None,
        enable_swarm: Optional[bool] = None
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
        triage = detect_red_flags(question, context)

        # 确定性用药安全预检：过敏史、重复用药与常见相互作用
        medication_safety = check_medication_safety(question, context)

        # 服务重启后从 SQLite 恢复本会话的最近问答
        await self._restore_short_term_context(session_id)

        # ===== 统一的记忆检索（所有模式都使用）=====
        # 1. 检索短期记忆（当前会话历史）
        recent_history = self.short_term_memory.get_recent_messages(
            session_id=session_id,
            limit=10  # 最近5轮对话（10条消息）
        )

        # 2. 检索长期记忆（相似历史会话）
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
            result = await agent.process({
                'question': routed_question,
                'memory_question': question,
                'context': single_context,
                'session_id': session_id
            }, on_token=on_token)
            final_answer = result.get('answer', '')

            result["usage"] = merge_usages(lead_usage, result.get("usage"))
            result["llm_calls"] = int(result.get("llm_calls") or 0) + 1

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
            )
            final_answer = result.get('answer', '')

            # Swarm 的长期记忆已在 _process_with_swarm 中保存；这里补齐 triage 后
            # 再把会诊依据写入 SQLite
            result['triage'] = triage
            result['medication_safety'] = medication_safety
            result['assistant_message_id'] = await asyncio.to_thread(
                self._save_exchange,
                session_id,
                question,
                final_answer,
                metadata=self._history_metadata(result),
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

            result = await self.consultation_agent.process({
                'question': question,
                'context': single_context,
                'session_id': session_id
            }, on_token=on_token)
            final_answer = result.get('answer', '')

            result["usage"] = merge_usages(lead_usage, result.get("usage"))
            result["llm_calls"] = int(result.get("llm_calls") or 0) + 1

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

        # 保存到长期记忆
        if self.session_store.is_session_deleted(session_id):
            # 会诊进行中用户已删除会话：不能再把总结写回外部记忆
            logger.info(f"Session deleted, skip long-term memory (session={session_id})")
        else:
            try:
                memory_id = await asyncio.to_thread(
                    self.long_term_memory.add_session_summary,
                    session_id=session_id,
                    question=question,
                    answer=final_answer,
                    user_id=memory_scope,
                    metadata={
                        "mode": mode,
                        "subtasks_count": len(subtasks),
                        "total_time": (end_time - start_time).total_seconds(),
                    },
                )
                if memory_id:
                    logger.info(f"Saved to long-term memory (session={session_id}, mode={mode})")
                else:
                    logger.warning(f"Long-term memory not saved (session={session_id}, mode={mode})")
            except Exception as e:
                logger.error(f"Failed to save to long-term memory: {e}")

        # 持久化到 SQLite，供历史列表与重启恢复使用
        result['assistant_message_id'] = await asyncio.to_thread(
            self._save_exchange,
            session_id,
            question,
            final_answer,
            metadata=self._history_metadata(result),
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
        synthesis_usage: Dict[str, int] = {}
        final_answer = await self.lead_agent.synthesize_results(
            question=question,
            shared_context=shared_context,
            timeout_occurred=timeout_occurred,
            on_token=on_token,
            usage_out=synthesis_usage
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
            if self.session_store.is_session_deleted(session_id):
                # 会话已删除，不再落盘包含主诉的总结文件
                logger.info(
                    f"Session deleted, skip session summary (session={session_id})"
                )
            else:
                await asyncio.to_thread(
                    self.session_manager.save_summary, summary
                )
        except Exception as e:
            logger.error(f"Failed to generate session summary: {e}")

        # 注意：短期记忆已经在 Agent Loop 中保存了，这里不需要重复保存
        # Agent Loop 保存了完整的对话历史（user + assistant + tool messages）

        # 保存到 Mem0 长期记忆
        if self.session_store.is_session_deleted(session_id):
            logger.info(
                f"Session deleted, skip Mem0 summary (session={session_id})"
            )
        else:
            try:
                # 保存会话总结
                memory_id = await asyncio.to_thread(
                    self.long_term_memory.add_session_summary,
                    session_id=session_id,
                    question=question,
                    answer=final_answer,
                    user_id=memory_scope or session_memory_scope(session_id),
                    metadata={
                        "mode": "swarm",
                        "agents_count": len(shared_context.agent_contributions),
                        "total_time": (end_time - start_time).total_seconds(),
                        "timeout_occurred": timeout_occurred
                    },
                )

                if memory_id:
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
