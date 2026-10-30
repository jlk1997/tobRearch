from __future__ import annotations

from fengsuan.app.services.insight import dataset_list_empty_next, research_preflight
from fengsuan.app.services.metrics import build_funnel, period_event_counts
from fengsuan.app.db.session import SessionLocal


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
            "display_name": "生",
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _tree(client, h, lab: str):
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
    sib = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "并列方向", "keywords": ["sibkw"]},
        headers=h,
    ).json()["id"]
    return pid, cid, sib


def test_g189_funnel_week_invalid_not_labwide(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g189funnel")
    pid, cid, sib = _tree(client, h, lab)
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Funnel Paper", "direction_ids": [pid]},
        headers=h,
    ).json()
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Funnel Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Funnel Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    assert parent_lit["id"] and child_lit["id"] and sib_lit["id"]
    assert (
        client.post(
            f"/api/v1/literature/{child_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=h,
        ).status_code
        == 200
    )

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    body = parent.json()
    assert body.get("stats_includes_children") is True
    assert int(body.get("literature_count") or 0) >= 2
    funnel = body.get("funnel") or {}
    lit_stage = next((s for s in (funnel.get("stages") or []) if s.get("key") == "literature"), {})
    assert int(lit_stage.get("count") or 0) >= 2
    assert funnel.get("includes_children") is True
    assert int((body.get("this_week") or {}).get("reading_started") or 0) >= 1
    assert f"direction_id={pid}" in ((body.get("this_week") or {}).get("reading_started_href") or "")

    sib_stats = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": sib}, headers=h)
    assert int((sib_stats.json().get("this_week") or {}).get("reading_started") or 0) == 0
    assert int(sib_stats.json().get("literature_count") or 0) == 1

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h)
    ghost_body = ghost.json()
    assert ghost_body.get("stats_direction_valid") is False
    assert int(ghost_body.get("literature_count") or 0) == 0
    assert int((ghost_body.get("this_week") or {}).get("reading_started") or 0) == 0
    ghost_funnel = ghost_body.get("funnel") or {}
    assert ghost_funnel.get("empty") is True
    assert "不是全实验室" in (ghost_funnel.get("empty_text") or "")
    ghost_lit = next((s for s in (ghost_funnel.get("stages") or []) if s.get("key") == "literature"), {})
    assert int(ghost_lit.get("count") or 0) == 0

    lab_stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert int(lab_stats.json().get("literature_count") or 0) >= 3
    assert int((lab_stats.json().get("this_week") or {}).get("reading_started") or 0) >= 1

    empty = build_funnel(lab, {"literature_count": 9, "by_direction": []}, direction_id="missing-dir")
    assert empty["empty"] is True
    assert next(s["count"] for s in empty["stages"] if s["key"] == "literature") == 0


def test_g190_digest_preflight_dataset_detail(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g190digest")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Digest Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Digest Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    ds = client.post(
        f"/api/v1/labs/{lab}/datasets",
        json={"name": "ChildDigestSet", "literature_item_ids": [child_lit["id"]]},
        headers=h,
    )
    assert ds.status_code == 200, ds.text
    detail = client.get(f"/api/v1/datasets/{ds.json()['id']}", headers=h)
    assert detail.status_code == 200, detail.text
    assert cid in (detail.json().get("direction_ids") or [])
    assert sib not in (detail.json().get("direction_ids") or [])

    parent_dig = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert parent_dig.status_code == 200, parent_dig.text
    parent_funnel = parent_dig.json().get("funnel") or {}
    parent_lit = next((s for s in (parent_funnel.get("stages") or []) if s.get("key") == "literature"), {})
    assert int(parent_lit.get("count") or 0) >= 1
    assert parent_dig.json().get("includes_children") is True

    ghost = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": "missing-dir"}, headers=h)
    ghost_funnel = ghost.json().get("funnel") or {}
    assert ghost_funnel.get("empty") is True
    ghost_lit = next((s for s in (ghost_funnel.get("stages") or []) if s.get("key") == "literature"), {})
    assert int(ghost_lit.get("count") or 0) == 0

    sh = _invite_student(client, h, lab, "g190stu@example.com")
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": pid, "direction_ids": [pid]},
            headers=sh,
        ).status_code
        == 200
    )
    pre = client.get(f"/api/v1/labs/{lab}/research-preflight", headers=sh)
    assert pre.status_code == 200, pre.text
    assert int(pre.json().get("hung_inject_count") or 0) >= 1
    assert "注入已挂方向" in (pre.json().get("hung_inject_text") or "")
    assert sib_lit["id"]
    db = SessionLocal()
    try:
        pack = research_preflight(db, lab, user_id=None)
        assert "hung_inject_count" not in pack or pack.get("hung_inject_count") is None
        empty = dataset_list_empty_next(lab, direction_id=pid, includes_children=True, empty_kind="filter")
        assert "小课题" in (empty.get("title") or "") + (empty.get("text") or "")
        week = period_event_counts(db, lab, direction_id="missing-dir", since=None)
        assert week["valid"] is False
        assert week["reading_started"] == 0
    finally:
        db.close()
