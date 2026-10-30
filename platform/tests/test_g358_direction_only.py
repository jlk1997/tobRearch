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


def test_g358_picks_matching_direction_among_many(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g358ok")
    residual = _dir(client, h, lab, "Residual Detector Group", ["residual"])
    quantum = _dir(client, h, lab, "量子计算", ["qubit"])
    vision = _dir(client, h, lab, "视觉大组", ["vision"])
    paper = _paper(client, h, lab, "Residual Detector Follow-up")
    assert residual in paper["direction_ids"]
    assert paper["linked_via"][residual] == "match"
    assert quantum not in paper["direction_ids"]
    assert vision not in paper["direction_ids"]


def test_g358_unrelated_title_hangs_none(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g358no")
    a = _dir(client, h, lab, "量子计算", ["qubit"])
    b = _dir(client, h, lab, "视觉大组", ["vision"])
    paper = _paper(client, h, lab, "Attention residual detector notes", "CNN only.")
    assert a not in (paper.get("direction_ids") or [])
    assert b not in (paper.get("direction_ids") or [])
    assert "match" not in (paper.get("linked_via") or {}).values()
    assert "only" not in (paper.get("linked_via") or {}).values()


def test_g358_can_match_more_than_one_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g358mu")
    residual = _dir(client, h, lab, "Residual Detector Group", ["residual"])
    small = _dir(client, h, lab, "Small Objects", ["objects"])
    quantum = _dir(client, h, lab, "量子计算", ["qubit"])
    paper = _paper(client, h, lab, "Residual Detector for Small Objects")
    hung = set(paper.get("direction_ids") or [])
    assert residual in hung
    assert small in hung
    assert quantum not in hung
    assert paper["linked_via"][residual] == "match"
    assert paper["linked_via"][small] == "match"


def test_g358_teacher_guide_points_to_literature_when_no_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g358gd")
    _paper(client, h, lab, "Residual Detector Alpha Study")
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    guide = {x["key"]: x for x in home.json().get("guide") or []}
    assert guide["pick_direction"]["href"] == f"/labs/{lab}/literature"
