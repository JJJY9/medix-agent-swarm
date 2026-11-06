"""
SessionSummary：会话总结和经验提取

每次 Swarm 协作后自动生成会话总结，记录：
- 问题和背景
- 参与的 Agent
- 协作过程
- 关键发现
- 经验教训
- 性能指标

这是群体智能"持续学习"的关键机制
"""
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, List, Optional
import hashlib
import json
import re
import uuid
from loguru import logger


@dataclass
class AgentParticipation:
    """Agent 参与记录"""
    agent_id: str
    role: str  # lead/worker
    subtasks_handled: List[str]
    tool_calls: int
    execution_time: float  # 秒
    contribution_quality: float = 1.0  # 0-1


@dataclass
class KeyFinding:
    """关键发现"""
    category: str  # diagnosis/risk/evidence/treatment
    finding: str
    source_agent: str
    confidence: float = 1.0


@dataclass
class Lesson:
    """经验教训"""
    agent_id: str
    lesson_type: str  # success/failure/improvement
    description: str
    actionable: str  # 可执行的改进措施


@dataclass
class PerformanceMetrics:
    """性能指标"""
    total_time: float  # 总耗时（秒）
    agent_count: int  # 参与 Agent 数量
    parallel_efficiency: float  # 并行效率（0-1）
    information_coverage: float  # 信息覆盖度（0-1）
    redundancy: float  # 信息冗余度（0-1）
    speedup_vs_single: float = 1.0  # 相比单 Agent 的加速比


