from __future__ import annotations


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


def test_g6_abstract_only_analyze_has_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g6an")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G6 Abstract Only Method Card TitleX", "abstract": "We propose a method without PDF."},
        headers=h,
    ).json()
    ana = client.post(f"/api/v1/literature/{paper['id']}/analyze", json={"analysis_type": "method_card"}, headers=h)
    assert ana.status_code == 200, ana.text
    nxt = ana.json().get("next_step") or {}
    assert nxt.get("key")
    assert nxt.get("href")
    assert "panel=card" in (nxt.get("href") or "")


def test_g6_tutor_without_page_text_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g6tu")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G6 Tutor No Page TitleX", "abstract": "Abstract only."},
        headers=h,
    ).json()
    tut = client.post(
        f"/api/v1/literature/{paper['id']}/tutor",
        json={"question": "这一页在做什么？", "page_no": 1},
        headers=h,
    )
    assert tut.status_code == 200, tut.text
    body = tut.json()
    assert body.get("page_text_missing") is True
    assert (body.get("next_step") or {}).get("key") == "upload_pdf"


def test_g6_kb_docs_embed_fallback(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g6kb")
    docs = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert docs.status_code == 200, docs.text
    assert docs.json().get("embed_fallback") == "hash"
