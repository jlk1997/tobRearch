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


def _pdf(title_line: str = "Residual Detector Upload") -> bytes:
    return b"%PDF-1.4\n1 0 obj<<>>endobj\n" + title_line.encode("utf-8") + b"\ntrailer<<>>\n"


def test_g356_empty_lab_gap_is_no_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356nd")
    dirs = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert dirs.status_code == 200
    assert dirs.json() == []
    bare = client.get(f"/api/v1/labs/{lab}/literature", headers=h)
    assert bare.status_code == 200
    assert isinstance(bare.json(), list)
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "page_size": 20})
    assert pack.status_code == 200
    gap = pack.json()["direction_gap"]
    assert gap["kind"] == "no_direction"
    labels = [s.get("label") for s in (gap.get("steps") or [])]
    assert "从文献归纳" in labels
    assert "新建方向" in labels


def test_g356_import_does_not_invent_directions(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356inv")
    paper = _paper(client, h, lab, "Residual Detector for Small Objects")
    assert paper.get("direction_ids") == []
    assert paper.get("linked_via") == {}
    dirs = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert dirs.json() == []
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert pack.json()["direction_gap"]["kind"] == "no_direction"
    assert pack.json()["direction_gap"]["unlinked_count"] == 1


def test_g356_suggestions_do_not_auto_hang(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356sg")
    did = _dir(client, h, lab, "Residual Detector Group", ["residual", "detector"])
    paper = _paper(client, h, lab, "A Residual Detector for Small Objects", "We train a residual detector.")
    assert paper.get("direction_ids") == [did]
    assert paper.get("linked_via", {}).get(did) == "match"
    assert did not in {s["id"] for s in paper.get("direction_suggestions") or []}
    detail = client.get(f"/api/v1/literature/{paper['id']}", headers=h)
    assert detail.status_code == 200
    assert did in (detail.json().get("direction_ids") or [])
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert len(listed.json()) == 1
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert not pack.json().get("direction_gap")


def test_g356_patch_hangs_and_drops_suggestion(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356pt")
    did = _dir(client, h, lab, "Residual Detector Group", ["residual"])
    paper = _paper(client, h, lab, "Residual Detector Notes")
    hung = client.patch(f"/api/v1/literature/{paper['id']}", json={"direction_ids": [did]}, headers=h)
    assert hung.status_code == 200, hung.text
    body = hung.json()
    assert did in body.get("direction_ids", [])
    assert did not in {s["id"] for s in body.get("direction_suggestions") or []}
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert "direction_gap" not in pack.json() or not pack.json().get("direction_gap")


def test_g356_primary_hangs_on_import(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356pr")
    did = _dir(client, h, lab, "Hang Group", ["hang"])
    pick = client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=h)
    assert pick.status_code == 200, pick.text
    paper = _paper(client, h, lab, "Hang follow-up notes")
    assert paper["direction_ids"] == [did]
    assert paper["linked_via"][did] == "primary"


def test_g356_explicit_hangs_unmatched_primary_does_not(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356ex")
    primary = _dir(client, h, lab, "主方向")
    other = _dir(client, h, lab, "筛选方向")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": primary}, headers=h).status_code == 200
    paper = _paper(client, h, lab, "Both hang", direction_ids=[other])
    assert paper["direction_ids"] == [other]
    assert paper["linked_via"][other] == "explicit"
    assert primary not in paper["direction_ids"]


def test_g356_upload_query_hangs_filter_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356up")
    did = _dir(client, h, lab, "上传方向")
    r = client.post(
        f"/api/v1/labs/{lab}/literature/upload-pdfs",
        files=[("files", ("Residual_Detector.pdf", _pdf(), "application/pdf"))],
        params={"direction_id": did},
        headers=h,
    )
    assert r.status_code == 200, r.text
    item = next(x for x in r.json()["items"] if x.get("id"))
    assert did in item.get("direction_ids", [])
    assert item.get("linked_via", {}).get(did) == "explicit"


def test_g356_bibtex_hangs_explicit(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356bib")
    did = _dir(client, h, lab, "Bib方向")
    r = client.post(
        "/api/v1/literature/import-bibtex",
        json={
            "lab_id": lab,
            "bibtex": "@article{x,\n  title={Hello Residual Detector},\n  year={2020}\n}",
            "direction_ids": [did],
        },
        headers=h,
    )
    assert r.status_code == 200, r.text
    assert r.json()["direction_ids"] == [did]
    assert r.json()["linked_via"][did] == "explicit"
    item_id = r.json()["ids"][0]
    detail = client.get(f"/api/v1/literature/{item_id}", headers=h)
    assert did in detail.json().get("direction_ids", [])


def test_g356_keyword_hints_from_titles(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356kw")
    _paper(client, h, lab, "Residual Detector Alpha Study")
    _paper(client, h, lab, "Residual Detector Beta Study")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    hints = pack.json()["direction_gap"]["keyword_hints"]
    assert "residual" in hints
    assert "detector" in hints


def test_g356_unrelated_title_not_suggested(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g356no")
    did = _dir(client, h, lab, "量子计算", ["qubit"])
    paper = _paper(client, h, lab, "Attention residual detector notes", "CNN only.")
    assert did not in {s["id"] for s in paper.get("direction_suggestions") or []}
