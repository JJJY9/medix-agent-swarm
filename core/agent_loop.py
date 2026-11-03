"""
Agent循环引擎
实现 LLM 驱动的 Skill 调用循环
支持短期记忆集成
支持约束验证（Harness Engineering）
"""
import asyncio
import uuid
import json
from typing import Dict, Any, List, Optional
from loguru import logger

from .state_manager import StateManager, TaskStatus
from .llm_client import LLMResponse, LLMError

# Harness Engineering: 约束验证和自动修复
try:
    from constraints import ConstraintValidator
    from validation import AutoFixer
    CONSTRAINTS_ENABLED = True
except ImportError:
    logger.warning("Constraints module not found, running without constraint validation")
    CONSTRAINTS_ENABLED = False


class AgentLoop:
    """
    Agent循环引擎
    LLM 自主决策 Skill 调用，循环直到任务完成

    功能：
    - 支持短期记忆（ShortTermMemory）
    - 自动记录每轮的 user/assistant 消息
    """

    def __init__(self, max_iterations: int = 10, short_term_memory: Optional[Any] = None, max_tool_calls: int = 2):
        """
        初始化Agent循环引擎

        Args:
            max_iterations: 最大迭代次数（防止无限循环）
            short_term_memory: 短期记忆管理器（可选）
            max_tool_calls: 最大 Skill 调用次数（硬性限制，默认2次）
        """
        self.max_iterations = max_iterations
        self.max_tool_calls = max_tool_calls
        self.state_manager = StateManager()
        self.short_term_memory = short_term_memory

        # Harness Engineering: 约束验证器和自动修复器
        self.validator = ConstraintValidator() if CONSTRAINTS_ENABLED else None
        self.auto_fixer = AutoFixer() if CONSTRAINTS_ENABLED else None
        if CONSTRAINTS_ENABLED:
            logger.debug("✅ Constraint validation enabled")

    async def run(
        self,
        agent,
        input_data: Dict[str, Any],
        session_id: Optional[str] = None,
        on_token: Optional[Any] = None
    ) -> Dict[str, Any]:
        """
        执行Agent循环

        Args:
            agent: Agent实例
            input_data: 输入数据
            session_id: 会话ID
            on_token: 可选的流式回调；用于强制收尾/最大迭代兜底时逐段推送文本

        Returns:
            最终结果
        """
        task_id = str(uuid.uuid4())
        state = self.state_manager.create_state(
            task_id=task_id,
            agent_id=agent.agent_id,
            input_data=input_data,
            max_iterations=self.max_iterations
        )

        # 工具调用计数属于本次运行，不能放在实例上：同一个 Agent 可能被
        # Swarm 并发派发多个子任务，实例级计数会互相污染。
        tool_call_count = 0

        logger.info(f"Starting Agent Loop for {agent.agent_id}, task_id={task_id}")

        try:
            state.status = TaskStatus.IN_PROGRESS

            # 初始化消息历史（包含历史对话）
            messages = self._initialize_messages(agent, input_data, session_id)

            # 记录用户消息到短期记忆
            if self.short_term_memory and session_id:
                user_message = messages[-1]["content"] if messages else str(input_data)
                self.short_term_memory.add_message(
                    session_id=session_id,
                    role="user",
                    content=user_message
                )
                logger.debug(f"Recorded user message to short-term memory (session={session_id})")

            # 获取 Agent 的 Skills (OpenAI format)
            tools_openai_format = agent.get_tools_for_llm()

            logger.debug(f"Agent has {len(tools_openai_format) if tools_openai_format else 0} skills available")

            # 主循环：LLM → Skill Calls → Results → LLM
            while state.should_continue():
                state.iteration += 1
                logger.debug(f"=== Iteration {state.iteration}/{state.max_iterations} ===")

                try:
                    # 调用 LLM（可能返回 tool_calls）
                    llm_response: LLMResponse = await agent.llm_client.chat_with_tools(
                        messages=messages,
                        tools=tools_openai_format,
                        tool_choice="auto",
                        temperature=agent.config.get('temperature', 0.7)
                    )

                    # 记录中间结果
                    state.add_intermediate_result({
                        'iteration': state.iteration,
                        'llm_response': {
                            'content': llm_response.content,
                            'tool_calls': [
                                {'name': tc.name, 'arguments': tc.arguments}
                                for tc in llm_response.tool_calls
                            ],
                            'finish_reason': llm_response.finish_reason
                        }
                    })

                    # 情况1: LLM 返回 tool_calls，执行 Skills
                    if llm_response.has_tool_calls():
                        # 预算为 0 时不执行任何 Skill，直接让模型给出答案
                        if self.max_tool_calls <= 0:
                            logger.warning("⚠️ Skill 调用预算为 0，直接生成最终答案")
                            final_answer = await self._force_final_answer(agent, messages, on_token)
                            result = await self._finalize(
                                agent, state, final_answer, session_id, tool_call_count
                            )
                            break

                        logger.info(
                            f"LLM requested {len(llm_response.tool_calls)} tool calls "
                            f"(当前已调用 {tool_call_count}/{self.max_tool_calls})"
                        )

                        # 添加 assistant 消息（包含 tool_calls）
                        messages.append(self._create_assistant_message_with_tools(llm_response))

                        # 记录 assistant 消息到短期记忆
                        if self.short_term_memory and session_id:
                            tool_names = [tc.name for tc in llm_response.tool_calls]
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=f"调用工具：{', '.join(tool_names)}"
                            )

                        # 执行每个 Skill 调用
                        for tool_call in llm_response.tool_calls:
                            # 增加计数
                            tool_call_count += 1
                            logger.debug(f"Executing: {tool_call.name}({tool_call.arguments}) - 第 {tool_call_count} 次调用")

                            # Harness Engineering: 验证调用
                            if self.validator:
                                validation_result = self.validator.validate_tool_call(
                                    agent.agent_id,
                                    tool_call.name
                                )
                                if not validation_result.get("valid"):
                                    logger.warning(
                                        f"⚠️ 约束警告: {validation_result.get('reason')}"
                                    )

                            tool_result = await agent.execute_tool(
                                tool_name=tool_call.name,
                                arguments=tool_call.arguments
                            )

                            # 添加结果消息
                            messages.append(
                                agent.llm_client.create_tool_message(
                                    tool_call_id=tool_call.id,
                                    tool_name=tool_call.name,
                                    result=tool_result
                                )
                            )

                            # 记录结果到短期记忆
                            if self.short_term_memory and session_id:
                                result_summary = str(tool_result)[:200]
                                self.short_term_memory.add_message(
                                    session_id=session_id,
                                    role="tool",
                                    content=f"{tool_call.name}: {result_summary}"
                                )

                        # 预算耗尽后立即收尾。若继续带着 tools 进入下一轮，
                        # 模型往往会再次请求调用工具，白白空转到 max_iterations。
                        if tool_call_count >= self.max_tool_calls:
                            logger.warning(
                                f"⚠️ 已达到最大 Skill 调用次数限制 ({self.max_tool_calls})，强制生成最终答案"
                            )
                            final_answer = await self._force_final_answer(agent, messages, on_token)
                            result = await self._finalize(
                                agent, state, final_answer, session_id, tool_call_count
                            )
                            break

                        # 继续下一轮循环
                        continue

                    # 情况2: LLM 返回文本响应，任务完成
                    else:
                        logger.info(f"LLM provided final response (no tool calls)")
                        result = await self._finalize(
                            agent, state, llm_response.content or "", session_id, tool_call_count
                        )
                        break

                except LLMError as e:
                    logger.error(
                        f"LLM error in iteration {state.iteration}: "
                        f"{e.code} - {e.user_message}"
                    )
                    if not e.retryable:
                        # Key 无效、余额不足等错误重试没有意义，直接向上抛
                        state.mark_failed(e.user_message)
                        raise
                    if state.iteration >= state.max_iterations:
                        state.mark_failed(e.user_message)
                        break
                    await asyncio.sleep(min(2 ** state.iteration, 8))
                except Exception as e:
                    logger.error(f"Error in iteration {state.iteration}: {e}")
                    if state.iteration >= state.max_iterations:
                        state.mark_failed(str(e))
                        break
                    # 否则继续尝试

            # 如果达到最大迭代次数但没有完成
            if not state.is_completed():
                logger.warning(f"Max iterations reached without completion")

                # 强制调用 LLM 生成最终总结
                try:
                    logger.info("Forcing LLM to provide final answer")

                    # 添加强制总结的提示
                    messages.append({
                        'role': 'user',
                        'content': '请基于以上信息，提供最终的答复。'
                    })

                    # 调用 LLM（禁用 function calling）
                    final_text = await self._complete_text(agent, messages, on_token)

                    result = {
                        'answer': final_text or '抱歉，未能完成任务',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached'
                    }

                    # 记录最终回答到短期记忆
                    if self.short_term_memory and session_id:
                        self.short_term_memory.add_message(
                            session_id=session_id,
                            role="assistant",
                            content=result['answer']
                        )

                    state.mark_completed(result)
                    logger.info("Generated fallback answer after max iterations")

                except Exception as e:
                    logger.error(f"Failed to generate fallback answer: {e}")
                    # 降级到简单提取
                    result = {
                        'answer': '抱歉，系统在处理您的问题时遇到了问题。建议您简化问题或稍后重试。',
                        'iterations': state.iteration,
                        'warning': 'max_iterations_reached',
                        'error': str(e)
                    }
                    state.mark_completed(result)

            logger.info(f"Agent Loop finished: status={state.status.value}, iterations={state.iteration}")
            return state.final_result or {}

        except Exception as e:
            logger.error(f"Agent Loop failed: {e}")
            state.mark_failed(str(e))
            raise

    async def _force_final_answer(
        self,
        agent,
        messages: List[Dict[str, Any]],
        on_token: Optional[Any] = None
    ) -> str:
        """禁用工具，要求模型基于已有上下文给出最终答复。"""
        messages.append({
            'role': 'user',
            'content': (
                '信息检索次数已达上限。请不要再请求调用工具，'
                '直接基于以上已有信息给出最终答复。'
            )
        })

        return (await self._complete_text(agent, messages, on_token)).strip()

    async def _complete_text(
        self,
        agent,
        messages: List[Dict[str, Any]],
        on_token: Optional[Any] = None
    ) -> str:
        """生成最终文本：有流式回调时逐段推送，否则回退到普通调用。"""
        if on_token is not None and hasattr(agent.llm_client, "chat_stream"):
            return await agent.llm_client.chat_stream(messages, on_token=on_token)

        response: LLMResponse = await agent.llm_client.chat_with_tools(
            messages=messages,
            tools=None,
            temperature=agent.config.get('temperature', 0.7)
        )
        return response.content or ""

    async def _finalize(
        self,
        agent,
        state,
        final_answer: Optional[str],
        session_id: Optional[str],
        tool_calls_used: int
    ) -> Dict[str, Any]:
        """执行输出约束校验/自动修复，写入短期记忆并产出最终结果。"""
        final_answer = (final_answer or "").strip()

        # Harness Engineering: 验证和修复输出
        if self.validator and final_answer:
            validation_result = self.validator.validate_output(
                agent.agent_id,
                final_answer
            )

            if not validation_result.get("valid"):
                logger.warning(
                    f"⚠️ 输出约束违规: {validation_result.get('violations')}"
                )

                # 自动修复
                if self.auto_fixer and validation_result.get("auto_fixable"):
                    fixed_answer = self.auto_fixer.fix_output(
                        final_answer,
                        validation_result.get("auto_fixable", [])
                    )
                    if fixed_answer != final_answer:
                        logger.info("🔧 输出已自动修复")
                        final_answer = fixed_answer

        # 记录最终回答到短期记忆
        if self.short_term_memory and session_id:
            self.short_term_memory.add_message(
                session_id=session_id,
                role="assistant",
                content=final_answer or "(empty response)"
            )
            logger.debug(f"Recorded final answer to short-term memory (session={session_id})")

        result = {
            'answer': final_answer,
            'iterations': state.iteration,
            'agent_id': agent.agent_id,
            'tool_calls_used': tool_calls_used
        }

        # 让 Agent 进行结果后处理（如提取建议等）
        if hasattr(agent, 'post_process_result'):
            result = await agent.post_process_result(result, final_answer)

        state.mark_completed(result)
        return result

    def _initialize_messages(self, agent, input_data: Dict[str, Any], session_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """初始化消息列表，包含历史对话上下文"""
        messages = []

        # 系统提示词
        system_prompt = agent.get_system_prompt()
        if system_prompt:
            messages.append({
                'role': 'system',
                'content': system_prompt
            })

        # 加载历史对话（短期记忆）
        if self.short_term_memory and session_id:
            history = self.short_term_memory.get_history(session_id, limit=5)  # 最近5轮对话
            if history:
                logger.info(f"Loaded {len(history)} historical messages from short-term memory")
                messages.extend(history)

        # 用户输入
        user_message = agent.format_user_input(input_data)
        messages.append({
            'role': 'user',
            'content': user_message
        })

        return messages

    def _create_assistant_message_with_tools(self, llm_response: LLMResponse) -> Dict[str, Any]:
        """创建包含 tool_calls 的 assistant 消息"""
        message = {
            'role': 'assistant',
            'content': llm_response.content or None
        }

        # 添加 tool_calls（OpenAI 格式）
        if llm_response.tool_calls:
            message['tool_calls'] = [
                {
                    'id': tc.id,
                    'type': 'function',
                    'function': {
                        'name': tc.name,
                        'arguments': json.dumps(tc.arguments, ensure_ascii=False)
                    }
                }
                for tc in llm_response.tool_calls
            ]

        return message
