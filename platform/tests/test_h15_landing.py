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


def test_note_page_no_author_only(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "ntpg")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "np@example.com", "role": "student"}, headers=h)
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "np@example.com", "password": "student99", "display_name": "生", "consent": True},
    )
    sh = _h(reg.json()["access"])
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "P", "authors": [], "mark_read": True}, headers=h).json()
    note = client.post(f"/api/v1/literature/{paper['id']}/notes", json={"content_md": "hello page", "page_no": 3}, headers=sh)
    assert note.status_code == 200, note.text
    assert note.json()["page_no"] == 3
    patched = client.patch(f"/api/v1/literature/notes/{note.json()['id']}", json={"content_md": "hack"}, headers=h)
    assert patched.status_code == 403


def test_outline_uses_literature_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "oltl")
    a = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Graph Transformers for Molecules", "authors": ["A"]}, headers=h).json()
    b = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Attention Is Not All", "authors": ["B"]}, headers=h).json()
    r = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [a["id"], b["id"]]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    tree = r.json()["outline"]
    blob = str(tree)
    assert "引言" not in blob or "Graph Transformers" in blob or "Molecules" in blob or tree.get("used_literature")
    headings = " ".join(s.get("heading", "") for s in tree.get("sections") or [])
    assert "Graph Transformers" in headings or "Molecules" in headings or "文献" in headings


def test_dataset_cross_lab_403(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "dsa1")
    lab_b = _lab(client, h, "dsb1")
    inv = client.post(f"/api/v1/labs/{lab_a}/invites", json={"email": "ds@example.com", "role": "student"}, headers=h)
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "ds@example.com", "password": "student99", "consent": True},
    )
    sh = _h(reg.json()["access"])
    ds = client.post(f"/api/v1/labs/{lab_b}/datasets", json={"name": "secret"}, headers=h)
    assert ds.status_code == 200, ds.text
    forbidden = client.get(f"/api/v1/datasets/{ds.json()['id']}", headers=sh)
    assert forbidden.status_code == 403


def test_graph_lab_isolation(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "gra1")
    lab_b = _lab(client, h, "grb1")
    pa = client.post(f"/api/v1/labs/{lab_a}/literature", json={"title": "OnlyA"}, headers=h).json()
    pb = client.post(f"/api/v1/labs/{lab_b}/literature", json={"title": "OnlyB"}, headers=h).json()
    g = client.get(f"/api/v1/labs/{lab_a}/citation-graph", headers=h)
    assert g.status_code == 200, g.text
    ids = {n["id"] for n in g.json()["nodes"]}
    assert pa["id"] in ids
    assert pb["id"] not in ids


def test_reviewer_does_not_rewrite_body(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rvw1")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Draft", "kind": "conference", "content_md": "引言\n\n我们提出显著优于 SOTA 的方法。\n\n结论"},
        headers=h,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    before = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    r = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert r.status_code == 200, r.text
    assert "非知网" in (r.json().get("disclaimer") or "") or "查重" in str(r.json())
    after = client.get(f"/api/v1/manuscripts/{mid}", headers=h).json()["content_md"]
    assert after == before
    comments = client.get(f"/api/v1/manuscripts/{mid}/comments", headers=h)
    assert comments.status_code == 200
    assert len(comments.json()) >= 1


def test_kb_summarize_has_citation(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbsm")
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("a.txt", b"UNIQUE_SUMMARIZE_TOKEN appears in methods.", "text/plain")},
        headers=h,
    )
    assert up.status_code == 200, up.text
    sm = client.post(f"/api/v1/labs/{lab}/kb/summarize", headers=h)
    assert sm.status_code == 200, sm.text
    body = sm.json()
    assert "summary" in body
    cites = body.get("citations") or []
    assert cites
    assert cites[0]["citation"] in cites[0]["content"]


def test_capture_pdf_base64(client):
    import base64

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "capdf")
    raw = b"%PDF-1.1 toy"
    r = client.post(
        "/api/v1/literature/import-capture",
        json={
            "lab_id": lab,
            "page_url": "https://arxiv.org/abs/0000.00000",
            "title": "Toy PDF Capture",
            "site_adapter": "arxiv",
            "pdf_base64": base64.b64encode(raw).decode("ascii"),
            "mark_read": True,
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    detail = client.get(f"/api/v1/literature/{r.json()['id']}/detail", headers=h)
    assert detail.status_code == 200
    files = detail.json().get("files") or []
    assert files


def test_graph_edge_after_citation(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "gedg")
    lit = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Cited"}, headers=h).json()
    ms = client.post(f"/api/v1/labs/{lab}/manuscripts", json={"title": "Ms", "kind": "other", "content_md": "x"}, headers=h).json()
    add = client.post(f"/api/v1/manuscripts/{ms['id']}/citations", json={"literature_item_id": lit["id"], "citation_key": "cited"}, headers=h)
    assert add.status_code == 200, add.text
    g = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h).json()
    kinds = {(e["source"], e["target"], e["kind"]) for e in g["edges"]}
    assert (ms["id"], lit["id"], "cites") in kinds


def test_reproduce_still_no_fake_actual_gpu_off(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rpgpu")
    run = client.post(f"/api/v1/labs/{lab}/reproduce-runs", json={"dataset_path": ".", "claimed_metrics": {"acc": 0.9}}, headers=h)
    assert run.status_code == 200, run.text
    assert run.json()["actual_metrics"] in ({}, None)
    assert run.json()["status"] in {"awaiting_gpu", "blocked", "failed"}
    assert run.json().get("error_class") in {"awaiting_gpu", "need_data", "docker", "gpu", "parse", "stub", "no_docker", "no_gpu"}


def test_write_section_does_not_write_without_mode(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "wsnw")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Keep", "kind": "other", "content_md": "ORIGINAL_BODY"},
        headers=h,
    ).json()
    r = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "", "manuscript_id": ms["id"]},
        headers=h,
    )
    assert r.status_code == 200, r.text
    got = client.get(f"/api/v1/manuscripts/{ms['id']}", headers=h).json()
    assert got["content_md"] == "ORIGINAL_BODY"
    for hit in r.json().get("citations") or []:
        assert hit["citation"] in hit["content"]


def test_review_cross_lab_403(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "rva1")
    lab_b = _lab(client, h, "rvb1")
    inv = client.post(f"/api/v1/labs/{lab_a}/invites", json={"email": "rv@example.com", "role": "student"}, headers=h)
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": "rv@example.com", "password": "student99", "consent": True},
    )
    sh = _h(reg.json()["access"])
    ms = client.post(
        f"/api/v1/labs/{lab_b}/manuscripts",
        json={"title": "OtherLab", "kind": "other", "content_md": "x"},
        headers=h,
    ).json()
    r = client.post(f"/api/v1/manuscripts/{ms['id']}/review-simulate", headers=sh)
    assert r.status_code == 403


def test_dataset_rejects_absolute_path(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dabs")
    r = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "bad", "local_path": "C:/Windows"}, headers=h)
    assert r.status_code == 400
    r2 = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "bad2", "local_path": "../secret"}, headers=h)
    assert r2.status_code == 400


def test_kb_docs_paginated(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "kbpg")
    client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("b.txt", b"paginated token", "text/plain")},
        headers=h,
    )
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert listed.status_code == 200, listed.text
    body = listed.json()
    assert "items" in body
    assert body["total"] >= 1


def test_digest_object_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dgst")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "DigestPaper"}, headers=h).json()
    d = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert d.status_code == 200, d.text
    hrefs = [x.get("href") for x in d.json().get("items") or []]
    assert any(paper["id"] in (h or "") for h in hrefs)
