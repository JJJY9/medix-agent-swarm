"""
LeadAgent：任务分解和结果汇总

注意：LeadAgent 不是编排器！
- 只负责分解任务和汇总结果
- 不控制 Worker 的执行顺序
- 不直接调用 Worker
- Worker 自主认领任务并执行
"""
import json
import re
import uuid
from typing import Any, Callable, Dict, List, Optional
from loguru import logger

from core.llm_client import LLMClient, LLMError
from .shared_context import SharedContext, SubTask, TaskStatus
from .events import Event, EventType


#: 单个问题最多拆出的子任务数（与 constraints/swarm_constraints.yaml 的
#: max_parallel_tasks 保持一致，避免模型把任务拆得过碎）。
MAX_SUBTASKS = 5

KNOWN_AGENT_IDS = ("consultation_agent", "diagnostic_agent", "research_agent")

# 模型可能返回类名、短名或中文名，这里统一映射到内部 agent_id。
_AGENT_ALIASES = {
    "consultation_agent": "consultation_agent",
    "consultation": "consultation_agent",
    "consultationagent": "consultation_agent",
    "health_consultation": "consultation_agent",
    "健康咨询": "consultation_agent",
    "健康咨询专家": "consultation_agent",
    "consultation_expert": "consultation_agent",
    "diagnostic_agent": "diagnostic_agent",
    "diagnostic": "diagnostic_agent",
    "diagnosticagent": "diagnostic_agent",
    "diagnosis": "diagnostic_agent",
    "诊断": "diagnostic_agent",
    "诊断推理专家": "diagnostic_agent",
    "症状诊断": "diagnostic_agent",
    "research_agent": "research_agent",
    "research": "research_agent",
    "researchagent": "research_agent",
    "researcher": "research_agent",
    "循证医学": "research_agent",
    "循证医学专家": "research_agent",
    "医学研究": "research_agent",
}


def normalize_agent_id(raw_agent: Any) -> Optional[str]:
    """把模型输出的 Agent 名称归一化为内部 agent_id。

    Returns:
        已知 agent_id；无法识别时返回 None。
    """
    if not isinstance(raw_agent, str) or not raw_agent.strip():
        return None

    key = raw_agent.strip().lower().replace("-", "_").replace(" ", "_")
    if key in _AGENT_ALIASES:
        return _AGENT_ALIASES[key]

    compact = key.replace("_", "")
    if compact in _AGENT_ALIASES:
        return _AGENT_ALIASES[compact]

    if key in KNOWN_AGENT_IDS:
        return key

    return None


def extract_json_payload(content: Optional[str]) -> Optional[Any]:
    """从 LLM 回复中提取第一个包含 subtasks 的 JSON 对象或数组。

    模型经常在 JSON 前后附带解释文字，或者用 ```json 代码块包裹，
    直接 re.search(r'\\{.*\\}') 在贪婪匹配下容易把尾随文本也吞掉。
    这里先去掉代码块围栏，再用 JSONDecoder 从每个可能的起始位置尝试解析。
    """
    if not content:
        return None

    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    text = text.strip()

    decoder = json.JSONDecoder()
    for index, char in enumerate(text):
        if char not in "{[":
            continue
        try:
            payload, _ = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue

        if isinstance(payload, dict) and "subtasks" in payload:
            return payload
        if isinstance(payload, list) and all(
            isinstance(item, dict) for item in payload
        ):
            # 只有元素看起来像子任务时才认这个数组；
            # 否则继续向后扫描，避免前面的说明性数组遮住真正的 subtasks
            if any(
                item.get("description")
                or item.get("task")
                or item.get("desc")
                for item in payload
            ):
                return {"subtasks": payload}

    return None


