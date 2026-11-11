"""就医资料附件 API 测试。"""
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import api_server
import memory
from core.ratelimit import SlidingWindowLimiter
from memory.session_store import SessionStore


@pytest.fixture
def client_and_store(tmp_path, monkeypatch):
    monkeypatch.delenv("MEDIX_ACCESS_TOKEN", raising=False)
    store = SessionStore(str(tmp_path / "sessions.db"))
    monkeypatch.setattr(memory, "get_session_store", lambda: store)
    monkeypatch.setattr(
        api_server, "ATTACHMENT_DIR", tmp_path / "attachments"
    )
    # 上传用例较多；默认限流单独在 test_upload_rate_limit 中验证
    monkeypatch.setattr(
        api_server, "attachment_limiter", SlidingWindowLimiter(1000)
    )
    try:
        yield TestClient(api_server.app), store
    finally:
        store.close()


def _png(width=10, height=10):
    import struct

    ihdr = (
        struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    )
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00\x00\x00\x00"


def _jpeg(width=20, height=10):
    app0 = (
        b"\xff\xe0" + (18).to_bytes(2, "big")
        + b"JFIF\x00" + b"\x01\x01" + b"\x00" * 9
    )
    sof = (
        b"\xff\xc0" + (17).to_bytes(2, "big") + bytes([8])
        + height.to_bytes(2, "big") + width.to_bytes(2, "big")
        + b"\x03" + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    )
    return b"\xff\xd8" + app0 + sof + b"\xff\xd9"


