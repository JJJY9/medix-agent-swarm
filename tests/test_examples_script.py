"""examples/test_all.py 的退出码契约。"""
import py_compile
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "examples" / "test_all.py"
)
SOURCE = SCRIPT.read_text(encoding="utf-8")


def test_examples_returns_nonzero_when_tests_fail():
    assert "async def main() -> int:" in SOURCE
    assert "return failed" in SOURCE
    assert "raise SystemExit(1 if asyncio.run(main()) else 0)" in SOURCE


def test_examples_script_compiles(tmp_path):
    py_compile.compile(
        str(SCRIPT),
        cfile=str(tmp_path / "test_all.pyc"),
        doraise=True,
    )
