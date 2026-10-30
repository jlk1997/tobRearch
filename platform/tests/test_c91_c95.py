from __future__ import annotations

import json
from datetime import timedelta

from fengsuan.app.db.base import utcnow
from fengsuan.app.db.session import SessionLocal
from fengsuan.app.models.agent import AgentTrace
from fengsuan.app.models.literature import LiteratureDirectionLink


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


def _invite_student(client, h, lab: str, email: str, name: str = "学生"):
    inv = client.post(f"/api/v1/labs/{lab}/invites", json={"email": email, "role": "student"}, headers=h)
    assert inv.status_code == 200, inv.text
    reg = client.post(
        "/api/v1/auth/register",
        json={
            "invite_token": inv.json()["token"],
            "email": email,
            "password": "student99",
            "display_name": name,
            "consent": True,
        },
    )
    assert reg.status_code == 200, reg.text
    return _h(reg.json()["access"])


def _add_trace(db, *, lab, user_id, session_id, q, sources=None, created_at=None, error=None, result=""):
    db.add(
        AgentTrace(
            lab_id=lab,
            user_id=user_id,
            session_id=session_id,
            feature="kb_query",
            scope="lab",
            step=1,
            kind="tool",
            tool="search_lab_literature",
            args_json=json.dumps({"q": q}, ensure_ascii=False),
            result_summary=result or ("2 篇" if sources else "0 篇"),
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
            error=error,
        )
    )
    db.add(
        AgentTrace(
            lab_id=lab,
            user_id=user_id,
            session_id=session_id,
            feature="kb_query",
            scope="lab",
            step=2,
            kind="answer",
            result_summary=result or ("有材料。" if sources else "没有。"),
            source_ids_json=json.dumps(sources or [], ensure_ascii=False),
            error=error,
        )
    )
    db.flush()
    if created_at is not None:
        for row in db.query(AgentTrace).filter(AgentTrace.session_id == session_id).all():
            row.created_at = created_at


def test_c91_c94_c95_stats_never_privacy(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c9195")
    ada = _invite_student(client, h, lab, "c91ada@example.com", "Ada")
    zoe = _invite_student(client, h, lab, "c91zoe@example.com", "Zoe")
    aid = client.get("/api/v1/auth/me", headers=ada).json()["id"]
    zid = client.get("/api/v1/auth/me", headers=zoe).json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    other = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "茶壶校准", "keywords": ["teapot"], "description": ""},
        headers=h,
    ).json()["id"]
    assert client.patch(f"/api/v1/labs/{lab}/members/{aid}", json={"direction_id": did}, headers=h).status_code == 200
    assert client.patch(f"/api/v1/labs/{lab}/members/{zid}", json={"direction_id": did}, headers=h).status_code == 200
    db = SessionLocal()
    try:
        _add_trace(db, lab=lab, user_id=aid, session_id="sess-c91-src", q="组里残差论文", sources=["lit-1"])
        _add_trace(db, lab=lab, user_id=zid, session_id="sess-c91-empty", q="空问残差")
        _add_trace(
            db,
            lab=lab,
            user_id=zid,
            session_id="sess-c91-old",
            q="上周问过",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()

    stats = client.get(f"/api/v1/labs/{lab}/stats", headers=h)
    assert stats.status_code == 200, stats.text
    by = {m["user_id"]: m for m in stats.json().get("by_member") or []}
    assert "组里残差论文" in ((by[aid].get("last_ai") or {}).get("question") or "")
    assert any("空问残差" in (x.get("question") or "") for x in (by[zid].get("week_empty_ai") or []))
    peer_stats = client.get(f"/api/v1/labs/{lab}/stats", headers=ada).json()
    peer_by = {m["user_id"]: m for m in peer_stats.get("by_member") or []}
    assert not peer_by[zid].get("last_ai")
    assert not peer_by[zid].get("week_empty_ai")
    assert not peer_by[zid].get("week_degraded_ai")

    tea = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    ada_card = next(m for m in tea["members"] if m["user_id"] == aid)
    zoe_card = next(m for m in tea["members"] if m["user_id"] == zid)
    assert ada_card.get("week_never_ai") is False
    assert zoe_card.get("week_never_ai") is False
    peer = client.get(f"/api/v1/labs/{lab}/cockpit", headers=ada).json()
    peer_zoe = next(m for m in peer["members"] if m["user_id"] == zid)
    assert "week_never_ai" not in peer_zoe or peer_zoe.get("week_never_ai") in (None, False)
    assert peer_zoe.get("week_never_ai") is not True
    mine = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=zoe).json()
    assert mine.get("week_never_ai") is False
    hidden = client.get(f"/api/v1/labs/{lab}/cockpit/members/{zid}", headers=ada).json()
    assert hidden.get("week_never_ai") is not True

    silent = _invite_student(client, h, lab, "c91silent@example.com", "Sid")
    sid = client.get("/api/v1/auth/me", headers=silent).json()["id"]
    assert client.patch(f"/api/v1/labs/{lab}/members/{sid}", json={"direction_id": did}, headers=h).status_code == 200
    tea2 = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    sid_card = next(m for m in tea2["members"] if m["user_id"] == sid)
    assert sid_card.get("week_never_ai") is True
    old_only = _invite_student(client, h, lab, "c91old@example.com", "Old")
    oid = client.get("/api/v1/auth/me", headers=old_only).json()["id"]
    assert client.patch(f"/api/v1/labs/{lab}/members/{oid}", json={"direction_id": did}, headers=h).status_code == 200
    db = SessionLocal()
    try:
        _add_trace(
            db,
            lab=lab,
            user_id=oid,
            session_id="sess-c91-only-old",
            q="更早的问",
            created_at=utcnow() - timedelta(days=10),
        )
        db.commit()
    finally:
        db.close()
    tea3 = client.get(f"/api/v1/labs/{lab}/cockpit", headers=h).json()
    old_card = next(m for m in tea3["members"] if m["user_id"] == oid)
    assert old_card.get("week_never_ai") is True
    peer_silent = next(m for m in client.get(f"/api/v1/labs/{lab}/cockpit", headers=ada).json()["members"] if m["user_id"] == sid)
    assert peer_silent.get("week_never_ai") is not True

    ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h)
    assert ov.status_code == 200, ov.text
    people = {p["user_id"]: p for p in ov.json().get("members") or []}
    assert people[aid].get("week_never_ai") is False
    assert people[sid].get("week_never_ai") is True
    assert people[oid].get("week_never_ai") is True
    peer_ov = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=ada).json()
    peer_people = {p["user_id"]: p for p in peer_ov.get("members") or []}
    assert peer_people[sid].get("week_never_ai") is not True
    other_ov = client.get(f"/api/v1/labs/{lab}/directions/{other}/overview", headers=h).json()
    other_ids = {p["user_id"] for p in other_ov.get("members") or []}
    assert sid not in other_ids


