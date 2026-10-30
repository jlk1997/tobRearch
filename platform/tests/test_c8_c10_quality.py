from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.activity import emit_activity


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
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "辅导生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c8_week_tutor_on_member_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c8tutor")
    sh = _invite_student(client, h, lab, "c8tutor@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Tutor Pulse"}, headers=h).json()
    db = SessionLocal()
    try:
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="analysis.tutor",
            object_type="literature",
            object_id=paper["id"],
            summary="问了这一页",
        )
        db.commit()
    finally:
        db.close()
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert card.get("week_tutor") >= 1
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert member.get("week_tutor") >= 1


def test_c9_recommend_falls_back_to_hung_titles(client, monkeypatch):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c9q")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": [], "description": ""},
        headers=h,
    ).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={
            "title": "Residual Detector Lab Paper",
            "authors": ["He Kaiming"],
            "direction_ids": [did],
        },
        headers=h,
    )
    monkeypatch.setattr(
        "fengsuan.app.services.literature.search_sources",
        lambda query, sources, max_per_source=8, language=None: [
            {"title": f"Hit {query[:12]}", "authors": ["He Kaiming"], "abstract": "residual", "doi": "10.1/c9"}
        ],
    )
    rec = client.post(f"/api/v1/labs/{lab}/literature/recommend", headers=h, params={"direction_id": did})
    assert rec.status_code == 200, rec.text
    body = rec.json()
    assert body.get("query_fallback") is True
    assert "Residual Detector Lab Paper" in (body.get("query") or "")
    he = next(row for row in body.get("authors") or [] if row["name"] == "He Kaiming")
    assert he.get("why")


def test_c10_graph_author_label_counts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c10g")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper A", "authors": ["Zhang Wei"], "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper B", "authors": ["Zhang Wei", "Li Ming"], "direction_ids": [did]},
        headers=h,
    )
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did}).json()
    authors = [n for n in graph.get("nodes") or [] if n.get("type") == "author"]
    zhang = next(n for n in authors if n.get("title") == "Zhang Wei")
    assert zhang.get("paper_count") == 2
    assert "2篇" in (zhang.get("label") or "")
    ids = {n["id"] for n in authors}
    assert len(ids) == len(authors)