def normalize_subtasks(raw_subtasks: Any, max_subtasks: int = MAX_SUBTASKS) -> List[Dict[str, str]]:
    """校验并归一化 LeadAgent 分解出的子任务列表。

    丢弃缺少描述的条目；未知 Agent 改派给 ConsultationAgent；
    超过上限时截断，保证后续流程不会因模型输出不规范而崩溃。
    """
    if isinstance(raw_subtasks, dict):
        raw_subtasks = [raw_subtasks]
    if not isinstance(raw_subtasks, list):
        return []

    normalized: List[Dict[str, str]] = []
    for item in raw_subtasks:
        if not isinstance(item, dict):
            continue

        description = item.get("description") or item.get("task") or item.get("desc")
        if not isinstance(description, str) or not description.strip():
            logger.warning(f"丢弃缺少 description 的子任务: {item!r}")
            continue

        raw_agent = (
            item.get("assigned_agent")
            or item.get("agent")
            or item.get("assigned_to")
        )
        agent_id = normalize_agent_id(raw_agent)
        if agent_id is None:
            logger.warning(f"未知 Agent {raw_agent!r}，子任务改派给 consultation_agent")
            agent_id = "consultation_agent"

        normalized.append({
            "description": description.strip(),
            "assigned_agent": agent_id,
        })

        if len(normalized) >= max_subtasks:
            logger.warning(f"子任务数量超过上限 {max_subtasks}，已截断")
            break

    return normalized


