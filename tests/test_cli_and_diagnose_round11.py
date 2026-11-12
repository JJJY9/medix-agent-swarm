"""CLI 参数、诊断脚本与端到端脚本的边界回归。"""
from pathlib import Path

import pytest

import api_server

ROOT = Path(__file__).resolve().parents[1]


def test_invalid_port_is_rejected_by_argparse(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["api_server.py", "--port", "70000"])
    with pytest.raises(SystemExit) as exc:
        api_server.main()
    assert exc.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_diagnose_reconfigures_stdio_before_preflight():
    script = (ROOT / "scripts" / "diagnose.py").read_text(encoding="utf-8")
    call = script.index("_missing = _missing_dependencies()")
    reconfigure = script.index(
        'reconfigure(encoding="utf-8", errors="replace")'
    )
    # 缺依赖时要在打印 emoji 之前就把输出切到 UTF-8
    assert reconfigure < call


def test_kb_default_db_path_is_absolute():
    pytest.importorskip("sentence_transformers")
    from knowledge.milvus_kb import MedicalKnowledgeBase

    path = Path(MedicalKnowledgeBase.DEFAULT_DB_PATH)
    assert path.is_absolute()
    assert path.parent.name == "data"
    assert path.parent.parent.name == "knowledge"


def test_kb_initialize_uses_resolved_db_path():
    script = (ROOT / "knowledge" / "milvus_kb.py").read_text(
        encoding="utf-8"
    )
    # 默认参数改为 None 后，后续每处都必须使用解析后的 self.db_path
    assert "Path(self.db_path).parent.mkdir" in script
    assert "_create_vector_store(self.db_path)" in script


def test_web_e2e_sends_access_token():
    script = (ROOT / "examples" / "test_web_e2e.py").read_text(
        encoding="utf-8"
    )
    assert 'os.getenv("MEDIX_ACCESS_TOKEN")' in script
    assert '"X-Medix-Token": TOKEN' in script
    assert "resp.status_code != 200" in script
    assert "未收到 result 事件" in script
