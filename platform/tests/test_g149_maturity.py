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


def _ids(payload):
    if isinstance(payload, list):
        return {x["id"] for x in payload}
    return {x["id"] for x in (payload.get("items") or [])}


def test_g149_parent_lists_include_children(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g149scope")
    parent = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "视觉大组"}, headers=h)
    assert parent.status_code == 200, parent.text
    pid = parent.json()["id"]
    child = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "多模态", "parent_id": pid}, headers=h)
    assert child.status_code == 200, child.text
    cid = child.json()["id"]
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature", json={"title": "Parent Only Paper", "direction_ids": [pid]}, headers=h
    )
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature", json={"title": "Child Only Paper", "direction_ids": [cid]}, headers=h
    )
    assert parent_lit.status_code == 200 and child_lit.status_code == 200, parent_lit.text + child_lit.text
    plid, clid = parent_lit.json()["id"], child_lit.json()["id"]
    parent_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Parent Draft", "direction_ids": [pid]},
        headers=h,
    )
    child_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Child Draft", "direction_ids": [cid]},
        headers=h,
    )
    assert parent_ms.status_code == 200 and child_ms.status_code == 200
    pmid, cmid = parent_ms.json()["id"], child_ms.json()["id"]

    lit_parent = client.get(f"/api/v1/labs/{lab}/literature", params={"direction_id": pid, "page": 1}, headers=h)
    assert lit_parent.status_code == 200, lit_parent.text
    assert lit_parent.json().get("includes_children") is True
    assert {plid, clid} <= _ids(lit_parent.json())

    lit_child = client.get(f"/api/v1/labs/{lab}/literature", params={"direction_id": cid, "page": 1}, headers=h)
    assert lit_child.status_code == 200, lit_child.text
    assert lit_child.json().get("includes_children") is False
    assert _ids(lit_child.json()) == {clid}

    ghost = client.get(f"/api/v1/labs/{lab}/literature", params={"direction_id": "missing-dir", "page": 1}, headers=h)
    assert ghost.status_code == 200, ghost.text
    assert _ids(ghost.json()) == set()
    assert (ghost.json().get("empty_next") or {}).get("title")

    ms_parent = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"direction_id": pid}, headers=h)
    assert ms_parent.status_code == 200, ms_parent.text
    assert {pmid, cmid} <= _ids(ms_parent.json())
    ms_child = client.get(f"/api/v1/labs/{lab}/manuscripts", params={"direction_id": cid}, headers=h)
    assert _ids(ms_child.json()) == {cmid}

    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": pid}, headers=h)
    assert graph.status_code == 200, graph.text
    node_ids = {n.get("id") for n in (graph.json().get("nodes") or [])}
    assert {plid, clid, pmid, cmid} <= node_ids
    graph_child = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": cid}, headers=h)
    child_ids = {n.get("id") for n in (graph_child.json().get("nodes") or [])}
    assert {clid, cmid} <= child_ids
    assert plid not in child_ids and pmid not in child_ids


def test_g151_stats_csv_and_funnel_leak(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g151csv")
    did = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "漏斗方向"}, headers=h).json()["id"]
    stats = client.get(f"/api/v1/labs/{lab}/stats", params={"days": 7, "direction_id": did}, headers=h)
    assert stats.status_code == 200, stats.text
    funnel = stats.json().get("funnel") or {}
    assert funnel.get("direction_id") == did
    assert funnel.get("empty") is True
    reading = next(s for s in funnel["stages"] if s["key"] == "reading")
    assert reading.get("leak_href")
    assert "leak_count" in reading
    csv = client.get(f"/api/v1/labs/{lab}/stats.csv", params={"days": 7, "direction_id": did}, headers=h)
    assert csv.status_code == 200
    assert b"funnel" in csv.content
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    assert (digest.json().get("funnel") or {}).get("stages")
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    assert (cockpit.json().get("funnel") or {}).get("stages")
    overview = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert overview.status_code == 200, overview.text
    assert (overview.json().get("funnel") or {}).get("empty") is True
    assert overview.json().get("coverage")


def test_g153_workbench_multi_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g153pick")
    pid = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "主方向"}, headers=h).json()["id"]
    cid = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "副方向"}, headers=h).json()["id"]
    picked = client.put(
        f"/api/v1/labs/{lab}/me/direction",
        json={"direction_id": pid, "direction_ids": [pid, cid]},
        headers=h,
    )
    assert picked.status_code == 200, picked.text
    assert picked.json()["direction_id"] == pid
    assert set(picked.json()["direction_ids"]) == {pid, cid}
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    assert home.json()["my_direction_id"] == pid
    assert set(home.json()["my_direction_ids"]) == {pid, cid}
    assert {d["id"] for d in home.json()["my_directions"]} == {pid, cid}


def test_g154_kb_upload_and_list_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g154kbdir")
    d1 = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "甲组"}, headers=h).json()["id"]
    d2 = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "乙组"}, headers=h).json()["id"]
    up = client.post(
        f"/api/v1/labs/{lab}/kb/documents",
        files={"file": ("note.md", b"Attention is useful for this direction.", "text/markdown")},
        data={"direction_id": d1},
        headers=h,
    )
    assert up.status_code == 200, up.text
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", params={"direction_id": d1}, headers=h)
    assert listed.status_code == 200, listed.text
    rows = listed.json().get("items") or []
    assert rows and rows[0].get("direction_id") == d1
    other = client.get(f"/api/v1/labs/{lab}/kb/documents", params={"direction_id": d2}, headers=h)
    assert other.status_code == 200, other.text
    assert (other.json().get("total") or 0) == 0
    assert (other.json().get("empty_next") or {}).get("title") == "这些论文的正文还不能问"
