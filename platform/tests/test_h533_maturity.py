from __future__ import annotations


def _admin(client):
    r = client.post("/api/v1/auth/login", json={"email": "admin@example.com", "password": "adminpass1"})
    assert r.status_code == 200, r.text
    return r.json()["access"]


def _h(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _lab(client, h, code: str) -> str:
    r = client.post("/api/v1/labs", json={"name": code, "code": code, "description": ""}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_h534_kb_query_and_summarize_stamp_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h534kb")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "KB Href Paper", "abstract": "We propose a convolution method on graphs."},
        headers=h,
    ).json()
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert listed.status_code == 200, listed.text
    doc = next(x for x in listed.json()["items"] if x.get("literature_item_id") == paper["id"])
    assert (doc.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert doc.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    q = client.post(f"/api/v1/labs/{lab}/kb/query", json={"q": "convolution graphs"}, headers=h)
    assert q.status_code == 200, q.text
    hits = [x for x in (q.json().get("hits") or []) if x.get("literature_item_id") == paper["id"]]
    assert hits
    assert (hits[0].get("href") or "").startswith(f"/literature/{paper['id']}")
    assert "page=" in (hits[0].get("href") or "")
    assert hits[0].get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    cites = [x for x in (sm.json().get("citations") or []) if x.get("literature_item_id") == paper["id"]]
    assert cites
    assert (cites[0].get("href") or "").startswith(f"/literature/{paper['id']}")
    assert cites[0].get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"


def test_h535_direction_and_graph_reproduce_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h535dir")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "复现方向", "description": ""}, headers=h).json()
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Dir Repro Paper", "abstract": "graphs", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    ov = client.get(f"/api/v1/labs/{lab}/directions/{d['id']}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    lit = next(x for x in (ov.json().get("literature") or []) if x.get("id") == paper["id"])
    assert (lit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert lit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h)
    assert g.status_code == 200, g.text
    node = next(x for x in (g.json().get("nodes") or []) if x.get("id") == paper["id"])
    assert node.get("type") == "literature"
    assert node.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"


def test_h533_manuscript_citations_stamp_and_comment_href_unchanged(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h533ms")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Ms Cite Paper", "abstract": "method"},
        headers=h,
    ).json()
    client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 3, "page_count": 10},
        headers=h,
    )
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Cite Tab", "kind": "survey", "content_md": "引言\n\n结论"},
        headers=h,
    ).json()
    cite = client.post(
        f"/api/v1/manuscripts/{ms['id']}/citations",
        json={"literature_item_id": paper["id"], "citation_key": "citepg"},
        headers=h,
    )
    assert cite.status_code == 200, cite.text
    listed = client.get(f"/api/v1/manuscripts/{ms['id']}/citations", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["literature_item_id"] == paper["id"])
    assert "page=3" in (row.get("href") or "")
    assert row.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    posted = client.post(f"/api/v1/manuscripts/{ms['id']}/comments", json={"body": "侧栏评论"}, headers=h)
    assert posted.status_code == 200, posted.text
    assert posted.json()["href"] == f"/manuscripts/{ms['id']}?comment={posted.json()['id']}"
    comments = client.get(f"/api/v1/manuscripts/{ms['id']}/comments", headers=h)
    assert comments.status_code == 200, comments.text
    hit = next(x for x in comments.json() if x["id"] == posted.json()["id"])
    assert hit["href"] == f"/manuscripts/{ms['id']}?comment={hit['id']}"