class LeadAgent:
    """
    Lead Agent：任务协调者

    职责：
    1. 评估问题复杂度
    2. 分解复杂任务为独立子任务
    3. 等待 Worker 完成
    4. 汇总所有结果

    不做：
    - 不编排执行顺序
    - 不分配任务给特定 Agent
    - 不控制 Worker 行为
    """

    def __init__(self, llm_client: Optional[LLMClient] = None):
        self.agent_id = "lead_agent"
        self.llm_client = llm_client or LLMClient()

    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return """你是医疗 Swarm 的 Lead Agent。你的职责是**分析问题并分配给合适的 Worker Agent**。

**核心原则**：
1. **尽量少分配任务**：能用 1 个 Agent 解决的，不要用 2 个；能用 2 个的，不要用 3 个；**最多 5 个子任务**，超出的会被丢弃
2. **优先使用 ConsultationAgent**：对于常见病症（感冒、发烧、咳嗽等）、健康科普，单独使用 ConsultationAgent 就足够
3. 你**只负责分配 Agent**，不决定具体使用哪些工具/技能（Worker Agent 会自己选择）
4. 子任务应该相对独立，可以并行执行

---

## 可用的 Worker Agents

### 1. ConsultationAgent（健康咨询专家）
**擅长**：
- 常见疾病科普和健康建议
- 症状初步评估和风险分级
- 生活方式指导（饮食、运动、睡眠）
- 日常健康管理

**适用场景**：
- 简单症状咨询（"我感冒了""头痛怎么办"）
- 健康科普（"什么是高血压""多喝水的好处"）
- 预防建议（"如何预防感冒"）
- 生活方式指导（"高血压患者饮食注意什么"）

---

### 2. DiagnosticAgent（诊断推理专家）
**擅长**：
- 症状模式分析和关联性评估
- 鉴别诊断推理
- 疾病编码和分类（ICD-10）
- 复杂症状的风险评估

**适用场景**：
- 复杂症状分析（"头痛+恶心+视力模糊"）
- 多系统问题（"胸闷气短冒冷汗，严重吗"）
- 症状持续加重（"头痛一周了越来越严重"）
- 需要鉴别诊断的情况

---

### 3. ResearchAgent（循证医学专家）
**擅长**：
- 临床指南和诊疗规范检索
- 最新医学研究和证据综合
- 权威治疗方案查询
- 文献支持和证据等级评估

**适用场景**：
- 需要权威指南（"高血压最新诊疗指南"）
- 询问标准治疗方案（"糖尿病如何治疗"）
- 需要最新医学进展
- 需要循证医学证据支持

---

## 任务分配策略

### 策略 1：简单问题 → 1 个 Agent（ConsultationAgent）
**问题特征**：
- 单一常见症状（感冒、发烧、头痛、咳嗽）
- 健康科普和预防建议
- 一般性健康咨询

**示例**：
- "我感冒了怎么办？" → ConsultationAgent
- "什么是高血压？" → ConsultationAgent
- "如何预防流感？" → ConsultationAgent
- "糖尿病患者饮食注意什么？" → ConsultationAgent

---

### 策略 2：复杂症状 → 2 个 Agents（DiagnosticAgent + ConsultationAgent）
**问题特征**：
- 多个症状组合（3个以上不同症状）
- 症状持续时间长或加重
- 明确询问严重程度或是否需要就医
- 有既往病史或用药史

**示例**：
- "头痛一周了越来越严重，需要就医吗？" → DiagnosticAgent (评估风险) + ConsultationAgent (处理建议)
- "胸闷气短冒冷汗，严重吗？" → DiagnosticAgent (症状分析) + ConsultationAgent (建议)

---

### 策略 3：需要权威指南 → 2-3 个 Agents
**问题特征**：
- 询问疾病治疗方案
- 需要标准诊疗规范
- 需要权威指南和生活建议的综合方案

**示例**：
- "高血压如何治疗？" → ResearchAgent (指南) + ConsultationAgent (生活建议)
- "糖尿病最新诊疗指南是什么？" → ResearchAgent

---

## 输出格式（JSON）

**重要**：输出中**不需要 `type` 字段**，只需要 `description`（任务描述）和 `assigned_agent`（分配的 Agent）

### 示例 1：简单问题（1 个 Agent）
```json
{
  "subtasks": [
    {
      "description": "回答用户关于感冒的咨询，提供处理建议和注意事项",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

### 示例 2：复杂症状（2 个 Agents）
```json
{
  "subtasks": [
    {
      "description": "评估头痛症状的风险等级、紧急程度，分析症状模式和可能原因",
      "assigned_agent": "diagnostic_agent"
    },
    {
      "description": "提供头痛的处理建议、缓解方法和注意事项",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

### 示例 3：需要指南（2 个 Agents）
```json
{
  "subtasks": [
    {
      "description": "检索高血压的最新临床诊疗指南和标准治疗方案",
      "assigned_agent": "research_agent"
    },
    {
      "description": "提供高血压患者的日常生活管理建议（饮食、运动、用药）",
      "assigned_agent": "consultation_agent"
    }
  ]
}
```

---

## 关键要点

1. **只写 `description` 和 `assigned_agent`**
2. **description 要具体**：明确说明这个 Agent 需要做什么
3. **Agent 会自主选择工具**：你不需要指定使用哪个工具/技能
4. **尽量少分配**：1 个 Agent 能搞定的，不要分配 2 个
5. **任务要独立**：各个 Agent 的任务应该可以并行执行
"""

    async def assess_and_decompose(
        self,
        question: str,
        context: Optional[Dict[str, Any]] = None,
        usage_out: Optional[Dict[str, int]] = None
    ) -> Dict[str, Any]:
        """
        分析问题并分解为子任务

        返回：
        - subtasks: List[SubTask] - 子任务列表
          每个子任务包含：type（工具名）、description（描述）、assigned_agent（负责的Agent）
        """
        messages = [
            {"role": "system", "content": self._get_system_prompt()},
            {"role": "user", "content": f"问题：{question}\n\n背景：{context or '无'}"}
        ]

        try:
            if hasattr(self.llm_client, "chat_with_usage_retry"):
                content, usage = await self.llm_client.chat_with_usage_retry(messages)
            elif hasattr(self.llm_client, "chat_with_usage"):
                content, usage = await self.llm_client.chat_with_usage(messages)
            else:
                content, usage = await self.llm_client.chat(messages), None

            if usage_out is not None and usage:
                usage_out.update(usage)

            logger.debug(f"LeadAgent assessment: {(content or '')[:200]}...")

            payload = extract_json_payload(content)
            if not isinstance(payload, dict):
                logger.warning("LeadAgent 返回内容中没有可解析的 JSON，回退到单 Agent")
                return self._fallback_assessment(
                    "无法解析 LLM 响应，默认使用 ConsultationAgent"
                )

            subtasks = normalize_subtasks(payload.get("subtasks"))
            if not subtasks:
                logger.warning("LeadAgent 没有给出有效子任务，回退到单 Agent")
                return self._fallback_assessment(
                    "LLM 未给出有效子任务，默认使用 ConsultationAgent"
                )

            return {
                "subtasks": subtasks,
                "reason": payload.get("reason", "由 LeadAgent 分解")
            }

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"LeadAgent assessment error: {e}")
            return {
                "subtasks": [],
                "reason": f"评估失败：{e}"
            }

    @staticmethod
    def _fallback_assessment(reason: str) -> Dict[str, Any]:
        """解析失败时交给单个 ConsultationAgent，避免整条请求失败。"""
        return {
            "subtasks": [{
                "description": "回答用户问题，并提供必要的健康建议",
                "assigned_agent": "consultation_agent"
            }],
            "reason": reason
        }

    def create_subtasks(
        self,
        decomposition_result: Dict[str, Any],
        shared_context: SharedContext
    ) -> List[SubTask]:
        """
        根据分解结果创建 SubTask 并发布到 SharedContext

        直接指定 assigned_agent（中心化分配）
        """
        subtasks_data = decomposition_result.get("subtasks", [])
        subtasks = []

        for data in subtasks_data:
            if not isinstance(data, dict):
                logger.warning(f"跳过非字典子任务: {data!r}")
                continue

            description = data.get("description") or data.get("task") or "回答用户问题"
            description = str(description).strip()

            raw_agent = data.get("assigned_agent") or data.get("agent")
            assigned_agent = normalize_agent_id(raw_agent)
            if assigned_agent is None:
                logger.warning(f"未知 Agent {raw_agent!r}，改派给 consultation_agent")
                assigned_agent = "consultation_agent"

            # 自动推断 type（基于 assigned_agent，向后兼容）
            # LeadAgent 不再输出 type 字段，这里根据 Agent 生成通用 type
            inferred_type = data.get("type") or f"{assigned_agent}_task"

            subtask = SubTask(
                id=str(uuid.uuid4()),
                type=inferred_type,
                description=description,
                assigned_agent=assigned_agent
            )

            shared_context.add_subtask(subtask)
            subtasks.append(subtask)

            logger.info(
                f"Created SubTask: {subtask.type} "
                f"(assigned to: {subtask.assigned_agent})"
            )

            if len(subtasks) >= MAX_SUBTASKS:
                logger.warning(f"子任务数量达到上限 {MAX_SUBTASKS}，忽略其余任务")
                break

        return subtasks

    async def wait_for_completion(
        self,
        shared_context: SharedContext,
        timeout: float = 30.0
    ) -> bool:
        """
        等待所有子任务完成

        这不是"主动控制"，而是"被动等待"
        Worker 自主完成任务，Lead 只是等待
        """
        import asyncio

        start_time = asyncio.get_event_loop().time()

        while True:
            if shared_context.is_all_subtasks_completed():
                logger.info("All subtasks completed")
                return True

            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.warning(f"Timeout waiting for subtasks ({timeout}s)")
                return False

            await asyncio.sleep(0.5)  # 每 0.5 秒检查一次

    async def synthesize_results(
        self,
        question: str,
        shared_context: SharedContext,
        timeout_occurred: bool = False,
        on_token: Optional[Callable[[str], None]] = None,
        usage_out: Optional[Dict[str, int]] = None
    ) -> str:
        """
        汇总所有 Agent 的贡献，生成最终答案

        这是 Lead Agent 的核心价值：整合多个视角

        Args:
            question: 用户问题
            shared_context: 共享上下文
            timeout_occurred: 是否发生超时
            on_token: 可选的流式回调，汇总过程中逐段推送文本
        """
        # 收集所有贡献
        all_contributions = shared_context.get_contributions()

        if not all_contributions:
            # 如果没有任何贡献
            if timeout_occurred:
                return """抱歉，由于系统响应超时，所有 Agent 均未能在规定时间内完成分析。

【建议】：
- 您的问题可能比较复杂，建议简化问题后重试
- 或者将问题拆分为多个小问题分别咨询

【紧急情况】：
如果您的症状严重或紧急，请立即就医或拨打急救电话，不要依赖在线咨询。"""
            else:
                return "抱歉，Swarm 未能提供有效分析结果。"

        # 构建汇总提示
        contributions_text = []
        completed_agents = []
        for contrib in all_contributions:
            subtask = shared_context.get_subtask(contrib.subtask_id)
            contributions_text.append(
                f"**{contrib.agent_id}** ({subtask.type if subtask else '未知'}):\n"
                f"{contrib.result}"
            )
            completed_agents.append(contrib.agent_id)

        # 未完成模块（超时或出错）都要在汇总里说明，不能只处理超时
        all_subtasks = list(shared_context.task_decomposition.values())
        incomplete_tasks = [
            subtask.type for subtask in all_subtasks
            if subtask.status.value != "completed"
        ]
        timeout_note = ""
        if incomplete_tasks:
            reason = (
                "由于系统响应超时"
                if timeout_occurred
                else "由于部分分析模块出错"
            )
            timeout_note = f"""

**注意**：{reason}，以下分析模块未能完成：{', '.join(incomplete_tasks)}
以下是基于已完成的 {len(completed_agents)} 个 Agent 的部分分析结果。"""

        synthesis_prompt = f"""你是医疗 Swarm 的 Lead Agent，负责汇总多个专业 Agent 的分析结果。

**用户问题**：{question}

**Agent 贡献**：
{chr(10).join(contributions_text)}{timeout_note}

**任务**：
整合以上所有分析，生成一个全面、专业的最终答案。

**要求**：
1. 综合所有 Agent 的观点
2. 突出多角度分析的优势
3. 保持医疗建议的严谨性
4. 包含【风险评估】【诊断分析】【医学证据】等模块（如果相关 Agent 提供了）
5. 给出【核心建议】
6. 添加【免责声明】
{"7. 如果有分析模块未完成，在答案中明确说明" if incomplete_tasks else ""}

**输出格式**：
【风险评估】 (如果有)
...

【诊断分析】 (如果有)
...

【医学证据】 (如果有)
...

【核心建议】
1. ...
2. ...

【免责声明】
...
"""

        try:
            messages = [{"role": "user", "content": synthesis_prompt}]

            if on_token is not None and hasattr(self.llm_client, "chat_stream_retry"):
                stream_usage: Dict[str, int] = {}
                text = await self.llm_client.chat_stream_retry(
                    messages, on_token=on_token, usage_out=stream_usage
                )
                if usage_out is not None:
                    usage_out.update(stream_usage)
                return text

            if on_token is not None and hasattr(self.llm_client, "chat_stream"):
                stream_usage = {}
                text = await self.llm_client.chat_stream(
                    messages, on_token=on_token, usage_out=stream_usage
                )
                if usage_out is not None:
                    usage_out.update(stream_usage)
                return text

            if hasattr(self.llm_client, "chat_with_usage_retry"):
                response, usage = await self.llm_client.chat_with_usage_retry(messages)
                if usage_out is not None and usage:
                    usage_out.update(usage)
            elif hasattr(self.llm_client, "chat_with_usage"):
                response, usage = await self.llm_client.chat_with_usage(messages)
                if usage_out is not None and usage:
                    usage_out.update(usage)
            else:
                response = await self.llm_client.chat(messages)

            return response

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"Synthesis error: {e}")
            # 不要把内部异常文本当作会诊意见持久化/展示
            return "抱歉，汇总会诊意见时出现系统错误，请稍后重试。"