def _webp(width=10, height=10):
    payload = (
        b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    chunk = b"VP8X" + len(payload).to_bytes(4, "little") + payload
    return (
        b"RIFF"
        + (4 + len(chunk)).to_bytes(4, "little")
        + b"WEBP"
        + chunk
    )


def _upload(
    client,
    profile="妈妈",
    kind="visit",
    ref_id=0,
    note="",
    file_name="出院小结.pdf",
    content=b"%PDF-1.4 test content",
    content_type="application/pdf",
):
    return client.post(
        "/api/attachments",
        data={
            "profile": profile,
            "kind": kind,
            "ref_id": str(ref_id),
            "note": note,
        },
        files={
            "file": (file_name, content, content_type),
        },
    )


def test_upload_list_and_download(client_and_store, tmp_path):
    client, _store = client_and_store
    response = _upload(
        client, note="出院小结", file_name="../../出院小结.pdf"
    )
    assert response.status_code == 200
    item = response.json()["attachment"]
    assert item["profile"] == "妈妈"
    assert item["kind"] == "visit"
    assert item["kind_label"] == "就诊资料"
    assert item["original_name"] == "出院小结.pdf"
    assert item["size"] == len(b"%PDF-1.4 test content")
    stored = item["stored_name"]
    assert ".." not in stored and "/" not in stored
    attachment_dir = tmp_path / "attachments"
    assert (attachment_dir / stored).is_file()
    assert not (tmp_path / "出院小结.pdf").exists()

    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    assert listed[0]["original_name"] == "出院小结.pdf"
    assert listed[0]["note"] == "出院小结"
    assert listed[0]["file_missing"] is False
    assert client.get(
        "/api/attachments", params={"profile": "妈妈", "kind": "lab"}
    ).json()["attachments"] == []

    _store.add_attachment(
        "妈妈", "visit", 0, "丢失.pdf", "missing.pdf",
        "application/pdf", 10, "",
    )
    missing_row = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"][0]
    assert missing_row["original_name"] == "丢失.pdf"
    assert missing_row["file_missing"] is True

    download = client.get(f"/api/attachments/{item['id']}/file")
    assert download.status_code == 200
    assert download.content == b"%PDF-1.4 test content"
    assert download.headers["content-type"].startswith("application/pdf")
    assert "inline" in download.headers["content-disposition"]
    assert download.headers["cache-control"] == "no-store"


def test_reject_unsupported_type_and_mismatched_mime(client_and_store):
    client, _store = client_and_store
    text_file = client.post(
        "/api/attachments",
        data={"profile": "妈妈", "kind": "other"},
        files={"file": ("notes.txt", b"hello", "text/plain")},
    )
    assert text_file.status_code == 400
    assert "JPG" in text_file.json()["detail"]

    mismatched = client.post(
        "/api/attachments",
        data={"profile": "妈妈", "kind": "lab"},
        files={"file": ("scan.pdf", b"%PDF-1.4", "image/png")},
    )
    assert mismatched.status_code == 400
    assert "类型" in mismatched.json()["detail"]

    fake_pdf = client.post(
        "/api/attachments",
        data={"profile": "妈妈", "kind": "lab"},
        files={"file": ("fake.pdf", b"<html>hello</html>", "application/pdf")},
    )
    assert fake_pdf.status_code == 400
    assert "内容" in fake_pdf.json()["detail"]

    empty = _upload(client, content=b"")
    assert empty.status_code == 400
    assert "空" in empty.json()["detail"]


@pytest.mark.parametrize(
    "name, content, mime",
    [
        ("scan.jpg", _jpeg(), "image/jpeg"),
        ("scan.png", _png(), "image/png"),
        ("scan.webp", _webp(), "image/webp"),
    ],
)
def test_accepts_supported_image_formats(
    client_and_store, name, content, mime
):
    client, _store = client_and_store
    response = _upload(
        client, kind="other", file_name=name,
        content=content, content_type=mime,
    )
    assert response.status_code == 200, response.text
    item = response.json()["attachment"]
    assert item["mime"] == mime
    assert item["original_name"] == name


def test_rejects_decompression_bomb_dimensions(client_and_store):
    client, _store = client_and_store
    bomb = _upload(
        client, kind="other", file_name="bomb.png",
        content=_png(30_000, 30_000), content_type="image/png",
    )
    assert bomb.status_code == 400
    assert "尺寸" in bomb.json()["detail"]

    too_wide = _upload(
        client, kind="other", file_name="wide.png",
        content=_png(25_000, 10), content_type="image/png",
    )
    assert too_wide.status_code == 400


def test_size_limit_boundary(client_and_store, monkeypatch):
    client, _store = client_and_store
    assert api_server.MAX_ATTACHMENT_UPLOAD_BYTES == 10 * 1024 * 1024
    assert api_server._body_limit_for_path(
        "/api/attachments"
    ) == 11 * 1024 * 1024

    monkeypatch.setattr(api_server, "MAX_ATTACHMENT_UPLOAD_BYTES", 64)
    exact = b"%PDF-" + b"x" * 59
    assert len(exact) == 64
    accepted = _upload(client, content=exact, note="恰好上限")
    assert accepted.status_code == 200
    rejected = _upload(client, content=exact + b"x")
    assert rejected.status_code == 413


def test_upload_ref_id_bounds_and_validity(client_and_store, tmp_path):
    client, _store = client_and_store
    overflow = _upload(client, ref_id=2**63)
    assert overflow.status_code == 400
    assert "编号" in overflow.json()["detail"]
    attachment_dir = tmp_path / "attachments"
    assert not attachment_dir.exists() or not list(
        attachment_dir.glob("*")
    )

    listed = client.get(
        "/api/attachments", params={"ref_id": str(2**63)}
    )
    assert listed.status_code == 400

    missing = _upload(client, kind="visit", ref_id=999999)
    assert missing.status_code == 400
    assert "不存在" in missing.json()["detail"]

    wrong_kind = _upload(client, kind="other", ref_id=1)
    assert wrong_kind.status_code == 400
    assert "不支持" in wrong_kind.json()["detail"]

    visit = client.post("/api/visits", json={
        "profile": "爸爸",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "复诊",
    }).json()["visit"]
    cross = _upload(
        client, profile="妈妈", kind="visit", ref_id=visit["id"]
    )
    assert cross.status_code == 400
    assert "档案" in cross.json()["detail"]


def test_long_file_name_keeps_extension(client_and_store):
    client, _store = client_and_store
    name = "x" * 117 + ".pdf"
    response = _upload(client, file_name=name)
    assert response.status_code == 200, response.text
    item = response.json()["attachment"]
    assert item["original_name"].endswith(".pdf")
    assert len(item["original_name"]) <= 120


def test_store_failure_cleans_up_file(
    client_and_store, tmp_path, monkeypatch
):
    client, store = client_and_store

    def boom(*_args, **_kwargs):
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(store, "add_attachment", boom)
    with TestClient(api_server.app, raise_server_exceptions=False) as raw:
        response = raw.post(
            "/api/attachments",
            data={"profile": "妈妈", "kind": "visit", "ref_id": "0"},
            files={"file": ("小结.pdf", b"%PDF-1.4", "application/pdf")},
        )
    assert response.status_code == 500
    attachment_dir = tmp_path / "attachments"
    assert not attachment_dir.exists() or not list(
        attachment_dir.glob("*")
    )


def test_upload_rate_limit(client_and_store, monkeypatch):
    client, _store = client_and_store
    monkeypatch.setattr(
        api_server, "attachment_limiter", SlidingWindowLimiter(2)
    )
    assert _upload(client, note="1").status_code == 200
    assert _upload(client, note="2").status_code == 200
    limited = _upload(client, note="3")
    assert limited.status_code == 429
    assert limited.json()["code"] == "rate_limited"


def test_search_attachments_by_name_and_note(client_and_store):
    client, _store = client_and_store
    first = _upload(
        client, file_name="出院小结.pdf", note="心内科"
    ).json()["attachment"]
    second = _upload(
        client, file_name="化验单.pdf", note="出院复查 100%"
    ).json()["attachment"]

    def search(query):
        return client.get(
            "/api/attachments", params={"profile": "妈妈", "q": query}
        ).json()["attachments"]

    assert {row["id"] for row in search("出院")} == {
        first["id"], second["id"],
    }
    assert [row["id"] for row in search("心内科")] == [first["id"]]
    assert [row["id"] for row in search("100%")] == [second["id"]]
    assert search("不存在") == []
    assert client.get(
        "/api/attachments",
        params={"profile": "妈妈", "kind": "visits"},
    ).status_code == 400

    third = _upload(
        client, file_name="空格.pdf", note="有  两个空格"
    ).json()["attachment"]
    spaced = client.get(
        "/api/attachments",
        params={"profile": "妈妈", "q": "有  两个"},
    ).json()["attachments"]
    assert [row["id"] for row in spaced] == [third["id"]]

    linked = _store.add_attachment(
        "妈妈", "visit", 123, "关联资料.pdf", "linked.pdf",
        "application/pdf", 10, "",
    )
    only_linked = client.get(
        "/api/attachments",
        params={"profile": "妈妈", "linked": "true"},
    ).json()["attachments"]
    assert [row["id"] for row in only_linked] == [linked["id"]]
    unlinked = client.get(
        "/api/attachments",
        params={"profile": "妈妈", "linked": "false"},
    ).json()["attachments"]
    assert linked["id"] not in {row["id"] for row in unlinked}


def test_bulk_link_attachments_to_visit_and_unlink(client_and_store):
    client, _store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "复诊",
    }).json()["visit"]
    first = _upload(client, kind="visit").json()["attachment"]
    second = _upload(
        client, kind="visit", file_name="复查.pdf"
    ).json()["attachment"]

    linked = client.post("/api/attachments/link", json={
        "ids": [first["id"], second["id"]],
        "ref_id": visit["id"],
    })
    assert linked.status_code == 200, linked.text
    assert linked.json()["updated"] == 2
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    rows = {row["id"]: row for row in listed}
    assert rows[first["id"]]["ref_id"] == visit["id"]
    assert rows[second["id"]]["ref_id"] == visit["id"]
    assert "复诊" in rows[first["id"]]["ref_label"]

    unlinked = client.post("/api/attachments/link", json={
        "ids": [first["id"], second["id"]],
        "ref_id": 0,
    })
    assert unlinked.status_code == 200, unlinked.text
    assert unlinked.json()["updated"] == 2
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    assert {row["ref_id"] for row in listed} == {0}


