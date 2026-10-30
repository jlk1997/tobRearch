from __future__ import annotations

from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import persist_unused_diagnosis


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


def test_g198_unused_and_stuck_follow_direction(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g198stuck")
    pid, cid, sib = _tree(client, h, lab)
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Parent Stuck Paper", "direction_ids": [pid]},
        headers=h,
    )
    child_lit = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Child Stuck Paper", "direction_ids": [cid]},
        headers=h,
    ).json()
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Sibling Stuck Paper", "direction_ids": [sib]},
        headers=h,
    )
    sh = _invite_student(client, h, lab, "g198stu@example.com")
    assert (
        client.put(
            f"/api/v1/labs/{lab}/me/direction",
            json={"direction_id": pid, "direction_ids": [pid]},
            headers=sh,
        ).status_code
        == 200
    )
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "G198 unused scope", "domain": "general"},
        headers=sh,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = Path(tmp_path) / "g198run"
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        "@article{z0,\n  title={Unused Scope TitleX},\n  author={A},\n  year={2021},\n  journal={J}\n}\n",
        encoding="utf-8",
    )
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述未点名"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()

    parent = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": pid}, headers=h)
    assert parent.status_code == 200, parent.text
    body = parent.json()
    assert int(body.get("unused_research") or 0) >= 1
    assert f"direction_id={pid}" in (body.get("unused_research_href") or "")
    stuck = body.get("stuck_brief") or {}
    assert int(stuck.get("unread") or 0) >= 2
    assert int(stuck.get("unused_research") or 0) >= 1
    assert stuck.get("includes_children") is True
    assert "含小课题" in (stuck.get("text") or "")
    assert f"direction_id={pid}" in (stuck.get("unread_href") or "")
    direction_line = next(
        (x for x in (body.get("situation") or {}).get("lines") or [] if x.get("key") == "direction"),
        {},
    )
    assert "含小课题" in (direction_line.get("text") or "")

    sib_stats = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": sib}, headers=h)
    sib_body = sib_stats.json()
    assert int(sib_body.get("unused_research") or 0) == 0
    assert int((sib_body.get("stuck_brief") or {}).get("unread") or 0) == 1
    assert int((sib_body.get("stuck_brief") or {}).get("unused_research") or 0) == 0

    ghost = client.get(f"/api/v1/labs/{lab}/stats", params={"direction_id": "missing-dir"}, headers=h)
    ghost_body = ghost.json()
    assert int(ghost_body.get("unused_research") or 0) == 0
    assert int((ghost_body.get("stuck_brief") or {}).get("unread") or 0) == 0
    assert "不是全实验室" in ((ghost_body.get("stuck_brief") or {}).get("text") or "")

    assert (
        client.post(
            f"/api/v1/literature/{child_lit['id']}/mark-read",
            json={"status": "reading"},
            headers=sh,
        ).status_code
        == 200
    )
    after = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": pid}, headers=h)
    assert after.status_code == 200, after.text
    digest_stuck = after.json().get("stuck_brief") or {}
    assert digest_stuck.get("includes_children") is True
    assert int(after.json().get("unused_research") or 0) >= 1
    ghost_dig = client.get(f"/api/v1/labs/{lab}/digest", params={"direction_id": "missing-dir"}, headers=h)
    assert int(ghost_dig.json().get("unused_research") or 0) == 0
    assert int((ghost_dig.json().get("stuck_brief") or {}).get("unread") or 0) == 0
