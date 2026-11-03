"""LLM 工具调用参数解析的单元测试。"""
from core.llm_client import parse_tool_call_arguments


def test_valid_json_object_is_parsed():
    assert parse_tool_call_arguments('{"query": "高血压"}') == {"query": "高血压"}


def test_empty_arguments_become_empty_dict():
    assert parse_tool_call_arguments(None) == {}
    assert parse_tool_call_arguments("") == {}


def test_malformed_json_is_swallowed():
    # 模型偶尔会返回带注释或截断的 JSON，不能让它打断整轮对话
    assert parse_tool_call_arguments('{"query": "高血压"') == {}
    assert parse_tool_call_arguments("not json at all") == {}


def test_non_object_json_is_rejected():
    assert parse_tool_call_arguments("[1, 2, 3]") == {}
    assert parse_tool_call_arguments('"text"') == {}
