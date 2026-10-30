from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ReproduceRun


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


def _titles(pack) -> str:
    rows = (pack or {}).get("not_run_titles") or []
    assert isinstance(rows, list)
    return " ".join(str((x.get("title") if isinstance(x, dict) else x) or "") for x in rows)


def test_g219_not_run_titles_follow_direction(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g219repro")
    pid, cid, sib = _tree(client, h, lab)
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child NotRun Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    sib_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling NotRun Paper", "direction_ids": [sib]},
        headers=h,
    ).json()
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
    assert child_run.status_code == 200, child_run.text
    assert sib_run.status_code == 200, sib_run.text
    db = SessionLocal()
    try:
        for rid in (child_run.json()["id"], sib_run.json()["id"]):
            row = db.get(ReproduceRun, rid)
            row.status = "failed"
            row.error_class = "parse"
            row.error = "未实测"
            db.commit()
    finally:
        db.close()

    listed = client.get(f"/api/v1/labs/{lab}/reproduce-runs", headers=h)
    assert listed.status_code == 200, listed.text
    assert isinstance(listed.json(), list)

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    parent_spine = parent.json().get("reproduce_spine") or {}
    parent_text = _titles(parent_spine)
    assert int(parent_spine.get("not_run") or 0) >= 1
    assert "Child NotRun Paper" in parent_text
    assert "Sibling NotRun Paper" not in parent_text
    child_chip = next(
        (x for x in parent_spine.get("not_run_titles") or [] if x.get("title") == "Child NotRun Paper"),
        {},
    )
    assert child_lit["id"] in (child_chip.get("href") or "")

    sib_body = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": sib}, headers=h).json()
    sib_text = _titles(sib_body.get("reproduce_spine"))
    assert "Sibling NotRun Paper" in sib_text
    assert "Child NotRun Paper" not in sib_text

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h).json()
    assert (ghost.get("reproduce_spine") or {}).get("not_run_titles") == []
    assert int((ghost.get("reproduce_spine") or {}).get("not_run") or 0) == 0

    digest = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert digest.status_code == 200, digest.text
    assert "Child NotRun Paper" in _titles(digest.json().get("reproduce_spine"))
    assert "Sibling NotRun Paper" not in _titles(digest.json().get("reproduce_spine"))
