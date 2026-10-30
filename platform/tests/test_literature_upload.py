from __future__ import annotations

from fengsuan.app.services.literature import find_duplicate
from fengsuan.app.services.pdfparse import clean_extracted_text, guess_literature_title, looks_like_running_header


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


def _pdf(title_line: str = "An Efficient Regenerated Cross-Modal Hashing") -> bytes:
    return (
        b"%PDF-1.4\n1 0 obj<<>>endobj\n"
        + title_line.encode("utf-8")
        + b"\ntrailer<<>>\n"
    )


def test_upload_same_pdf_twice_reuses_item(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "updup")
    payload = _pdf()
    first = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("An_Efficient_Hashing.pdf", payload, "application/pdf"))],
        headers=h,
    )
    assert first.status_code == 200, first.text
    one = first.json()["items"][0]
    assert one["already_in_library"] is False
    again = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("An_Efficient_Hashing.pdf", payload, "application/pdf"))],
        headers=h,
    )
    assert again.status_code == 200, again.text
    two = again.json()["items"][0]
    assert two["already_in_library"] is True
    assert two["id"] == one["id"]
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h).json()
    items = listed.get("items") if isinstance(listed, dict) else listed
    assert len(items) == 1


def test_upload_skips_journal_header_title(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "uptitle")
    body = (
        b"%PDF-1.4\n"
        b"10382 IEEE TRANSACTIONS ON PATTERN ANALYSIS AND MACHINE INTELLIGENCE, VOL. 48, NO. 9\n"
        b"An Efficient Regenerated Cross-Modal Hashing Method\n"
        b"trailer<<>>\n"
    )
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("An_Efficient_Regenerated_Cross_Modal_Hashing.pdf", body, "application/pdf"))],
        headers=h,
    )
    assert r.status_code == 200, r.text
    title = r.json()["items"][0]["title"]
    assert "IEEE TRANSACTIONS" not in title.upper()
    assert "Hashing" in title or "hashing" in title.lower()


def test_clean_extracted_text_drops_nul():
    assert "\x00" not in clean_extracted_text("Wavelet\x00 structure")
    assert clean_extracted_text("Wavelet\x00 structure") == "Wavelet structure"
    assert clean_extracted_text("") == ""


def test_guess_title_prefers_filename_over_header():
    header = "10382 IEEE TRANSACTIONS ON PATTERN ANALYSIS AND MACHINE INTELLIGENCE, VOL. 48, NO. 9, SEPTEMBER 2026"
    assert looks_like_running_header(header)
    title = guess_literature_title([header], "An_Efficient_Regenerated_Cross_Modal_Hashing.pdf", header)
    assert "IEEE TRANSACTIONS" not in title.upper()
    assert "Efficient" in title


def test_backfill_hash_then_reuse(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "upback")
    payload = _pdf("Backfill Hash Title")
    first = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("Backfill_Hash_Title.pdf", payload, "application/pdf"))],
        headers=h,
    )
    assert first.status_code == 200, first.text
    item_id = first.json()["items"][0]["id"]
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.literature import LiteratureFile

    db = SessionLocal()
    try:
        for row in db.query(LiteratureFile).filter(LiteratureFile.item_id == item_id).all():
            row.content_sha256 = None
        db.commit()
    finally:
        db.close()
    again = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("Backfill_Hash_Title.pdf", payload, "application/pdf"))],
        headers=h,
    )
    assert again.status_code == 200, again.text
    two = again.json()["items"][0]
    assert two["already_in_library"] is True
    assert two["id"] == item_id


def test_upload_strips_nul_page_text(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "upnul")

    def fake_extract(_content):
        return ["Wavelet\x00 structure-texture-aware", "Method\x00"], "ok", None, {}

    monkeypatch.setattr("fengsuan.app.api.v1.literature.extract_pdf_full", fake_extract)
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("Wavelet_SR.pdf", _pdf("Wavelet structure"), "application/pdf"))],
        headers=h,
    )
    assert r.status_code == 200, r.text
    item = r.json()["items"][0]
    assert item.get("id")
    assert "\x00" not in (item.get("title") or "")
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.literature import LiteratureFilePage

    db = SessionLocal()
    try:
        pages = db.query(LiteratureFilePage).filter(LiteratureFilePage.item_id == item["id"]).all()
        assert pages
        assert all("\x00" not in (p.text or "") for p in pages)
        assert any("Wavelet" in (p.text or "") for p in pages)
    finally:
        db.close()


def test_upload_one_bad_file_does_not_fail_batch(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "upmix")

    def boom(content):
        if b"BOOM" in content:
            raise RuntimeError("nul")
        return ["ok page"], "ok", None, {}

    monkeypatch.setattr("fengsuan.app.api.v1.literature.extract_pdf_full", boom)
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[
            ("files", ("good.pdf", _pdf("Good Paper"), "application/pdf")),
            ("files", ("bad.pdf", b"%PDF-1.4 BOOM trailer<<>>\n", "application/pdf")),
        ],
        headers=h,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 1
    assert any(x.get("id") for x in body["items"])
    assert any(x.get("error") for x in body["items"])


def test_find_duplicate_uses_doi_not_full_scan(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "updoi")
    first = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper A", "doi": "10.1/abc", "authors": []},
        headers=h,
    )
    assert first.status_code == 200, first.text
    from fengsuan.app.db.session import SessionLocal

    db = SessionLocal()
    try:
        hit = find_duplicate(db, lab, doi="10.1/ABC", title="other", year=None)
        assert hit is not None
        assert hit.id == first.json()["id"]
    finally:
        db.close()


def test_upload_zip_unpacks_pdfs_and_skips_other_files(client):
    import io
    import zipfile

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "upzip")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("papers/One_Paper.pdf", _pdf("One Paper Title"))
        zf.writestr("papers/Two_Paper.pdf", _pdf("Two Paper Title"))
        zf.writestr("notes/readme.txt", b"not a paper")
        zf.writestr("../escape.pdf", _pdf("Should Use Base Name"))
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-zip",
        files=[("file", ("pack.zip", buf.getvalue(), "application/zip"))],
        headers=h,
    )
    assert r.status_code == 200, r.text
    titles = {x.get("title") for x in r.json()["items"] if x.get("id")}
    assert len(titles) == 3
    assert r.json()["pdfs"] == 3
