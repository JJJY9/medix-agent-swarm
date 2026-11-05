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
from typing import List, Dict, Any, Optional, Callable, Tuple
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

from .security import install_log_redaction, redact_secrets, register_secret

# 所有模型调用路径都会经过这里，导入时即开启日志脱敏，
# 避免脚本或 CLI 绕过 api_server 时把 SDK 报错里的 Key 原样打出来。
install_log_redaction()

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

# config.py 中的密钥不在环境变量里，登记后再做日志/错误脱敏
register_secret(LLM_CONFIG.get("api_key"))


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


def tool_arguments_incomplete(raw_arguments: Optional[str]) -> bool:
    """判断工具参数是否被截断或不是 JSON 对象。

    与 parse_tool_call_arguments 的兜底不同，这里用于识别
    finish_reason=length 时被截断的参数，避免用空参数执行 Skill。
    """
    if not raw_arguments:
        return False
    try:
        parsed = json.loads(raw_arguments)
    except (json.JSONDecodeError, TypeError):
        return True
    return not isinstance(parsed, dict)


def usage_to_dict(raw_usage: Any) -> Optional[Dict[str, int]]:
    """把 OpenAI SDK 的 usage 对象转换成普通字典。"""
    if raw_usage is None:
        return None

    prompt = getattr(raw_usage, "prompt_tokens", None)
    completion = getattr(raw_usage, "completion_tokens", None)
    total = getattr(raw_usage, "total_tokens", None)
    if prompt is None and completion is None and total is None:
        return None

    def safe_int(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError, OverflowError):
            return 0

    return {
        "prompt_tokens": safe_int(prompt),
        "completion_tokens": safe_int(completion),
        "total_tokens": safe_int(total or ((prompt or 0) + (completion or 0))),
    }


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
        safe_message = redact_secrets(user_message)
        super().__init__(safe_message)
        self.code = code
        self.user_message = safe_message
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
    except (TypeError, ValueError, OverflowError):
        status = None

    message = redact_secrets(str(exc))
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
    usage: Optional[Dict[str, int]] = None

    def has_tool_calls(self) -> bool:
        """是否包含 function calls"""
        return len(self.tool_calls) > 0


