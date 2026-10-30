from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.analysis import card_is_written, written_method_item_ids


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


def _put_card(item_id: str, user_id: str, payload: dict) -> LiteratureAnalysis:
    db = SessionLocal()
    try:
        row = LiteratureAnalysis(
            item_id=item_id,
            analysis_type="method_card",
            result_json=json.dumps(payload, ensure_ascii=False),
            created_by=user_id,
            model="configured",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row
    finally:
        db.close()


def _invite_student(client, h, lab: str, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": "诚实生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def test_c26_abstract_card_is_not_written(client):
    assert card_is_written({"source": "abstract_only", "problem": {"text": "x"}}) is False
    assert card_is_written({"source": "pdf", "problem": {"text": "x"}}) is True
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c26abs")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Abstract Only Residual", "abstract": "only abstract"},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    db = SessionLocal()
    try:
        assert written_method_item_ids(db, [paper["id"]]) == set()
    finally:
        db.close()
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    row = next(x for x in pack["items"] if x["id"] == paper["id"])
    assert row.get("has_method_card") is False
    assert (row.get("card_empty") or {}).get("kind") == "abstract_only"
    none = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "has_card": True}).json()
    assert all(x["id"] != paper["id"] for x in none.get("items") or [])
    gap = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "has_card": False}).json()
    assert any(x["id"] == paper["id"] for x in gap.get("items") or [])
    sh = _invite_student(client, h, lab, "c26abs@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    assert client.post(f"/api/v1/literature/{paper['id']}/progress", json={"page_no": 1, "page_count": 8}, headers=sh).status_code == 200
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert card["method_card_count"] == 0
    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h).json()
    assert stats.get("method_card_count") == 0


def test_c26_pdf_card_counts_as_written(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c26pdf")
    paper = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Fulltext Card"}, headers=h).json()
    _put_card(paper["id"], uid, {"source": "pdf", "problem": {"text": "from pages", "pages": [1]}})
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "has_card": True}).json()
    assert any(x["id"] == paper["id"] for x in pack.get("items") or [])
    row = next(x for x in client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()["items"] if x["id"] == paper["id"])
    assert row.get("has_method_card") is True


def test_c28_week_ms_titles_on_member_card(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c28ms")
    sh = _invite_student(client, h, lab, "c28ms@example.com")
    sid = client.get("/api/v1/auth/me", headers=sh).json()["id"]
    ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={"title": "Week Draft Residual", "kind": "conference", "content_md": "## 引言\n草稿。"},
        headers=sh,
    )
    assert ms.status_code == 200, ms.text
    cockpit = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    card = next(m for m in cockpit["members"] if m["user_id"] == sid)
    assert any((t.get("title") or "") == "Week Draft Residual" for t in card.get("week_ms_titles") or [])
    member = client.get(f"/api/v1/labs/{lab}/cockpit/members/{sid}", headers=h).json()
    assert any((t.get("title") or "") == "Week Draft Residual" for t in member.get("week_ms_titles") or [])


def test_c29_draft_keywords_include_method_tokens(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c29kw")
    a = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Alpha Probe One"}, headers=h).json()
    b = client.post(f"/api/v1/labs/{lab}/literature", json={"title": "Alpha Probe Two"}, headers=h).json()
    _put_card(a["id"], uid, {"source": "pdf", "methods": {"text": "residual detector baseline", "pages": [1]}})
    _put_card(b["id"], uid, {"source": "pdf", "methods": {"text": "residual detector network", "pages": [1]}})
    pack = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1}).json()
    drafts = pack.get("direction_drafts") or []
    assert drafts
    blob = " ".join(" ".join(d.get("keywords") or []) for d in drafts).lower()
    assert "residual" in blob
    assert client.get(f"/api/v1/labs/{lab}/directions", headers=h).json() == []


def test_c30_graph_literature_stamps_card_state(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c30graph")
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Graph Paper", "authors": ["Ada"], "direction_ids": [did]},
        headers=h,
    ).json()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}})
    graph = client.get(f"/api/v1/labs/{lab}/citation-graph", headers=h, params={"direction_id": did}).json()
    node = next(n for n in graph.get("nodes") or [] if n.get("id") == paper["id"])
    assert node.get("has_method_card") is False
    assert (node.get("card_empty") or {}).get("kind") == "abstract_only"
    detail = client.get(f"/api/v1/literature/{paper['id']}", headers=h).json()
    assert detail.get("has_method_card") is False
