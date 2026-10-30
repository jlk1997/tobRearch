"""C234：卡点连上文献和方法卡，同学看不到，检索问题能转成文献调研。"""

from fengsuan.app.services.insight import build_member_stuck, enrich_member_stuck


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


def _student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c234_reading_stuck_names_the_paper_and_peers_cannot_see_it(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c234ev")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "水印", "keywords": ["watermark"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "C234 还没读的水印论文", "abstract": "a watermark method"},
        headers=h,
    ).json()
    hung = client.patch(f"/api/v1/literature/{paper['id']}", json={"direction_ids": [did]}, headers=h)
    assert hung.status_code == 200, hung.text
    sh = _student(client, h, lab, "c234ev@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": did}, headers=sh)
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(row for row in cockpit["members"] if row["user_id"] == sid)
    reading = next(step for step in card["stuck"]["steps"] if step["key"] == "reading")
    assert any(ev.get("title") == "C234 还没读的水印论文" for ev in reading.get("evidence") or [])
    assert "在读" in reading.get("evidence_text", "")
    assert "文献" in reading["search"]["question"] or "证据" in reading["search"]["question"]
    assert paper["id"] in reading["search"]["literature_item_ids"]
    peer = _student(client, h, lab, "c234peer@example.com")
    hidden = client.get(f"/api/v1/labs/{lab}/cockpit", headers=peer).json()
    other = next(row for row in hidden["members"] if row["user_id"] == sid)
    assert other["stuck"]["steps"] == []
    detail = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=peer).json()
    assert detail["stuck"]["steps"] == []
    bare = build_member_stuck(lab, role="student", direction_id=did, reading_count=0, manuscripts=[])
    assert "evidence" not in bare["steps"][0]


def test_c234_enrich_without_papers_still_says_what_is_missing(client):
    from fengsuan.app.db.session import SessionLocal

    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c234empty")
    sh = _student(client, h, lab, "c234empty@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    stuck = build_member_stuck(lab, role="student", direction_id=None, reading_count=0, manuscripts=[])
    db = SessionLocal()
    try:
        out = enrich_member_stuck(db, lab, sid, stuck)
    finally:
        db.close()
    reading = next(step for step in out["steps"] if step["key"] == "reading")
    assert "还没开读" in reading["evidence_text"]
    assert reading.get("evidence") in (None, [])
