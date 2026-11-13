"""
约束验证器
运行时检查 Agent 行为是否违反约束

基于 Harness Engineering 原则：
- 显式化约束
- 运行时验证
- 自动修复（可选）
"""
import re
from typing import Dict, Any, List, Optional
import yaml
from pathlib import Path
from loguru import logger


#: 不同 YAML 约束文件用不同措辞表达同一类禁止行为，这里统一映射
_DIAGNOSIS_ACTIONS = {
    "diagnose_disease",
    "give_definitive_diagnosis",
    "give_diagnosis",
}
_PRESCRIPTION_ACTIONS = {
    "prescribe_medication",
    "recommend_specific_drugs",
    "recommend_treatment",
}
_DEFINITIVE_DIAGNOSIS_PATTERN = re.compile(
    r"(?:您患有|您得了|您得的是|您患的是|确诊为|可以确诊为|"
    r"明确诊断为|诊断为|医生诊断(?:您)?为|诊断结果\s*[：:]|肯定是)"
)
_DIAGNOSIS_NEGATION_PATTERN = re.compile(
    r"(?:(?:不能|无法|不可|尚未|未曾|没有|否认|并非|不是|不建议|不应)"
    r"(?:仅|单纯|完全|凭|根据|据此|明确|直接|轻易|发现|出现)?"
    # “无法排除肺炎，确诊为…”里的“无法排除”并不否定诊断
    r"(?![^，。；]{0,6}(?:排除|除外))"
    r"[^，。；]{0,6}|未|无)\s*$"
)
_DIAGNOSIS_CLAUSE_DELIMITERS = "，。；！？、,.!?;\n"

