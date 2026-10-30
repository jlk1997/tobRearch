from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.insight import graph_empty_next
from fengsuan.app.services.literature import recommend_empty_next, rewrite_query


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


def test_g159_parent_core_mentions_child_pin(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g159core")
    pid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["vision"]},
        headers=h,
    ).json()["id"]
    cid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "多模态", "parent_id": pid, "keywords": ["multimodal"]},
        headers=h,
    ).json()["id"]
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Core Only Vision Paper", "direction_ids": [pid]},
        headers=h,
    ).json()
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Core Only Multimodal Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    assert (
        client.patch(
            f"/api/v1/labs/{lab}/literature/{parent_lit['id']}/core",
            json={"direction_id": pid, "is_core": True},
            headers=h,
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/v1/labs/{lab}/literature/{child_lit['id']}/core",
            json={"direction_id": cid, "is_core": True},
            headers=h,
        ).status_code
        == 200
    )
    parent_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "Parent Draft",
            "kind": "other",
            "content_md": f"## 相关工作\n{parent_lit['title']}",
            "direction_ids": [pid],
        },
        headers=h,
    ).json()
    child_ms = client.post(
        f"/api/v1/labs/{lab}/manuscripts",
        json={
            "title": "Child Draft",
            "kind": "other",
            "content_md": f"## 相关工作\n{child_lit['title']}",
            "direction_ids": [cid],
        },
        headers=h,
    ).json()
    parent_claims = client.get(f"/api/v1/manuscripts/{parent_ms['id']}/claims", headers=h)
    assert parent_claims.status_code == 200, parent_claims.text
    body = parent_claims.json()
    assert body.get("includes_children") is True
    missing = {x.get("title") for x in (body.get("core_missing") or [])}
    assert child_lit["title"] in missing
    assert parent_lit["title"] not in missing
    nxt = body.get("next_step") or {}
    assert nxt.get("key") == "core"
    assert "小课题" in (nxt.get("text") or "")

    child_claims = client.get(f"/api/v1/manuscripts/{child_ms['id']}/claims", headers=h)
    assert child_claims.status_code == 200, child_claims.text
    child_body = child_claims.json()
    assert child_body.get("includes_children") is False
    child_missing = {x.get("title") for x in (child_body.get("core_missing") or [])}
    assert parent_lit["title"] not in child_missing
    assert child_lit["title"] not in child_missing
    assert (child_body.get("next_step") or {}).get("key") != "core"


def test_g160_rewrite_recommend_graph_scope(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g160kw")
    pid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "视觉大组", "keywords": ["visionkw"]},
        headers=h,
    ).json()["id"]
    cid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "多模态", "parent_id": pid, "keywords": ["multimodalkw"]},
        headers=h,
    ).json()["id"]
    db = SessionLocal()
    try:
        parent_q = rewrite_query(db, lab, "attention", direction_id=pid)
        assert "visionkw" in parent_q and "multimodalkw" in parent_q
        child_q = rewrite_query(db, lab, "attention", direction_id=cid)
        assert "multimodalkw" in child_q
        assert "visionkw" not in child_q
        ghost = rewrite_query(db, lab, "attention", direction_id="missing-dir")
        assert ghost == "attention"
    finally:
        db.close()

    rec = recommend_empty_next(lab, "", role="student", direction_id=pid, includes_children=True)
    assert "小课题" in (rec.get("text") or "")
    assert f"direction_id={pid}" in ((rec.get("steps") or [{}])[0].get("href") or "")

    empty = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": pid}, headers=h)
    assert empty.status_code == 200, empty.text
    assert empty.json().get("includes_children") is True
    assert "小课题" in ((empty.json().get("empty_next") or {}).get("text") or "")
    filled_child = client.get(f"/api/v1/labs/{lab}/citation-graph", params={"direction_id": cid}, headers=h)
    assert filled_child.json().get("includes_children") is False
    nxt = graph_empty_next(lab, cid, includes_children=False)
    assert "小课题" not in (nxt.get("text") or "")


def test_g162_outline_keywords_and_keep_extras(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g162out")
    pid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "主方向", "keywords": ["parentkw"]},
        headers=h,
    ).json()["id"]
    cid = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "子课题", "parent_id": pid, "keywords": ["childkw"]},
        headers=h,
    ).json()["id"]
    extra = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "并列方向", "keywords": ["sibkw"]},
        headers=h,
    ).json()["id"]
    ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "direction_id": pid},
        headers=h,
    )
    assert ol.status_code == 200, ol.text
    kws = ol.json().get("keywords") or []
    assert "parentkw" in kws and "childkw" in kws
    assert "sibkw" not in kws
    assert ol.json().get("includes_children") is True
    child_ol = client.post(
        f"/api/v1/labs/{lab}/writing/outline",
        json={"title": "综述", "kind": "survey", "direction_id": cid},
        headers=h,
    )
    assert child_ol.status_code == 200, child_ol.text
    child_kws = child_ol.json().get("keywords") or []
    assert "childkw" in child_kws
    assert "parentkw" not in child_kws
    assert child_ol.json().get("includes_children") is False

    picked = client.put(
        f"/api/v1/labs/{lab}/me/direction",
        json={"direction_id": pid, "direction_ids": [pid, extra]},
        headers=h,
    )
    assert picked.status_code == 200, picked.text
    keep = client.put(
        f"/api/v1/labs/{lab}/me/direction",
        json={"direction_id": extra, "direction_ids": [extra, pid]},
        headers=h,
    )
    assert keep.status_code == 200, keep.text
    assert keep.json()["direction_id"] == extra
    assert set(keep.json()["direction_ids"]) == {pid, extra}
