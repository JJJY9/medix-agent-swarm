"""第十七轮审查：短期记忆去重、历史搜索与脚本退出码回归测试。"""
from pathlib import Path

from core.auth import _decode_header
from core.usage import merge_usage
from memory.entropy_manager import MemoryEntropyManager
from memory.short_term import ConversationHistory

ROOT = Path(__file__).resolve().parents[1]


def test_deduplicate_keeps_repeated_turn_pairs():
    manager = MemoryEntropyManager()
    messages = [
        {"role": "user", "content": "重复问题"},
        {"role": "assistant", "content": "第一次回答"},
        {"role": "user", "content": "重复问题"},
        {"role": "assistant", "content": "第二次回答"},
    ]

    cleaned = manager.deduplicate_messages(messages)

    # 重复提问是真实轮次：问答必须成对保留，不能只删用户消息
    assert [item["content"] for item in cleaned] == [
        "重复问题", "第一次回答", "重复问题", "第二次回答",
    ]


def test_auto_clean_keeps_latest_round_after_compression():
    manager = MemoryEntropyManager()
    messages = []
    for index in range(10):
        messages.append(
            {"role": "user", "content": f"问题{index}" + "长" * 40}
        )
        messages.append(
            {"role": "assistant", "content": f"回答{index}" + "长" * 40}
        )

    cleaned = manager.auto_clean(messages, max_messages=4)

    assert cleaned[-1]["content"].startswith("回答9")
    assert any(
        item["role"] == "user" and item["content"].startswith("问题9")
        for item in cleaned
    )


def test_search_history_formats_summary_and_turns():
    from importlib.util import module_from_spec, spec_from_file_location

    path = (
        ROOT / ".claude" / "skills" / "search-history"
        / "script" / "search.py"
    )
    spec = spec_from_file_location("search_history_format", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)

    text = module.format_history([
        {"role": "system", "content": "早期对话摘要"},
        {"role": "user", "content": "第一问"},
        {"role": "assistant", "content": "第一答"},
        {"role": "user", "content": "第二问"},
        {"role": "assistant", "content": "第二答"},
    ])

    assert "【历史摘要】" in text
    assert "早期对话摘要" in text
    assert "【轮次 1】" in text
    assert "【轮次 2】" in text


def test_merge_usage_ignores_negative_tokens():
    total = merge_usage({}, {
        "prompt_tokens": -100,
        "completion_tokens": -50,
        "total_tokens": -150,
    })

    assert total["prompt_tokens"] == 0
    assert total["completion_tokens"] == 0
    assert total["total_tokens"] == 0


def test_screenshot_script_waits_for_streaming_and_fails_loudly():
    script = (ROOT / "tools" / "capture_screenshots.js").read_text(
        encoding="utf-8"
    )

    assert "classList.contains('streaming')" in script
    assert "process.exitCode = 1" in script
    assert "等待会诊答复超时" in script


def test_import_script_is_idempotent_and_exits_nonzero():
    script = (
        ROOT / "knowledge" / "scripts" / "import_hardcoded_data.py"
    ).read_text(encoding="utf-8")

    assert "replace_existing=True" in script
    assert "sys.exit(main())" in script
    assert "result = service.rebuild(replace_existing=not args.append)" not in script


def test_cli_disclaimer_default():
    text = (ROOT / "main.py").read_text(encoding="utf-8")

    assert 'result.get("disclaimer")' in text
    assert "以上信息仅供参考" in text


def test_recent_history_labels_system_and_tool_messages():
    from core.context import format_context

    text = format_context({
        "recent_history": [
            {"role": "system", "content": "早期摘要"},
            {"role": "tool", "content": "工具输出"},
            {"role": "assistant", "content": "回答"},
        ],
    }, include_internal=True)

    assert "历史摘要：早期摘要" in text
    assert "工具结果：工具输出" in text
    assert "助手：回答" in text


def test_session_summary_default_dir_is_absolute():
    from memory.session_summary import SessionSummaryManager

    assert SessionSummaryManager().base_dir.is_absolute()


def test_unknown_identity_file_is_backed_up(tmp_path):
    from memory.agent_identity import AgentIdentityManager

    manager = AgentIdentityManager(str(tmp_path))
    path = manager._get_identity_path("demo")
    path.write_text("这不是可识别的身份文件", encoding="utf-8")
    identity = manager.create_identity(
        "demo", "consultation", ["问诊"], ["高血压"]
    )

    manager.save_identity(identity)

    backup = path.with_name("IDENTITY.md.bak")
    assert backup.read_text(encoding="utf-8") == "这不是可识别的身份文件"
    assert "# " in path.read_text(encoding="utf-8")


def test_e2e_non_ascii_token_header_roundtrip():
    from importlib.util import module_from_spec, spec_from_file_location

    path = ROOT / "examples" / "test_web_e2e.py"
    spec = spec_from_file_location("web_e2e_header", path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    token = "中文令牌🔒"

    encoded = module._header_token(token)

    assert _decode_header(encoded) == token
