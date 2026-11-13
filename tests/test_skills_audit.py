"""技能加载、参数校验与配置合并的回归测试（离线）。"""
import asyncio
from pathlib import Path

import pytest

from agents.consultation_agent import ConsultationAgent
from agents.research_agent import ResearchAgent
from agents.skill_registry_mixin import SkillRegistryMixin
from core.skill_loader import discover_skills, parse_skill_md
from core.skill_registry import SkillParameter, SkillRegistry, _coerce_argument


def test_coerce_argument_types():
    assert _coerce_argument("integer", "5") == 5
    assert _coerce_argument("integer", 2.0) == 2
    assert _coerce_argument("number", "2.5") == 2.5
    assert _coerce_argument("boolean", "false") is False
    assert _coerce_argument("array", (1, 2)) == [1, 2]
    assert _coerce_argument("object", {"a": 1}) == {"a": 1}
    assert _coerce_argument("string", 3) == "3"

    with pytest.raises(ValueError):
        _coerce_argument("integer", 2.5)


def test_skill_registry_rejects_unknown_and_missing_arguments():
    registry = SkillRegistry()
    calls = []

    async def skill(query: str, limit: int = 5):
        calls.append((query, limit))
        return {"ok": True}

    registry.register(
        "demo",
        skill,
        "demo skill",
        [
            SkillParameter("query", "string", "查询", required=True),
            SkillParameter("limit", "integer", "上限", required=False),
        ],
    )

    unknown = asyncio.run(registry.execute("demo", query="x", top_k=3))
    missing = asyncio.run(registry.execute("demo"))
    blank = asyncio.run(registry.execute("demo", query="   "))

    assert unknown["success"] is False
    assert "未知参数" in unknown["error"] and "top_k" in unknown["error"]
    assert missing["success"] is False
    assert "缺少必填参数" in missing["error"]
    assert blank["success"] is False
    assert "缺少必填参数" in blank["error"]
    assert calls == []


def test_skill_annotation_types_and_varargs():
    from agents.skill_registry_mixin import SkillRegistryMixin

    assert SkillRegistryMixin._annotation_to_skill_type("bool") == "boolean"
    assert SkillRegistryMixin._annotation_to_skill_type("list[int]") == "array"
    assert SkillRegistryMixin._annotation_to_skill_type(list[int]) == "array"
    assert SkillRegistryMixin._annotation_to_skill_type(dict[str, int]) == "object"
    assert SkillRegistryMixin._annotation_to_skill_type(int | None) == "integer"
    assert SkillRegistryMixin._annotation_to_skill_type("Dict[str, int]") == "object"
    # PEP 563 下的字符串组合写法
    assert SkillRegistryMixin._annotation_to_skill_type(
        "Optional[int]"
    ) == "integer"
    assert SkillRegistryMixin._annotation_to_skill_type(
        "int | None"
    ) == "integer"
    assert SkillRegistryMixin._annotation_to_skill_type(
        "Optional[list[int]]"
    ) == "array"
    assert SkillRegistryMixin._annotation_to_skill_type(
        "Union[int, str]"
    ) == ""
    # 容器元素里的联合类型不能按顶层联合切分
    assert SkillRegistryMixin._annotation_to_skill_type(
        "list[int | None]"
    ) == "array"
    assert SkillRegistryMixin._annotation_to_skill_type(
        "dict[str, int | None]"
    ) == "object"
    assert SkillRegistryMixin._annotation_to_skill_type(
        "tuple[int | None, ...]"
    ) == "array"

    mixin = SkillRegistryMixin()

    def skill(age: int, tags: list[str], *extra, **options):
        pass

    parameters = mixin._infer_skill_parameters({"function": skill})

    assert [param.name for param in parameters] == ["age", "tags"]
    assert [param.type for param in parameters] == ["integer", "array"]


def test_skill_registry_coerces_registered_types():
    registry = SkillRegistry()
    calls = []

    async def skill(limit: int):
        calls.append(limit)
        return {"ok": True}

    registry.register(
        "demo", skill, "demo",
        [SkillParameter("limit", "integer", "上限")],
    )

    result = asyncio.run(registry.execute("demo", limit="2"))

    assert result == {"ok": True}
    assert calls == [2]


class DummyAgent(SkillRegistryMixin):
    def __init__(self):
        self.skill_registry = SkillRegistry()


def test_infer_skill_parameters_uses_annotations():
    def sample(query: str, limit: int, ratio: float, active: bool, tags: list):
        pass

    agent = DummyAgent()
    parameters = agent._infer_skill_parameters({
        "function": sample,
        "metadata": {"name": "sample"},
    })
    types = {param.name: param.type for param in parameters}

    assert types == {
        "query": "string",
        "limit": "integer",
        "ratio": "number",
        "active": "boolean",
        "tags": "array",
    }


def test_parse_skill_md_rejects_non_mapping(tmp_path):
    path = tmp_path / "SKILL.md"
    path.write_text("---\n- search-knowledge\n---\n", encoding="utf-8")

    assert parse_skill_md(path) is None


def test_discover_skills_skips_broken_skill(tmp_path):
    skills_dir = tmp_path / ".claude" / "skills"
    good = skills_dir / "good-skill" / "script"
    bad = skills_dir / "bad-skill" / "script"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)

    (skills_dir / "good-skill" / "SKILL.md").write_text(
        "---\nname: good-skill\ndescription: ok\n---\n", encoding="utf-8"
    )
    (good / "good_skill.py").write_text(
        "def good_skill(query):\n    return query\n", encoding="utf-8"
    )
    (skills_dir / "bad-skill" / "SKILL.md").write_text(
        "---\nname: bad-skill\ndescription: ok\n---\n", encoding="utf-8"
    )
    (bad / "bad_skill.py").write_text(
        "raise RuntimeError('boom')\n", encoding="utf-8"
    )

    discovered = discover_skills(tmp_path)

    assert [item["name"] for item in discovered] == ["good-skill"]


def test_consultation_agent_merges_default_config():
    agent = ConsultationAgent({"max_iterations": 3})

    assert agent.config["max_iterations"] == 3
    assert agent.config["temperature"] == 0.8
    assert agent.config["model"] == "openai_compatible"


def test_research_agent_prefers_labeled_evidence_level():
    agent = object.__new__(ResearchAgent)

    result = asyncio.run(agent.post_process_result(
        {},
        "证据等级：C 级（参考了 A 级指南，但本次证据不足）",
    ))

    assert result["evidence_level"] == "C"
