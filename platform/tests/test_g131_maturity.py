from __future__ import annotations

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.services.directions import direction_scope_ids
from fengsuan.app.services.insight import build_direction_stuck


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


def _student(client, h, lab, email: str):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    token = inv.json()["token"]
    reg = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "pass1234a", "invite_token": token, "display_name": "学生甲", "consent": True},
    )
    assert reg.status_code == 200, reg.text
    return reg.json()["access"]


def test_g131_parent_and_member_status(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g131dir")
    parent = client.post(f"/api/v1/labs/{lab}/directions", json={"name": "视觉大组"}, headers=h)
    assert parent.status_code == 200, parent.text
    pid = parent.json()["id"]
    child = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "多模态子课题", "parent_id": pid},
        headers=h,
    )
    assert child.status_code == 200, child.text
    cid = child.json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    assert listed.status_code == 200, listed.text
    rows = {r["id"]: r for r in listed.json()}
    assert rows[pid]["child_count"] == 1
    assert rows[pid]["includes_children"] is True
    assert rows[cid]["parent_id"] == pid
    db = SessionLocal()
    try:
        assert direction_scope_ids(db, pid) == [pid, cid] or set(direction_scope_ids(db, pid)) == {pid, cid}
    finally:
        db.close()
    stu = _student(client, h, lab, "stu-g131@example.com")
    me = client.get("/api/v1/auth/me", headers=_h(stu))
    uid = me.json()["id"]
    patched = client.patch(
        f"/api/v1/labs/{lab}/members/{uid}",
        json={"direction_ids": [pid, cid], "direction_id": pid},
        headers=h,
    )
    assert patched.status_code == 200, patched.text
    members = client.get(f"/api/v1/labs/{lab}/members", headers=h).json()
    row = next(x for x in members if x["user_id"] == uid)
    assert row["direction_id"] == pid
    assert {d["id"] for d in row["directions"]} == {pid, cid}
    client.patch(f"/api/v1/labs/{lab}/members/{uid}", json={"status": "graduated"}, headers=h)
    overview = client.get(f"/api/v1/labs/{lab}/directions/{pid}/overview", headers=h)
    assert overview.status_code == 200, overview.text
    assert overview.json().get("child_count") == 1
    assert overview.json().get("includes_children") is True
    assert all(isinstance(x, str) for x in (overview.json().get("child_ids") or []))
    idle = overview.json().get("stuck", {}).get("idle_members") or []
    assert uid not in {p["user_id"] for p in idle}
    people = overview.json().get("members") or []
    assert any(p["user_id"] == uid and p.get("membership_status") == "graduated" for p in people)
    db2 = SessionLocal()
    try:
        stuck = build_direction_stuck(
            db2,
            lab,
            pid,
            live_ids=["x"],
            manuscripts=[],
            people=[{"user_id": uid, "role": "student", "display_name": "学生甲", "membership_status": "graduated"}],
            spine={},
            milestones=[],
        )
        assert stuck["idle_members"] == []
    finally:
        db2.close()