_DOSE_UNIT = (
    r"(?:mg|g|毫克|克|μg|ug|微克|片|粒|支|ml|毫升|单位|国际单位|iu|u|U)"
)
_NUMERAL = r"(?:\d+(?:\.\d+)?|[一二两三四五六七八九十半]+)"
_DOSE_PATTERN = re.compile(rf"{_NUMERAL}\s*{_DOSE_UNIT}")
_PRESCRIPTION_PATTERNS = (
    re.compile(rf"(?:药物|药品|药).{{0,10}}{_NUMERAL}\s*{_DOSE_UNIT}"),
    re.compile(
        rf"每(?:日|天|次).{{0,5}}{_NUMERAL}\s*次.{{0,10}}{_NUMERAL}\s*{_DOSE_UNIT}"
    ),
    re.compile(
        rf"(?:建议|推荐|可以|可|应|请)\s*"
        rf"(?:服用|使用|吃|口服|注射).{{0,15}}{_NUMERAL}\s*{_DOSE_UNIT}"
    ),
    # 「推荐依折麦布 10mg」「首选布洛芬 200mg」：没有用药动词也要识别
    re.compile(
        rf"(?:建议|推荐|首选|可以|可|应|请)"
        rf"[^，。；！？\n]{{0,12}}?{_NUMERAL}\s*{_DOSE_UNIT}"
    ),
    re.compile(
        rf"(?:服用|使用|口服|注射).{{0,15}}{_NUMERAL}\s*{_DOSE_UNIT}"
    ),
    re.compile(
        rf"(?:用法|用量|剂量).{{0,10}}{_NUMERAL}\s*{_DOSE_UNIT}"
    ),
)
#: 片/粒/支等计数单位也可能是食物（“每天一片面包”），需有用药语境
_COUNT_UNIT_PATTERN = re.compile(
    rf"(?:每日|每天|一日|每次).{{0,8}}{_NUMERAL}\s*(?:片|粒|支|ml|毫升)"
)
_MEDICATION_TERMS = (
    "药", "口服", "注射", "片剂", "胶囊", "颗粒", "滴剂", "喷雾", "胰岛素",
)
#: 片/粒/支既可能是药也可能是食物，需有明确药名或“药”语境
_COUNT_UNIT_SUFFIX = re.compile(r"(?:片|粒|支)\s*$")
#: “误服 20 片”是中毒描述，不是处方
_OVERDOSE_PATTERN = re.compile(r"(?:误服|过量|超量|超过|中毒|自杀|自伤|服毒)")
#: 复述患者现有用药、说明书剂量或明确否定性建议，不是开处方
_SAFE_PRESCRIPTION_CONTEXT = re.compile(
    r"(?:"
    r"正在(?:服用|使用|口服|注射)|目前在?(?:服用|使用)|"
    r"已经?(?:服用|使用)|长期(?:服用|使用)|一直(?:服用|使用)|"
    r"既往(?:用药|服用)|现有用药|您(?:目前|正在)?(?:服用|使用)的|"
    r"常见剂量|常用剂量|常规剂量|说明书|最大剂量|"
    r"(?:不建议|不推荐|无需|不必|不能|不可以|勿|不要|避免|禁止|别|"
    r"不可|不宜|不应)(?:自行)?(?:服用|使用|口服|注射)"
    r")"
)
#: 相互作用/联合用药提示，需同时出现风险提示才视为安全语境
_INTERACTION_MARKER = re.compile(
    r"(?:相互作用|联合使用|联合应用|联合用药|同时(?:服用|使用)|"
    r"(?<!适)合用|联用)"
)
_INTERACTION_WARNING = re.compile(
    r"(?:相互作用|风险|出血|禁忌|不良反应|注意|监测|谨慎|增加|避免|"
    r"不宜|慎用)"
)
#: 同一分句里出现明确的用药推荐时，安全语境不成立（“不建议服用”除外）
#: 允许推荐词与用药词之间隔着药名/剂量列表，但不能跨标点。
_AFFIRMATIVE_MEDICATION_ADVICE = re.compile(
    r"(?<![不无])(?:建议|推荐|可以|可|应|请|需要|需|必须|务必|坚持|"
    r"一定要|最好|宜)"
    r"([^，。；！？\n]{0,30}?)"
    r"(?:服用|使用|吃|口服|注射|应用|合用|联用|加用|改用|换用|换成|"
    r"改为|调整为|加量|减量|增量|增至|降至)"
)
#: 推荐动词与用药动词之间出现否定时，不视为肯定性用药建议
_NEGATION_IN_MEDICATION_ADVICE = re.compile(
    r"(?:勿|不要|避免|禁止|别|不可|不宜|不应|不建议|不推荐|无需|不必|"
    r"不能|不可以)"
)
#: 上一分句里的推荐语气可能支配下一分句的用药（“建议在医生指导下，长期服用…”）
_RECOMMENDATION_VERB = re.compile(
    r"(?<![不无])(?:建议|推荐|必须|务必|请|需要|需|最好|宜|应|可以|可)"
)
_USE_VERB = re.compile(
    r"(?:服用|使用|吃|口服|注射|应用|合用|联用|加用|改用|换用|换成|"
    r"改为|调整为|加量|减量|增量|增至|降至)"
)


def _clause_text(text: str, match: "re.Match[str]") -> str:
    """返回匹配所在完整分句（两侧以标点或换行分隔）。"""
    start = match.start()
    while start > 0 and text[start - 1] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        start -= 1
    end = match.end()
    while end < len(text) and text[end] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        end += 1
    return text[start:end]


def _previous_clause_text(text: str, match: "re.Match[str]") -> str:
    """返回匹配前一个由逗号类标点连接的分句，用于识别跨分句的推荐语气。"""
    start = match.start()
    while start > 0 and text[start - 1] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        start -= 1
    pos = start - 1
    if pos < 0 or text[pos] not in "，、,;":
        return ""
    end = pos
    prev_start = end
    while (
        prev_start > 0
        and text[prev_start - 1] not in _DIAGNOSIS_CLAUSE_DELIMITERS
    ):
        prev_start -= 1
    return text[prev_start:end].strip()


def _has_affirmative_medication_advice(text: str) -> bool:
    """是否含明确的用药推荐（排除推荐动词后带否定的情况）。"""
    for advice in _AFFIRMATIVE_MEDICATION_ADVICE.finditer(text):
        if not _NEGATION_IN_MEDICATION_ADVICE.search(advice.group(1)):
            return True
    return False


