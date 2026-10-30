from __future__ import annotations

import shutil
from pathlib import Path

from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.runs import ResearchRun
from fengsuan.app.services.research_engine import persist_unused_diagnosis, run_unused_actionable


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


def _rows(payload):
    if isinstance(payload, list):
        return payload
    return list(payload.get("items") or [])


def _plant(dest: Path, title: str):
    (dest / "workspace").mkdir(parents=True, exist_ok=True)
    (dest / "workspace" / "literature_review.md").write_text("综述正文没有点名。", encoding="utf-8")
    (dest / "workspace" / "literature_review.bib").write_text(
        f"@article{{z,\n  title={{{title}}},\n  author={{A}},\n  year={{2021}},\n  journal={{J}}\n}}\n",
        encoding="utf-8",
    )


def _fail_run(rid: str, dest: Path):
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        dest.mkdir(parents=True, exist_ok=True)
        row.save_path = str(dest)
        row.status = "failed"
        row.error_class = "parse"
        row.error = "综述没写到"
        persist_unused_diagnosis(row)
        db.commit()
    finally:
        db.close()


def _unused_job(client, h, lab: str, dest: Path, title: str, question: str):
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": question, "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    _plant(dest, title)
    _fail_run(rid, dest)
    return rid


def _direction(client, h, lab: str, name: str):
    r = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": name, "keywords": [name.lower()], "description": ""},
        headers=h,
    )
    assert r.status_code == 200, r.text
    return r.json()["id"]


def test_g116_member_unused_href_and_list(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g116mem")
    a = _invite_student(client, h, lab, "g116a@example.com")
    b = _invite_student(client, h, lab, "g116b@example.com")
    aid = client.get("/api/v1/auth/me", headers=a).json()["id"]
    bid = client.get("/api/v1/auth/me", headers=b).json()["id"]
    rid_a = _unused_job(client, a, lab, tmp_path / "a", "Alpha Member Paper", "member A")
    rid_b = _unused_job(client, b, lab, tmp_path / "b", "Beta Member Paper", "member B")

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    by_member = {row["user_id"]: row for row in stats.json().get("by_member") or []}
    assert f"requester_id={aid}" in (by_member[aid].get("unused_research_href") or "")
    assert by_member[aid].get("unused_research") == 1
    assert by_member[bid].get("unused_research") == 1

    scoped = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "requester_id": aid}, headers=h)
    assert scoped.status_code == 200, scoped.text
    assert {row["id"] for row in _rows(scoped.json())} == {rid_a}

    other = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "requester_id": bid}, headers=h)
    assert {row["id"] for row in _rows(other.json())} == {rid_b}

    empty = client.get(
        f"/api/v1/labs/{lab}/research-runs",
        params={"unused": "1", "requester_id": "nobody-not-a-user"},
        headers=h,
    )
    body = empty.json()
    assert not _rows(body)
    assert body.get("empty_kind") == "filter"
    assert (body.get("empty_next") or {}).get("title") == "该成员没有综述没写到的文献调研"

    forced = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "requester_id": bid}, headers=a)
    assert {row["id"] for row in _rows(forced.json())} == {rid_a}


def test_g117_direction_snapshot_survives_switch(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g117snap")
    a = _invite_student(client, h, lab, "g117a@example.com")
    aid = _direction(client, h, lab, "SnapA")
    cid = _direction(client, h, lab, "SnapC")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": aid}, headers=a).status_code == 200
    rid = _unused_job(client, a, lab, tmp_path / "job", "Snap Unseen Paper", "snapshot unused")
    assert client.put(f"/api/v1/labs/{lab}/me/direction", json={"direction_id": cid}, headers=a).status_code == 200

    listed = client.get(f"/api/v1/labs/{lab}/directions", headers=h)
    rows = {x["id"]: x for x in listed.json()}
    assert rows[aid].get("unused_research") == 1
    assert rows[cid].get("unused_research") == 0

    old = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": aid}, headers=h)
    assert {row["id"] for row in _rows(old.json())} == {rid}
    moved = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1", "direction_id": cid}, headers=h)
    body = moved.json()
    assert not _rows(body)
    assert (body.get("empty_next") or {}).get("title") == "该方向没有综述没写到的文献调研"


def test_g118_unused_trusts_review_ready_without_disk(client, tmp_path):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "g118disk")
    created = client.post(
        f"/api/v1/labs/{lab}/research-runs",
        json={"question": "no rescan", "domain": "general"},
        headers=h,
    )
    assert created.status_code == 200, created.text
    rid = created.json()["id"]
    dest = tmp_path / "job"
    _plant(dest, "Ready Flag Paper")
    _fail_run(rid, dest)
    shutil.rmtree(dest / "workspace")
    db = SessionLocal()
    try:
        row = db.get(ResearchRun, rid)
        assert run_unused_actionable(row)
    finally:
        db.close()
    listed = client.get(f"/api/v1/labs/{lab}/research-runs", params={"unused": "1"}, headers=h)
    assert {row["id"] for row in _rows(listed.json())} == {rid}