def test_c92_c93_list_hints_home_hang_do_not_write(client):
    token = _admin(client)
    h = _h(token)
    lab = _lab(client, h, "c9293")
    student = _invite_student(client, h, lab, "c92stu@example.com", "Stu")
    tea_id = client.get("/api/v1/auth/me", headers=h).json()["id"]
    paper = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Teapot Calibration Protocol Alpha", "abstract": "teapot only", "direction_ids": []},
        headers=h,
    )
    assert paper.status_code == 200, paper.text
    pid = paper.json()["id"]
    client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Teapot Calibration Protocol Beta", "abstract": "teapot kitchen", "direction_ids": []},
        headers=h,
    )
    residual = client.post(
        f"/api/v1/labs/{lab}/literature",
        json={"title": "Residual Network Detection Study", "abstract": "residual nets", "direction_ids": []},
        headers=h,
    )
    assert residual.status_code == 200, residual.text
    rid = residual.json()["id"]
    did = client.post(
        f"/api/v1/labs/{lab}/directions",
        json={"name": "残差检测", "keywords": ["residual"], "description": ""},
        headers=h,
    ).json()["id"]
    listed = client.get(f"/api/v1/labs/{lab}/literature", headers=h, params={"page": 1, "page_size": 20})
    assert listed.status_code == 200, listed.text
    hints = listed.json().get("keyword_hints") or []
    assert "teapot" in hints
    assert "residual" not in hints
    peer = client.get(f"/api/v1/labs/{lab}/literature", headers=student, params={"page": 1, "page_size": 20})
    assert peer.status_code == 200, peer.text
    assert not (peer.json().get("keyword_hints") or [])
    after_list = client.get(f"/api/v1/labs/{lab}/directions/{did}/overview", headers=h).json()
    assert "teapot" not in (after_list.get("keywords") or [])

    marked = client.post(f"/api/v1/literature/{rid}/mark-read", json={"status": "reading"}, headers=h)
    assert marked.status_code == 200, marked.text
    home = client.get("/api/v1/me/home", headers=h, params={"lab_id": lab})
    assert home.status_code == 200, home.text
    readings = home.json().get("readings") or []
    hit = next((x for x in readings if x.get("id") == rid), None)
    assert hit, readings
    names = [s.get("name") for s in (hit.get("direction_suggestions") or [])]
    assert "残差检测" in names
    db = SessionLocal()
    try:
        n = (
            db.query(LiteratureDirectionLink)
            .filter(LiteratureDirectionLink.item_id == rid, LiteratureDirectionLink.direction_id == did)
            .count()
        )
        assert n == 0
        unused = db.query(LiteratureDirectionLink).filter(LiteratureDirectionLink.item_id == pid).count()
        assert unused == 0
        assert tea_id
    finally:
        db.close()
