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


def test_h539_literature_list_and_recommend_stamp(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h539lit")
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "主表方向", "description": ""}, headers=h).json()
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Library Main Paper", "abstract": "graphs", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    prog = client.post(
        f"/api/v1/literature/{paper['id']}/progress",
        json={"page_no": 6, "page_count": 12},
        headers=h,
    )
    assert prog.status_code == 200, prog.text
    listed = client.get(f"/api/v1/labs/{lab}/literature", params={"page": 1, "page_size": 20}, headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json()["items"] if x["id"] == paper["id"])
    assert "page=6" in (row.get("href") or "")
    assert row.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    other = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Unread Rec Paper", "abstract": "graphs method", "direction_ids": [d["id"]]},
        headers=h,
    ).json()
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", params={"direction_id": d["id"]}, headers=h)
    assert rec.status_code == 200, rec.text
    unread = next(x for x in (rec.json().get("lab_unread") or []) if x.get("id") == other["id"])
    assert (unread.get("href") or "").startswith(f"/literature/{other['id']}")
    assert unread.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={other['id']}"


def test_h540_notification_reproduce_keeps_comment_href(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h540nt")
    sh = _invite_student(client, h, lab, "h540s@example.com")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Notify Repro Paper", "abstract": "method"},
        headers=sh,
    ).json()
    notes = client.get("/api/v1/notifications", headers=h)
    assert notes.status_code == 200, notes.text
    hit = next(x for x in notes.json() if x.get("object_id") == paper["id"])
    assert (hit.get("href") or "").startswith(f"/literature/{paper['id']}")
    assert hit.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h)
    assert home.status_code == 200, home.text
    unread = next(x for x in (home.json().get("unread_items") or []) if x.get("object_id") == paper["id"])
    assert unread.get("reproduce_href") == f"/labs/{lab}/reproduces?literature_item_id={paper['id']}"
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "待批", "kind": "other", "content_md": "引言"},
        headers=sh,
    ).json()
    posted = client.post(f"/api/v1/manuscripts/{ms['id']}/comments", json={"body": "请补实验"}, headers=h)
    assert posted.status_code == 200, posted.text
    assert posted.json()["href"] == f"/manuscripts/{ms['id']}?comment={posted.json()['id']}"
    stu_notes = client.get("/api/v1/notifications", headers=sh)
    assert stu_notes.status_code == 200, stu_notes.text
    comment = next(x for x in stu_notes.json() if x.get("comment_id") == posted.json()["id"])
    assert comment["href"] == f"/manuscripts/{ms['id']}?comment={posted.json()['id']}"
    assert not comment.get("reproduce_href")


def test_h541_stats_and_manuscript_list_hrefs(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "h541st")
    sh = _invite_student(client, h, lab, "h541s@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    d = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "统计方向", "description": ""}, headers=h).json()
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "列表草稿", "kind": "other", "content_md": "引言"},
        headers=sh,
    ).json()
    listed = client.get(f"/api/v1/labs/{lab}/manuscripts", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json() if x["id"] == ms["id"])
    assert row.get("href") == f"/manuscripts/{ms['id']}"
    assert row.get("writing_href") == f"/labs/{lab}/writing?ms={ms['id']}"
    st = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert st.status_code == 200, st.text
    body = st.json()
    assert body.get("literature_href") == f"/labs/{lab}/literature"
    assert body.get("manuscripts_href") == f"/labs/{lab}/manuscripts"
    assert body.get("kb_href") == f"/labs/{lab}/kb"
    direction = next(x for x in (body.get("by_direction") or []) if x.get("direction_id") == d["id"])
    assert direction.get("href") == f"/labs/{lab}/directions/{d['id']}"
    assert direction.get("literature_href") == f"/labs/{lab}/literature?direction_id={d['id']}"
    assert direction.get("manuscripts_href") == f"/labs/{lab}/manuscripts?direction_id={d['id']}"
    member = next(x for x in (body.get("by_member") or []) if x.get("user_id") == sid)
    assert member.get("href") == f"/labs/{lab}/cockpit/members/{sid}"
