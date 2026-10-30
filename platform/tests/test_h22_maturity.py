from __future__ import annotations

import os
from pathlib import Path


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str):
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_literature_pdf_absent_asks_for_copyable_file(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "pdf0")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "No File Paper"}, headers=h)
    assert lit.status_code == 200, lit.text
    r = client.get(f"/api/v1/literature/{lit.json()['id']}/pdf", headers=h)
    assert r.status_code == 404, r.text
    assert "PDF" in r.json()["detail"]
    assert "上传" in r.json()["detail"]


def test_literature_pdf_missing_on_disk_is_readable(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "pdfm")
    up = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("gone.pdf", b"%PDF-1.4 gone", "application/pdf"))],
        headers=h,
    )
    assert up.status_code == 200, up.text
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    items = listed.get("items") if isinstance(listed, dict) else listed
    iid = items[0]["id"]
    root = Path(os.environ["FILE_ROOT"])
    stored = [p for p in root.rglob("*") if p.is_file()]
    assert stored, "upload should store a file under FILE_ROOT"
    for p in stored:
        p.unlink()
    r = client.get(f"/api/v1/literature/{iid}/pdf", headers=h)
    assert r.status_code == 404, r.text
    assert "缺失" in r.json()["detail"]
    assert "上传" in r.json()["detail"]


def test_language_xor_and_empty_write_mode_unchanged(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "lg22")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Xor Paper"}, headers=h).json()
    note = client.post(f"/api/v1/literature/{lit['id']}/notes", json={"content_md": "old"}, headers=h).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Xor Ms", "kind": "other", "content_md": "KEEP"},
        headers=h,
    ).json()
    both = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={
            "text": "both",
            "manuscript_id": ms["id"],
            "write_mode": "append",
            "note_id": note["id"],
        },
        headers=h,
    )
    assert both.status_code == 400, both.text
    assert "手稿或笔记" in both.json()["detail"]
    idle = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "no auto note", "literature_item_id": lit["id"]},
        headers=h,
    )
    assert idle.status_code == 200, idle.text
    assert not idle.json().get("wrote_note")
    skip = client.post(
        f"/api/v1/labs/{lab}/language/polish",
        json={"text": "SHOULD_NOT_LAND", "manuscript_id": ms["id"], "write_mode": ""},
        headers=h,
    )
    assert skip.status_code == 200, skip.text
    assert not skip.json().get("wrote")
    body = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert "KEEP" in body["content_md"]
    assert "SHOULD_NOT_LAND" not in body["content_md"]
