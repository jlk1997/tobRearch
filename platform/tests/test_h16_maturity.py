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


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "student99", "display_name": "生", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _invite_teacher(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "teacher"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={"invite_token": inv.json()["token"], "email": email, "password": "teacher99", "display_name": "导", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_comment_notifies_manuscript_owner(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "cmtn")
    sh = _invite_student(client, h, lab, "cmtn@example.com")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Student Draft", "kind": "conference", "content_md": "引言段落用于定位。"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    c = client.post(
        f"/api/v1/manuscripts/{mid}/comments",
        json={"body": "请补实验", "anchor_json": {"excerpt": "引言段落用于定位。"}},
        headers=h,
    )
    assert c.status_code == 200, c.text
    notes = client.get("/api/v1/notifications", headers=sh)
    assert notes.status_code == 200, notes.text
    hit = [n for n in notes.json() if n.get("object_id") == mid]
    assert hit, notes.json()
    assert hit[0]["object_type"] == "manuscript"


def test_review_simulate_notifies_owner_no_rewrite(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "rsim")
    sh = _invite_student(client, h, lab, "rsim@example.com")
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Review Me", "kind": "conference", "content_md": "引言\n\n我们提出显著优于 SOTA 的方法。\n\n结论"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    mid = ms.json()["id"]
    before = client.get(f"/api/v1/manuscripts/{mid}", headers=sh).json()["content_md"]
    r = client.post(f"/api/v1/manuscripts/{mid}/review-simulate", headers=h)
    assert r.status_code == 200, r.text
    after = client.get(f"/api/v1/manuscripts/{mid}", headers=sh).json()["content_md"]
    assert after == before
    notes = client.get("/api/v1/notifications", headers=sh).json()
    assert any(n.get("object_id") == mid for n in notes), notes


def test_reading_progress_no_flood_and_keeps_pct(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "prg1")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Quiet Progress", "authors": []}, headers=h)
    assert paper.status_code == 200, paper.text
    pid = paper.json()["id"]
    first = client.post(f"/api/v1/literature/{pid}/progress", json={"page_no": 2, "page_count": 10}, headers=h)
    assert first.status_code == 200, first.text
    assert first.json()["my_reading"] == "reading"
    assert first.json()["progress_pct"] == 20
    second = client.post(f"/api/v1/literature/{pid}/progress", json={"page_no": 5, "page_count": 10}, headers=h)
    assert second.status_code == 200, second.text
    assert second.json()["progress_pct"] == 50
    assert second.json()["last_page_no"] == 5
    again = client.post(f"/api/v1/literature/{pid}/mark-read", json={"status": "reading"}, headers=h)
    assert again.status_code == 200, again.text
    assert again.json()["progress_pct"] == 50
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    assert stats.json()["this_month"]["reading_started"] == 1
    detail = client.get(f"/api/v1/literature/{pid}/detail", headers=h)
    assert detail.status_code == 200, detail.text
    assert detail.json()["last_opened_at"]
    assert detail.json()["progress_pct"] == 50
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=h).json()
    row = next(x for x in home["readings"] if x["id"] == pid)
    assert row["progress_pct"] == 50
    assert row["last_page_no"] == 5


def test_progress_cross_lab_403(client):
    token = _admin(client)
    h = _h(token)
    lab_a = _lab(client, h, "prga")
    lab_b = _lab(client, h, "prgb")
    sh = _invite_student(client, h, lab_a, "prgx@example.com")
    paper = client.post(f"/api/v1/labs/{lab_b}/literature", json={"title": "Secret"}, headers=h).json()
    forbidden = client.post(f"/api/v1/literature/{paper['id']}/progress", json={"page_no": 1, "page_count": 3}, headers=sh)
    assert forbidden.status_code == 403


def test_dataset_created_notifies_other_teacher(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "dsn1")
    th = _invite_teacher(client, h, lab, "dsn1@example.com")
    ds = client.post(f"/api/v1/labs/{lab}/datasets", json={"name": "MIMIC-demo"}, headers=h)
    assert ds.status_code == 200, ds.text
    notes = client.get("/api/v1/notifications", headers=th).json()
    hit = [n for n in notes if n.get("object_id") == ds.json()["id"]]
    assert hit, notes
    assert hit[0]["object_type"] == "dataset"