@dataclass
class SessionSummary:
    """
    会话总结数据类

    记录一次完整的 Swarm 协作过程
    """
    session_id: str
    question: str
    context: Dict[str, Any]
    timestamp: datetime

    # 参与者
    agents_participated: List[AgentParticipation]

    # 过程
    subtasks_created: int
    subtasks_completed: int
    events_count: int

    # 结果
    final_answer: str
    key_findings: List[KeyFinding]

    # 学习
    lessons_learned: List[Lesson]

    # 性能
    performance: PerformanceMetrics

    # 元数据
    swarm_enabled: bool = True
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_markdown(self) -> str:
        """转换为 Markdown 格式"""
        date_str = self.timestamp.strftime("%Y-%m-%d %H:%M:%S")

        lines = [
            f"# Session Summary: {self.session_id}",
            "",
            f"**时间**: {date_str}",
            "",
            "## 问题",
            self.question,
            ""
        ]

        if self.context:
            lines.extend([
                "## 背景",
                "```json",
                json.dumps(self.context, ensure_ascii=False, indent=2),
                "```",
                ""
            ])

        lines.extend([
            "## 参与 Agent",
            ""
        ])

        for agent in self.agents_participated:
            lines.append(f"### {agent.agent_id} ({agent.role})")
            lines.append(f"- 处理子任务：{len(agent.subtasks_handled)} 个")
            lines.append(f"- 工具调用：{agent.tool_calls} 次")
            lines.append(f"- 执行时间：{agent.execution_time:.2f} 秒")
            lines.append("")

        lines.extend([
            "## 协作过程",
            "",
            f"- 创建子任务：{self.subtasks_created} 个",
            f"- 完成子任务：{self.subtasks_completed} 个",
            f"- 发布事件：{self.events_count} 个",
            ""
        ])

        if self.key_findings:
            lines.extend([
                "## 关键发现",
                ""
            ])

            for finding in self.key_findings:
                lines.append(f"### {finding.category.upper()}")
                lines.append(f"**来源**: {finding.source_agent}")
                lines.append(f"**发现**: {finding.finding}")
                lines.append(f"**置信度**: {finding.confidence:.1%}")
                lines.append("")

        lines.extend([
            "## 最终答案",
            "",
            self.final_answer[:500] + ("..." if len(self.final_answer) > 500 else ""),
            ""
        ])

        if self.lessons_learned:
            lines.extend([
                "## 经验教训",
                ""
            ])

            for lesson in self.lessons_learned:
                emoji = "✅" if lesson.lesson_type == "success" else "⚠️" if lesson.lesson_type == "failure" else "💡"
                lines.append(f"### {emoji} {lesson.agent_id}")
                lines.append(f"**{lesson.lesson_type.upper()}**: {lesson.description}")
                if lesson.actionable:
                    lines.append(f"**改进措施**: {lesson.actionable}")
                lines.append("")

        lines.extend([
            "## 性能指标",
            "",
            f"- 总耗时：{self.performance.total_time:.2f} 秒",
            f"- 参与 Agent：{self.performance.agent_count} 个",
            f"- 并行效率：{self.performance.parallel_efficiency:.1%}",
            f"- 信息覆盖度：{self.performance.information_coverage:.1%}",
            f"- 信息冗余度：{self.performance.redundancy:.1%}",
            f"- 加速比：{self.performance.speedup_vs_single:.2f}x",
            ""
        ])

        return "\n".join(lines)

    @classmethod
    def from_shared_context(
        cls,
        session_id: str,
        question: str,
        shared_context: Any,
        final_answer: str,
        start_time: datetime,
        end_time: datetime
    ) -> "SessionSummary":
        """从 SharedContext 构建 SessionSummary"""

        # 计算性能指标
        total_time = (end_time - start_time).total_seconds()

        # 提取 Agent 参与信息
        agents_participated = []
        for agent_id, contributions in shared_context.agent_contributions.items():
            # 优先使用子任务结果里的真实工具调用次数；
            # 只有所有贡献都没提供该字段时才退回成功贡献数，
            # 真实为 0 次不能和“字段缺失”混为一谈
            tool_calls = 0
            has_tool_metric = False
            for contribution in contributions:
                result = contribution.result
                if isinstance(result, dict) and "tool_calls_used" in result:
                    has_tool_metric = True
                    try:
                        tool_calls += int(result.get("tool_calls_used") or 0)
                    except (TypeError, ValueError, OverflowError):
                        pass
            if not has_tool_metric:
                tool_calls = sum(
                    1 for c in contributions
                    if isinstance(c.result, dict) and c.result.get('success', True)
                )

            # 执行时间按该 Agent 名下子任务的起止时间累加，
            # 不再把总耗时平均分给每个 Agent
            execution_seconds = 0.0
            seen_subtasks = set()
            for contribution in contributions:
                if contribution.subtask_id in seen_subtasks:
                    continue
                seen_subtasks.add(contribution.subtask_id)
                subtask = shared_context.get_subtask(contribution.subtask_id)
                if subtask and subtask.started_at and subtask.completed_at:
                    span = (
                        subtask.completed_at - subtask.started_at
                    ).total_seconds()
                    if span > 0:
                        execution_seconds += span

            agents_participated.append(AgentParticipation(
                agent_id=agent_id,
                role="worker",
                subtasks_handled=[c.subtask_id for c in contributions],
                tool_calls=tool_calls,
                execution_time=execution_seconds,
            ))

        # 提取关键发现
        key_findings = []
        for contrib in shared_context.get_contributions():
            if (
                isinstance(contrib.result, dict)
                and "risk_level" in contrib.result
            ):
                key_findings.append(KeyFinding(
                    category="risk",
                    finding=f"风险等级：{contrib.result['risk_level']}",
                    source_agent=contrib.agent_id,
                    confidence=contrib.confidence
                ))

        # 性能指标：优先用子任务的起止时间估算并行收益
        subtasks = list(shared_context.task_decomposition.values())
        completed_subtasks = shared_context.get_all_completed_subtasks()
        information_coverage = (
            len(completed_subtasks) / len(subtasks) if subtasks else 1.0
        )

        work_seconds = 0.0
        for subtask in subtasks:
            if subtask.started_at and subtask.completed_at:
                span = (
                    subtask.completed_at - subtask.started_at
                ).total_seconds()
                if span > 0:
                    work_seconds += span
        agent_count = max(1, len(shared_context.agent_contributions))
        if total_time > 0 and work_seconds > 0:
            speedup = round(work_seconds / total_time, 2)
            parallel_efficiency = min(
                1.0, work_seconds / (total_time * agent_count)
            )
        else:
            speedup = 1.0
            parallel_efficiency = 1.0

        seen_findings = set()
        duplicate_findings = 0
        for finding in key_findings:
            key = (finding.category, finding.finding)
            if key in seen_findings:
                duplicate_findings += 1
            else:
                seen_findings.add(key)
        redundancy = (
            duplicate_findings / len(key_findings) if key_findings else 0.0
        )

        lessons_learned: List[Lesson] = []
        for subtask in subtasks:
            if getattr(subtask.status, "value", "") != "failed":
                continue
            error = ""
            if isinstance(subtask.result, dict):
                error = str(subtask.result.get("error") or "")
            lessons_learned.append(Lesson(
                agent_id=subtask.assigned_agent or "unknown",
                lesson_type="failure",
                description=(
                    f"子任务「{subtask.description or subtask.id}」执行失败"
                    + (f"：{error}" if error else "")
                ),
                actionable="检查该 Agent 的工具调用与输入上下文后重试。",
            ))

        # 性能指标
        performance = PerformanceMetrics(
            total_time=total_time,
            agent_count=agent_count,
            parallel_efficiency=round(parallel_efficiency, 3),
            information_coverage=round(information_coverage, 3),
            redundancy=round(redundancy, 3),
            speedup_vs_single=speedup,
        )

        return cls(
            session_id=session_id,
            question=question,
            context={},
            timestamp=start_time,
            agents_participated=agents_participated,
            subtasks_created=len(shared_context.task_decomposition),
            subtasks_completed=len(shared_context.get_all_completed_subtasks()),
            events_count=len(shared_context.events),
            final_answer=final_answer,
            key_findings=key_findings,
            lessons_learned=lessons_learned,
            performance=performance
        )


