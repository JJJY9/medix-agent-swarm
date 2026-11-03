"""
LLM客户端
支持调用 OpenAI 兼容的 API（如字节跳动豆包、OpenAI、Deepseek 等）
支持 function calling
"""
import os
import sys
import asyncio
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable
from dataclasses import dataclass
from openai import (
    APIStatusError,
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    RateLimitError,
)
from loguru import logger

# 加载项目根目录的 .env（可选，用于配置 API Key 而不改动 config.py）
try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# 加载上层目录的 config.py（config.py 位于项目根目录的上一层）
_CONFIG_DIR = Path(__file__).resolve().parent.parent.parent
if str(_CONFIG_DIR) not in sys.path:
    sys.path.insert(0, str(_CONFIG_DIR))

try:
    from config import LLM_CONFIG
except ImportError:
    # 没有 config.py 时使用环境变量，便于在不同机器上运行
    LLM_CONFIG = {
        "api_key": os.getenv("MEDIX_API_KEY", ""),
        "model_name": os.getenv("MEDIX_MODEL", "doubao-seed-1-6-flash-250828"),
        "base_url": os.getenv("MEDIX_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3"),
        "temperature": 0.7,
        "max_tokens": 8192,
    }

# 环境变量优先级高于 config.py，且不修改原字典
LLM_CONFIG = dict(LLM_CONFIG)
for _env_key, _cfg_key in (
    ("MEDIX_API_KEY", "api_key"),
    ("MEDIX_MODEL", "model_name"),
    ("MEDIX_BASE_URL", "base_url"),
):
    if os.getenv(_env_key):
        LLM_CONFIG[_cfg_key] = os.getenv(_env_key)

del _env_key, _cfg_key


@dataclass
class ToolCall:
    """Function call 数据结构"""
    id: str
    name: str
    arguments: Dict[str, Any]


def parse_tool_call_arguments(raw_arguments: Optional[str]) -> Dict[str, Any]:
    """解析 LLM 返回的工具调用参数。

    模型偶尔会输出不合法或非对象的 JSON（例如空字符串、带注释的片段），
    直接 json.loads 会抛异常并中断整轮对话。这里统一兜底为空参数，
    让 Skill 自己校验，至少能返回可读的错误而不是把 Agent Loop 打断。

    Args:
        raw_arguments: OpenAI function calling 返回的 arguments 字符串

    Returns:
        解析后的参数字典；无法解析时返回空字典
    """
    if not raw_arguments:
        return {}

    try:
        arguments = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError) as e:
        logger.warning(f"工具调用参数不是合法 JSON，已按空参数处理: {e}")
        return {}

    if not isinstance(arguments, dict):
        logger.warning(
            f"工具调用参数不是 JSON 对象（实际为 {type(arguments).__name__}），已按空参数处理"
        )
        return {}

    return arguments


class LLMError(Exception):
    """模型调用错误。

    携带稳定的错误码、用户可读提示和是否可重试标记，供 Agent Loop、
    API 和前端统一处理，避免把 401/402 这类不可恢复错误当成普通异常反复重试。
    """

    def __init__(
        self,
        code: str,
        user_message: str,
        retryable: bool = False,
        status_code: Optional[int] = None
    ):
        super().__init__(user_message)
        self.code = code
        self.user_message = user_message
        self.retryable = retryable
        self.status_code = status_code