def _match_in_safe_medication_context(text: str, match: "re.Match[str]") -> bool:
    """判断剂量匹配是否只是在复述现有用药/说明书信息，而非给出处方。

    只按分句判断，避免相邻分句的“正在服用”等措辞豁免另一分句里的换药建议；
    但上一分句的推荐语气（“建议在医生指导下，”）可以支配当前分句的用药。
    """
    clause = _clause_text(text, match)
    if _has_affirmative_medication_advice(clause):
        return False
    previous = _previous_clause_text(text, match)
    if (
        previous
        and _RECOMMENDATION_VERB.search(previous)
        and _USE_VERB.search(clause)
    ):
        return False
    if _SAFE_PRESCRIPTION_CONTEXT.search(clause):
        return True
    if (
        _INTERACTION_MARKER.search(clause)
        and _INTERACTION_WARNING.search(clause)
    ):
        return True
    # 相互作用提示可能写在上一分句（“与华法林合用时，布洛芬 200mg 会增加出血风险”）
    if (
        previous
        and _INTERACTION_MARKER.search(previous)
        and (
            _INTERACTION_WARNING.search(previous)
            or _INTERACTION_WARNING.search(clause)
        )
    ):
        return True
    return False


def _clause_prefix(text: str, start: int) -> str:
    """返回短语之前、同一分句内的文本。"""
    boundary = start
    while boundary > 0 and text[boundary - 1] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        boundary -= 1
    return text[boundary:start]


#: 「勿超量/不要超过/避免过量」是安全提示，不是误服描述
_OVERDOSE_NEGATIONS = re.compile(
    r"(?:勿|不要|不得|避免|不能|不可|防止|切勿|严禁|别|不|"
    r"没有|未|无)\s*$"
)


def _match_is_overdose(text: str, match: "re.Match[str]") -> bool:
    """匹配所在分句是否在描述误服/过量，而不是给出处方。"""
    boundary = match.start()
    while boundary > 0 and text[boundary - 1] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        boundary -= 1
    tail = match.end()
    while tail < len(text) and text[tail] not in _DIAGNOSIS_CLAUSE_DELIMITERS:
        tail += 1
    clause = text[boundary:tail]
    for marker in _OVERDOSE_PATTERN.finditer(clause):
        prefix = clause[max(0, marker.start() - 4):marker.start()]
        if _OVERDOSE_NEGATIONS.search(prefix):
            # 「请勿超量」「不要超过」是安全提示，不能豁免同句处方
            continue
        return True
    return False


def _contains_definitive_diagnosis(output: str) -> bool:
    """判断是否给出明确诊断，忽略同一分句内「不能诊断为」这类否定表述。"""
    for match in _DEFINITIVE_DIAGNOSIS_PATTERN.finditer(output):
        prefix = _clause_prefix(output, match.start())
        if not _DIAGNOSIS_NEGATION_PATTERN.search(prefix):
            return True
    return False


def _mentions_known_drug(output: str) -> bool:
    try:
        from core.medication_safety import DRUG_GROUPS
    except Exception:
        return False
    lowered = output.lower()
    for group in DRUG_GROUPS.values():
        for _, aliases in group:
            for alias in aliases:
                alias = alias.lower()
                if alias.isascii() and alias.isalpha():
                    if re.search(
                        rf"(?<![a-z]){re.escape(alias)}(?![a-z])", lowered
                    ):
                        return True
                elif alias in lowered:
                    return True
    return False