class SessionSummaryManager:
    """
    会话总结管理器

    负责保存和检索会话总结
    """

    def __init__(self, base_dir: str = "memory/swarm/session_summaries"):
        self.base_dir = Path(base_dir)
        try:
            self.base_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # 总结是可选功能，目录不可写不应拖垮整个协调器
            logger.warning(f"会话总结目录不可用: {e}")

    def _get_summary_path(
        self, session_id: str, create: bool = True, hashed: bool = True
    ) -> Path:
        """获取会话总结文件路径（会话 ID 只保留安全字符，防止路径穿越）。

        文件名附加原始会话 ID 的短哈希，保证 ``a:b`` 与 ``a_b`` 这类
        清理后相同的 ID 不会互相覆盖；``hashed=False`` 用于兼容旧文件。
        """
        raw_id = str(session_id or "")
        safe_id = re.sub(r"[^\w.-]", "_", raw_id)[:120]
        if not safe_id or safe_id in {".", ".."}:
            safe_id = "unknown"
        date_str = (
            safe_id.split("-")[0] if "-" in safe_id else "unknown"
        )
        date_str = re.sub(r"[^0-9A-Za-z._-]", "_", date_str)[:40]
        if not date_str or date_str in {".", ".."}:
            date_str = "unknown"
        date_dir = self.base_dir / date_str
        if create:
            date_dir.mkdir(parents=True, exist_ok=True)
        if hashed:
            digest = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()[:10]
            filename = f"{safe_id}-{digest}.md"
        else:
            filename = f"{safe_id}.md"
        return date_dir / filename

    def delete_summary(self, session_id: str) -> bool:
        """删除会话总结文件；返回是否真的删除了文件。

        删除后若日期目录已空则一并移除，避免留下空目录。
        旧版本文件名为纯安全化 ID，这里一并清理。
        """
        deleted = False
        for hashed in (True, False):
            try:
                summary_path = self._get_summary_path(
                    session_id, create=False, hashed=hashed
                )
            except OSError as e:
                logger.warning(f"定位会话总结失败: {e}")
                continue
            try:
                summary_path.unlink()
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning(f"删除会话总结失败: {e}")
                continue
            deleted = True
            try:
                summary_path.parent.rmdir()
            except OSError:
                pass
        if deleted:
            logger.info(f"Deleted session summary: {session_id}")
        return deleted

    def save_summary(self, summary: SessionSummary):
        """保存会话总结"""
        temp_path = None
        try:
            summary_path = self._get_summary_path(summary.session_id)
            content = summary.to_markdown()
            # 每次写入使用唯一临时名，多个实例并发保存同一会话也不会写坏
            temp_path = summary_path.with_name(
                f"{summary_path.name}.{uuid.uuid4().hex}.tmp"
            )
            temp_path.write_text(content, encoding="utf-8")
            temp_path.replace(summary_path)
            logger.info(f"Saved session summary: {summary.session_id}")
        except Exception as e:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            logger.error(f"Error saving session summary: {e}")

    def load_summary(self, session_id: str) -> Optional[SessionSummary]:
        """加载会话总结（尚未实现完整反序列化，保留安全占位）。"""
        try:
            summary_path = self._get_summary_path(session_id, create=False)
            if not summary_path.exists():
                return None
            # 这里可以实现从 Markdown 解析回 SessionSummary
            return None
        except Exception as e:
            logger.error(f"Error loading session summary: {e}")
            return None

    def search_similar_sessions(
        self,
        query: str,
        limit: int = 5
    ) -> List[Path]:
        """
        搜索相似的会话（按关键词过滤后返回最近的总结）

        未来可以使用向量相似度搜索
        """
        try:
            limit = max(1, min(int(limit or 5), 50))
        except (TypeError, ValueError):
            limit = 5
        try:
            all_summaries = list(self.base_dir.rglob("*.md"))
        except OSError as e:
            logger.warning(f"读取会话总结目录失败: {e}")
            return []
        query = str(query or "").strip().lower()
        if query:
            matched = []
            for path in all_summaries:
                try:
                    if query in path.read_text(
                        encoding="utf-8", errors="ignore"
                    ).lower():
                        matched.append(path)
                except OSError:
                    continue
            all_summaries = matched
        all_summaries.sort(
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=True,
        )
        return all_summaries[:limit]