def classify_llm_error(exc: Exception) -> LLMError:
    """把 OpenAI SDK 的异常转换成带用户提示的 LLMError。"""
    if isinstance(exc, LLMError):
        return exc

    status = getattr(exc, "status_code", None)
    if status is None:
        response = getattr(exc, "response", None)
        status = getattr(response, "status_code", None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None

    message = str(exc)
    lowered = message.lower()
    type_name = type(exc).__name__.lower()

    if status == 402 or "insufficient balance" in lowered or "余额不足" in message:
        return LLMError(
            "llm_balance",
            "模型账户余额不足，请充值或更换 API Key 后重试。",
            retryable=False,
            status_code=status,
        )

    if status in (401, 403) or isinstance(exc, AuthenticationError):
        return LLMError(
            "llm_auth",
            "API Key 无效、已过期或没有权限，请检查 .env 中的 MEDIX_API_KEY。",
            retryable=False,
            status_code=status,
        )

    if status == 429 or isinstance(exc, RateLimitError) or "rate limit" in lowered:
        return LLMError(
            "llm_rate_limit",
            "模型请求过于频繁或额度受限，请稍后重试。",
            retryable=True,
            status_code=status,
        )

    if (
        isinstance(exc, APITimeoutError)
        or "timeout" in type_name
        or "timed out" in lowered
    ):
        return LLMError(
            "llm_timeout",
            "模型响应超时，请稍后重试或简化问题。",
            retryable=True,
            status_code=status,
        )

    if isinstance(exc, APIConnectionError) or "connection" in type_name:
        return LLMError(
            "llm_network",
            "无法连接模型服务，请检查网络或 base_url 配置。",
            retryable=True,
            status_code=status,
        )

    if status is not None and 500 <= status < 600:
        return LLMError(
            "llm_server",
            "模型服务暂时不可用，请稍后重试。",
            retryable=True,
            status_code=status,
        )

    return LLMError(
        "llm_error",
        f"模型调用失败：{message[:200]}",
        retryable=False,
        status_code=status,
    )


@dataclass
class LLMResponse:
    """LLM 响应数据结构（支持 function calling）"""
    content: Optional[str]
    tool_calls: List[ToolCall]
    finish_reason: str  # "stop", "tool_calls", "length", "content_filter"

    def has_tool_calls(self) -> bool:
        """是否包含 function calls"""
        return len(self.tool_calls) > 0


class LLMClient:
    """统一的LLM客户端，支持多种模型"""

    def __init__(self, model_type: str = "openai_compatible"):
        """
        初始化LLM客户端

        Args:
            model_type: 模型类型，默认 "openai_compatible"（支持 OpenAI 兼容的 API）
        """
        self.model_type = model_type

        if model_type == "openai_compatible":
            # 使用 OpenAI 兼容的 API（通过 config.py 配置）
            self.config = LLM_CONFIG
            try:
                self.request_timeout = float(
                    os.getenv("MEDIX_TIMEOUT")
                    or self.config.get("timeout")
                    or 60
                )
            except (TypeError, ValueError):
                self.request_timeout = 60.0

            # 关闭 SDK 自带重试，统一由 chat_with_retry / AgentLoop 控制，
            # 避免不可恢复错误（401/402）被 SDK 重复请求。
            self.client = AsyncOpenAI(
                api_key=self.config["api_key"],
                base_url=self.config["base_url"],
                timeout=self.request_timeout,
                max_retries=0,
            )
            self.model_name = self.config["model_name"]
            self.temperature = self.config.get("temperature", 0.7)
            self.max_tokens = self.config.get("max_tokens", 8192)
        else:
            raise ValueError(f"Unknown model type: {model_type}")

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """
        异步聊天接口

        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 温度参数（可选）
            max_tokens: 最大token数（可选）

        Returns:
            模型返回的文本
        """
        try:
            temperature = temperature or self.temperature
            max_tokens = max_tokens or self.max_tokens

            logger.debug(f"Calling LLM ({self.model_type}) with {len(messages)} messages")

            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

            content = response.choices[0].message.content or ""
            logger.debug(f"LLM response length: {len(content)} chars")
            return content

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise classify_llm_error(e) from e

    async def chat_with_retry(
        self,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """
        带重试的聊天接口

        Args:
            messages: 消息列表
            max_retries: 最大重试次数

        Returns:
            模型返回的文本
        """
        for attempt in range(max_retries):
            try:
                return await self.chat(messages, **kwargs)
            except LLMError as e:
                if not e.retryable or attempt == max_retries - 1:
                    raise
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {e.code}: {e.user_message}"
                )
                await asyncio.sleep(2 ** attempt)  # 指数退避

        raise LLMError("llm_error", f"模型调用在 {max_retries} 次重试后仍失败。")

    def create_message(self, role: str, content: str) -> Dict[str, str]:
        """
        创建消息对象

        Args:
            role: 角色，"user" 或 "assistant" 或 "system"
            content: 消息内容

        Returns:
            消息字典
        """
        return {"role": role, "content": content}

    async def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        on_token: Optional[Callable[[str], None]] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> str:
        """流式聊天接口（纯文本，不支持工具调用）。

        逐块读取 OpenAI 兼容接口的流式响应，把增量通过 ``on_token`` 回调交给
        调用方（用于 SSE 推送），同时累积完整文本作为返回值。

        Args:
            messages: 消息列表
            on_token: 每收到一段增量文本时调用的同步回调
            temperature: 温度参数
            max_tokens: 最大 token 数

        Returns:
            模型返回的完整文本
        """
        temperature = self.temperature if temperature is None else temperature
        max_tokens = self.max_tokens if max_tokens is None else max_tokens

        try:
            stream = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                stream=True,
                **kwargs
            )

            chunks: List[str] = []
            callback_failed = False

            async for chunk in stream:
                if not chunk.choices:
                    continue
                content = getattr(chunk.choices[0].delta, "content", None)
                if not content:
                    continue

                chunks.append(content)
                if on_token and not callback_failed:
                    try:
                        on_token(content)
                    except Exception as e:
                        callback_failed = True
                        logger.warning(f"流式回调失败，后续增量将被忽略: {e}")

            return "".join(chunks)

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"LLM stream call failed: {e}")
            raise classify_llm_error(e) from e

    async def chat_with_tools(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> LLMResponse:
        """
        带工具支持的聊天接口

        Args:
            messages: 消息列表
            tools: 工具定义列表（OpenAI format）
            tool_choice: 工具选择策略 ("auto"/"required"/"none")
            temperature: 温度参数
            max_tokens: 最大token数

        Returns:
            LLMResponse 对象
        """
        try:
            temperature = temperature or self.temperature
            max_tokens = max_tokens or self.max_tokens

            logger.debug(f"Calling LLM with {len(tools) if tools else 0} tools")

            # 准备请求参数
            request_params = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                **kwargs
            }

            # 添加工具参数（如果提供）
            if tools:
                request_params["tools"] = tools
                if tool_choice != "auto":
                    request_params["tool_choice"] = tool_choice

            response = await self.client.chat.completions.create(**request_params)

            # 解析响应
            message = response.choices[0].message
            finish_reason = response.choices[0].finish_reason

            # 提取工具调用
            tool_calls = []
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tc in message.tool_calls:
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=parse_tool_call_arguments(tc.function.arguments)
                    ))
                logger.debug(f"LLM requested {len(tool_calls)} tool calls")

            return LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=finish_reason
            )

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"LLM call with tools failed: {e}")
            raise classify_llm_error(e) from e

    def create_tool_message(
        self,
        tool_call_id: str,
        tool_name: str,
        result: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        创建工具执行结果消息

        Args:
            tool_call_id: 工具调用ID
            tool_name: 工具名称
            result: 工具执行结果

        Returns:
            工具消息字典
        """
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": tool_name,
            "content": json.dumps(result, ensure_ascii=False, default=str)
        }
