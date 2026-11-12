"""备份恢复与 CSV 导入保留内容不同的同键记录。"""
from memory.session_store import SessionStore


def _source_with_records(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    source.add_care_item(
        "妈妈", "复查血压", 30, next_due_at="2026-12-01",
    )
    source.add_care_item(
        "妈妈", "复查血压", 14, next_due_at="2026-12-15",
    )
    source.add_appointment(
        "妈妈", "口腔复查", doctor="张医生",
        scheduled_at="2026-12-02T09:00",
    )
    source.add_appointment(
        "妈妈", "口腔复查", doctor="李医生",
        scheduled_at="2026-12-02T09:00",
    )
    source.add_vaccination(
        "妈妈", "流感疫苗", 1, vaccinated_at="2026-10-01",
        next_due="2027-10-01",
    )
    source.add_vaccination(
        "妈妈", "流感疫苗", 1, vaccinated_at="2026-10-01",
        next_due="2028-10-01",
    )
    source.add_visit_record(
        "妈妈", visit_date="2026-11-01", department="心内科",
        doctor="王医生", diagnosis="高血压",
    )
    source.add_visit_record(
        "妈妈", visit_date="2026-11-01", department="心内科",
        doctor="赵医生", diagnosis="高血压",
    )
    return source


def test_backup_import_keeps_same_key_distinct_records(tmp_path):
    source = _source_with_records(tmp_path)
    try:
        data = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        first = target.import_data(data)
        counts = (
            len(target.list_care_items(profile="妈妈")),
            len(target.list_appointments(profile="妈妈")),
            len(target.list_vaccinations(profile="妈妈")),
            len(target.list_visit_records(profile="妈妈")),
        )
        second = target.import_data(data)
    finally:
        target.close()

    assert first["care_items"] == 2
    assert first["appointments"] == 2
    assert first["vaccinations"] == 2
    assert first["visit_records"] == 2
    assert counts == (2, 2, 2, 2)
    assert second["care_items"] == 0
    assert second["appointments"] == 0
    assert second["vaccinations"] == 0
    assert second["visit_records"] == 0


def test_csv_style_import_keeps_distinct_content(tmp_path):
    payload = {
        "care_items": [
            {
                "profile": "爸爸", "title": "复查血糖",
                "interval_days": 30, "next_due_at": "2026-12-01",
            },
            {
                "profile": "爸爸", "title": "复查血糖",
                "interval_days": 7, "next_due_at": "2026-12-08",
            },
        ],
        "appointments": [
            {
                "profile": "爸爸", "title": "复诊",
                "scheduled_at": "2026-12-03T09:00", "doctor": "张医生",
            },
            {
                "profile": "爸爸", "title": "复诊",
                "scheduled_at": "2026-12-03T09:00", "doctor": "李医生",
            },
        ],
        "vaccinations": [
            {
                "profile": "爸爸", "vaccine_name": "肺炎疫苗",
                "dose_number": 1, "vaccinated_at": "2026-10-01",
                "next_due": "2027-10-01",
            },
            {
                "profile": "爸爸", "vaccine_name": "肺炎疫苗",
                "dose_number": 1, "vaccinated_at": "2026-10-01",
                "next_due": "2028-10-01",
            },
        ],
        "visit_records": [
            {
                "profile": "爸爸", "visit_date": "2026-11-01",
                "diagnosis": "高血压", "doctor": "王医生",
            },
            {
                "profile": "爸爸", "visit_date": "2026-11-01",
                "diagnosis": "高血压", "doctor": "赵医生",
            },
        ],
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        counts = (
            len(store.list_care_items(profile="爸爸")),
            len(store.list_appointments(profile="爸爸")),
            len(store.list_vaccinations(profile="爸爸")),
            len(store.list_visit_records(profile="爸爸")),
        )
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["care_items"] == 2
    assert first["appointments"] == 2
    assert first["vaccinations"] == 2
    assert first["visit_records"] == 2
    assert counts == (2, 2, 2, 2)
    assert second["care_items"] == 0
    assert second["appointments"] == 0
    assert second["vaccinations"] == 0
    assert second["visit_records"] == 0
