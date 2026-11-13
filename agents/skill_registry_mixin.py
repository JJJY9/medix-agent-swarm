"""
统一的 Skills 注册（自动发现）
所有 Worker Agents 共享
"""
from core.skill_loader import discover_skills
from core.skill_registry import SkillParameter
from pathlib import Path
from loguru import logger
import inspect
import re
import types
import typing


class SkillRegistryMixin:
    """
    统一注册所有 Skills（自动发现模式）

    所有 Worker Agents (ConsultationAgent, DiagnosticAgent, ResearchAgent)
    都继承这个 Mixin，避免重复代码
    """

    def register_all_skills(self):
        """
        自动扫描并注册所有 Skills

        Skills 会从 .claude/skills/ 目录自动发现，
        无需手动维护列表
        """
        project_root = Path(__file__).parent.parent
        discovered = discover_skills(project_root)

        # 自动注册所有发现的 skills
        for skill_info in discovered:
            function_name = skill_info["function_name"]
            metadata = skill_info.get("metadata")
            if not isinstance(metadata, dict):
                metadata = {}
            func = skill_info["function"]

            # 从 metadata 获取描述
            description = metadata.get("description", f"Skill: {skill_info['name']}")

            # 根据函数签名自动推断参数
            parameters = self._infer_skill_parameters(skill_info)

            # 注册到 SkillRegistry
            self.skill_registry.register(
                name=function_name,
                function=func,
                description=description,
                parameters=parameters
            )
            logger.info(f"✅ Registered skill: {function_name}")

        logger.info(f"Total {len(discovered)} skills registered")

    def _infer_skill_parameters(self, skill_info: dict) -> list:
        """
        从 skill 信息推断参数

        Args:
            skill_info: skill 信息字典

        Returns:
            [SkillParameter(...), ...]
        """
        func = skill_info["function"]

        # 获取函数签名
        sig = inspect.signature(func)
        parameters = []

        for param_name, param in sig.parameters.items():
            # 跳过 self 与 *args/**kwargs（按参数种类判断，不能只看名字）
            if param_name == "self" or param.kind in (
                inspect.Parameter.VAR_POSITIONAL,
                inspect.Parameter.VAR_KEYWORD,
            ):
                continue

            # 判断是否必需
            required = param.default == inspect.Parameter.empty

            # 优先按类型注解推断，注解缺失时退回按参数名猜测
            param_type = self._annotation_to_skill_type(
                param.annotation
            )
            if not param_type:
                if (
                    "count" in param_name or "limit" in param_name
                    or "max" in param_name or "iterations" in param_name
                ):
                    param_type = "integer"
                else:
                    param_type = "string"

            # 生成描述
            param_desc = param_name.replace('_', ' ').title()

            parameters.append(SkillParameter(
                param_name,
                param_type,
                param_desc,
                required
            ))

        return parameters

    @staticmethod
    def _annotation_to_skill_type(annotation) -> str:
        """把类型注解（含 PEP 563 字符串与 typing 泛型）映射为技能类型。"""
        if annotation is inspect.Parameter.empty:
            return ""
        if isinstance(annotation, str):
            text = annotation.strip().lower()
            # 容器类型先判断：list[int | None]、dict[str, int | None] 里的
            # 竖线属于元素类型，不能按顶层联合类型切分
            if text.startswith(("list", "tuple", "sequence", "set")):
                return "array"
            if text.startswith(("dict", "mapping")):
                return "object"
            # PEP 563 下的组合写法：Optional[int] / Union[int, None] /
            # int | None 都要还原成基础类型
            optional = re.fullmatch(r"optional\[(.+)\]", text)
            if optional:
                return SkillRegistryMixin._annotation_to_skill_type(
                    optional.group(1)
                )
            union = re.fullmatch(r"union\[(.+)\]", text)
            if union:
                parts = [
                    part.strip() for part in union.group(1).split(",")
                    if part.strip() not in ("none", "nonetype")
                ]
                return (
                    SkillRegistryMixin._annotation_to_skill_type(parts[0])
                    if len(parts) == 1 else ""
                )
            if "|" in text:
                parts = [
                    part.strip() for part in text.split("|")
                    if part.strip() not in ("none", "nonetype")
                ]
                return (
                    SkillRegistryMixin._annotation_to_skill_type(parts[0])
                    if len(parts) == 1 else ""
                )
            if text in {"int", "integer"}:
                return "integer"
            if text in {"float", "number"}:
                return "number"
            if text in {"bool", "boolean"}:
                return "boolean"
            if text.startswith(("list", "tuple", "sequence", "set")):
                return "array"
            if text.startswith(("dict", "mapping")):
                return "object"
            return "string"

        origin = typing.get_origin(annotation)
        if origin is typing.Union or origin is types.UnionType:
            args = [
                arg for arg in getattr(annotation, "__args__", ())
                if arg is not type(None)
            ]
            return (
                SkillRegistryMixin._annotation_to_skill_type(args[0])
                if len(args) == 1 else ""
            )
        if origin in (list, tuple, set, frozenset):
            return "array"
        if origin in (dict,):
            return "object"
        if annotation is int:
            return "integer"
        if annotation is float:
            return "number"
        if annotation is bool:
            return "boolean"
        if annotation in (list, tuple, set, frozenset):
            return "array"
        if annotation is dict:
            return "object"
        return "string"