class LLMClient:
    """统一的LLM客户端，支持多种模型"""

    #: 指数退避的基数（秒）；测试里可设为 0 跳过等待
    RETRY_BASE_DELAY = 2

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
        content, _ = await self.chat_with_usage(
            messages, temperature=temperature, max_tokens=max_tokens, **kwargs
        )
        return content

    async def chat_with_usage(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        **kwargs
    ) -> Tuple[str, Optional[Dict[str, int]]]:
        """异步聊天接口，同时返回 token 用量字典。"""
        try:
            if temperature is None:
                temperature = self.temperature
            if max_tokens is None:
                max_tokens = self.max_tokens

            logger.debug(f"Calling LLM ({self.model_type}) with {len(messages)} messages")

            response = await self.client.chat.completions.create(
                model=self.model_name,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                **kwargs
            )

            content = response.choices[0].message.content or ""
            usage = usage_to_dict(getattr(response, "usage", None))
            logger.debug(f"LLM response length: {len(content)} chars")
            return content, usage

        except LLMError:
            raise
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            raise classify_llm_error(e) from e

    async def _sleep_before_retry(self, attempt: int) -> None:
        """指数退避等待，单次最多 8 秒。"""
        await asyncio.sleep(min(self.RETRY_BASE_DELAY ** attempt, 8))

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
            max_retries: 最大尝试次数

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
                await self._sleep_before_retry(attempt)

        raise LLMError("llm_error", f"模型调用在 {max_retries} 次重试后仍失败。")

    async def chat_with_usage_retry(
        self,
        messages: List[Dict[str, str]],
        max_retries: int = 3,
        **kwargs
    ) -> Tuple[str, Optional[Dict[str, int]]]:
        """带重试的 chat_with_usage，只重试可恢复错误。"""
        for attempt in range(max_retries):
            try:
                return await self.chat_with_usage(messages, **kwargs)
            except LLMError as e:
                if not e.retryable or attempt == max_retries - 1:
                    raise
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {e.code}: {e.user_message}"
                )
                await self._sleep_before_retry(attempt)

        raise LLMError("llm_error", f"模型调用在 {max_retries} 次重试后仍失败。")

    async def chat_stream_retry(
        self,
        messages: List[Dict[str, str]],
        on_token: Optional[Callable[[str], None]] = None,
        usage_out: Optional[Dict[str, int]] = None,
        max_retries: int = 3,
        **kwargs
    ) -> str:
        """带重试的流式聊天。

        已经向前端推送过增量文本时不再重试，否则同一段文字会重复出现；
        此时直接抛出错误，由上层决定如何展示。
        """
        for attempt in range(max_retries):
            emitted = False

            def guarded_on_token(delta: str) -> None:
                nonlocal emitted
                emitted = True
                if on_token is not None:
                    on_token(delta)

            try:
                return await self.chat_stream(
                    messages,
                    on_token=guarded_on_token,
                    usage_out=usage_out,
                    **kwargs
                )
            except LLMError as e:
                if emitted or not e.retryable or attempt == max_retries - 1:
                    raise
                logger.warning(
                    f"Retry stream {attempt + 1}/{max_retries} after "
                    f"{e.code}: {e.user_message}"
                )
                await self._sleep_before_retry(attempt)

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
        usage_out: Optional[Dict[str, int]] = None,
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
            usage_out: 可选字典，若上游返回 usage 则写入其中

        Returns:
            模型返回的完整文本
        """
        temperature = self.temperature if temperature is None else temperature
        max_tokens = self.max_tokens if max_tokens is None else max_tokens

        try:
            request_params: Dict[str, Any] = {
                "model": self.model_name,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "stream": True,
                **kwargs,
            }
            if usage_out is not None:
                request_params["stream_options"] = {"include_usage": True}

            try:
                stream = await self.client.chat.completions.create(**request_params)
            except Exception as e:
                # 少数 OpenAI 兼容服务不支持 stream_options，退回普通流式请求
                if usage_out is not None and "stream_options" in str(e):
                    request_params.pop("stream_options", None)
                    stream = await self.client.chat.completions.create(**request_params)
                else:
                    raise

            chunks: List[str] = []
            callback_failed = False

            try:
                async for chunk in stream:
                    chunk_usage = usage_to_dict(getattr(chunk, "usage", None))
                    if chunk_usage and usage_out is not None:
                        usage_out.clear()
                        usage_out.update(chunk_usage)

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
            finally:
                # 取消/异常时也要关闭上游 HTTP 流，避免连接悬挂到 GC
                close = getattr(stream, "aclose", None) or getattr(
                    stream, "close", None
                )
                if close:
                    try:
                        closed = close()
                        if asyncio.iscoroutine(closed):
                            await closed
                    except Exception:
                        pass

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
            if temperature is None:
                temperature = self.temperature
            if max_tokens is None:
                max_tokens = self.max_tokens

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
            truncated = False
            if hasattr(message, 'tool_calls') and message.tool_calls:
                for tc in message.tool_calls:
                    if (
                        finish_reason == "length"
                        and tool_arguments_incomplete(tc.function.arguments)
                    ):
                        truncated = True
                    tool_calls.append(ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=parse_tool_call_arguments(tc.function.arguments)
                    ))
                logger.debug(f"LLM requested {len(tool_calls)} tool calls")

            # 参数被 max_tokens 截断时不能用空参数执行 Skill，
            # 交给上层重试或降级，避免返回无依据的结果
            if truncated:
                raise LLMError(
                    "llm_truncated",
                    "模型回答在工具参数中途被截断，请重试或缩短输入。",
                    retryable=True,
                )

            return LLMResponse(
                content=message.content,
                tool_calls=tool_calls,
                finish_reason=finish_reason,
                usage=usage_to_dict(getattr(response, "usage", None)),
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
