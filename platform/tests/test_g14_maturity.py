from __future__ import annotations

from fengsuan.app.services.literature import search_empty_next


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


def _items(payload):
    return payload if isinstance(payload, list) else payload.get("items") or []


def test_g14_search_empty_next_offers_existing_sources():
    nxt = search_empty_next("lab1", "q", sources=["lab"])
    href = (nxt.get("steps") or [{}])[0].get("href") or ""
    assert "medrxiv" in href
    assert "biorxiv" in href
    again = search_empty_next("lab1", "q", sources=["arxiv", "pubmed", "semantic"])
    href2 = (again.get("steps") or [{}])[0].get("href") or ""
    assert "iacr" in href2 or "biorxiv" in href2


def test_g14_recommend_uses_adapter_sources(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g14rec")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200, rec.text
    used = rec.json().get("sources_used") or []
    assert "medrxiv" in used
    assert "biorxiv" in used


def test_g15_research_empty_and_preflight(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g15run")
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert isinstance(body, dict)
    assert body.get("empty_kind") == "library"
    assert body.get("empty_next")
    pf = client.get(f"/api/v1/labs/{lab}/research-preflight", headers=h)
    assert pf.status_code == 200, pf.text
    pack = pf.json()
    assert pack.get("dataset_optional") is True
    keys = {m.get("key") for m in pack.get("missing") or []}
    assert "dataset" not in keys
    created = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "G15 q", "domain": "general"}, headers=h)
    assert created.status_code == 200, created.text
    assert created.json().get("preflight")
    assert "dataset" not in {m.get("key") for m in (created.json().get("preflight") or {}).get("missing") or []}
    again = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert again.status_code == 200, again.text
    assert isinstance(again.json(), list)
    assert again.json()[0]["id"] == created.json()["id"]


def test_g15_research_filter_empty_kind(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g15flt")
    client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "keep", "domain": "general"}, headers=h)
    empty = client.get(f"/api/v1/labs/{lab}/research-runs", params={"status": "awaiting_gpu"}, headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body.get("empty_kind") == "filter"
    hrefs = " ".join(s.get("href") or "" for s in (body.get("empty_next") or {}).get("steps") or [])
    assert "/runs" in hrefs


def test_g16_dataset_empty_wrap_only_when_empty(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g16ds")
    empty = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert isinstance(body, dict)
    assert body.get("empty_kind") == "library"
    assert "/datasets" in ((body.get("empty_next") or {}).get("steps") or [{"href": ""}])[0]["href"]
    created = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "G16 Set"}, headers=h)
    assert created.status_code == 200, created.text
    listed = client.get(f"/api/v1/labs/{lab}/datasets", headers=h)
    assert listed.status_code == 200, listed.text
    rows = _items(listed.json())
    assert isinstance(listed.json(), list)
    assert any(x["id"] == created.json()["id"] for x in rows)
