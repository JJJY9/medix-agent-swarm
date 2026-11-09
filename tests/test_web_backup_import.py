"""整库备份导入请求体的前端回归测试。"""
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parent.parent / "web" / "app.js"
).read_text(encoding="utf-8")


def test_backup_import_payload_includes_all_categories():
    for key in (
        "sessions",
        "profiles",
        "health_metrics",
        "metric_targets",
        "reminders",
        "symptoms",
        "medications",
        "medication_logs",
        "medication_history",
        "vaccinations",
        "appointments",
        "lab_results",
        "allergies",
        "care_items",
        "visit_records",
        "conditions",
        "audit_log",
        "checksum",
    ):
        assert f"{key}: data.{key}" in SCRIPT
