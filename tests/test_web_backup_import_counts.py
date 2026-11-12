"""手动导入备份时，仅含健康数据的文件也应被接受。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_backup_import_counts_all_data_types():
    start = SCRIPT.index(
        "const count = Array.isArray(data.sessions)"
    )
    end = SCRIPT.index("window.confirm(`导入 ${summary}", start)
    body = SCRIPT[start:end]

    for key in (
        "lab_results", "allergies", "conditions", "care_items",
        "visit_records", "vaccinations", "appointments",
        "medication_history",
    ):
        assert f'"{key}"' in body
    assert "extraCounts.some(([, , value]) => value > 0)" in body
    assert "...extraCounts" in body
