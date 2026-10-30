from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.activity import emit_activity
from fengsuan.app.services.analysis import excerpt_in_pages, iter_running_analyses, running_method_card
from fengsuan.app.services.literature import looks_like_citation_title


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


def _put_card(item_id: str, user_id: str, payload: dict, *, model: str = "configured", created_at=None) -> LiteratureAnalysis:
    db = SessionLocal()
    try:
        row = LiteratureAnalysis(
            item_id=item_id,
            analysis_type="method_card",
            result_json=json.dumps(payload, ensure_ascii=False),
            created_by=user_id,
            model=model,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        if created_at is not None:
            live = db.get(LiteratureAnalysis, row.id)
            live.created_at = created_at
            db.commit()
            db.refresh(live)
            return live
        return row
    finally:
        db.close()


def test_c11_fold_excerpt_hyphen_linebreak():
    page = "we present a state-\nof-the-art residual detector"
    assert excerpt_in_pages("state-of-the-art", [page], None)
    assert excerpt_in_pages("residual detector", ["residual\ndetector on page 2"], None)
    assert not excerpt_in_pages("invented metric 99.9", [page], None)


def test_c11_pending_running_not_crowded_out(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c11pend")
    old = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Old Running"}, headers=h).json()
    newer = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Newer Done"}, headers=h).json()
    pending = _put_card(old["id"], uid, running_method_card(), model="pending", created_at=utcnow() - timedelta(minutes=8))
    for i in range(5):
        _put_card(newer["id"], uid, {"source": "pdf", "problem": {"text": f"done {i}", "pages": [1]}}, model="configured")
    db = SessionLocal()
    try:
        rows = iter_running_analyses(db, cap=8)
        assert any(r.id == pending.id for r in rows)
        assert rows[0].id == pending.id
    finally:
        db.close()


def test_c12_list_prefers_running_over_abstract(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c12list")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "List Running Residual", "abstract": "abstract only"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"problem": {"text": "from abstract", "pages": []}, "source": "abstract_only"})
    _put_card(paper["id"], uid, running_method_card(), model="pending")
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1})
    assert pack.status_code == 200, pack.text
    row = next(x for x in pack.json()["items"] if x["id"] == paper["id"])
    assert row.get("analysis_status") == "running"
    assert (row.get("card_empty") or {}).get("kind") == "running"
    assert row.get("has_method_card") is False


def test_c13_week_titles_on_member_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c13week")
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": "c13s@example.com", "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": "c13s@example.com",
            "password": "student99",
            "display_name": "出卡生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    sh = _h(reg.json()["access"])
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Week Title Pulse"}, headers=h).json()
    _put_card(paper["id"], sid, {"source": "pdf", "problem": {"text": "from pages", "pages": [1]}})
    db = SessionLocal()
    try:
        emit_activity(
            db,
            lab_id=lab,
            actor_id=sid,
            verb="analysis.ready",
            object_type="literature",
            object_id=paper["id"],
            summary="Week Title Pulse",
        )
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
    assert any((t.get("title") or "") == "Week Title Pulse" for t in card.get("week_card_titles") or [])
    assert any((t.get("title") or "") == "Week Title Pulse" for t in card.get("week_tutor_titles") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert member.get("week_card_titles")
    assert member.get("week_tutor_titles")


def test_c14_hung_title_helps_next_english_paper(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c14hung")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": [], "description": ""},
        headers=h,
    ).json()["id"]
    hung = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Detector Lab Paper", "direction_ids": [did]},
        headers=h,
    ).json()
    nxt = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Networks for Detection"},
        headers=h,
    ).json()
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    hang_ids = {x["item_id"] for x in (pack.get("direction_gap") or {}).get("hangable") or []}
    assert hung["id"] not in hang_ids
    assert nxt["id"] in hang_ids
    row = next(x for x in pack["items"] if x["id"] == nxt["id"])
    assert did not in (row.get("direction_ids") or [])


def test_c15_citation_title_rejects_claims_and_graph_lists_papers(client):
    assert looks_like_citation_title("Residual Networks for Image Detection")
    assert not looks_like_citation_title("We propose a residual detector that works well on noisy scans.")
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c15g")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper One Graph", "authors": ["Zhou Li"], "direction_ids": [did]},
        headers=h,
    )
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Paper Two Graph", "authors": ["Zhou Li"], "direction_ids": [did]},
        headers=h,
    )
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did}).json()
    zhou = next(n for n in graph.get("nodes") or [] if n.get("type") == "author" and n.get("title") == "Zhou Li")
    titles = {p.get("title") for p in zhou.get("papers") or []}
    assert "Paper One Graph" in titles
    assert "Paper Two Graph" in titles
    assert zhou.get("paper_count") == 2
