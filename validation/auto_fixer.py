"""
自动修复器
根据约束违规自动修复输出

基于 Harness Engineering 原则：
- 自动检测问题
- 自动修复（在可能的情况下）
- 保持 Agent 输出质量
"""
from typing import Dict, Any, List
from loguru import logger


_DISCLAIMER_BLOCK = (
    "\n\n【免责声明】\n"
    "以上信息仅供参考，不能替代专业医生的诊断和治疗。如有疑虑，请及时就医。"
)
_TRUNCATION_NOTICE = "\n\n[回答内容较长，已截断。如需完整信息，请咨询专业医生]"


class AutoFixer:
    """自动修复器"""

    @staticmethod
    def _has_disclaimer(text: str) -> bool:
        """与 ConstraintValidator 的免责声明判定保持一致。"""
        return (
            "免责声明" in text
            or "仅供参考" in text
            or "disclaimer" in text.lower()
        )

    def fix_output(
        self,
        output: str,
        auto_fixable: List[str],
        user_input: str = ""
    ) -> str:
        """
        自动修复输出

        Args:
            output: 原始输出
            auto_fixable: 可修复的违规列表
            user_input: 原始用户问题；用于判断高危症状语境

        Returns:
            修复后的输出
        """
        fixed_output = output

        fix_types = [str(item) for item in auto_fixable]
        truncations = [
            item for item in fix_types
            if item.startswith("truncate_response:")
        ]
        others = [
            item for item in fix_types
            if not item.startswith("truncate_response:")
        ]

        limits: List[int] = []
        for fix_type in truncations:
            try:
                limits.append(int(fix_type.split(":", 1)[1]))
            except (TypeError, ValueError):
                continue

        # 原始回答已有免责声明时，截断要保留末尾声明原文，避免替换成通用模板。
        had_disclaimer = self._has_disclaimer(output)
        needs_disclaimer = had_disclaimer or "add_disclaimer" in others

        # 先截断再补免责声明/警告；截断时同步保留末尾已有免责声明。
        for limit in limits:
            fixed_output = self._truncate_preserving_disclaimer(
                fixed_output,
                max(100, limit),
                ensure_disclaimer=needs_disclaimer,
            )

        for fix_type in others:
            if fix_type == "add_disclaimer":
                fixed_output = self.fix_missing_disclaimer(fixed_output)
            elif fix_type == "add_emergency_warning":
                fixed_output = self.fix_high_risk_warning(
                    fixed_output, user_input
                )

        if needs_disclaimer and not self._has_disclaimer(fixed_output):
            fixed_output = self.fix_missing_disclaimer(fixed_output)

        # 兜底保证修复结果不再超长，同时保留末尾免责声明。
        if limits and len(fixed_output) > min(limits):
            fixed_output = self._truncate_preserving_disclaimer(
                fixed_output,
                min(limits),
                ensure_disclaimer=needs_disclaimer,
            )

        if fixed_output != output:
            logger.info("🔧 输出已自动修复")

        return fixed_output

    def fix_missing_disclaimer(self, output: str) -> str:
        """
        自动添加免责声明

        Args:
            output: 原始输出

        Returns:
            添加免责声明后的输出
        """
        if not self._has_disclaimer(output):
            logger.debug("+ 自动添加免责声明")
            return output + _DISCLAIMER_BLOCK
        return output

    def fix_high_risk_warning(self, output: str, user_input: str = "") -> str:
        """
        自动添加高危症状警告

        Args:
            output: 原始输出
            user_input: 原始用户问题；用于区分患者症状与科普描述

        Returns:
            添加警告后的输出
        """
        from core.triage import (
            clinical_statement_text,
            detect_red_flags,
            has_visit_recommendation,
        )

        risk_text = "\n".join(
            part for part in (
                user_input.strip(),
                clinical_statement_text(output),
            ) if part
        )
        # 复用急症红旗与就医建议识别：否定或已给出就医建议时不再插入警告
        if (
            detect_red_flags(risk_text)["level"] == "emergency"
            and not has_visit_recommendation(output)
        ):
            warning = "⚠️ **重要提醒**：您描述的症状可能提示严重问题，建议立即就医或拨打急救电话120，不要延误。\n\n"
            logger.debug("+ 自动添加高危症状警告")
            return warning + output

        return output

    def fix_excessive_length(self, output: str, max_length: int) -> str:
        """
        截断过长的输出

        Args:
            output: 原始输出
            max_length: 最大长度

        Returns:
            截断后的输出
        """
        if len(output) > max_length:
            logger.warning(f"输出过长（{len(output)} > {max_length}），自动截断")
            truncated = output[:max_length - 50]  # 保留50字空间添加提示
            truncated += _TRUNCATION_NOTICE
            return truncated

        return output

    def _truncate_preserving_disclaimer(
        self,
        output: str,
        max_length: int,
        ensure_disclaimer: bool = False,
    ) -> str:
        """按上限截断正文，但保留末尾免责声明。

        Args:
            output: 待截断文本
            max_length: 最终文本长度上限
            ensure_disclaimer: 无正式免责声明块时是否补一个
        """
        if len(output) <= max_length:
            return output

        body = output
        suffix = ""
        marker_index = output.rfind("【免责声明】")
        # 只有位于末尾的声明块才作为后缀整体保留；正文中段的声明按普通正文处理
        if marker_index >= 0 and marker_index >= max(0, len(output) - 200):
            start = output.rfind("\n\n", 0, marker_index)
            start = start if start >= 0 else marker_index
            body = output[:start]
            suffix = output[start:]
        elif ensure_disclaimer:
            suffix = _DISCLAIMER_BLOCK

        # 去掉前一轮截断留下的提示，避免重复累积
        body = body.replace(_TRUNCATION_NOTICE, "").rstrip()

        notice = _TRUNCATION_NOTICE
        budget = max_length - len(suffix) - len(notice)
        if budget < 0:
            # 上限极小时优先保留免责声明，省略截断提示
            notice = ""
            budget = max(0, max_length - len(suffix))
        return body[:budget].rstrip() + notice + suffix

    def remove_diagnosis_statements(self, output: str) -> str:
        """
        移除明确的诊断语句（高级功能，需要 LLM 辅助）

        Args:
            output: 原始输出

        Returns:
            移除诊断语句后的输出
        """
        # 简单替换（实际应该使用 LLM 进行更智能的重写）
        output = output.replace("您患有", "可能存在")
        output = output.replace("确诊为", "建议检查")
        output = output.replace("肯定是", "很可能是")

        return output
