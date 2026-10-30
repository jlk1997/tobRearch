from __future__ import annotations

import json

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.literature import LiteratureAnalysis
from fengsuan.app.services.writing import literature_card_hints


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


def test_c41_outline_abstract_card_is_not_used_method(client):
    token = _admin(client)
    h = _h(token)
    uid = client.get("/api/v1/auth/me", headers=h).json()["id"]
    lab = _lab(client, h, "c41ol")
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Outline Abstract Residual"},
        headers=h,
    ).json()
    bare = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Outline Bare Residual"},
        headers=h,
    ).json()
    db = SessionLocal()
    try:
        assert literature_card_hints(db, lab, [bare["id"]])[0]["has_card"] is False
    finally:
        db.close()
    _put_card(paper["id"], uid, {"source": "abstract_only", "problem": {"text": "from abstract", "pages": []}, "methods": {"text": "abs method"}})
    db = SessionLocal()
    try:
        hints = literature_card_hints(db, lab, [paper["id"]])
        assert hints and hints[0]["has_card"] is False
    finally:
        db.close()
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    body = ol.json()
    assert body.get("used_method_card") is False
    assert "还没写方法" in (body.get("notes") or "")
    note = next(n for n in (body.get("honesty_notes") or []) if n.get("kind") == "method_card")
    assert note.get("item_id") == paper["id"]
    assert "panel=card" in (note.get("href") or "")
    _put_card(
        paper["id"],
        uid,
        {"source": "pdf", "problem": {"text": "from pages", "pages": [1]}, "methods": {"text": "full method", "pages": [1]}},
    )
    ol2 = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "literature_item_ids": [paper["id"]]},
        headers=h,
    )
    assert ol2.status_code == 200, ol2.text
    assert ol2.json().get("used_method_card") is True
    assert "还没写方法" not in (ol2.json().get("notes") or "")
