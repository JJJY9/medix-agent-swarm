"""
Agent基类
支持 LLM 驱动的 Skill 调用 + Swarm 协作
"""
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List
from loguru import logger

from core import LLMClient, AgentLoop
from core.context import format_context
from core.skill_registry import SkillRegistry


class BaseAgent(ABC):
    """
    Agent基类
    子类需要实现：
    - get_system_prompt(): 返回系统提示词
    - register_tools(): 注册 Agent 的工具
    - process(): 主入口（可选，默认使用 run_loop）
    """

    def __init__(
        self,
        agent_id: str,
        config: Dict[str, Any],
        llm_client: Optional[LLMClient] = None
    ):
        self.agent_id = agent_id
        self.config = config
        self.llm_client = llm_client or LLMClient(model_type=config.get('model', 'openai_compatible'))
        self.loop = AgentLoop(
            max_iterations=config.get('max_iterations', 10),
            max_tool_calls=config.get('max_tool_calls', 2),
        )

        # Skill 注册表
        self.skill_registry = SkillRegistry()
        self.register_tools()

        # Swarm 协作相关
        self.capabilities: List[str] = []  # 能力标签
        self.shared_context: Optional[Any] = None  # SharedContext 引用
        self.identity_manager: Optional[Any] = None  # AgentIdentityManager 引用

        logger.info(
            f"Initialized {self.__class__.__name__} (id={agent_id}) "
            f"with {len(self.skill_registry.get_all())} skills"
        )

    @abstractmethod
    def get_system_prompt(self) -> str:
        """
        获取系统提示词
        子类必须实现
        """
        pass

    @abstractmethod
    def register_tools(self):
        """注册 Agent 的 Skills（子类必须实现）"""
        pass

    def get_tools_for_llm(self) -> List[Dict[str, Any]]:
        """获取 OpenAI function calling 格式的列表"""
        return self.skill_registry.to_openai_format()

    async def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行 Skill

        Args:
            tool_name: Skill 名称
            arguments: Skill 参数

        Returns:
            Skill 执行结果
        """
        return await self.skill_registry.execute(tool_name, **arguments)

    def format_user_input(self, input_data: Dict[str, Any]) -> str:
        """格式化用户输入：会话信息 + 结构化背景 + 问题。"""
        question = (
            input_data.get('question')
            or input_data.get('query')
            or str(input_data)
        )

        parts = []
        session_id = input_data.get('session_id')
        if session_id:
            parts.append(f"[系统信息] 当前会话ID: {session_id}")

        context_text = format_context(
            input_data.get('context') or {}, include_internal=True
        )
        if context_text:
            parts.append(f"背景信息：\n{context_text}\n")

        parts.append(f"用户问题：{question}")
        return "\n".join(parts)

    async def post_process_result(
        self,
        result: Dict[str, Any],
        final_response: str
    ) -> Dict[str, Any]:
        """
        结果后处理
        子类可以重写来提取结构化信息

        Args:
            result: 初始结果
            final_response: LLM 的最终响应

        Returns:
            处理后的结果
        """
        # 默认不做额外处理
        return result

    async def process(self, input_data: Dict[str, Any], on_token: Optional[Any] = None) -> Dict[str, Any]:
        """
        处理输入数据
        默认实现：运行 Agent Loop
        子类可以重写以实现自定义逻辑
        """
        return await self.run_loop(input_data, on_token=on_token)

    async def run_loop(
        self,
        input_data: Dict[str, Any],
        on_token: Optional[Any] = None,
        persist_memory: bool = True,
    ) -> Dict[str, Any]:
        """运行 Agent Loop"""
        # 提取session_id（如果有）
        session_id = input_data.get('session_id')
        return await self.loop.run(
            self,
            input_data,
            session_id=session_id,
            on_token=on_token,
            persist_memory=persist_memory,
            memory_question=input_data.get('memory_question'),
        )

    # ===== Swarm 协作能力 =====

    def set_capabilities(self, capabilities: List[str]):
        """设置 Agent 的能力标签"""
        self.capabilities = capabilities

    def get_capabilities(self) -> List[str]:
        """获取 Agent 的能力标签"""
        return self.capabilities

    def attach_shared_context(self, shared_context: Any):
        """附加 SharedContext（由 Swarm 调用）"""
        self.shared_context = shared_context

    def attach_identity_manager(self, identity_manager: Any):
        """附加 AgentIdentityManager（由 Swarm 调用）"""
        self.identity_manager = identity_manager

    async def process_subtask(
        self,
        subtask: Any,
        shared_context: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        处理子任务（Swarm 模式）

        子类可以重写以实现自定义逻辑
        默认实现：运行 Agent Loop

        shared_context 由协调器按请求显式传入；Worker 是长期复用的实例，
        不应依赖实例字段，否则并发请求会串用患者背景。
        """
        shared = shared_context if shared_context is not None else self.shared_context

        # 使用 subtask.description 作为输入；带上患者背景信息，
        # 让诊断 / 研究 Agent 也能看到年龄、病程等结构化上下文。
        patient_context = {}
        session_id = None
        if shared is not None:
            patient_context = getattr(shared, "data", {}).get(
                "patient_context", {}
            ) or {}
            session_id = getattr(shared, "session_id", None)

        input_data = {
            'question': subtask.description,
            'subtask_id': subtask.id,
            'subtask_type': subtask.type,
            'context': patient_context,
        }
        if session_id:
            # 模型据此调用 search_history，避免自行编造会话 ID；
            # Worker 不写短期记忆，历史已由协调器注入 context。
            input_data['session_id'] = session_id

        return await self.run_loop(input_data, persist_memory=False)
