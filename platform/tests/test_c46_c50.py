from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.writing import literature_card_hints, related_work_context


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


def test_c46_outline_placeholder_is_not_used_reproduce(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c46ol")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Outline No Reproduce Residual"},
        headers=h,
    ).json()
    db = SessionLocal()
    try:
        hint = literature_card_hints(db, lab, [paper["id"]])[0]
        assert hint.get("has_reproduce") is False
        assert hint.get("reproduce_brief")
        ctx = related_work_context(db, lab, [paper["id"]])
        assert ctx.get("used_reproduce") is False
    finally:
        db.close()
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert body.get("used_reproduce") is False
    assert "已结合组内复现差距" not in (body.get("notes") or "")
    assert "尚无组内实测" in (body.get("notes") or "")
    note = next(n for n in (body.get("honesty_notes") or []) if n.get("kind") == "reproduce")
    assert note.get("level") != "success"


def test_c49_section_abstract_card_is_not_used_method(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c49sec")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Section Abstract Residual", "abstract": "only abstract"},
        headers=h,
    ).json()
    sec = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "notes": "写差距", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert sec.status_code == 200, sec.text
    body = sec.json()
    assert body.get("used_method_card") is False
    assert body.get("used_reproduce") is False
    note = next(n for n in (body.get("honesty_notes") or []) if n.get("kind") == "method_card")
    assert note.get("item_id") == paper["id"]
    assert "panel=card" in (note.get("href") or "")
    lit = next(x for x in (body.get("literature") or []) if x.get("id") == paper["id"])
    assert lit.get("has_method_card") is False


def test_c50_kb_documents_stamp_card_state(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c50kb")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "KB Source Residual", "abstract": "kb source"},
        headers=h,
    ).json()
    kb = client.post(f"/api/v1/literature/{paper['id']}/add-to-kb", headers=h)
    assert kb.status_code == 200, kb.text
    listed = client.get(f"/api/v1/labs/{lab}/kb/documents", headers=h)
    assert listed.status_code == 200, listed.text
    row = next(x for x in listed.json().get("items") or [] if x.get("literature_item_id") == paper["id"])
    assert row.get("has_method_card") is False
    assert "panel=card" in (row.get("card_href") or "")
