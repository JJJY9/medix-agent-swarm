"""用药清单重建后恢复键盘焦点。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_medication_focus_helpers_exist():
    assert "function captureMedicationFocusLabel()" in SCRIPT
    assert "function restoreMedicationFocusLabel(state)" in SCRIPT
    assert "root.contains(active)" in SCRIPT
    # 条目被删除时按原位置聚焦下一条，焦点不掉到 body
    assert "Math.max(Number(state.index) || 0, 0)" in SCRIPT


def test_load_medications_restores_focus():
    start = SCRIPT.index("async function loadMedications()")
    end = SCRIPT.index("function renderMedicationLogs", start)
    body = SCRIPT[start:end]
    assert "const focusLabel = captureMedicationFocusLabel();" in body
    assert "restoreMedicationFocusLabel(focusLabel);" in body


def test_history_toggle_restores_focus():
    start = SCRIPT.index("async function toggleMedicationHistory(item)")
    end = SCRIPT.index("async function loadMedications()", start)
    body = SCRIPT[start:end]
    assert body.count("captureMedicationFocusLabel()") == 1
    assert body.count("restoreMedicationFocusLabel(focusLabel);") == 3
