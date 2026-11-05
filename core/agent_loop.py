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
from .citations import merge_sources
from .session_context import session_memory_scope, use_memory_scope
from .usage import merge_usage

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
        on_token: Optional[Any] = None,
        persist_memory: bool = True,
        memory_question: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        执行Agent循环

        Args:
            agent: Agent实例
            input_data: 输入数据
            session_id: 会话ID
            on_token: 可选的流式回调；用于强制收尾/最大迭代兜底时逐段推送文本
            persist_memory: 是否读写短期记忆；Swarm Worker 传 False，
                避免把内部工具消息写进用户会话或重复加载历史
            memory_question: 写入短期记忆的用户问题；默认取 input_data 中的
                question。单 Agent 路由会增强 prompt，但记忆应保留原始问题

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
        sources: List[Dict[str, Any]] = []
        usage: Dict[str, int] = {}
        llm_calls = 0
        user_question = (
            memory_question
            or input_data.get('memory_question')
            or input_data.get('question')
            or input_data.get('query')
            or str(input_data)
        )
        context_data = input_data.get('context')
        memory_scope = ""
        if isinstance(context_data, dict):
            memory_scope = str(context_data.get("memory_scope") or "").strip()
        if not memory_scope and session_id:
            # 会话 ID 可能由客户端自定义，同样不能把原文发给外部记忆服务
            memory_scope = session_memory_scope(session_id)
        memory_scope = memory_scope[:120]

        logger.info(f"Starting Agent Loop for {agent.agent_id}, task_id={task_id}")

        try:
            state.status = TaskStatus.IN_PROGRESS

            # 初始化消息历史（包含历史对话）
            messages = self._initialize_messages(
                agent, input_data, session_id, load_history=persist_memory
            )

            # 用户消息与助手回答统一在 _finalize 收尾成功后写入短期记忆；
            # 提前写入会在取消/失败/收尾异常时留下没有回答的孤立提问

            # 获取 Agent 的 Skills (OpenAI format)
            tools_openai_format = agent.get_tools_for_llm()

            logger.debug(f"Agent has {len(tools_openai_format) if tools_openai_format else 0} skills available")

            # 主循环：LLM → Skill Calls → Results → LLM
            finalizing = False
            while state.should_continue():
                state.iteration += 1
                finalizing = False
                logger.debug(f"=== Iteration {state.iteration}/{state.max_iterations} ===")

                try:
                    # 调用 LLM（可能返回 tool_calls）
                    llm_response: LLMResponse = await agent.llm_client.chat_with_tools(
                        messages=messages,
                        tools=tools_openai_format,
                        tool_choice="auto",
                        temperature=agent.config.get('temperature', 0.7)
                    )
                    llm_calls += 1
                    merge_usage(usage, llm_response.usage)

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
                            llm_calls += 1
                            final_answer = await self._force_final_answer(
                                agent, messages, on_token, usage
                            )
                            finalizing = True
                            result = await self._finalize(
                                agent, state, final_answer, session_id,
                                tool_call_count, sources, usage, llm_calls,
                                remember=persist_memory,
                                user_input=user_question,
                            )
                            break

                        logger.info(
                            f"LLM requested {len(llm_response.tool_calls)} tool calls "
                            f"(当前已调用 {tool_call_count}/{self.max_tool_calls})"
                        )

                        # 添加 assistant 消息（包含 tool_calls）
                        messages.append(self._create_assistant_message_with_tools(llm_response))

                        # 记录 assistant 消息到短期记忆
                        if self.short_term_memory and session_id and persist_memory:
                            tool_names = [tc.name for tc in llm_response.tool_calls]
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="assistant",
                                content=f"调用工具：{', '.join(tool_names)}"
                            )

                        # 执行每个 Skill 调用（超出预算的调用返回错误结果，
                        # 保证每条 tool_call 都有配对的 tool 消息）
                        remaining_budget = max(
                            0, self.max_tool_calls - tool_call_count
                        )
                        to_execute = llm_response.tool_calls[:remaining_budget]
                        to_skip = llm_response.tool_calls[remaining_budget:]
                        for tool_call in to_execute:
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

                            try:
                                with use_memory_scope(memory_scope):
                                    tool_result = await agent.execute_tool(
                                        tool_name=tool_call.name,
                                        arguments=tool_call.arguments
                                    )
                            except Exception as e:
                                # 任何 Skill 异常都要变成一条 tool 响应，
                                # 否则历史里会留下没有结果的 tool_calls，下一轮 API 直接 400
                                logger.error(f"Skill {tool_call.name} execution failed: {e}")
                                tool_result = {"success": False, "error": str(e)}

                            # 收集 Skill 返回的知识库来源，最终随意见一起展示
                            if isinstance(tool_result, dict):
                                tool_sources = tool_result.get("sources")
                                if isinstance(tool_sources, list):
                                    sources.extend(tool_sources)

                            # 添加结果消息
                            try:
                                tool_message = agent.llm_client.create_tool_message(
                                    tool_call_id=tool_call.id,
                                    tool_name=tool_call.name,
                                    result=tool_result
                                )
                            except Exception as e:
                                logger.error(f"Failed to serialize tool result: {e}")
                                tool_message = {
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "name": tool_call.name,
                                    "content": json.dumps(
                                        {"error": str(e)}, ensure_ascii=False
                                    ),
                                }
                            messages.append(tool_message)

                            # 记录结果到短期记忆
                            if self.short_term_memory and session_id and persist_memory:
                                result_summary = str(tool_result)[:200]
                                self.short_term_memory.add_message(
                                    session_id=session_id,
                                    role="tool",
                                    content=f"{tool_call.name}: {result_summary}"
                                )

                        # 超出预算的 tool_call 也要补一条结果，避免历史里
                        # 留下没有 tool 响应的调用导致下一轮请求 400
                        for tool_call in to_skip:
                            try:
                                skipped_message = (
                                    agent.llm_client.create_tool_message(
                                        tool_call_id=tool_call.id,
                                        tool_name=tool_call.name,
                                        result={
                                            "success": False,
                                            "error": "已超出工具调用预算",
                                        },
                                    )
                                )
                            except Exception as e:
                                logger.error(
                                    f"Failed to serialize skipped tool result: {e}"
                                )
                                skipped_message = {
                                    "role": "tool",
                                    "tool_call_id": tool_call.id,
                                    "name": tool_call.name,
                                    "content": json.dumps(
                                        {"error": "已超出工具调用预算"},
                                        ensure_ascii=False,
                                    ),
                                }
                            messages.append(skipped_message)

                        # 预算耗尽后立即收尾。若继续带着 tools 进入下一轮，
                        # 模型往往会再次请求调用工具，白白空转到 max_iterations。
                        if tool_call_count >= self.max_tool_calls:
                            logger.warning(
                                f"⚠️ 已达到最大 Skill 调用次数限制 ({self.max_tool_calls})，强制生成最终答案"
                            )
                            llm_calls += 1
                            final_answer = await self._force_final_answer(
                                agent, messages, on_token, usage
                            )
                            finalizing = True
                            result = await self._finalize(
                                agent, state, final_answer, session_id,
                                tool_call_count, sources, usage, llm_calls,
                                remember=persist_memory,
                                user_input=user_question,
                            )
                            break

                        # 继续下一轮循环
                        continue

                    # 情况2: LLM 返回文本响应，任务完成
                    else:
                        logger.info(f"LLM provided final response (no tool calls)")
                        finalizing = True
                        result = await self._finalize(
                            agent, state, llm_response.content or "", session_id,
                            tool_call_count, sources, usage, llm_calls,
                            remember=persist_memory,
                            user_input=user_question,
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
                        logger.warning(
                            "LLM 重试次数用尽，改为生成兜底答复"
                        )
                        break
                    await asyncio.sleep(min(2 ** state.iteration, 8))
                except Exception as e:
                    logger.error(f"Error in iteration {state.iteration}: {e}")
                    if finalizing:
                        # 收尾阶段（约束校验/后处理/记忆写入）出错时重试
                        # 只会重复调用模型并写入重复答复，直接降级
                        logger.warning("收尾阶段出错，不再重试，改为降级答复")
                        result = {
                            'answer': '抱歉，系统在处理您的问题时遇到了问题。建议您简化问题或稍后重试。',
                            'iterations': state.iteration,
                            'warning': 'finalize_failed',
                            'error': str(e)
                        }
                        state.mark_completed(result)
                        break
                    if state.iteration >= state.max_iterations:
                        logger.warning(
                            "连续异常达到最大迭代次数，改为生成兜底答复"
                        )
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
                    llm_calls += 1
                    final_text = await self._complete_text(
                        agent, messages, on_token, usage
                    )

                    result = {
                        'answer': final_text or '抱歉，未能完成任务',
                        'iterations': state.iteration,
                        'sources': merge_sources(sources),
                        'usage': dict(usage),
                        'llm_calls': llm_calls,
                        'warning': 'max_iterations_reached'
                    }

                    # 记录最终回答到短期记忆；强制收尾路径也补上用户问题，
                    # 避免留下没有提问的回答
                    if self.short_term_memory and session_id and persist_memory:
                        if user_question:
                            self.short_term_memory.add_message(
                                session_id=session_id,
                                role="user",
                                content=user_question
                            )
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
        finally:
            # 每次运行都会新建状态；AgentLoop 长期存活，不清理会累积
            # 所有历史输入与中间结果，内存只增不减
            self.state_manager.delete_state(task_id)

    async def _force_final_answer(
        self,
        agent,
        messages: List[Dict[str, Any]],
        on_token: Optional[Any] = None,
        usage: Optional[Dict[str, int]] = None
    ) -> str:
        """禁用工具，要求模型基于已有上下文给出最终答复。"""
        advisory = (
            '信息检索次数已达上限。请不要再请求调用工具，'
            '直接基于以上已有信息给出最终答复。'
        )
        # 重试时不要重复追加同一条提示，避免提示词里堆叠相同指令
        if not messages or messages[-1].get('content') != advisory:
            messages.append({
                'role': 'user',
                'content': advisory
            })

        return (await self._complete_text(agent, messages, on_token, usage)).strip()

    async def _complete_text(
        self,
        agent,
        messages: List[Dict[str, Any]],
        on_token: Optional[Any] = None,
        usage: Optional[Dict[str, int]] = None
    ) -> str:
        """生成最终文本：有流式回调时逐段推送，否则回退到普通调用。"""
        if on_token is not None and hasattr(agent.llm_client, "chat_stream"):
            stream_usage: Dict[str, int] = {}
            chunks: List[str] = []

            def guarded_on_token(delta: str) -> None:
                chunks.append(delta)
                on_token(delta)

            try:
                # 优先生成带「已输出不重试」保护的版本；测试/自定义客户端
                # 只实现 chat_stream 时退回普通流式调用
                stream_method = getattr(
                    agent.llm_client, "chat_stream_retry", None
                ) or agent.llm_client.chat_stream
                return await stream_method(
                    messages, on_token=guarded_on_token, usage_out=stream_usage
                )
            except LLMError:
                # 已经推送过增量时不要再重试或重新生成，
                # 否则前端会把两段回答拼接在一起
                if chunks:
                    logger.warning("流式回答中断，保留已推送的部分内容")
                    return "".join(chunks)
                raise
            finally:
                if usage is not None:
                    merge_usage(usage, stream_usage)

        response: LLMResponse = await agent.llm_client.chat_with_tools(
            messages=messages,
            tools=None,
            temperature=agent.config.get('temperature', 0.7)
        )
        if usage is not None:
            merge_usage(usage, response.usage)
        return response.content or ""

    async def _finalize(
        self,
        agent,
        state,
        final_answer: Optional[str],
        session_id: Optional[str],
        tool_calls_used: int,
        sources: Optional[List[Dict[str, Any]]] = None,
        usage: Optional[Dict[str, int]] = None,
        llm_calls: int = 0,
        remember: bool = True,
        user_input: str = "",
    ) -> Dict[str, Any]:
        """执行输出约束校验/自动修复，写入短期记忆并产出最终结果。"""
        final_answer = (final_answer or "").strip()
        if not final_answer:
            # 预算耗尽等路径可能拿到空文本；与 max_iterations 兜底保持一致
            final_answer = "抱歉，未能生成有效答复，请稍后重试或简化问题。"

        # Harness Engineering: 验证和修复输出
        if self.validator and final_answer:
            validation_result = self.validator.validate_output(
                agent.agent_id,
                final_answer,
                user_input=user_input,
            )

            if not validation_result.get("valid"):
                logger.warning(
                    f"⚠️ 输出约束违规: {validation_result.get('violations')}"
                )

                # 自动修复
                if self.auto_fixer and validation_result.get("auto_fixable"):
                    fixed_answer = self.auto_fixer.fix_output(
                        final_answer,
                        validation_result.get("auto_fixable", []),
                        user_input=user_input,
                    )
                    if fixed_answer != final_answer:
                        logger.info("🔧 输出已自动修复")
                        final_answer = fixed_answer

        result = {
            'answer': final_answer,
            'iterations': state.iteration,
            'agent_id': agent.agent_id,
            'tool_calls_used': tool_calls_used,
            'sources': merge_sources(sources or []),
            'usage': dict(usage or {}),
            'llm_calls': llm_calls
        }

        # 让 Agent 进行结果后处理（如提取建议等）
        if hasattr(agent, 'post_process_result'):
            result = await agent.post_process_result(result, final_answer)

        # 后处理成功后再写入短期记忆，保证记忆与最终持久化的回答一致；
        # 收尾异常时不会留下孤立提问或与 SQLite 不同的旧回答
        if self.short_term_memory and session_id and remember:
            if user_input:
                self.short_term_memory.add_message(
                    session_id=session_id,
                    role="user",
                    content=user_input
                )
            self.short_term_memory.add_message(
                session_id=session_id,
                role="assistant",
                content=result.get('answer') or final_answer or "(empty response)"
            )
            logger.debug(f"Recorded exchange to short-term memory (session={session_id})")

        state.mark_completed(result)
        return result

    def _initialize_messages(
        self,
        agent,
        input_data: Dict[str, Any],
        session_id: Optional[str] = None,
        load_history: bool = True,
    ) -> List[Dict[str, Any]]:
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
        if self.short_term_memory and session_id and load_history:
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
