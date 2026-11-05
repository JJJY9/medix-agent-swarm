"""
Skill 注册系统
直接将 Skill 函数转换为 OpenAI function calling 格式
"""
from typing import Dict, Any, List, Callable, Optional
from dataclasses import dataclass
import inspect
import asyncio
from loguru import logger


@dataclass
class SkillParameter:
    """Skill 参数定义"""
    name: str
    type: str  # "string", "number", "integer", "boolean", "object", "array"
    description: str
    required: bool = False
    enum: Optional[List[str]] = None


def _coerce_argument(param_type: str, value: Any) -> Any:
    """按注册的类型转换模型传入的参数；无法转换时抛出 ValueError。"""
    if value is None:
        return None
    if param_type == "integer":
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value != int(value):
                raise ValueError(f"参数应为整数，收到 {value}")
            return int(value)
        return int(str(value).strip())
    if param_type == "number":
        if isinstance(value, bool):
            return float(int(value))
        return float(value)
    if param_type == "boolean":
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "是"):
                return True
            if lowered in ("false", "0", "no", "否"):
                return False
            raise ValueError(f"参数应为布尔值，收到 {value}")
        return bool(value)
    if param_type == "array":
        if not isinstance(value, (list, tuple)):
            raise ValueError("参数应为数组")
        return list(value)
    if param_type == "object":
        if not isinstance(value, dict):
            raise ValueError("参数应为对象")
        return value
    return str(value)


class SkillRegistry:
    """
    Skill 注册表

    存储 Skill 函数并提供执行和格式转换能力
    """

    def __init__(self):
        self.skills: Dict[str, Dict[str, Any]] = {}

    def register(
        self,
        name: str,
        function: Callable,
        description: str,
        parameters: List[SkillParameter]
    ):
        """
        注册 Skill

        Args:
            name: Skill 名称
            function: Skill 函数（async 或 sync）
            description: Skill 描述
            parameters: 参数列表
        """
        self.skills[name] = {
            'function': function,
            'description': description,
            'parameters': parameters,
            'is_async': inspect.iscoroutinefunction(function)
        }
        logger.debug(f"Registered skill: {name}")

    def get(self, name: str) -> Optional[Dict[str, Any]]:
        """获取 Skill"""
        return self.skills.get(name)

    def get_all(self) -> Dict[str, Dict[str, Any]]:
        """获取所有 Skills"""
        return self.skills

    async def execute(self, name: str, **kwargs) -> Dict[str, Any]:
        """
        执行 Skill

        Args:
            name: Skill 名称
            **kwargs: Skill 参数

        Returns:
            Skill 执行结果
        """
        skill = self.skills.get(name)
        if not skill:
            error_msg = f"Skill not found: {name}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg
            }

        # 按注册的参数定义校验模型传入的 kwargs：
        # 直接把错误 kwargs 塞进函数只会得到泛化的 TypeError，既浪费
        # 工具预算，也不告诉模型正确用法
        parameters = skill.get('parameters') or []
        allowed = {param.name for param in parameters}
        unknown = sorted(set(kwargs) - allowed)
        if unknown:
            return {
                "success": False,
                "error": (
                    f"未知参数：{', '.join(unknown)}；"
                    f"可用参数：{', '.join(sorted(allowed)) or '无'}"
                ),
                "skill": name,
            }
        missing = [
            param.name for param in parameters
            if param.required and kwargs.get(param.name) is None
        ]
        if missing:
            return {
                "success": False,
                "error": f"缺少必填参数：{', '.join(missing)}",
                "skill": name,
            }
        type_by_name = {param.name: param.type for param in parameters}
        try:
            kwargs = {
                key: _coerce_argument(type_by_name.get(key, "string"), value)
                for key, value in kwargs.items()
            }
        except (TypeError, ValueError) as e:
            return {
                "success": False,
                "error": str(e),
                "skill": name,
            }

        try:
            logger.debug(f"Executing skill: {name} with args: {kwargs}")

            if skill['is_async']:
                # Async skill
                result = await skill['function'](**kwargs)
            else:
                # Sync skill - run in executor
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(
                    None,
                    lambda: skill['function'](**kwargs)
                )

            logger.debug(f"Skill {name} completed successfully")
            return result

        except Exception as e:
            error_msg = f"Skill execution failed: {name} - {str(e)}"
            logger.error(error_msg)
            return {
                "success": False,
                "error": error_msg,
                "skill": name
            }

    def to_openai_format(self) -> List[Dict[str, Any]]:
        """
        直接转换为 OpenAI function calling 格式

        Returns:
            OpenAI tools 格式的列表
        """
        tools = []

        for name, skill in self.skills.items():
            properties = {}
            required = []

            for param in skill['parameters']:
                prop = {
                    'type': param.type,
                    'description': param.description
                }
                if param.enum:
                    prop['enum'] = param.enum

                properties[param.name] = prop

                if param.required:
                    required.append(param.name)

            tools.append({
                'type': 'function',
                'function': {
                    'name': name,
                    'description': skill['description'],
                    'parameters': {
                        'type': 'object',
                        'properties': properties,
                        'required': required
                    }
                }
            })

        return tools
