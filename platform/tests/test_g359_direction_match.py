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


def _dir(client, h, lab: str, name: str, keywords=None):
    r = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": name, "keywords": keywords or [], "description": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _paper(client, h, lab: str, title: str, abstract: str = "", direction_ids=None) -> dict:
    body = {"title": title, "abstract": abstract or title}
    if direction_ids is not None:
        body["direction_ids"] = direction_ids
    r = client.post(f"/api/v1/labs/{lab}/literature", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def test_g359_primary_without_match_does_not_hang(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g359pr")
    did = _dir(client, h, lab, "量子计算", ["qubit"])
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=h).status_code == 200
    paper = _paper(client, h, lab, "Residual Detector Notes")
    assert did not in (paper.get("direction_ids") or [])
    assert "primary" not in (paper.get("linked_via") or {}).values()
    assert "only" not in (paper.get("linked_via") or {}).values()


def test_g359_existing_papers_become_hangable_not_drafts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g359hg")
    a = _paper(client, h, lab, "Residual Detector Alpha Study")
    b = _paper(client, h, lab, "Residual Detector Beta Study")
    did = _dir(client, h, lab, "Residual Detector Group", ["residual", "detector"])
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "page_size": 20})
    assert pack.status_code == 200
    assert not (pack.json().get("direction_drafts") or [])
    gap = pack.json().get("direction_gap") or {}
    assert gap.get("kind") == "unlinked"
    assert gap.get("matchable_count") == 2
    hung_ids = {x["item_id"] for x in (gap.get("hangable") or [])}
    assert hung_ids == {a["id"], b["id"]}
    assert did in (gap["hangable"][0].get("direction_ids") or [])
    assert "Residual Detector Group" in (gap.get("hang_names") or [])
    for row in pack.json().get("items") or []:
        if row["id"] in {a["id"], b["id"]}:
            assert did not in (row.get("direction_ids") or [])


def test_g359_hangable_patch_clears_gap(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g359pt")
    a = _paper(client, h, lab, "Residual Detector Alpha Study")
    b = _paper(client, h, lab, "Residual Detector Beta Study")
    did = _dir(client, h, lab, "Residual Detector Group", ["residual"])
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    for row in pack.json().get("direction_gap", {}).get("hangable") or []:
        hung = client.patch(
            f"/api/v1/literature/{row['item_id']}",
            json={"direction_ids": row["direction_ids"]},
            headers=h,
        )
        assert hung.status_code == 200, hung.text
        assert did in hung.json().get("direction_ids", [])
    again = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert not again.json().get("direction_gap")
    assert not (again.json().get("direction_drafts") or [])
    seen = {row["id"]: row for row in again.json().get("items") or []}
    assert did in (seen[a["id"]].get("direction_ids") or [])
    assert did in (seen[b["id"]].get("direction_ids") or [])


def test_g359_unmatched_still_induces_when_some_match(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g359mx")
    residual_a = _paper(client, h, lab, "Residual Detector Alpha Study")
    residual_b = _paper(client, h, lab, "Residual Detector Beta Study")
    quantum_a = _paper(client, h, lab, "Quantum Computing Primer Notes")
    quantum_b = _paper(client, h, lab, "Quantum Computing Survey Notes")
    did = _dir(client, h, lab, "Residual Detector Group", ["residual"])
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    body = pack.json()
    hang_ids = {x["item_id"] for x in (body.get("direction_gap") or {}).get("hangable") or []}
    assert hang_ids == {residual_a["id"], residual_b["id"]}
    drafts = body.get("direction_drafts") or []
    assert len(drafts) == 1
    draft_ids = set(drafts[0].get("item_ids") or [])
    assert draft_ids == {quantum_a["id"], quantum_b["id"]}
    assert residual_a["id"] not in draft_ids
    first = ((body.get("direction_gap") or {}).get("hangable") or [{}])[0]
    assert did in (first.get("direction_ids") or [])
