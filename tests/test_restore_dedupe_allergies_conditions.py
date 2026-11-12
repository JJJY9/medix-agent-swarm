"""备份恢复与 CSV 导入保留同一过敏原/慢病的多次记录。"""
from memory.session_store import SessionStore


def test_backup_import_keeps_distinct_allergy_reactions(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.add_allergy(
            "妈妈", "青霉素", "皮疹", "mild", "drug",
            noted_at="2010-05-01",
        )
        source.add_allergy(
            "妈妈", "青霉素", "过敏性休克", "severe", "drug",
            noted_at="2020-08-12",
        )
        data = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        first = target.import_data(data)
        rows = target.list_allergies(profile="妈妈")
        second = target.import_data(data)
    finally:
        target.close()

    assert first["allergies"] == 2
    assert {
        (row["reaction"], row["severity"], row["noted_at"]) for row in rows
    } == {
        ("皮疹", "mild", "2010-05-01"),
        ("过敏性休克", "severe", "2020-08-12"),
    }
    assert second["allergies"] == 0


def test_csv_allergy_import_without_created_at_keeps_reactions(tmp_path):
    payload = {
        "allergies": [
            {
                "profile": "爸爸",
                "substance": "海鲜",
                "reaction": "荨麻疹",
                "severity": "moderate",
                "kind": "food",
                "noted_at": "2018-06-01",
            },
            {
                "profile": "爸爸",
                "substance": "海鲜",
                "reaction": "喉头水肿",
                "severity": "severe",
                "kind": "food",
                "noted_at": "2023-09-15",
            },
        ]
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        rows = store.list_allergies(profile="爸爸")
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["allergies"] == 2
    assert {row["reaction"] for row in rows} == {"荨麻疹", "喉头水肿"}
    assert second["allergies"] == 0


def test_backup_import_keeps_same_condition_recurrences(tmp_path):
    source = SessionStore(str(tmp_path / "source.db"))
    try:
        source.add_condition(
            "奶奶", "高血压", "I10", diagnosed_at="2015-03-01",
            status="controlled", severity="moderate",
        )
        source.add_condition(
            "奶奶", "高血压", "I10", diagnosed_at="2024-11-20",
            status="active", severity="severe",
        )
        data = source.export_data()
    finally:
        source.close()

    target = SessionStore(str(tmp_path / "target.db"))
    try:
        first = target.import_data(data)
        rows = target.list_conditions(profile="奶奶")
        second = target.import_data(data)
    finally:
        target.close()

    assert first["conditions"] == 2
    assert {
        (row["diagnosed_at"], row["status"], row["severity"])
        for row in rows
    } == {
        ("2015-03-01", "controlled", "moderate"),
        ("2024-11-20", "active", "severe"),
    }
    assert second["conditions"] == 0


def test_csv_condition_import_without_created_at_keeps_recurrences(tmp_path):
    payload = {
        "conditions": [
            {
                "profile": "爷爷",
                "name": "痛风",
                "diagnosed_at": "2016-01-01",
                "status": "controlled",
                "severity": "moderate",
            },
            {
                "profile": "爷爷",
                "name": "痛风",
                "diagnosed_at": "2025-02-02",
                "status": "active",
                "severity": "severe",
            },
        ]
    }
    store = SessionStore(str(tmp_path / "sessions.db"))
    try:
        first = store.import_data(payload)
        rows = store.list_conditions(profile="爷爷")
        second = store.import_data(payload)
    finally:
        store.close()

    assert first["conditions"] == 2
    assert {row["diagnosed_at"] for row in rows} == {
        "2016-01-01", "2025-02-02",
    }
    assert second["conditions"] == 0
