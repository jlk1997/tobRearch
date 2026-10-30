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


def _paper(client, h, lab: str, title: str, abstract: str = "") -> dict:
    r = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": title, "abstract": abstract or title},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生三七",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_g357_two_titles_make_draft_without_writing(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g357dr")
    a = _paper(client, h, lab, "Residual Detector Alpha Study")
    b = _paper(client, h, lab, "Residual Detector Beta Study")
    dirs = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert dirs.status_code == 200
    assert dirs.json() == []
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "page_size": 20})
    assert pack.status_code == 200
    drafts = pack.json().get("direction_drafts") or []
    assert len(drafts) == 1
    draft = drafts[0]
    assert set(draft["item_ids"]) == {a["id"], b["id"]}
    assert draft["paper_count"] == 2
    assert draft["via"] == "title_tokens"
    assert "residual" in " ".join(draft.get("keywords") or []).lower() or "Residual" in (draft.get("name") or "")
    assert client.get(f"/api/v1/labs/{lab}/directions", headers=h).json() == []


def test_g357_adopt_creates_direction_and_hangs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g357ad")
    a = _paper(client, h, lab, "Residual Detector Alpha Study")
    b = _paper(client, h, lab, "Residual Detector Beta Study")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    draft = (pack.json().get("direction_drafts") or [])[0]
    created = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={
            "name": draft["name"],
            "keywords": draft["keywords"],
            "description": f"从 {draft['paper_count']} 篇题名归纳，可改名。",
        },
        headers=h,
    )
    assert created.status_code == 200, created.text
    did = created.json()["id"]
    for iid in draft["item_ids"]:
        hung = client.patch(f"/api/v1/literature/{iid}", json={"direction_ids": [did]}, headers=h)
        assert hung.status_code == 200, hung.text
        assert did in hung.json().get("direction_ids", [])
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200
    assert isinstance(listed.json(), list)
    assert len(listed.json()) == 1
    assert listed.json()[0]["id"] == did
    again = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert not (again.json().get("direction_drafts") or [])
    for row in again.json().get("items") or []:
        if row["id"] in {a["id"], b["id"]}:
            assert did in (row.get("direction_ids") or [])


def test_g357_unrelated_titles_make_no_draft(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g357no")
    _paper(client, h, lab, "Quantum Computing Primer Notes")
    _paper(client, h, lab, "Attention Is All You Need Survey")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert not (pack.json().get("direction_drafts") or [])
    assert client.get(f"/api/v1/labs/{lab}/directions", headers=h).json() == []


def test_g357_student_cannot_create_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g357st")
    _paper(client, h, lab, "Residual Detector Alpha Study")
    _paper(client, h, lab, "Residual Detector Beta Study")
    sh = _invite_student(client, h, lab, "g357st@example.com")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=sh, params={"page": 1})
    assert pack.status_code == 200
    assert pack.json().get("direction_drafts")
    denied = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "不该写入", "keywords": ["residual"], "description": ""},
        headers=sh,
    )
    assert denied.status_code == 403
    assert client.get(f"/api/v1/labs/{lab}/directions", headers=h).json() == []
