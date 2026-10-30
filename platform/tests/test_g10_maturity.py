from __future__ import annotations

from pathlib import Path

from fengsuan.app.services.insight import compare_next_step, graph_empty_next, kb_ask_empty_next

ROOT = Path(__file__).resolve().parents[2]


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


def test_g10_kb_ask_empty_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g10kb")
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "anything"}, headers=h)
    assert q.status_code == 200, q.text
    body = q.json()
    assert body.get("empty_reason") == "no_indexed"
    nxt = body.get("empty_next") or {}
    hrefs = " ".join(s.get("href") or "" for s in nxt.get("steps") or [])
    assert f"/labs/{lab}/kb?tab=docs" in hrefs


def test_g10_recommend_empty_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g10rec")
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h)
    assert rec.status_code == 200, rec.text
    body = rec.json()
    if not body.get("items") and not body.get("lab_unread") and not body.get("lab_related"):
        nxt = body.get("empty_next") or {}
        hrefs = " ".join(s.get("href") or "" for s in nxt.get("steps") or [])
        assert "/directions" in hrefs or "tab=search" in hrefs


def test_g10_compare_next_step_helper():
    empty = compare_next_step("lab1", {"gap_md": "", "related_work_md": "", "items": [{"id": "a"}]})
    assert empty["kind"] == "analyze"
    assert "literature/a" in empty["href"]
    filled = compare_next_step("lab1", {"gap_md": "差距", "related_work_md": "", "items": []})
    assert filled["kind"] == "writeback"
    kb = kb_ask_empty_next("lab1", "no_indexed")
    assert kb["steps"][0]["href"] == "/labs/lab1/kb?tab=docs"


def test_g10_compare_api_next_step(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g10cmp")
    a = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G10 Compare Alpha TitleX", "abstract": "a"},
        headers=h,
    ).json()
    b = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G10 Compare Beta TitleX", "abstract": "b"},
        headers=h,
    ).json()
    cmp = client.post(
        f"/api/v1/labs/{lab}/literature/compare",
        json={"item_ids": [a["id"], b["id"]]},
        headers=h,
    )
    assert cmp.status_code == 200, cmp.text
    body = cmp.json()
    assert body.get("next_step")
    assert body["next_step"].get("href")
    if not (body.get("gap_md") or "").strip() and not (body.get("related_work_md") or "").strip():
        assert body["next_step"]["kind"] in {"analyze", "writing"}


def test_g11_graph_empty_next(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g11graph")
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert g.status_code == 200, g.text
    body = g.json()
    assert body.get("empty_kind") == "library"
    nxt = body.get("empty_next") or {}
    hrefs = " ".join(s.get("href") or "" for s in nxt.get("steps") or [])
    assert f"/labs/{lab}/literature" in hrefs
    d = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "G11", "keywords": ["g11"], "description": ""},
        headers=h,
    )
    did = d.json()["id"]
    scoped = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": did}, headers=h)
    assert scoped.status_code == 200, scoped.text
    scoped_body = scoped.json()
    assert scoped_body.get("empty_kind") == "filter"
    assert did in ((scoped_body.get("empty_next") or {}).get("steps") or [{"href": ""}])[0]["href"]
    helper = graph_empty_next(lab, did)
    assert "direction_id=" in helper["steps"][0]["href"]


def test_g12_reviewer_wires_existing_searchers():
    src = (ROOT / "openlens_ai" / "agents" / "literature_reviewer.py").read_text(encoding="utf-8")
    assert "SearchBioRxivTool()" in src
    assert "SearchIACRTool()" in src
    prompt = (ROOT / "openlens_ai" / "prompts" / "general" / "literature_search.md").read_text(encoding="utf-8")
    assert "bioRxiv" in prompt
    assert "IACR" in prompt


def test_g13_preflight_without_list_wrap(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g13pf")
    bare = client.get(f"/api/v1/labs/{lab}/reproduce-preflight", headers=h)
    assert bare.status_code == 200, bare.text
    missing = {m.get("key") for m in (bare.json().get("missing") or [])}
    assert "dataset" in missing
    assert "method_card" not in missing
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "G13 Preflight No Card TitleX", "abstract": "a"},
        headers=h,
    ).json()
    with_lit = client.get(
        f"/api/v1/labs/{lab}/reproduce-preflight",
        params={"literature_item_id": paper["id"]},
        headers=h,
    )
    assert with_lit.status_code == 200, with_lit.text
    keys = {m.get("key") for m in (with_lit.json().get("missing") or [])}
    assert "method_card" in keys
    assert with_lit.json().get("has_method_card") is False
