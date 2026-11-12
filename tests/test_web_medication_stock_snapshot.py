"""编辑用药表单不得用旧库存快照覆盖并发打卡。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_edit_form_records_stock_snapshot():
    assert "let medicationEditSnapshot = null;" in SCRIPT
    assert "medicationEditSnapshot = {" in SCRIPT
    assert "stock: els.medicationStock.value.trim()" in SCRIPT
    assert "stockUnit: els.medicationStockUnit.value.trim()" in SCRIPT
    assert "medicationEditSnapshot = null;" in SCRIPT.split(
        "function resetMedicationForm", 1
    )[1]


def test_unchanged_stock_is_not_sent_on_update():
    assert "stockText === medicationEditSnapshot.stock" in SCRIPT
    assert "stockUnit === medicationEditSnapshot.stockUnit" in SCRIPT
    assert "delete payload.stock;" in SCRIPT
    assert "delete payload.stock_unit;" in SCRIPT