def test_bulk_link_rejects_cross_profile_and_mixed_kinds(
    client_and_store,
):
    client, _store = client_and_store
    father_visit = client.post("/api/visits", json={
        "profile": "爸爸",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "复诊",
    }).json()["visit"]
    first = _upload(client, kind="visit").json()["attachment"]
    second = _upload(
        client, kind="visit", file_name="复查.pdf"
    ).json()["attachment"]

    cross = client.post("/api/attachments/link", json={
        "ids": [first["id"], second["id"]],
        "ref_id": father_visit["id"],
    })
    assert cross.status_code == 400
    assert "档案" in cross.json()["detail"]
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    assert {row["ref_id"] for row in listed} == {0}

    mother_visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "取药",
    }).json()["visit"]
    lab = _upload(
        client, kind="lab", file_name="化验单.pdf"
    ).json()["attachment"]
    mixed = client.post("/api/attachments/link", json={
        "ids": [first["id"], lab["id"]],
        "ref_id": mother_visit["id"],
    })
    assert mixed.status_code == 400
    assert "同类型" in mixed.json()["detail"]

    other = _upload(client, kind="other").json()["attachment"]
    unsupported = client.post("/api/attachments/link", json={
        "ids": [other["id"]],
        "ref_id": mother_visit["id"],
    })
    assert unsupported.status_code == 400
    assert "同类型" in unsupported.json()["detail"]


