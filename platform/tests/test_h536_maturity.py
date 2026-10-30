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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_h536_home_stamps_reading_and_writing_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h536hm")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Home Repro Paper", "abstract": "method"},
        headers=h,
    ).json()
    prog = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 5, "page_count": 10},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Home Draft", "kind": "other", "content_md": "引言"},
        headers=h,
    ).json()
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    body = home.json()
    rd = next(x for x in (body.get("readings") or []) if x.get("id") == paper["id"])
    assert "page=5" in (rd.get("href") or "")
    assert rd.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    row = next(x for x in (body.get("manuscripts") or []) if x.get("id") == ms["id"])
    assert row.get("href") == f"/manuscripts/{ms['id']}"
    assert row.get("writing_href") == f"/labs/{lab}/writing?ms={ms['id']}"
    empty_lab = _lab(client, h, "h536emp")
    empty = client.get("/api/v1/me/home", params={"lab_id": empty_lab}, headers=h)
    assert empty.status_code == 200, empty.text
    empty_body = empty.json()
    assert "readings" in empty_body
    assert "queue" in empty_body
    assert empty_body["readings"] == []
    assert empty_body["manuscripts"] == []


def test_h537_cockpit_member_keeps_student_page(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h537ck")
    sh = _invite_student(client, h, lab, "h537s@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Student Page Paper", "abstract": "graphs"},
        headers=h,
    ).json()
    stu = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 7, "page_count": 12},
        headers=sh,
    )
    assert stu.status_code == 200, stu.text
    tea = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 2, "page_count": 12},
        headers=h,
    )
    assert tea.status_code == 200, tea.text
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h)
    assert member.status_code == 200, member.text
    now = next(x for x in (member.json().get("reading_now") or []) if x.get("id") == paper["id"])
    href = now.get("href") or ""
    assert "page=7" in href
    assert "page=2" not in href
    assert now.get("last_page_no") == 7
    assert now.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h)
    assert cockpit.status_code == 200, cockpit.text
    card = next(x for x in (cockpit.json().get("members") or []) if x.get("user_id") == sid)
    preview = next(x for x in (card.get("readings") or []) if x.get("id") == paper["id"])
    assert "page=7" in (preview.get("href") or "")
    assert "page=2" not in (preview.get("href") or "")
    assert preview.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"


def test_h538_jump_digest_run_reproduce_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h538jp")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Jump Digest Paper Unique", "abstract": "inject"},
        headers=h,
    ).json()
    jump = client.get(f"/api/v1/labs/{lab}/jump", params={"q": "Jump Digest"}, headers=h)
    assert jump.status_code == 200, jump.text
    lit = next(x for x in (jump.json().get("items") or []) if x.get("id") == paper["id"])
    assert lit.get("type") == "literature"
    assert (lit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert lit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    digest = client.get(f"/api/v1/labs/{lab}/digest", headers=h)
    assert digest.status_code == 200, digest.text
    ev = next(
        x
        for x in (digest.json().get("items") or [])
        if x.get("object_id") == paper["id"] and (x.get("verb") or "").startswith("literature.")
    )
    assert (ev.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert ev.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    empty = client.post(f"/api/v1/labs/{lab}/research-runs", json={"question": "empty", "domain": "general"}, headers=h)
    assert empty.status_code == 200, empty.text
    arts = client.get(f"/api/v1/research-runs/{empty.json()['id']}/artifacts", headers=h)
    assert arts.status_code == 200
    assert arts.json()["items"] == []
    cancelled = client.post(f"/api/v1/research-runs/{empty.json()['id']}/cancel", headers=h)
    assert cancelled.status_code == 200, cancelled.text
    run = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "q", "literature_item_ids": [paper["id"]], "domain": "general"},
        headers=h,
    )
    assert run.status_code == 200, run.text
    inj = (run.json().get("injected") or {}).get("literature") or []
    hit = next(x for x in inj if x.get("id") == paper["id"])
    assert hit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == run.json()["id"])
    lit_row = next(x for x in (row.get("literature") or []) if x.get("id") == paper["id"])
    assert lit_row.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
