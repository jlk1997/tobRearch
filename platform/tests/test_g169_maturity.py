from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.directions import direction_scope_keywords
from fengsuan.app.services.insight import reproduce_list_empty_next
from fengsuan.app.services.literature import search_empty_next
from fengsuan.app.services.reproduce import empty_reproduce_spine


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


def test_g169_scope_keywords_search_empty_and_spine(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g169kw")
    pid, cid, sib = _tree(client, h, lab)
    db = SessionLocal()
    try:
        parent_kws, parent_kids = direction_scope_keywords(db, lab, pid)
        assert "visionkw" in parent_kws and "multimodalkw" in parent_kws
        assert "sibkw" not in parent_kws
        assert parent_kids is True
        child_kws, child_kids = direction_scope_keywords(db, lab, cid)
        assert "multimodalkw" in child_kws
        assert "visionkw" not in child_kws
        assert child_kids is False
        ghost, ghost_kids = direction_scope_keywords(db, lab, "missing-dir")
        assert ghost == []
        assert ghost_kids is False
    finally:
        db.close()

    nxt = search_empty_next(lab, "q", direction_id=pid, includes_children=True)
    assert "小课题" in (nxt.get("title") or "")
    assert f"direction_id={pid}" in ((nxt.get("steps") or [{}])[0].get("href") or "")
    child_nxt = search_empty_next(lab, "q", direction_id=cid, includes_children=False)
    assert "小课题" not in (child_nxt.get("title") or "")

    spine = empty_reproduce_spine(lab, direction_id=pid)
    assert f"direction_id={pid}" in (spine.get("href") or "")
    assert f"direction_id={pid}" in (spine.get("not_run_href") or "")
    assert "status=failed" in (spine.get("not_run_href") or "")
    empty = reproduce_list_empty_next(lab, empty_kind="filter", direction_id=pid, includes_children=True)
    assert "小课题" in (empty.get("title") or "") + (empty.get("text") or "")
    bad = client.post(
        f"/api/v1/labs/{lab}/writing/section",
        json={"heading": "相关工作", "direction_id": "missing-dir"},
        headers=h,
    )
    assert bad.status_code == 404


def test_g170_reproduce_list_direction_scope(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g170repro")
    pid, cid, sib = _tree(client, h, lab)
    parent_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Repro Paper", "direction_ids": [pid]},
        headers=h,
    ).json()
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Repro Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Repro Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    parent_run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": parent_lit["id"]},
        headers=h,
    )
    child_run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": child_lit["id"]},
        headers=h,
    )
    sib_run = client.post(
        f"/api/v1/labs/{lab}/reproduce-runs",
        json={"literature_item_id": sib_lit["id"]},
        headers=h,
    )
    assert parent_run.status_code == 200, parent_run.text
    assert child_run.status_code == 200, child_run.text
    assert sib_run.status_code == 200, sib_run.text
    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)

    parent_list = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs", params={"direction_id": pid}, headers=h
    )
    assert parent_list.status_code == 200, parent_list.text
    parent_ids = {row["id"] for row in parent_list.json()}
    assert parent_run.json()["id"] in parent_ids
    assert child_run.json()["id"] in parent_ids
    assert sib_run.json()["id"] not in parent_ids

    child_list = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs", params={"direction_id": cid}, headers=h
    )
    child_ids = {row["id"] for row in child_list.json()}
    assert child_run.json()["id"] in child_ids
    assert parent_run.json()["id"] not in child_ids
    assert sib_run.json()["id"] not in child_ids

    ghost = client.get(
        f"/api/v1/labs/{lab}/reproduce-runs", params={"direction_id": "missing-dir"}, headers=h
    )
    assert ghost.status_code == 200, ghost.text
    body = ghost.json()
    assert body.get("items") == []
    assert body.get("empty_kind") == "filter"
    assert "direction_id=missing-dir" not in str(body.get("empty_next") or {})


def test_g171_home_guide_child_reading_counts(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g171read")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Reading Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Reading Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
    sh = _invite_student(client, h, lab, "g171stu@example.com")
    picked = client.put(
        f"/api/v1/labs/{lab}/me/direction",
        json={"direction_id": pid, "direction_ids": [pid]},
        headers=sh,
    )
    assert picked.status_code == 200, picked.text
    home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert home.status_code == 200, home.text
    before = {s.get("key"): s for s in (home.json().get("next_steps") or [])}
    assert "reading" in before
    assert f"direction_id={pid}" in (before["reading"].get("href") or "")
    assert f"direction_id={cid}" not in (before["reading"].get("href") or "")
    assert "小课题" in (before["reading"].get("text") or "")
    guide = {g.get("key"): g for g in (home.json().get("guide") or [])}
    assert guide.get("open_direction_papers", {}).get("done") is False
    assert f"direction_id={pid}" in (guide.get("open_direction_papers", {}).get("href") or "")

    marked = client.post(
        f"/api/v1/literature/{child_lit['id']}/mark-read",
        json={"status": "reading"},
        headers=sh,
    )
    assert marked.status_code == 200, marked.text
    after = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=sh)
    assert after.status_code == 200, after.text
    keys = {s.get("key") for s in (after.json().get("next_steps") or [])}
    assert "reading" not in keys
    guide_after = {g.get("key"): g for g in (after.json().get("guide") or [])}
    assert guide_after.get("open_direction_papers", {}).get("done") is True

    other = _invite_student(client, h, lab, "g171sib@example.com")
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": pid, "direction_ids": [pid]},
            headers=other,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/literature/{sib_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=other,
        ).status_code
        == 200
    )
    sib_home = client.get("/api/v1/me/home", params={"lab_id": lab}, headers=other)
    sib_keys = {s.get("key"): s for s in (sib_home.json().get("next_steps") or [])}
    assert "reading" in sib_keys
    assert f"direction_id={pid}" in (sib_keys["reading"].get("href") or "")