def _contains_prescription(output: str) -> bool:
    """判断是否给出带剂量/用量的具体处方或用药方案。"""
    known_drug = _mentions_known_drug(output)
    medication_context = (
        any(term in output for term in _MEDICATION_TERMS) or known_drug
    )

    for pattern in _PRESCRIPTION_PATTERNS:
        for match in pattern.finditer(output):
            if _match_is_overdose(output, match):
                continue
            if _match_in_safe_medication_context(output, match):
                continue
            # 片/粒/支也可能是食物（“每天吃一片面包”），
            # 没有明确药名或“药”语境时不算处方
            if (
                _COUNT_UNIT_SUFFIX.search(match.group(0))
                and not medication_context
            ):
                continue
            return True

    if not medication_context:
        return False

    # 有用药语境时，片/粒计数也可能是食物，需再匹配具体用法
    for match in _COUNT_UNIT_PATTERN.finditer(output):
        if _match_is_overdose(output, match):
            continue
        if _match_in_safe_medication_context(output, match):
            continue
        return True

    # 已知药名 + 任意剂量（如“布洛芬 200mg”）
    if known_drug:
        for match in _DOSE_PATTERN.finditer(output):
            if _match_is_overdose(output, match):
                continue
            if _match_in_safe_medication_context(output, match):
                continue
            return True
    return False