def test_bulk_link_validates_ids_and_reference(client_and_store):
    client, _store = client_and_store
    attachment = _upload(client, kind="visit").json()["attachment"]

    assert client.post("/api/attachments/link", json={
        "ids": [], "ref_id": 0,
    }).status_code == 422
    assert client.post("/api/attachments/link", json={
        "ids": list(range(1, 202)), "ref_id": 0,
    }).status_code == 422
    assert client.post("/api/attachments/link", json={
        "ids": [attachment["id"]], "ref_id": -1,
    }).status_code == 422
    missing = client.post("/api/attachments/link", json={
        "ids": [attachment["id"], 999999], "ref_id": 0,
    })
    assert missing.status_code == 404
    assert "不存在" in missing.json()["detail"]
    no_target = client.post("/api/attachments/link", json={
        "ids": [attachment["id"]], "ref_id": 999999,
    })
    assert no_target.status_code == 400
    assert "不存在" in no_target.json()["detail"]


def test_update_attachment_note(client_and_store):
    client, _store = client_and_store
    item = _upload(client, note="旧备注").json()["attachment"]
    updated = client.patch(
        f"/api/attachments/{item['id']}", json={"note": "  新 备注  "}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["attachment"]["note"] == "新 备注"
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    assert listed[0]["note"] == "新 备注"

    cleared = client.patch(
        f"/api/attachments/{item['id']}", json={"note": ""}
    )
    assert cleared.status_code == 200
    assert cleared.json()["attachment"]["note"] == ""
    assert client.patch(
        "/api/attachments/999999", json={"note": "x"}
    ).status_code == 404
    assert client.patch(
        f"/api/attachments/{item['id']}", json={"note": "x" * 301}
    ).status_code == 422

def test_list_attachments_date_range(client_and_store, monkeypatch):
    client, _store = client_and_store
    monkeypatch.setattr(
        SessionStore, "_now",
        staticmethod(lambda: "2026-01-01T09:00:00"),
    )
    first = _upload(client, file_name="一月.pdf").json()["attachment"]
    monkeypatch.setattr(
        SessionStore, "_now",
        staticmethod(lambda: "2026-02-15T09:00:00"),
    )
    second = _upload(client, file_name="二月.pdf").json()["attachment"]

    def search(**params):
        response = client.get(
            "/api/attachments", params={"profile": "妈妈", **params}
        )
        assert response.status_code == 200, response.text
        return {row["id"] for row in response.json()["attachments"]}

    assert search(start="2026-02-01") == {second["id"]}
    assert search(end="2026-01-31") == {first["id"]}
    assert search(
        start="2026-01-01", end="2026-02-28"
    ) == {first["id"], second["id"]}
    assert search(
        start="2026-02-15", end="2026-02-15"
    ) == {second["id"]}
    assert client.get(
        "/api/attachments",
        params={"start": "2026-02-01", "end": "2026-01-31"},
    ).status_code == 400
    assert client.get(
        "/api/attachments", params={"start": "2026/01/01"}
    ).status_code == 400
    assert client.get(
        "/api/attachments", params={"end": "2026-13-01"}
    ).status_code == 400


def test_delete_removes_file(client_and_store, tmp_path):
    client, _store = client_and_store
    item = _upload(client).json()["attachment"]
    path = tmp_path / "attachments" / item["stored_name"]
    assert path.is_file()

    deleted = client.delete(f"/api/attachments/{item['id']}")
    assert deleted.status_code == 200
    assert not path.exists()
    assert client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"] == []
    assert client.get(
        f"/api/attachments/{item['id']}/file"
    ).status_code == 404


def test_delete_keeps_record_when_file_locked(
    client_and_store, tmp_path, monkeypatch
):
    client, _store = client_and_store
    item = _upload(client).json()["attachment"]
    path = tmp_path / "attachments" / item["stored_name"]
    real_unlink = Path.unlink

    def locked(self, *args, **kwargs):
        if self.name == item["stored_name"]:
            raise PermissionError("file is in use")
        return real_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", locked)
    locked_response = client.delete(f"/api/attachments/{item['id']}")
    assert locked_response.status_code == 409
    assert path.is_file()
    assert len(client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]) == 1

    monkeypatch.setattr(Path, "unlink", real_unlink)
    assert client.delete(
        f"/api/attachments/{item['id']}"
    ).status_code == 200
    assert not path.exists()


