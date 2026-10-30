"""C204：出处带上能收进组里的标识。没有开放 PDF 时仍能按题录写入。"""

from fengsuan.app.services.agent.tools_web import _web_source
from fengsuan.app.services.paper_import import find_open_pdf, resolve_ref


def test_source_keeps_open_access_ids():
    src = _web_source(
        {
            "title": "A Watermark",
            "url": "https://doi.org/10.1000/wm",
            "doi": "10.1000/wm",
            "arxiv_id": "2401.00001",
            "openalex_id": "W1",
            "pdf_url": "https://arxiv.org/pdf/2401.00001",
            "cluster_id": "99",
            "cnki_id": "CNKI1",
        },
        kind="external",
    )
    assert src["openalex_id"] == "W1"
    assert src["pdf_url"].endswith("2401.00001")
    assert src["cluster_id"] == "99"
    assert src["doi"] == "10.1000/wm"


def test_resolve_uses_openalex_id(monkeypatch):
    def work(wid):
        assert wid == "W9"
        return {"title": "From OpenAlex", "doi": "10.1000/oa", "pdf_url": "https://example.org/a.pdf", "openalex_id": "W9"}

    monkeypatch.setattr("fengsuan.app.services.web.scholarly.openalex_work", work)
    monkeypatch.setattr("fengsuan.app.services.web.scholarly.openalex_work_by_doi", lambda *_a, **_k: None)
    meta = resolve_ref({"openalex_id": "W9", "title": "From OpenAlex"})
    assert meta["doi"] == "10.1000/oa"
    assert meta["pdf_url"].endswith("a.pdf")


def test_known_pdf_is_used_before_unpaywall():
    got = find_open_pdf({"title": "X", "pdf_url": "https://arxiv.org/pdf/1.pdf"})
    assert got["pdf_url"].endswith("1.pdf")


def test_metadata_only_item_is_still_created(client):
    login = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    token = login.json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "ingestlab", "code": "ingestlab", "description": ""}, headers=h)
    lab_id = lab.json()["id"]
    created = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        headers=h,
        json={
            "title": "Only A Record",
            "authors": ["Ada"],
            "doi": "10.1000/record-only",
            "openalex_id": "W42",
            "url": "https://doi.org/10.1000/record-only",
            "publish_year": 2024,
            "fetch_oa_pdf": False,
        },
    )
    assert created.status_code == 200, created.text
    assert created.json()["title"] == "Only A Record"
    assert created.json().get("pdf") != "attached"


def test_failed_download_keeps_the_open_pdf_url(client, monkeypatch):
    def _boom(_url):
        raise RuntimeError("no pdf")

    monkeypatch.setattr("fengsuan.app.services.paper_import._download", _boom)
    login = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    token = login.json()["access"]
    h = {"Authorization": f"Bearer {token}"}
    lab = client.post("/api/v1/labs", json={"name": "queuelab", "code": "queuelab", "description": ""}, headers=h)
    lab_id = lab.json()["id"]
    created = client.post(
        f"/api/v1/labs/{lab_id}/literature",
        headers=h,
        json={
            "title": "Queued PDF",
            "authors": ["Ada"],
            "doi": "10.1000/queued-pdf",
            "fetch_oa_pdf": True,
            "oa_pdf_url": "https://arxiv.org/pdf/2401.00001",
        },
    )
    assert created.status_code == 200, created.text
    assert created.json().get("pdf") == "queued"
    from fengsuan.app.db.session import SessionLocal
    from fengsuan.app.models.literature import LiteratureItem

    db = SessionLocal()
    try:
        item = db.query(LiteratureItem).filter(LiteratureItem.doi == "10.1000/queued-pdf").one()
        assert "2401.00001" in (item.external_ids_json or "")
    finally:
        db.close()