class ConstraintValidator:
    """约束验证器"""

    def __init__(
        self,
        agent_constraints_file: str = "constraints/agent_constraints.yaml",
        swarm_constraints_file: str = "constraints/swarm_constraints.yaml"
    ):
        """
        初始化约束验证器

        Args:
            agent_constraints_file: Agent约束定义文件
            swarm_constraints_file: Swarm约束定义文件
        """
        # 加载 Agent 约束
        agent_path = Path(__file__).parent / "agent_constraints.yaml"
        with open(agent_path, 'r', encoding='utf-8') as f:
            self.agent_constraints = yaml.safe_load(f)

        # 加载 Swarm 约束
        swarm_path = Path(__file__).parent / "swarm_constraints.yaml"
        with open(swarm_path, 'r', encoding='utf-8') as f:
            self.swarm_constraints = yaml.safe_load(f)

        logger.info("✅ ConstraintValidator initialized")

    def validate_tool_call(self, agent_id: str, tool_name: str) -> Dict[str, Any]:
        """
        验证工具调用是否允许

        Args:
            agent_id: Agent ID
            tool_name: 工具名称

        Returns:
            {
                "valid": bool,
                "reason": str (如果不允许)
            }
        """
        agent_constraints = self.agent_constraints['agents'].get(agent_id, {})
        allowed_tools = agent_constraints.get('allowed_tools', [])

        # 如果 allowed_tools 为空，表示没有限制
        if not allowed_tools:
            return {"valid": True}

        # 检查工具是否在允许列表中
        if tool_name not in allowed_tools:
            reason = f"工具 {tool_name} 不在 {agent_id} 的推荐工具列表中"
            return {
                "valid": False,
                "reason": reason,
                "severity": "warning"  # 警告级别（不阻止执行，只记录）
            }

        return {"valid": True}

    def validate_output(
        self, agent_id: str, output: str, user_input: str = ""
    ) -> Dict[str, Any]:
        """
        验证输出是否符合约束

        Args:
            agent_id: Agent ID
            output: Agent 的输出文本
            user_input: 原始用户问题；提供时用于判断高危症状语境，
                避免把科普文本里的红旗词当成患者当前症状

        Returns:
            {
                "valid": bool,
                "violations": List[str],
                "auto_fixable": List[str]  # 可以自动修复的违规
            }
        """
        agent_constraints = self.agent_constraints['agents'].get(agent_id, {})
        output_constraints = agent_constraints.get('output_constraints', [])
        common_constraints = self.agent_constraints.get('common', {}).get('output_constraints', [])

        # 合并约束
        all_constraints = output_constraints + common_constraints

        violations = []
        auto_fixable = []

        # 检查免责声明
        if 'must_include_disclaimer' in all_constraints:
            if '免责声明' not in output and 'disclaimer' not in output.lower() and '仅供参考' not in output:
                violations.append("缺少免责声明")
                auto_fixable.append("add_disclaimer")

        # 检查长度限制
        max_length_constraint = next(
            (c for c in all_constraints if isinstance(c, dict) and 'max_response_length' in c),
            None
        )
        if max_length_constraint:
            max_length = max_length_constraint.get('max_response_length')
            if len(output) > max_length:
                violations.append(f"回答过长（{len(output)} > {max_length}字）")
                try:
                    limit = int(max_length)
                except (TypeError, ValueError):
                    limit = 0
                if limit > 0:
                    auto_fixable.append(f"truncate_response:{limit}")

        # 检查高危症状必须建议就医（复用急症红旗规则，识别否定与语境）
        if 'must_recommend_doctor_visit_if_high_risk' in all_constraints:
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
            if (
                detect_red_flags(risk_text)['level'] == 'emergency'
                and not has_visit_recommendation(output)
            ):
                violations.append("高危症状未建议就医")
                auto_fixable.append("add_emergency_warning")

        # 检查是否引用来源（仅 ResearchAgent）
        if 'must_cite_sources' in all_constraints:
            if "指南" not in output and "文献" not in output and "研究" not in output:
                violations.append("未引用来源或证据")

        # 检查禁止行为（YAML 中不同 Agent 使用不同的动作名，统一归类）
        forbidden_actions = agent_constraints.get('forbidden_actions', [])
        if _DIAGNOSIS_ACTIONS.intersection(forbidden_actions):
            if _contains_definitive_diagnosis(output):
                violations.append("包含明确诊断（越界行为）")

        if _PRESCRIPTION_ACTIONS.intersection(forbidden_actions):
            if _contains_prescription(output):
                violations.append("包含具体药物处方（越界行为）")

        return {
            "valid": len(violations) == 0,
            "violations": violations,
            "auto_fixable": auto_fixable
        }

    def validate_task_decomposition(
        self,
        question: str,
        subtasks: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        验证任务分解是否合理（基于 Swarm 约束）

        Args:
            question: 用户问题
            subtasks: LeadAgent 分解的子任务列表

        Returns:
            {
                "valid": bool,
                "issues": List[str],
                "recommendations": List[str]
            }
        """
        rules = self.swarm_constraints['swarm']['task_decomposition_rules']
        issues = []
        recommendations = []

        num_subtasks = len(subtasks)

        # 检查是否匹配规则
        for rule in rules:
            pattern = rule['pattern']
            keywords = pattern.split('|')

            if any(kw in question for kw in keywords):
                max_subtasks = rule.get('max_subtasks')
                min_subtasks = rule.get('min_subtasks', 1)

                if max_subtasks and num_subtasks > max_subtasks:
                    issues.append(
                        f"任务过度分解：{rule['name']} 类型问题最多 {max_subtasks} 个子任务，"
                        f"当前 {num_subtasks} 个"
                    )
                    recommendations.append(f"建议合并为 {max_subtasks} 个任务")

                if min_subtasks and num_subtasks < min_subtasks:
                    issues.append(
                        f"任务分解不足：{rule['name']} 类型问题至少需要 {min_subtasks} 个子任务，"
                        f"当前 {num_subtasks} 个"
                    )

                # 找到匹配规则，停止检查
                break

        return {
            "valid": len(issues) == 0,
            "issues": issues,
            "recommendations": recommendations
        }

    def get_required_agents(self, question: str) -> List[str]:
        """
        根据约束规则推荐必须包含的 Agent

        Args:
            question: 用户问题

        Returns:
            必须包含的 Agent ID 列表
        """
        rules = self.swarm_constraints['swarm']['agent_selection_rules']
        required_agents = []

        for rule in rules:
            # 检查症状关键词：必须做否定感知匹配，避免「无呕血」
            # 「否认自杀想法」被当成高危症状强制升级 Swarm
            if_symptoms = rule.get('if_symptoms', [])
            if if_symptoms:
                from core.triage import has_positive_mention

                if any(
                    has_positive_mention(question, symptom)
                    for symptom in if_symptoms
                ):
                    required_agents.extend(rule['must_include'])
                    logger.info(
                        f"🔒 检测到高危症状，必须包含: {rule['must_include']}"
                        f"（{rule['reason']}）"
                    )

            # 检查一般关键词
            if_keywords = rule.get('if_keywords', [])
            if any(kw in question for kw in if_keywords):
                required_agents.extend(rule['must_include'])
                logger.info(
                    f"💡 检测到关键词，推荐包含: {rule['must_include']}"
                    f"（{rule['reason']}）"
                )

        return list(set(required_agents))  # 去重