def test_attachment_requires_token_when_enabled(
    client_and_store, monkeypatch,
):
    client, _store = client_and_store
    item = _upload(client).json()["attachment"]
    monkeypatch.setenv("MEDIX_ACCESS_TOKEN", "secret-token")
    assert client.get(
        f"/api/attachments/{item['id']}/file"
    ).status_code == 401
    assert client.get(
        f"/api/attachments/{item['id']}/file",
        headers={"X-Medix-Token": "secret-token"},
    ).status_code == 200


def test_linked_attachment_detaches_when_visit_deleted(
    client_and_store,
):
    client, _store = client_and_store
    visit = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "复诊",
    }).json()["visit"]
    other = client.post("/api/visits", json={
        "profile": "妈妈",
        "visit_date": datetime.now().strftime("%Y-%m-%d"),
        "diagnosis": "取药",
    }).json()["visit"]
    item = _upload(
        client, kind="visit", ref_id=visit["id"]
    ).json()["attachment"]
    kept = _upload(
        client, kind="visit", ref_id=other["id"]
    ).json()["attachment"]
    assert item["ref_id"] == visit["id"]
    labels = {
        row["id"]: row["ref_label"]
        for row in client.get(
            "/api/attachments", params={"profile": "妈妈"}
        ).json()["attachments"]
    }
    assert "复诊" in labels[item["id"]]
    assert "取药" in labels[kept["id"]]

    assert client.delete(f"/api/visits/{visit['id']}").status_code == 200
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    refs = {row["id"]: row["ref_id"] for row in listed}
    assert refs[item["id"]] == 0
    assert refs[kept["id"]] == other["id"]


def test_linked_attachment_detaches_when_lab_deleted(client_and_store):
    client, _store = client_and_store
    today = datetime.now().strftime("%Y-%m-%d")
    first = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "ALT", "value": 30,
        "tested_at": today, "unit": "U/L",
    }).json()["lab_result"]
    second = client.post("/api/lab-results", json={
        "profile": "妈妈", "item": "AST", "value": 20,
        "tested_at": today, "unit": "U/L",
    }).json()["lab_result"]
    item = _upload(
        client, kind="lab", ref_id=first["id"]
    ).json()["attachment"]
    kept = _upload(
        client, kind="lab", ref_id=second["id"]
    ).json()["attachment"]
    labels = {
        row["id"]: row["ref_label"]
        for row in client.get(
            "/api/attachments", params={"profile": "妈妈"}
        ).json()["attachments"]
    }
    assert "ALT 30 U/L" in labels[item["id"]]
    assert "AST 20 U/L" in labels[kept["id"]]

    assert client.delete(
        f"/api/lab-results/{first['id']}"
    ).status_code == 200
    listed = client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"]
    refs = {row["id"]: row["ref_id"] for row in listed}
    assert refs[item["id"]] == 0
    assert refs[kept["id"]] == second["id"]


def test_profile_rename_updates_attachments(client_and_store):
    client, _store = client_and_store
    client.post("/api/profiles", json={
        "name": "妈妈", "context": {"age": "60"},
    })
    _upload(client)
    renamed = client.patch(
        "/api/profiles/妈妈", json={"new_name": "母亲"}
    )
    assert renamed.status_code == 200
    assert client.get(
        "/api/attachments", params={"profile": "妈妈"}
    ).json()["attachments"] == []
    assert len(client.get(
        "/api/attachments", params={"profile": "母亲"}
    ).json()["attachments"]) == 1


def test_attachment_only_profile_is_listed_and_renameable(client_and_store):
    client, _store = client_and_store
    _upload(client, profile="临时档案")
    assert "临时档案" in client.get(
        "/api/profile-names"
    ).json()["profiles"]

    renamed = client.patch(
        "/api/profiles/临时档案", json={"new_name": "归档资料"}
    )
    assert renamed.status_code == 200
    listed = client.get(
        "/api/attachments", params={"profile": "归档资料"}
    ).json()["attachments"]
    assert len(listed) == 1


def test_backup_excludes_attachment_metadata(client_and_store):
    client, store = client_and_store
    assert _upload(client).status_code == 200
    payload = store.export_data()
    assert "attachments" not in payload
